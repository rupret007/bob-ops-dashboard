"""Disposable, bounded Git transport for authenticated supplemental hunks.

Like the complete-diff fallback, this fetches immutable objects into a new bare
repository. It never reads a caller-provided cache or checks out a worktree.
"""
from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import tempfile
import time

from .diff_context import Blob, ContextUnavailable, GitContext
from .source_context import MAX_SOURCE_BYTES


class BoundedGit:
    def __init__(self, directory, shared):
        self.directory, self.shared = directory, shared
        # Do not inherit replacement object databases, config injection or a
        # caller's worktree/index. Normal credential helpers are left intact.
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.environment.update(GIT_NO_REPLACE_OBJECTS='1', GIT_ATTR_NOSYSTEM='1',
                                GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')

    def run(self, *args, maximum=65536, network=False):
        from .gate import review_checkpoint
        from .deadline import remaining_timeout
        review_checkpoint()
        timeout = remaining_timeout(min(10, self.shared.seconds))
        if self.shared.seconds <= 0 or timeout <= 0:
            raise ContextUnavailable('time_budget')
        started = time.monotonic()
        environment = dict(self.environment, GIT_NO_LAZY_FETCH='0' if network else '1')
        command = ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
                   '-c', 'core.attributesFile=/dev/null', '-c', 'protocol.allow=never',
                   '-c', 'protocol.https.allow=' + ('always' if network else 'never'),
                   '-c', 'fetch.fsckObjects=true', '-c', 'transfer.fsckObjects=true', *args]
        child = None
        completed = False
        output = bytearray()
        try:
            child = subprocess.Popen(command, cwd=self.directory, env=environment,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
                stderr_size = 0
                while selector.get_map():
                    left = timeout - (time.monotonic() - started)
                    if left <= 0:
                        raise ContextUnavailable('time_budget')
                    for key, _ in selector.select(left):
                        data = os.read(key.fileobj.fileno(), 8192)
                        if not data:
                            selector.unregister(key.fileobj)
                        elif key.data == 'stdout':
                            output.extend(data)
                            if len(output) > maximum:
                                raise ContextUnavailable('response_size')
                        else:
                            stderr_size += len(data)
                            if stderr_size > 65536:
                                raise ContextUnavailable('response_size')
            if child.wait(timeout=max(.001, timeout - (time.monotonic() - started))):
                raise ContextUnavailable('git_read')
            completed = True
            return bytes(output)
        except subprocess.TimeoutExpired:
            raise ContextUnavailable('time_budget') from None
        finally:
            try:
                if child is not None:
                    try:
                        if not completed and child.poll() is None:
                            # Signal only the still-running session created by
                            # this Popen. Darwin may report EPERM after exit.
                            try:
                                if os.getpgid(child.pid) != child.pid:
                                    raise ContextUnavailable('git_read')
                                os.killpg(child.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            except PermissionError:
                                if child.poll() is None:
                                    raise
                        try:
                            child.wait(timeout=min(1, max(.001, timeout -
                                (time.monotonic() - started))))
                        except subprocess.TimeoutExpired:
                            raise ContextUnavailable('time_budget') from None
                    finally:
                        child.stdout.close()
                        child.stderr.close()
            finally:
                # A cleanup failure cannot restore consumed review time.
                self.shared.seconds -= max(0, time.monotonic() - started)


def _entry(git, revision, path):
    raw = git.run('ls-tree', '-z', revision, '--', path, maximum=1024)
    try:
        metadata, found = raw.rstrip(b'\0').decode('ascii').split('\t')
        mode, kind, sha = metadata.split(' ')
    except (ValueError, UnicodeError):
        raise ContextUnavailable('tree_shape') from None
    if (found != path or kind != 'blob' or mode not in ('100644', '100755')
            or not re.fullmatch('[0-9a-f]{40}', sha)):
        raise ContextUnavailable('tree_identity')
    return mode, sha


def fetch_context(pr, base, descriptions, shared):
    """Fetch only fixed GitHub revisions; publish no raw blob in the PR object."""
    if (not re.fullmatch('[A-Za-z0-9-]+', pr.owner)
            or not re.fullmatch('[A-Za-z0-9_.-]+', pr.repo) or pr.repo in ('.', '..')
            or not all(re.fullmatch('[0-9a-f]{40}', s) for s in (pr.head_sha, base))):
        raise ContextUnavailable('identity')
    from .source_context import safe_source_path
    if not descriptions or len(descriptions) > 4 or not all(map(safe_source_path, descriptions)):
        raise ContextUnavailable('manifest')
    with tempfile.TemporaryDirectory(prefix='karen-diff-context-') as directory:
        git = BoundedGit(directory, shared)
        git.run('init', '--bare', '--template=', '--object-format=sha1', '-q')
        git.run('remote', 'add', 'origin', 'https://github.com/{}/{}.git'.format(pr.owner, pr.repo))
        git.run('config', 'remote.origin.promisor', 'true')
        git.run('config', 'remote.origin.partialclonefilter', 'blob:none')
        # Bound ancestry work; an older common ancestor simply leaves the
        # original material unchanged rather than starting an expansion loop.
        git.run('fetch', '-q', '--no-tags', '--depth=64', '--filter=blob:none',
                'origin', pr.head_sha, base, network=True)
        ancestor = git.run('merge-base', base, pr.head_sha, maximum=64).decode().strip()
        if not re.fullmatch('[0-9a-f]{40}', ancestor):
            raise ContextUnavailable('base_identity')
        blobs = {}
        for path, info in descriptions.items():
            entries = [_entry(git, revision, path) for revision in (ancestor, pr.head_sha)]
            if entries[1] != (info['mode'], info['blob_sha']):
                raise ContextUnavailable('head_identity')
            pair = []
            for mode, sha in entries:
                # This is the only allowed lazy transfer: exact tree-pinned
                # blobs, immediately bounded and later independently hashed.
                data = git.run('cat-file', 'blob', sha, network=True,
                               maximum=MAX_SOURCE_BYTES * 4)
                pair.append(Blob(sha, mode, data))
            blobs[path] = tuple(pair)
        # Every object is now local. A missing one cannot initiate a network
        # fetch, external diff driver, text conversion, hook or checkout.
        diff = git.run('diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                       '--full-index', '--function-context', '--unified=16',
                       ancestor, pr.head_sha, '--', *descriptions,
                       maximum=MAX_SOURCE_BYTES).decode('utf-8')
        return GitContext((pr.owner, pr.repo, pr.number, pr.head_sha), base, diff, blobs)

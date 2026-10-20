"""Real Git hunks must remain bounded evidence, never review authority."""
from dataclasses import replace
import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import chunk_context as c, diff_context as d
from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.checks import PRInfo

def oid(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()

@pytest.fixture
def packet(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                       cwd=tmp_path).decode()
    git('init', '-q')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@invalid')
    path = 'src/runtime.py'
    (tmp_path / 'src').mkdir()
    raw = ''.join('def check_{}(value):\n    # bounded implementation context {}\n'
                  '    normalized = value.strip()\n    return normalized\n\n'.format(i, 'x' * 80)
                  for i in range(140)).encode()
    (tmp_path / path).write_bytes(raw)
    git('add', path)
    git('commit', '-qm', 'Before')
    base = git('rev-parse', 'HEAD').strip()
    changed = raw.replace(b'def check_70(value):', b'def check_70(value, strict=False):')
    (tmp_path / path).write_bytes(changed)
    git('commit', '-qam', 'After')
    head = git('rev-parse', 'HEAD').strip()
    original = git('diff', '--no-ext-diff', '--no-textconv', '--full-index', base, head)
    expanded = git('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--full-index',
                   '--function-context', '--unified=16', base, head)
    # A second file makes this a genuinely chunked review. Its original bytes
    # remain in static checks and in the unmodified chunk plan.
    document = ('diff --git a/guide.md b/guide.md\nnew file mode 100644\n'
                '--- /dev/null\n+++ b/guide.md\n@@ -0,0 +1 @@\n+' + 'guidance ' * 1550 + '\n')
    pr = PRInfo('owner', 'repo', 17, head, 'Fix runtime', 'author', False,
                original + document, {'tests': 'success'}, [path, 'guide.md'], [])
    blobs = {path: (d.Blob(oid(raw), '100644', raw), d.Blob(oid(changed), '100644', changed))}
    info = {path: dict(path=path, head_sha=head, blob_sha=oid(changed), bytes=len(changed),
                       mode='100644', tree_sha=git('rev-parse', head + '^{tree}').strip())}
    candidate = d.GitContext((pr.owner, pr.repo, pr.number, head), base, expanded, blobs)
    return pr, base, candidate, info, git, tmp_path

def enrich(packet, loader=None):
    pr, base, candidate, info, _, _ = packet
    listing = {'sha': next(iter(info.values()))['tree_sha'], 'truncated': False, 'tree': [
        dict(path='src', type='tree', mode='040000', sha='d' * 40),
        dict(path='src/runtime.py', type='blob', mode='100644',
             sha=info['src/runtime.py']['blob_sha'], size=info['src/runtime.py']['bytes'])]}
    reads = []
    def read(argv, **kwargs):
        reads.append(argv)
        response = ({'sha': pr.head_sha, 'tree': {'sha': listing['sha']}}
                    if '/commits/' in argv[-1] else listing)
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), '')
    with patch.object(s, 'budgeted_gh', side_effect=read):
        result = d.prepare_context(pr, base, loader or (lambda *args: candidate))
    return result, reads

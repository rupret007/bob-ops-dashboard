"""Bounded, in-memory source evidence from immutable GitHub objects only."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time

from ..github_reads import run as budgeted_gh
from .checks import SECRET_PATTERNS
from .verified_source_cache import VerifiedSourceSnapshots
from .deadline import remaining_timeout

MAX_FILE_BYTES = 16000
MAX_SOURCE_BYTES = 48000
MAX_API_CALLS = 6  # One commit, one tree, and at most four blobs per review.
SOURCE_EXTENSIONS = frozenset(('py js jsx ts tsx mjs cjs sh go rs swift kt java c h cpp hpp cs'.split()))
ARTIFACTS = frozenset(('auth credentials secrets keys sessions logs evidence fixtures memory memories '
                       'runtime artifacts rollbacks data backups history conversations transcripts '
                       'recordings screenshots personal contacts node_modules vendor dist build').split())
PRIVATE_TEXT = re.compile(
    r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\+[1-9][0-9]{7,14}\b|'
    r'\b[0-9]{3}[- .][0-9]{3}[- .][0-9]{4}\b|\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b|'
    r'/(?:Users|home)/[^/\s"\']+|[\w.-]+\.ts\.net\b|'
    r'(?i:\b(?:full_name|first_name|last_name|phone|address|recipient|contact|'
    r'api[_-]?key|apikey|password|passwd|pwd|secret|token|credential)["\']?\s*[:=]\s*["\'][^"\']+)')


class SourceUnavailable(RuntimeError):
    """Only fixed diagnostic codes, never remote text or source values."""
    CODES = frozenset(('source_budget source_read response_size response_shape source_identity_or_path '
                       'commit_identity tree_identity_or_incomplete tree_shape source_metadata '
                       'blob_identity source_encoding blob_hash source_nontext source_private').split())
    METADATA_REASONS = frozenset(('source_entry_missing source_parent_metadata source_type_or_mode '
                                  'source_size_invalid source_file_limit source_total_limit source_blob_sha').split())

    def __init__(self, code, *, metadata_reason=None):
        self.code = code if code in self.CODES else 'source_unavailable'
        # Optional operational detail must not change the established error API.
        self.metadata_reason = (metadata_reason if self.code == 'source_metadata'
                                and type(metadata_reason) is str and metadata_reason in self.METADATA_REASONS
                                else None)
        super().__init__(self.code)


def safe_source_path(path):
    if (not isinstance(path, str) or len(path) > 240
            or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*', path)):
        return False
    parts = path.split('/')
    return (all(part and not part.startswith('.') and part.lower() not in ARTIFACTS for part in parts)
            and parts[-1].rsplit('.', 1)[-1] in SOURCE_EXTENSIONS
            and not re.search(r'(?i)(?:^|[/_.-])(?:auth|credentials?|secrets?|keys?|tokens?|passwords?|memory|fixtures?)(?:[_.-]|$)', path))


class PinnedSourceContext:
    def __init__(self, pr):
        self.identity = (pr.owner, pr.repo, pr.head_sha)
        self.pr_number = getattr(pr, 'number', None)
        self._snapshots = VerifiedSourceSnapshots(self.identity, self.pr_number)
        self.calls, self.bytes = 0, 0
        self.tree, self.tree_sha, self.sources = None, None, {}
        self.seconds = 30.0

    def previously_verified(self, pr):
        """Copies of immutable snapshots, never caller-mutable cache entries.

        Only fetch's completed hash, metadata and privacy checks create these
        snapshots. Another PR, repository or revision cannot reuse this cache.
        This performs no IO and cannot replenish source/API budgets.
        """
        return self._snapshots.for_pr(pr)

    def _json(self, suffix, maximum):
        from .gate import review_checkpoint
        review_checkpoint()
        if self.calls >= MAX_API_CALLS or self.seconds <= 0:
            raise SourceUnavailable('source_budget')
        self.calls += 1
        owner, repo, _ = self.identity
        started = time.monotonic()
        try:
            result = budgeted_gh(['api', '--method', 'GET', 'repos/{}/{}/git/{}'.format(owner, repo, suffix)],
                                 timeout=remaining_timeout(min(10, self.seconds)))
            if result.returncode or time.monotonic() - started > self.seconds:
                raise SourceUnavailable('source_read')
            if len(result.stdout.encode('utf-8')) > maximum:
                raise SourceUnavailable('response_size')
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise SourceUnavailable('response_shape')
            return value
        except SourceUnavailable:
            raise
        except Exception:
            raise SourceUnavailable('source_read') from None
        finally:
            self.seconds -= max(0, time.monotonic() - started)

    def fetch(self, pr, path):
        owner, repo, head = self.identity
        if ((pr.owner, pr.repo, pr.head_sha) != self.identity
                or not isinstance(owner, str) or not isinstance(repo, str)
                or not re.fullmatch(r'[A-Za-z0-9-]+', owner)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+', repo)
                or repo in ('.', '..')
                or not isinstance(head, str) or not re.fullmatch(r'[0-9a-f]{40}', head) or not safe_source_path(path)):
            raise SourceUnavailable('source_identity_or_path')
        if path in self.sources:
            return self.sources[path]
        if self.tree is None:
            commit = self._json('commits/' + head, 65536)
            tree_sha = commit.get('tree', {}).get('sha') if isinstance(commit.get('tree'), dict) else None
            if commit.get('sha') != head or not isinstance(tree_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', tree_sha):
                raise SourceUnavailable('commit_identity')
            listing = self._json('trees/' + tree_sha + '?recursive=1', 2 * 1024 * 1024)
            if listing.get('sha') != tree_sha or listing.get('truncated') is not False or not isinstance(listing.get('tree'), list):
                raise SourceUnavailable('tree_identity_or_incomplete')
            tree = {}
            for entry in listing['tree']:
                if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or entry['path'] in tree:
                    raise SourceUnavailable('tree_shape')
                tree[entry['path']] = entry
            self.tree = tree
            self.tree_sha = tree_sha
        parts = path.split('/')
        for length in range(1, len(parts)):
            parent = self.tree.get('/'.join(parts[:length]), {})
            if parent.get('type') != 'tree' or parent.get('mode') != '040000':
                raise SourceUnavailable('source_metadata', metadata_reason='source_parent_metadata')
        # Preserve validation order and the legacy primary code; the optional
        # fixed detail contains no requested path, source value or remote text.
        if path not in self.tree:
            raise SourceUnavailable('source_metadata', metadata_reason='source_entry_missing')
        entry = self.tree[path]
        size, sha = entry.get('size'), entry.get('sha')
        if entry.get('type') != 'blob' or entry.get('mode') not in ('100644', '100755'):
            raise SourceUnavailable('source_metadata', metadata_reason='source_type_or_mode')
        if type(size) is not int or size < 0:
            raise SourceUnavailable('source_metadata', metadata_reason='source_size_invalid')
        if size > MAX_FILE_BYTES:
            raise SourceUnavailable('source_metadata', metadata_reason='source_file_limit')
        if self.bytes + size > MAX_SOURCE_BYTES:
            raise SourceUnavailable('source_metadata', metadata_reason='source_total_limit')
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise SourceUnavailable('source_metadata', metadata_reason='source_blob_sha')
        blob = self._json('blobs/' + sha, MAX_FILE_BYTES * 2 + 2048)
        if blob.get('sha') != sha or type(blob.get('size')) is not int or blob['size'] != size or blob.get('encoding') != 'base64':
            raise SourceUnavailable('blob_identity')
        try:
            raw = base64.b64decode(''.join(blob['content'].split()), validate=True)
            text = raw.decode('utf-8')
        except (KeyError, TypeError, AttributeError, ValueError, UnicodeError):
            raise SourceUnavailable('source_encoding') from None
        digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if len(raw) != size or digest != sha:
            raise SourceUnavailable('blob_hash')
        if any(not c.isprintable() and c not in '\n\r\t' for c in text):
            raise SourceUnavailable('source_nontext')
        # Do not redact partial evidence and pretend the complete file was seen.
        # No fixture exceptions: unchanged source has not passed the diff scan.
        if PRIVATE_TEXT.search(text) or any(re.search(pattern, text) for pattern, _ in SECRET_PATTERNS):
            raise SourceUnavailable('source_private')
        self.bytes += size
        source = dict(path=path, head_sha=head, tree_sha=self.tree_sha, blob_sha=sha, bytes=size, source=text)
        self.sources[path] = source
        self._snapshots.remember(source)
        return source

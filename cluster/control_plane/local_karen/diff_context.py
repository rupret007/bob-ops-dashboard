"""Bounded supplemental diff context; never a replacement for complete source.

Static checks and chunk boundaries retain the original GitHub patch. Extra
lines must belong to authenticated immutable Git blobs and cannot change any
addition/deletion, its source coordinate, or the normal full-source limits.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import logging
import re

from .checks import PRInfo, SECRET_PATTERNS
from .source_context import (MAX_FILE_BYTES, MAX_SOURCE_BYTES, PRIVATE_TEXT,
                             PinnedSourceContext, SourceUnavailable, safe_source_path)

from .diff_patch import (ContextUnavailable, _sections, _index, parse_section, canonical_section)

logger = logging.getLogger(__name__)




@dataclass(frozen=True)
class Blob:
    sha: str
    mode: str
    data: bytes


@dataclass(frozen=True)
class GitContext:
    identity: tuple
    base: str
    diff: str
    blobs: dict


@dataclass(frozen=True)
class DiffContext:
    identity: tuple
    original_hash: str
    files: tuple
    source_bytes: int


@dataclass
class ContextualPRInfo(PRInfo):
    pinned_diff_context: DiffContext | None = None
    pinned_source_context: object = None


def _identity(pr):
    return pr.owner, pr.repo, pr.number, pr.head_sha


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()










def eligible_paths(diff):
    from .chunk_context import MAX_CONTEXT_FILES, MAX_CONTEXT_SIZE
    if not isinstance(diff, str) or len(diff.encode('utf-8')) > MAX_CONTEXT_SIZE:
        return []
    try:
        sections = _sections(diff)
        paths = [p for p, s in sections.items() if safe_source_path(p) and _index(s)]
        for path in paths:
            parse_section(sections[path])
        return paths if len(paths) <= MAX_CONTEXT_FILES else []
    except (ContextUnavailable, UnicodeError):
        return []


def _validate_blob(blob, expected_sha, mapping):
    # This is internal checksum work, not publication of a complete source file.
    # The bound is below the existing 2 MiB metadata response bound. Emitted
    # source views retain the original 16,000/48,000 byte limits below.
    from .chunk_context import MAX_CONTEXT_FILES
    if (not isinstance(blob, Blob) or blob.mode not in ('100644', '100755')
            or not isinstance(blob.data, bytes)
            or len(blob.data) > MAX_SOURCE_BYTES * MAX_CONTEXT_FILES
            or not isinstance(blob.sha, str) or not re.fullmatch('[0-9a-f]{40}', blob.sha)
            or not blob.sha.startswith(expected_sha)):
        raise ContextUnavailable('blob_identity')
    digest = hashlib.sha1(b'blob ' + str(len(blob.data)).encode() + b'\0' + blob.data).hexdigest()
    if digest != blob.sha:
        raise ContextUnavailable('blob_hash')
    text = blob.data.decode('utf-8')
    if any(not c.isprintable() and c not in '\n\r\t' for c in text):
        raise ContextUnavailable('source_nontext')
    lines = blob.data.splitlines(keepends=True)
    if any(n <= 0 or n > len(lines) or lines[n - 1] != value for n, value in mapping.items()):
        raise ContextUnavailable('context_line_identity')
    exposed = b''.join(mapping.values())
    if len(exposed) > MAX_FILE_BYTES:
        raise ContextUnavailable('file_budget')
    source = exposed.decode('utf-8')
    if PRIVATE_TEXT.search(source) or any(re.search(p, source) for p, _ in SECRET_PATTERNS):
        raise ContextUnavailable('source_private')
    return len(exposed)


def validate_expansion(pr, candidate, base, descriptions):
    from .chunk_context import MAX_CONTEXT_FILES, MAX_CONTEXT_SIZE, context_size
    if (not isinstance(candidate, GitContext) or candidate.identity != _identity(pr)
            or candidate.base != base or not re.fullmatch('[0-9a-f]{40}', base)):
        raise ContextUnavailable('identity')
    original, expanded = _sections(pr.diff), _sections(candidate.diff)
    paths = eligible_paths(pr.diff)
    if not paths or set(expanded) != set(paths) or set(candidate.blobs) != set(paths):
        raise ContextUnavailable('manifest')
    entries, total = [], 0
    for path in paths:
        before_events, _ = parse_section(original[path])
        after_events, views = parse_section(expanded[path])
        if before_events != after_events:
            raise ContextUnavailable('changed_events')
        old_index, new_index = _index(original[path]), _index(expanded[path])
        if (new_index is None or any(len(sha) != 40 for sha in new_index[:2])
                or old_index[2] != new_index[2]):
            raise ContextUnavailable('file_mode')
        old_blob, new_blob = candidate.blobs[path]
        info = descriptions[path]
        if (new_blob.sha != info['blob_sha'] or len(new_blob.data) != info['bytes']
                or old_blob.mode != old_index[2] or new_blob.mode != old_index[2]
                or not new_index[0].startswith(old_index[0])
                or not new_index[1].startswith(old_index[1])):
            raise ContextUnavailable('blob_identity')
        total += _validate_blob(old_blob, new_index[0], views[0])
        total += _validate_blob(new_blob, new_index[1], views[1])
        entries.append(dict(path=path, diff=canonical_section(path, expanded[path])))
    if total > MAX_SOURCE_BYTES or len(entries) > MAX_CONTEXT_FILES or max(context_size(entries)) > MAX_CONTEXT_SIZE:
        raise ContextUnavailable('context_budget')
    return DiffContext(_identity(pr), _hash(pr.diff), tuple((e['path'], e['diff']) for e in entries), total)


def lookup_context(pr, path):
    if type(pr) is not ContextualPRInfo:
        return None
    context = pr.pinned_diff_context
    if (not isinstance(context, DiffContext) or context.identity != _identity(pr)
            or context.original_hash != _hash(pr.diff)):
        return None
    return dict(context.files).get(path)


def prepare_context(pr, base, loader):
    """Optional enhancement; failed attempts keep the same shared source budget."""
    from .reviewers import REVIEW_DIFF_LIMIT
    # Only the existing chunked-review protocol accepts proactive complements.
    # Leave single-chunk preparation and its resource use unchanged.
    if len(pr.diff) <= REVIEW_DIFF_LIMIT:
        return pr
    paths = eligible_paths(pr.diff)
    if not paths or not isinstance(base, str) or not re.fullmatch('[0-9a-f]{40}', base):
        return pr
    from cluster.never_touch import is_never_touch
    if is_never_touch(pr.owner + '/' + pr.repo):
        return pr
    shared = PinnedSourceContext(pr)
    result = ContextualPRInfo(**{f.name: getattr(pr, f.name) for f in fields(PRInfo)},
                              pinned_source_context=shared)
    try:
        descriptions = {path: shared.describe(pr, path) for path in paths}
        # Bound both revisions before initiating a Git object transfer.
        for path in paths:
            events, _ = parse_section(_sections(pr.diff)[path])
            estimate = descriptions[path]['bytes'] + sum((1 if k == '-' else -1) *
                len(value) for k, _, value in events)
            if not 0 <= max(estimate, descriptions[path]['bytes']) <= MAX_SOURCE_BYTES * 4:
                raise ContextUnavailable('object_budget')
        candidate = loader(pr, base, descriptions, shared)
        result.pinned_diff_context = validate_expansion(pr, candidate, base, descriptions)
        if shared.bytes + result.pinned_diff_context.source_bytes > MAX_SOURCE_BYTES:
            raise ContextUnavailable('context_budget')
        shared.bytes += result.pinned_diff_context.source_bytes
        logger.info('Supplemental diff context: status=verified; files=%s; source_bytes=%s',
                    len(paths), result.pinned_diff_context.source_bytes)
    except (ContextUnavailable, SourceUnavailable, UnicodeError, OSError, ValueError, TypeError, KeyError) as error:
        result.pinned_diff_context = None
        # Never include remote output, filenames or exception values.
        logger.info('Supplemental diff context: status=unavailable; error_type=%s', type(error).__name__)
    return result

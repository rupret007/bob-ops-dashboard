"""Strict unified-diff parsing for bounded immutable Git evidence."""
from __future__ import annotations

import re


class ContextUnavailable(ValueError):
    """Operational diagnostics never contain source or command output."""

def _sections(diff):
    from .chunk_context import _file_sections
    if not isinstance(diff, str) or any(not c.isprintable() and c not in '\n\r\t' for c in diff):
        raise ContextUnavailable('patch_shape')
    result = {}
    for item in _file_sections(diff):
        path = item['path']
        if not path or path in result:
            raise ContextUnavailable('patch_shape')
        result[path] = diff[item['start']:item['end']]
    if not result:
        raise ContextUnavailable('patch_shape')
    return result

def _index(section):
    metadata = section.split('\n@@ ', 1)[0]
    if any(s in metadata for s in ('new file mode', 'deleted file mode', 'old mode',
                                   'new mode', 'rename from', 'copy from', 'GIT binary patch',
                                   'Binary files')):
        return None
    match = re.search(r'(?m)^index ([0-9a-f]{7,40})\.\.([0-9a-f]{7,40}) (100644|100755)$', metadata)
    return match.groups() if match else None

def parse_section(section):
    """Validate hunk counts and retain exact bytes, positions and newline state."""
    events, views = [], ({}, {})
    active = False
    old = new = remaining_old = remaining_new = 0
    previous = None
    for line in section.split('\n'):
        if line.startswith('@@ '):
            if active and (remaining_old or remaining_new):
                raise ContextUnavailable('hunk_counts')
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$', line)
            if not match:
                raise ContextUnavailable('hunk_shape')
            old, count_old, new, count_new = match.groups()
            old, new = int(old), int(new)
            remaining_old = int(count_old) if count_old is not None else 1
            remaining_new = int(count_new) if count_new is not None else 1
            active, previous = True, None
        elif active and line == '\\ No newline at end of file':
            if previous is None:
                raise ContextUnavailable('newline_marker')
            for side, position in previous:
                if not views[side][position].endswith(b'\n'):
                    raise ContextUnavailable('newline_marker')
                views[side][position] = views[side][position][:-1]
        elif active and (remaining_old or remaining_new):
            if not line or line[0] not in ' +-':
                raise ContextUnavailable('hunk_shape')
            kind, value = line[0], line[1:]
            raw = value.encode('utf-8') + b'\n'
            previous = []
            if kind in ' -':
                if remaining_old <= 0 or old in views[0]:
                    raise ContextUnavailable('hunk_counts')
                views[0][old] = raw
                previous.append((0, old))
                if kind == '-':
                    events.append(('-', old))
                old += 1
                remaining_old -= 1
            if kind in ' +':
                if remaining_new <= 0 or new in views[1]:
                    raise ContextUnavailable('hunk_counts')
                views[1][new] = raw
                previous.append((1, new))
                if kind == '+':
                    events.append(('+', new))
                new += 1
                remaining_new -= 1
        elif active and line:
            raise ContextUnavailable('hunk_trailer')
    if not active or remaining_old or remaining_new:
        raise ContextUnavailable('hunk_counts')
    return [(kind, position, views[0 if kind == '-' else 1][position])
            for kind, position in events], views

def canonical_section(path, section):
    # Hunk labels are hints, not mapped source lines. Publish only validated
    # line coordinates; do not accidentally expose unverified header snippets.
    lines = section.split('\n')
    index = _index(section)
    expected = ['diff --git a/{0} b/{0}'.format(path),
                'index {}..{} {}'.format(*index), '--- a/' + path, '+++ b/' + path]
    if lines[:4] != expected:
        raise ContextUnavailable('patch_headers')
    return '\n'.join(re.sub(r'(@@ -[0-9,]+ \+[0-9,]+ @@).*', r'\1', line)
                     if line.startswith('@@ ') else line for line in lines)

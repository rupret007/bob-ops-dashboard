"""Migrate only positively recovered machine-generated legacy health alerts."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from .health_state import unknown


def resolve_legacy(root, status):
    group = status.get('groups', {}).get('watchdog', {})
    passed = {c['name'] for c in group.get('checks', [])
              if c.get('passed') and not c.get('execution_error') and not unknown(c)}
    path = root / 'conductor/NEEDS_JEFF.md'
    if not path.exists() or not passed:
        return
    text = path.read_text()
    pattern = re.compile(r'(?ms)^## (\d{4}-\d\d-\d\d \d\d:\d\d UTC) - Watchdog\n\n'
                         r'\*\*Issue:\*\* ([a-zA-Z0-9_-]+)\n.*?^---\n(?:\n)?')
    removed = []
    def replace(match):
        stamp = datetime.strptime(match[1], '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc).timestamp()
        if match[2] in passed and stamp <= group.get('finished_at', 0):
            removed.append(match[0])
            return ''
        return match[0]
    updated = pattern.sub(replace, text)
    if removed:
        archive = path.with_name('NEEDS_JEFF.resolved.md')
        archived = archive.read_text() if archive.exists() else ''
        for entry in removed:
            marker = '<!-- health-migrated:' + hashlib.sha256(entry.encode()).hexdigest() + ' -->'
            if marker not in archived:
                archived += '\n' + marker + '\n' + entry
        archive.write_text(archived)
        path.write_text(updated)

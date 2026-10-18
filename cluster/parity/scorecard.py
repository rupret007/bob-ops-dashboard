"""Report audited coverage without promoting planned scenarios to passes."""
import json
from pathlib import Path
from datetime import datetime, timezone


def render():
    rules = json.loads(Path(__file__).with_name('rules.json').read_text())
    covered = sum(r['status'] == 'covered' for r in rules)
    lines = ['Parity: {} / {} covered; {} missing.'.format(covered, len(rules), len(rules)-covered),
             'Updated: ' + datetime.now(timezone.utc).isoformat(),
             'Planned scenarios are not passing evidence. No Grok calls were made.',
             'First-try pass rate, time to merge, fix success, false-PASS count: unverified.', '']
    lines += ['{} [{}] {}'.format(r['id'], r['status'], r['rule']) for r in rules]
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    target = Path.home() / '.local/share/storypool/PARITY.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render())

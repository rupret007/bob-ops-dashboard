"""Read-only, bounded evidence for the engine's work-flow requirement."""
import re
from datetime import datetime, timezone


def stale_alerts(data_dir, now, tail):
    """Active dated entries become stale after 60 minutes; archives are ignored.

    Accept the monitor's ISO dates and the watchdog/conductor's UTC headings.
    Never publish alert contents, which may contain private operator context.
    """
    count = 0
    for path in (data_dir / 'NEEDS_JEFF.md', data_dir / 'conductor/NEEDS_JEFF.md'):
        try:
            if path.stat().st_size > 262144:
                count += 1  # A truncated read cannot prove absence of stale alerts.
                continue
        except FileNotFoundError:
            continue
        except OSError:
            count += 1
            continue
        for line in tail(path, 262144):
            text = re.sub(r'^(?:## |[-*] )', '', line).strip()
            pattern = r'(\d{4}-\d\d-\d\d(?:T\S+| \d\d:\d\d UTC))'
            match = re.search(pattern, text) if line.startswith('## ') else re.match(pattern, text)
            if not match:
                continue
            value = match.group(1)
            try:
                if ' UTC' in value:
                    date = datetime.strptime(value, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
                else:
                    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                count += 1
                continue
            if re.match(r'\s*[—:-]*\s*(?:Resolved:|\[x\])', text[match.end():], re.I):
                continue
            if date.tzinfo is None or now - date.timestamp() >= 3600:
                count += 1
    return count


def flow_reasons(local, peers, now):
    """Peers contain only fresh, self-reported local evidence (never an echo)."""
    values = [local]
    reasons = []
    for name, peer in peers.items():
        value = peer.get('flow_local')
        if not isinstance(value, dict) or value.get('machine') != name:
            reasons.append(name + ' work-flow evidence unavailable')
        else:
            values.append(value)
    for key, reason in (
            ('last_verdict_at', 'No completed Karen verdict in the last 60 minutes'),
            ('last_success_at', 'No verified successful job in the last 60 minutes')):
        timestamps = [v.get(key) for v in values]
        if not any(isinstance(t, (int, float)) and not isinstance(t, bool)
                   and now - 3600 <= t <= now for t in timestamps):
            reasons.append(reason)
    if any(type(v.get('stale_alerts')) is not int or v['stale_alerts'] < 0 for v in values):
        reasons.append('NEEDS_JEFF freshness unavailable')
    elif sum(v['stale_alerts'] for v in values):
        reasons.append('Unresolved stale NEEDS_JEFF entries (60 minutes or older)')
    return reasons

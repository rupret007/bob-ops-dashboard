"""Read only approved usage sources, returning numeric aggregates, never raw text."""
import json
from datetime import datetime
from pathlib import Path
from usage_collect import BOARD_TZ, collect_codex_for_day, aggregate_gemini_spend
from cluster.live_metrics import number


def weekly_limit(sessions_root: Path):
    latest = None
    # Only session JSONL; never auth files or databases. No mtime inference.
    for path in sessions_root.glob('*/*/*/*.jsonl'):
        if 'auth' in path.name.lower() or path.is_symlink():
            continue
        try:
            with path.open() as source:
                for line in source:
                    try:
                        row = json.loads(line)
                        payload = row.get('payload') or {}
                        if row.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                            continue
                        limits = payload.get('rate_limits') or {}
                        for key in ('primary', 'secondary'):
                            period = limits.get(key) or {}
                            pct = number(period.get('used_percent'))
                            if period.get('window_minutes') != 10080 or pct is None or pct > 100:
                                continue
                            stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()
                            reset = number(period.get('resets_at'))
                            observed = (stamp, pct, reset)
                            if latest is None or stamp > latest[0]:
                                latest = observed
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
        except OSError:
            continue
    now = datetime.now().timestamp()
    if latest is None or latest[0] > now or now - latest[0] > 7 * 86400 or (latest[2] is not None and latest[2] <= now):
        return None
    return latest[1]


def usage_today(sessions_root, lane_logs):
    day = datetime.now(BOARD_TZ).date().isoformat()
    codex = collect_codex_for_day(sessions_root, day)
    gemini = aggregate_gemini_spend(lane_logs / 'gemini-spend.txt').get(day)
    # Tokens are partial unless both observed sources are present. No inferred zero.
    tokens = None
    if codex.total_tokens is not None and gemini and gemini.get('tokens') is not None:
        tokens = codex.total_tokens + gemini['tokens']
    return {'tokens_today': tokens, 'codex_weekly_pct': weekly_limit(sessions_root),
            'gemini_usd_today': gemini.get('usd') if gemini else None, 'queue_depth': None}

"""Read only approved usage sources, returning numeric aggregates, never raw text."""
from datetime import datetime
from pathlib import Path
from usage_collect import BOARD_TZ, collect_codex_for_day, aggregate_gemini_spend


def weekly_limit(sessions_root: Path):
    from cluster.control_plane.codex_quota import read_codex_quota
    return read_codex_quota(sessions_root)['weekly_pct']


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

"""Read-only quality/spend scorecard computations. No inferred costs or attribution."""
from __future__ import annotations
from datetime import datetime, timezone
import html
import math
import re

PROVIDERS = ('codex', 'gemini', 'cursor', 'claude', 'minimax', 'grok_bot')
LABELS = {'grok_bot': 'Grok Bot (conserve-only)'}
# (direction, good inclusive bound, watch inclusive bound); units in docs/scorecards.md.
THRESHOLDS = {
    'karen_first_pass_pct': ('high', 90, 70), 'karen_rounds': ('low', 1, 2),
    'ci_first_push_pct': ('high', 95, 80), 'rollback_48h_pct': ('low', 0, 5),
    'gemini_agreement_pct': ('high', 90, 70), 'gemini_missed': ('low', 0, 1),
    'gemini_false_alarm': ('low', 0, 1), 'stuck_prs': ('low', 0, 2),
    'tokens': ('low', 10_000_000, 100_000_000), 'usd': ('low', 25, 100),
    'codex_weekly_pct': ('low', 50, 80), 'tokens_per_merge': ('low', 1_000_000, 5_000_000),
    'usd_per_merge': ('low', 5, 20), 'tokens_per_pass': ('low', 1_000_000, 5_000_000),
    'usd_per_pass': ('low', 5, 20), 'wasted_runs': ('low', 0, 2),
    'empty_gemini': ('low', 0, 1), 'refusals': ('low', 0, 1),
    'idle_slot_hours': ('low', 1, 6),
}


def timestamp(value):
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.timestamp() if dt.tzinfo else None
    except (ValueError, OverflowError):
        return None


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def status(name, value):
    if not numeric(value):
        return 'no data'
    direction, good, watch = THRESHOLDS[name]
    if direction == 'high':
        return 'good' if value >= good else 'watch' if value >= watch else 'bad'
    return 'good' if value <= good else 'watch' if value <= watch else 'bad'


def metric(name, value, observed=0, eligible=0):
    return {'value': round(value, 4) if numeric(value) else None,
            'status': status(name, value), 'observed': observed, 'eligible': eligible,
            'partial': observed < eligible}


def rate(name, values, eligible=None):
    known = [v for v in values if type(v) is bool]
    return metric(name, 100 * sum(known) / len(known) if known else None,
                  len(known), len(values) if eligible is None else eligible)


def total(name, rows, field):
    known = [r[field] for r in rows if numeric(r.get(field))]
    # Complete sum needs every record; an absent source is not observed zero.
    value = sum(known) if rows and len(known) == len(rows) else None
    return metric(name, value, len(known), len(rows))


def compute_group(records, now, seconds):
    start = now - seconds
    def inside(value):
        stamp = timestamp(value)
        return stamp is not None and start < stamp <= now
    prs = [r for r in records if r.get('kind') == 'pr']
    review_rows = [r for r in records if r.get('kind') == 'review' and timestamp(r.get('at')) is not None and timestamp(r['at']) <= now]
    reviews = {}
    for r in sorted(review_rows, key=lambda r: timestamp(r['at'])):
        if r.get('verdict') in ('PASS', 'FAIL') and r.get('sha') and r.get('pr') is not None:
            key = (r.get('repo'), r['pr'])
            # Deduplicate identical observations, not distinct FAIL/PASS rounds on one head.
            reviews.setdefault(key, {})[(r['sha'], r['at'], r['verdict'])] = r
    first, rounds, passes = [], [], 0
    for heads in reviews.values():
        ordered = sorted(heads.values(), key=lambda r: timestamp(r['at']))
        passing = next((r for r in ordered if r['verdict'] == 'PASS'), None)
        # Explicit round from existing notes, or full review history from GitHub.
        first_row = ordered[0]
        complete = first_row.get('history_complete') is True or first_row.get('round') == 1
        if inside(first_row['at']):
            first.append(first_row['verdict'] == 'PASS' if complete else None)
        if passing and inside(passing['at']):
            passes += 1
            n = passing.get('round')
            if not numeric(n) or n < 1:
                n = ordered.index(passing) + 1 if complete else None
            rounds.append(n)
    known_rounds = [n for n in rounds if numeric(n)]
    merged = [p for p in prs if inside(p.get('merged_at'))]
    mature = [p for p in prs if timestamp(p.get('merged_at')) is not None and inside(timestamp(p['merged_at']) + 48 * 3600)]
    rollback = []
    for p in mature:
        reverted = timestamp(p.get('reverted_at'))
        if reverted is not None:
            rollback.append(0 <= reverted - timestamp(p['merged_at']) <= 48 * 3600)
        else:
            rollback.append(False if p.get('rollback_observed') is True else None)
    ci = [p.get('first_push_green') for p in prs if inside(p.get('opened_at'))]
    # Open PR age is NOT inferred from updatedAt or commit authored/committed time.
    opened = [p for p in prs if p.get('state') == 'open' and timestamp(p.get('opened_at')) is not None and timestamp(p['opened_at']) <= now]
    head_known = [p for p in opened if timestamp(p.get('head_changed_at')) is not None and timestamp(p['head_changed_at']) <= now]
    stuck = sum(now - timestamp(p['head_changed_at']) >= 6 * 3600 for p in head_known)
    comparisons = [r for r in records if r.get('kind') == 'comparison' and inside(r.get('at'))]
    comparable = [r for r in comparisons if isinstance(r.get('karen_blockers'), list) and isinstance(r.get('gemini_blockers'), list) and r.get('adjudicated') is True]
    caught = missed = false = 0
    for r in comparable:
        karen, gemini = set(r['karen_blockers']), set(r['gemini_blockers'])
        caught += len(karen & gemini)
        missed += len(karen - gemini)
        false += len(gemini - karen)
    agreement = 100 * caught / (caught + missed + false) if caught + missed + false else (100 if comparable else None)
    spends = [r for r in records if r.get('kind') == 'spend' and inside(r.get('at'))]
    runs = [r for r in records if r.get('kind') == 'run' and inside(r.get('at'))]
    def count_runs(name, field, predicate):
        known = [r for r in runs if type(r.get(field)) is bool]
        return metric(name, sum(predicate(r[field]) for r in known) if known else None, len(known), len(runs))
    out = {
        'karen_first_pass_pct': rate('karen_first_pass_pct', first),
        'karen_rounds': metric('karen_rounds', sum(known_rounds) / len(known_rounds) if known_rounds else None, len(known_rounds), len(rounds)),
        'ci_first_push_pct': rate('ci_first_push_pct', ci),
        'rollback_48h_pct': rate('rollback_48h_pct', rollback),
        'gemini_agreement_pct': metric('gemini_agreement_pct', agreement, len(comparable), len(comparisons)),
        'gemini_missed': metric('gemini_missed', missed if comparable else None, len(comparable), len(comparisons)),
        'gemini_false_alarm': metric('gemini_false_alarm', false if comparable else None, len(comparable), len(comparisons)),
        'stuck_prs': metric('stuck_prs', stuck if len(head_known) == len(opened) and prs else None, len(head_known), len(opened)),
        'tokens': total('tokens', spends, 'tokens'), 'usd': total('usd', spends, 'usd'),
        'wasted_runs': count_runs('wasted_runs', 'usable_output', lambda x: x is False),
        'empty_gemini': count_runs('empty_gemini', 'empty_gemini', lambda x: x is True),
        'refusals': count_runs('refusals', 'refused', lambda x: x is True),
    }
    limits = [r for r in records if r.get('kind') == 'limit' and inside(r.get('at')) and numeric(r.get('percent')) and r['percent'] <= 100 and (timestamp(r.get('reset_at')) is None or timestamp(r['reset_at']) > now)]
    latest = max(limits, key=lambda r: timestamp(r['at']), default=None)
    out['codex_weekly_pct'] = metric('codex_weekly_pct', latest['percent'] if latest else None, int(latest is not None), 1)
    # Window-wide ratios, not causal job costs. Require complete numerator and denominator scope.
    for suffix, denominator in [('merge', len(merged)), ('pass', passes)]:
        for field in ('tokens', 'usd'):
            name = f'{field}_per_{suffix}'
            value = out[field]['value']
            out[name] = metric(name, value / denominator if value is not None and denominator else None,
                               out[field]['observed'], out[field]['eligible'])
    return out


def idle_metrics(records, now, seconds):
    result = {}
    for host in ('mini', 'nucbox'):
        intervals = [r for r in records if r.get('kind') == 'idle' and r.get('host') == host]
        valid = []
        for r in intervals:
            begin, end = timestamp(r.get('start')), timestamp(r.get('end'))
            if begin is None or end is None or end <= begin or not numeric(r.get('idle_slots')):
                continue
            begin, end = max(begin, now - seconds), min(end, now)
            if end > begin:
                valid.append((begin, end, r['idle_slots']))
        valid = sorted(set(valid))
        overlap = any(a[1] > b[0] for a, b in zip(valid, valid[1:]))
        hours = sum((end - begin) * slots / 3600 for begin, end, slots in valid) if valid and not overlap else None
        result[host] = metric('idle_slot_hours', hours, len(valid), len(intervals))
    return result


def build_scorecards(records, *, now=None, repos=()):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    # Grok is never a lane or inferred from other providers' observations.
    records = [r for r in records if isinstance(r, dict) and r.get('provider') != 'grok_bot']
    records = [dict(r, repo=r.get('repo') if safe_repo(r.get('repo')) else None) for r in records]
    repositories = sorted({r for r in repos if safe_repo(r)} | {r['repo'] for r in records if safe_repo(r.get('repo'))})
    windows = {}
    for name, seconds in (('24h', 86400), ('7d', 7 * 86400)):
        providers = {p: compute_group([r for r in records if r.get('provider') == p], now, seconds) for p in PROVIDERS}
        providers['grok_bot'] = {k: dict(v, status='unknown') for k, v in providers['grok_bot'].items()}
        windows[name] = {'providers': providers,
                         'repos': {repo: compute_group([r for r in records if r.get('repo') == repo], now, seconds) for repo in repositories},
                         'idle_slot_hours': idle_metrics(records, now, seconds)}
    return {'v': 'scorecards/v1', 'generated_at': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'windows': windows}


def safe_repo(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', value)) and not re.search(r'\d+\.\d+\.\d+\.\d+', value)


def normalize_scorecards(raw):
    """Project only known metrics and safe repository labels from a saved aggregate."""
    clean = build_scorecards([])
    if not isinstance(raw, dict) or raw.get('v') != 'scorecards/v1':
        return clean
    stamp = timestamp(raw.get('generated_at'))
    clean['generated_at'] = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else ''
    for window, groups in clean['windows'].items():
        source = (raw.get('windows') or {}).get(window) or {}
        for category in ('providers', 'repos', 'idle_slot_hours'):
            incoming = source.get(category) or {}
            if not isinstance(incoming, dict):
                continue
            for label, metrics in incoming.items():
                if category == 'repos' and not safe_repo(label):
                    continue
                if category != 'repos' and label not in groups[category]:
                    continue
                if category == 'idle_slot_hours':
                    metrics = {'idle_slot_hours': metrics}
                if not isinstance(metrics, dict):
                    continue
                target = {}
                for name in (['idle_slot_hours'] if category == 'idle_slot_hours' else [k for k in THRESHOLDS if k != 'idle_slot_hours']):
                    item = metrics.get(name) or {}
                    if not isinstance(item, dict):
                        item = {}
                    observed = item.get('observed') if numeric(item.get('observed')) else 0
                    eligible = item.get('eligible') if numeric(item.get('eligible')) else 0
                    target[name] = metric(name, item.get('value'), observed, eligible)
                if label == 'grok_bot':
                    target = {k: dict(metric(k, None), status='unknown') for k in target}
                groups[category][label] = target['idle_slot_hours'] if category == 'idle_slot_hours' else target
    return clean


def scorecards_html(data):
    """Escaped static renderer; Pages retains its existing refresh cadence."""
    data = normalize_scorecards(data)
    bits = ['<section class="scorecards"><h2>Quality &amp; spend scorecards</h2>',
            '<p>Rolling windows. No data means no direct evidence. Partial coverage is shown; costs are never estimated.</p>']
    for window, groups in data['windows'].items():
        bits.append('<h3>' + html.escape(window) + '</h3>')
        for category in ('providers', 'repos'):
            for label, metrics in groups[category].items():
                bits.append('<details><summary>' + html.escape(LABELS.get(label, label)) + '</summary><table>')
                for name, item in metrics.items():
                    value = item['value'] if item['value'] is not None else ('unknown' if label == 'grok_bot' else 'no data')
                    coverage = f"{item['observed']}/{item['eligible']} observed" + ('; partial' if item['partial'] else '')
                    bits.append('<tr><th>' + html.escape(name.replace('_', ' ')) + '</th><td>' + html.escape(str(value)) + '</td><td>' + html.escape(item['status']) + '</td><td>' + html.escape(coverage) + '</td></tr>')
                bits.append('</table></details>')
        for host, item in groups['idle_slot_hours'].items():
            bits.append('<p>' + html.escape(host) + ' idle slot hours: ' + html.escape(str(item['value']) if item['value'] is not None else 'no data') + ' (' + html.escape(item['status']) + ')</p>')
    bits.append('<p>Grok Bot (conserve-only): unknown — no direct source; lanes never consume it.</p></section>')
    return ''.join(bits)

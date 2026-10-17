"""Opt-in readers of existing session/log/note/GitHub evidence. No source writes."""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from usage_collect import BOARD_TZ
from scorecards import PROVIDERS, build_scorecards, timestamp, numeric, scorecards_html

GH = str(Path.home() / '.local/bin/gh')
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def lines(path):
    if path.is_symlink() or path.name in ('auth.json', 'chat.db') or 'auth' in path.name.lower():
        return []
    try:
        return path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []


def log_time(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=BOARD_TZ)).isoformat()
    except ValueError:
        return None


def session_records(root):
    records = []
    for path in root.glob('*/*/*/*.jsonl'):
        seen = set()
        for line in lines(path):
            try:
                row = json.loads(line)
                p = row.get('payload') or {}
                at = row.get('timestamp')
                if row.get('type') != 'event_msg' or p.get('type') != 'token_count' or timestamp(at) is None:
                    continue
                # Only explicit per-event tokens support rolling windows. Cumulative
                # session totals are not assigned to a day/hour by file timestamps.
                usage = (p.get('info') or {}).get('last_token_usage') or {}
                cumulative = (p.get('info') or {}).get('total_token_usage') or {}
                fingerprint = json.dumps([cumulative, usage], sort_keys=True)
                if (usage or cumulative) and fingerprint not in seen:
                    records.append({'kind': 'spend', 'provider': 'codex', 'at': at,
                                    'tokens': usage.get('total_tokens'), 'usd': None})
                    seen.add(fingerprint)
                for period in (p.get('rate_limits') or {}).values():
                    if isinstance(period, dict) and period.get('window_minutes') == 10080:
                        records.append({'kind': 'limit', 'provider': 'codex', 'at': at,
                                        'percent': period.get('used_percent'), 'reset_at': period.get('resets_at')})
            except (ValueError, TypeError, AttributeError):
                continue
    return records


def gemini_records(path):
    records = []
    for line in lines(path):
        match = re.match(r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?)\s+(.+)$', line)
        if not match:
            continue
        at = log_time(match[1])
        if at is None:
            continue
        body = match[2]
        cost = re.search(r'\$(\d+(?:\.\d+)?)\s*$', body)
        # Existing positional log: run-id model input output $cost. Also labeled fields.
        labeled = dict(re.findall(r'\b(in|out|tokens)=(\d+)\b', body))
        tokens = int(labeled['tokens']) if 'tokens' in labeled else (int(labeled['in']) + int(labeled['out']) if 'in' in labeled and 'out' in labeled else None)
        positional = re.fullmatch(r'\S+\s+\S+\s+(\d+)\s+(\d+)\s+\$\d+(?:\.\d+)?', body)
        if positional and not labeled:
            tokens = int(positional[1]) + int(positional[2])
        records.append({'kind': 'spend', 'provider': 'gemini', 'at': at,
                        'tokens': tokens, 'usd': float(cost[1]) if cost else None})
    return records


def provider_records(path):
    records = []
    for line in lines(path):
        cols = line.split('\t')
        if len(cols) < 4 or cols[2].lower() not in PROVIDERS[:-1]:
            continue
        at = log_time(cols[0])
        if at is None:
            continue
        provider = cols[2].lower()
        extra = dict(c.split('=', 1) for c in cols[4:] if '=' in c)
        row = {'kind': 'run', 'provider': provider, 'at': at}
        if REPO.fullmatch(extra.get('repo', '')):
            row['repo'] = extra['repo']
        for key in ('usable_output', 'refused'):
            if extra.get(key) in ('true', 'false'):
                row[key] = extra[key] == 'true'
        if provider == 'gemini' and extra.get('output_bytes', '').isdigit():
            row['empty_gemini'] = int(extra['output_bytes']) == 0
        records.append(row)
        # Optional providers only: Codex/Gemini have their own primary sources,
        # so do not double-count a log row repeated in both sources.
        if provider in ('cursor', 'claude', 'minimax'):
            spend = dict(row, kind='spend', tokens=None, usd=None)
            for key in ('tokens', 'usd'):
                try:
                    value = float(extra[key])
                    if numeric(value):
                        spend[key] = value
                except (KeyError, ValueError):
                    pass
            records.append(spend)
    return records


def note_records(text, *, repo=None, pr=None, at=None):
    """Accept explicit evidence, never semantic guesses about reviewer agreement.

    Existing structured SCORECARD JSON lines are optional. Legacy Karen lines
    (- #N SHA PASS/FAIL) are recognized but do not imply complete round history.
    """
    out = []
    for line in text.splitlines():
        heading = re.match(r'^## .*?(\d{4}-\d{2}-\d{2}) (\d{1,2}:\d{2}) CT', line)
        if heading:
            at = log_time(heading[1] + 'T' + heading[2].zfill(5) + ':00')
        stripped = line.strip()
        if stripped.startswith('SCORECARD '):
            try:
                r = json.loads(stripped[10:])
                if r.get('kind') not in ('review', 'comparison', 'pr', 'idle'):
                    continue
                if repo is not None and r.get('repo', repo) != repo:
                    continue
                if pr is not None and r.get('pr', pr) != pr:
                    continue
                r = dict(r)
                r.setdefault('repo', repo)
                r.setdefault('pr', pr)
                r.setdefault('at', at)
                out.append(r)
            except (ValueError, TypeError, AttributeError):
                continue
        else:
            # Match explicit Karen gate syntax only; arbitrary prose is not a verdict.
            match = re.search(r'(?:^-\s+#|KAREN\s+pr=)(\d+)\s+(?:sha=)?([0-9a-f]{7,40})\s+(PASS|FAIL)\b', stripped, re.I)
            if not match:
                continue
            fields = dict(re.findall(r'\b(repo|provider|round|at)=([^\s]+)', stripped))
            if pr is not None and int(match[1]) != pr:
                continue
            r = {'kind': 'review', 'pr': int(match[1]), 'repo': fields.get('repo', repo),
                 'sha': match[2].lower(), 'verdict': match[3].upper(), 'at': fields.get('at', at)}
            if fields.get('provider') in PROVIDERS[:-1]:
                r['provider'] = fields['provider']
            if fields.get('round', '').isdigit():
                r['round'] = int(fields['round'])
            out.append(r)
    return out


def gh_json(args):
    # Caller chooses read-only endpoints. Never print stderr (may contain private URLs).
    result = subprocess.run([GH, *args], capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError('GitHub evidence unavailable')
    return json.loads(result.stdout)


def gh_pages(endpoint):
    pages = gh_json(['api', '--method', 'GET', '--paginate', '--slurp', endpoint])
    return [row for page in pages for row in page]


def github_records(repo, karen_logins, read=gh_pages):
    if not REPO.fullmatch(repo):
        raise ValueError('expected owner/repo')
    # Event history may be incomplete; absence never proves no movement/first push.
    events = read(f'repos/{repo}/events?per_page=100')
    records = []
    for pr in read(f'repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=100'):
        n = pr['number']
        row = {'kind': 'pr', 'repo': repo, 'pr': n, 'state': pr['state'],
               'opened_at': pr['created_at'], 'merged_at': pr.get('merged_at')}
        labels = [x['name'].removeprefix('provider:') for x in pr.get('labels', []) if x['name'].startswith('provider:')]
        if len(labels) == 1 and labels[0] in PROVIDERS[:-1]:
            row['provider'] = labels[0]
        head = pr['head']['sha']
        moves = [e['created_at'] for e in events if
                 (e.get('type') == 'PushEvent' and e.get('payload', {}).get('head') == head and
                  e.get('payload', {}).get('ref') == 'refs/heads/' + pr['head']['ref']) or
                 (e.get('type') == 'PullRequestEvent' and e.get('payload', {}).get('number') == n and
                  e['payload'].get('action') in ('opened', 'synchronize') and
                  e['payload'].get('pull_request', {}).get('head', {}).get('sha') == head)]
        if moves:
            row['head_changed_at'] = max(moves)
        opens = [e for e in events if e.get('type') == 'PullRequestEvent' and e.get('payload', {}).get('number') == n and e['payload'].get('action') == 'opened']
        if opens:
            first_head = opens[0]['payload']['pull_request']['head']['sha']
            # Check-runs are evidence on the recorded opening head. Earliest run per
            # name wins, so a later green rerun cannot erase a red first push.
            checks = read(f'repos/{repo}/commits/{first_head}/check-runs?filter=all&per_page=100')
            earliest = {}
            for check in sorted(checks, key=lambda c: c['id']):
                earliest.setdefault((check.get('app', {}).get('id'), check['name']), check)
            conclusions = [c.get('conclusion') for c in earliest.values()]
            if conclusions and all(c is not None for c in conclusions):
                row['first_push_green'] = all(c == 'success' for c in conclusions)
        records.append(row)
        comments = read(f'repos/{repo}/issues/{n}/comments?per_page=100')
        comments += read(f'repos/{repo}/pulls/{n}/reviews?per_page=100')
        for comment in comments:
            if comment.get('user', {}).get('login') in karen_logins:
                notes = note_records(comment.get('body', ''), repo=repo, pr=n, at=comment.get('created_at') or comment.get('submitted_at'))
                for note in notes:
                    if row.get('provider'):
                        note.setdefault('provider', row['provider'])
                records.extend(notes)
    return records


def github_pages(endpoint):
    # check-runs pagination has an envelope instead of an array.
    pages = gh_json(['api', '--method', 'GET', '--paginate', '--slurp', endpoint])
    return [row for page in pages for row in (page['check_runs'] if isinstance(page, dict) else page)]


def collect(sessions, logs, notes=(), repos=(), karen_logins=()):
    records = session_records(sessions) + gemini_records(logs / 'gemini-spend.txt') + provider_records(logs / 'provider-usage.tsv')
    for path in notes:
        records.extend(note_records('\n'.join(lines(path))))
    unavailable = []
    for repo in repos:
        try:
            records.extend(github_records(repo, set(karen_logins), read=github_pages))
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
            unavailable.append(repo)
    # Merge explicit PR evidence with the GH PR once; no denominator duplication.
    prs, other = {}, []
    for r in records:
        if r.get('kind') == 'pr':
            key = (r.get('repo'), r.get('pr'))
            prs.setdefault(key, {}).update({k: v for k, v in r.items() if v is not None})
        else:
            other.append(r)
    return build_scorecards([*prs.values(), *other], repos=repos), unavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions', type=Path, required=True)
    parser.add_argument('--logs', type=Path, required=True)
    parser.add_argument('--notes', type=Path, nargs='*', default=[])
    parser.add_argument('--repo', action='append', default=[])
    parser.add_argument('--karen-login', action='append', default=[])
    parser.add_argument('--html', action='store_true')
    args = parser.parse_args()
    data, unavailable = collect(args.sessions, args.logs, args.notes, args.repo, args.karen_login)
    data['github_unavailable'] = unavailable
    print(scorecards_html(data) if args.html else json.dumps(data, allow_nan=False))


if __name__ == '__main__':
    main()

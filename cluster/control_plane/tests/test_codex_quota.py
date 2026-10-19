"""Real plan observations, bounded reads and fail-closed Codex admission."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cluster.control_plane import codex_quota as q

NOW = datetime(2026, 10, 19, 0, 1, tzinfo=timezone.utc).timestamp()


def event(pct=21.0, age=0, window='primary', **changes):
    limits = {'limit_id': 'codex', 'plan_type': 'pro',
              window: {'used_percent': pct, 'window_minutes': 10080,
                       'resets_at': int(NOW + 86400)}}
    limits.update(changes)
    return {'timestamp': datetime.fromtimestamp(NOW-age, timezone.utc).isoformat(),
            'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': limits}}


def write(root, rows, name='session.jsonl', day='19'):
    path = root / '2026' / '10' / day / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return path


@pytest.mark.parametrize('window', ['primary', 'secondary'])
def test_weekly_window_can_be_primary_or_secondary(tmp_path, window):
    write(tmp_path, [event(window=window)])
    value = q.read_codex_quota(tmp_path, now=NOW)
    assert value == dict(status='ok', weekly_pct=21.0,
                         observed_at=datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
                         resets_at=int(NOW+86400), window_minutes=10080,
                         window=window, plan_type='pro', source=q.SOURCE)


def test_actual_timestamp_wins_over_file_name_mtime_and_day_rollover(tmp_path):
    earlier = write(tmp_path, [event(10, age=30)], 'z-latest-name.jsonl')
    later = write(tmp_path, [event(74, age=1)], 'a-old-name.jsonl', day='18')
    os.utime(earlier, (NOW, NOW))
    os.utime(later, (NOW-86400, NOW-86400))
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] == 74.0


def test_records_select_by_timestamp_not_append_order_and_ignore_other_ids(tmp_path):
    write(tmp_path, [event(74, age=1), event(5, age=10),
                     event(100, limit_id='codex_other')])
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] == 74.0


@pytest.mark.parametrize('field,bad', [
    ('used_percent', True), ('used_percent', float('nan')),
    ('used_percent', float('inf')), ('used_percent', -1), ('used_percent', 101),
    ('used_percent', 10**1000), ('used_percent', '21'),
    ('window_minutes', True), ('window_minutes', float('nan')),
    ('window_minutes', 300), ('resets_at', True), ('resets_at', float('nan')),
    ('resets_at', int(NOW)), ('resets_at', int(NOW+8*86400)),
])
def test_newest_malformed_observation_never_falls_back_to_old_valid(tmp_path, field, bad):
    newest = event()
    newest['payload']['rate_limits']['primary'][field] = bad
    write(tmp_path, [event(5, age=5), newest])
    value = q.read_codex_quota(tmp_path, now=NOW)
    assert value['status'] == 'malformed'
    assert value['weekly_pct'] is None


@pytest.mark.parametrize('age,reset', [(3601, NOW+86400), (60, NOW-1)])
def test_stale_or_reset_window_is_unknown_for_admission(tmp_path, age, reset):
    row = event(age=age)
    row['payload']['rate_limits']['primary']['resets_at'] = int(reset)
    write(tmp_path, [row])
    value = q.read_codex_quota(tmp_path, now=NOW)
    assert value['status'] == 'stale'
    assert value['weekly_pct'] is None


@pytest.mark.parametrize('stamp', [None, 'invalid-private-value', '2026-10-19T00:01:00',
                                  '2026-10-19T00:02:01+00:00'])
def test_invalid_or_future_timestamps_never_authorize(tmp_path, stamp):
    row = event()
    row['timestamp'] = stamp
    write(tmp_path, [event(5, age=10), row])
    value = q.read_codex_quota(tmp_path, now=NOW)
    assert value['weekly_pct'] is None
    assert 'invalid-private-value' not in json.dumps(value)


def test_conflicting_weekly_windows_and_equal_timestamp_take_no_lower_vote(tmp_path):
    row = event()
    row['payload']['rate_limits']['secondary'] = dict(
        row['payload']['rate_limits']['primary'], used_percent=80)
    path = write(tmp_path, [row])
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] is None
    path.unlink()
    write(tmp_path, [event(21), event(80)])
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] is None
    assert q.read_codex_quota(tmp_path, now=NOW)['status'] == 'malformed'


def test_partial_append_and_tail_start_never_parse_fragment_or_leak(tmp_path, monkeypatch):
    monkeypatch.setattr(q, 'MAX_TAIL_BYTES', 1024)
    path = write(tmp_path, [event()])
    path.write_bytes(b'private-session-content'*1000 + b'\n' + path.read_bytes()
                     + b'{"private-incomplete-append":')
    value = q.read_codex_quota(tmp_path, now=NOW)
    assert value['weekly_pct'] == 21
    assert 'private' not in json.dumps(value)


def test_missing_unrelated_malformed_and_unreadable_sources_fail_closed(tmp_path, monkeypatch):
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] is None
    path = write(tmp_path, [event(limit_id='another-provider')])
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] is None
    path.write_text('{bad complete record}\n')
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] is None
    original = Path.open
    def unreadable(self, *args, **kwargs):
        if self == path:
            raise PermissionError('private path must not escape')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', unreadable)
    value = q.read_codex_quota(tmp_path, now=NOW)
    assert value['weekly_pct'] is None
    assert 'private' not in json.dumps(value)


def test_scan_is_bounded_and_symlinked_session_files_are_not_read(tmp_path, monkeypatch):
    for index in range(q.MAX_FILES + 3):
        path = write(tmp_path, [event()], 'session-{}.jsonl'.format(index))
        os.utime(path, (NOW-index, NOW-index))
    (path.parent / 'not-a-session.jsonl').symlink_to(tmp_path / 'missing-private-target')
    original, reads = q._tail_records, []
    def counted(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(q, '_tail_records', counted)
    assert q.read_codex_quota(tmp_path, now=NOW)['weekly_pct'] == 21
    assert len(reads) == q.MAX_FILES


def test_unrecognized_plan_and_session_values_never_escape(tmp_path):
    row = event(plan_type='private-plan-content')
    row['payload']['prompt'] = 'private-prompt-content'
    row['payload']['rate_limits']['credits'] = {'private-value': 'private-credit-content'}
    write(tmp_path, [row], name='private-file-name.jsonl')
    value = q.read_codex_quota(tmp_path, now=NOW)
    assert value['weekly_pct'] == 21
    assert value['plan_type'] is None
    assert 'private' not in json.dumps(value)



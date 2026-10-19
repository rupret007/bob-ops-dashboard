"""Persistent health incidents and actual cross-process repair exclusion."""
import json
import multiprocessing
import time

from cluster.control_plane import health_state as state


def test_three_failures_dedupe_positive_resolution_and_unknown(tmp_path):
    status = {'incidents': {}}
    failure = {'name': 'labels_rupret007_webjam', 'passed': False,
               'message': 'Could not execute label check', 'execution_error': True}
    for tick in range(2):
        state.observe(status, 'nucbox', 'watchdog', [failure], tick)
    issue = next(iter(status['incidents'].values()))
    assert issue['failures'] == 2 and not issue['notify']
    state.observe(status, 'nucbox', 'watchdog', [failure], 3)
    assert len(status['incidents']) == 1 and issue['notify']
    state.render_alerts(tmp_path, status)
    state.render_alerts(tmp_path, status)
    assert (tmp_path / 'NEEDS_JEFF.md').read_text().count('labels_rupret007_webjam') == 1
    unknown = dict(failure, passed=True, message='WARNING: label check deferred by GitHub API budget; labels unverified')
    state.observe(status, 'nucbox', 'watchdog', [unknown], 4)
    assert issue['state'] == 'firing'
    state.observe(status, 'nucbox', 'watchdog', [failure], 4.5)
    assert issue['state'] == 'firing' and issue['notify']
    state.observe(status, 'nucbox', 'watchdog', [dict(failure, passed=True)], 5)
    assert issue['state'] == 'resolved' and issue['resolved_at'] == 5
    state.render_alerts(tmp_path, status)
    assert 'labels_rupret007_webjam' not in (tmp_path / 'NEEDS_JEFF.md').read_text()


def test_human_text_and_first_seen_survive_restart(tmp_path):
    path = tmp_path / 'NEEDS_JEFF.md'
    path.write_text('# Jeff\n\n- Keep this human action.\n')
    status = {'incidents': {}}
    state.observe(status, 'mini', 'guardian', [{'name': 'dashboard', 'passed': False}], 10)
    state.atomic_json(tmp_path / 'health-status.json', status)
    loaded = state.read_status(tmp_path)
    state.observe(loaded, 'mini', 'guardian', [{'name': 'dashboard', 'passed': False}], 20)
    state.render_alerts(tmp_path, loaded)
    text = path.read_text()
    assert text.startswith('# Jeff\n\n- Keep this human action.\n')
    assert next(iter(loaded['incidents'].values()))['first_seen'] == 10


def test_target_cooldown_is_shared_and_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    called = []
    assert state.repair_once('dashboard', lambda: called.append(1) or True, now=100) == (True, True)
    assert state.repair_once('dashboard', lambda: called.append(2) or True, now=200) == (False, False)
    assert state.repair_once('dashboard', lambda: called.append(3) or True, now=401) == (True, True)
    assert called == [1, 3]


def test_only_positive_new_health_resolves_owned_legacy_alert(tmp_path):
    from cluster.control_plane.health_legacy import resolve_legacy
    conductor = tmp_path / 'conductor'
    conductor.mkdir()
    alert = conductor / 'NEEDS_JEFF.md'
    old = ('## 2026-10-18 20:00 UTC - Watchdog\n\n**Issue:** head_lag\n\n'
           '**Details:** Behind origin\n\n**Action needed:** Manual review required\n\n---\n\n')
    alert.write_text(old + '## Jeff action\n\nKeep this.\n')
    status = {'groups': {'watchdog': {'finished_at': 2000000000,
              'checks': [{'name': 'head_lag', 'passed': True, 'unknown': True}]}}}
    resolve_legacy(tmp_path, status)
    assert alert.read_text().startswith(old)
    status['groups']['watchdog']['checks'][0]['unknown'] = False
    resolve_legacy(tmp_path, status)
    resolve_legacy(tmp_path, status)
    assert alert.read_text() == '## Jeff action\n\nKeep this.\n'
    assert (conductor / 'NEEDS_JEFF.resolved.md').read_text().count('**Issue:** head_lag') == 1


def _race_lock(root, ready, result):
    ready.wait()
    with state.recovery_lock(root, blocking=False) as acquired:
        result.put(acquired)
        if acquired:
            time.sleep(0.25)


def test_only_one_process_owns_recovery_and_nested_calls_are_safe(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    ready, result = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_race_lock, args=(tmp_path, ready, result)) for _ in range(2)]
    for process in processes:
        process.start()
    ready.set()
    assert sorted(result.get(timeout=10) for _ in processes) == [False, True]
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    with state.recovery_lock(tmp_path) as first:
        with state.recovery_lock(tmp_path) as nested:
            assert first and nested


def test_snapshot_expiry_identity_and_future_are_unknown(tmp_path):
    status = {'schema': 1, 'machine': 'mini', 'timestamp': 100,
              'groups': {'guardian': {'finished_at': 100, 'period': 120, 'checks': [{'passed': True}]}}}
    assert state.verdict(status, 'mini', 101)['state'] == 'green'
    assert state.verdict(status, 'nucbox', 101)['state'] == 'yellow'
    assert state.verdict(status, 'mini', 500)['state'] == 'yellow'
    assert state.verdict(status, 'mini', 50)['state'] == 'yellow'
    status['groups']['guardian']['checks'][0]['passed'] = False
    assert state.verdict(status, 'mini', 101)['state'] == 'red'


def test_missing_group_and_error_cannot_prove_green():
    status = {'schema': 1, 'machine': 'mini', 'timestamp': 100,
              'expected_groups': ['guardian', 'doctor'],
              'groups': {'guardian': {'finished_at': 100, 'period': 120, 'checks': [{'passed': True}]}}}
    assert state.verdict(status, 'mini', 101)['state'] == 'yellow'
    status['groups']['doctor'] = {'finished_at': 100, 'period': 3600, 'error': 'TimeoutExpired'}
    assert state.verdict(status, 'mini', 101)['state'] == 'yellow'

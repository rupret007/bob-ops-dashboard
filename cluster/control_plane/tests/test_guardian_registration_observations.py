"""Native registration proof resolves only the corresponding health incident."""
import json
import plistlib
import subprocess
from functools import partial
from unittest.mock import Mock

import pytest

from cluster.control_plane import guardian, health_adapters as adapter, health_state as state

TIMER = 'storypool-codex-quota.timer'


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    home, data = tmp_path / 'home', tmp_path / 'state'
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(data))
    monkeypatch.setattr(guardian, 'event', Mock())
    monkeypatch.setattr(guardian, 'authoritative', lambda: False)
    monkeypatch.setattr(guardian, 'recover', partial(guardian.recover, root=home))
    monkeypatch.setattr(guardian, 'guard_dashboard', Mock(return_value={'ok': True, 'restarted': False}))
    monkeypatch.setattr(adapter, 'is_draining', lambda *_: False)
    return home, data


def timer(home, name=TIMER):
    path = home / '.config/systemd/user' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def agent(home, name):
    path = home / 'Library/LaunchAgents' / (name + '.plist')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps({'Label': name, 'ProgramArguments': ['/bin/true']}))
    return path


def reply(code=0, output=''):
    return subprocess.CompletedProcess([], code, output, '')


def incident(machine='nucbox', unit=TIMER):
    status = {'incidents': {}}
    state.observe(status, machine, 'guardian', [dict(name=unit, passed=False)], 10)
    return status, status['incidents'][machine + ':guardian:' + unit]


def test_native_recovery_resolves_and_archives_without_repair_or_reload(monitor, monkeypatch):
    home, data = monitor
    timer(home)
    healthy = [False]
    calls = []
    def native(argv):
        calls.append(argv)
        assert argv[2] in ('is-enabled', 'is-active')
        return reply(0, 'enabled' if argv[2] == 'is-enabled' else 'active') if healthy[0] else reply(
            1 if argv[2] == 'is-enabled' else 3,
            'disabled' if argv[2] == 'is-enabled' else 'inactive')
    monkeypatch.setattr(guardian, 'command', native)
    data.mkdir()
    (data / 'NEEDS_JEFF.md').write_text('# Jeff\n\nKeep this human action.\n')
    status = {'incidents': {}}
    failed = adapter.collect('guardian', 'nucbox', False)
    state.observe(status, 'nucbox', 'guardian', failed['checks'], 10)
    state.render_alerts(data, status)
    issue = status['incidents']['nucbox:guardian:' + TIMER]
    assert issue['state'] == 'firing' and TIMER in (data / 'NEEDS_JEFF.md').read_text()

    healthy[0] = True
    recovered = adapter.collect('guardian', 'nucbox', False)
    state.observe(status, 'nucbox', 'guardian', recovered['checks'], 20)
    assert issue['state'] == 'resolved' and issue['first_seen'] == 10 and issue['resolved_at'] == 20
    assert recovered['details']['actions'] == [] and recovered['details']['reloads'] == 0
    assert not recovered['repair_attempted']
    state.render_alerts(data, status)
    state.render_alerts(data, status)
    assert (data / 'NEEDS_JEFF.md').read_text().startswith('# Jeff\n\nKeep this human action.\n')
    assert TIMER not in (data / 'NEEDS_JEFF.md').read_text()
    assert (data / 'NEEDS_JEFF.resolved.md').read_text().count('health-resolved:nucbox:guardian:' + TIMER) == 1
    assert len(calls) == 4  # Reuse exactly the two existing native reads per cycle.


@pytest.mark.parametrize('enabled,active,unknown', [
    ((0, 'enabled'), (3, 'inactive'), False),
    ((1, 'disabled'), (0, 'active'), False),
    ((1, 'disabled'), (3, 'inactive'), False),
    ((1, ''), (0, 'active'), True),
    ((0, 'enabled'), (1, ''), True),
    ((1, 'private diagnostic'), (0, 'active'), False),
])
def test_failed_or_unconfirmed_native_state_cannot_resolve(monitor, monkeypatch, enabled, active, unknown):
    home, _ = monitor
    timer(home)
    monkeypatch.setattr(guardian, 'command', lambda argv: reply(
        *(enabled if argv[2] == 'is-enabled' else active)))
    status, issue = incident()
    result = adapter.collect('guardian', 'nucbox', False)
    unit_check = next(c for c in result['checks'] if c['name'] == TIMER)
    assert not unit_check['passed'] and unit_check['unknown'] is unknown
    state.observe(status, 'nucbox', 'guardian', result['checks'], 20)
    assert issue['state'] == 'firing' and 'resolved_at' not in issue
    assert 'private diagnostic' not in json.dumps(result)


def test_pending_running_mini_reload_is_not_positive_registration(monitor, monkeypatch):
    home, _ = monitor
    path = agent(home, 'com.storypool.selfupdate')
    path.with_suffix('.reload-pending').touch()
    native = Mock(return_value=reply(0, 'pid = 42'))
    monkeypatch.setattr(guardian, 'command', native)
    status, issue = incident('mini', path.stem)
    result = adapter.collect('guardian', 'mini', False)
    unit_check = next(c for c in result['checks'] if c['name'] == path.stem)
    assert not unit_check['passed'] and unit_check['unknown']
    assert result['details']['actions'] == [] and result['details']['reloads'] == 0
    state.observe(status, 'mini', 'guardian', result['checks'], 20)
    assert issue['state'] == 'firing' and 'resolved_at' not in issue
    assert native.call_count == 1 and native.call_args.args[0][1] == 'print'
    assert path.with_suffix('.reload-pending').exists()


def test_registered_mini_agent_is_observed_without_a_reload(monitor, monkeypatch):
    home, _ = monitor
    path = agent(home, 'com.storypool.selfupdate')
    native = Mock(return_value=reply())
    monkeypatch.setattr(guardian, 'command', native)
    result = adapter.collect('guardian', 'mini', False)
    unit_check = next(c for c in result['checks'] if c['name'] == path.stem)
    assert unit_check['passed'] and not unit_check['unknown']
    assert result['details']['actions'] == [] and result['details']['reloads'] == 0
    assert native.call_count == 1


def test_absent_unit_is_not_blanket_positive_recovery(monitor, monkeypatch):
    native = Mock(side_effect=AssertionError('No native unit should be queried'))
    monkeypatch.setattr(guardian, 'command', native)
    status, issue = incident()
    result = adapter.collect('guardian', 'nucbox', False)
    state.observe(status, 'nucbox', 'guardian', result['checks'], 20)
    assert issue['state'] == 'firing' and 'resolved_at' not in issue
    assert TIMER not in [c['name'] for c in result['checks']]
    native.assert_not_called()


@pytest.mark.parametrize('machine', ['nucbox', 'mini'])
def test_authoritative_disabled_legacy_units_remain_excluded(monitor, monkeypatch, machine):
    home, _ = monitor
    monkeypatch.setattr(guardian, 'authoritative', lambda: True)
    if machine == 'nucbox':
        for name in ('watchdog', 'guardian', 'ops-doctor', 'shadow'):
            timer(home, 'storypool-' + name + '.timer')
        timer(home)
        expected = TIMER
    else:
        for name in ('watchdog', 'guardian', 'ops-doctor', 'shadow', 'health', 'karen-poller'):
            agent(home, 'com.storypool.' + name)
        agent(home, 'com.storypool.codex-quota')
        expected = 'com.storypool.codex-quota'
    native = Mock(return_value=reply())
    monkeypatch.setattr(guardian, 'command', native)
    result = adapter.collect('guardian', machine, False)
    assert [c['name'] for c in result['checks']] == ['dashboard_http', expected]
    assert result['details']['actions'] == [] and result['details']['reloads'] == 0
    assert all(expected in ' '.join(c.args[0]) for c in native.call_args_list)
    assert native.call_count == (2 if machine == 'nucbox' else 1)


def test_enable_success_is_not_native_proof_and_healthy_units_do_not_inflate_reloads(monitor, monkeypatch):
    home, _ = monitor
    timer(home)
    timer(home, 'storypool-selfupdate.timer')
    healthy = [False]
    def native(argv):
        if argv[2] == 'enable':
            return reply()  # Command success alone does not prove enabled + active.
        if argv[-1] != TIMER or healthy[0]:
            return reply(0, 'enabled' if argv[2] == 'is-enabled' else 'active')
        return reply(1 if argv[2] == 'is-enabled' else 3,
                     'disabled' if argv[2] == 'is-enabled' else 'inactive')
    monkeypatch.setattr(guardian, 'command', native)
    repair = Mock(side_effect=lambda _target, function: (True, function()))
    monkeypatch.setattr(adapter, 'repair_once', repair)
    status, issue = incident()
    result = adapter.collect('guardian', 'nucbox', True)
    assert result['details']['actions'] == [{'unit': TIMER, 'ok': True}]
    assert result['details']['reloads'] == 1 and result['repair_attempted']
    assert not next(c for c in result['checks'] if c['name'] == TIMER)['passed']
    state.observe(status, 'nucbox', 'guardian', result['checks'], 20)
    assert issue['state'] == 'firing'

    healthy[0] = True
    verified = adapter.collect('guardian', 'nucbox', True)
    state.observe(status, 'nucbox', 'guardian', verified['checks'], 30)
    assert issue['state'] == 'resolved' and issue['resolved_at'] == 30
    assert verified['details']['actions'] == [] and verified['details']['reloads'] == 0
    assert not verified['repair_attempted'] and repair.call_count == 1

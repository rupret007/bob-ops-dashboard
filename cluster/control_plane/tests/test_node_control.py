"""Maintenance is durable, confirmed, scoped and never starts new work."""
import json
import os
from http.server import HTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from cluster.control_plane.node_control import (
    allowed_address, authorized_post, is_draining, read_control, set_maintenance,
)
from cluster.control_plane.dashboard.server import DashboardHandler
from cluster.control_plane.dashboard.data_collector import DataCollector, DashboardData
from cluster.control_plane.dashboard.server import render_dashboard
from cluster.control_plane.dashboard.tachometer import EngineCollector


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('STORYPOOL_MINI_URL', 'https://' + '.'.join(['mini', 'example', 'ts', 'net']) + ':8790')


def test_persists_and_returns_with_audit(tmp_path):
    assert not is_draining()
    set_maintenance(True, 'Jeff via 100\x2e64\x2e1\x2e2')
    assert is_draining() and read_control(tmp_path)['maintenance']
    assert 'Jeff via 100\x2e64\x2e1\x2e2' in (tmp_path / 'HANDOFF.md').read_text()
    set_maintenance(False, 'Jeff via 127.0.0.1')
    assert not is_draining()
    assert len((tmp_path / 'HANDOFF.md').read_text().splitlines()) == 2


@pytest.mark.parametrize('value', ['broken', '[]', '{"maintenance": "false"}'])
def test_corruption_drains_fail_closed(tmp_path, value):
    (tmp_path / 'node-control.json').write_text(value)
    assert is_draining()
    set_maintenance(False, 'operator')
    assert not is_draining()


@pytest.mark.parametrize('addr,allowed', [('127.0.0.1', True), ('::1', True),
    ('.'.join(map(str, [100, 90, 1, 2])), True), ('fd7a:115c:a1e0::1', True), ('192.168.1.2', False),
    ('8.8.8.8', False), ('::ffff:8.8.8.8', False)])
def test_control_network_boundary(addr, allowed):
    assert allowed_address(addr) is allowed


@pytest.mark.parametrize('addr,host,origin,kind', [
    ('8.8.8.8', 'localhost:8790', 'http://localhost:8790', 'application/json'),
    ('127.0.0.1', 'evil.example', 'http://evil.example', 'application/json'),
    ('127.0.0.1', 'localhost:8790', 'http://evil.example', 'application/json'),
    ('127.0.0.1', 'localhost:8790', '', 'application/json'),
    ('127.0.0.1', 'localhost:8790', 'http://localhost:8790', 'text/plain'),
])
def test_csrf_and_rebinding_denied(addr, host, origin, kind):
    assert not authorized_post(addr, host, origin, kind)


@pytest.fixture
def dashboard(tmp_path):
    class Handler(DashboardHandler):
        collector = DataCollector(data_dir=tmp_path, karen_dir=tmp_path)
    Handler.collector.machine = 'nucbox'
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%s' % server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def post(url, body, origin=None):
    req = Request(url + '/api/control/node', data=json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json', 'Origin': origin or url})
    return urlopen(req, timeout=2)


def test_http_confirmed_action_and_engine(dashboard, tmp_path):
    with post(dashboard, {'machine': 'nucbox', 'maintenance': True, 'confirm': True}) as response:
        assert json.load(response)['maintenance']
    with urlopen(dashboard + '/api/engine') as response:
        data = json.load(response)
    assert data['machines']['nucbox']['state'] == 'maintenance'
    assert data['audit'] and data['control']['maintenance']
    assert data['engine'] != 'red'
    with post(dashboard, {'machine': 'nucbox', 'maintenance': False, 'confirm': True}):
        pass
    assert not is_draining()


@pytest.mark.parametrize('body', [
    {'machine': 'nucbox', 'maintenance': True},
    {'machine': 'nucbox', 'maintenance': 'true', 'confirm': True},
    {'machine': 'elsewhere', 'maintenance': True, 'confirm': True},
])
def test_http_rejects_unconfirmed_or_invalid(dashboard, tmp_path, body):
    with pytest.raises(HTTPError) as err:
        post(dashboard, body)
    assert err.value.code == 400
    assert not (tmp_path / 'node-control.json').exists()


def test_http_foreign_origin_cannot_change_state(dashboard, tmp_path):
    with pytest.raises(HTTPError) as err:
        post(dashboard, {'machine': 'nucbox', 'maintenance': True, 'confirm': True}, 'http://evil.example')
    assert err.value.code == 403
    assert not (tmp_path / 'node-control.json').exists()


def test_peer_unreachable_does_not_drain_local_node(dashboard, tmp_path):
    with patch('urllib.request.OpenerDirector.open', side_effect=OSError):
        # Use the unpatched URL opener for the client.
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', int(dashboard.rsplit(':', 1)[1]))
        conn.request('POST', '/api/control/node', json.dumps({'machine': 'mini', 'maintenance': True, 'confirm': True}),
                     {'Content-Type': 'application/json', 'Origin': dashboard})
        assert conn.getresponse().status == 503
        conn.close()
    assert not (tmp_path / 'node-control.json').exists()


def test_dispatcher_drain_skips_all_allocation(tmp_path):
    from cluster.control_plane.dispatcher import Dispatcher
    from cluster.control_plane.job_ledger import JobLedger
    set_maintenance(True, 'operator')
    dispatcher = Dispatcher(machine='mini', log_dir=tmp_path / 'dispatch', job_ledger=JobLedger(tmp_path / 'jobs'))
    with patch('cluster.control_plane.dispatcher._get_open_prs') as prs, \
         patch('cluster.control_plane.dispatcher._spawn_worker') as spawn:
        decisions = dispatcher.evaluate(live=True)
    prs.assert_not_called()
    spawn.assert_not_called()
    assert 'maintenance' in decisions[0].reason


def test_running_worker_can_finish_during_drain(tmp_path):
    from cluster.control_plane.job_ledger import JobLedger
    ledger = JobLedger(tmp_path / 'jobs')
    job = ledger.create_job('owner/repo', 1, 'a' * 40, 'codex', 'nucbox')
    ledger.start_job(job.job_id, os.getpid())
    set_maintenance(True, 'operator')
    assert ledger.expire_stale_leases() == []
    assert ledger.complete_job(job.job_id, 'commit_pushed', 'b' * 40, verified=True).state == 'done'


def test_watchdog_does_not_repair_or_escalate_maintenance(tmp_path):
    from cluster.control_plane.watchdog import run_watchdog
    set_maintenance(True, 'operator')
    with patch('cluster.control_plane.watchdog._write_watchdog_log'), \
         patch('cluster.control_plane.watchdog.check_service_stuck') as check, \
         patch('cluster.control_plane.watchdog.repair_restart_service') as repair, \
         patch('cluster.control_plane.watchdog._escalate_to_jeff') as escalate:
        result = run_watchdog('mini')
    check.assert_not_called()
    repair.assert_not_called()
    escalate.assert_not_called()
    assert result.checks[0].name == 'maintenance' and result.repairs_attempted == 0


def test_worker_queued_before_drain_is_released(tmp_path):
    from cluster.control_plane import fix_worker
    from cluster.control_plane.job_ledger import JobLedger
    ledger = JobLedger(tmp_path / 'jobs')
    job = ledger.create_job('owner/repo', 1, 'a' * 40, 'codex', 'nucbox')
    set_maintenance(True, 'operator')
    with patch.object(fix_worker, 'get_ledger', return_value=ledger), patch.object(fix_worker, 'fix_pr') as fix:
        assert fix_worker.execute(job.job_id) == 0
    fix.assert_not_called()
    assert not ledger.has_active_lease('owner/repo', 1)
    assert ledger._load_jobs()[0].error.startswith('maintenance-before-start')


def test_panel_has_both_nodes_confirmation_and_audit():
    page = render_dashboard(DashboardData(timestamp='', machine='mini', live_mode=True))
    assert "['mini','nucbox']" in page
    assert 'window.confirm' in page and "method:'POST'" in page
    assert 'min-height:44px' in page and 'control-audit' in page
    assert "maintenance:'#8b949e'" in page


def test_conductor_deferred_before_snapshot(tmp_path):
    from cluster.control_plane.conductor import Conductor
    set_maintenance(True, 'operator')
    conductor = object.__new__(Conductor)
    with patch.object(Conductor, 'build_state_snapshot') as snapshot:
        assert conductor.run(live=True)['maintenance']
    snapshot.assert_not_called()


def test_doctor_deferred_before_any_cli(tmp_path):
    from cluster.control_plane import ops_doctor
    set_maintenance(True, 'operator')
    with patch.object(ops_doctor.subprocess, 'run') as command:
        assert ops_doctor.main() == 0
    command.assert_not_called()


def test_karen_defers_reviews(tmp_path):
    from cluster.control_plane.local_karen import poller
    from cluster.control_plane.local_karen.config import KarenConfig
    set_maintenance(True, 'operator')
    with patch.object(poller, 'list_prs_with_label', return_value=[{'number': 1}]), \
         patch.object(poller, 'run_gate') as gate:
        assert poller.poll_and_run(['owner/repo'], KarenConfig(), guard_machine='mini') == []
    gate.assert_not_called()


def test_mesh_declines_fetched_messages_and_advertises_no_caps(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from cluster.mesh_worker import MeshWorker, NodeConfig
    cfg = NodeConfig({'BOB_NODE_USER': 'mini', 'BOB_NODE_TOKEN': 'test-only', 'BOB_ROLE': 'member'})
    executor = MagicMock()
    worker = MeshWorker(cfg, executor, providers={}, detected=set())
    set_maintenance(True, 'operator')
    msg = MagicMock()
    msg.nak = AsyncMock()
    asyncio.run(worker.handle(msg, 'code'))
    msg.nak.assert_awaited_once_with(delay=60)
    executor.run.assert_not_called()
    with patch('cluster.mesh_worker.build_heartbeat', return_value={}) as heartbeat:
        worker.heartbeat()
    assert heartbeat.call_args.args[3] == []


def test_successful_peer_control_updates_only_control_state(dashboard, tmp_path):
    from unittest.mock import MagicMock
    snapshot = tmp_path / 'machines' / 'mini' / 'engine.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps({'synced_at': '2026-01-01T00:00:00Z', 'jobs': {'count': 2}}))
    response = MagicMock()
    response.read.return_value = b'{"maintenance": true, "reason": "Operator requested maintenance"}'
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    with patch('urllib.request.build_opener', return_value=opener):
        # Use HTTPConnection to avoid patching our test client's opener.
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', int(dashboard.rsplit(':', 1)[1]))
        conn.request('POST', '/api/control/node', json.dumps({'machine': 'mini', 'maintenance': True, 'confirm': True}),
                     {'Content-Type': 'application/json', 'Origin': dashboard})
        assert conn.getresponse().status == 200
        conn.close()
    state = json.loads(snapshot.read_text())
    assert state['control']['maintenance'] and state['synced_at'] == '2026-01-01T00:00:00Z'
    assert state['jobs']['count'] == 2
    assert not is_draining()
    assert EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['machines']['mini']['state'] == 'maintenance'


def test_phone_grid_tracks_fit_available_width():
    # A fixed 350px minimum overflows a 360px phone after body/card padding.
    # Keep the track minimum bounded by its container across 360–430px widths.
    page = render_dashboard(DashboardData(timestamp='', machine='mini', live_mode=True))
    assert 'minmax(350px' not in page and 'minmax(150px' not in page
    assert 'minmax(min(100%, 350px), 1fr)' in page
    assert 'table-layout: fixed' in page and 'overflow-wrap: anywhere' in page
    assert 'repeat(2,minmax(0,1fr))' in page


def test_drain_reconciles_dead_running_workers(tmp_path):
    from cluster.control_plane.dispatcher import Dispatcher
    from cluster.control_plane.job_ledger import Job, JobLedger
    ledger = JobLedger(tmp_path / 'jobs')
    job = ledger.create_job('owner/repo', 7, 'a' * 40, 'codex', 'nucbox')
    ledger.start_job(job.job_id, 12345)
    set_maintenance(True, 'operator')
    dispatcher = Dispatcher(machine='nucbox', log_dir=tmp_path / 'dispatch', job_ledger=ledger)
    with patch.object(Job, 'is_process_running', return_value=False):
        dispatcher.evaluate(live=True)
    assert ledger._load_jobs()[0].error == 'worker-died'
    assert ledger._load_jobs()[0].state == 'failed'

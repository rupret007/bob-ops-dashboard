"""Peer transport contact is separate from the health of the remote host."""
import pytest
from cluster.control_plane.health_peer import direct_observation
from cluster.control_plane.health_state import atomic_json, machine_health


def report(machine, now, passed=True):
    return dict(schema=1, machine=machine, timestamp=now,
                rollout={'phase': 'active'}, expected_groups=['guardian'],
                groups={'guardian': dict(finished_at=now, period=120,
                    checks=[dict(name='dashboard_http', passed=passed)])})


def project(tmp_path, *, error=None, received_age=0, peer_offset=0, local_passed=True):
    now = 10000
    remote = report('mini', now + peer_offset)
    remote['observation'] = dict(nonce='fixture-nonce', served_at=now + peer_offset)
    details = direct_observation(remote, 'mini', 'fixture-nonce', now, 0)
    details['received_at'] = now - received_age
    local = report('nucbox', now, local_passed)
    local['groups']['peer'] = dict(finished_at=now, period=120, details=details,
        checks=[dict(name='peer_freshness', passed=True)])
    if error:
        local['groups']['peer'] = dict(finished_at=now, period=120, error=error,
            checks=[dict(name='peer_execution', passed=False, execution_error=True)])
    atomic_json(tmp_path / 'health-status.json', local)
    return machine_health(tmp_path, 'nucbox', now)


@pytest.mark.parametrize('error', ['URLError', 'TimeoutExpired', 'TimeoutError',
    'ConnectionError', 'ConnectionRefusedError', 'ConnectionResetError'])
def test_transport_failure_is_unreachable_and_unconfirmed(tmp_path, error):
    peer = project(tmp_path, error=error)['mini']
    assert peer['state'] == 'yellow' and peer['connectivity'] == 'unreachable'
    assert 'unreachable' in peer['detail'].lower()
    assert 'unconfirmed' in peer['detail'].lower()


def test_invalid_reply_does_not_claim_a_transport_outage(tmp_path):
    peer = project(tmp_path, error='ValueError')['mini']
    assert peer['state'] == 'yellow' and peer['connectivity'] == 'unknown'
    assert 'failed' in peer['detail'].lower() and 'unreachable' not in peer['detail'].lower()


def test_stale_peer_is_unknown_even_if_last_contact_was_healthy(tmp_path):
    peer = project(tmp_path, received_age=500)['mini']
    assert peer['state'] == 'yellow' and peer['connectivity'] == 'unknown'
    assert 'stale' in peer['detail'].lower()


def test_fresh_clock_corrected_peer_remains_reachable(tmp_path):
    peer = project(tmp_path, peer_offset=3600)['mini']
    assert peer['state'] == 'green' and peer['connectivity'] == 'reachable'


def test_local_health_failure_is_preserved(tmp_path):
    local = project(tmp_path, local_passed=False)['nucbox']
    assert local['state'] == 'red' and local.get('connectivity') != 'unreachable'

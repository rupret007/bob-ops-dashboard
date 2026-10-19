"""Age direct peer observations without assuming synchronized host clocks."""
import math


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def observation_time(details, now):
    """Conservative peer time: served time + full RTT + local elapsed time.

    Only the collector creates this anchor after verifying a fresh nonce reply.
    Legacy snapshots retain strict local-clock validation until recollected.
    """
    anchor = details.get('clock_observation')
    if anchor is None:
        return now
    if not isinstance(anchor, dict):
        raise ValueError('Invalid peer clock observation')
    served = anchor.get('served_at')
    elapsed = anchor.get('round_trip_seconds')
    received = details.get('received_at')
    if (not all(number(v) for v in (served, elapsed, received, now))
            or served <= 0 or not 0 <= elapsed <= 5
            or not 0 <= now - received <= 390):
        raise ValueError('Peer clock observation expired or invalid')
    return served + elapsed + now - received


def direct_observation(value, machine, nonce, received, elapsed):
    from .health_state import verdict
    if isinstance(value, dict) and 'observation' not in value:
        # Rolling upgrades: old dashboards get no clock-skew compensation.
        # They must satisfy the original local-clock freshness/identity guard.
        if (not number(elapsed) or not 0 <= elapsed <= 5 or
                verdict(value, machine, received)['detail'] == 'health evidence unavailable or stale'):
            raise ValueError('Legacy peer evidence expired or invalid')
        return dict(machine=machine, received_at=received, health=value,
                    clock_mode='legacy-strict')
    reply = value.get('observation', {}) if isinstance(value, dict) else {}
    if not isinstance(reply, dict) or reply.get('nonce') != nonce:
        raise ValueError('Peer observation challenge mismatch')
    details = dict(machine=machine, received_at=received, health=value,
                   clock_observation=dict(served_at=reply.get('served_at'),
                                          round_trip_seconds=elapsed))
    peer_now = observation_time(details, received)
    # Future evidence is still rejected against the source's own served time.
    for when in (reply['served_at'], peer_now):
        if verdict(value, machine, when)['detail'] == 'health evidence unavailable or stale':
            raise ValueError('Peer evidence expired or invalid')
    details['clock_observation']['offset_seconds'] = reply['served_at'] - received
    return details

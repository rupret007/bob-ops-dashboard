"""Stage-2 hub permissions for lanes (Karen L1). NOT applied to the live hub: nothing reads this at runtime.

LANES.md documents the same delta; tests/test_lanes_perms.py renders it into a throwaway nats-server
config and proves on a real server that a lane host cannot raw-put or delete ``$KV.LANE_LEASES`` keys,
cannot touch another host's consumer, and can still do its own work through the lease service.

Roles
-----
* ``operator`` (hub): runs LeaseService, DeadLetterWatcher and ensure_lanes. Only role that may publish
  ``$KV.LANE_LEASES.>`` or call the JetStream API broadly.
* ``conductor`` (Mini fill loop): may enqueue (``lanes.<provider>.<host>.<job>``) and read DLQ state.
* ``lane host`` (``host(name, providers)``): its own consumers' MSG.NEXT and ``$JS.ACK`` only, its own
  lease-service subject, and its own reply inbox. No ``$KV.>``, no ``$JS.API.STREAM.>``, no ``lanes.>``.
"""

from __future__ import annotations

from cluster import lanes


def operator() -> dict:
    return {
        "publish": {"allow": ["$JS.API.>", "$KV.LANE_LEASES.>", "lanes.>", "lanes_dlq.>", f"{lanes.OPERATOR_INBOX}.>"]},
        "subscribe": {"allow": [f"{lanes.LEASE_SVC_PREFIX}.*.*", lanes.MAX_DELIVERIES_ADVISORY, f"{lanes.OPERATOR_INBOX}.>"]},
        "allow_responses": {"max": 1},
    }


def conductor(name: str = "conductor") -> dict:
    inbox = lanes.role_inbox_prefix(name)
    return {
        "publish": {"allow": [lanes.LANES_SUBJECTS,
                              f"$JS.API.STREAM.INFO.{lanes.DLQ_STREAM}",
                              f"$JS.API.STREAM.MSG.GET.{lanes.DLQ_STREAM}"]},
        "subscribe": {"allow": [f"{inbox}.>"]},
    }


def host(name: str, providers) -> dict:
    lanes.check_host(name)
    inbox = lanes.inbox_prefix(name)
    pub = [lanes.lease_subject(name, op) for op in lanes.LEASE_OPS]
    for p in providers:
        c = lanes.consumer_name(p, name)
        pub += [f"$JS.API.CONSUMER.MSG.NEXT.{lanes.LANES_STREAM}.{c}", f"$JS.ACK.{lanes.LANES_STREAM}.{c}.>"]
    return {
        "publish": {"allow": pub, "deny": ["$KV.>", "$JS.API.STREAM.>", "lanes.>"]},
        "subscribe": {"allow": [f"{inbox}.>"]},
    }


def _fmt(v, indent: int) -> str:
    pad = " " * indent
    if isinstance(v, dict):
        inner = "\n".join(f"{pad}  {k}: {_fmt(x, indent + 2)}" for k, x in v.items())
        return "{\n" + inner + "\n" + pad + "}"
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x, indent) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_users(users: dict) -> str:
    """users maps login -> (password, permissions dict); returns a nats.conf ``authorization`` block."""
    rows = []
    for login, (password, perms) in users.items():
        rows.append(f'    {{ user: {_fmt(login, 0)}, password: {_fmt(password, 0)}, permissions: {_fmt(perms, 4)} }}')
    return "authorization {\n  users = [\n" + "\n".join(rows) + "\n  ]\n}\n"


def metrics_publisher() -> dict:
    """Proposed dedicated identity only; never grants replies or JetStream access."""
    return {
        "publish": {"allow": ["metrics.*"],
                    "deny": ["$KV.>", "lanes.>", "lanes_dlq.>", "_INBOX.>", "$JS.>"]},
        "subscribe": {"deny": [">"]},
    }


def metrics_viewer() -> dict:
    return {"publish": {"deny": [">"]}, "subscribe": {"allow": ["metrics.*"]}}

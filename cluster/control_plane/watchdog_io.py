"""Watchdog implementation; the public facade supplies current runtime bindings."""
from __future__ import annotations


def _write_watchdog_log(context, entry: dict, followup: bool = False) -> None:
    """Append entry to watchdog log."""
    context.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = context.LOG_DIR / "main-ci.jsonl" if followup else context.WATCHDOG_LOG
    with open(log_path, "a") as f:
        f.write(context.json.dumps(entry) + "\n")


def _escalate_to_jeff(context, issue: str, details: str) -> None:
    """Write issue to NEEDS_JEFF.md."""
    context.NEEDS_JEFF_FILE.parent.mkdir(parents=True, exist_ok=True)

    now = context.datetime.now(context.timezone.utc)

    existing = ""
    if context.NEEDS_JEFF_FILE.is_file():
        existing = context.NEEDS_JEFF_FILE.read_text()

    entry = f"""## {now.strftime('%Y-%m-%d %H:%M UTC')} - Watchdog

**Issue:** {issue}

**Details:** {details}

**Action needed:** Manual review required

---

"""

    header = "# NEEDS_JEFF\n\nIssues requiring human intervention.\n\n---\n\n"
    if not existing.startswith("# NEEDS_JEFF"):
        existing = header + existing

    context.NEEDS_JEFF_FILE.write_text(entry + existing)
    context.logger.warning("Escalated to NEEDS_JEFF.md: %s", issue)


def _dashboard_alive(context, timeout: float = 3.0) -> bool:
    """An idle dashboard writes no logs; a live /health answer means it is not stuck."""
    import urllib.request
    port = context.os.environ.get("STORYPOOL_DASHBOARD_PORT", "8790")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def store_peer_engine(context, peer: str, data: dict) -> bool:
    """Keep the peer's self-reported running jobs and LLM usage for the local dashboard.

    Only the peer's own (source=local) section is kept, so data never echoes back.
    """
    jobs = (data.get("jobs") or {}).get(peer) if isinstance(data, dict) else None
    if not isinstance(jobs, dict) or jobs.get("source") != "local":
        return False
    snapshot = {"synced_at": context.datetime.now(context.timezone.utc).isoformat(), "machine": peer,
                "peer_timestamp": data.get("timestamp"), "control": data.get("control", {}),
                "guardian": data.get("machines", {}).get(peer, {}).get("guardian", {}),
                "jobs": dict(jobs, running=list(jobs.get("running") or [])[:50]),
                "llm_local": [g for g in (data.get("llm_local") or []) if isinstance(g, dict)][:20]}
    resources = data.get("resources")
    if (isinstance(resources, dict) and resources.get("machine") == peer
            and resources.get("source") == "local"):
        snapshot["resources"] = resources
    flow = data.get('flow_local')
    if isinstance(flow, dict) and flow.get('machine') == peer:
        snapshot['flow_local'] = {key: flow.get(key) for key in (
            'machine', 'last_verdict_at', 'last_success_at', 'stale_alerts')}
    out = context.LOG_DIR / "machines" / peer / "engine.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(context.json.dumps(snapshot))
    tmp.replace(out)
    return True


def _peer_sync_status(context, peer, ok, failure_kind=None):
    path = context.LOG_DIR / 'machines' / peer / 'sync-status.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {'timestamp': context._now_iso(), 'ok': ok,
             'detail': 'Peer sync verified' if ok else
                       'Peer sync failed; check private endpoint configuration and connectivity'}
    if failure_kind:
        value['failure_kind'] = failure_kind
        action = {
            'address-unavailable': 'peer address unavailable; configure private peers.json',
            'discovery-unavailable': 'check Tailscale CLI access or configure private peers.json',
            'identity-missing': 'peer hostname not recognized; configure private peers.json',
            'identity-ambiguous': 'multiple matching hosts; select one peer in private peers.json',
        }.get(failure_kind)
        if action:
            value['detail'] += '; ' + action
    temporary = path.with_suffix('.tmp')
    temporary.write_text(context.json.dumps(value))
    temporary.replace(path)


def sync_peer_health(context, machine: str) -> bool:
    """Copy the peer's self-reported health (over Tailscale) into machines/<peer>/watchdog.log.

    The row keeps the peer's own watchdog timestamp, so stale peers still show stale.
    """
    import urllib.request
    peer = "nucbox" if machine == "mini" else "mini"
    try:
        peer_url = context.PEER_URLS[peer].strip().rstrip("/")
        if not peer_url:
            context._peer_sync_status(peer, False)
            return False
        with urllib.request.urlopen(peer_url + "/api/engine", timeout=5) as resp:
            data = context.json.loads(resp.read().decode("utf-8"))
        context.store_peer_engine(peer, data)
        info = data.get("machines", {}).get(peer, {})
        if not info.get("timestamp"):
            context._peer_sync_status(peer, False)
            return False
        row = {"timestamp": info["timestamp"], "machine": peer, "source": "peer-sync",
               "checks": [{"name": "peer_reported_health", "passed": info.get("state") != "red",
                           "message": str(info.get("detail", ""))[:200]}]}
        out = context.LOG_DIR / "machines" / peer / "watchdog.log"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            f.write(context.json.dumps(row) + "\n")
        context._peer_sync_status(peer, True)
        return True
    except Exception as exc:
        context._peer_sync_status(peer, False, exc.kind if isinstance(exc, context.PeerUnavailable) else "transport-or-response")
        context.logger.warning("peer health sync for %s failed; check private endpoint configuration and connectivity", peer)
        return False

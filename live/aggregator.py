#!/usr/bin/env python3
"""Live ops aggregator — stdlib HTTP + SSE."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from live.sanitize import sanitize_obj
from live.speed_index import compute_speed_index

CT = ZoneInfo("America/Chicago")
NODE_OFFLINE_SEC = 15.0
RING_SECONDS = 600
OWNER = "rupret007"
_THROUGHPUT_CACHE: dict = {"at": 0.0, "owner": "", "data": {}}
_THROUGHPUT_TTL = 60.0


def load_repos_config(raw) -> tuple[list[str], dict]:
    if isinstance(raw, list):
        return raw, {"default": "bob-brain"}
    repos = raw.get("repos") or []
    gate_map = raw.get("gate_default_repo") or {"default": "bob-brain"}
    return repos, gate_map


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ct_day_bounds(now_ct: datetime) -> tuple[datetime, datetime]:
    start = now_ct.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def _dedupe_prs(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for row in rows:
        repo = row.get("repository") or {}
        name = repo.get("name") if isinstance(repo, dict) else str(repo)
        num = int(row.get("number") or 0)
        key = (name, num)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _gh_search_prs(owner: str, merged_range: str, created_range: str | None = None) -> list[dict]:
    cmd = [
        "gh",
        "search",
        "prs",
        "--owner",
        owner,
        f"--merged-at={merged_range}",
        "--json",
        "number,repository,closedAt",
        "--limit",
        "500",
    ]
    out = subprocess.check_output(cmd, text=True, timeout=90)
    return json.loads(out or "[]")


def _gh_search_opened(owner: str, created_range: str) -> list[dict]:
    cmd = [
        "gh",
        "search",
        "prs",
        "--owner",
        owner,
        f"--created={created_range}",
        "--json",
        "number,repository,createdAt",
        "--limit",
        "500",
    ]
    out = subprocess.check_output(cmd, text=True, timeout=90)
    return json.loads(out or "[]")


def _throughput_counts(owner: str, clock=time.time) -> dict:
    global _THROUGHPUT_CACHE
    now = clock()
    if (
        _THROUGHPUT_CACHE.get("owner") == owner
        and now - float(_THROUGHPUT_CACHE.get("at") or 0) < _THROUGHPUT_TTL
        and _THROUGHPUT_CACHE.get("data")
    ):
        return dict(_THROUGHPUT_CACHE["data"])

    now_ct = datetime.fromtimestamp(now, CT)
    start_today, end_today = _ct_day_bounds(now_ct)
    merged_range = f"{_utc_iso(start_today)}..{_utc_iso(end_today)}"
    created_range = f"{_utc_iso(start_today)}..{_utc_iso(end_today)}"

    merged_today = 0
    opened_today = 0
    try:
        merged_today = len(_dedupe_prs(_gh_search_prs(owner, merged_range)))
        opened_today = len(_dedupe_prs(_gh_search_opened(owner, created_range)))
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        pass

    prev_end = start_today
    prev_start = start_today - timedelta(days=7)
    merged_prev = 0
    opened_prev = 0
    try:
        prev_range = f"{_utc_iso(prev_start)}..{_utc_iso(prev_end)}"
        merged_prev = len(_dedupe_prs(_gh_search_prs(owner, prev_range)))
        opened_prev = len(_dedupe_prs(_gh_search_opened(owner, prev_range)))
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        pass

    data = {
        "merged_today": merged_today,
        "opened_today": opened_today,
        "merged_7d_avg": round(merged_prev / 7.0, 1),
        "opened_7d_avg": round(opened_prev / 7.0, 1),
        "gates_pass": 0,
        "gates_fail": 0,
    }
    _THROUGHPUT_CACHE = {"at": now, "owner": owner, "data": data}
    return dict(data)


def _sha_matches(head: str, gate_sha: str) -> bool:
    h = (head or "").lower()
    g = (gate_sha or "").lower()
    if not h or not g:
        return False
    return h.startswith(g) or g.startswith(h)


def _resolve_gate_repo(gate: dict, prs: list[dict], gate_map: dict) -> str | None:
    if gate.get("repo"):
        return gate["repo"]
    sha = str(gate.get("sha") or "")
    pr_num = int(gate.get("pr") or 0)
    matches = [
        p
        for p in prs
        if _sha_matches(str(p.get("headRefOid") or ""), sha)
    ]
    if len(matches) == 1:
        return matches[0].get("repo")
    default_repo = gate_map.get("default") or gate_map.get(str(pr_num)) or "bob-brain"
    for p in prs:
        if p.get("number") == pr_num and p.get("repo") == default_repo:
            return default_repo
    for p in prs:
        if p.get("number") == pr_num:
            return p.get("repo")
    return default_repo


def gate_totals(throughput: dict, gates: list[dict]) -> dict:
    """Fill gates_pass/gates_fail from parsed Karen verdicts (deduped by pr+sha)."""
    out = dict(throughput or {})
    seen: dict = {}
    for g in gates or []:
        key = (g.get("pr"), str(g.get("sha") or "")[:8])
        v = str(g.get("verdict") or "").upper()
        if v in ("PASS", "FAIL"):
            seen[key] = v
    out["gates_pass"] = sum(1 for v in seen.values() if v == "PASS")
    out["gates_fail"] = sum(1 for v in seen.values() if v == "FAIL")
    return out


def _collect_gates(node_reports: dict) -> list[dict]:
    gates: list[dict] = []
    for rep in node_reports.values():
        gates.extend(rep.get("gates") or [])
    return gates


def _verdict_for_head(gates: list[dict], pr_num: int, head: str) -> str | None:
    verdict = None
    for g in gates:
        if int(g.get("pr") or 0) != pr_num:
            continue
        if not _sha_matches(head, str(g.get("sha") or "")):
            continue
        verdict = str(g.get("verdict") or "").upper()
    return verdict


def apply_karen_to_prs(prs: list[dict], gates: list[dict], gate_map: dict) -> None:
    for pr in prs:
        pr_num = int(pr.get("number") or 0)
        head = str(pr.get("headRefOid") or "").lower()
        verdict = _verdict_for_head(gates, pr_num, head)
        pr["karen_pass"] = verdict == "PASS"
        pr["karen_fail"] = verdict == "FAIL"
        if pr.get("isDraft"):
            pr["stage"] = "draft"
            continue
        if verdict == "FAIL":
            pr["stage"] = "karen_fail"
            continue
        if verdict == "REQUESTED" or (verdict is None and _has_gate_request(gates, pr_num, head)):
            pr["stage"] = "karen"
            continue
        ci_state = pr.get("ci_state") or "unknown"
        if verdict == "PASS" and ci_state == "success":
            pr["stage"] = "ready"
            continue
        if ci_state in ("pending", "unknown"):
            pr["stage"] = "ci"
            continue
        if ci_state == "success" and verdict != "PASS":
            pr["stage"] = "ci"
            continue
        pr["stage"] = "ci"


def _has_gate_request(gates: list[dict], pr_num: int, head: str) -> bool:
    for g in gates:
        if int(g.get("pr") or 0) != pr_num:
            continue
        if str(g.get("verdict") or "").upper() != "REQUESTED":
            continue
        if _sha_matches(head, str(g.get("sha") or "")):
            return True
    return False


def _stage_counts(prs: list[dict], merged_today: int) -> dict:
    stages = {"draft": 0, "ci": 0, "karen": 0, "ready": 0, "merged_today": merged_today}
    for p in prs:
        st = p.get("stage", "ci")
        if st == "karen_fail":
            continue
        if st in stages:
            stages[st] += 1
    return stages


def _now_mono(clock):
    return clock()


def _now_wall(clock):
    return datetime.now(CT)


class LiveStore:
    def __init__(self, clock=time.time, nodes_config=None, needs_manual=None):
        self.clock = clock
        self._lock = threading.Lock()
        self.nodes_config = nodes_config or []
        self.needs_manual = needs_manual or []
        self.gate_default_repo: dict = {"default": "bob-brain"}
        self.node_reports: dict[str, dict] = {}
        self.github: dict = {"prs": [], "stages": {}, "throughput": {}, "fetched_at": None}
        self.events: deque = deque(maxlen=100)
        self.speed_history: deque = deque(maxlen=RING_SECONDS // 2)
        self.merge_history: deque = deque(maxlen=RING_SECONDS // 2)
        self._seen_gates: set[tuple] = set()

    def ingest_node(self, payload: dict):
        nid = payload.get("node_id")
        if not nid:
            return
        with self._lock:
            prev = self.node_reports.get(nid)
            was_offline = prev and (self.clock() - prev.get("_received_at", 0)) > NODE_OFFLINE_SEC
            payload["_received_at"] = self.clock()
            self.node_reports[nid] = payload
            if was_offline:
                self._push_event("node_online", f"Node {nid} back online")
            for g in payload.get("gates") or []:
                key = (g.get("pr"), g.get("sha"), g.get("verdict"))
                if key not in self._seen_gates:
                    self._seen_gates.add(key)
                    at_show = g.get("at_display") or g.get("at_ct") or "time unknown"
                    self._push_event(
                        "gate",
                        f"PR #{g.get('pr')} {g.get('verdict')} {g.get('sha', '')}",
                        at=at_show,
                    )

    def _push_event(self, kind: str, message: str, at: str | None = None):
        when = at if at else _now_wall(self.clock).strftime("%I:%M %p").lstrip("0")
        self.events.appendleft(
            {
                "kind": kind,
                "message": message,
                "at": when,
                "ts": self.clock(),
            }
        )

    def _node_online(self, report: dict | None) -> bool:
        if not report:
            return False
        return (self.clock() - report.get("_received_at", 0)) <= NODE_OFFLINE_SEC

    def _cluster_metrics(self):
        loc = test = actions = 0.0
        cpu_sum = 0.0
        cpu_n = 0
        lanes = 0
        for nid, rep in self.node_reports.items():
            if not self._node_online(rep):
                continue
            tp = rep.get("throughput") or {}
            loc += float(tp.get("loc_per_min") or 0)
            test += float(tp.get("test_runs_per_min") or 0)
            actions += float(tp.get("actions_per_min") or 0)
            lanes += len(rep.get("lanes") or [])
            cpu_sum += float((rep.get("cpu") or {}).get("pct") or 0)
            cpu_n += 1
        cpu_avg = cpu_sum / cpu_n if cpu_n else 0.0
        return loc, test, actions, cpu_avg, lanes

    def build_state(self) -> dict:
        with self._lock:
            loc, test, actions, cpu_avg, lanes = self._cluster_metrics()
            speed = compute_speed_index(loc, test, actions, cpu_avg, lanes)
            self.speed_history.append(
                {"ts": self.clock(), "score": speed["score"]}
            )
            nodes_out = []
            for meta in self.nodes_config:
                nid = meta["id"]
                rep = self.node_reports.get(nid)
                online = self._node_online(rep)
                if rep and online:
                    tp = rep.get("throughput") or {}
                    nspeed = compute_speed_index(
                        float(tp.get("loc_per_min") or 0),
                        float(tp.get("test_runs_per_min") or 0),
                        float(tp.get("actions_per_min") or 0),
                        float((rep.get("cpu") or {}).get("pct") or 0),
                        len(rep.get("lanes") or []),
                    )
                    nodes_out.append(
                        {
                            "id": nid,
                            "label": meta.get("label", nid),
                            "online": True,
                            "optional": bool(meta.get("optional")),
                            "last_seen_sec": round(
                                self.clock() - rep.get("_received_at", self.clock()), 1
                            ),
                            "load": rep.get("load"),
                            "cpu": rep.get("cpu"),
                            "memory": rep.get("memory"),
                            "swap": rep.get("swap"),
                            "disk": rep.get("disk"),
                            "max_lanes": meta.get("max_lanes"),
                            "lanes": rep.get("lanes") or [],
                            "routines": rep.get("routines") or [],
                            "speed": nspeed,
                            "ai_capacity": rep.get("ai_capacity") or [],
                        }
                    )
                else:
                    nodes_out.append(
                        {
                            "id": nid,
                            "label": meta.get("label", nid),
                            "online": False,
                            "optional": bool(meta.get("optional")),
                            "max_lanes": meta.get("max_lanes"),
                            "speed": None,
                        }
                    )

            gates = _collect_gates(self.node_reports)
            apply_karen_to_prs(
                self.github.get("prs") or [], gates, self.gate_default_repo
            )
            merged_today = int(
                (self.github.get("throughput") or {}).get("merged_today") or 0
            )
            self.github["stages"] = _stage_counts(
                self.github.get("prs") or [], merged_today
            )
            needs = list(self.needs_manual)
            needs.extend(self._derive_needs(nodes_out))

            ai_all = []
            for n in nodes_out:
                if n.get("online"):
                    for a in n.get("ai_capacity") or []:
                        ai_all.append({**a, "node": n["id"]})

            routines = self._derive_routines(nodes_out)

            return sanitize_obj(
                {
                    "version": "state/v1",
                    "updated_at": _now_wall(self.clock).isoformat(),
                    "timezone": "America/Chicago",
                    "speed_index": speed,
                    "speed_history": list(self.speed_history),
                    "nodes": nodes_out,
                    "needs_you": needs,
                    "pipeline": self.github.get("stages", {}),
                    "pipeline_rows": self.github.get("prs", []),
                    "throughput": gate_totals(self.github.get("throughput", {}), gates),
                    "throughput_history": list(self.merge_history),
                    "events": sorted(
                        list(self.events), key=lambda e: float(e.get("ts") or 0), reverse=True
                    )[:30],
                    "routines": routines,
                    "ai_capacity": ai_all,
                    "formula": {
                        "description": "Speed index 0-100",
                        "weights": speed["weights"],
                        "full_scale": speed["full_scale"],
                    },
                }
            )

    def _derive_needs(self, nodes_out: list[dict]) -> list[dict]:
        items = []
        for n in nodes_out:
            if not n.get("online") and not n.get("optional"):
                items.append(
                    {
                        "severity": "warn",
                        "kind": "node_offline",
                        "title": f"{n.get('label')} offline",
                    }
                )
            load = (n.get("load") or {}).get("l5")
            if load is not None and load > 14:
                items.append(
                    {
                        "severity": "bad",
                        "kind": "load",
                        "title": f"{n.get('label')} load5 {load} > 14",
                    }
                )
            disk = n.get("disk") or {}
            free = disk.get("free_gb")
            if free is not None and free < 15:
                items.append(
                    {
                        "severity": "bad",
                        "kind": "disk",
                        "title": f"{n.get('label')} disk {free} GB free (<15)",
                    }
                )
            for a in n.get("ai_capacity") or []:
                pct = a.get("remaining_pct")
                if pct is not None and float(pct) < 10:
                    prov = str(a.get("provider") or "AI")
                    label = prov
                    if "codex" in prov.lower():
                        label = "Codex nearly out"
                    else:
                        label = f"{prov} nearly out"
                    items.append(
                        {
                            "severity": "bad",
                            "kind": "ai_capacity",
                            "title": label,
                        }
                    )

        for pr in self.github.get("prs") or []:
            if pr.get("karen_pass") and pr.get("isDraft"):
                items.append(
                    {
                        "severity": "warn",
                        "kind": "decision",
                        "title": f"Decision: undraft {pr.get('repo')}#{pr.get('number')}?",
                        "detail": "Karen PASS · still draft",
                    }
                )
            if pr.get("karen_pass") and pr.get("ci_failing"):
                items.append(
                    {
                        "severity": "bad",
                        "kind": "ci",
                        "title": f"CI failing on {pr.get('repo')} #{pr.get('number')}",
                    }
                )
        return items

    def _derive_routines(self, nodes_out: list[dict]) -> list[dict]:
        routines = []
        for n in nodes_out:
            for routine in n.get("routines") or []:
                routines.append(
                    {
                        "name": routine.get("name", ""),
                        "node": n.get("label"),
                        "status": routine.get("status", "running"),
                    }
                )
            for lane in n.get("lanes") or []:
                name = lane.get("name", "")
                if name.startswith("svc-"):
                    routines.append(
                        {
                            "name": name,
                            "node": n.get("label"),
                            "status": lane.get("status", "running"),
                        }
                    )
        return routines

    def update_github(self, data: dict):
        with self._lock:
            self.github = data
            merged_today = data.get("throughput", {}).get("merged_today", 0)
            self.merge_history.append({"ts": self.clock(), "merged": merged_today})


def fetch_github_state(
    repos: list[str], owner: str = OWNER, clock=time.time
) -> dict:
    """Call gh CLI; return empty structure on failure."""
    prs: list[dict] = []
    try:
        for repo in repos:
            out = subprocess.check_output(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    f"{owner}/{repo}",
                    "--state",
                    "open",
                    "--json",
                    "number,title,isDraft,headRefOid,statusCheckRollup,updatedAt",
                    "--limit",
                    "50",
                ],
                text=True,
                timeout=60,
            )
            for row in json.loads(out or "[]"):
                checks = row.get("statusCheckRollup") or []
                ci_state = "unknown"
                ci_failing = False
                for c in checks:
                    st = (c or {}).get("state", "").upper()
                    if st == "FAILURE":
                        ci_failing = True
                        ci_state = "failure"
                    elif st == "PENDING" and ci_state != "failure":
                        ci_state = "pending"
                    elif st == "SUCCESS" and ci_state not in ("failure", "pending"):
                        ci_state = "success"
                prs.append(
                    {
                        "repo": repo,
                        "number": row["number"],
                        "title": row.get("title", ""),
                        "isDraft": row.get("isDraft"),
                        "headRefOid": (row.get("headRefOid") or "")[:8],
                        "updatedAt": row.get("updatedAt"),
                        "ci_state": ci_state,
                        "ci_failing": ci_failing,
                        "karen_pass": False,
                        "stage": _stage_for_pr(row, ci_state),
                    }
                )
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        pass

    throughput = _throughput_counts(owner, clock=clock)
    stages = _stage_counts(prs, throughput["merged_today"])
    return {
        "prs": prs,
        "stages": stages,
        "throughput": throughput,
        "fetched_at": clock(),
    }


def _stage_for_pr(row: dict, ci_state: str) -> str:
    if row.get("isDraft"):
        return "draft"
    if ci_state in ("pending", "unknown"):
        return "ci"
    if ci_state == "success":
        return "ready"
    return "ci"


class AggregatorServer:
    def __init__(
        self,
        host="127.0.0.1",
        port=8787,
        token=None,
        store: LiveStore | None = None,
        github_fetcher=None,
    ):
        self.host = host
        self.port = port
        self.token = token
        live_dir = Path(__file__).resolve().parent
        nodes = json.loads((live_dir / "nodes.json").read_text(encoding="utf-8"))
        needs_path = live_dir / "needs.json"
        needs = json.loads(needs_path.read_text(encoding="utf-8")) if needs_path.is_file() else []
        self.store = store or LiveStore(nodes_config=nodes, needs_manual=needs)
        repos_raw = json.loads((live_dir / "repos.json").read_text(encoding="utf-8"))
        self.repos, gate_map = load_repos_config(repos_raw)
        self.store.gate_default_repo = gate_map
        def _default_fetch(repos, clock=time.time):
            return fetch_github_state(repos, clock=clock)

        self.github_fetcher = github_fetcher or _default_fetch
        self._sse_clients: list[threading.Condition] = []
        self._stop = threading.Event()
        self.web_root = live_dir / "web"

    def _notify_sse(self):
        for cond in self._sse_clients:
            with cond:
                cond.notify_all()

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _check_token(self):
                if not server.token:
                    return True
                return self.headers.get("X-Live-Token") == server.token

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/healthz":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                    return
                if path == "/api/state":
                    body = json.dumps(server.store.build_state()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    cond = threading.Condition()
                    server._sse_clients.append(cond)
                    try:
                        while not server._stop.is_set():
                            state = server.store.build_state()
                            payload = json.dumps(state)
                            self.wfile.write(
                                f"event: state\ndata: {payload}\n\n".encode("utf-8")
                            )
                            self.wfile.flush()
                            with cond:
                                cond.wait(timeout=2.0)
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        if cond in server._sse_clients:
                            server._sse_clients.remove(cond)
                    return
                if path == "/" or path == "/index.html":
                    index = server.web_root / "index.html"
                    body = index.read_bytes() if index.is_file() else b""
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                path = urlparse(self.path).path
                if path != "/api/node":
                    self.send_response(404)
                    self.end_headers()
                    return
                if not self._check_token():
                    self.send_response(401)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                if payload.get("version") != "node/v1":
                    self.send_response(400)
                    self.end_headers()
                    return
                server.store.ingest_node(payload)
                server._notify_sse()
                self.send_response(204)
                self.end_headers()

        return Handler

    def _github_loop(self):
        while not self._stop.is_set():
            try:
                data = self.github_fetcher(self.repos, clock=self.store.clock)
            except TypeError:
                data = self.github_fetcher(self.repos)
            self.store.update_github(data)
            self._notify_sse()
            self._stop.wait(60.0)

    def serve_forever(self):
        handler = self.make_handler()
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        t = threading.Thread(target=self._github_loop, daemon=True)
        t.start()
        try:
            httpd.serve_forever()
        finally:
            self._stop.set()
            httpd.shutdown()


def main():
    host = os.environ.get("LIVE_BIND", "127.0.0.1")
    port = int(os.environ.get("LIVE_PORT", "8787"))
    token = os.environ.get("LIVE_TOKEN")
    server = AggregatorServer(host=host, port=port, token=token)
    print(f"live aggregator on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

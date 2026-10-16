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
from datetime import datetime, timedelta
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
                    self._push_event(
                        "gate",
                        f"PR #{g.get('pr')} {g.get('verdict')} {g.get('sha', '')}",
                    )

    def _push_event(self, kind: str, message: str):
        self.events.appendleft(
            {
                "kind": kind,
                "message": message,
                "at": _now_wall(self.clock).strftime("%I:%M %p"),
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
                            "max_lanes": meta.get("max_lanes"),
                            "speed": None,
                        }
                    )

            self._join_gates_to_prs()
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
                    "throughput": self.github.get("throughput", {}),
                    "throughput_history": list(self.merge_history),
                    "events": list(self.events)[:30],
                    "routines": routines,
                    "ai_capacity": ai_all,
                    "formula": {
                        "description": "Speed index 0-100",
                        "weights": speed["weights"],
                        "full_scale": speed["full_scale"],
                    },
                }
            )

    def _join_gates_to_prs(self):
        gate_pass = set()
        for rep in self.node_reports.values():
            for g in rep.get("gates") or []:
                if g.get("verdict") == "PASS":
                    gate_pass.add((g.get("repo"), g.get("pr")))
        for pr in self.github.get("prs") or []:
            key = (pr.get("repo"), pr.get("number"))
            if key in gate_pass or any(
                g.get("pr") == pr.get("number")
                for rep in self.node_reports.values()
                for g in rep.get("gates") or []
                if g.get("verdict") == "PASS"
            ):
                pr["karen_pass"] = True
                if pr.get("stage") == "ready":
                    pr["stage"] = "karen"
                    self.github.setdefault("stages", {})
                    self.github["stages"]["karen"] = self.github["stages"].get("karen", 0) + 1

    def _derive_needs(self, nodes_out: list[dict]) -> list[dict]:
        items = []
        for n in nodes_out:
            if not n.get("online") and n.get("id") != "mbp":
                items.append(
                    {
                        "severity": "warn",
                        "kind": "node_offline",
                        "title": f"{n.get('label')} offline",
                    }
                )
            load = (n.get("load") or {}).get("l1")
            if load is not None and load > 14:
                items.append(
                    {
                        "severity": "bad",
                        "kind": "load",
                        "title": f"{n.get('label')} load {load} > 14",
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
                    items.append(
                        {
                            "severity": "bad",
                            "kind": "ai_capacity",
                            "title": f"{a.get('provider')} {pct}% left",
                        }
                    )

        for pr in self.github.get("prs") or []:
            if pr.get("karen_pass") and pr.get("isDraft"):
                items.append(
                    {
                        "severity": "warn",
                        "kind": "decision",
                        "title": f"Undraft {pr.get('repo')} #{pr.get('number')}?",
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


def fetch_github_state(repos: list[str], owner: str = OWNER) -> dict:
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

    stages = {"draft": 0, "ci": 0, "karen": 0, "ready": 0, "merged_today": 0}
    for p in prs:
        st = p.get("stage", "ci")
        if st in stages:
            stages[st] += 1

    throughput = _throughput_counts(repos, owner)
    return {
        "prs": prs,
        "stages": stages,
        "throughput": throughput,
        "fetched_at": time.time(),
    }


def _stage_for_pr(row: dict, ci_state: str) -> str:
    if row.get("isDraft"):
        return "draft"
    if ci_state in ("pending", "unknown"):
        return "ci"
    if ci_state == "success":
        return "ready"
    return "ci"


def _throughput_counts(repos: list[str], owner: str) -> dict:
    now_ct = datetime.now(CT)
    start_today = now_ct.replace(hour=0, minute=0, second=0, microsecond=0)
    merged_today = 0
    opened_today = 0
    for repo in repos:
        try:
            out = subprocess.check_output(
                [
                    "gh",
                    "search",
                    "prs",
                    "--owner",
                    owner,
                    "--repo",
                    repo,
                    "--merged",
                    f">{start_today.strftime('%Y-%m-%d')}",
                    "--json",
                    "number",
                    "--limit",
                    "100",
                ],
                text=True,
                timeout=30,
            )
            merged_today += len(json.loads(out or "[]"))
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            pass
    week_ago = start_today - timedelta(days=7)
    merged_7d = max(merged_today, 1)
    return {
        "merged_today": merged_today,
        "opened_today": opened_today,
        "merged_7d_avg": round(merged_7d / 7.0, 1),
        "opened_7d_avg": 0.0,
        "gates_pass": 0,
        "gates_fail": 0,
    }


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
        self.repos = json.loads((live_dir / "repos.json").read_text(encoding="utf-8"))
        self.github_fetcher = github_fetcher or fetch_github_state
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

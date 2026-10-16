import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

from live.aggregator import AggregatorServer, LiveStore


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def advance(self, sec):
        self.t += sec


class AggregatorHttpTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        nodes = [{"id": "nuc", "label": "Nuc", "max_lanes": 6}]
        self.store = LiveStore(clock=self.clock.time, nodes_config=nodes, needs_manual=[])
        self.server = AggregatorServer(
            host="127.0.0.1",
            port=0,
            token="synthetic-test-token",
            store=self.store,
            github_fetcher=lambda repos: {
                "prs": [],
                "stages": {"draft": 0, "ci": 0, "karen": 0, "ready": 0, "merged_today": 0},
                "throughput": {"merged_today": 0, "merged_7d_avg": 0},
                "fetched_at": self.clock.time(),
            },
        )
        handler = self.server.make_handler()
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_post_and_state(self):
        payload = {
            "version": "node/v1",
            "node_id": "nuc",
            "throughput": {"loc_per_min": 10, "test_runs_per_min": 1, "actions_per_min": 5},
            "cpu": {"pct": 20},
            "lanes": [{"name": "lane-a"}],
            "load": {"l1": 2},
        }
        req = urllib.request.Request(
            self._url("/api/node"),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer synthetic-test-token"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(204, resp.status)
        with urllib.request.urlopen(urllib.request.Request(self._url("/api/state"), headers={"Authorization": "Bearer synthetic-test-token"})) as resp:
            state = json.loads(resp.read().decode())
        self.assertEqual("state/v1", state["version"])
        self.assertTrue(state["nodes"][0]["online"])

    def test_sse_event(self):
        payload = {
            "version": "node/v1",
            "node_id": "nuc",
            "lanes": [],
            "cpu": {"pct": 1},
            "throughput": {},
        }
        urllib.request.urlopen(
            urllib.request.Request(
                self._url("/api/node"),
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer synthetic-test-token"},
                method="POST",
            )
        )
        req = urllib.request.Request(self._url("/api/stream"), headers={"Authorization": "Bearer synthetic-test-token"})
        resp = urllib.request.urlopen(req, timeout=5)
        chunk = resp.read(4096).decode("utf-8", errors="replace")
        self.assertIn("event: state", chunk)
        self.assertIn("state/v1", chunk)
        resp.close()

    def test_offline_after_timeout(self):
        payload = {
            "version": "node/v1",
            "node_id": "nuc",
            "lanes": [],
            "cpu": {"pct": 1},
            "throughput": {},
        }
        urllib.request.urlopen(
            urllib.request.Request(
                self._url("/api/node"),
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer synthetic-test-token"},
                method="POST",
            )
        )
        self.clock.advance(20)
        with urllib.request.urlopen(urllib.request.Request(self._url("/api/state"), headers={"Authorization": "Bearer synthetic-test-token"})) as resp:
            state = json.loads(resp.read().decode())
        self.assertFalse(state["nodes"][0]["online"])


if __name__ == "__main__":
    unittest.main()

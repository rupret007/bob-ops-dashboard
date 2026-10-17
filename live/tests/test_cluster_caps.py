import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

from live.aggregator import AggregatorServer, LiveStore, clean_cluster_caps


class Clock:
    t = 1000.0

    def time(self):
        return self.t


HB = {"v": "caps/v1", "node": "macbook", "role": "member", "os": "darwin", "arch": "arm64",
      "cpu_count": 10, "mem_total_gb": 16, "load1": 1.5, "max_lanes": 3, "active": 1,
      "llm": {"codex": {"installed": True, "logged_in": True, "quota": {"remaining_pct": 42}},
              "claude": {"installed": True, "logged_in": False}, "evil": {"installed": True}},
      "caps": ["llm.codex.high", "shell.build"], "token": "sk-should-not-pass-through-ever-123456"}


class CleanTests(unittest.TestCase):
    def test_whitelists_fields(self):
        row = clean_cluster_caps(HB)
        self.assertNotIn("token", row)
        self.assertNotIn("evil", row["llm"])
        self.assertEqual(row["llm"]["codex"], {"installed": True, "logged_in": True, "remaining_pct": 42})
        self.assertIsNone(clean_cluster_caps({**HB, "v": "node/v1"}))
        self.assertIsNone(clean_cluster_caps({**HB, "node": "<script>"}))

    def test_guest_llm_dropped(self):
        self.assertEqual(clean_cluster_caps({**HB, "role": "guest"})["llm"], {})


class ClusterEndpointTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = LiveStore(clock=self.clock.time, nodes_config=[], needs_manual=[])
        self.server = AggregatorServer(host="127.0.0.1", port=0, token="synthetic-test-token", store=self.store,
                                       github_fetcher=lambda repos: {"prs": [], "stages": {}, "throughput": {},
                                                                     "fetched_at": self.clock.time()})
        self.httpd = HTTPServer(("127.0.0.1", 0), self.server.make_handler())
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, body, token="synthetic-test-token"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/cluster", data=json.dumps(body).encode(),
                                     method="POST", headers={"Authorization": f"Bearer {token}",
                                                             "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_post_requires_token_and_appears_in_state(self):
        self.assertEqual(self._post(HB, token="wrong"), 401)
        self.assertEqual(self._post({"v": "caps/v1"}), 400)
        self.assertEqual(self._post(HB), 204)
        state = self.store.build_state()
        self.assertEqual(state["cluster_caps"][0]["node"], "macbook")
        self.assertTrue(state["cluster_caps"][0]["online"])
        self.clock.t += 61
        self.assertFalse(self.store.build_state()["cluster_caps"][0]["online"])

    def test_node_cap(self):
        import live.aggregator as agg
        old = agg.MAX_CLUSTER_NODES
        agg.MAX_CLUSTER_NODES = 2
        try:
            for n in ("aa", "bb", "cc"):
                self.store.ingest_cluster({**HB, "node": n})
            self.assertEqual(len(self.store.cluster_caps), 2)
        finally:
            agg.MAX_CLUSTER_NODES = old


if __name__ == "__main__":
    unittest.main()

import unittest

from cluster.metrics_collect import collect_metrics, to_node_v1


class TestMetrics(unittest.TestCase):
    def test_shape(self):
        m = collect_metrics("mini", active_jobs=0, light=True)
        for key in (
            "cpu_pct",
            "load1",
            "mem_used_gb",
            "disk_free_gb",
            "active_jobs",
            "jobs_done_total",
        ):
            self.assertIn(key, m)

    def test_node_v1_bridge(self):
        m = collect_metrics("nucbox", 1, light=True)
        n = to_node_v1(m)
        self.assertEqual(n["v"], "node/v1")
        self.assertEqual(n["node_id"], "nucbox")
        self.assertTrue(n["online"])


if __name__ == "__main__":
    unittest.main()

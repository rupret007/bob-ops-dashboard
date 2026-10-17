import unittest

from live.aggregator import LiveStore


class NeedsTests(unittest.TestCase):
    def test_optional_node_no_offline_alarm(self):
        nodes = [
            {"id": "nuc", "label": "Nuc", "max_lanes": 6},
            {"id": "mbp", "label": "MacBook Pro", "max_lanes": 0, "optional": True},
        ]
        store = LiveStore(clock=lambda: 1000.0, nodes_config=nodes, needs_manual=[])
        store.github = {"prs": [], "stages": {}, "throughput": {}}
        nodes_out = [
            {"id": "nuc", "label": "Nuc", "online": False},
            {"id": "mbp", "label": "MacBook Pro", "online": False, "optional": True},
        ]
        needs = store._derive_needs(nodes_out)
        kinds = [n["kind"] for n in needs]
        self.assertIn("node_offline", kinds)
        self.assertEqual(1, sum(1 for k in kinds if k == "node_offline"))

    def test_manual_needs_in_state(self):
        nodes = [{"id": "nuc", "label": "Nuc", "max_lanes": 6}]
        manual = [
            {
                "severity": "warn",
                "kind": "contact",
                "title": "Contact form: site inquiry",
            }
        ]
        store = LiveStore(clock=lambda: 1000.0, nodes_config=nodes, needs_manual=manual)
        store.node_reports["nuc"] = {
            "_received_at": 1000.0,
            "throughput": {},
            "cpu": {"pct": 1},
            "lanes": [],
            "load": {"l1": 1, "l5": 1},
        }
        store.github = {"prs": [], "stages": {}, "throughput": {"merged_today": 0}}
        state = store.build_state()
        titles = [n["title"] for n in state["needs_you"]]
        self.assertIn("Contact form: site inquiry", titles)


if __name__ == "__main__":
    unittest.main()

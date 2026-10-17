"""AI capacity file ingest and aggregator merge."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

from live.agent import Agent
from live.aggregator import LiveStore


class AgentAiCapacityTests(unittest.TestCase):
    def test_reads_ai_capacity_v1_at_startup(self):
        payload = {
            "schema": "ai-capacity/v1",
            "rows": [
                {
                    "provider": "Codex",
                    "used_pct": 98,
                    "remaining_pct": 2,
                    "reset_ct": "Thu 1:29 AM",
                    "freshness": "fresh",
                    "status": "ok",
                    "note": "secret note",
                    "source": "inject",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            cap_path = Path(tmp) / "ai-capacity.json"
            cap_path.write_text(json.dumps(payload), encoding="utf-8")
            old_cap = os.environ.get("BOB_AI_CAPACITY")
            old_prog = os.environ.get("BOB_PROGRESS_GLOB")
            os.environ["BOB_AI_CAPACITY"] = str(cap_path)
            os.environ["BOB_PROGRESS_GLOB"] = str(Path(tmp) / "none" / "progress.md")
            try:
                agent = Agent(
                    argparse.Namespace(
                        node_id="mini",
                        push="http://127.0.0.1:9/api/node",
                        interval=2,
                    )
                )
            finally:
                if old_cap is None:
                    os.environ.pop("BOB_AI_CAPACITY", None)
                else:
                    os.environ["BOB_AI_CAPACITY"] = old_cap
                if old_prog is None:
                    os.environ.pop("BOB_PROGRESS_GLOB", None)
                else:
                    os.environ["BOB_PROGRESS_GLOB"] = old_prog
            self.assertEqual(1, len(agent._ai_capacity))
            row = agent._ai_capacity[0]
            self.assertEqual("Codex", row["provider"])
            self.assertEqual(2, row["remaining_pct"])
            self.assertEqual(98, row["used_pct"])
            self.assertNotIn("note", row)
            self.assertNotIn("source", row)

    def test_aggregator_merges_ai_capacity_into_state(self):
        nodes = [{"id": "mini", "label": "Mac mini", "max_lanes": 6}]
        store = LiveStore(clock=lambda: 1000.0, nodes_config=nodes, needs_manual=[])
        store.ingest_node(
            {
                "node_id": "mini",
                "throughput": {},
                "cpu": {"pct": 1},
                "lanes": [],
                "load": {"l1": 1},
                "ai_capacity": [
                    {
                        "provider": "Codex",
                        "remaining_pct": 2,
                        "used_pct": 98,
                        "reset_ct": "Thu 1:29 AM",
                        "freshness": "fresh",
                        "status": "ok",
                    }
                ],
            }
        )
        store.github = {"prs": [], "stages": {}, "throughput": {"merged_today": 0}}
        state = store.build_state()
        self.assertEqual(1, len(state["ai_capacity"]))
        self.assertEqual("Codex", state["ai_capacity"][0]["provider"])
        self.assertEqual(2, state["ai_capacity"][0]["remaining_pct"])


if __name__ == "__main__":
    unittest.main()

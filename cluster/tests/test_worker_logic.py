import json
import unittest

from cluster.worker_logic import (
    JobAction,
    build_result,
    check_load_ceiling,
    parse_job_payload,
    should_stop_redelivery,
)


class TestWorkerLogic(unittest.TestCase):
    def spec_bytes(self, **overrides):
        base = {
            "v": 1,
            "id": "j1",
            "class": "test",
            "repo": "rupret007/bob-ops-dashboard",
            "ref": "abc",
            "cmd": ["npm", "test"],
            "timeout_s": 10,
            "load_ceiling": 8,
        }
        base.update(overrides)
        return json.dumps(base).encode()

    def test_term_invalid_spec(self):
        _, d = parse_job_payload(b"not json", {"test"})
        self.assertEqual(d.action, JobAction.TERM)

    def test_skip_unsupported_class(self):
        spec, d = parse_job_payload(self.spec_bytes(**{"class": "render", "cmd": ["npm", "run", "render"]}), {"test"})
        self.assertEqual(d.action, JobAction.SKIP)

    def test_load_ceiling_nak(self):
        d = check_load_ceiling(1.0, load1=2.0)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, JobAction.NAK)

    def test_redelivery_max(self):
        self.assertTrue(should_stop_redelivery(3, 3))
        self.assertFalse(should_stop_redelivery(2, 3))

    def test_build_result_sanitizes(self):
        r = build_result("j1", "ok", 0, 1.0, "ghp_abcdefghijklmnopqrstuvwxyz1234567890AB")
        self.assertNotIn("ghp_", r["tail"])


if __name__ == "__main__":
    unittest.main()

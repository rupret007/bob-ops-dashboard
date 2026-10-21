"""An unresolved receiving-commit result cannot age into a healthy result."""
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from cluster.control_plane import health_adapters, main_ci as m


REPO = "owner/repo"


def pr(number=7):
    return dict(number=number, repository=dict(nameWithOwner=REPO))


class UnresolvedCITests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.clock = [10000]
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(m, "time", Mock(
            monotonic=lambda: 0, time=lambda: self.clock[0])))
        self.stack.enter_context(patch.object(m.ClaimClient, "from_env"))
        self.recent = self.stack.enter_context(patch.object(m, "recent_prs", return_value=[]))
        self.one = self.stack.enter_context(patch.object(m, "reconcile_one", side_effect=lambda _, p:
            dict(repo=REPO, pr=p["number"], main_ci="failure")))

    def save(self, records):
        (self.root / "main-ci-progress.json").write_text(json.dumps(dict(records=records)))

    def row(self, status, number=7, **extra):
        return dict(repo=REPO, pr=number, main_ci=status,
                    checked_at=self.clock[0] - 601,
                    attempted_at=self.clock[0] - 601, **extra)

    def tick(self):
        return m.reconcile_batch(self.root)

    def test_failed_merge_is_rechecked_after_it_leaves_the_search_window(self):
        self.recent.return_value = [pr()]
        self.assertEqual(self.tick()["results"][0]["main_ci"], "failure")
        self.recent.return_value = []
        self.clock[0] += 601
        result = self.tick()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["main_ci"], "failure")
        self.assertEqual(self.one.call_count, 2)

    def test_an_aged_failure_still_fails_the_health_adapter(self):
        self.save({REPO + "#7": self.row("failure")})
        result = self.tick()
        with patch.object(m, "reconcile_batch", return_value=result):
            health = health_adapters.collect("main_ci", "nucbox", True)
        self.assertFalse(health["checks"][0]["passed"])

    def test_pending_unknown_and_deferred_merges_cannot_disappear(self):
        for status in ("pending", "unknown", "deferred"):
            with self.subTest(status=status):
                self.save({REPO + "#7": self.row(status)})
                self.one.return_value = None
                self.one.side_effect = lambda _, p: dict(repo=REPO, pr=p["number"], main_ci=status)
                result = self.tick()
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["unconfirmed"], 1)

    def test_unattempted_tail_survives_restart_and_discovery_ageout(self):
        self.save({REPO + "#7": {}})
        result = self.tick()
        self.assertEqual(result["total"], 1)
        self.one.assert_called_once()

    def test_authoritative_recovery_allows_the_old_record_to_retire(self):
        for status in ("success", "recovered", "ineligible"):
            with self.subTest(status=status):
                self.save({REPO + "#7": self.row("failure")})
                self.one.side_effect = lambda _, p: dict(repo=REPO, pr=p["number"], main_ci=status)
                result = self.tick()
                self.assertEqual(result["results"][0]["main_ci"], status)
                self.one.reset_mock()
                self.assertEqual(self.tick()["total"], 0)
                self.one.assert_not_called()

    def test_recent_and_carried_records_are_deduplicated(self):
        self.save({REPO + "#7": self.row("failure")})
        self.recent.return_value = [pr()]
        self.assertEqual(self.tick()["total"], 1)
        self.one.assert_called_once()

    def test_aged_green_records_are_not_replayed(self):
        for status in ("success", "recovered", "ineligible"):
            with self.subTest(status=status):
                self.save({REPO + "#7": self.row(status)})
                self.assertEqual(self.tick()["total"], 0)
                self.one.assert_not_called()

    def test_existing_repository_guard_still_excludes_carried_work(self):
        self.save({REPO + "#7": self.row("failure")})
        with patch.object(m, "check_merge_allowed", return_value=(False, "held")):
            self.assertEqual(self.tick()["total"], 0)
        self.one.assert_not_called()

    def test_a_deferred_recheck_cannot_renew_the_failed_receipt(self):
        old = self.row("failure")
        old["checked_at"] = self.clock[0] - 550
        self.save({REPO + "#7": old})
        self.one.side_effect = lambda _, p: dict(repo=REPO, pr=p["number"], main_ci="deferred")
        result = self.tick()
        self.assertEqual(result["results"][0]["main_ci"], "failure")
        saved = json.loads((self.root / "main-ci-progress.json").read_text())["records"][REPO + "#7"]
        self.assertEqual(saved["checked_at"], old["checked_at"])
        self.clock[0] += 51
        self.assertEqual(self.tick()["unconfirmed"], 1)

    def test_deadline_exhaustion_leaves_the_old_result_unconfirmed(self):
        self.save({REPO + "#7": self.row("failure")})
        times = iter([0, 120])
        with patch.object(m.time, "monotonic", side_effect=lambda: next(times)):
            result = self.tick()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["unconfirmed"], 1)
        self.one.assert_not_called()

    def test_least_recent_attempt_order_includes_carried_records(self):
        self.save({REPO + "#7": self.row("failure")})
        self.recent.return_value = [pr(8)]
        self.tick()
        self.assertEqual([c.args[1]["number"] for c in self.one.call_args_list], [8, 7])

    def test_progress_corruption_cannot_report_empty_green(self):
        for body in ("not JSON", "[]", '{"records": []}',
                     '{"records":{"owner/repo#7":{"repo":"other/repo","pr":7,"main_ci":"failure"}}}',
                     '{"records":{"owner/repo#7":{"repo":"owner/repo","pr":true,"main_ci":"failure"}}}',
                     '{"records":{"owner/repo#07":{}}}',
                     '{"records":{"owner/repo#7":{"repo":"owner/repo","pr":7,"main_ci":"unrecognized"}}}'):
            with self.subTest(body=body):
                p = self.root / "main-ci-progress.json"
                p.write_text(body)
                result = self.tick()
                self.assertGreater(result["unconfirmed"], 0)
                self.assertTrue(result["progress_unavailable"])
                self.assertEqual(p.read_text(), body)
        self.one.assert_not_called()

    def test_oversized_progress_remains_unconfirmed_without_discarding_it(self):
        p = self.root / "main-ci-progress.json"
        p.write_bytes(b" " * (1024 * 1024 + 1))
        self.assertTrue(self.tick()["progress_unavailable"])
        self.assertEqual(p.stat().st_size, 1024 * 1024 + 1)
        self.one.assert_not_called()

    def test_read_failure_remains_unconfirmed(self):
        (self.root / "main-ci-progress.json").write_text("{}")
        with patch.object(Path, "read_text", side_effect=PermissionError("fixture denial")):
            self.assertTrue(self.tick()["progress_unavailable"])
        self.one.assert_not_called()

    def test_missing_progress_is_a_legitimate_first_start(self):
        self.assertEqual(self.tick()["unconfirmed"], 0)
        self.one.assert_not_called()


if __name__ == "__main__":
    unittest.main()

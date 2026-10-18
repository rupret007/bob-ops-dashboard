"""MiniMax second-judge verdict parsing: blocking issues fail closed, notes allowed."""
import json
import unittest

from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.reviewers import (
    MiniMaxReviewer, ReviewResult, ReviewTier, ReviewVerdict,
)


def _pr():
    return PRInfo(owner="o", repo="r", number=1, head_sha="a" * 40, title="t",
                  author="u", is_draft=False, diff="+x", ci_status={},
                  changed_files=["x.py"], deleted_files=[])


def _resp(verdict, **findings):
    base = {n: [] for n in ("security_issues", "functional_issues",
                            "test_coverage_issues", "code_quality_issues", "risk_notes")}
    base.update(findings)
    return json.dumps({"verdict": verdict, "reason": "Independent review of the pinned diff found the change correct and adequately tested.", "confidence": 0.8, "findings": base})


class MiniMaxSecondJudgeTest(unittest.TestCase):
    def setUp(self):
        self.mm = MiniMaxReviewer()
        tier = list(ReviewTier)[0]
        self.primary = ReviewResult("codex", ReviewVerdict.PASS, "ok", tier=tier)

    def _parse(self, text):
        return self.mm._parse_second_judge_verdict(text, _pr(), self.primary, 1.0, "file")

    def test_pass_with_security_issue_fails_closed_as_borderline(self):
        r = self._parse(_resp("PASS", security_issues=["token logged"]))
        self.assertEqual(r.verdict, ReviewVerdict.BORDERLINE)
        self.assertIn("security_issues", r.reason)

    def test_pass_with_functional_issue_fails_closed(self):
        r = self._parse(_resp("PASS", functional_issues=["breaks login"]))
        self.assertEqual(r.verdict, ReviewVerdict.BORDERLINE)

    def test_pass_with_only_quality_notes_is_not_an_error(self):
        r = self._parse(_resp("PASS", code_quality_issues=["rename var"], risk_notes=["low risk"]))
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_fenced_json_is_parsed(self):
        r = self._parse("```json\n" + _resp("FAIL", functional_issues=["bug"]) + "\n```")
        self.assertEqual(r.verdict, ReviewVerdict.FAIL)

    def test_garbage_is_still_an_error(self):
        self.assertEqual(self._parse("not json").verdict, ReviewVerdict.ERROR)


if __name__ == "__main__":
    unittest.main()

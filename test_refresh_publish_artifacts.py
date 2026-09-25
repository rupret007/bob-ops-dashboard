#!/usr/bin/env python3
"""Guard: Pages publish set + race-safe rebase must stay aligned.

Measured fail (2026-09-25 ~7:53 CT, actions run 36137598706):
  refresh.sh rewrote llm-work-now.json (R126) but the Actions commit step only
  git-added index.html + status.json. Leftover unstaged llm-work-now blocked
  `git rebase origin/main` after a concurrent tip move (`cannot rebase: You
  have unstaged changes` → `fatal: no rebase in progress`).

Invariant: unstaged llm-work-now must never block rebase; llm-work-now must
publish with status.json (same commit / same Pages deploy).
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".github" / "workflows" / "refresh-dashboard.yml"
REFRESH = ROOT / "refresh.sh"


class RefreshPublishArtifactsTests(unittest.TestCase):
    def test_workflow_adds_llm_work_now_with_status(self):
        yml = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("llm-work-now.json", yml)
        # Commit step must stage the full Pages set, not a narrow two-file add.
        self.assertIn('PAGES_ARTIFACTS=(index.html status.json llm-work-now.json)', yml)
        self.assertNotIn("git add index.html status.json\n", yml)

    def test_workflow_stashes_before_rebase_on_push_reject(self):
        yml = WORKFLOW.read_text(encoding="utf-8")
        stash_at = yml.find("git stash push --include-untracked")
        rebase_at = yml.find("git rebase origin/main")
        self.assertGreater(stash_at, 0, "workflow must stash dirt before rebase")
        self.assertGreater(rebase_at, stash_at, "stash must precede rebase")
        # Conflict path prefers this job's artifacts for the SAME file set.
        self.assertIn('git checkout --theirs -- "${PAGES_ARTIFACTS[@]}"', yml)
        self.assertIn('git add "${PAGES_ARTIFACTS[@]}"', yml)

    def test_refresh_push_path_documents_and_stages_pages_set(self):
        sh = REFRESH.read_text(encoding="utf-8")
        self.assertIn("Unstaged llm-work-now must never block rebase", sh)
        self.assertIn("it must publish with status.json", sh)
        self.assertIn("llm-work-now.json must ship with status.json", sh)
        self.assertIn('PAGES_ARTIFACTS=(index.html status.json)', sh)
        self.assertIn("git stash push --include-untracked", sh)
        self.assertIn("git rebase origin/main", sh)
        # agents-status stays off the default publish set.
        self.assertIn("Do not commit agents-status.json", sh)

    def test_workflow_dual_sot_assert_before_and_after_push(self):
        yml = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Dual-SoT assert (status ↔ llm-work-now)", yml)
        self.assertIn("assert_dual_sot_files", yml)
        self.assertIn('assert_dual_sot_files("status.json", "llm-work-now.json")', yml)
        # K5 stub listener present (release nudge without cron wait).
        self.assertIn("repository_dispatch:", yml)
        self.assertIn("release-published", yml)
        # Post-push re-assert before exit 0.
        push_at = yml.find("Pushed on attempt")
        post_at = yml.find("dual-SoT PASS post-push")
        self.assertGreater(push_at, 0)
        self.assertGreater(post_at, push_at)

    def test_refresh_k2_k3_dual_sot_wire(self):
        sh = REFRESH.read_text(encoding="utf-8")
        self.assertIn("build_llm_work_now_freshness_meta", sh)
        self.assertIn("assert_dual_sot_files", sh)
        self.assertIn("DUAL_SOT_MAX_STAMP_SKEW_SEC", sh)
        # K3: freshness from the blob just written, not a stale prior.
        self.assertIn("Never claim ok on a stale paired artifact", sh)
        self.assertIn(
            'status["llm_work_now_freshness"] = build_llm_work_now_freshness_meta(_pub)',
            sh,
        )
        # K2: assert after atomic write + post-push.
        self.assertIn(
            'assert_dual_sot_files(root / "status.json", root / "llm-work-now.json")',
            sh,
        )
        self.assertIn("dual-SoT PASS post-push", sh)
        # verify-block guard must remain.
        self.assertIn("drop_leftover_verify", sh)


if __name__ == "__main__":
    unittest.main()

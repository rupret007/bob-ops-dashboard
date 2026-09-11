"""Synthetic git histories for the source-only PR boundary. No remote access."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "qa-source-only-pr.sh"


class SourceOnlyPrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bob-pr-source-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic QA")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.initial = self.commit_tree({"index.html": "old html", "status.json": "old json", "source.py": "old source"})
        self.main = self.commit_tree({"index.html": "new scheduled html", "status.json": "new scheduled json"}, self.initial)
        self.feature = self.commit_tree({"source.py": "new feature"}, self.initial)
        self.merge = self.commit_tree({"index.html": "new scheduled html", "status.json": "new scheduled json", "source.py": "new feature"}, self.main, self.feature)

    def git(self, *args, input=None):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env, text=True, input=input).strip()

    def commit_tree(self, changes, *parents):
        if parents:
            self.git("read-tree", parents[0])
        for name, text in changes.items():
            blob = self.git("hash-object", "-w", "--stdin", input=text)
            self.git("update-index", "--add", "--cacheinfo", "100644," + blob + "," + name)
        tree = self.git("write-tree")
        args = ["commit-tree", tree, "-m", "synthetic fixture"]
        for parent in parents:
            args.extend(["-p", parent])
        return self.git(*args)

    def check(self, checkout=None, head=None):
        self.git("update-ref", "HEAD", checkout or self.merge)
        return subprocess.run(["bash", str(SCRIPT)], cwd=self.root,
                              env={**self.env, "PR_HEAD_SHA": self.feature if head is None else head},
                              capture_output=True, text=True)

    def test_main_only_refresh_does_not_count_as_feature_snapshot_change(self):
        # Reproduce the old event-base gate's false failure.
        old = subprocess.run(["git", "diff", "--quiet", self.initial + "..." + self.merge,
                              "--", "index.html", "status.json"], cwd=self.root, env=self.env)
        self.assertEqual(old.returncode, 1)
        self.assertEqual(self.check().returncode, 0)

    def test_feature_snapshot_edits_are_rejected_even_when_merge_chooses_base(self):
        for name in ("index.html", "status.json"):
            with self.subTest(name=name):
                bad = self.commit_tree({name: "feature changed snapshot"}, self.initial)
                merge = self.commit_tree({}, self.main, bad)
                result = self.check(merge, bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Feature PRs must not change", result.stderr)

    def test_merge_snapshot_edits_are_rejected_even_when_feature_is_clean(self):
        for name in ("index.html", "status.json"):
            with self.subTest(name=name):
                merge = self.commit_tree({name: "integration changed snapshot"}, self.main, self.feature)
                result = self.check(merge)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("integration must preserve", result.stderr)

    def test_wrong_or_missing_head_and_nonmerge_checkout_fail_closed(self):
        for head in ("", "not-a-sha", self.main):
            with self.subTest(head=head):
                self.assertNotEqual(self.check(head=head).returncode, 0)
        self.assertNotEqual(self.check(self.feature).returncode, 0)

    def test_feature_based_on_new_main_is_supported(self):
        feature = self.commit_tree({"source.py": "newer feature"}, self.main)
        merge = self.commit_tree({"source.py": "newer feature"}, self.main, feature)
        self.assertEqual(self.check(merge, feature).returncode, 0)


if __name__ == "__main__":
    unittest.main()

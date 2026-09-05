#!/usr/bin/env python3
"""Offline runner safety contracts. No live repository or provider calls."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from offline_qa_fixtures import (
    FAKE_GH_SOURCE,
    assert_fixture_calls,
    fixture_environment,
    prepare_generator,
    require_temporary_child,
)


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("qa_offline", ROOT / "qa-offline.py")
assert spec and spec.loader
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


class OfflineQaTests(unittest.TestCase):
    def test_fake_program_is_standalone_and_parseable(self):
        self.assertTrue(FAKE_GH_SOURCE.startswith("#!/usr/bin/env python3\n"))
        compile(FAKE_GH_SOURCE, "offline-fake-gh", "exec")

    def test_environment_removes_credentials_probes_and_preloads(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GH_TOKEN": "synthetic-credential-marker",
            "GITHUB_TOKEN": "synthetic-credential-marker",
            "GH_CONFIG_DIR": "/synthetic-owner-config",
            "AGENTS_STATUS_JSON": '{"synthetic_owner_probe":true}',
            "BOB_DASHBOARD_APPLY_DECISIONS": "1",
            "OWNER": "unrelated-owner",
            "PYTHONPATH": "/synthetic-owner-module",
            "NODE_OPTIONS": "--require /synthetic-owner-module",
        }):
            env = fixture_environment(Path(tmp))
            for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "AGENTS_STATUS_JSON", "PYTHONPATH", "NODE_OPTIONS"):
                self.assertNotIn(key, env)
            self.assertEqual(env["BOB_DASHBOARD_APPLY_DECISIONS"], "0")
            self.assertEqual(env["OWNER"], "rupret007")
            self.assertEqual(shutil.which("gh", path=env["PATH"]), str(Path(tmp).resolve() / "fixture-bin" / "gh"))

    def test_known_metadata_is_synthetic_and_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = fixture_environment(Path(tmp))
            result = subprocess.run(
                [str(Path(tmp) / "fixture-bin" / "gh"), "api", "repos/rupret007/StoryLiner"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(result.stdout)["default_branch"], "main")
            self.assertEqual(assert_fixture_calls(Path(env["BOB_DASHBOARD_FIXTURE_LOG"])), [
                {"args": ["api", "repos/rupret007/StoryLiner"], "outcome": "fixture"},
            ])

    def test_unsupported_calls_are_rejected_without_falling_through_to_another_cli(self):
        bad_calls = (
            ["repo", "clone", "rupret007/StoryLiner"],
            ["issue", "close", "1", "-R", "rupret007/bob-ops-dashboard"],
            ["api", "repos/unknown/private-repository"],
            ["api", "repos/rupret007/StoryLiner", "--method", "POST"],
            ["api", "repos/rupret007/StoryLiner/contents/private-file"],
            ["auth", "status"],
        )
        for args in bad_calls:
            with self.subTest(args=args), tempfile.TemporaryDirectory() as tmp:
                scratch = Path(tmp)
                env = fixture_environment(scratch)
                fallback = scratch / "fallback-bin"
                fallback.mkdir()
                marker = scratch / "fallback-was-called"
                fallback_gh = fallback / "gh"
                fallback_gh.write_text(f'#!/bin/sh\n: > "{marker}"\nexit 99\n', encoding="utf-8")
                fallback_gh.chmod(0o755)
                fake_bin = scratch / "fixture-bin"
                env["PATH"] = f"{fake_bin}{os.pathsep}{fallback}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
                self.assertEqual(shutil.which("gh", path=env["PATH"]), str(fake_bin / "gh"))
                result = subprocess.run(["gh", *args], env=env, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(marker.exists())
                with self.assertRaisesRegex(RuntimeError, "unsupported command"):
                    assert_fixture_calls(Path(env["BOB_DASHBOARD_FIXTURE_LOG"]))

    def test_generator_gets_only_source_and_synthetic_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            for name in ("README.md", "board_meta.py", "refresh.sh"):
                (source / name).write_text("fixture source\n", encoding="utf-8")
            for name in ("status.json", "index.html", "agents-status.json"):
                (source / name).write_text("synthetic-owner-data-must-not-copy\n", encoding="utf-8")
            destination = Path(tmp) / "generator"
            prepare_generator(source, destination)
            self.assertEqual(json.loads((destination / "status.json").read_text()), {"offline_fixture": True})
            agents = json.loads((destination / "agents-status.json").read_text())["agents"]
            self.assertEqual([row["state"] for row in agents], ["unknown", "unknown", "unknown"])
            self.assertFalse(any("owner-data" in file.read_text() for file in destination.iterdir()))

    def test_output_must_be_an_empty_non_repository_temporary_child(self):
        for unsafe in (str(ROOT), str(Path("/tmp").resolve()), "/", str(ROOT.parent / "not-temp-output")):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                qa.validate_output_directory(unsafe)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            empty = base / "empty"
            empty.mkdir()
            self.assertEqual(qa.validate_output_directory(str(empty)), empty.resolve())
            (empty / "keep.txt").write_text("do not replace\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty"):
                qa.validate_output_directory(str(empty))
            repo = base / "fixture-repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            with self.assertRaisesRegex(ValueError, "repository"):
                qa.validate_output_directory(str(repo / "output"))
            link = base / "source-link"
            link.symlink_to(ROOT, target_is_directory=True)
            with self.assertRaises(ValueError):
                qa.validate_output_directory(str(link))

    def test_nested_tmpdir_keeps_bsd_mktemp_and_private_fixture_seed_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = fixture_environment(Path(tmp))
            created = subprocess.check_output(
                ["mktemp", "-d", str(Path(env["TMPDIR"]) / "bob-claim-smoke.XXXXXXXX")],
                env=env, text=True,
            ).strip()
            self.assertTrue(Path(created).resolve().is_relative_to(Path(env["TMPDIR"]).resolve()))
            subprocess.run(
                [sys.executable, str(ROOT / "offline_qa_fixtures.py"), "seed-agents", created],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(
                json.loads((Path(created) / "agents-status.json").read_text())["agents"][0]["state"],
                "unknown",
            )
            with tempfile.TemporaryDirectory(dir="/tmp") as system_tmp:
                with patch("offline_qa_fixtures.tempfile.gettempdir", return_value=env["TMPDIR"]):
                    self.assertEqual(require_temporary_child(Path(system_tmp)), Path(system_tmp).resolve())

    def test_preserved_fixture_is_explicitly_labeled_synthetic_and_not_publishable(self):
        with tempfile.TemporaryDirectory() as tmp:
            generator = Path(tmp)
            (generator / "status.json").write_text('{}\n', encoding="utf-8")
            (generator / "index.html").write_text('<body class="tab-home"><main>fixture</main></body>', encoding="utf-8")
            qa.mark_offline_artifacts(generator)
            html = (generator / "index.html").read_text()
            metadata = json.loads((generator / "status.json").read_text())["offline_fixture"]
            self.assertIn('data-offline-fixture="true"', html)
            self.assertIn(qa.OFFLINE_NOTE, html)
            self.assertEqual(metadata["synthetic"], True)
            self.assertEqual(metadata["publishable"], False)

    def test_wrapper_has_no_push_option(self):
        env = {"PATH": os.environ.get("PATH", os.defpath), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [sys.executable, str(ROOT / "qa-offline.py"), "--push"],
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)
        self.assertNotIn("Fetching", result.stdout)


if __name__ == "__main__":
    unittest.main()

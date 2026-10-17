import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cluster.tests.nats_test_util import require_or_skip

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Must match cluster.tests.run_strict.DEFAULT_REQUIRE exactly (order and membership).
PINNED_DEFAULT_REQUIRE = [
    "cluster.tests.test_nats_integration.TestWorkerPermissions.test_template_allows_work_and_denies_administration",
    "cluster.tests.test_nats_integration.TestNatsIntegration.test_setup_and_publish",
    "cluster.tests.test_idle_timeout.TestFetchOrEmpty.test_non_idle_errors_propagate",
    "cluster.tests.test_idle_timeout.TestNatsDependentSkipGuard.test_non_idle_errors_runs_when_nats_available",
]

_WORKFLOW_STRICT_RUN_LINE = "run: python3 -m cluster.tests.run_strict"


def _flatten_tests(suite):
    flat = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            flat.extend(_flatten_tests(item))
        else:
            flat.append(item)
    return flat


class TestStrictGuard(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self.tmpdir.name
        self.pkg = f"synth_pkg_{os.urandom(4).hex()}"
        self.pkg_dir = Path(self.tmp) / self.pkg
        self.pkg_dir.mkdir()
        (self.pkg_dir / "__init__.py").touch()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run_strict(self, env_updates=None, require=None, start=None, top_level=None):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONPATH": f"{_REPO_ROOT}:{self.tmp}",
        }
        if env_updates:
            env.update(env_updates)

        start = self.tmp if start is None else start
        top_level = self.tmp if top_level is None else top_level
        cmd = [
            sys.executable,
            "-m",
            "cluster.tests.run_strict",
            "--start",
            start,
            "--top-level",
            top_level,
        ]
        if require is not None:
            cmd.extend(["--require"] + require)
        else:
            cmd.extend(["--require", "none"])

        return subprocess.run(cmd, cwd=_REPO_ROOT, env=env, capture_output=True, text=True)

    def test_a_all_passing(self):
        (self.pkg_dir / "test_pass.py").write_text(
            "import unittest\n"
            "class TestPass(unittest.TestCase):\n"
            "    def test_ok(self): pass\n"
        )
        proc = self._run_strict()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_b_class_decorated_skip(self):
        (self.pkg_dir / "test_skip.py").write_text(
            "import unittest\n"
            "@unittest.skip('reason')\n"
            "class TestSkip(unittest.TestCase):\n"
            "    def test_ok(self): pass\n"
        )
        proc = self._run_strict()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("skipped=1", proc.stdout)

    def test_c_import_error_skip(self):
        (self.pkg_dir / "test_import_skip.py").write_text(
            "import unittest\n"
            "class TestImportSkip(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        try: import nonexistent_mod_xyz\n"
            "        except ImportError: raise unittest.SkipTest('x')\n"
        )
        proc = self._run_strict()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("skipped=1", proc.stdout)

    def test_d_require_or_skip_strict(self):
        (self.pkg_dir / "test_req_skip.py").write_text(
            "import unittest\n"
            "from cluster.tests.nats_test_util import require_or_skip\n"
            "@require_or_skip(False, 'nats')\n"
            "class TestReqSkip(unittest.TestCase):\n"
            "    def test_ok(self): pass\n"
        )
        proc = self._run_strict({"BOB_REQUIRE_NATS": "1"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("strict", proc.stderr.lower() + proc.stdout.lower())

        proc2 = self._run_strict({"CI": "true"})
        self.assertNotEqual(proc2.returncode, 0)
        self.assertIn("strict", proc2.stderr.lower() + proc2.stdout.lower())

    def test_d_require_or_skip_in_process(self):
        with patch.dict(os.environ, {"CI": "", "BOB_REQUIRE_NATS": ""}):
            @require_or_skip(False, "nats")
            class DummyTestSkip(unittest.TestCase):
                def test_ok(self): pass

            suite = unittest.defaultTestLoader.loadTestsFromTestCase(DummyTestSkip)
            res = unittest.TestResult()
            suite.run(res)
            self.assertEqual(len(res.skipped), 1)
            self.assertEqual(len(res.errors), 0)

        with patch.dict(os.environ, {"BOB_REQUIRE_NATS": "1"}):
            @require_or_skip(False, "nats")
            class DummyTestFail(unittest.TestCase):
                def test_ok(self): pass

            suite = unittest.defaultTestLoader.loadTestsFromTestCase(DummyTestFail)
            res = unittest.TestResult()
            suite.run(res)
            self.assertEqual(len(res.skipped), 0)
            self.assertEqual(len(res.errors), 1)

        with patch.dict(os.environ, {"CI": "true"}):
            @require_or_skip(False, "nats")
            class DummyTestFailCI(unittest.TestCase):
                def test_ok(self): pass

            suite = unittest.defaultTestLoader.loadTestsFromTestCase(DummyTestFailCI)
            res = unittest.TestResult()
            suite.run(res)
            self.assertEqual(len(res.skipped), 0)
            self.assertEqual(len(res.errors), 1)

        with patch.dict(os.environ, {"BOB_REQUIRE_NATS": "1"}):
            class DummyTestFailFunc(unittest.TestCase):
                @require_or_skip(False, "nats")
                def test_ok(self): pass

            suite = unittest.defaultTestLoader.loadTestsFromTestCase(DummyTestFailFunc)
            res = unittest.TestResult()
            suite.run(res)
            self.assertEqual(len(res.skipped), 0)
            self.assertEqual(len(res.failures) + len(res.errors), 1)

    def test_e_missing_required_id(self):
        (self.pkg_dir / "test_missing.py").write_text(
            "import unittest\n"
            "class TestMissing(unittest.TestCase):\n"
            "    def test_ok(self): pass\n"
        )
        proc = self._run_strict(require=[f"{self.pkg}.test_missing.TestMissing.test_renamed"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing_required", proc.stdout)

    def test_f_required_id_skipped(self):
        (self.pkg_dir / "test_req_skipped.py").write_text(
            "import unittest\n"
            "class TestReqSkipped(unittest.TestCase):\n"
            "    @unittest.skip('reason')\n"
            "    def test_ok(self): pass\n"
        )
        proc = self._run_strict(require=[f"{self.pkg}.test_req_skipped.TestReqSkipped.test_ok"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing_required", proc.stdout)

    def test_default_require_is_exactly_pinned(self):
        from cluster.tests.run_strict import DEFAULT_REQUIRE

        self.assertEqual(list(DEFAULT_REQUIRE), list(PINNED_DEFAULT_REQUIRE))
        self.assertEqual(len(DEFAULT_REQUIRE), 4)
        self.assertEqual(
            DEFAULT_REQUIRE[0],
            "cluster.tests.test_nats_integration.TestWorkerPermissions."
            "test_template_allows_work_and_denies_administration",
        )

    def test_default_required_ids_resolve(self):
        from cluster.tests.run_strict import DEFAULT_REQUIRE

        loader = unittest.TestLoader()
        for req in DEFAULT_REQUIRE:
            suite = loader.loadTestsFromName(req)
            flat = _flatten_tests(suite)
            self.assertEqual(len(flat), 1, f"Could not resolve {req}: {flat!r}")
            test = flat[0]
            self.assertNotEqual(
                type(test).__name__,
                "_FailedTest",
                f"{req} resolved to placeholder _FailedTest "
                f"({getattr(test, '_exception', None)})",
            )
            self.assertEqual(
                test.id(),
                req,
                f"{req} resolved to id {test.id()!r} (placeholder or rename)",
            )

    def test_workflow_file_contents(self):
        wf = _REPO_ROOT / ".github" / "workflows" / "cluster-tests.yml"
        content = wf.read_text()
        self.assertIn('BOB_REQUIRE_NATS: "1"', content)
        self.assertNotIn("unittest discover", content)

        strict_lines = [
            line for line in content.splitlines() if "cluster.tests.run_strict" in line
        ]
        self.assertEqual(
            len(strict_lines),
            1,
            f"expected exactly one run_strict line, got {strict_lines!r}",
        )
        self.assertEqual(strict_lines[0].strip(), _WORKFLOW_STRICT_RUN_LINE)
        self.assertNotIn("--require", strict_lines[0])
        self.assertNotIn("||", strict_lines[0])

    def test_zero_tests_run_fails(self):
        empty = Path(self.tmp) / "empty_start"
        empty.mkdir()
        proc = self._run_strict(start=str(empty), top_level=str(empty), require=["none"])
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ran=0", proc.stdout)

    def test_runner_exits_nonzero_on_failure(self):
        (self.pkg_dir / "test_fail.py").write_text(
            "import unittest\n"
            "class TestFail(unittest.TestCase):\n"
            "    def test_boom(self):\n"
            "        self.fail('boom')\n"
        )
        proc = self._run_strict()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("failed=1", proc.stdout)

    def test_runner_returns_nonzero_in_process_on_failure(self):
        """Catch mutations that force sys.exit(0) by testing the return code API."""
        from cluster.tests import run_strict as rs

        (self.pkg_dir / "test_fail_ip.py").write_text(
            "import unittest\n"
            "class TestFailIP(unittest.TestCase):\n"
            "    def test_boom(self):\n"
            "        self.fail('boom')\n"
        )
        code = rs.run_strict(start=self.tmp, top_level=self.tmp, require=["none"])
        self.assertEqual(code, 1)

    def test_expected_failure_fails_strict(self):
        (self.pkg_dir / "test_xfail.py").write_text(
            "import unittest\n"
            "class TestXFail(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n"
            "    def test_broken(self):\n"
            "        raise AssertionError('broken')\n"
        )
        proc = self._run_strict()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("expectedFailures=1", proc.stdout)

    def test_unexpected_success_fails_strict(self):
        (self.pkg_dir / "test_xpass.py").write_text(
            "import unittest\n"
            "class TestXPass(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n"
            "    def test_ok(self):\n"
            "        pass\n"
        )
        proc = self._run_strict()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("unexpectedSuccesses=1", proc.stdout)

    def test_run_strict_main_block_exits_with_main_return(self):
        src = (_REPO_ROOT / "cluster" / "tests" / "run_strict.py").read_text()
        self.assertIn("sys.exit(main())", src)
        # Failure path must be a returned code, not a hard-coded exit that can
        # be flipped to 0 while leaving tests green at the process level.
        self.assertNotIn("sys.exit(1)", src)
        self.assertNotIn("sys.exit(0)", src)

    def test_run_strict_checks_zero_ran_and_expected_failures(self):
        src = (_REPO_ROOT / "cluster" / "tests" / "run_strict.py").read_text()
        self.assertIn("ran == 0", src)
        self.assertIn("expected_failures > 0", src)
        self.assertIn("unexpected_successes > 0", src)
        self.assertIn("missing_required", src)


if __name__ == "__main__":
    unittest.main()

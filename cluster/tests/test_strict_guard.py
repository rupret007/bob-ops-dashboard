import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cluster.tests.nats_test_util import require_or_skip

_REPO_ROOT = Path(__file__).resolve().parents[2]

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

    def _run_strict(self, env_updates=None, require=None):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONPATH": f"{_REPO_ROOT}:{self.tmp}",
        }
        if env_updates:
            env.update(env_updates)

        cmd = [sys.executable, "-m", "cluster.tests.run_strict", "--start", self.tmp, "--top-level", self.tmp]
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

    def test_default_required_ids_resolve(self):
        from cluster.tests.run_strict import DEFAULT_REQUIRE
        loader = unittest.TestLoader()
        for req in DEFAULT_REQUIRE:
            suite = loader.loadTestsFromName(req)
            self.assertGreater(suite.countTestCases(), 0, f"Could not resolve {req}")

    def test_workflow_file_contents(self):
        wf = _REPO_ROOT / ".github" / "workflows" / "cluster-tests.yml"
        content = wf.read_text()
        self.assertIn("cluster.tests.run_strict", content)
        self.assertIn('BOB_REQUIRE_NATS: "1"', content)
        self.assertNotIn("unittest discover", content)

if __name__ == "__main__":
    unittest.main()

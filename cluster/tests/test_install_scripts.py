"""Tests for install script JSON validation logic."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestJSONValidation(unittest.TestCase):
    """Test that JSON validation works correctly for self-check."""

    def test_valid_json_passes(self):
        """Valid JSON file should pass validation."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"timestamp": "2026-10-17T00:00:00Z", "dispatch_decisions": []}, f)
            f.flush()
            
            result = subprocess.run(
                ["python3", "-c", "import json,sys; json.load(open(sys.argv[1]))", f.name],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            
            Path(f.name).unlink()

    def test_invalid_json_fails(self):
        """Invalid JSON should fail validation."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json {")
            f.flush()
            
            result = subprocess.run(
                ["python3", "-c", "import json,sys; json.load(open(sys.argv[1]))", f.name],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            
            Path(f.name).unlink()

    def test_empty_file_fails(self):
        """Empty file should fail validation."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.flush()
            
            result = subprocess.run(
                ["python3", "-c", "import json,sys; json.load(open(sys.argv[1]))", f.name],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            
            Path(f.name).unlink()

    def test_json_with_dispatch_decisions(self):
        """Real shadow runner output format should pass."""
        output = {
            "timestamp": "2026-10-17T18:00:00+00:00",
            "machine": "nucbox",
            "dispatch_decisions": [
                {"timestamp": "2026-10-17T18:00:00+00:00", "decision_type": "dispatch"}
            ],
            "gate_decisions": [],
            "totals": {
                "would_dispatch": 1,
                "dispatch_skipped": 0,
                "gate_pass": 0,
                "gate_fail": 0,
                "gate_needs_llm": 0
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(output, f)
            f.flush()
            
            result = subprocess.run(
                ["python3", "-c", "import json,sys; json.load(open(sys.argv[1]))", f.name],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            
            Path(f.name).unlink()


class TestShadowRunnerOutput(unittest.TestCase):
    """Test that shadow runner produces valid JSON."""

    def test_shadow_runner_json_output(self):
        """Shadow runner --json should produce valid JSON."""
        repo_root = Path(__file__).parent.parent.parent
        
        log_dir = tempfile.TemporaryDirectory()
        self.addCleanup(log_dir.cleanup)
        result = subprocess.run(
            ["python3", "-m", "cluster.control_plane.shadow_runner", "--machine", "mini", "--json", "--log-dir", log_dir.name],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env={"PYTHONPATH": str(repo_root)},
            timeout=30,
        )
        
        self.assertEqual(result.returncode, 0, f"Shadow runner failed: {result.stderr}")
        
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"Shadow runner output is not valid JSON: {e}\nOutput: {result.stdout[:500]}")
        
        self.assertIn("timestamp", data)
        self.assertIn("machine", data)
        self.assertIn("dispatch_decisions", data)


class TestLiveMarkerPreservation(unittest.TestCase):
    """Test that --live mode is preserved across reinstalls."""

    def test_sed_pattern_adds_live_to_execstart(self):
        """The sed pattern should add --live to ExecStart lines."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.service', delete=False) as f:
            f.write("""[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 -m cluster.control_plane.dispatcher --machine nucbox
WorkingDirectory=/home/user/repo
""")
            f.flush()
            
            # Run the exact sed command from go-live-nucbox.sh
            result = subprocess.run(
                ["sed", "-i", r"/^ExecStart=/{/--live/!s/$/ --live/}", f.name],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            
            content = Path(f.name).read_text()
            self.assertIn("--live", content)
            self.assertIn("ExecStart=/usr/bin/env python3 -m cluster.control_plane.dispatcher --machine nucbox --live", content)
            
            Path(f.name).unlink()

    def test_sed_pattern_is_idempotent(self):
        """Running sed twice should not add --live twice."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.service', delete=False) as f:
            f.write("""[Service]
ExecStart=/usr/bin/env python3 -m cluster.control_plane.conductor
""")
            f.flush()
            
            # Run sed twice
            subprocess.run(["sed", "-i", r"/^ExecStart=/{/--live/!s/$/ --live/}", f.name])
            subprocess.run(["sed", "-i", r"/^ExecStart=/{/--live/!s/$/ --live/}", f.name])
            
            content = Path(f.name).read_text()
            # Should have exactly one --live
            self.assertEqual(content.count("--live"), 1)
            
            Path(f.name).unlink()

    def test_sed_pattern_preserves_existing_live(self):
        """Files already with --live should not be modified."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.service', delete=False) as f:
            original = """[Service]
ExecStart=/usr/bin/env python3 -m cluster.control_plane.dispatcher --machine nucbox --live
"""
            f.write(original)
            f.flush()
            
            subprocess.run(["sed", "-i", r"/^ExecStart=/{/--live/!s/$/ --live/}", f.name])
            
            content = Path(f.name).read_text()
            self.assertEqual(content, original)
            
            Path(f.name).unlink()


if __name__ == "__main__":
    unittest.main()

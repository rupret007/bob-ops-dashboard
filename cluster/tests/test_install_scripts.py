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
        
        result = subprocess.run(
            ["python3", "-m", "cluster.control_plane.shadow_runner", "--machine", "mini", "--json"],
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


if __name__ == "__main__":
    unittest.main()

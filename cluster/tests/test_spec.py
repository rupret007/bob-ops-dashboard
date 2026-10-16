import json
import unittest

from cluster.spec import SpecError, validate_job_spec


class TestJobSpec(unittest.TestCase):
    def base(self):
        return {
            "v": 1,
            "id": "job-1",
            "class": "test",
            "repo": "rupret007/bob-ops-dashboard",
            "ref": "main",
            "cmd": ["npm", "test"],
            "timeout_s": 60,
            "load_ceiling": 4.0,
        }

    def test_valid(self):
        spec = validate_job_spec(self.base())
        self.assertEqual(spec["id"], "job-1")

    def test_reject_secret_in_cmd(self):
        data = self.base()
        data["cmd"] = ["echo", "ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"]
        with self.assertRaises(SpecError):
            validate_job_spec(data)

    def test_reject_env_secret(self):
        data = self.base()
        data["env"] = {"AWS": "AKIAIOSFODNN7EXAMPLE"}
        with self.assertRaises(SpecError):
            validate_job_spec(data)

    def test_argv_only_no_shell(self):
        data = self.base()
        data["cmd"] = ["bash", "-c", "echo hi"]
        with self.assertRaises(SpecError):
            validate_job_spec(data)
        data["cmd"] = [123]
        with self.assertRaises(SpecError):
            validate_job_spec(data)


if __name__ == "__main__":
    unittest.main()

"""Credential detection must not copy the detected value into its report."""

from dataclasses import asdict
import json
import unittest

from cluster.control_plane.local_karen.checks import (
    PRInfo, check_no_secrets_in_diff, run_static_security_scan,
)
from cluster.control_plane.local_karen.config import KarenConfig


class SecretReportingTests(unittest.TestCase):
    def test_detected_value_is_redacted_from_every_report_field(self):
        synthetic = "ghp_" + "x" * 36
        pr = PRInfo("owner", "repo", 1, "a" * 40, "test", "author", False,
                    'diff --git a/config.py b/config.py\n@@ -0,0 +1 @@\n+TOKEN = "' + synthetic + '"\n',
                    {}, ["config.py"], [])
        for check in (check_no_secrets_in_diff, run_static_security_scan):
            with self.subTest(check=check.__name__):
                result = check(pr, KarenConfig())
                self.assertTrue(result.failed)
                self.assertIn("config.py", result.details)
                self.assertNotIn(synthetic, json.dumps(asdict(result), default=str))
                self.assertNotIn(synthetic[:20], result.details)

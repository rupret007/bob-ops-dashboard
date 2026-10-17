import unittest

from live.sanitize import sanitize


class SanitizeTests(unittest.TestCase):
    def test_aws_key(self):
        self.assertNotIn("AKIA", sanitize("key AKIAIOSFODNN7EXAMPLE here"))

    def test_slack_token(self):
        self.assertEqual(sanitize("xoxb-1234567890-abcdef"), "[REDACTED_SLACK]")

    def test_openai_sk(self):
        self.assertTrue(sanitize("sk-" + "a" * 24).startswith("[REDACTED"))

    def test_gh_pat(self):
        self.assertIn("[REDACTED_GH]", sanitize("ghp_" + "x" * 40))

    def test_jwt(self):
        tok = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        self.assertIn("[REDACTED_JWT]", sanitize(tok))

    def test_email(self):
        self.assertIn("[REDACTED_EMAIL]", sanitize("contact me@example.com please"))

    def test_phone(self):
        self.assertNotIn("312-555-0199", sanitize("call 312-555-0199 now"))

    def test_home_path(self):
        self.assertIn("/home/[user]", sanitize("/home/rupret/work"))

    def test_users_path(self):
        self.assertIn("/Users/[user]", sanitize("/Users/jeff/Documents"))

    def test_truncate(self):
        self.assertLessEqual(len(sanitize("a" * 200)), 120)


if __name__ == "__main__":
    unittest.main()

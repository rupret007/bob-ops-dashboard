import unittest

from cluster.sanitize import sanitize_tail


class TestSanitize(unittest.TestCase):
    def test_redacts_ghp(self):
        tail = "failed: token ghp_abcdefghijklmnopqrstuvwxyz1234567890AB leaked"
        out = sanitize_tail(tail)
        self.assertNotIn("ghp_", out)
        self.assertIn("[REDACTED]", out)

    def test_truncates_to_2kb(self):
        out = sanitize_tail("x" * 5000)
        self.assertLessEqual(len(out.encode()), 2048)


if __name__ == "__main__":
    unittest.main()

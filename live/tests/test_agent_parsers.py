import unittest
from pathlib import Path

from live.agent import parse_meminfo, parse_screen_ls, parse_vm_stat

FIX = Path(__file__).resolve().parent / "fixtures"


class AgentParserTests(unittest.TestCase):
    def test_vm_stat(self):
        text = (FIX / "vm_stat.txt").read_text(encoding="utf-8")
        m = parse_vm_stat(text)
        self.assertGreater(m["mem_total_bytes"], 0)
        self.assertGreater(m["mem_used_bytes"], 0)

    def test_meminfo(self):
        text = (FIX / "meminfo.txt").read_text(encoding="utf-8")
        m = parse_meminfo(text)
        self.assertEqual(m["mem_total_bytes"], 24576000 * 1024)
        self.assertEqual(m["swap_used_bytes"], 0)

    def test_screen_ls(self):
        text = (FIX / "screen_ls.txt").read_text(encoding="utf-8")
        names = parse_screen_ls(text)
        self.assertIn("lane-site-contact", names)
        self.assertIn("svc-dashboard-refresh", names)
        self.assertNotIn("random-session", names)


if __name__ == "__main__":
    unittest.main()

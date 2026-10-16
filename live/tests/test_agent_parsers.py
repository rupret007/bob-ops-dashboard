import unittest
from pathlib import Path

from live.agent import (
    lane_log_stem,
    parse_lane_log_tail,
    parse_meminfo,
    parse_screen_ls,
    parse_screen_routines,
    parse_vm_stat,
    runs_on_for_session,
)

FIX = Path(__file__).resolve().parent / "fixtures"


class AgentParserTests(unittest.TestCase):
    def test_vm_stat(self):
        text = (FIX / "vm_stat.txt").read_text(encoding="utf-8")
        m = parse_vm_stat(text)
        self.assertGreater(m["mem_total_bytes"], 0)
        self.assertGreater(m["mem_used_bytes"], 0)

    def test_vm_stat_16k_page_size_and_hw_memsize(self):
        text = (FIX / "vm_stat_16k.txt").read_text(encoding="utf-8")
        memsize = 8 * 1024 ** 3
        m = parse_vm_stat(text, memsize_bytes=memsize)
        self.assertEqual(memsize, m["mem_total_bytes"])
        used_pages = 120000 + 80000 + 5000
        self.assertEqual(used_pages * 16384, m["mem_used_bytes"])

    def test_meminfo(self):
        text = (FIX / "meminfo.txt").read_text(encoding="utf-8")
        m = parse_meminfo(text)
        self.assertEqual(m["mem_total_bytes"], 24576000 * 1024)
        self.assertEqual(m["swap_used_bytes"], 0)

    def test_screen_ls(self):
        text = (FIX / "screen_ls.txt").read_text(encoding="utf-8")
        names = parse_screen_ls(text)
        self.assertIn("lane-site-contact", names)
        self.assertNotIn("svc-dashboard-refresh", names)
        self.assertNotIn("random-session", names)

    def test_screen_ls_macos_tabs(self):
        text = (FIX / "screen_ls_macos.txt").read_text(encoding="utf-8")
        names = parse_screen_ls(text)
        self.assertIn("lane-games-music2", names)
        self.assertIn("nlane-site-contact", names)
        self.assertIn("grok-explore-a", names)
        self.assertIn("resume-hotfix-1", names)
        self.assertNotIn("svc-dashboard-refresh", names)
        routines = parse_screen_routines(text)
        self.assertIn("svc-dashboard-refresh", routines)

    def test_lane_log_and_runs_on(self):
        tail = (FIX / "lane_games_music2.log").read_text(encoding="utf-8")
        parsed = parse_lane_log_tail(tail)
        self.assertEqual("bob-brain", parsed["repo"])
        self.assertEqual("running", parsed["status"])
        self.assertEqual("games-music2", lane_log_stem("lane-games-music2"))
        self.assertEqual("nuc", runs_on_for_session("nlane-site-contact", "mini"))
        self.assertEqual("mini", runs_on_for_session("lane-games-music2", "mini"))


if __name__ == "__main__":
    unittest.main()

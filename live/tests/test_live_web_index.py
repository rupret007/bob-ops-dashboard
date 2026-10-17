"""Static checks on live/web/index.html (no browser)."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "live" / "web" / "index.html"


class LiveWebIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_server_strings_are_escaped_at_runtime(self):
        subprocess.run(["node", str(ROOT / "live/tests/test_web_security.js")], check=True, timeout=10)

    def test_viewport_meta(self):
        self.assertIn('name="viewport"', self.html)
        self.assertIn("width=device-width", self.html)

    def test_no_overwide_fixed_css_widths(self):
        style_m = re.search(r"<style>(.*?)</style>", self.html, re.DOTALL)
        self.assertIsNotNone(style_m, "missing <style>")
        for m in re.finditer(r"(?<![\w-])width\s*:\s*(\d+)px", style_m.group(1)):
            self.assertLessEqual(
                int(m.group(1)),
                390,
                f"width:{m.group(1)}px exceeds 390px mobile viewport",
            )

    def test_arc_path_generator_exists(self):
        self.assertRegex(
            self.html,
            r"function\s+(describeArc|svgArcPath|arcPath)\s*\(",
        )

    def test_gauge_label_and_value_separate(self):
        self.assertIn("gauge-label", self.html)
        self.assertIn("gauge-value", self.html)
        self.assertIn('class="gauge-label"', self.html)
        self.assertIn('class="gauge-value"', self.html)

    def test_speed_sheet_hidden_until_open(self):
        self.assertIn('id="sheet"', self.html)
        self.assertIn('id="sheetBackdrop"', self.html)
        self.assertIn("sheet-backdrop", self.html)
        self.assertRegex(self.html, r'id="sheet"[^>]*\bhidden\b')
        self.assertIn("function closeSheet", self.html)
        self.assertIn("Escape", self.html)
        self.assertIn("sheetBackdrop", self.html)

    def test_sparkline_only_on_hero_and_throughput(self):
        self.assertRegex(
            self.html,
            r"svgId!=='heroSpark'&&svgId!=='thruSpark'",
        )
        self.assertNotRegex(self.html, r"sparkline\([^)]*gauge")


if __name__ == "__main__":
    unittest.main()

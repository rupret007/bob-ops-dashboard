import unittest

from live.gate_parse import parse_progress_text
from live.sanitize import sanitize


class GateParseTests(unittest.TestCase):
    def test_gate_request(self):
        text = "KAREN_GATE_REQUEST pr=79 sha=abcdef0123456789 repo=Turdanoid"
        out = parse_progress_text(text)
        self.assertEqual(1, len(out))
        self.assertEqual(79, out[0]["pr"])
        self.assertEqual("abcdef01", out[0]["sha"])
        self.assertEqual("REQUESTED", out[0]["verdict"])
        self.assertEqual("Turdanoid", out[0]["repo"])

    def test_karen_section_line(self):
        text = """## Karen
- #142 0c0128cde9 PASS
Some free text with Jeff's phone 312-555-0199 should not appear in structured fields.
"""
        out = parse_progress_text(text)
        self.assertEqual(1, len(out))
        self.assertEqual("PASS", out[0]["verdict"])
        blob = sanitize(str(out))
        self.assertNotIn("312-555-0199", blob)
        self.assertNotIn("Jeff", blob)

    def test_phone_in_line_not_in_output(self):
        text = "## Karen\n- #1 deadbeef PASS call 312-555-0199\n"
        out = parse_progress_text(text)
        sanitized = sanitize(str(out))
        self.assertNotIn("312-555-0199", sanitized)

    def test_karen_heading_and_note_times(self):
        text = """## Karen nightly 2026-10-16 16:18 CT
NOTE 16:40 CT
KAREN_GATE_REQUEST pr=79 sha=abcdef0123456789
- #79 abcdef01 PASS
"""
        out = parse_progress_text(text)
        self.assertEqual("4:40 PM", out[0]["at_display"])
        self.assertEqual("4:40 PM", out[1]["at_display"])

    def test_unknown_time_not_now(self):
        text = "KAREN_GATE_REQUEST pr=1 sha=1234567890abcdef"
        out = parse_progress_text(text)
        self.assertEqual("time unknown", out[0]["at_display"])

    def test_heading_carry_for_gate_outside_karen(self):
        text = """## Progress 2026-10-16 16:18 CT
KAREN_GATE_REQUEST pr=79 sha=abcdef0123456789
"""
        out = parse_progress_text(text)
        self.assertEqual("4:18 PM", out[0]["at_display"])

    def test_mtime_fallback_tilde_ct(self):
        text = "KAREN_GATE_REQUEST pr=1 sha=1234567890abcdef"
        # 2026-10-16 21:30:00 UTC -> 4:30 PM CT (CDT)
        out = parse_progress_text(text, file_mtime=1760644200.0)
        self.assertTrue(out[0]["at_display"].startswith("~"))
        self.assertIn("CT", out[0]["at_display"])


if __name__ == "__main__":
    unittest.main()

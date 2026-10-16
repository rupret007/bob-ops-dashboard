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


if __name__ == "__main__":
    unittest.main()

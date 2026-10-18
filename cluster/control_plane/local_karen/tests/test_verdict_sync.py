import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster.control_plane.local_karen import verdict_sync
from cluster.control_plane.local_karen.verdict import Verdict

SHA = "a" * 40


def _verdict(passed=False, sha=SHA, pr=7):
    return Verdict(owner="rupret007", repo="demo", pr_number=pr, head_sha=sha, passed=passed,
                   reason="r", timestamp="t", reviewer="karen", tier="t1",
                   confidence=1.0, check_results=[], review_results=[])


def _name(v):
    return "rupret007_demo_{}_{}_{}.json".format(v.pr_number, v.head_sha[:12],
                                                 "KARENPASS" if v.passed else "KARENFAIL")


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestVerdictSync(unittest.TestCase):
    def test_validate_accepts_consistent_artifact(self):
        v = _verdict()
        self.assertIsNotNone(verdict_sync.validate_entry(_name(v), v.to_json()))

    def test_validate_rejects_name_content_mismatch(self):
        v = _verdict()
        other = _verdict(pr=8)
        self.assertIsNone(verdict_sync.validate_entry(_name(other), v.to_json()))
        self.assertIsNone(verdict_sync.validate_entry(_name(v).replace("FAIL", "PASS"), v.to_json()))

    def test_validate_rejects_short_sha_and_traversal(self):
        v = _verdict(sha="a" * 12)
        self.assertIsNone(verdict_sync.validate_entry(_name(v), v.to_json()))
        self.assertIsNone(verdict_sync.validate_entry("../x_1_" + "a" * 12 + "_KARENFAIL.json", _verdict().to_json()))
        self.assertIsNone(verdict_sync.validate_entry(_name(_verdict()), "not json"))

    def test_collect_skips_symlinks_and_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            v = _verdict()
            (d / _name(v)).write_text(v.to_json())
            (d / "rupret007_demo_7_aaaaaaaaaaaa_LATEST.json").symlink_to(d / _name(v))
            (d / "rupret007_demo_9_bbbbbbbbbbbb_KARENFAIL.json").write_text("{}")
            self.assertEqual(list(verdict_sync.collect_verdicts(d)), [_name(v)])

    def test_pull_writes_only_valid_entries(self):
        good = _verdict()
        bad = _verdict(pr=8)
        body = json.dumps({"verdicts": {_name(good): good.to_json(),
                                        _name(_verdict(pr=9)): bad.to_json()}}).encode()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(verdict_sync.urllib.request, "urlopen", return_value=_Resp(body)):
            self.assertEqual(verdict_sync.pull("http://x", Path(d)), 1)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), [_name(good)])
            self.assertEqual(verdict_sync.pull("http://x", Path(d)), 0)

    def test_share_refuses_wildcard_bind(self):
        self.assertIsNone(verdict_sync.start_share_thread(Path("/nonexistent"), bind="0.0.0.0", port=0))

    def test_share_serves_only_verdict_path(self):
        import urllib.error
        import urllib.request
        with tempfile.TemporaryDirectory() as d:
            v = _verdict()
            (Path(d) / _name(v)).write_text(v.to_json())
            server = verdict_sync.start_share_thread(Path(d), bind="127.0.0.1", port=0)
            self.assertIsNotNone(server)
            try:
                port = server.server_address[1]
                data = json.loads(urllib.request.urlopen(
                    "http://127.0.0.1:{}{}".format(port, verdict_sync.SHARE_PATH), timeout=5).read())
                self.assertEqual(list(data["verdicts"]), [_name(v)])
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen("http://127.0.0.1:{}/etc/passwd".format(port), timeout=5)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Fail-closed unit tests for verify preserve (Jeff iPhone Unlock wipe race)."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from preserve_verify import (
    GRACE_MS,
    JEFF_EMAIL,
    TTL_MS,
    apply_preserved_verify,
    apply_status_file,
    normalize_verify,
    should_preserve,
)

NOW = 1_787_453_382_996  # frozen ms, independent of wall clock
SHA = hashlib.sha256(b"000000:jeffstory007@gmail.com").hexdigest()


def challenge(*, issued_at, exp, email=JEFF_EMAIL, sha=SHA):
    return {"email": email, "sha256": sha, "exp": exp, "issued_at": issued_at}


def old_exp_only_keep(v, now_ms):
    """The wipe bug: keep only if exp > now (no issued_at window, no grace)."""
    try:
        return int(v["exp"]) > int(now_ms)
    except (KeyError, TypeError, ValueError):
        return False


class NormalizeTests(unittest.TestCase):
    def test_allowlisted_hex_ok(self):
        v = normalize_verify(challenge(issued_at=NOW, exp=NOW + TTL_MS))
        self.assertIsNotNone(v)
        self.assertEqual(v["email"], JEFF_EMAIL)
        self.assertEqual(v["sha256"], SHA)

    def test_missing_email_fail_closed(self):
        raw = challenge(issued_at=NOW, exp=NOW + TTL_MS)
        del raw["email"]
        self.assertIsNone(normalize_verify(raw))

    def test_wrong_email_fail_closed(self):
        self.assertIsNone(
            normalize_verify(challenge(issued_at=NOW, exp=NOW + 1, email="other@example.com"))
        )

    def test_email_not_defaulted_to_jeff(self):
        # Old refresh fail-open: (email or jeffstory007@gmail.com)
        raw = {"sha256": SHA, "exp": NOW + TTL_MS, "issued_at": NOW}
        self.assertIsNone(normalize_verify(raw))

    def test_bad_sha_fail_closed(self):
        self.assertIsNone(
            normalize_verify(challenge(issued_at=NOW, exp=NOW + 1, sha="deadbeef"))
        )
        self.assertIsNone(
            normalize_verify(challenge(issued_at=NOW, exp=NOW + 1, sha="g" * 64))
        )

    def test_missing_exp_fail_closed(self):
        raw = {"email": JEFF_EMAIL, "sha256": SHA, "issued_at": NOW}
        self.assertIsNone(normalize_verify(raw))


class WipeRaceTests(unittest.TestCase):
    def test_repro_15m_otp_refresh_overrun_old_logic_wipes(self):
        """Written repro: 15m OTP, cron near TTL end, 20s rebuild, exp past.

        Timeline (matches Jeff's iPhone Unlock miss):
          t=0        Bob issues OTP, exp = issued_at + 15m, pushes status.json.verify
          t=14m50s   Actions cron starts refresh (10s of TTL left)
          t=15m10s   rebuild finished (~20s); preserve sees exp < now and used to drop
          client     status.json.verify is gone → "No code active yet. Ask Bob for one."
        """
        ttl_15m = 15 * 60 * 1000
        leftover_at_start_ms = 10 * 1000
        rebuild_ms = 20 * 1000
        refresh_start = NOW
        issued_at = refresh_start - ttl_15m + leftover_at_start_ms
        exp = issued_at + ttl_15m
        preserve_now = refresh_start + rebuild_ms
        self.assertLess(exp, preserve_now, "repro setup: exp must be past at preserve-time")
        v = challenge(issued_at=issued_at, exp=exp)
        self.assertFalse(
            old_exp_only_keep(v, preserve_now),
            "old exp>now gate wipes the live OTP — this is the iPhone bug",
        )
        keep, reason = should_preserve(normalize_verify(v), preserve_now)
        self.assertTrue(keep, reason)
        self.assertIn("preserve verify", reason)

    def test_repro_2h_ttl_last_seconds_rebuild_still_kept(self):
        issued_at = NOW - TTL_MS + 10_000
        exp = issued_at + TTL_MS  # 10s of stored exp left
        preserve_now = exp + 20_000  # 20s rebuild overruns exp
        v = normalize_verify(challenge(issued_at=issued_at, exp=exp))
        self.assertFalse(old_exp_only_keep(v, preserve_now))
        keep, reason = should_preserve(v, preserve_now)
        self.assertTrue(keep, reason)
        self.assertIn("grace", reason)

    def test_within_issued_at_ttl_kept_even_if_exp_already_past(self):
        issued_at = NOW - 60_000
        exp = NOW - 1  # stale/short exp; issued_at+2h still live
        keep, reason = should_preserve(
            normalize_verify(challenge(issued_at=issued_at, exp=exp)), NOW
        )
        self.assertTrue(keep, reason)
        self.assertIn("issued_at+TTL still live", reason)

    def test_true_expiry_dropped_and_logged(self):
        issued_at = NOW - TTL_MS - GRACE_MS - 1
        exp = issued_at + TTL_MS
        keep, reason = should_preserve(
            normalize_verify(challenge(issued_at=issued_at, exp=exp)), NOW
        )
        self.assertFalse(keep, reason)
        self.assertIn("drop verify", reason)
        self.assertIn("expired", reason)

    def test_legacy_no_issued_at_uses_exp_plus_grace(self):
        raw = {"email": JEFF_EMAIL, "sha256": SHA, "exp": NOW - 1}
        v = normalize_verify(raw)
        self.assertIsNotNone(v)
        keep, reason = should_preserve(v, NOW)
        self.assertTrue(keep, reason)
        self.assertIn("no issued_at", reason)
        keep2, reason2 = should_preserve(v, NOW + GRACE_MS + 1)
        self.assertFalse(keep2, reason2)

    def test_refresh_started_ms_keeps_exp_that_lapses_during_rebuild(self):
        """Clock is refresh start, not preserve-time. 20s rebuild cannot wipe."""
        start = NOW
        rebuild_ms = 20 * 1000
        exp = start + 10_000
        issued_at = exp - 15 * 60 * 1000
        end = start + rebuild_ms
        v = challenge(issued_at=issued_at, exp=exp)
        self.assertGreater(exp, start)
        self.assertLess(exp, end)
        self.assertFalse(old_exp_only_keep(v, end))
        keep_start, reason = should_preserve(normalize_verify(v), start)
        self.assertTrue(keep_start, reason)
        self.assertIn("unexpired at refresh_started_ms", reason)
        logs = []
        status = {}
        kept = apply_preserved_verify(
            status,
            {"verify": v},
            refresh_started_ms=start,
            log=logs.append,
        )
        self.assertIsNotNone(kept)
        self.assertTrue(any("refresh_started_ms=" + str(start) in m for m in logs))


class ApplyTests(unittest.TestCase):
    def test_apply_copies_verify(self):
        prev = {
            "verify": challenge(issued_at=NOW - 1000, exp=NOW - 1000 + TTL_MS),
        }
        status = {}
        logs = []
        kept = apply_preserved_verify(status, prev, now_ms=NOW, log=logs.append)
        self.assertIsNotNone(kept)
        self.assertEqual(status["verify"]["email"], JEFF_EMAIL)
        self.assertEqual(status["verify"]["sha256"], SHA)
        self.assertTrue(any("preserve verify" in m for m in logs))

    def test_apply_drops_wrong_email_without_writing(self):
        prev = {"verify": challenge(issued_at=NOW, exp=NOW + TTL_MS, email="x@y.z")}
        status = {}
        logs = []
        kept = apply_preserved_verify(status, prev, now_ms=NOW, log=logs.append)
        self.assertIsNone(kept)
        self.assertNotIn("verify", status)
        self.assertTrue(any("fail-closed" in m for m in logs))

    def test_apply_status_file_seed_survives(self):
        verify = challenge(issued_at=NOW - 5_000, exp=NOW - 5_000 + TTL_MS)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            path.write_text(json.dumps({"generated_at": "x", "verify": verify}) + "\n")
            kept = apply_status_file(str(path), now_ms=NOW)
            self.assertIsNotNone(kept)
            data = json.loads(path.read_text())
            self.assertEqual(data["verify"]["sha256"], SHA)
            self.assertEqual(data["verify"]["email"], JEFF_EMAIL)

    def test_apply_status_file_true_expiry_removed(self):
        verify = challenge(
            issued_at=NOW - TTL_MS - GRACE_MS - 50,
            exp=NOW - GRACE_MS - 50,
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            path.write_text(json.dumps({"verify": verify}) + "\n")
            kept = apply_status_file(str(path), now_ms=NOW)
            self.assertIsNone(kept)
            data = json.loads(path.read_text())
            self.assertNotIn("verify", data)

    def test_constants_stay_2h_and_jeff_only(self):
        self.assertEqual(JEFF_EMAIL, "jeffstory007@gmail.com")
        self.assertEqual(TTL_MS, 2 * 60 * 60 * 1000)
        self.assertGreaterEqual(GRACE_MS, 20 * 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)

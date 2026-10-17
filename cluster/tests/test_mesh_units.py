"""Unit tests: capabilities, routing, join codes, registry, rendering (no server needed)."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("BOB_BCRYPT_ROUNDS", "4")

from cluster.capabilities import (  # noqa: E402
    CAPABILITIES, GUEST_CAPS, CapError, ProviderStatus, build_heartbeat, cap_consumer, cap_filter,
    cap_subject, cap_token, detect_providers, detected_caps, effective_caps, load_quota_hints, validate_grant,
)
from cluster.registry import (  # noqa: E402
    Registry, RegistryError, decode_join_code, encode_join_code, guest_permissions, member_permissions,
    render_conf,
)
from cluster.routing import ROUTES, llm_argv, pick_capability, subject_for, validate_cap_spec  # noqa: E402
from cluster.sandbox import sandbox_argv  # noqa: E402
from cluster.spec import SpecError  # noqa: E402

URL = "nats://100.64.0.1:4222"


def base(cap, **kw):
    s = {"v": 2, "id": "j1", "cap": cap, "repo": "rupret007/bob-ops-dashboard", "ref": "main",
         "timeout_s": 60, "load_ceiling": 8}
    s.update(kw)
    return s


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class CapabilityTests(unittest.TestCase):
    def test_tokens_consumers_filters_never_overlap(self):
        filters = {cap_filter(c) for c in CAPABILITIES}
        self.assertEqual(len(filters), len(CAPABILITIES))
        self.assertEqual(cap_token("llm.codex.high"), "llm_codex_high")
        self.assertEqual(cap_consumer("llm.codex"), "cap_llm_codex")
        self.assertEqual(cap_subject("llm.codex", "a1"), "cap.llm_codex.a1")
        for f in filters:  # one token for the cap, one wildcard for the id
            self.assertEqual(len(f.split(".")), 3)
        with self.assertRaises(CapError):
            cap_token("llm.unknown")

    def test_guest_grants_are_sandbox_only(self):
        self.assertEqual(GUEST_CAPS, {"sandbox.test"})
        with self.assertRaises(CapError):
            validate_grant("guest", ["llm.codex.high"])
        with self.assertRaises(CapError):
            validate_grant("guest", ["shell.build"])
        self.assertEqual(validate_grant("member", ["shell.build", "llm.claude", "shell.build"]),
                         ["llm.claude", "shell.build"])
        self.assertEqual(effective_caps("guest", CAPABILITIES, CAPABILITIES), ["sandbox.test"])

    def test_effective_is_granted_and_detected(self):
        self.assertEqual(effective_caps("member", ["llm.codex.high", "llm.claude"], {"llm.codex.high"}),
                         ["llm.codex.high"])

    def test_detection_uses_status_commands_and_presence_only(self):
        calls = []

        def which(n):
            return {"codex": "/x/codex", "cursor-agent": "/x/ca", "claude": "/x/claude", "git": "/x/git",
                    "python3": "/x/py"}.get(n)

        def run(argv):
            calls.append(argv)
            if argv[1:] == ["login", "status"]:
                return 0, "Logged in using ChatGPT"
            if argv[1:] == ["status"]:
                return 0, "Not logged in"
            if argv[1:] == ["auth", "status"]:
                return 0, '{"loggedIn": true, "authMethod": "claude.ai"}'
            return 1, ""
        with tempfile.TemporaryDirectory() as h:
            st = detect_providers(which=which, run=run, env={"MINIMAX_API_KEY": "x"}, home=Path(h))
        self.assertTrue(st["codex"].logged_in)
        self.assertFalse(st["cursor"].logged_in)
        self.assertTrue(st["claude"].logged_in)
        self.assertFalse(st["gemini"].installed)
        self.assertTrue(st["minimax"].logged_in)
        caps = detected_caps(st, which=which)
        self.assertIn("llm.codex.high", caps)
        self.assertNotIn("llm.composer", caps)
        self.assertIn("shell.build", caps)
        self.assertNotIn("sandbox.test", caps)
        self.assertTrue(all(c[0].startswith("/x/") for c in calls))

    def test_heartbeat_shape_and_guest_hides_llm(self):
        prov = {"codex": ProviderStatus(True, True)}
        hb = build_heartbeat("macbook", "member", prov, ["llm.codex.high"], 3, 1,
                             quota={"codex": {"remaining_pct": 40.0}}, now=5.0)
        self.assertEqual(hb["v"], "caps/v1")
        self.assertEqual(hb["llm"]["codex"], {"installed": True, "logged_in": True, "quota": {"remaining_pct": 40.0}})
        self.assertEqual(hb["max_lanes"], 3)
        g = build_heartbeat("friend", "guest", prov, ["llm.codex.high", "sandbox.test"], 1, 0)
        self.assertEqual(g["llm"], {})
        self.assertEqual(g["caps"], ["sandbox.test"])
        json.dumps(hb)

    def test_quota_hints_are_filtered(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.json"
            p.write_text(json.dumps({"codex": {"remaining_pct": 140, "token": "sk-abcdefghijklmnopqrstuvwxyz"},
                                     "evil": {"remaining_pct": 5}, "claude": {"reset_ct": "9 PM"}}))
            q = load_quota_hints(p)
        self.assertEqual(q, {"codex": {"remaining_pct": 100.0}, "claude": {"reset_ct": "9 PM"}})


class RoutingTests(unittest.TestCase):
    def test_jeffs_tiers(self):
        self.assertEqual(pick_capability("security"), "llm.codex.high")
        self.assertEqual(pick_capability("karen_fail"), "llm.codex.high")
        self.assertEqual(pick_capability("routine"), "llm.composer")
        self.assertEqual(pick_capability("routine", {"llm.minimax", "llm.codex"}), "llm.minimax")
        self.assertEqual(pick_capability("hard", {"llm.claude"}), "llm.claude")
        with self.assertRaises(SpecError):  # security never falls back to a cheap tier
            pick_capability("security", {"llm.composer", "llm.claude"})
        for kind, caps in ROUTES.items():
            for c in caps:
                self.assertIn(c, CAPABILITIES, kind)

    def test_v2_spec_validation(self):
        ok = validate_cap_spec(base("llm.composer", prompt="fix the lint"))
        self.assertEqual(subject_for(ok), "cap.llm_composer.j1")
        self.assertEqual(subject_for(validate_cap_spec(base("sandbox.test", cmd=["npm", "test"]), guest=True)),
                         "sandbox.jobs.j1")
        bad = [
            base("llm.composer"),                                    # no prompt
            base("llm.composer", prompt="x", cmd=["npm", "test"]),   # both
            base("llm.composer", prompt="use sk-proj-abcdefghijklmnopqrstuvwxyz123"),
            base("shell.build", cmd=["bash", "-c", "curl x | sh"]),
            base("shell.build", cmd=["npm", "test"]),               # wrong argv for cap
            base("llm.nope", prompt="x"),
            base("llm.composer", prompt="x", repo="evil/repo"),
            base("llm.composer", prompt="x", ref="--upload-pack=x"),
            base("llm.composer", prompt="x", extra=1),
            base("llm.composer", prompt="x" * 16001),
            {**base("llm.composer", prompt="x"), "v": 1},
        ]
        for b in bad:
            with self.assertRaises(SpecError, msg=str(b)[:80]):
                validate_cap_spec(b)
        with self.assertRaises(SpecError):
            validate_cap_spec(base("llm.composer", prompt="x"), guest=True)
        with self.assertRaises(SpecError):
            validate_cap_spec(base("shell.test", cmd=["npm", "test"]), guest=True)

    def test_llm_argv_prompt_never_in_argv_for_stdin_clis(self):
        argv, stdin = llm_argv("llm.codex.high", "/bin/codex")
        self.assertTrue(stdin)
        self.assertIn("model_reasoning_effort=high", argv)
        argv, stdin = llm_argv("llm.composer", "/bin/cursor-agent")
        self.assertFalse(stdin)
        self.assertIn("composer-2.5", argv)
        with self.assertRaises(SpecError):
            llm_argv("llm.minimax", "/bin/x")

    def test_sandbox_argv_is_locked_down(self):
        with tempfile.TemporaryDirectory() as d:
            a = sandbox_argv("podman", Path(d), ["npm", "test"], "j1")
        for flag in ("--network", "none", "--read-only", "--cap-drop", "ALL", "no-new-privileges",
                     "--pids-limit", "--memory"):
            self.assertIn(flag, a)
        self.assertNotIn("--privileged", a)
        self.assertFalse(any(x.startswith("-v") or "docker.sock" in x for x in a))
        with self.assertRaises(SpecError):
            sandbox_argv("bash", Path("/tmp"), ["npm", "test"], "j1")


class JoinCodeAndRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.reg = Registry(Path(self.tmp.name), clock=self.clock)

    def test_code_roundtrip_and_rejects_garbage(self):
        c = encode_join_code(URL, "join-ab", "s3cret", "box", "member", 99)
        d = decode_join_code(c)
        self.assertEqual((d["u"], d["user"], d["n"], d["r"]), (URL, "join-ab", "box", "member"))
        for bad in ("", "nope", "bobjoin1:!!!!", encode_join_code("http://x", "u", "s", "box", "member", 1),
                    encode_join_code(URL, "u", "s", "Bad Name", "member", 1),
                    encode_join_code(URL, "u", "s", "box", "admin", 1)):
            with self.assertRaises(RegistryError):
                decode_join_code(bad)

    def test_mint_redeem_single_use(self):
        code, info = self.reg.mint("macbook2", "member", ["llm.codex.high"], URL)
        d = decode_join_code(code)
        raw = self.reg.path.read_text()
        self.assertNotIn(d["s"], raw)  # only the bcrypt hash is stored
        out = self.reg.redeem(d["user"], d["s"], "macbook2")
        self.assertEqual(out["account"], "BOB")
        self.assertNotIn(out["token"], self.reg.path.read_text())
        with self.assertRaises(RegistryError):
            self.reg.redeem(d["user"], d["s"], "macbook2")
        self.assertEqual(stat.S_IMODE(self.reg.path.stat().st_mode), 0o600)

    def test_wrong_secret_wrong_name_and_expiry(self):
        code, _ = self.reg.mint("box", "member", None, URL, ttl_s=120)
        d = decode_join_code(code)
        with self.assertRaises(RegistryError):
            self.reg.redeem(d["user"], d["s"] + "x", "box")
        with self.assertRaises(RegistryError):
            self.reg.redeem(d["user"], d["s"], "other")
        self.clock.t += 121
        with self.assertRaises(RegistryError):
            self.reg.redeem(d["user"], d["s"], "box")
        self.assertEqual(self.reg.expire(), 1)
        self.assertEqual(self.reg.read()["pending"], {})

    def test_mint_validation(self):
        for name in ("mini", "nucbox", "join-x", "A", "x" * 40, "has space"):
            with self.assertRaises(RegistryError, msg=name):
                self.reg.mint(name, "member", None, URL)
        with self.assertRaises(RegistryError):
            self.reg.mint("box", "admin", None, URL)
        with self.assertRaises(RegistryError):
            self.reg.mint("box", "guest", ["llm.claude"], URL)
        with self.assertRaises(RegistryError):
            self.reg.mint("box", "member", None, URL, ttl_s=10)
        self.reg.mint("box", "member", None, URL)
        with self.assertRaises(RegistryError):  # one outstanding code per name
            self.reg.mint("box", "member", None, URL)

    def test_revoke_removes_hash_and_render_skips_revoked(self):
        code, _ = self.reg.mint("box", "member", ["shell.build"], URL)
        d = decode_join_code(code)
        self.reg.redeem(d["user"], d["s"], "box")
        self.assertIn('user: "box"', render_conf(self.reg.read(), "/tmp/js"))
        self.assertTrue(self.reg.revoke("box"))
        self.assertNotIn("pw", self.reg.read()["nodes"]["box"])
        self.assertNotIn('user: "box"', render_conf(self.reg.read(), "/tmp/js"))
        # Each guard stands alone: a revoked node that somehow kept a hash is still skipped.
        data = self.reg.read()
        data["nodes"]["box"]["pw"] = "$2b$04$" + "a" * 53
        self.assertNotIn('user: "box"', render_conf(data, "/tmp/js"))
        self.assertFalse(self.reg.revoke("box"))
        # A revoked name can be re-minted later.
        self.reg.mint("box", "member", None, URL)

    def test_revoke_kills_outstanding_code(self):
        self.reg.mint("box", "guest", None, URL)
        self.assertTrue(self.reg.revoke("box"))
        self.assertEqual(self.reg.read()["pending"], {})

    def test_permissions_are_minimal(self):
        m = member_permissions("box", ["llm.composer", "sandbox.test"])
        self.assertIn("$JS.API.CONSUMER.MSG.NEXT.CAPJOBS.cap_llm_composer", m["publish"])
        self.assertFalse(any("cap_llm_codex" in s for s in m["publish"]))
        self.assertFalse(any(s in ("cap.>", "jobs.>", ">", "$JS.API.>") for s in m["publish"]))
        self.assertEqual(m["subscribe"], ["_INBOX_box.>"])
        g = guest_permissions("friend")
        self.assertTrue(all(s.startswith(("sandbox.", "$JS.API.CONSUMER.INFO.SANDBOX.guests",
                                          "$JS.API.CONSUMER.MSG.NEXT.SANDBOX.guests", "$JS.ACK.SANDBOX.guests."))
                            for s in g["publish"]))
        self.assertEqual(g["subscribe"], ["_INBOX_friend.>"])

    def test_render_has_no_plaintext_and_validates_names(self):
        tok = Path(self.tmp.name) / "tokens.env"
        tok.write_text("NUCBOX_TOKEN=plain-nucbox-token\nDASHBOARD_TOKEN=plain-dash\n")
        self.reg.ensure_service_users()
        self.reg.import_legacy(tok)
        conf = render_conf(self.reg.read(), "/tmp/js")
        self.assertNotIn("plain-nucbox-token", conf)
        svc = (Path(self.tmp.name) / "hub-service.env").read_text()
        for line in svc.splitlines():
            self.assertNotIn(line.split("=", 1)[1], conf)
        self.assertIn("BOB: {", conf)
        self.assertIn("GUEST: {", conf)
        self.assertEqual(stat.S_IMODE((Path(self.tmp.name) / "hub-service.env").stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

"""Hub-side node registry, join codes, revocation and accounts nats.conf rendering.

Lives only on the NucBox (operator host). Node tokens are stored as bcrypt
hashes; nats-server verifies bcrypt passwords natively. Plaintext exists only
for the hub's own service users (joind, guestops), in a 0600 file that never
leaves the hub, and in the single reply that hands a node its own token.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

from cluster.capabilities import (
    NODE_NAME_PATTERN, CapError, allowed_caps_for_role, cap_consumer, validate_grant,
)

REGISTRY_VERSION = 1
JOIN_PREFIX = "bobjoin1:"
DEFAULT_TTL_S = 30 * 60
MAX_TTL_S = 24 * 3600
RESERVED_NAMES = frozenset({"mini", "nucbox", "macbook", "dashboard", "joind", "guestops", "sys", "operator"})
LEGACY_USERS = ("mini", "nucbox", "macbook", "dashboard")
PENDING_PREFIX = "join-"
ROLES = ("member", "guest")
STREAM_CAPJOBS = "CAPJOBS"
STREAM_SANDBOX = "SANDBOX"
STREAM_SANDBOX_RESULTS = "SANDBOX_RESULTS"
CONSUMER_GUESTS = "guests"


class RegistryError(ValueError):
    pass


def _bcrypt():
    try:
        import bcrypt  # type: ignore
    except ImportError as e:  # pragma: no cover - dependency documented
        raise RegistryError("python bcrypt is required on the hub (pip install bcrypt)") from e
    return bcrypt


def _rounds() -> int:
    try:
        return max(4, min(14, int(os.environ.get("BOB_BCRYPT_ROUNDS", "11"))))
    except ValueError:
        return 11


def hash_secret(secret: str, rounds: int | None = None) -> str:
    b = _bcrypt()
    return b.hashpw(secret.encode(), b.gensalt(rounds=rounds or _rounds())).decode()


def check_secret(secret: str, hashed: str) -> bool:
    if not isinstance(secret, str) or not isinstance(hashed, str) or not hashed.startswith("$2"):
        return False
    try:
        return _bcrypt().checkpw(secret.encode(), hashed.encode())
    except ValueError:
        return False


def inbox_prefix(user: str) -> str:
    return f"_INBOX_{user}"


# ------------------------------------------------------------ join codes

def encode_join_code(url: str, user: str, secret: str, name: str, role: str, expires: float) -> str:
    body = json.dumps({"u": url, "user": user, "s": secret, "n": name, "r": role, "x": int(expires)},
                      separators=(",", ":")).encode()
    return JOIN_PREFIX + base64.urlsafe_b64encode(body).decode().rstrip("=")


def decode_join_code(code: str) -> dict[str, Any]:
    if not isinstance(code, str) or not code.startswith(JOIN_PREFIX):
        raise RegistryError("not a bob join code")
    raw = code[len(JOIN_PREFIX):].strip()
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError) as e:
        raise RegistryError("corrupt join code") from e
    if not isinstance(data, dict) or not {"u", "user", "s", "n", "r", "x"} <= set(data):
        raise RegistryError("incomplete join code")
    if not str(data["u"]).startswith(("nats://", "tls://")):
        raise RegistryError("join code url must be nats:// or tls://")
    if data["r"] not in ROLES or not NODE_NAME_PATTERN.fullmatch(str(data["n"])):
        raise RegistryError("join code role/name invalid")
    return data


# --------------------------------------------------------------- registry

def _empty() -> dict[str, Any]:
    return {"v": REGISTRY_VERSION, "nodes": {}, "pending": {}, "service": {}}


class Registry:
    """JSON registry guarded by an flock; every write is atomic and 0600."""

    def __init__(self, config_dir: Path, clock=time.time):
        self.dir = Path(config_dir)
        self.path = self.dir / "registry.json"
        self.secrets_path = self.dir / "hub-service.env"
        self.clock = clock

    @contextlib.contextmanager
    def locked(self) -> Iterator[dict[str, Any]]:
        import fcntl

        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.dir / ".registry.lock", "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            data = self._load()
            yield data
            self._save(data)

    def read(self) -> dict[str, Any]:
        return self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("v") != REGISTRY_VERSION:
            raise RegistryError("unsupported registry version")
        for k in ("nodes", "pending", "service"):
            data.setdefault(k, {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        _atomic_write(self.path, json.dumps(data, indent=1, sort_keys=True) + "\n")

    # service users (joind in JOIN, guestops in GUEST): plaintext stays on hub
    def service_password(self, user: str) -> str:
        env = _read_env(self.secrets_path)
        key = f"{user.upper()}_PASSWORD"
        if key in env:
            return env[key]
        pw = secrets.token_urlsafe(32)
        env[key] = pw
        _atomic_write(self.secrets_path, "".join(f"{k}={v}\n" for k, v in sorted(env.items())))
        with self.locked() as data:
            data["service"][user] = hash_secret(pw)
        return pw

    def ensure_service_users(self) -> None:
        for u in ("joind", "guestops"):
            self.service_password(u)
        # Keep bcrypt in sync if the registry was recreated.
        env = _read_env(self.secrets_path)
        with self.locked() as data:
            for u in ("joind", "guestops"):
                h = data["service"].get(u)
                if not h or not check_secret(env[f"{u.upper()}_PASSWORD"], h):
                    data["service"][u] = hash_secret(env[f"{u.upper()}_PASSWORD"])

    def import_legacy(self, tokens_env: Path) -> list[str]:
        """Hash the four existing tokens.env users into the registry (no plaintext kept)."""
        env = _read_env(tokens_env)
        added = []
        with self.locked() as data:
            legacy = data.setdefault("legacy", {})
            for u in LEGACY_USERS:
                tok = env.get(f"{u.upper()}_TOKEN")
                if tok and not (legacy.get(u) and check_secret(tok, legacy[u])):
                    legacy[u] = hash_secret(tok)
                    added.append(u)
        return added

    # ---- mint / redeem / revoke
    def mint(self, name: str, role: str, caps: list[str] | None, url: str,
             ttl_s: int = DEFAULT_TTL_S) -> tuple[str, dict[str, Any]]:
        if role not in ROLES:
            raise RegistryError("role must be member or guest")
        if not isinstance(name, str) or not NODE_NAME_PATTERN.fullmatch(name) or name in RESERVED_NAMES \
                or name.startswith(PENDING_PREFIX):
            raise RegistryError("name must match [a-z][a-z0-9-]{1,30} and not be reserved")
        if not 60 <= int(ttl_s) <= MAX_TTL_S:
            raise RegistryError("ttl must be 60s..24h")
        if caps is None:
            caps = sorted(allowed_caps_for_role(role))
        try:
            caps = validate_grant(role, caps)
        except CapError as e:
            raise RegistryError(str(e)) from e
        now = self.clock()
        user = PENDING_PREFIX + secrets.token_hex(4)
        secret = secrets.token_urlsafe(24)
        expires = now + int(ttl_s)
        with self.locked() as data:
            self._expire(data, now)
            existing = data["nodes"].get(name)
            if existing and not existing.get("revoked"):
                raise RegistryError(f"node {name} already exists; revoke it first")
            if any(p["name"] == name for p in data["pending"].values()):
                raise RegistryError(f"a join code for {name} is already outstanding")
            data["pending"][user] = {"name": name, "role": role, "caps": caps,
                                     "pw": hash_secret(secret), "expires": expires, "created": now}
        return encode_join_code(url, user, secret, name, role, expires), {
            "user": user, "name": name, "role": role, "caps": caps, "expires": expires}

    def redeem(self, user: str, secret: str, name: str) -> dict[str, Any]:
        now = self.clock()
        with self.locked() as data:
            self._expire(data, now)
            p = data["pending"].get(user)
            # Same error for unknown, expired, used, wrong secret or wrong name.
            if not p or p["name"] != name or not check_secret(secret, p["pw"]):
                raise RegistryError("join code is invalid, expired or already used")
            del data["pending"][user]
            token = secrets.token_urlsafe(32)
            data["nodes"][name] = {"role": p["role"], "caps": p["caps"], "pw": hash_secret(token),
                                   "created": now, "revoked": False, "owner": "jeff" if p["role"] == "member" else "guest"}
        return {"user": name, "token": token, "role": p["role"], "caps": p["caps"],
                "account": "BOB" if p["role"] == "member" else "GUEST"}

    def revoke(self, name: str) -> bool:
        with self.locked() as data:
            hit = False
            n = data["nodes"].get(name)
            if n and not n.get("revoked"):
                n["revoked"] = True
                n["revoked_at"] = self.clock()
                n.pop("pw", None)
                hit = True
            for u in [u for u, p in data["pending"].items() if p["name"] == name]:
                del data["pending"][u]
                hit = True
        return hit

    def set_caps(self, name: str, caps: list[str]) -> list[str]:
        with self.locked() as data:
            n = data["nodes"].get(name)
            if not n or n.get("revoked"):
                raise RegistryError("no such active node")
            try:
                n["caps"] = validate_grant(n["role"], caps)
            except CapError as e:
                raise RegistryError(str(e)) from e
            return n["caps"]

    def expire(self) -> int:
        with self.locked() as data:
            return self._expire(data, self.clock())

    @staticmethod
    def _expire(data: dict[str, Any], now: float) -> int:
        dead = [u for u, p in data["pending"].items() if p["expires"] <= now]
        for u in dead:
            del data["pending"][u]
        return len(dead)


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return out


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ------------------------------------------------------------ permissions

def _q(items: list[str]) -> str:
    for s in items:
        if not re.fullmatch(r"[A-Za-z0-9_.>*$-]+", s):
            raise RegistryError(f"unsafe subject {s!r}")
    return "[" + ", ".join(json.dumps(s) for s in items) + "]"


def member_permissions(name: str, caps: list[str]) -> dict[str, Any]:
    pub = [f"caps.{name}", f"metrics.{name}", "results.>",
           "$JS.API.CONSUMER.INFO.JOBS.workers", "$JS.API.CONSUMER.MSG.NEXT.JOBS.workers",
           "$JS.ACK.JOBS.workers.>"]
    for c in caps:
        if c.startswith("sandbox."):
            continue
        cons = cap_consumer(c)
        pub += [f"$JS.API.CONSUMER.INFO.{STREAM_CAPJOBS}.{cons}",
                f"$JS.API.CONSUMER.MSG.NEXT.{STREAM_CAPJOBS}.{cons}",
                f"$JS.ACK.{STREAM_CAPJOBS}.{cons}.>"]
    return {"publish": pub, "subscribe": [f"{inbox_prefix(name)}.>"]}


def guest_permissions(name: str) -> dict[str, Any]:
    return {
        "publish": [f"sandbox.caps.{name}", f"sandbox.results.{name}.>",
                    f"$JS.API.CONSUMER.INFO.{STREAM_SANDBOX}.{CONSUMER_GUESTS}",
                    f"$JS.API.CONSUMER.MSG.NEXT.{STREAM_SANDBOX}.{CONSUMER_GUESTS}",
                    f"$JS.ACK.{STREAM_SANDBOX}.{CONSUMER_GUESTS}.>"],
        "subscribe": [f"{inbox_prefix(name)}.>"],
    }


LEGACY_PERMS = {
    "mini": {"publish": ["metrics.mini", "results.>", "$JS.API.CONSUMER.INFO.JOBS.workers",
                         "$JS.API.CONSUMER.MSG.NEXT.JOBS.workers", "$JS.ACK.JOBS.workers.>"],
             "subscribe": ["$JS.API.>", "$JS.ACK.>", "$JS.FC.>", "_INBOX.>"]},
    "macbook": {"publish": ["metrics.macbook", "results.>", "$JS.API.CONSUMER.INFO.JOBS.workers",
                            "$JS.API.CONSUMER.MSG.NEXT.JOBS.workers", "$JS.ACK.JOBS.workers.>"],
                "subscribe": ["$JS.API.>", "$JS.ACK.>", "$JS.FC.>", "_INBOX.>"]},
    # Operator: everything it had, plus enqueueing capability jobs and reading caps.
    "nucbox": {"publish": ["metrics.nucbox", "results.>", "jobs.>", "cap.>", "$JS.API.>", "$JS.ACK.JOBS.workers.>",
                           "$JS.ACK.CAPJOBS.>"],
               "subscribe": ["$JS.API.>", "$JS.ACK.>", "$JS.FC.>", "_INBOX.>", "results.>", "caps.>",
                             "sandbox.caps.>", "sandbox.results.>"]},
    "dashboard": {"publish": [],
                  "subscribe": ["metrics.>", "results.>", "caps.>", "sandbox.caps.>", "sandbox.results.>"]},
}


def _user_block(user: str, pw: str, perms: dict[str, Any] | None, allow_responses: bool = False) -> str:
    if '"' in pw or "\\" in pw or "\n" in pw:
        raise RegistryError("password contains unsafe characters")
    lines = [f"      {{ user: {json.dumps(user)}, password: {json.dumps(pw)}"]
    if perms is not None:
        p = [f"publish: {_q(perms['publish'])}" if perms["publish"] else "publish: { deny: [\">\"] }",
             f"subscribe: {_q(perms['subscribe'])}" if perms["subscribe"] else "subscribe: { deny: [\">\"] }"]
        if allow_responses:
            p.append("allow_responses: { max: 1, expires: \"30s\" }")
        lines.append("        permissions: { " + ", ".join(p) + " }")
    lines.append("      }")
    return "\n".join(lines)


def render_conf(reg: dict[str, Any], store_dir: str, port: int = 4222, http: str = "127.0.0.1:8232",
                host: str = "127.0.0.1") -> str:
    """Render the accounts-based nats.conf. Only bcrypt hashes are written."""
    bob, guest, join = [], [], []
    for u, h in sorted((reg.get("legacy") or {}).items()):
        if u in LEGACY_PERMS:
            bob.append(_user_block(u, h, LEGACY_PERMS[u]))
    for name, n in sorted(reg["nodes"].items()):
        if n.get("revoked") or not n.get("pw"):
            continue
        if n["role"] == "member":
            bob.append(_user_block(name, n["pw"], member_permissions(name, n["caps"])))
        elif n["role"] == "guest":
            guest.append(_user_block(name, n["pw"], guest_permissions(name)))
    svc = reg.get("service") or {}
    if svc.get("guestops"):
        guest.append(_user_block("guestops", svc["guestops"], {
            "publish": ["sandbox.jobs.>", "$JS.API.>"],
            "subscribe": ["_INBOX.>", "sandbox.results.>", "sandbox.caps.>"]}))
    if svc.get("joind"):
        join.append(_user_block("joind", svc["joind"], {"publish": [], "subscribe": ["join.redeem"]},
                                allow_responses=True))
    for user, p in sorted(reg["pending"].items()):
        join.append(_user_block(user, p["pw"], {"publish": ["join.redeem"], "subscribe": [f"{inbox_prefix(user)}.>"]}))

    def users(blocks: list[str]) -> str:
        return "[\n" + "\n".join(blocks) + "\n    ]" if blocks else "[]"

    return f"""# Bob cluster mesh NATS config, rendered by cluster/bobctl.py. Do not edit by hand.
# Only bcrypt hashes appear here. Accounts: BOB (Jeff's nodes) | GUEST (sandbox) | JOIN (codes).
port: {port}
host: {json.dumps(host)}
http: {json.dumps(http)}

jetstream {{
  store_dir: {json.dumps(store_dir)}
  max_mem_store: 268435456
  max_file_store: 2147483648
}}

accounts {{
  BOB: {{
    jetstream: enabled
    users: {users(bob)}
    imports: [
      {{ stream: {{ account: GUEST, subject: "sandbox.caps.>" }} }}
      {{ stream: {{ account: GUEST, subject: "sandbox.results.>" }} }}
    ]
  }}
  GUEST: {{
    jetstream: enabled
    users: {users(guest)}
    exports: [
      {{ stream: "sandbox.caps.>", accounts: [BOB] }}
      {{ stream: "sandbox.results.>", accounts: [BOB] }}
    ]
  }}
  JOIN: {{
    users: {users(join)}
  }}
}}
"""

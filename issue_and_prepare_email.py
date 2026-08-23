#!/usr/bin/env python3
"""Issue OTP and print JSON email payload with real code interpolated."""
from __future__ import annotations
import hashlib, json, secrets, time
from pathlib import Path
EMAIL = "jeffstory007@gmail.com"
TTL_MS = 15 * 60 * 1000
ROOT = Path(__file__).resolve().parent
DASH = "https://rupret007.github.io/bob-ops-dashboard/"

def main() -> int:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = int(time.time() * 1000)
    digest = hashlib.sha256(f"{code}:{EMAIL}".encode()).hexdigest()
    path = ROOT / "status.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data["verify"] = {"email": EMAIL, "sha256": digest, "exp": now + TTL_MS, "issued_at": now}
    path.write_text(json.dumps(data, indent=2) + "\n")
    body = (
        f"Jeff — your Bob Ops Dashboard unlock code is:\n\n{code}\n\n"
        f"Enter it at {DASH}\nExpires in about 15 minutes.\n\n— Bob\n"
    )
    assert code in body and "{{" not in body
    out = {"email": EMAIL, "code": code, "subject": "Bob Ops dashboard unlock code", "body": body, "dashboard": DASH, "exp": now + TTL_MS}
    Path("/tmp/dashboard-otp.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Issue a one-time dashboard unlock code for Jeff. Prints code on stdout."""
from __future__ import annotations
import hashlib, json, secrets, sys, time
from pathlib import Path
from preserve_verify import JEFF_EMAIL as EMAIL, TTL_MS
ROOT = Path(__file__).resolve().parent

def main() -> int:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = int(time.time() * 1000)
    digest = hashlib.sha256(f"{code}:{EMAIL}".encode()).hexdigest()
    path = ROOT / "status.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data["verify"] = {"email": EMAIL, "sha256": digest, "exp": now + TTL_MS, "issued_at": now}
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(code)
    print(f"email={EMAIL}", file=sys.stderr)
    return 0
if __name__ == "__main__":
    raise SystemExit(main())

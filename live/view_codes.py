"""Private, process-locked storage for single-use browser credentials."""
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import time
from contextlib import contextmanager

DEFAULT_TTL = 300
MAX_TTL = 3600


def view_codes_path():
    return Path(os.environ.get("LIVE_VIEW_CODES_FILE", "~/.config/bob-live/view-codes.json")).expanduser()


@contextmanager
def _locked_codes(path, *, create=False):
    path = Path(path).expanduser()
    root = Path(__file__).resolve().parent.parent
    if path.resolve() == root or root in path.resolve().parents:
        raise ValueError("view codes must be outside the repository")
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError("view codes must be owned by you with chmod 600")
        # Lock the same inode for both CLI and HTTP processes. Never replace it.
        fcntl.flock(stream, fcntl.LOCK_EX)
        raw = stream.read()
        codes = json.loads(raw) if raw else {}
        if not isinstance(codes, dict) or any(
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            or type(expiry) is not int
            for digest, expiry in codes.items()
        ):
            raise ValueError("invalid view code store")
        yield codes
        stream.seek(0)
        json.dump(codes, stream)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def mint_view_code(ttl=DEFAULT_TTL, *, path=None, now=None):
    if type(ttl) is not int or not 1 <= ttl <= MAX_TTL:
        raise ValueError("view code TTL must be between 1 and 3600 seconds")
    now = time.time() if now is None else now
    code = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code.encode()).hexdigest()
    with _locked_codes(view_codes_path() if path is None else path, create=True) as codes:
        for expired in [key for key, expiry in codes.items() if expiry <= now]:
            del codes[expired]
        codes[digest] = int(now) + ttl
    return code


def redeem_view_code(code, *, path=None, now=None, clock=time.time):
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    accepted = False
    try:
        with _locked_codes(view_codes_path() if path is None else path) as codes:
            # A concurrent writer may hold the lock past this code's expiry.
            current = clock() if now is None else now
            for stored, expiry in list(codes.items()):
                matches = hmac.compare_digest(digest, stored)
                if matches or expiry <= current:
                    del codes[stored]
                if matches and current < expiry:
                    accepted = True
    except (OSError, ValueError):
        # Missing, unsafe, corrupt, or unwritable stores must fail closed.
        return False
    return accepted

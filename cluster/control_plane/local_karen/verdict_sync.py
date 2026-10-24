"""Share local Karen verdicts from the Mini with the NucBox over Tailscale.

The Mini runs the Karen gate, so its verdict directory is the only trusted
source of verdict artifacts. The NucBox dispatcher needs those artifacts to
authorize repair lanes. The Mini serves them read-only on its Tailscale
address (never on LAN or public interfaces); the NucBox pulls them. Public PR
comments and labels are never used as a source.

Every pulled entry is re-validated: it must parse as a Verdict, carry a full
40-hex head SHA, and its filename must match its own contents, so a
malformed or mislabeled artifact can never authorize work for another PR/SHA.
Pulls only add or refresh files; they never delete local verdicts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

from .verdict import Verdict, _verdict_filename

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8791
SHARE_PATH = "/karen/verdicts.json"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+_[0-9]+_[0-9a-f]{12}_(KARENPASS|KARENFAIL)\.json$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_BYTES = 8 * 1024 * 1024


def validate_entry(name, text):
    # type: (str, str) -> Optional[Verdict]
    """Return the Verdict if name/content are a consistent artifact, else None."""
    if not isinstance(name, str) or not isinstance(text, str) or not NAME_RE.match(name):
        return None
    try:
        verdict = Verdict.from_json(text)
    except (ValueError, TypeError, KeyError):
        return None
    if not isinstance(verdict.head_sha, str) or not SHA_RE.match(verdict.head_sha):
        return None
    if not isinstance(verdict.pr_number, int) or isinstance(verdict.pr_number, bool):
        return None
    if not isinstance(verdict.passed, bool):
        return None
    kind = "KARENPASS" if verdict.passed else "KARENFAIL"
    expected = "{}_{}.json".format(
        _verdict_filename(verdict.owner, verdict.repo, verdict.pr_number, verdict.head_sha), kind)
    return verdict if expected == name else None


def collect_verdicts(verdict_dir):
    # type: (Path) -> Dict[str, str]
    """Valid verdict artifacts in verdict_dir (regular files only, no symlinks)."""
    out = {}  # type: Dict[str, str]
    if not verdict_dir.is_dir():
        return out
    for path in sorted(verdict_dir.iterdir()):
        if path.is_symlink() or not path.is_file() or not NAME_RE.match(path.name):
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        if validate_entry(path.name, text) is not None:
            out[path.name] = text
    return out


def _handler_for(verdict_dir):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != SHARE_PATH:
                self.send_error(404)
                return
            body = json.dumps({"verdicts": collect_verdicts(verdict_dir)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            logger.debug("verdict share: " + fmt, *args)

    return Handler


def start_share_thread(verdict_dir=None, bind=None, port=None):
    # type: (Optional[Path], Optional[str], Optional[int]) -> Optional[ThreadingHTTPServer]
    """Serve verdicts read-only on the Tailscale address, if this host owns it."""
    if os.environ.get("STORYPOOL_VERDICT_SHARE", "1") == "0":
        return None
    from ..peer_endpoints import share_address, PeerUnavailable
    try:
        bind = bind or os.environ.get("STORYPOOL_VERDICT_SHARE_BIND") or share_address()
    except PeerUnavailable:
        logger.warning("Verdict share discovery unavailable; configure private bind address")
        return None
    if not bind or bind in ("0.0.0.0", "::"):
        logger.warning("Refusing to share verdicts on wildcard address %r", bind)
        return None
    port = int(port if port is not None else os.environ.get("STORYPOOL_VERDICT_SHARE_PORT", DEFAULT_PORT))
    if verdict_dir is None:
        from .config import KarenConfig
        verdict_dir = KarenConfig.load().get_verdict_dir()
    try:
        server = ThreadingHTTPServer((bind, port), _handler_for(Path(verdict_dir)))
    except OSError as exc:
        logger.info("Verdict share not started on %s:%s (%s)", bind, port, exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="verdict-share", daemon=True)
    thread.start()
    logger.info("Sharing Karen verdicts read-only on http://%s:%s%s", bind, port, SHARE_PATH)
    return server


def pull(source=None, verdict_dir=None, timeout=10):
    # type: (Optional[str], Optional[Path], int) -> int
    """Fetch valid verdicts without replacing conflicting local evidence."""
    source = source or os.environ.get("STORYPOOL_VERDICT_SOURCE")
    if not source:
        from ..peer_endpoints import peer_node
        from ..node_control import allowed_address
        addresses = peer_node('mini').get('TailscaleIPs', [])
        address = next((ip for ip in addresses if allowed_address(ip) and ':' not in ip), None)
        if not address:
            raise ValueError('Mini verdict source unavailable; configure STORYPOOL_VERDICT_SOURCE')
        source = 'http://{}:{}{}'.format(address, DEFAULT_PORT, SHARE_PATH)
    if verdict_dir is None:
        from .config import KarenConfig
        verdict_dir = KarenConfig.load().get_verdict_dir()
    verdict_dir = Path(verdict_dir)
    with urllib.request.urlopen(source, timeout=timeout) as resp:  # nosec - tailnet only
        raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("verdict share response too large")
    entries = json.loads(raw.decode()).get("verdicts", {})
    if not isinstance(entries, dict):
        raise ValueError("verdict share response malformed")
    verdict_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, text in entries.items():
        if validate_entry(name, text) is None:
            logger.warning("Rejected invalid shared verdict %r", name)
            continue
        dest = verdict_dir / name
        if dest.is_symlink():
            continue
        if dest.is_file():
            try:
                if dest.read_text() == text:
                    continue
            except OSError:
                logger.warning("Existing shared verdict unreadable; preserving %r", name)
                continue
            logger.warning("Shared verdict conflicts with local evidence; preserving %r", name)
            continue
        fd, tmp = tempfile.mkstemp(dir=str(verdict_dir), prefix=".sync-")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
            try:
                os.link(tmp, str(dest))
            except FileExistsError:
                logger.warning("Verdict published during sync; preserving %r", name)
                continue
            written += 1
        finally:
            os.unlink(tmp)
    return written


def main():
    # type: () -> int
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Share/pull local Karen verdicts over Tailscale")
    parser.add_argument("command", choices=["serve", "pull"])
    parser.add_argument("--source", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == "pull":
        print("pulled {} verdict file(s)".format(pull(args.source)))
        return 0
    server = start_share_thread()
    if server is None:
        return 1
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())

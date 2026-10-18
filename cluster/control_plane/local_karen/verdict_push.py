"""Mini-origin signed verdict delivery; public comments cannot grant authority.

Only the Mini holds the RSA private key. NucBox pins its public key from this
repository. Signatures cover exact bytes, purpose, issue time and all artifacts.
The receiver accepts completed reviews only and never rolls a SHA back to an
older decision. No endpoint can execute a command or trigger a dispatch.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from .config import KarenConfig
from .verdict_sync import MAX_BYTES, collect_verdicts, validate_entry

PUSH_PATH = '/api/karen/verdicts'
PUBLIC_KEY = Path(__file__).with_name('mini-verdict-public.pem')
PRIVATE_KEY = Path('~/.local/share/karen/verdict-signing.pem').expanduser()
PURPOSE = 'storypool-karen-verdict-v1'


def reviewed(verdict):
    """Transport errors and legacy synthetic FAILs cannot authorize repairs."""
    if (verdict is None or verdict.status != 'COMPLETE'
            or not isinstance(verdict.reason, str)
            or len(verdict.reason.strip()) < 10
            or verdict.reason.strip().lower() == 'unknown'):
        return False
    reviews = verdict.review_results
    if not isinstance(reviews, list) or not reviews:
        return False
    valid = [r for r in reviews if r.get('verdict') in ('PASS', 'FAIL', 'BORDERLINE')
             and isinstance(r.get('reason'), str) and len(r['reason'].strip()) >= 10
             and any(r.get('findings', {}).values())]
    if verdict.passed:
        return (any(r.get('reviewer') != 'minimax' and r['verdict'] == 'PASS' for r in valid)
                and any(r.get('reviewer') == 'minimax' and r['verdict'] == 'PASS' for r in valid)
                and all(r['verdict'] == 'PASS' for r in valid))
    return bool(valid)


def receive(body, signature, verdict_dir=None, public_key=PUBLIC_KEY, now=None):
    """Authenticate before parsing, validate whole batch before writing."""
    if len(body) > MAX_BYTES:
        raise ValueError('oversized batch')
    try:
        sig = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError):
        raise PermissionError('invalid signature')
    with tempfile.TemporaryDirectory(prefix='karen-verify-') as temp:
        sigfile = Path(temp) / 'signature'
        sigfile.write_bytes(sig)
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public_key),
                                 '-signature', str(sigfile)], input=body,
                                capture_output=True, timeout=10)
    if result.returncode:
        raise PermissionError('invalid signature')
    payload = json.loads(body)
    issued = payload.get('issued_at')
    if (payload.get('purpose') != PURPOSE or type(issued) not in (int, float)
            or not abs((time.time() if now is None else now) - issued) <= 300):
        raise PermissionError('expired or wrong-purpose delivery')
    entries = payload.get('verdicts')
    if not isinstance(entries, dict) or len(entries) > 1000:
        raise ValueError('invalid batch')
    validated = []
    for name, content in entries.items():
        verdict = validate_entry(name, content)
        if not reviewed(verdict):
            raise ValueError('incomplete review in batch')
        from datetime import datetime
        stamp = datetime.fromisoformat(verdict.timestamp)
        if stamp.tzinfo is None or stamp.timestamp() > issued + 60:
            raise ValueError('invalid review timestamp')
        validated.append((name, content, verdict))
    directory = Path(verdict_dir or KarenConfig.load().get_verdict_dir())
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    accepted = []
    with (directory / '.push.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name, content, verdict in validated:
            # Compare both outcomes; a newer FAIL must supersede an older PASS.
            prefix = name.rsplit('_', 1)[0]
            siblings = [directory / (prefix + '_' + outcome + '.json')
                        for outcome in ('KARENPASS', 'KARENFAIL')]
            for path in siblings:
                if path.is_symlink():
                    raise ValueError('symlink destination')
                if path.exists():
                    existing = validate_entry(path.name, path.read_text())
                    if existing and existing.timestamp > verdict.timestamp:
                        raise ValueError('older verdict cannot replace current decision')
            dest = directory / name
            fd, temp = tempfile.mkstemp(dir=str(directory), prefix='.push-')
            with os.fdopen(fd, 'w') as stream:
                stream.write(content)
            os.replace(temp, str(dest))
            for path in siblings:
                if path != dest and path.exists():
                    path.unlink()
            # Preserve the authoritative marker on the receiving node too.
            # Only authenticated, validated and non-replayed decisions reach it.
            latest = directory / (prefix + '_LATEST.json')
            fd, temp = tempfile.mkstemp(dir=str(directory), prefix='.latest-')
            with os.fdopen(fd, 'w') as stream:
                stream.write(content)
            os.replace(temp, str(latest))
            accepted.append({'repo': verdict.owner + '/' + verdict.repo,
                             'pr': verdict.pr_number, 'sha': verdict.head_sha,
                             'passed': verdict.passed})
    receipt = {'accepted': accepted, 'body_sha256': hashlib.sha256(body).hexdigest()}
    (directory / 'push-receipt.json').write_text(json.dumps(receipt, indent=2))
    return receipt


def push(config=None, target=None, private_key=PRIVATE_KEY):
    config = config or KarenConfig.load()
    entries = {name: content for name, content in collect_verdicts(config.get_verdict_dir()).items()
               if reviewed(validate_entry(name, content))}
    if not entries:
        return {'accepted': []}
    if not private_key.is_file():
        raise RuntimeError('Mini signing key is not provisioned')
    # Only the newest decision for each exact PR/head is sent.
    newest = {}
    for name, content in entries.items():
        verdict = validate_entry(name, content)
        key = (verdict.owner, verdict.repo, verdict.pr_number, verdict.head_sha)
        if key not in newest or verdict.timestamp > newest[key][2]:
            newest[key] = (name, content, verdict.timestamp)
    entries = {name: content for name, content, _ in newest.values()}
    body = json.dumps({'purpose': PURPOSE, 'issued_at': time.time(), 'verdicts': entries},
                      sort_keys=True).encode()
    signature = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(private_key)],
                               input=body, capture_output=True, timeout=10, check=True).stdout
    target = target or os.environ.get('STORYPOOL_VERDICT_PUSH_URL',
                                     'http://100.93.165.1:8790' + PUSH_PATH)
    request = urllib.request.Request(target, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'X-Karen-Signature': base64.b64encode(signature).decode('ascii')})
    with urllib.request.urlopen(request, timeout=30) as response:
        receipt = json.load(response)
    if receipt.get('body_sha256') != hashlib.sha256(body).hexdigest():
        raise RuntimeError('Unexpected delivery receipt')
    (config.get_state_dir() / 'push-receipt.json').write_text(json.dumps(receipt, indent=2))
    return receipt

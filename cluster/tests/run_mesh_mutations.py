"""Run the PR #68 negative tests against disposable one-fault source copies.

Usage: python -m cluster.tests.run_mesh_mutations
No live services, configuration, or credentials are accessed.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MUTANTS = [
    ('argv-code-leak', 'bobctl.py',
     'f"bash -s -- --ref {ref} --sha256 {sha256} --prompt-code")',
     'f"bash -s -- --ref {ref} --sha256 {sha256} --code \'{code}\'")'),
    ('unpinned-ref', 'install/join.sh', 'if [[ ! "$REF" =~ ^[0-9a-fA-F]{40}$ ]]; then', 'if false; then'),
    ('missing-checksum', 'install/join.sh', 'if [[ ! "$SHA256" =~ ^[0-9a-fA-F]{64}$ ]]; then', 'if false; then'),
    ('bad-checksum-accepted', 'install/join.sh',
     'if [[ "$(sha256_of "${TMP}/repo.tgz")" != "${SHA256}" ]]; then', 'if false; then'),
    ('revoke-render-only', 'bobctl.py', 'getattr(args, "cmd", None) == "revoke" or ', ''),
    ('unconfirmed-reload-accepted', 'joind.py',
     'and after["config_load_time"] != before["config_load_time"]', 'and True'),
    ('reload-without-signal', 'joind.py', 'os.kill(pid, signal.SIGHUP)', 'pass  # mutant: no signal'),
    ('global-rate-limit', 'joind.py', 'window = self.windows.setdefault(source, [])',
     'window = self.windows.setdefault("global", [])'),
    ('rate-limit-not-wired', 'joind.py', 'if not limiter.allow(source):', 'if False:'),
    ('joind-serial-lock-removed', 'joind.py', 'async with serial:', 'async with asyncio.Lock():'),
    ('secret-length-cap-removed', 'registry.py', ' or len(secret) > 200', ''),
    ('secret-length-cap-raised', 'registry.py', 'len(secret) > 200', 'len(secret) > 256'),
    ('body-user-check-removed', 'joind.py', 'if json.loads(msg.data).get("user") != user:', 'if False:'),
    ('monitor-loopback-guard-removed', 'joind.py', 'host != "127.0.0.1" or ', ''),
    ('member-shared-ack-permissions', 'registry.py', 'cons = cap_consumer(c, name)', 'cons = cap_consumer(c)'),
    ('guest-shared-ack-permissions', 'registry.py', 'consumer = guest_consumer(name)', 'consumer = CONSUMER_GUESTS'),
    ('per-user-bucket-removed', 'joind.py', 'if not users.allow(user):', 'if False:'),
    ('local-user-bucket-removed', 'joind.py', 'not local_users.allow(user) or ', ''),
    ('loopback-opt-in-bypassed', 'joind.py', 'if loopback and not allow_loopback:', 'if False:'),
    ('executable-check-removed', 'joind.py', 'if Path(os.readlink(f"/proc/{pid}/exe")).name != "nats-server":', 'if False:'),
    ('legacy-wildcard-restored', 'registry.py', '"subscribe": [f"_INBOX_{node}.>"]',
     '"subscribe": [f"_INBOX_{node}.>", "_INBOX.>", "$JS.ACK.>", "$JS.API.>"]'),

]


def run(root):
    proc = subprocess.run([sys.executable, '-m', 'unittest', 'cluster.tests.test_mesh_hardening'],
                          cwd=root, env={**os.environ, 'PYTHONPATH': str(root)},
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout + proc.stderr


def main():
    rc, output = run(ROOT)
    if rc:
        print('Baseline failed; refusing to score mutants.\n' + output)
        return 1
    killed = 0
    for name, relative, before, after in MUTANTS:
        with tempfile.TemporaryDirectory(prefix='bob-mesh-mutant-') as td:
            root = Path(td)
            shutil.copytree(ROOT / 'cluster', root / 'cluster', ignore=shutil.ignore_patterns('__pycache__'))
            source = root / 'cluster' / relative
            text = source.read_text()
            if text.count(before) != 1:
                raise RuntimeError(f'{name}: mutation anchor changed')
            source.write_text(text.replace(before, after, 1))
            rc, output = run(root)
            # Syntax/import errors do not count as a killed behavioral mutant.
            caught = rc != 0 and 'FAIL:' in output and 'SyntaxError' not in output
            print(f'{name}: {"KILLED" if caught else "SURVIVED/ERROR"}')
            killed += int(caught)
    print(f'Mutations: {killed}/{len(MUTANTS)} killed')
    return 0 if killed == len(MUTANTS) else 1


if __name__ == '__main__':
    raise SystemExit(main())

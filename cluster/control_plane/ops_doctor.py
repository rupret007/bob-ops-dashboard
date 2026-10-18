"""Hourly local health checks and bounded, isolated Codex repair PRs."""
from __future__ import annotations
import fcntl
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from .fix_dispatcher import _find_codex
from .fix_worker import run, ensure_no_staged_secrets
from .coordination import ClaimClient, CoordinationError, Lease
from .service_logging import bind_context, reset_context

REPO = 'rupret007/bob-ops-dashboard'
BRANCH = 'cursor/storypool-all-in-one-47c7'


class RepairLimit(RuntimeError):
    pass


def reserve_attempt(state_dir, today):
    """Called under the doctor lock. Reserve before launching any paid repair."""
    path = state_dir / 'daily.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    count = state.get('attempts', 0) if state.get('date') == today else 0
    if count >= 3:
        return False
    path.write_text(json.dumps({'date': today, 'attempts': count + 1}))
    return True


def escalate(state_dir, message):
    with (state_dir.parent / 'NEEDS_JEFF.md').open('a') as f:
        f.write(f'\n- {datetime.now(timezone.utc).isoformat()}: {message}\n')


def repair(root, log, stamp):
    """Admit one cluster repair before reserving budget or starting Codex."""
    from .fix_worker import _lease
    client = ClaimClient.from_env()
    remote = run(['git', 'ls-remote', f'https://github.com/{REPO}.git', f'refs/heads/{BRANCH}'])
    fields = remote.split()
    if len(fields) != 2 or fields[1] != f'refs/heads/{BRANCH}':
        raise CoordinationError('Cannot pin the doctor repair base; new work paused')
    sha = fields[0]
    machine = os.environ.get('STORYPOOL_MACHINE', 'mini' if platform.system() == 'Darwin' else 'nucbox')
    claim = client.acquire_task(REPO, 'ops-doctor:' + BRANCH, sha, machine)
    with Lease(client, claim) as lease:
        token = _lease.set(lease)
        context = bind_context(job_id='doctor-' + stamp, correlation_id=claim['owner'],
                               repo=REPO, sha=sha, service='ops-doctor')
        try:
            if not reserve_attempt(log.parent, datetime.now(timezone.utc).date().isoformat()):
                raise RepairLimit('Ops doctor repair limit (3/day) reached')
            return _repair_claimed(root, log, stamp, sha)
        finally:
            reset_context(context)
            _lease.reset(token)


def _repair_claimed(root, log, stamp, sha):
    codex = _find_codex()
    if not codex:
        raise RuntimeError('Codex executable unavailable')
    branch = f'ops/storypool-doctor-{stamp}'
    with tempfile.TemporaryDirectory(prefix='storypool-doctor-') as temp:
        clone, tree = Path(temp) / 'repo', Path(temp) / 'worktree'
        run(['git', 'clone', '--no-checkout', '--single-branch', '--branch', BRANCH,
             f'https://github.com/{REPO}.git', str(clone)])
        run(['git', 'worktree', 'add', '-b', branch, str(tree), sha], clone)
        # Only health diagnostics are provided; auth files and environment are never included.
        prompt = ('Fix StoryPool health failures in this repository. Read the diagnostics below. '
                  'Make focused changes and add meaningful regression coverage. Run cluster tests. '
                  'Do not weaken tests, use sudo, expose secrets, touch rad-dad-show-night, merge, '
                  'create tags/releases, or commit/push. The supervisor handles commits and a draft PR.\n'
                  + log.read_text()[-24000:])
        # Shared runner fences every command and kills its process group on lease
        # loss. It records safe test names, never raw provider output or prompts.
        run([str(codex), 'exec', '-s', 'workspace-write', '-c', 'model_reasoning_effort=xhigh',
             '-c', 'sandbox_workspace_write.network_access=true', '-'], tree, input=prompt)
        run([sys.executable, '-m', 'pytest', 'cluster', '-q'], tree,
            env={**os.environ, 'STORYPOOL_JOBS_DIR': str(Path(temp) / 'test-jobs'),
                 'STORYPOOL_METRICS_DIR': str(Path(temp) / 'test-metrics'),
                 'STORYPOOL_DATA_DIR': str(Path(temp) / 'test-data'),
                 'STORYPOOL_FIX_STATE_DIR': str(Path(temp) / 'test-fixes')})
        run(['git', 'add', '-A'], tree)
        if not run(['git', 'diff', '--cached', '--name-only'], tree):
            raise RuntimeError('Codex produced no repair')
        ensure_no_staged_secrets(tree)
        run(['git', 'commit', '-m', 'fix: repair StoryPool health checks'], tree)
        run(['git', 'push', 'origin', f'HEAD:refs/heads/{branch}'], tree)
        body = Path(temp) / 'pr-body.txt'
        body.write_text('Repair failures detected by the hourly local StoryPool ops doctor.\n\n'
                        'Validation: cluster pytest suite passed. Local diagnostics retained in doctor logs.\n')
        return run(['gh', 'pr', 'create', '--repo', REPO, '--base', BRANCH, '--head', branch,
                    '--draft', '--label', 'needs-karen', '--title', 'fix: StoryPool ops doctor repair',
                    '--body-file', str(body)], tree)


def main(argv=None):
    from .node_control import is_draining
    if is_draining():
        return 0
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--findings-only', action='store_true',
                        help='Report health findings without starting repair jobs')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    state_dir = Path(os.environ.get('STORYPOOL_DOCTOR_DIR', '~/.local/share/storypool/doctor')).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        now = datetime.now(timezone.utc)
        stamp = now.strftime('%Y%m%dT%H%M%S%fZ')
        log = state_dir / f'{stamp}.log'
        failed = False
        execution_failed = False
        machine = os.environ.get('STORYPOOL_MACHINE', 'mini' if platform.system() == 'Darwin' else 'nucbox')
        with log.open('w') as output:
            os.chmod(log, 0o600)
            for command in ([sys.executable, '-m', 'cluster.control_plane.smoke'],
                            [sys.executable, '-m', 'cluster.control_plane.watchdog', '--machine', machine,
                             '--check-only']):
                if args.findings_only:
                    command.append('--findings-only')
                output.write(f'CHECK {command[2]}\n')
                output.flush()
                try:
                    result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT,
                                            timeout=600, env={**os.environ, 'PYTHONPATH': str(root)})
                    failed = failed or result.returncode != 0
                    allowed = (0,) if args.findings_only else (0, 1)
                    execution_failed = execution_failed or result.returncode not in allowed
                    output.write(f'CHECK EXIT {result.returncode}\n')
                except (OSError, subprocess.SubprocessError) as exc:
                    failed = True
                    execution_failed = True
                    output.write(f'CHECK ERROR: {type(exc).__name__}\n')
        if execution_failed:
            return 1
        if args.findings_only:
            print(f'Ops doctor: diagnostic run completed; findings retained in {log}')
            return 0
        if not failed:
            return 0
        try:
            url = repair(root, log, stamp)
            print(f'Draft repair PR: {url}')
            return 0
        except CoordinationError as exc:
            with log.open('a') as output:
                output.write(f'REPAIR DEFERRED: {exc}\n')
            return 0
        except RepairLimit:
            escalate(state_dir, f'Ops doctor repair limit (3/day) reached; diagnostics: {log}')
            return 1
        except Exception as exc:
            with log.open('a') as output:
                # Exit code and executable are useful; captured output may contain secrets.
                if isinstance(exc, subprocess.CalledProcessError):
                    output.write(f"REPAIR ERROR: {Path(exc.cmd[0]).name} exited {exc.returncode}\n")
                else:
                    output.write(f"REPAIR ERROR: {type(exc).__name__}\n")
            escalate(state_dir, f'Ops doctor repair failed ({type(exc).__name__}); diagnostics: {log}')
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

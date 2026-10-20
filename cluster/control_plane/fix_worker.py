"""Supervised Codex fixes. Only a verified push can finish a ledger job."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import logging
import signal
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from ..never_touch import is_never_touch
from .service_logging import event, bind_context, reset_context, failing_tests, failure_reason
from .job_ledger import get_ledger
from .retry_policy import PreflightFailure, REASONS, STAGE_CODES, WorkerFailure, classify_failure
from .fix_dispatcher import _find_codex, extract_findings_from_verdict
from .local_karen.config import KarenConfig
from .local_karen.verdict import load_verdict
from .worker_runtime import (qualified_runtime, RuntimeUnqualified, MODULE_RUNNER,
                             test_files, outside_standard_tests)
from . import cursor_worker


CODEX_EFFORT = 'xhigh'
_lease = ContextVar('cluster_lease', default=None)
_job_deadline = ContextVar('worker_job_deadline', default=None)
_failure_stage = ContextVar('worker_failure_stage', default='supervisor')
_provider = ContextVar('worker_provider', default='codex')
JOB_SECONDS = 45 * 60


def _stage(name):
    """Only supervisor call sites choose the measurement stage."""
    if not isinstance(name, str) or name not in STAGE_CODES:
        raise ValueError('Unknown controlled worker stage')
    _failure_stage.set(name)


def codex_model():
    """Model the Codex CLI will actually use: explicit override, else its config default."""
    explicit = os.environ.get('STORYPOOL_CODEX_MODEL', '').strip()
    if explicit:
        return explicit
    try:
        # Only the top-level `model = "..."` key is read; nothing else is parsed or logged.
        for line in (Path.home() / '.codex' / 'config.toml').read_text().splitlines():
            if line.startswith('['):
                break
            match = re.match(r'\s*model\s*=\s*["\']([^"\']+)["\']', line)
            if match:
                return match.group(1)
    except OSError:
        pass
    return ''


def codex_args():
    model = os.environ.get('STORYPOOL_CODEX_MODEL', '').strip()
    return (['exec'] + (['-m', model] if model else []) +
            ['-s', 'workspace-write', '-c', 'model_reasoning_effort=' + CODEX_EFFORT,
             '-c', 'sandbox_workspace_write.network_access=true', '-'])


def model_label():
    if _provider.get() == 'cursor':
        return 'cursor default'
    return '{} {}'.format(codex_model() or 'default', CODEX_EFFORT)


MAX_FIX_ATTEMPTS = 3
TEST_OUTPUT_TAIL = 6000


def _run_claimed(args, cwd, lease, **kwargs):
    """Stop the entire command process group if ownership is lost mid-command."""
    deadline = min(time.monotonic() + 1800, _job_deadline.get() or float('inf'))
    if time.monotonic() >= deadline:
        raise subprocess.TimeoutExpired(args, 0)
    prompt = kwargs.pop('input', None)
    process = subprocess.Popen(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, stdin=subprocess.PIPE if prompt is not None else None,
        start_new_session=True, **kwargs)
    try:
        while True:
            if lease.lost:
                from .coordination import ClaimLost
                raise ClaimLost('Cluster claim lost during command; command stopped')
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(args, 1800)
            try:
                stdout, stderr = process.communicate(input=prompt, timeout=1)
                break
            except subprocess.TimeoutExpired:
                prompt = None
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            except ProcessLookupError:
                process.communicate()


def run(args, cwd=None, **kwargs):
    lease = _lease.get()
    if lease is not None:
        lease.renew()
    started = time.monotonic()
    # Only executable/operation are logged. Arguments may contain credentials,
    # PR content, URLs, or prompts; stdout/stderr are never persisted wholesale.
    executable = Path(str(args[0])).name
    operation = args[1] if len(args) > 1 and re.fullmatch(r'[a-z-]+', args[1]) else ''
    stage = _failure_stage.get()
    event('fix-worker', 'command_started', executable=executable, operation=operation,
          failure_stage=stage)
    try:
        if lease is None:
            result = subprocess.run(args, cwd=cwd, check=True, text=True,
                                    capture_output=True, timeout=1800, **kwargs)
        else:
            result = _run_claimed(args, cwd, lease, **kwargs)
        if lease is not None:
            lease.renew()
    except (subprocess.SubprocessError, OSError) as exc:
        is_pytest = ('-m' in args and 'pytest' in args) or MODULE_RUNNER in args
        names = failing_tests(getattr(exc, 'stdout', '') or '') if is_pytest else []
        event('fix-worker', 'command_failed', level=logging.ERROR, exc_info=True,
              executable=executable, operation=operation, error_type=type(exc).__name__,
              returncode=getattr(exc, 'returncode', None), failed_tests=names,
              failure_code=classify_failure(exc, stage)[0], failure_stage=stage,
              reason=failure_reason(exc, stage=stage),
              duration_ms=round((time.monotonic() - started) * 1000))
        raise
    event('fix-worker', 'command_finished', executable=executable, operation=operation,
          duration_ms=round((time.monotonic() - started) * 1000), returncode=result.returncode,
          failure_stage=stage)
    if tuple(args[:2]) == ('git', 'ls-files') and '-z' in args:
        return result.stdout  # NUL-delimited filenames are literal data, including leading whitespace.
    return result.stdout.strip()


def test_command(tree, python=None):
    """Select a real test runner; unsupported repos need a human decision."""
    package = tree / 'package.json'
    if package.exists() and json.loads(package.read_text()).get('scripts', {}).get('test'):
        return ['npm', 'test']
    if ((tree / 'pytest.ini').exists() or list(tree.glob('test_*.py'))
            or list((tree / 'tests').glob('**/test_*.py'))):
        return [python or sys.executable, '-m', 'pytest', '-q']
    raise PreflightFailure('unsupported-tests')


def ensure_no_staged_secrets(tree):
    from .local_karen.checks import SECRET_PATTERNS
    diff = run(['git', 'diff', '--cached', '--no-ext-diff', '--unified=0'], tree)
    for line in diff.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            if any(re.search(pattern, line[1:]) for pattern, _ in SECRET_PATTERNS):
                raise RuntimeError('Potential staged secret; refusing commit (value redacted)')


def fix_pr(repo, pr_number, head_sha, findings):
    if is_never_touch(repo):
        raise ValueError('never-touch repository')
    token = _failure_stage.set('setup')
    try:
        return _fix_pr(repo, pr_number, head_sha, findings)
    except (PreflightFailure, WorkerFailure):
        raise
    except Exception as exc:
        code, stage = classify_failure(exc, _failure_stage.get())
        failure = WorkerFailure(code, stage)
        # Preserve the original frame chain before replacing the exception.
        # The structured formatter excludes its message, locals and CLI output.
        try:
            event('fix-worker', 'operation_failed', level=logging.ERROR, exc_info=True,
                  raise_on_error=True,
                  failure_code=code, failure_stage=stage,
                  error_type=type(exc).__name__)
        except Exception:
            # Telemetry cannot replace the cause or turn failure into success.
            failure.cause_diagnostic_unavailable = True
        raise failure from None
    finally:
        _failure_stage.reset(token)


@contextmanager
def _private_workspace():
    """One worker owns its process; keep its checkout and children private."""
    previous = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix='storypool-fix-') as temp:
            yield temp
    finally:
        os.umask(previous)


def _fix_pr(repo, pr_number, head_sha, findings):
    _stage('provider')
    cursor = _provider.get() == 'cursor'
    codex = None if cursor else _find_codex()
    if cursor and not cursor_worker.available():
        raise RuntimeError('Cursor worker unavailable')
    if not cursor and not codex:
        raise RuntimeError('codex CLI not found (expected ~/bin/codex)')
    _stage('git')
    pr = json.loads(run(['gh', 'pr', 'view', str(pr_number), '--repo', repo,
                         '--json', 'headRefName,headRefOid,isCrossRepository,state']))
    branch = pr['headRefName']
    if pr['state'] != 'OPEN' or pr['isCrossRepository'] or branch in ('main', 'master'):
        raise PreflightFailure('protected-pr')
    if pr['headRefOid'] != head_sha:
        raise PreflightFailure('head-changed')
    with _private_workspace() as temp:
        checkout = Path(temp) / 'repo'
        tree = Path(temp) / 'worktree'
        run(['git', 'clone', '--no-checkout', '--single-branch', '--branch', branch,
             f'https://github.com/{repo}.git', str(checkout)])
        run(['git', 'worktree', 'add', '--detach', str(tree), head_sha], checkout)
        from .fix_iteration import edit_and_test
        edit_and_test(sys.modules[__name__], tree, temp, repo, pr_number,
                      head_sha, findings, codex, cursor)
        _stage('verification')
        if run(['git', 'rev-parse', 'HEAD'], tree) != head_sha:
            raise RuntimeError('Worker changed HEAD unexpectedly')
        _stage('git')
        run(['git', 'add', '-A'], tree)
        _stage('verification')
        if not run(['git', 'diff', '--cached', '--name-only'], tree):
            raise WorkerFailure('no-change', 'verification')
        ensure_no_staged_secrets(tree)
        _stage('git')
        run(['git', 'commit', '-m', f'fix: address Karen findings for PR #{pr_number}'], tree)
        sha = run(['git', 'rev-parse', 'HEAD'], tree)
        _stage('push')
        run(['git', 'push', 'origin', f'HEAD:refs/heads/{branch}'], tree)
        _stage('verification')
        remote = run(['git', 'ls-remote', 'origin', f'refs/heads/{branch}'], tree).split()[0]
        if remote != sha:
            raise RuntimeError('Pushed SHA could not be verified')
        run(['gh', 'pr', 'edit', str(pr_number), '--repo', repo, '--add-label', 'needs-karen'])
        return sha


def findings_for_pr(repo, pr_number, head_sha):
    owner, name = repo.split('/')
    verdict = load_verdict(owner, name, pr_number, head_sha, KarenConfig.load())
    # Public PR comments/reviews are untrusted data, even when they contain
    # Karen's marker and a matching short SHA. Only the local gate's artifact
    # can authorize a repair. Check its contents too: filenames use short SHAs.
    if (verdict is None or verdict.passed
            or verdict.owner != owner or verdict.repo != name
            or verdict.pr_number != pr_number or verdict.head_sha != head_sha):
        return []
    from .local_karen.verdict_push import reviewed
    if not reviewed(verdict):
        return []
    return extract_findings_from_verdict(verdict.to_dict())


def execute(job_id):
    ledger = get_ledger()
    jobs = [j for j in ledger.get_recent_jobs(hours=24) if j.job_id == job_id]
    if not jobs:
        raise RuntimeError('Job missing from ledger')
    job = jobs[0]
    from .node_control import is_draining
    if is_draining(job.machine):
        ledger.fail_job(job_id, "maintenance-before-start; eligible for redispatch")
        return 0
    context = bind_context(job_id=job_id, correlation_id=job_id, repo=job.repo,
                           pr_number=job.pr_number, sha=job.head_sha)
    started = time.monotonic()
    lease_token = None
    lease = None
    deadline_token = _job_deadline.set(started + JOB_SECONDS)
    stage_token = _failure_stage.set('coordination')
    provider_token = _provider.set(job.provider)
    try:
        if job.provider not in ('codex', 'cursor'):
            raise RuntimeError('Worker provider is not implemented')
        if job.provider == 'cursor' and (job.machine != 'nucbox' or sys.platform != 'linux'):
            raise RuntimeError('Cursor worker requires its qualified Nuc runtime')
        from .coordination import ClaimClient, Lease
        lease = Lease(ClaimClient.from_env(), job.cluster_claim)
        lease.__enter__()
        lease_token = _lease.set(lease)
        _stage('setup')
        ledger.start_job(job_id, os.getpid(), model=model_label())
        event('fix-worker', 'job_started', machine=job.machine, provider=job.provider)
        _stage('findings')
        findings = findings_for_pr(job.repo, job.pr_number, job.head_sha)
        if not findings:
            raise RuntimeError('No Karen findings available for this PR')
        _stage('setup')
        sha = fix_pr(job.repo, job.pr_number, job.head_sha, findings)
        _stage('verification')
        ledger.complete_job(job_id, 'commit_pushed', sha, verified=True)
        try:
            with lease.lock:
                state = lease.client.retry_state(lease.claim)
                if state:
                    lease.client.retry_state(lease.claim, dict(state, code='already-fixed'))
        except Exception:
            event('fix-worker', 'retry_state_unavailable', level=logging.ERROR, exc_info=True)
        event('fix-worker', 'job_pushed', sha=sha, verified=True,
              duration_ms=round((time.monotonic() - started) * 1000))
        return 0
    except Exception as exc:
        # Exception strings can contain secrets or provider argv; keep ledger errors bounded.
        code, stage = classify_failure(exc, _failure_stage.get())
        message = failure_reason(exc, stage=stage)
        ledger.fail_job(job_id, message, failure_code=code, retryable=code not in REASONS,
                        failure_stage=stage)
        if lease is not None and lease_token is not None:
            try:
                with lease.lock:
                    state = lease.client.retry_state(lease.claim)
                    if state:
                        lease.client.retry_state(lease.claim, dict(state, code=code))
            except Exception:
                event('fix-worker', 'retry_state_unavailable', level=logging.ERROR, exc_info=True)
        event('fix-worker', 'job_failed', level=logging.ERROR, exc_info=True,
              error_type=type(exc).__name__, reason=message,
              failure_code=code, failure_stage=stage,
              cause_diagnostic_unavailable=bool(getattr(exc, 'cause_diagnostic_unavailable', False)),
              duration_ms=round((time.monotonic() - started) * 1000))
        return 1
    finally:
        _provider.reset(provider_token)
        _job_deadline.reset(deadline_token)
        _failure_stage.reset(stage_token)
        if lease_token is not None:
            _lease.reset(lease_token)
            lease.__exit__(None, None, None)
        reset_context(context)


if __name__ == '__main__':
    raise SystemExit(execute(sys.argv[1]))

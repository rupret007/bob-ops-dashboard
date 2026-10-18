"""Supervised Codex fixes. Only a verified push can finish a ledger job."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ..never_touch import is_never_touch
from .job_ledger import get_ledger
from .fix_dispatcher import _find_codex, extract_findings_from_verdict
from .local_karen.config import KarenConfig
from .local_karen.verdict import load_verdict


def run(args, cwd=None, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          capture_output=True, timeout=1800, **kwargs).stdout.strip()


def test_command(tree):
    """Select a real test runner; unsupported repos need a human decision."""
    if (tree / 'pytest.ini').exists() or (tree / 'tests').is_dir() or list(tree.glob('test_*.py')):
        return [sys.executable, '-m', 'pytest', '-q']
    package = tree / 'package.json'
    if package.exists() and json.loads(package.read_text()).get('scripts', {}).get('test'):
        return ['npm', 'test']
    raise RuntimeError('No supported test command; leave for Jeff')


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
    codex = _find_codex()
    if not codex:
        raise RuntimeError('codex CLI not found (expected ~/bin/codex)')
    pr = json.loads(run(['gh', 'pr', 'view', str(pr_number), '--repo', repo,
                         '--json', 'headRefName,headRefOid,isCrossRepository,state']))
    branch = pr['headRefName']
    if pr['state'] != 'OPEN' or pr['isCrossRepository'] or branch in ('main', 'master'):
        raise RuntimeError('Refusing closed, fork, or protected-branch PR')
    if pr['headRefOid'] != head_sha:
        raise RuntimeError('PR head changed before fix')
    with tempfile.TemporaryDirectory(prefix='storypool-fix-') as temp:
        checkout = Path(temp) / 'repo'
        tree = Path(temp) / 'worktree'
        run(['git', 'clone', '--no-checkout', '--single-branch', '--branch', branch,
             f'https://github.com/{repo}.git', str(checkout)])
        run(['git', 'worktree', 'add', '--detach', str(tree), head_sha], checkout)
        tests = test_command(tree)
        prompt = (f'Apply these Karen findings for {repo} PR #{pr_number}:\n' +
                  '\n'.join(findings) + '\nMake focused code fixes and run the tests. '
                  'Do not weaken tests. Do not commit or push; the supervisor does that. '
                  'No sudo, force-push, main pushes, tags, releases, merges, secrets, or rad-dad-show-night.\n'
                  f'Test command: {" ".join(tests)}')
        run([str(codex), 'exec', '--full-auto', '-c', 'model_reasoning_effort=low', '-'],
            tree, input=prompt)
        run(tests, tree, env={**os.environ,
            'STORYPOOL_JOBS_DIR': str(Path(temp) / 'test-jobs'),
            'STORYPOOL_METRICS_DIR': str(Path(temp) / 'test-metrics'),
            'STORYPOOL_DATA_DIR': str(Path(temp) / 'test-data'),
            'STORYPOOL_FIX_STATE_DIR': str(Path(temp) / 'test-fixes')})
        if run(['git', 'rev-parse', 'HEAD'], tree) != head_sha:
            raise RuntimeError('Worker changed HEAD unexpectedly')
        run(['git', 'add', '-A'], tree)
        if not run(['git', 'diff', '--cached', '--name-only'], tree):
            raise RuntimeError('No fix produced')
        ensure_no_staged_secrets(tree)
        run(['git', 'commit', '-m', f'fix: address Karen findings for PR #{pr_number}'], tree)
        sha = run(['git', 'rev-parse', 'HEAD'], tree)
        run(['git', 'push', 'origin', f'HEAD:refs/heads/{branch}'], tree)
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
    return extract_findings_from_verdict(verdict.to_dict())


def execute(job_id):
    ledger = get_ledger()
    jobs = [j for j in ledger.get_recent_jobs(hours=24) if j.job_id == job_id]
    if not jobs:
        raise RuntimeError('Job missing from ledger')
    job = jobs[0]
    ledger.start_job(job_id, os.getpid())
    try:
        findings = findings_for_pr(job.repo, job.pr_number, job.head_sha)
        if not findings:
            raise RuntimeError('No Karen findings available for this PR')
        sha = fix_pr(job.repo, job.pr_number, job.head_sha, findings)
        ledger.complete_job(job_id, 'commit_pushed', sha, verified=True)
        print(f'PUSHED {job_id} {sha}', flush=True)
        return 0
    except Exception as exc:
        # Log command names and errors, never captured CLI output (may contain secrets).
        message = f'{type(exc).__name__}: {exc}'
        ledger.fail_job(job_id, message)
        print(f'FAILED {job_id}: {message}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(execute(sys.argv[1]))

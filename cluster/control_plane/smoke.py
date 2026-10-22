"""Prove the real queued -> running -> pushed -> exact-SHA Karen pipeline.

Without --repo/--pr, inspect the latest job (safe for hourly checks).
With both, enqueue and execute one real fix, then run Karen on its pushed SHA.
"""
from __future__ import annotations
import argparse
import json
import os
from .fix_worker import execute, run
from .job_ledger import get_ledger
from .local_karen.config import KarenConfig
from .local_karen.verdict import load_verdict
from ..never_touch import is_never_touch


class PipelineFinding(RuntimeError):
    """The pipeline has not yet supplied the required success evidence."""


def verify(job, config):
    if not (job.created_at and job.start_time and job.state == 'done'
            and job.outcome == 'commit_pushed' and job.outcome_verified
            and job.outcome_sha and job.outcome_sha != job.head_sha):
        # Stage details exclude arbitrary worker output and subprocess arguments.
        raise PipelineFinding(
            f'Job {job.job_id} ({job.repo}#{job.pr_number}) has not completed '
            f'queued -> running -> pushed: state={job.state}, '
            f'outcome={job.outcome or "none"}, verified={job.outcome_verified}. '
            'Inspect its worker log; a fresh verified push is required.')
    current = json.loads(run(['gh', 'pr', 'view', str(job.pr_number), '--repo', job.repo,
                              '--json', 'headRefOid']))['headRefOid']
    if current != job.outcome_sha:
        raise PipelineFinding('Pushed job no longer matches PR head')
    owner, repo = job.repo.split('/')
    verdict = load_verdict(owner, repo, job.pr_number, current, config)
    if not verdict or verdict.head_sha != current:
        raise PipelineFinding('No exact-SHA Karen verdict for pushed job')
    print(f'{job.job_id}: queued -> running -> pushed {current} -> {verdict.verdict_type}')
    if not verdict.passed:
        raise PipelineFinding('Karen verdict is FAIL: see verdict findings')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo')
    parser.add_argument('--pr', type=int)
    parser.add_argument('--findings-only', action='store_true')
    args = parser.parse_args()
    if bool(args.repo) != bool(args.pr):
        parser.error('--repo and --pr must be provided together')
    if args.findings_only and args.repo:
        parser.error('Findings-only checks cannot enqueue work')
    try:
        ledger = get_ledger()
        config = KarenConfig.load()
        if args.repo:
            if is_never_touch(args.repo):
                raise ValueError('never-touch repository')
            sha = json.loads(run(['gh', 'pr', 'view', str(args.pr), '--repo', args.repo,
                                  '--json', 'headRefOid']))['headRefOid']
            job = ledger.create_job(args.repo, args.pr, sha, 'codex',
                                    os.environ.get('STORYPOOL_MACHINE', 'nucbox'))
            print(f'QUEUED {job.job_id}', flush=True)
            if execute(job.job_id):
                raise RuntimeError('Real fix worker failed; see job log')
            from .local_karen.gate import run_gate
            owner, repo = args.repo.split('/')
            run_gate(owner=owner, repo=repo, pr_number=args.pr, config=config, post_comment=True)
            jobs = ledger.get_jobs_for_pr(args.repo, args.pr)
            # A concurrent dispatch must not replace the job we just ran.
            jobs = [candidate for candidate in jobs if candidate.job_id == job.job_id]
            if not jobs:
                raise PipelineFinding(f'Smoke job {job.job_id} missing from ledger after execution')
        else:
            jobs = ledger.get_recent_jobs(hours=24)
        if not jobs:
            raise PipelineFinding('No real jobs in the last 24h; run with --repo OWNER/REPO --pr N')
        verify(max(jobs, key=lambda j: j.created_at), config)
        return 0
    except PipelineFinding as exc:
        print(f'SMOKE FAILED: {type(exc).__name__}: {exc}')
        return 0 if args.findings_only else 1
    except Exception as exc:
        print(f'SMOKE ERROR: {type(exc).__name__}; pipeline check could not execute')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

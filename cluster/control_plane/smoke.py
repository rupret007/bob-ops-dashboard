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


def verify(job, config):
    if not (job.created_at and job.start_time and job.state == 'done'
            and job.outcome == 'commit_pushed' and job.outcome_verified
            and job.outcome_sha and job.outcome_sha != job.head_sha):
        raise RuntimeError('Latest job has not completed queued -> running -> pushed')
    current = json.loads(run(['gh', 'pr', 'view', str(job.pr_number), '--repo', job.repo,
                              '--json', 'headRefOid']))['headRefOid']
    if current != job.outcome_sha:
        raise RuntimeError('Pushed job no longer matches PR head')
    owner, repo = job.repo.split('/')
    verdict = load_verdict(owner, repo, job.pr_number, current, config)
    if not verdict or verdict.head_sha != current:
        raise RuntimeError('No exact-SHA Karen verdict for pushed job')
    print(f'{job.job_id}: queued -> running -> pushed {current} -> {verdict.verdict_type}')
    if not verdict.passed:
        raise RuntimeError('Karen verdict is FAIL: see verdict findings')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo')
    parser.add_argument('--pr', type=int)
    args = parser.parse_args()
    if bool(args.repo) != bool(args.pr):
        parser.error('--repo and --pr must be provided together')
    ledger = get_ledger()
    config = KarenConfig.load()
    try:
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
        else:
            jobs = ledger.get_recent_jobs(hours=24)
        if not jobs:
            raise RuntimeError('No real jobs in the last 24h; run with --repo OWNER/REPO --pr N')
        verify(max(jobs, key=lambda j: j.created_at), config)
        return 0
    except Exception as exc:
        print(f'SMOKE FAILED: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

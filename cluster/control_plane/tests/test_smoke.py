from types import SimpleNamespace
from unittest.mock import patch
import pytest
from cluster.control_plane.smoke import verify


def job(**changes):
    fields = dict(job_id='real', repo='owner/repo', pr_number=1, created_at='now',
                  start_time='now', state='done', outcome='commit_pushed',
                  outcome_verified=True, head_sha='before', outcome_sha='after')
    return SimpleNamespace(**{**fields, **changes})


@patch('cluster.control_plane.smoke.load_verdict')
@patch('cluster.control_plane.smoke.run', return_value='{"headRefOid":"after"}')
def test_requires_exact_sha_verdict(run, verdict):
    verdict.return_value = SimpleNamespace(head_sha='before', passed=True)
    with pytest.raises(RuntimeError, match='exact-SHA'):
        verify(job(), None)
    verdict.return_value = SimpleNamespace(head_sha='after', passed=True, verdict_type='KARENPASS')
    verify(job(), None)


def test_failed_or_unverified_jobs_never_prove_pipeline():
    for changes in ({'state': 'failed'}, {'outcome_verified': False}, {'start_time': ''}):
        with pytest.raises(RuntimeError, match='queued'):
            verify(job(**changes), None)

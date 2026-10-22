"""Feedback measures outcomes, counts failures, and keeps missing evidence unknown."""
import json
from datetime import datetime, timezone

from cluster.control_plane import improvement as loop


NOW = 2000000000


def iso(when):
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


def job(number, state='done', when=NOW - 20, **fields):
    return dict(job_id=str(number), state=state, end_time=iso(when),
                outcome='commit_pushed', outcome_verified=True, outcome_sha='a' * 40, **fields)


def write_jobs(root, rows):
    path = root / 'jobs/ledger.jsonl'
    path.parent.mkdir(exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def test_duplicate_rows_expired_jobs_and_unverified_success_do_not_inflate_rate():
    valid = job(1)
    missing_evidence = dict(job(2), outcome_verified=False)
    short_sha = dict(job(3), outcome_sha='abc')
    expired = job(4, 'expired')
    rows = [valid, valid, missing_evidence, short_sha, expired, job(5, when=NOW - 90000)]
    value = loop.measure(rows, [], NOW)
    assert value['terminal_jobs'] == 4 and value['verified_jobs'] == 1
    assert value['verified_job_percent'] == 25
    assert value['top_failures'] == [dict(code='unclassified', count=3)]


def test_missing_corrupt_and_empty_evidence_cannot_prove_success(tmp_path):
    first = loop.refresh(tmp_path, [], NOW)
    assert first['coverage'] == 'unavailable'
    assert first['metrics']['verified_job_percent'] is None
    assert first['success'] == 'unproven'
    write_jobs(tmp_path, [job(1)])
    with (tmp_path / 'jobs/ledger.jsonl').open('a') as stream:
        stream.write('invalid json\n')
    second = loop.refresh(tmp_path, [], NOW)
    assert second['coverage'] == 'incomplete'
    assert second['metrics']['verified_job_percent'] is None
    assert loop.measure([], [], NOW)['verified_job_percent'] is None


def test_backlog_age_stale_sessions_and_unknown_failure_codes_are_safe():
    rows = [job(1, 'running', lease_expires=iso(NOW - 1)),
            job(2, 'failed', failure_code='do-not-publish-credential-value')]
    prs = [dict(updatedAt=iso(NOW - 172801), isDraft=True), dict(updatedAt='unknown')]
    value = loop.measure(rows, prs, NOW)
    assert value['overdue_sessions'] == 1
    assert value['backlog'] == 2 and value['inactive_prs_48h'] == 1
    assert value['undated_prs'] == 1
    assert 'do-not-publish' not in json.dumps(value)


def test_undated_or_future_terminal_jobs_make_rate_unknown():
    for end in ('unknown', iso(NOW + 1)):
        report = loop.measure([job(1), dict(job(2), end_time=end)], [], NOW)
        assert report['undated_terminal_jobs'] == 1
        assert report['verified_job_percent'] is None


def test_overlapping_windows_cannot_claim_improvement_and_regression_is_actionable(tmp_path):
    write_jobs(tmp_path, [job(n) for n in range(10)])
    baseline = loop.refresh(tmp_path, [], NOW)
    assert baseline['job_outcome_trend'] == 'insufficient_observation'
    overlap = loop.refresh(tmp_path, [], NOW + 10)
    assert overlap['job_outcome_trend'] == 'insufficient_observation'
    write_jobs(tmp_path, [job(n, 'done' if n < 5 else 'failed', when=NOW + 86400) for n in range(10)])
    later = loop.refresh(tmp_path, [], NOW + 86401)
    assert later['verified_job_percent_delta'] == -50
    assert later['job_outcome_trend'] == 'regressing'
    assert later['success'] == 'unproven'
    assert 'hold further rollout' in later['recommendations'][0]
    assert json.loads((tmp_path / 'conductor/improvement-baseline.json').read_text()) == baseline_before_save(baseline)


def baseline_before_save(report):
    return {k: v for k, v in report.items() if k not in
            ('baseline_observed_at', 'job_outcome_trend')}


def test_positive_job_trend_does_not_claim_overall_release_success(tmp_path):
    write_jobs(tmp_path, [job(n, 'done' if n < 5 else 'failed') for n in range(10)])
    loop.refresh(tmp_path, [], NOW)
    write_jobs(tmp_path, [job(n, when=NOW + 86400) for n in range(10)])
    after = loop.refresh(tmp_path, [], NOW + 86401)
    assert after['job_outcome_trend'] == 'improving'
    assert after['verified_job_percent_delta'] == 50
    assert after['success'] == 'unproven'

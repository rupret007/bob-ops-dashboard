"""Dashboard review counts are observations, never merge authorization."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from cluster.control_plane.dashboard.tachometer import EngineCollector


def snapshot(tmp_path, values):
    directory = tmp_path / 'verdicts'
    directory.mkdir()
    for name, value in values.items():
        (directory / name).write_text(json.dumps(value))
    return EngineCollector(tmp_path, tmp_path, 'nucbox').collect()


def review(**changes):
    value = dict(owner='owner', repo='repo', pr_number=3, head_sha='a' * 40,
                 timestamp=datetime.now(timezone.utc).isoformat(), passed=True,
                 status='COMPLETE', review_results=[{'reviewer': 'codex'}])
    value.update(changes)
    return value


def test_regular_latest_copy_and_result_count_once(tmp_path):
    value = review()
    data = snapshot(tmp_path, {'v_KARENPASS.json': value, 'v_LATEST.json': value})
    assert data['gauges'][2]['value'] == 1
    assert len(data['activity']) == 1
    assert next(g for g in data['llm_local'] if g['service'] == 'codex')['reviews_hour'] == 1


@pytest.mark.parametrize('passed,rate,label', [('False', 0.0, 'FAIL'), ('True', 100.0, 'PASS')])
def test_legacy_boolean_strings_are_not_python_truthiness(tmp_path, passed, rate, label):
    data = snapshot(tmp_path, {'v.json': review(passed=passed)})
    assert data['gauges'][3]['value'] == rate
    assert data['activity'][0]['text'].endswith(label)


@pytest.mark.parametrize('changes', [{'status': 'ERROR', 'passed': False}, {'passed': 'unknown'}])
def test_review_errors_are_retries_not_failures(tmp_path, changes):
    data = snapshot(tmp_path, {'v.json': review(**changes)})
    assert data['gauges'][2]['value'] == 0
    assert data['gauges'][3]['value'] is None
    assert data['activity'][0]['text'].endswith('ERROR')


def test_real_retry_and_different_full_sha_remain_distinct(tmp_path):
    first = review(passed=False)
    retry = dict(first, timestamp=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(), passed=True)
    other = dict(first, head_sha='b' * 40)
    data = snapshot(tmp_path, {'fail.json': first, 'retry.json': retry, 'other.json': other})
    assert data['gauges'][2]['value'] == 3
    assert data['gauges'][3]['value'] == 33.3
    assert len(data['activity']) == 3


def test_conflicting_copies_never_inflate_pass_rate(tmp_path):
    value = review()
    data = snapshot(tmp_path, {'pass.json': value, 'fail.json': dict(value, passed=False)})
    assert data['gauges'][2]['value'] == 1
    assert data['gauges'][3]['value'] == 0.0
    assert data['activity'][0]['text'].endswith('FAIL')


@pytest.mark.parametrize('age,state', [(60, 'recent'), (901, 'stale')])
def test_dashboard_distinguishes_review_evidence_freshness(tmp_path, age, state):
    value = review(timestamp=(datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat())
    data = snapshot(tmp_path, {'v.json': value})
    assert data['review_evidence']['state'] == state
    assert age <= data['review_evidence']['age_seconds'] <= age + 2


def test_missing_verdict_evidence_does_not_claim_poller_failure(tmp_path):
    data = snapshot(tmp_path, {})
    assert data['review_evidence']['age_seconds'] is None
    assert data['review_evidence']['state'] == 'stale'
    assert 'Check the poller and verdict sync' in data['review_evidence']['reason']


def test_verdict_evidence_banner_renders_without_html_injection():
    from cluster.control_plane.dashboard.server import render_dashboard
    from cluster.control_plane.dashboard.data_collector import DashboardData
    page = render_dashboard(DashboardData(timestamp='', machine='nucbox', live_mode=True))
    assert 'id="engine-review-evidence"' in page
    assert '.textContent=data.review_evidence?.reason' in page

"""Changed external CI evidence refreshes reviews, never retry or merge authority."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cluster.control_plane import backlog, ci_review_refresh as refresh, conductor, dispatcher
from cluster.control_plane.coordination import CoordinationError
from cluster.control_plane.job_ledger import JobLedger
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.verdict import Verdict, save_verdict, load_verdict

SHA = 'a' * 40
REPO = 'owner/repo'


def pr(**changes):
    return dict(number=7, headRefOid=SHA, state='OPEN', isDraft=False,
                statusCheckRollup=[{'name': 'unit', 'conclusion': 'SUCCESS', 'status': 'COMPLETED'}],
                labels=[{'name': 'karen-fail'}], **changes)


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    config = KarenConfig(verdict_dir=str(tmp_path / 'verdicts'), state_dir=str(tmp_path / 'state'))
    monkeypatch.setattr(KarenConfig, 'load', classmethod(lambda cls: config))
    # Legacy complete evidence can contain a conservative chunk BORDERLINE.
    # Preserve it; refreshed CI cannot resolve that review uncertainty.
    verdict = Verdict(owner='owner', repo='repo', pr_number=7, head_sha=SHA,
        passed=False, reason='Deterministic CI incomplete; reviewer requires broader context',
        timestamp='2026-10-19T01:00:00+00:00', reviewer='codex', tier='medium', confidence=.5,
        check_results=[{'check': 'ci_green', 'verdict': 'FAIL', 'reason': 'CI pending'},
                       {'check': 'no_secrets', 'verdict': 'PASS'}],
        review_results=[{'reviewer': 'codex', 'verdict': 'BORDERLINE',
                         'reason': 'Implementation context and regression evidence unavailable',
                         'scope': {'chunk': 1, 'total': 8},
                         'findings': {'test_coverage_issues': ['Regression evidence unavailable'],
                                      'code_quality_issues': ['Documentation scope needs reconciliation']}}])
    save_verdict(verdict, config)
    return config, verdict


@pytest.mark.parametrize('changes', [
    {'passed': True}, {'status': 'ERROR'}, {'review_results': []},
    {'review_results': [{'reviewer': 'codex', 'verdict': 'ERROR', 'reason': 'Provider unavailable'}]},
    {'review_results': [{'reviewer': 'codex', 'verdict': 'FAIL', 'reason': 'Concrete runtime defect identified',
                         'findings': {'functional_issues': ['Runtime defect']}}]},
    {'check_results': [{'check': 'no_secrets', 'verdict': 'FAIL'}]},
    {'check_results': [{'check': 'ci_green', 'verdict': 'ERROR'}]},
    {'check_results': [{'check': 'ci_green', 'verdict': 'FAIL'},
                       {'check': 'security_patterns', 'verdict': 'FAIL'}]},
])
def test_ci_refresh_never_discards_code_security_or_missing_review_evidence(evidence, changes):
    assert not refresh.ci_is_only_repair_finding(replace(evidence[1], **changes))


def test_exact_head_and_complete_local_review_required(evidence):
    config, verdict = evidence
    assert refresh.ci_failure_for_head(REPO, 7, SHA, config.get_verdict_dir())
    for repo, number, sha in [('other/repo', 7, SHA), (REPO, 8, SHA),
                              (REPO, 7, 'b' * 40), (REPO, 7, 'short')]:
        assert not refresh.ci_failure_for_head(repo, number, sha, config.get_verdict_dir())


@pytest.mark.parametrize('details,expected', [
    (pr(), 'green'),
    (dict(pr(), statusCheckRollup=[{'name': 'unit', 'conclusion': 'FAILURE'}]), 'repair'),
    (dict(pr(), statusCheckRollup=[{'name': 'unit', 'status': 'IN_PROGRESS', 'conclusion': 'SUCCESS'}]), 'pending'),
    (dict(pr(), statusCheckRollup=[]), 'unknown'),
    (dict(pr(), statusCheckRollup=[{'name': 'unit', 'conclusion': 'FAILURE'},
                                   {'name': 'slow', 'status': 'IN_PROGRESS'}]), 'pending'),
    (dict(pr(), headRefOid='b' * 40), 'head-changed'),
    (dict(pr(), state='CLOSED'), 'ineligible'),
])
def test_ci_preflight_reads_one_current_head_and_preserves_artifact(evidence, details, expected):
    config, verdict = evidence
    before = {p.name: p.read_bytes() for p in config.get_verdict_dir().iterdir()}
    with patch.object(refresh.github_reads, 'run', return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps(details))) as gh:
        assert refresh.repair_preflight(REPO, 7, SHA, time.monotonic() + 30) == expected
    assert gh.call_count == 1
    assert gh.call_args.kwargs['timeout'] <= 10
    assert gh.call_args.args[0][:2] == ['pr', 'view']
    assert {p.name: p.read_bytes() for p in config.get_verdict_dir().iterdir()} == before


def test_expired_tick_budget_does_not_read_ci_or_authorize_fix(evidence):
    with patch.object(refresh.github_reads, 'run') as gh:
        assert refresh.repair_preflight(REPO, 7, SHA, time.monotonic() - 1) == 'unknown'
    gh.assert_not_called()


def dispatch_cycle(tmp_path, evidence, state, *, coordination_error=False, historical_failure=False):
    ledger = JobLedger(tmp_path / 'jobs')
    if historical_failure:
        old_job = ledger.create_job(REPO, 7, SHA, 'codex', 'nucbox')
        ledger.fail_job(old_job.job_id, 'Previous real failure', failure_code='transient')
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=ledger,
                                   budget_tracker=MagicMock())
    client = MagicMock()
    client.acquire.return_value = {'sha': SHA}
    client.retry_state.return_value = None
    if coordination_error:
        client.acquire.side_effect = CoordinationError('offline')
    with patch.object(worker, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(worker, 'select_provider', return_value='codex'), \
         patch.object(dispatcher, 'REPOS', [REPO]), \
         patch.object(dispatcher, '_get_open_prs', return_value=[pr()]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(dispatcher, '_has_codex_work_queued', return_value=False), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(refresh, 'observe_current_ci', return_value=(state, pr())), \
         patch.object(refresh.ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker', return_value=0) as spawn:
        decisions = worker.evaluate(live=True)
    return ledger, client, spawn, decisions


@pytest.mark.parametrize('state,reason', [('green', 'review-needed'), ('pending', 'ci-preflight-deferred'),
    ('unknown', 'ci-preflight-deferred'), ('head-changed', 'ci-preflight-deferred')])
def test_ci_only_nonred_does_not_reserve_attempt_or_create_worker(tmp_path, evidence, state, reason):
    ledger, client, spawn, decisions = dispatch_cycle(tmp_path, evidence, state)
    assert decisions[0].extra['reason_code'] == reason
    assert ledger._load_jobs() == []
    client.acquire.assert_not_called()
    client.retry_state.assert_not_called()
    spawn.assert_not_called()
    assert load_verdict('owner', 'repo', 7, SHA, evidence[0]).passed is False


def test_real_red_ci_still_uses_claimed_bounded_repair(tmp_path, evidence):
    ledger, client, spawn, decisions = dispatch_cycle(tmp_path, evidence, 'red')
    assert decisions[0].action == 'dispatch' and spawn.call_count == 1
    assert len(ledger._load_jobs()) == 1
    assert client.acquire.call_args.args[:3] == (REPO, 7, SHA)


def test_obsolete_ci_does_not_reset_existing_failed_attempt_history(tmp_path, evidence):
    ledger, client, spawn, decisions = dispatch_cycle(tmp_path, evidence, 'green', historical_failure=True)
    jobs = ledger._load_jobs()
    assert len(jobs) == 1 and jobs[0].state == 'failed'
    assert jobs[0].failure_code == 'transient'
    assert jobs[0].error == 'Previous real failure'
    client.retry_state.assert_not_called()
    spawn.assert_not_called()


def test_unreviewed_ci_artifact_cannot_spawn_repair_or_refresh(tmp_path, evidence):
    config, verdict = evidence
    verdict.review_results = []
    save_verdict(verdict, config)
    ledger, client, spawn, decisions = dispatch_cycle(tmp_path, evidence, 'green')
    assert decisions[0].reason == 'No Karen findings available'
    assert ledger._load_jobs() == []
    client.acquire.assert_not_called()
    spawn.assert_not_called()
    assert not refresh.refresh_candidate(REPO, pr(), config.get_verdict_dir())


def test_coordination_outage_never_creates_repair_or_pass(tmp_path, evidence):
    ledger, client, spawn, decisions = dispatch_cycle(tmp_path, evidence, 'red', coordination_error=True)
    assert decisions[0].action == 'dispatch_failed'
    assert ledger._load_jobs() == []
    client.retry_state.assert_not_called()
    spawn.assert_not_called()
    assert load_verdict('owner', 'repo', 7, SHA, evidence[0]).passed is False


def test_mixed_code_findings_keep_existing_repair_path_without_extra_ci_read(tmp_path, evidence):
    config, verdict = evidence
    verdict.review_results[0]['verdict'] = 'FAIL'
    verdict.review_results[0]['findings'] = {'functional_issues': ['Concrete defect still needs repair']}
    save_verdict(verdict, config)
    ledger, client, spawn, decisions = dispatch_cycle(tmp_path, evidence, 'green')
    assert decisions[0].action == 'dispatch' and spawn.call_count == 1
    assert len(ledger._load_jobs()) == 1


@pytest.mark.parametrize('state', ['head-changed', 'pending', 'red', 'unknown'])
def test_refresh_queue_checks_current_head_and_ci_under_lease(evidence, state):
    with patch.object(refresh.ClaimClient, 'from_env') as factory, \
         patch.object(refresh, 'Lease'), \
         patch.object(refresh, 'observe_current_ci', return_value=(state, pr())), \
         patch.object(refresh.github_reads, 'run') as gh:
        assert not refresh.queue_refresh(REPO, 7, SHA, evidence[0].get_verdict_dir())
    gh.assert_not_called()


def test_refresh_coordination_outage_does_not_write_or_change_review(evidence):
    with patch.object(refresh.ClaimClient, 'from_env') as factory, \
         patch.object(refresh.github_reads, 'run') as gh, \
         patch.object(backlog, 'ensure_label_exists') as ensure:
        factory.return_value.acquire.side_effect = CoordinationError('offline')
        with pytest.raises(CoordinationError):
            refresh.queue_refresh(REPO, 7, SHA, evidence[0].get_verdict_dir())
    gh.assert_not_called()
    ensure.assert_not_called()


def test_no_reviewed_authority_cannot_acquire_refresh_claim(evidence):
    config, verdict = evidence
    verdict.review_results = []
    save_verdict(verdict, config)
    with patch.object(refresh.ClaimClient, 'from_env') as factory:
        assert not refresh.queue_refresh(REPO, 7, SHA, config.get_verdict_dir())
        assert not refresh.queue_refresh('rupret007/rad-dad-show-night', 7, SHA, config.get_verdict_dir())
    factory.assert_not_called()


def test_poller_requeues_changed_ci_once_and_stops_after_replacement_review(evidence):
    config, verdict = evidence
    details = pr()
    label_writes = []

    def run(args, **kwargs):
        if args[:2] == ['pr', 'view']:
            return SimpleNamespace(returncode=0, stdout=json.dumps(details))
        assert args == ['pr', 'edit', '7', '--repo', REPO, '--add-label', 'needs-karen']
        label_writes.append(args)
        details['labels'].append({'name': 'needs-karen'})
        return SimpleNamespace(returncode=0)

    with patch.object(backlog, '_local_verdict_dir', return_value=config.get_verdict_dir()), \
         patch.object(backlog, 'get_open_prs', side_effect=lambda repo: [details]), \
         patch.object(backlog, 'ensure_label_exists'), \
         patch.object(refresh.ClaimClient, 'from_env'), patch.object(refresh, 'Lease'), \
         patch.object(refresh.github_reads, 'run', side_effect=run):
        assert len(backlog.label_queued_prs([REPO], dry_run=False)) == 1
        assert backlog.label_queued_prs([REPO], dry_run=False) == []
        # A completed new review resolves CI but can remain BORDERLINE. It
        # still cannot merge and must not cause an endless same-head re-review.
        verdict.timestamp = '2026-10-19T02:00:00+00:00'
        verdict.check_results[0]['verdict'] = 'PASS'
        save_verdict(verdict, config)
        details['labels'] = [{'name': 'karen-fail'}]
        assert backlog.label_queued_prs([REPO], dry_run=False) == []
    assert len(label_writes) == 1
    assert load_verdict('owner', 'repo', 7, SHA, config).passed is False


def test_conductor_refresh_requires_fresh_claimed_evidence(evidence, tmp_path):
    config, _ = evidence
    worker = conductor.Conductor(log_dir=tmp_path / 'logs', verdict_dir=config.get_verdict_dir(),
        budget_tracker=MagicMock(), metrics_tracker=MagicMock(), codex_client=MagicMock(), gemini_client=MagicMock())
    candidate = dict(pr(), repo=REPO)
    with patch.object(conductor.ClaimClient, 'from_env'), patch.object(conductor, 'Lease'), \
         patch.object(conductor, '_get_pr_details', return_value=candidate), \
         patch.object(conductor.github_reads, 'run', return_value=SimpleNamespace(returncode=0)) as gh, \
         patch.object(conductor, 'ensure_label_exists'):
        result = worker.deterministic_review_pass(SimpleNamespace(open_prs=[candidate]), live=True)
    assert len(result) == 1 and result[0].executed
    assert gh.call_args.args[0] == ['pr', 'edit', '7', '--repo', REPO, '--add-label', 'needs-karen']


def test_completed_review_replacement_while_claiming_cancels_refresh(evidence, tmp_path):
    config, verdict = evidence
    worker = conductor.Conductor(log_dir=tmp_path / 'logs', verdict_dir=config.get_verdict_dir(),
        budget_tracker=MagicMock(), metrics_tracker=MagicMock(), codex_client=MagicMock(), gemini_client=MagicMock())
    candidate = dict(pr(), repo=REPO)

    def fresh_details(*args):
        verdict.timestamp = '2026-10-19T02:00:00+00:00'
        verdict.check_results[0]['verdict'] = 'PASS'
        save_verdict(verdict, config)
        return candidate

    with patch.object(conductor.ClaimClient, 'from_env'), patch.object(conductor, 'Lease'), \
         patch.object(conductor, '_get_pr_details', side_effect=fresh_details), \
         patch.object(conductor.github_reads, 'run') as gh, \
         patch.object(conductor, 'ensure_label_exists'):
        result = worker.deterministic_review_pass(SimpleNamespace(open_prs=[candidate]), live=True)
    assert len(result) == 1 and not result[0].executed
    assert 'evidence changed' in result[0].execution_result
    gh.assert_not_called()

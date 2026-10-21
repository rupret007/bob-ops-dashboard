"""Repeated immutable context refusals must leave slots for eligible reviews."""
import json
import stat
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import poller
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.gate import GateRunResult
from cluster.control_plane.local_karen.review_queue import Candidate


def item(number=1, head='a' * 40, repo='owner/project'):
    return Candidate(repo, dict(number=number, headRefOid=head, isDraft=False))


def outcome(candidate, *, code='source_metadata', detail='source_file_limit', passed=False):
    owner, repo = candidate.repo.split('/')
    diagnostic = dict(reviewer='codex', chunk=1, source_context_error=code,
                      source_metadata_reason=detail, context_code='source_unavailable')
    verdict = SimpleNamespace(owner=owner, repo=repo, pr_number=candidate.pr['number'],
                              head_sha=candidate.pr['headRefOid'], passed=passed,
                              status='COMPLETE' if passed else 'ERROR', review_results=[],
                              reason='The bounded source context was unavailable')
    return GateRunResult(verdict, 'fixture-receipt' if passed else None, passed,
                         None if passed else 'Context unavailable', [diagnostic])


def cycle(config, candidates, result):
    def metadata(rows, **kwargs):
        for row in rows:
            row.ci, row.lines = 'green', 20
    gate = Mock(side_effect=lambda **kwargs: result(next(
        value for value in candidates if value.pr['number'] == kwargs['pr_number'])))
    with patch.object(poller, 'list_prs_with_label', return_value=[c.pr for c in candidates]), \
         patch.object(poller, 'enrich', side_effect=metadata) as enrich, \
         patch.object(poller, 'run_gate', gate), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch('cluster.control_plane.local_karen.verdict_push.push', return_value={}), \
         patch.object(poller, '_dispatch_fix_if_eligible'):
        results = poller.poll_and_run(['owner/project'], config, post_comment=False,
                                     remove_label_on_complete=False)
    return gate, results, enrich


def test_poller_defers_repeated_context_refusal_and_reaches_unreviewed_work(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    blocked, fresh = item(), item(2)
    for _ in range(2):
        gate, results, _ = cycle(config, [blocked], outcome)
        assert gate.call_count == 1 and len(results) == 1
    gate, results, enrich = cycle(config, [blocked, fresh], outcome)
    assert gate.call_count == 1
    assert gate.call_args.kwargs['pr_number'] == 2
    assert any(row.get('skipped') and row['pr'] == 1 for row in results)
    assert [value.pr['number'] for value in enrich.call_args.args[0]] == [2]
    assert all('passed' not in row for row in results)


def test_temporary_label_absence_does_not_reset_refusal_history(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    cycle(config, [item()], outcome)
    gate, _, _ = cycle(config, [], outcome)
    gate.assert_not_called()
    cycle(config, [item()], outcome)
    gate, records, _ = cycle(config, [item()], outcome)
    gate.assert_not_called()
    assert len(records) == 1 and records[0]['skipped'] is True


def test_two_refusals_persist_privately_without_model_prose_or_paths(tmp_path):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    config = KarenConfig(state_dir=str(tmp_path))
    candidate, result = item(), outcome(item())
    result.context_diagnostics[0].update(reason='never-persist-this-prose', path='private/example.py')
    for count in (1, 2):
        state = ReviewDeferrals(config)
        state.observe(candidate, result)
        assert state.blocked(candidate) == (count == 2)
    loaded = ReviewDeferrals(config)
    assert loaded.blocked(candidate)
    assert stat.S_IMODE(loaded.path.stat().st_mode) == 0o600
    assert 'never-persist-this-prose' not in loaded.path.read_text()
    assert 'private/example.py' not in loaded.path.read_text()


@pytest.mark.parametrize('code', ['source_read', 'source_budget', 'response_size', 'unknown'])
def test_transient_or_unknown_refusals_do_not_park_reviews(tmp_path, code):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    state = ReviewDeferrals(KarenConfig(state_dir=str(tmp_path)))
    for _ in range(3):
        state.observe(item(), outcome(item(), code=code))
    assert not state.blocked(item()) and state.records == {}


@pytest.mark.parametrize('field,value', [('head_sha', 'b' * 40), ('owner', 'other'),
                                       ('repo', 'other'), ('pr_number', 2)])
def test_mismatched_native_identity_is_never_recorded(tmp_path, field, value):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    state = ReviewDeferrals(KarenConfig(state_dir=str(tmp_path)))
    result = outcome(item())
    setattr(result.verdict, field, value)
    for _ in range(2):
        state.observe(item(), result)
    assert not state.records


def test_complete_fail_retains_context_refusal_from_review_scopes(tmp_path):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    state = ReviewDeferrals(KarenConfig(state_dir=str(tmp_path)))
    result = outcome(item())
    result.context_diagnostics = []
    result.error, result.sha_verified = None, True
    result.verdict.status = 'COMPLETE'
    result.verdict.review_results = [dict(reviewer='codex', scope=dict(
        context_incomplete=True, source_context_error='source_identity_or_path',
        context_resolution=dict(code='source_unavailable', source_requests=1)))]
    for _ in range(2):
        state.observe(item(), result)
    assert state.blocked(item())


def test_changed_head_and_reviewer_policy_reopen_without_worker_retry_changes(tmp_path, monkeypatch):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    config = KarenConfig(state_dir=str(tmp_path))
    state = ReviewDeferrals(config)
    for _ in range(2):
        state.observe(item(), outcome(item()))
    assert state.blocked(item()) and not state.blocked(item(head='b' * 40))
    monkeypatch.setenv('STORYPOOL_KAREN_EFFORT', 'a-different-effort')
    assert not ReviewDeferrals(config).blocked(item())
    assert {p.name for p in tmp_path.iterdir()} == {'review-deferrals.json'}


def test_policy_identity_follows_source_and_public_config_only(tmp_path, monkeypatch):
    from cluster.control_plane.local_karen import review_deferrals as module
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'module.py'))
    path = tmp_path / 'module.py'
    path.write_text('initial code')
    config = KarenConfig()
    original = module.policy_identity(config)
    config.gemini_api_key_file = 'different-credential-location'
    assert module.policy_identity(config) == original
    config.minimax_model = 'a-different-model'
    assert module.policy_identity(config) != original
    after_config = module.policy_identity(config)
    path.write_text('changed code')
    assert module.policy_identity(config) != after_config


def test_different_stable_refusal_starts_its_own_count(tmp_path):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    state = ReviewDeferrals(KarenConfig(state_dir=str(tmp_path)))
    state.observe(item(), outcome(item()))
    state.observe(item(), outcome(item(), detail='source_total_limit'))
    assert not state.blocked(item())
    state.observe(item(), outcome(item(), detail='source_total_limit'))
    assert state.blocked(item())


def test_completed_review_clears_refusal_and_partial_scan_keeps_other_repos(tmp_path):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    state = ReviewDeferrals(KarenConfig(state_dir=str(tmp_path)))
    first, other = item(), item(repo='other/project')
    for candidate in (first, other):
        for _ in range(2):
            state.observe(candidate, outcome(candidate))
    state.reconcile([item(head='b' * 40)], {'owner/project'})
    assert first.key not in state.records and state.blocked(other)
    result = outcome(other, passed=True)
    result.context_diagnostics = []
    state.observe(other, result)
    assert not state.records


@pytest.mark.parametrize('raw', ['not-json', '[]', '{"entry":{},"entry":{}}'])
def test_corrupt_state_stops_model_admission_without_recreating_it(tmp_path, raw):
    path = tmp_path / 'review-deferrals.json'
    path.write_text(raw)
    path.chmod(0o600)
    gate, results, _ = cycle(KarenConfig(state_dir=str(tmp_path)), [item()], outcome)
    gate.assert_not_called()
    assert results == [] and path.read_text() == raw


def test_symlink_state_refused_without_reading_or_overwriting_target(tmp_path):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    target = tmp_path / 'other'
    target.write_text('unchanged')
    (tmp_path / 'review-deferrals.json').symlink_to(target)
    with pytest.raises(OSError):
        ReviewDeferrals(KarenConfig(state_dir=str(tmp_path)))
    assert target.read_text() == 'unchanged'


def test_deferral_write_failure_preserves_completed_gate_and_stops_next_admission(tmp_path):
    from cluster.control_plane.local_karen.review_deferrals import ReviewDeferrals
    with patch.object(ReviewDeferrals, 'observe', side_effect=OSError('fixture write refusal')):
        gate, results, _ = cycle(KarenConfig(state_dir=str(tmp_path)), [item(), item(2)],
                                 lambda value: outcome(value, passed=True))
    assert gate.call_count == 1
    assert len(results) == 1 and results[0]['passed'] is True

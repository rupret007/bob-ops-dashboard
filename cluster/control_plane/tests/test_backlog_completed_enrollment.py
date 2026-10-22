"""Completed local exact-head evidence controls enrollment independently of labels."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import backlog, ci_review_refresh
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.verdict import Verdict, save_verdict
from cluster.control_plane.local_karen.verdict_push import completed_for_delivery
from cluster.control_plane.local_karen.verdict_sync import validate_entry

REPO = 'owner/repo'
SHA = 'a' * 40


def pr(head=SHA):
    return {'number': 7, 'title': 'Synthetic enrollment fixture', 'headRefOid': head,
            'isDraft': False, 'labels': [],
            'statusCheckRollup': [{'name': 'unit', 'status': 'COMPLETED', 'conclusion': 'SUCCESS'}]}


def complete(config, passed=True):
    reviews = [{'reviewer': name, 'verdict': 'PASS' if passed else 'FAIL',
                'reason': 'Exact-head implementation and regression evidence reviewed',
                'findings': {'risk_notes': ['Pinned implementation and tests reviewed']},
                'scope': {'chunk': 1, 'total': 1},
                'requested_files': [], 'requested_source_files': []}
               for name in ('gemini', 'minimax')]
    verdict = Verdict('owner', 'repo', 7, SHA, passed,
                      'Complete exact-head independent review with grounded findings',
                      '2026-10-19T01:00:00+00:00', 'chain(gemini,minimax)', 'low', .9,
                      [{'check': 'ci_green', 'verdict': 'PASS', 'reason': 'All checks passed'}], reviews)
    path = save_verdict(verdict, config)
    assert validate_entry(path.name, path.read_text()) is not None
    assert completed_for_delivery(verdict)
    return verdict, path


@pytest.fixture
def harness(tmp_path):
    config = KarenConfig(verdict_dir=str(tmp_path / 'local'), state_dir=str(tmp_path / 'state'))
    config.get_verdict_dir().mkdir()
    boundary = Mock(return_value=SimpleNamespace(returncode=0))
    ensure = Mock()
    def enroll(candidate, directory=config.get_verdict_dir(), repo=REPO):
        with patch.object(backlog, '_local_verdict_dir', return_value=directory), \
             patch.object(backlog, 'get_open_prs', return_value=[candidate]), \
             patch.object(backlog, 'ensure_label_exists', ensure), \
             patch.object(backlog.github_reads, 'run', boundary):
            return backlog.label_queued_prs([repo], dry_run=False)
    return config, boundary, ensure, enroll


@pytest.mark.parametrize('passed', [True, False])
def test_completed_exact_head_queued_without_result_labels_is_not_reenrolled(harness, passed):
    config, boundary, ensure, enroll = harness
    complete(config, passed)
    candidate = pr()
    assert backlog.get_pr_state(candidate) == 'queued'
    assert enroll(candidate) == []
    assert enroll(candidate) == []
    boundary.assert_not_called()
    ensure.assert_not_called()


def test_new_head_still_enrolls_with_old_complete_verdict(harness):
    config, boundary, _, enroll = harness
    complete(config)
    assert [x['pr'] for x in enroll(pr('b' * 40))] == [7]
    boundary.assert_called_once_with(['pr', 'edit', '7', '--repo', REPO,
                                     '--add-label', 'needs-karen'], timeout=30)


@pytest.mark.parametrize('kind', ['incomplete', 'error', 'unreviewed', 'malformed'])
def test_noncomplete_or_unreviewed_artifact_cannot_suppress_enrollment(harness, kind):
    config, boundary, _, enroll = harness
    verdict, path = complete(config)
    if kind == 'incomplete':
        verdict.review_results[0]['scope']['context_incomplete'] = True
    elif kind == 'error':
        verdict.status = 'ERROR'
    elif kind == 'unreviewed':
        verdict.review_results = []
    path.write_text('{}' if kind == 'malformed' else verdict.to_json())
    assert [x['pr'] for x in enroll(pr())] == [7]
    assert boundary.call_count == 1


@pytest.mark.parametrize('head', ['', 'a' * 7, 'g' * 40])
def test_unknown_identity_is_not_mistaken_for_completed_authority(harness, head):
    config, boundary, _, enroll = harness
    complete(config)
    assert len(enroll(pr(head))) == 1
    assert boundary.call_count == 1


@pytest.mark.parametrize('repo', ['owner/extra/repo', 'owner//repo', '/repo', 'owner/',
                                  'owner/with space', 'owner/repo\n', 'owner'])
def test_malformed_repo_never_consults_canonical_completion_storage(harness, repo):
    config, _, _, enroll = harness
    complete(config)
    with patch('cluster.control_plane.local_karen.verdict.load_verdict',
               side_effect=AssertionError('invalid identity reached canonical storage')) as completion, \
         patch.object(ci_review_refresh, 'load_verdict',
                      side_effect=AssertionError('invalid identity reached refresh storage')) as refresh:
        enroll(pr(), repo=repo)
    completion.assert_not_called()
    refresh.assert_not_called()


@pytest.mark.parametrize('number', [True, False, 0, -1, 1.5, '7', None])
def test_malformed_pr_number_never_consults_canonical_completion_storage(harness, number):
    config, _, _, enroll = harness
    complete(config)
    candidate = dict(pr(), number=number)
    with patch('cluster.control_plane.local_karen.verdict.load_verdict',
               side_effect=AssertionError('invalid identity reached canonical storage')) as completion, \
         patch.object(ci_review_refresh, 'load_verdict',
                      side_effect=AssertionError('invalid identity reached refresh storage')) as refresh:
        enroll(candidate)
    completion.assert_not_called()
    refresh.assert_not_called()


def test_no_local_artifact_still_enrolls_even_with_peer_only_completion(harness, tmp_path):
    config, boundary, _, enroll = harness
    peer = KarenConfig(verdict_dir=str(tmp_path / 'peer'), state_dir=str(tmp_path / 'peer-state'))
    complete(peer)
    assert len(enroll(pr())) == 1
    assert boundary.call_count == 1


def test_unlabeled_ci_only_fail_with_changed_green_ci_keeps_claimed_refresh(harness):
    config, boundary, ensure, enroll = harness
    verdict, path = complete(config)
    verdict.passed = False
    verdict.check_results[0]['verdict'] = 'FAIL'
    verdict.reason = 'Completed review blocked only by previously incomplete CI'
    # Existing pure refresh policy recognizes CI-only reviewed evidence.
    path.unlink()
    save_verdict(verdict, config)
    assert ci_review_refresh.refresh_candidate(REPO, pr(), config.get_verdict_dir())
    with patch.object(ci_review_refresh, 'queue_refresh', return_value=True) as refresh:
        assert len(enroll(pr())) == 1
    refresh.assert_called_once_with(REPO, 7, SHA, config.get_verdict_dir())
    boundary.assert_not_called()
    ensure.assert_not_called()

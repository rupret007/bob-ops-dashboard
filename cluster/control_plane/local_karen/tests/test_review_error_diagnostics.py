"""Unpublished errors retain bounded context structure, never model/source data.

Real gate, chunk planning and reviewer adapters use synthetic inputs and offline
transports. No model, GitHub, broker, credentials or installed state is accessed.
"""
from dataclasses import asdict
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import gate, reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.review_diagnostics import (
    MAX_DIAGNOSTICS, sanitize_context_diagnostics,
)
from cluster.control_plane.local_karen.source_context import PinnedSourceContext, SourceUnavailable
from cluster.control_plane.local_karen.verdict import Verdict, create_verdict, save_verdict


SENTINEL = 'PRIVATE_UNTRUSTED_DIAGNOSTIC_SENTINEL'


def section(path):
    header = 'diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -0,0 +1,100 @@\n'.format(path)
    padding = 8000 - len(header) - 200
    width, extra = divmod(padding, 100)
    return header + ''.join('+' + 'x' * (width + (i < extra)) + '\n' for i in range(100))


def review_answer(verdict):
    return json.dumps({'verdict': verdict, 'reason': 'Independently reviewed the synthetic change',
                       'requested_files': [], 'requested_source_files': [],
                       'findings': {'risk_notes': ['Checked synthetic supplied scope']}})


class OfflineAdapters:
    """Real Codex adapter, synthetic CLI output and no provider/network access."""

    def __init__(self, tmp_path, primary='BORDERLINE'):
        self.config = KarenConfig(codex_host='local', state_dir=str(tmp_path))
        self.primary = primary
        self.prompts = {'codex': [], 'minimax': []}
        self.stack = ExitStack()

    def __enter__(self):
        patches = [patch.object(r, 'get_available_reviewers', return_value=[('codex', r.CodexReviewer())]),
                   patch.object(r.CodexReviewer, '_execution_target', return_value='local'),
                   patch.object(r.CodexReviewer, '_get_weekly_usage_pct', return_value=21.0),
                   patch.object(r.subprocess, 'run', side_effect=self.cli),
                   patch.object(r.MiniMaxReviewer, 'is_available', side_effect=self.unexpected_second_judge),
                   patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden in offline fixture'))]
        for item in patches:
            self.stack.enter_context(item)
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def unexpected_second_judge(self, *args):
        self.prompts['minimax'].append('Unexpected second-judge attempt')
        raise AssertionError('Incomplete primary must not request a second judge')

    def reply(self):
        return review_answer(self.primary)

    def cli(self, args, **kwargs):
        assert args[:2] == ['codex', 'exec']
        assert args[args.index('--sandbox') + 1] == 'read-only'
        self.prompts['codex'].append(kwargs['input'])
        Path(args[args.index('-o') + 1]).write_text(self.reply())
        return subprocess.CompletedProcess(args, 0, '', '')


def pr_info():
    paths = ['src/main.py', 'test_main.py']
    diff = ''.join(section(path) for path in paths)
    return PRInfo('owner', 'repo', 62, 'a' * 40, 'Synthetic two-file change', 'author',
                  False, diff, {'tests': 'success'}, paths, [])


class MissingSourceAdapters(OfflineAdapters):
    def reply(self):
        response = json.loads(review_answer('BORDERLINE'))
        response.update(reason='Need the pinned implementation before deciding this integration',
                        requested_source_files=['src/unchanged.py'])
        return json.dumps(response)


def error_result(scope=None):
    return r.ReviewResult('codex', r.ReviewVerdict.ERROR, 'Review requires more context',
                          raw_response=SENTINEL, scope=scope,
                          requested_files=[SENTINEL], requested_source_files=[SENTINEL])


def run_error(tmp_path, final, reviews=()):
    pr = pr_info()
    with patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(gate, 'run_review_chain', return_value=(final, list(reviews))), \
         patch.object(gate, 'save_verdict') as save, \
         patch.object(gate, 'verify_sha_current') as verify, \
         patch('cluster.control_plane.local_karen.github_client.post_verdict_comment') as comment:
        result = gate.run_gate(pr.owner, pr.repo, pr.number,
                               KarenConfig(verdict_dir=str(tmp_path / 'verdicts')),
                               post_comment=True, expected_sha=pr.head_sha)
    save.assert_not_called(); verify.assert_not_called(); comment.assert_not_called()
    return result


def test_actual_chunked_source_error_survives_gate_without_publication(tmp_path):
    pr = pr_info()
    with MissingSourceAdapters(tmp_path) as adapters, \
         patch.object(PinnedSourceContext, 'fetch', side_effect=SourceUnavailable('source_metadata')) as fetch, \
         patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(gate, 'save_verdict') as save, \
         patch.object(gate, 'verify_sha_current') as verify, \
         patch('cluster.control_plane.local_karen.github_client.post_verdict_comment') as comment:
        result = gate.run_gate(pr.owner, pr.repo, pr.number, adapters.config,
                               post_comment=True, expected_sha=pr.head_sha)

    assert len(adapters.prompts['codex']) == fetch.call_count == 2
    assert adapters.prompts['minimax'] == []
    assert result.verdict.status == result.verdict.verdict_type == 'ERROR'
    assert not result.verdict.passed and not result.sha_verified and result.verdict_path is None
    assert result.verdict.review_results == []
    assert result.error == ('Review error: Chunk 1/2: Reviewer codex rejected: Context or evidence incomplete; '
                            'retry required: Need the pinned implementation before deciding this integration')
    assert result.verdict.reason == 'Fail closed: ' + result.error
    diagnostics = result.verdict.to_dict()['context_diagnostics']
    assert [item['chunk'] for item in diagnostics] == [1, 2]
    for item in diagnostics:
        assert item['total'] == 2
        assert item['source_context_error'] == 'source_metadata'
        assert item['context_resolution'] == {'code': 'source_unavailable', 'changed_requests': 0, 'source_requests': 1}
        assert item['initial_context']['code'] == 'supplied'
        assert item['initial_context']['changed_files'] == 1
        assert item['initial_context']['raw_size'] == 8000
        assert 8000 < item['initial_context']['serialized_size'] <= 48000
        # Canonical077 does not emit db1's two extra planning counts. Preserve
        # exactly the available server metadata instead of inventing evidence.
        assert set(item['initial_context']) == {'code', 'changed_files', 'raw_size', 'serialized_size'}
        assert 'chunk_characters' not in item['initial_context']
        assert 'pr_changed_files' not in item['initial_context']
    assert 'src/unchanged.py' not in json.dumps(diagnostics)
    assert 'source' not in {key for item in diagnostics for key in item}
    save.assert_not_called(); verify.assert_not_called(); comment.assert_not_called()
    with pytest.raises(ValueError, match='error or unknown reason'):
        save_verdict(result.verdict, adapters.config)
    assert Verdict.from_json(result.verdict.to_json()).to_dict() == result.verdict.to_dict()


def test_actual_chain_error_keeps_attempt_context_when_aggregate_has_none(tmp_path):
    pr = pr_info()
    with OfflineAdapters(tmp_path, primary='INVALID') as adapters, \
         patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(gate, 'save_verdict') as save:
        result = gate.run_gate(pr.owner, pr.repo, pr.number, adapters.config, expected_sha=pr.head_sha)
    assert 'No reviewers provided valid verdicts' in result.error
    assert len(adapters.prompts['codex']) == 2 and not adapters.prompts['minimax']
    diagnostics = result.verdict.to_dict()['context_diagnostics']
    # The aggregate only knows its chunk; the actual failed attempts still know
    # whether automatic context was supplied and no explicit request was made.
    attempts = [item for item in diagnostics if 'context_resolution' in item]
    assert len(attempts) == 2
    assert [item['chunk'] for item in attempts] == [1, 2]
    assert all(item['context_resolution'] == {'code': 'not_requested', 'changed_requests': 0,
                                             'source_requests': 0} for item in attempts)
    assert result.verdict.status == 'ERROR' and result.verdict_path is None and not result.sha_verified
    save.assert_not_called()


def test_malformed_scope_values_do_not_leak_model_paths_or_source(tmp_path):
    scope = {'chunk': 1, 'total': 2, 'file_header': SENTINEL, 'reason': SENTINEL,
             'requested_files': [SENTINEL], 'requested_source_files': [SENTINEL],
             'context_attempts': [{'reason': SENTINEL}], 'source_context': [{'source': SENTINEL}],
             'initial_context': {'code': 'supplied', 'changed_files': 1, 'raw_size': SENTINEL,
                                 'serialized_size': True, 'chunk_characters': -1,
                                 'pr_changed_files': 10 ** 100, 'source': SENTINEL},
             'context_resolution': {'code': 'source_unavailable', 'changed_requests': 0,
                                    'source_requests': 1, 'path': SENTINEL},
             'source_context_error': 'source_private'}
    result = run_error(tmp_path, error_result(scope), [error_result(scope)])
    diagnostics = result.verdict.to_dict()['context_diagnostics']
    assert diagnostics == [{'chunk': 1, 'total': 2,
                            'initial_context': {'code': 'supplied', 'changed_files': 1},
                            'context_resolution': {'code': 'source_unavailable', 'changed_requests': 0, 'source_requests': 1},
                            'source_context_error': 'source_private'}]
    assert SENTINEL not in result.verdict.to_json()
    assert result.verdict.review_results == []


@pytest.mark.parametrize('invalid', [None, SENTINEL, True, -1, 10 ** 100, 1.0, float('nan'), [], {'source': SENTINEL}])
def test_invalid_allowed_field_values_are_omitted(tmp_path, invalid):
    scope = {'chunk': invalid, 'total': invalid, 'source_context_error': invalid,
             'initial_context': {key: invalid for key in ('code', 'changed_files', 'raw_size',
                                  'serialized_size', 'chunk_characters', 'pr_changed_files')},
             'context_resolution': {key: invalid for key in ('code', 'changed_requests', 'source_requests')}}
    result = run_error(tmp_path, error_result(scope))
    assert 'context_diagnostics' not in result.verdict.to_dict()
    assert result.verdict.status == 'ERROR' and result.verdict_path is None


@pytest.mark.parametrize('scope', [None, SENTINEL, [], True, {'chunk': 3, 'total': 2},
                                  {'initial_context': SENTINEL, 'context_resolution': [SENTINEL],
                                   'source_context_error': SENTINEL}])
def test_invalid_scope_shapes_are_not_serialized(tmp_path, scope):
    assert 'context_diagnostics' not in run_error(tmp_path, error_result(scope)).verdict.to_dict()


def test_diagnostics_are_bounded_and_terminal_outcome_is_first(tmp_path):
    final = error_result({'chunk': 80, 'total': 80, 'source_context_error': 'source_budget'})
    reviews = [error_result({'chunk': i, 'total': 80, 'source_context_error': 'source_read'}) for i in range(1, 81)]
    result = run_error(tmp_path, final, reviews)
    diagnostics = result.verdict.to_dict()['context_diagnostics']
    assert len(diagnostics) == MAX_DIAGNOSTICS
    assert diagnostics[0] == {'chunk': 80, 'total': 80, 'source_context_error': 'source_budget'}
    assert len(json.dumps(diagnostics)) < 2000


@pytest.mark.parametrize('code', sorted(SourceUnavailable.CODES | {'source_unavailable'}))
def test_all_fixed_source_failure_codes_remain_distinguishable(code):
    assert sanitize_context_diagnostics([{'source_context_error': code}]) == [{'source_context_error': code}]


def test_complete_and_legacy_verdict_serialization_are_unchanged():
    pr = pr_info()
    review = r.ReviewResult('codex', r.ReviewVerdict.PASS, 'Reviewed synthetic implementation',
                            findings=r.ReviewFindings(risk_notes=['Checked integration']))
    verdict = create_verdict(pr, [], [review], True, review.reason, review.reviewer)
    legacy = asdict(verdict); legacy.pop('context_diagnostics')
    assert verdict.to_dict() == legacy
    assert Verdict.from_dict(legacy).to_dict() == legacy
    # Diagnostics cannot accidentally expand the COMPLETE signing/transport schema.
    verdict.context_diagnostics = [{'source_context_error': 'source_budget'}]
    assert verdict.to_dict() == legacy
    assert Verdict.from_json(json.dumps(legacy)).to_json() == verdict.to_json()


@pytest.mark.parametrize('status', ['ERROR', 'COMPLETE'])
def test_serialization_excludes_unvalidated_diagnostics_before_deepcopy(status):
    pr = pr_info()
    verdict = create_verdict(pr, [], [], False, 'Synthetic outcome', 'gate_runner')
    verdict.status = status
    # Direct callers may supply malformed optional data. Projection must happen
    # before generic dataclass serialization can traverse arbitrary nested data.
    scope = {'source_context_error': 'source_read'}
    scope['untrusted_source'] = scope
    verdict.context_diagnostics = [scope]
    data = verdict.to_dict()
    if status == 'ERROR':
        assert data['context_diagnostics'] == [{'source_context_error': 'source_read'}]
    else:
        assert 'context_diagnostics' not in data

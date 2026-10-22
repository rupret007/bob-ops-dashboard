"""Actual gate-to-CLI errors retain only upstream-projected operational codes.

All review/GitHub/config/broker boundaries are offline fixtures. No credentials,
models, comments, signed artifacts or installed services are used.
"""
import json
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import cli, gate
from cluster.control_plane.local_karen.checks import CheckResult, CheckVerdict, PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.reviewers import ReviewResult, ReviewVerdict
from cluster.control_plane.local_karen.verdict import Verdict

SENTINEL = 'SYNTHETIC_PRIVATE_MODEL_PATH_VALUE'
SAFE = {'reviewer': 'minimax', 'chunk': 2, 'source_context_error': 'source_metadata',
        'context_code': 'source_unavailable', 'changed_requests': 0, 'source_requests': 1}
FIXED_ERROR = 'Review did not produce a verified artifact'
UNCHANGED = object()


def test_cli_does_not_lose_safe_gate_run_result_diagnostics(tmp_path, capsys):
    verdict = Verdict('owner', 'repo', 7, 'a' * 40, False, FIXED_ERROR,
                      '2026-10-19T00:00:00+00:00', 'gate_runner', 'unknown', 1.0,
                      [], [], status='ERROR')
    result = gate.GateRunResult(verdict, None, False, error=FIXED_ERROR,
                               context_diagnostics=[SAFE])
    with patch('sys.argv', ['karen-gate', 'owner/repo', '7', '--json']), \
         patch.object(cli.KarenConfig, 'load', return_value=KarenConfig(state_dir=str(tmp_path))), \
         patch.object(cli, 'run_gate', return_value=result):
        assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)['context_diagnostics'] == [SAFE]


def error_cli(tmp_path, capsys, failure='review', diagnostics=UNCHANGED):
    pr = PRInfo('owner', 'repo', 7, 'a' * 40, 'Synthetic fixture', 'writer', False,
                '+fixture', {}, [], [])
    scope = {'chunk': 2, 'source_context_error': 'source_metadata',
             'context_resolution': {'code': 'source_unavailable', 'changed_requests': 0, 'source_requests': 1},
             'path': SENTINEL, 'source': SENTINEL, 'reason': SENTINEL}
    review = ReviewResult('minimax', ReviewVerdict.BORDERLINE, SENTINEL,
                          raw_response=SENTINEL, requested_source_files=[SENTINEL],
                          findings={'risk_notes': [SENTINEL]}, scope=scope)
    reviews = [] if failure == 'empty' else [review]
    if failure == 'malformed':
        review.scope = SENTINEL
    combined = ReviewResult('chain', ReviewVerdict.ERROR, SENTINEL, scope={'context_incomplete': True})
    checks = [CheckResult('operational_fixture', CheckVerdict.ERROR, SENTINEL, SENTINEL)] if failure == 'check' else []
    config = KarenConfig(state_dir=str(tmp_path / 'state'), verdict_dir=str(tmp_path / 'verdicts'))
    actual_run = gate.run_gate
    observed = []

    def run(*args, **kwargs):
        result = actual_run(*args, **kwargs)
        if diagnostics is not UNCHANGED:
            result.context_diagnostics = diagnostics
        observed.append(result)
        return result

    with patch('sys.argv', ['karen-gate', 'owner/repo', '7', '--json', '--comment']), \
         patch.object(cli.KarenConfig, 'load', return_value=config), \
         patch.object(cli, 'run_gate', side_effect=run), \
         patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'run_all_checks', return_value=checks), \
         patch.object(gate, 'run_review_chain', return_value=(combined, reviews)) as model, \
         patch.object(gate, '_metrics') as metrics, \
         patch.object(gate, 'save_verdict') as save, \
         patch.object(gate, 'verify_sha_current') as verify, \
         patch('cluster.control_plane.local_karen.github_client.post_verdict_comment') as comment:
        status = cli.main()
    output = capsys.readouterr().out
    assert status == 1
    result, = observed
    assert result.verdict.status == 'ERROR' and not result.verdict.passed
    assert not result.sha_verified and result.verdict_path is None
    assert SENTINEL in result.error and SENTINEL in result.verdict.reason  # Private API is unchanged.
    assert 'context_diagnostics' not in result.verdict.to_dict()
    save.assert_not_called(); verify.assert_not_called(); comment.assert_not_called()
    metrics.record_karen_review.assert_not_called()
    assert not list(tmp_path.rglob('*.json'))
    if failure == 'check':
        model.assert_not_called()
        assert result.verdict.check_results[0]['details'] == SENTINEL
    data = json.loads(output)
    assert SENTINEL not in output
    assert data['status'] == 'ERROR' and data['passed'] is False
    assert data['reason'] == FIXED_ERROR
    assert data['check_results'] == data['review_results'] == []
    assert data['head_sha'] == 'a' * 40
    return data


@pytest.mark.parametrize('failure', ['review', 'empty', 'malformed', 'check'])
def test_actual_gate_error_cli_projects_safe_context_and_masks_free_text(tmp_path, capsys, failure):
    data = error_cli(tmp_path, capsys, failure)
    if failure == 'review':
        assert data['context_diagnostics'] == [SAFE]
    else:
        assert 'context_diagnostics' not in data


@pytest.mark.parametrize('diagnostics', [None, SENTINEL, {}, [SENTINEL]])
def test_cli_resanitizes_malformed_diagnostic_boundary(tmp_path, capsys, diagnostics):
    assert 'context_diagnostics' not in error_cli(tmp_path, capsys, diagnostics=diagnostics)


def test_cli_resanitizes_unknown_codes_paths_and_cyclic_untrusted_fields(tmp_path, capsys):
    record = dict(SAFE, context_code=SENTINEL, source_context_error=SENTINEL,
                  changed_requests=True, source_requests=999, chunk=-1, path=SENTINEL,
                  reason=SENTINEL, raw_response=SENTINEL, passed=True, status='COMPLETE')
    record['untrusted_cycle'] = record
    data = error_cli(tmp_path, capsys, diagnostics=[record])
    assert data['context_diagnostics'] == [{'reviewer': 'minimax',
             'context_code': 'unknown', 'source_context_error': 'unknown'}]


@pytest.mark.parametrize('passed', [False, True])
def test_complete_cli_json_remains_byte_identical_without_diagnostic_fields(tmp_path, capsys, passed):
    verdict = Verdict('owner', 'repo', 7, 'a' * 40, passed, 'Original complete reason',
                      '2026-10-19T00:00:00+00:00', 'minimax', 'low', 0.9,
                      [{'check': 'fixture', 'reason': 'Original check'}],
                      [{'reviewer': 'minimax', 'reason': 'Original review'}])
    expected = verdict.to_json() + '\n'
    result = gate.GateRunResult(verdict, 'synthetic-artifact-path', True,
                               context_diagnostics=[dict(SAFE, path=SENTINEL)])
    config = KarenConfig(state_dir=str(tmp_path))
    with patch('sys.argv', ['karen-gate', 'owner/repo', '7', '--json']), \
         patch.object(cli.KarenConfig, 'load', return_value=config), \
         patch.object(cli, 'run_gate', return_value=result):
        assert cli.main() == (0 if passed else 1)
    assert capsys.readouterr().out == expected
    assert 'context_diagnostics' not in verdict.to_dict()

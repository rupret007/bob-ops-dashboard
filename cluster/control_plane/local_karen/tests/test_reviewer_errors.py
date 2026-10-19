"""Transient formatting errors retry once; real votes and truncation never do."""
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig


@pytest.fixture
def pr():
    return PRInfo('owner', 'repo', 1, 'a' * 40, 'Title', 'author', False,
                  '+complete pinned change', {}, ['file.py'], [])


def verdict(name='PASS', **changes):
    data = dict(verdict=name, reason='Inspected the complete pinned implementation',
                confidence=.8, findings=r.ReviewFindings(risk_notes=['Checked validation']).to_dict())
    data.update(changes)
    return json.dumps(data)


def response(content, finish='stop', status=0):
    raw = json.dumps({'base_resp': {'status_code': status}, 'choices': [
        {'finish_reason': finish, 'message': {'content': content}}]}).encode()
    return raw_response(raw)


def raw_response(raw):
    value = Mock()
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    value.read.return_value = raw
    return value


def review(pr, responses, checkpoint_error=None):
    primary = r.ReviewResult('codex', r.ReviewVerdict.PASS, 'Reviewed complete pinned diff')
    with patch.object(r.MiniMaxReviewer, '_load_key', return_value='fixture-key'), \
         patch.object(r.MiniMaxReviewer, '_get_key_source', return_value='fixture'), \
         patch('urllib.request.urlopen', side_effect=responses) as request, \
         patch('cluster.control_plane.local_karen.gate.review_checkpoint',
               side_effect=checkpoint_error) as checkpoint:
        result = r.MiniMaxReviewer().review_as_second_judge(pr, primary, KarenConfig())
    return result, request, checkpoint


@pytest.mark.parametrize('bad', ['', ' \n ', None, 3, {}, [], 'private-malformed-value', '{}', '[]',
                               verdict(findings={}), verdict(requested_files=None),
                               '<think>private-reasoning</think>' + verdict()])
def test_completed_malformed_or_empty_review_retries_same_pinned_request_once(pr, bad):
    result, request, checkpoint = review(pr, [response(bad), response(verdict())])
    assert result.passed
    assert request.call_count == 2
    checkpoint.assert_called_once_with()
    payloads = [call.args[0].data for call in request.call_args_list]
    assert payloads[0] == payloads[1]
    assert pr.head_sha in json.loads(payloads[1])['messages'][0]['content']
    assert pr.diff in json.loads(payloads[1])['messages'][0]['content']
    assert 'private-' not in json.dumps(result.to_dict())


@pytest.mark.parametrize('raw', [b'{truncated envelope', b'\xff', b'[]', b'{}',
                               b'{"choices": []}'])
def test_malformed_response_envelope_has_one_bounded_retry(pr, raw):
    result, request, _ = review(pr, [raw_response(raw), response(verdict())])
    assert result.passed and request.call_count == 2


def test_repeated_malformed_review_stays_error_after_two_requests(pr):
    result, request, _ = review(pr, [response('private-malformed-value')] * 2 + [response(verdict())])
    assert result.verdict == r.ReviewVerdict.ERROR
    assert request.call_count == 2
    assert 'private-malformed-value' not in json.dumps(result.to_dict())


@pytest.mark.parametrize('body,leading', [
    ('', 'empty'), ('  \n', 'empty'), ('{"private-response-value":', 'json-object'),
    ('```json\nprivate-response-value', 'code-fence'),
    ('<think>private-response-value</think>', 'reasoning-tag'),
    ('private-response-value', 'other'),
])
def test_malformed_metadata_exposes_only_length_and_static_leading_class(pr, caplog, body, leading):
    result, request, _ = review(pr, [response(body)] * 3)
    assert result.verdict == r.ReviewVerdict.ERROR
    assert request.call_count == 2
    assert result.scope['response_characters'] == len(body)
    assert result.scope['leading_class'] == leading
    assert 'leading_class=' + leading in caplog.text
    assert 'private-response-value' not in caplog.text + json.dumps(result.to_dict())


def test_retry_checkpoint_stop_prevents_second_request(pr, caplog):
    result, request, checkpoint = review(pr, [response('malformed'), response(verdict())],
                                        RuntimeError('private-stop-value'))
    assert result.verdict == r.ReviewVerdict.ERROR
    assert request.call_count == 1
    checkpoint.assert_called_once_with()
    assert 'private-stop-value' not in caplog.text + json.dumps(result.to_dict())


def test_malformed_structured_values_are_not_retained_or_logged(pr, caplog):
    bad = verdict('private-response-value', reason='private-response-value-long-enough')
    result, request, _ = review(pr, [response(bad)] * 3)
    assert result.verdict == r.ReviewVerdict.ERROR
    assert request.call_count == 2
    assert result.raw_response == ''
    assert result.scope == {'response_characters': len(bad), 'leading_class': 'json-object'}
    assert 'private-response-value' not in caplog.text + json.dumps(result.to_dict())


@pytest.mark.parametrize('vote', ['PASS', 'FAIL', 'BORDERLINE'])
def test_real_vote_is_terminal_and_never_retried_until_pass(pr, vote):
    result, request, checkpoint = review(pr, [response(verdict(vote)), response(verdict())])
    assert result.verdict == r.ReviewVerdict[vote]
    assert request.call_count == 1
    checkpoint.assert_not_called()


def test_pass_reporting_blocking_defect_is_not_retried_or_approved(pr):
    findings = r.ReviewFindings(functional_issues=['file.py:1 fails validation']).to_dict()
    result, request, _ = review(pr, [response(verdict(findings=findings)), response(verdict())])
    assert result.verdict == r.ReviewVerdict.BORDERLINE
    assert request.call_count == 1


@pytest.mark.parametrize('finish,status', [('length', 0), ('stop', 1004)])
def test_truncated_or_rejected_response_cannot_be_accepted_or_retried(pr, finish, status):
    result, request, checkpoint = review(pr, [response(verdict(), finish, status), response(verdict())])
    assert result.verdict == r.ReviewVerdict.ERROR
    assert request.call_count == 1
    checkpoint.assert_not_called()


def test_transport_failure_never_logs_or_returns_exception_values(pr, caplog):
    result, request, _ = review(pr, [RuntimeError('private-credential-value')])
    assert result.verdict == r.ReviewVerdict.ERROR
    assert request.call_count == 1
    assert 'private-credential-value' not in caplog.text + json.dumps(result.to_dict())


@pytest.mark.parametrize('stderr,category', [
    ('failed to initialize in-process app-server', 'runtime_initialization'),
    ('unexpected argument', 'arguments'), ('401 Unauthorized', 'authentication'),
    ('rate limit exceeded', 'quota'), ('context window exceeded', 'context_limit'),
    ('invalid value in configuration', 'configuration'), ('model not found', 'model_access'),
    ('operation not permitted', 'permissions'), ('stream disconnected', 'transport'),
    ('unfamiliar error', 'unclassified'), ('', 'empty_stderr'),
])
def test_codex_failure_diagnostics_are_static_and_never_accept_output(pr, stderr, category):
    def execute(args, **kwargs):
        Path(args[args.index('-o') + 1]).write_text(verdict())
        return subprocess.CompletedProcess(args, 1, 'private-stdout-value',
                                           stderr + (' private-stderr-value' if stderr else ''))
    with patch.object(r, 'read_codex_quota', return_value={'weekly_pct': 21}), \
         patch.object(r.subprocess, 'run', side_effect=execute):
        result = r.CodexReviewer().review(pr, r.ReviewTier.LOW, KarenConfig(codex_host='local'))
    assert result.verdict == r.ReviewVerdict.ERROR
    assert result.raw_response == ''
    assert result.scope['failure_category'] == category
    assert result.scope['exit_code'] == 1
    assert result.scope['execution'] == 'local'
    assert 'private-' not in json.dumps(result.to_dict())

"""Documented function transport submits review data and never executes tools."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from .test_reviewer_errors import response, review, verdict, raw_response


@pytest.fixture
def pr():
    return PRInfo('owner', 'repo', 1, 'a' * 40, 'private-pr-title', 'author', False,
                  '+private-pinned-diff', {}, ['file.py'], [], 'private-pr-description')


def call(arguments=..., **changes):
    value = {'id': 'private-call-value', 'type': 'function', 'function': {
        'name': 'submit_review', 'arguments': verdict() if arguments is ... else arguments}}
    value.update(changes)
    return value


def tool_response(arguments=..., content=None, finish='tool_calls', **fields):
    return response(content, finish, tool_calls=[call(arguments)], **fields)


def test_payload_has_one_static_data_only_schema_and_no_unsupported_forcing(pr):
    result, request, _ = review(pr, [tool_response()])
    assert result.passed and request.call_count == 1
    payload = json.loads(request.call_args.args[0].data)
    assert payload['tools'] == [r.MINIMAX_REVIEW_TOOL]
    assert payload['tools'][0]['type'] == 'function'
    function = payload['tools'][0]['function']
    assert function['name'] == 'submit_review' and 'data only' in function['description']
    schema = function['parameters']
    required = {'verdict', 'reason', 'confidence', 'requested_files', 'requested_source_files', 'findings'}
    assert set(schema['properties']) == set(schema['required']) == required
    assert schema['type'] == 'object' and schema['additionalProperties'] is False
    fields = schema['properties']
    assert fields['verdict'] == {'type': 'string', 'enum': ['PASS', 'FAIL', 'BORDERLINE']}
    assert fields['reason'] == {'type': 'string', 'minLength': r.MIN_REASON_LENGTH}
    assert fields['confidence'] == {'type': 'number', 'minimum': 0, 'maximum': 1}
    for name in ('requested_files', 'requested_source_files'):
        assert fields[name] == {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4}
    findings = fields['findings']
    assert set(findings['properties']) == set(findings['required']) == set(r.ReviewFindings.__dataclass_fields__)
    assert all(value == {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}
               for value in findings['properties'].values())
    assert findings['additionalProperties'] is False
    assert not {'tool_choice', 'functions', 'function_call', 'response_format'} & payload.keys()
    assert payload['max_completion_tokens'] == 16384 and payload['temperature'] == .1
    assert payload['reasoning_split'] is True
    assert [m['role'] for m in payload['messages']] == ['system', 'user']
    static = json.dumps(payload['tools']) + payload['messages'][0]['content']
    assert 'private-' not in static and pr.head_sha not in static
    assert pr.diff in payload['messages'][1]['content']


@pytest.mark.parametrize('vote', ['PASS', 'FAIL', 'BORDERLINE'])
@pytest.mark.parametrize('content', [None, '', ' \n '])
def test_complete_single_function_vote_uses_unchanged_parser_and_is_terminal(pr, caplog, vote, content):
    arguments = verdict(vote, requested_files=[], requested_source_files=[])
    reviewer = r.MiniMaxReviewer()
    with patch.object(r.MiniMaxReviewer, '_parse_second_judge_verdict',
                      wraps=reviewer._parse_second_judge_verdict) as parser:
        result, request, checkpoint = review(pr, [tool_response(arguments, content,
            reasoning_content='private-reasoning-value'), tool_response()])
    assert result.verdict == r.ReviewVerdict[vote] and request.call_count == 1
    checkpoint.assert_not_called()
    assert parser.call_args.args[0] == arguments and result.raw_response == arguments
    assert 'private-call-value' not in caplog.text + json.dumps(result.to_dict())
    assert 'private-reasoning-value' not in caplog.text + json.dumps(result.to_dict())


@pytest.mark.parametrize('content', [None, 'private-sibling-context-note'])
def test_structured_context_requests_survive_function_transport_without_approval(pr, content):
    arguments = verdict('BORDERLINE', requested_files=['file.py'], requested_source_files=['impl.py'])
    result, request, checkpoint = review(pr, [tool_response(arguments, content)])
    assert result.verdict == r.ReviewVerdict.BORDERLINE and not result.passed
    assert result.requested_files == ['file.py'] and result.requested_source_files == ['impl.py']
    assert request.call_count == 1
    checkpoint.assert_not_called()


BAD_CALLS = [
    None, [], [call(), call()], {}, 'private-call-value', [{}],
    [call(type='private-unknown-type')], [call(id=None)], [call(id=3)], [call(id=' ')],
    [call(function=None)], [call(function={})],
    [call(function={'name': 'private-unknown-function', 'arguments': verdict()})],
    [call(function={'name': 'SUBMIT_REVIEW', 'arguments': verdict()})],
    [call(arguments={})], [call(arguments=[])], [call(arguments=None)], [call(arguments=3)],
    [call(arguments='{"private-response-value":')],
    [call(arguments='Explanation private-response-value: ' + verdict())],
    [call(arguments='```json\n' + verdict() + '\n```')],
    [call(arguments='\ufeff' + verdict())], [call(arguments=verdict() + verdict())],
    [call(arguments='[]')], [call(arguments='true')],
    [call(arguments=verdict(findings={}))], [call(arguments=verdict(requested_files=None))],
    [call(arguments=verdict(requested_files=['a', 'b', 'c'], requested_source_files=['d', 'e']))],
]


@pytest.mark.parametrize('calls', BAD_CALLS)
@pytest.mark.parametrize('content', [None, 'private-untrusted-sibling-text'])
def test_malformed_or_ambiguous_function_response_never_rescues_a_verdict(pr, caplog, calls, content):
    bad = response(content, 'tool_calls', tool_calls=deepcopy(calls), reasoning_content=verdict())
    result, request, checkpoint = review(pr, [bad, bad, tool_response()])
    assert result.verdict == r.ReviewVerdict.ERROR and request.call_count == 2
    checkpoint.assert_called_once_with()
    assert result.raw_response == '' and result.findings is None
    assert result.requested_files == result.requested_source_files == []
    assert 'private-' not in caplog.text + json.dumps(result.to_dict())
    assert request.call_args_list[0].args[0].data == request.call_args_list[1].args[0].data


@pytest.mark.parametrize('content', [{}, [], 1, False])
def test_nonstring_content_with_function_call_is_malformed_and_rejected(pr, caplog, content):
    result, request, _ = review(pr, [tool_response(content=content)] * 3)
    assert result.verdict == r.ReviewVerdict.ERROR and request.call_count == 2
    assert result.scope['transport_code'] == 'content_type'
    assert 'private-' not in caplog.text + json.dumps(result.to_dict())


@pytest.mark.parametrize('vote', ['PASS', 'FAIL', 'BORDERLINE'])
def test_documented_nonempty_text_plus_single_function_submits_only_arguments(pr, caplog, vote):
    arguments = verdict(vote, requested_files=[], requested_source_files=[])
    result, request, checkpoint = review(pr, [tool_response(arguments, "I'll check that for you."),
                                            tool_response()])
    assert result.verdict == r.ReviewVerdict[vote] and request.call_count == 1
    assert result.raw_response == arguments
    assert "I'll check that for you." not in caplog.text + json.dumps(result.to_dict())
    checkpoint.assert_not_called()


@pytest.mark.parametrize('vote,sibling', [
    ('FAIL', 'PASS: private-untrusted-sibling-text'),
    ('PASS', 'FAIL: private-untrusted-sibling-text'),
    ('BORDERLINE', verdict()),
    ('PASS', '<think>private-untrusted-sibling-text</think>'),
])
def test_sibling_text_never_overrides_or_supplies_the_structured_function_verdict(pr, caplog, vote, sibling):
    arguments = verdict(vote)
    result, request, checkpoint = review(pr, [tool_response(arguments, sibling), tool_response()])
    assert result.verdict == r.ReviewVerdict[vote] and request.call_count == 1
    assert result.raw_response == arguments
    assert 'private-untrusted-sibling-text' not in caplog.text + json.dumps(result.to_dict())
    checkpoint.assert_not_called()


def test_arbitrary_plain_prose_without_function_arguments_stays_error(pr, caplog):
    result, request, checkpoint = review(pr, [response('private-unstructured-prose-value')] * 3)
    assert result.verdict == r.ReviewVerdict.ERROR and request.call_count == 2
    assert result.raw_response == '' and result.findings is None
    assert 'private-unstructured-prose-value' not in caplog.text + json.dumps(result.to_dict())
    checkpoint.assert_called_once_with()


@pytest.mark.parametrize('finish', [None, '', 'private-unknown-finish', 'function_call', 'length', 'content_filter'])
def test_unknown_or_incomplete_finish_never_uses_tool_arguments_or_retries(pr, caplog, finish):
    result, request, checkpoint = review(pr, [tool_response(finish=finish), tool_response()])
    assert result.verdict == r.ReviewVerdict.ERROR and request.call_count == 1
    checkpoint.assert_not_called()
    assert 'private-' not in caplog.text + json.dumps(result.to_dict())


@pytest.mark.parametrize('fields', [dict(tool_calls=[call()]), dict(tool_calls={}),
                                   dict(function_call={'name': 'submit_review', 'arguments': verdict()})])
def test_stop_finish_cannot_hide_unsupported_or_unfinished_function_calls(pr, fields):
    bad = response(verdict(), **fields)
    result, request, _ = review(pr, [bad] * 3)
    assert result.verdict == r.ReviewVerdict.ERROR and request.call_count == 2


@pytest.mark.parametrize('fields', [{}, dict(tool_calls=None), dict(tool_calls=[])])
def test_complete_plain_json_content_remains_compatible_only_without_function_calls(pr, fields):
    result, request, _ = review(pr, [response(verdict(), **fields)])
    assert result.passed and request.call_count == 1


def test_operational_function_shape_error_has_only_one_identical_retry(pr):
    result, request, checkpoint = review(pr, [response(None, 'tool_calls', tool_calls=[]), tool_response()])
    assert result.passed and request.call_count == 2
    checkpoint.assert_called_once_with()
    assert request.call_args_list[0].args[0].data == request.call_args_list[1].args[0].data


def test_positive_blocker_in_function_pass_is_still_terminal_borderline(pr):
    findings = r.ReviewFindings(security_issues=['No private-secrets found']).to_dict()
    result, request, checkpoint = review(pr, [tool_response(verdict(findings=findings)), tool_response()])
    assert result.verdict == r.ReviewVerdict.BORDERLINE and request.call_count == 1
    assert result.findings.security_issues == findings['security_issues']
    checkpoint.assert_not_called()


def test_multiple_choices_cannot_select_a_convenient_review(pr):
    choice = {'finish_reason': 'tool_calls', 'message': {'tool_calls': [call()], 'content': None}}
    bad = raw_response(json.dumps({'choices': [choice, choice]}).encode())
    result, request, _ = review(pr, [bad] * 3)
    assert result.verdict == r.ReviewVerdict.ERROR and request.call_count == 2

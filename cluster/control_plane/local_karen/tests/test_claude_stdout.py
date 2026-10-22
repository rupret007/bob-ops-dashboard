"""Successful Claude CLI output must reach the existing strict review parser."""
import json
import subprocess
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig


def payload(vote='PASS'):
    return json.dumps(dict(verdict=vote, reason='Independently checked the pinned implementation', confidence=.8,
                           findings=r.ReviewFindings(risk_notes=['Checked correctness and tests']).to_dict()))


def review(stdout, stderr='', code=0):
    pr = PRInfo('owner', 'repo', 1, 'a'*40, 'Test', 'author', False, '+change', {}, ['file.py'], [])
    with patch.object(r.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, stdout, stderr)) as run, \
         patch.object(r.ClaudeReviewer, '_record_usage') as usage:
        result = r.ClaudeReviewer().review(pr, r.ReviewTier.LOW, KarenConfig())
    assert run.call_args.args[0] == ['claude', '-p', r.build_review_prompt(pr)]
    assert 0 < run.call_args.kwargs['timeout'] <= 180
    return result, usage


@pytest.mark.parametrize('vote', ['PASS', 'FAIL', 'BORDERLINE'])
def test_successful_cli_structured_stdout_is_parsed_without_name_error(vote):
    stdout = '\n ' + payload(vote) + ' \n'
    result, usage = review(stdout)
    assert result.verdict == r.ReviewVerdict[vote]
    assert result.raw_response == stdout.strip()
    assert result.findings.risk_notes == ['Checked correctness and tests']
    usage.assert_called_once()


@pytest.mark.parametrize('stdout', ['', 'natural language', '[]', '{"verdict":"PASS"}',
                                  payload().replace('Independently checked the pinned implementation', 'short')])
def test_successful_exit_with_invalid_stdout_never_uses_valid_stderr_as_a_vote(stdout):
    result, usage = review(stdout, stderr=payload())
    assert result.verdict == r.ReviewVerdict.ERROR and not result.passed
    usage.assert_called_once()


def test_nonzero_exit_never_accepts_structured_stdout():
    result, usage = review(payload(), code=1)
    assert result.verdict == r.ReviewVerdict.ERROR and result.raw_response == ''
    usage.assert_not_called()

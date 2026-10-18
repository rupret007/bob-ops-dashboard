"""Secret findings are actionable reviews; infrastructure failures stay retryable."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import checks, gate
from cluster.control_plane.local_karen.checks import PRInfo, CheckVerdict
from cluster.control_plane.local_karen.config import KarenConfig


def info(diff):
    return PRInfo('owner', 'repo', 1, 'a' * 40, 'Review', 'author', False,
                  diff, {}, ['config.py'], [])


def diff_for(path, value):
    return ('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n'
            '@@ -2,2 +7,3 @@\n unchanged\n-old\n+{1}\n+safe\n').format(path, value)


def test_secret_failure_is_saved_at_verified_head_without_model(tmp_path):
    secret = 'ghp_' + 'B7c9' * 9
    pr = info(diff_for('config.py', 'TOKEN=' + secret))
    config = KarenConfig(verdict_dir=str(tmp_path), state_dir=str(tmp_path))
    with patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'verify_sha_current', return_value=True), \
         patch.object(gate, 'run_review_chain') as review:
        result = gate.run_gate('owner', 'repo', 1, config)
    assert result.error is None
    assert result.sha_verified
    assert result.verdict.verdict_type == 'KARENFAIL'
    assert result.verdict.head_sha == pr.head_sha
    data = json.loads(open(result.verdict_path).read())
    assert data['reviewer'] == 'deterministic_security'
    assert 'config.py:8' in data['reason']
    assert 'GitHub PAT' in data['reason']
    assert secret not in json.dumps(data)
    review.assert_not_called()


def test_secret_failure_at_changed_head_is_only_retryable_error(tmp_path):
    pr = info(diff_for('config.py', 'TOKEN=' + 'ghp_' + 'B7c9' * 9))
    with patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'verify_sha_current', return_value=False), \
         patch.object(gate, 'run_review_chain') as review:
        result = gate.run_gate('owner', 'repo', 1,
                              KarenConfig(verdict_dir=str(tmp_path)))
    assert result.error and result.verdict.status == 'ERROR'
    assert not list(tmp_path.glob('*.json'))
    review.assert_not_called()


@pytest.mark.parametrize('path', ['cluster/control_plane/local_karen/tests/test_checks.py',
                                  'cluster/control_plane/local_karen/tests/test_gate.py'])
def test_only_exact_known_api_fixture_is_allowed_in_karen_tests(path):
    dummy = 'sk-' + '1234567890abcdefghijklmnopqrstuvwxyz123456789012'
    clean = checks.check_no_secrets_in_diff(info(diff_for(path, 'API_KEY = "' + dummy + '"')), KarenConfig())
    assert clean.verdict == CheckVerdict.PASS
    for changed_path, value in [('config.py', dummy), (path, dummy + 'REAL')]:
        result = checks.check_no_secrets_in_diff(info(diff_for(changed_path, 'API_KEY = "' + value + '"')), KarenConfig())
        assert result.verdict == CheckVerdict.FAIL


def test_dummy_fixture_does_not_hide_second_secret():
    dummy = 'sk-' + '1234567890abcdefghijklmnopqrstuvwxyz123456789012'
    leaked = 'ghp_' + 'B7c9' * 9
    result = checks.check_no_secrets_in_diff(info(diff_for(
        'cluster/control_plane/local_karen/tests/test_checks.py',
        'API_KEY = "' + dummy + '"; OTHER=' + leaked)), KarenConfig())
    assert result.verdict == CheckVerdict.FAIL
    assert leaked not in result.details


def test_private_key_fixture_requires_exact_bogus_body_and_terminator():
    path = 'cluster/control_plane/local_karen/tests/test_checks.py'
    header = '-----BEGIN RSA ' + 'PRIVATE KEY-----'
    for tail, expected in [('MIIEpQIBAAKCAQEA0Z...\n+""")', CheckVerdict.PASS),
                           ('real-key-material\n+""")', CheckVerdict.FAIL)]:
        result = checks.check_no_secrets_in_diff(info(diff_for(path, header + '\n+' + tail)), KarenConfig())
        assert result.verdict == expected


def test_known_pat_fixture_only_allowed_in_exact_scanner_test():
    value = 'GITHUB_TOKEN=' + 'ghp_' + 'x' * 36
    for path, expected in [('cluster/control_plane/local_karen/tests/test_checks.py', CheckVerdict.PASS),
                           ('tests/test_config.py', CheckVerdict.FAIL), ('.env', CheckVerdict.FAIL)]:
        result = checks.check_no_secrets_in_diff(info(diff_for(path, value)), KarenConfig())
        assert result.verdict == expected


def test_existing_scanner_and_gate_sources_are_not_secret_leaks():
    for name in ('test_checks.py', 'test_gate.py'):
        path = 'cluster/control_plane/local_karen/tests/' + name
        source = Path(__file__).with_name(name).read_text()
        diff = 'diff --git a/{0} b/{0}\n@@ -0,0 +1,999 @@\n'.format(path)
        diff += '\n'.join('+' + line for line in source.splitlines())
        result = checks.check_no_secrets_in_diff(info(diff), KarenConfig())
        assert result.verdict == CheckVerdict.PASS

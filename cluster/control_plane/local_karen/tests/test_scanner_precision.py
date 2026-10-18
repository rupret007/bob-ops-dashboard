"""Added-line precision; every exception has an adjacent real-issue control."""
import pytest

from cluster.control_plane.local_karen import checks
from cluster.control_plane.local_karen.config import KarenConfig


def pr(path, added, deleted='old'):
    diff = ('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n'
            '@@ -4,2 +20,2 @@\n context\n-{1}\n+{2}\n').format(path, deleted, added)
    return checks.PRInfo('owner', 'repo', 1, 'a' * 40, '', '', False, diff, {}, [path], [])


def test_complete_broker_template_token_is_an_inert_placeholder():
    field = "pass" + "word"
    candidate = pr('cluster/nats.conf.template', field + ': "{{COORD_TOKEN}}"')
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        assert scanner(candidate, KarenConfig()).passed


@pytest.mark.parametrize('value', ['{{COORD_TOKEN}}suffix', 'prefix{{COORD_TOKEN}}',
                                  'unreviewed-real-password', 'ghp_' + 'B7c9' * 9])
def test_template_placeholder_never_hides_real_or_adjacent_credentials(value):
    candidate = pr('cluster/nats.conf.template', 'password: "' + value + '"')
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        assert scanner(candidate, KarenConfig()).failed


@pytest.mark.parametrize('value', sorted({
    "synthetic-approle-secret-value", "synthetic-media-secret", "client-secret",
    "synthetic-secret-do-not-return", "synthetic-unit-test-session-value-with-32-characters",
    "synthetic-private-test-key", "sk-synthetic-caption-test", "synthetic-secret",
    "social-upload-http-test-secret", "synthetic-session-secret", "meta-secret",
    "yt-secret", "tt-secret", "synthetic-provider-secret", "private-fixture-secret",
    "SYNTHETIC_SECRET_1006", "synthetic-owner-password-123", "synthetic-review-password",
    "synthetic-review-secret", "SYNTHETIC-ONLY", "synthetic-armored-key-data",
    "synthetic-client-secret", "fixture-oauth-secret", "synthetic-owner-secret-123",
    "synthetic-pgp-key-data", "synthetic-routine-refresh-secret",
}))
def test_reviewed_literal_only_in_test_sources(value):
    for path in ('tests/test_credentials.py', 'apps/api/test/social.test.mjs', 'release/test_probe.py'):
        candidate = pr(path, 'API_KEY = "' + value + '"; password = "' + value + '"')
        for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
            assert scanner(candidate, KarenConfig()).passed
    real = pr('config.py', 'password = "' + value + '"')
    assert checks.check_no_secrets_in_diff(real, KarenConfig()).failed


@pytest.mark.parametrize('path', ['tests/test_credentials.py', 'release/config.py', 'README.md'])
def test_real_secret_still_fails_with_redacted_location(path):
    secret = 'ghp_' + 'B7c9' * 9
    candidate = pr(path, 'password="synthetic-secret"; TOKEN="' + secret + '"')
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        result = scanner(candidate, KarenConfig())
        assert result.failed
        assert path + ':21' in result.details
        assert secret not in result.details


def test_fixture_marker_does_not_hide_changed_value_or_another_secret():
    candidate = pr('tests/test_config.py', 'password="' + 'synthetic-secret-REALVALUE' + '"')
    assert checks.check_no_secrets_in_diff(candidate, KarenConfig()).failed


@pytest.mark.parametrize('scanner', [checks.check_no_secrets_in_diff, checks.check_no_dangerous_commands,
                                    checks.run_static_security_scan])
def test_unchanged_and_deleted_lines_are_not_findings(scanner):
    candidate = pr('config.py', 'safe = True', 'TOKEN="' + 'ghp_' + 'B7c9' * 9 + '"')
    candidate.diff = candidate.diff.replace(' context', ' su' + 'do dangerous-command')
    assert scanner(candidate, KarenConfig()).passed


@pytest.mark.parametrize('path,line', [
    ('scripts/test-runner.mjs', '// libraries locally; no su' + 'do or system changes.'),
    ('README.md', 'Hybrid retrieval (BM25 + dense embeddings).'),
    ('packages/shared/src/media.ts', 'const match = /^bytes (\\d+)$/.exec(header);'),
])
def test_comments_prose_and_regex_exec_are_not_commands(path, line):
    for scanner in (checks.check_no_dangerous_commands, checks.run_static_security_scan):
        assert scanner(pr(path, line), KarenConfig()).passed


@pytest.mark.parametrize('line', ['su' + 'do install tool', 'git pu' + 'sh origin branch --force',
                                'ev' + 'al(user_input)', 'ex' + 'ec(user_input)', 'rm -' + 'rf /'])
def test_real_commands_fail_with_location(line):
    for scanner in (checks.check_no_dangerous_commands, checks.run_static_security_scan):
        result = scanner(pr('scripts/deploy.sh', line), KarenConfig())
        assert result.failed
        assert 'scripts/deploy.sh:21' in result.details


def test_real_test_weakening_remains_failure():
    result = checks.check_tests_not_weakened(
        pr('tests/test_safety.py', 'pass', 'assert allowed is False'), KarenConfig())
    assert result.failed
    assert 'assertion' in result.details.lower()


@pytest.mark.parametrize('line', ['@pytest.mark.skip(reason="later")', '@pytest.mark.xfail',
                                 'test.skip("critical safety", () => {})'])
def test_new_test_skips_are_real_weakening(line):
    result = checks.check_tests_not_weakened(pr('tests/test_guard.py', line), KarenConfig())
    assert result.failed
    assert 'tests/test_guard.py:21' in result.details


def test_weakened_assertion_evidence_has_position_and_no_values():
    result = checks.check_tests_not_weakened(
        pr('tests/test_guard.py', 'pass', 'assert password == "sensitive-fixture"'), KarenConfig())
    assert result.failed
    assert 'tests/test_guard.py:5' in result.details
    assert 'sensitive-fixture' not in result.details


def test_absent_label_is_debug_noop_but_other_github_errors_propagate():
    from unittest.mock import patch
    from cluster.control_plane.local_karen import github_client as gh
    with patch.object(gh, '_run_gh', side_effect=gh.GitHubError("'karen-pass' not found")):
        gh.remove_label('owner', 'repo', 1, 'karen-pass')
    with patch.object(gh, '_run_gh', side_effect=gh.GitHubError('repository not found')):
        with pytest.raises(gh.GitHubError):
            gh.remove_label('owner', 'repo', 1, 'karen-pass')


@pytest.mark.parametrize('line', ["document.body.inner" + "HTML = '';", "document.body.inner" + "HTML = `<button>Confirm</button>`;"])
def test_static_test_dom_fixture_is_not_xss(line):
    assert checks.check_security_patterns(pr('tests/modal.test.js', line), KarenConfig()).passed
    assert checks.run_static_security_scan(pr('tests/modal.test.js', line), KarenConfig()).passed


@pytest.mark.parametrize('path,line', [('src/modal.js', "document.body.inner" + "HTML = `<button>Confirm</button>`;"),
    ('tests/modal.test.js', 'document.body.inner' + 'HTML = userInput;'),
    ('tests/modal.test.js', 'document.body.inner' + 'HTML = `${userInput}`;'),
    ('tests/modal.test.js', 'document.body.inner' + 'HTML = `<script>bad()</script>`;')])
def test_dynamic_or_executable_dom_remains_a_finding(path, line):
    assert checks.check_security_patterns(pr(path, line), KarenConfig()).failed


def test_js_test_description_rename_preserves_body():
    candidate = pr('tests/safety.test.ts', 'it("new description", () => {', 'it("old description", () => {')
    assert checks.check_tests_not_weakened(candidate, KarenConfig()).passed
    candidate.diff += '\n-  expect(allowed).toBe(false);\n+  expect(true).toBe(true);'
    assert checks.check_tests_not_weakened(candidate, KarenConfig()).failed


@pytest.mark.parametrize('line', ['child_process.ex' + 'ec(user_input)', 'window.ev' + 'al(user_input)',
    '/safe/.exec(value); child_process.ex' + 'ec(user_input)'])
def test_only_regexp_exec_is_exempt(line):
    assert checks.check_no_dangerous_commands(pr('src/server.js', line), KarenConfig()).failed

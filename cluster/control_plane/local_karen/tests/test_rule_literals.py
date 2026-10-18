"""Rule definitions are data; executable neighbors and credentials still fail."""
import pytest

from cluster.control_plane.local_karen import checks
from cluster.control_plane.local_karen.config import KarenConfig


PATH = 'cluster/control_plane/local_karen/checks.py'
FIELD = 'pass' + 'word'
DANGER = 'su' + 'do'
EXEC_CALL = 'os.' + 'system'
XSS_RULE = 'dangerously' + 'SetInnerHTML'
AUTH_RULE = 'Au' + 'th by' + 'pass mention'


def candidate(source, path=PATH):
    source = (source.replace('DANGER_WORD', DANGER).replace('EXEC_CALL', EXEC_CALL)
              .replace('AUTH_PATTERN', 'auth.*by' + 'pass').replace('XSS_RULE', XSS_RULE).replace('AUTH_RULE', AUTH_RULE))
    diff = 'diff --git a/{0} b/{0}\n@@ -0,0 +1,99 @@\n'.format(path)
    diff += '\n'.join('+' + line for line in source.splitlines())
    return checks.PRInfo('owner', 'repo', 1, 'a' * 40, '', '', False,
                         diff, {}, [path], [])


@pytest.mark.parametrize('scanner', [checks.check_no_dangerous_commands,
                                   checks.check_security_patterns,
                                   checks.run_static_security_scan])
def test_complete_literal_rule_tables_are_data(scanner):
    source = '''SECURITY_PATTERNS = [
    (r"XSS_RULE", "XSS: XSS_RULE", "security"),
    (r"AUTH_PATTERN", "AUTH_RULE", "auth"),
]
def scan():
    dangerous_patterns = [
        (r"DANGER_WORD", "DANGER_WORD usage"),
        (r"commands.getoutput", "commands.getoutput"),
    ]'''
    assert scanner(candidate(source), KarenConfig()).passed


@pytest.mark.parametrize('source', [
    'dangerous_patterns = [\n    (r"DANGER_WORD", "DANGER_WORD usage"),\n]\nEXEC_CALL("DANGER_WORD whoami")',
    'dangerous_patterns = [\n    (r"DANGER_WORD", "DANGER_WORD usage"),\n    EXEC_CALL("DANGER_WORD whoami"),\n]',
    'dangerous_patterns = [\n    (r"DANGER_WORD", "DANGER_WORD usage"),\n]; EXEC_CALL("DANGER_WORD whoami")',
    'dangerous_patterns = [\n    (r"DANGER_WORD", "DANGER_WORD usage"),',
])
def test_executable_computed_or_incomplete_rules_still_fail(source):
    for scanner in (checks.check_no_dangerous_commands, checks.run_static_security_scan):
        result = scanner(candidate(source), KarenConfig())
        assert result.failed
        assert PATH + ':' in result.details


def test_rule_exception_does_not_apply_to_other_files():
    pr = candidate('dangerous_patterns = [\n    (r"DANGER_WORD", "DANGER_WORD usage"),\n]', 'src/runner.py')
    assert checks.check_no_dangerous_commands(pr, KarenConfig()).failed


def test_real_credential_inside_rule_table_is_still_redacted_failure():
    value = 'ghp_' + 'B7c9' * 9
    pr = candidate('SECRET_PATTERNS = [\n    (r"pattern", "' + value + '"),\n]')
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        result = scanner(pr, KarenConfig())
        assert result.failed and value not in result.details


def test_actual_acl_generator_placeholder_passes_both_secret_scans():
    pr = candidate(FIELD + ': "{{%s_TOKEN}}"', 'cluster/nats_acl.py')
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        assert scanner(pr, KarenConfig()).passed


@pytest.mark.parametrize('path,value', [
    ('cluster/nats_acl.py', 'prefix{{%s_TOKEN}}'),
    ('cluster/nats_acl.py', '{{%s_TOKEN}}suffix'),
    ('config.py', '{{%s_TOKEN}}'),
    ('cluster/nats_acl.py', 'unreviewed-real-password'),
])
def test_acl_exception_has_exact_value_and_path(path, value):
    assert checks.check_no_secrets_in_diff(
        candidate(FIELD + ': "' + value + '"', path), KarenConfig()).failed


def test_acl_placeholder_never_hides_adjacent_real_credential():
    value = 'ghp_' + 'B7c9' * 9
    pr = candidate(FIELD + ': "{{%s_TOKEN}}"; TOKEN="' + value + '"', 'cluster/nats_acl.py')
    assert checks.check_no_secrets_in_diff(pr, KarenConfig()).failed


def test_context_can_complete_table_but_a_gap_cannot():
    pr = candidate('unused')
    pr.diff = ('diff --git a/{0} b/{0}\n@@ -1,2 +1,3 @@\n'
               ' SECURITY_PATTERNS = [\n'
               '+    (r"XSS_RULE", "XSS: XSS_RULE", "security"),\n'
               ' ]\n').format(PATH).replace('XSS_RULE', XSS_RULE)
    assert checks.check_security_patterns(pr, KarenConfig()).passed
    pr.diff = pr.diff.replace(' ]', '@@ -7,1 +8,1 @@\n ]')
    assert checks.check_security_patterns(pr, KarenConfig()).failed

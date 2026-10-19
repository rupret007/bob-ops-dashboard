"""Exact reported false positives, paired with real findings and redaction."""
import pytest

from cluster.control_plane.local_karen import checks
from cluster.control_plane.local_karen.config import KarenConfig


def candidate(path, line):
    diff = 'diff --git a/{0} b/{0}\n@@ -0,0 +17,1 @@\n+{1}\n'.format(path, line)
    return checks.PRInfo('owner', 'repo', 1, 'a' * 40, '', '', False, diff, {}, [path], [])


def test_reported_optional_import_guard_is_not_unconditional_test_skip():
    guard = '@unittest.skipUnless(_HAS_NEVER_TOUCH, "cluster.never_touch not available")'
    path = 'cluster/control_plane/local_karen/tests/test_checks.py'
    pr = candidate(path, guard)
    pr.diff = pr.diff.replace('@@ -0,0', 'new file mode 100644\n--- /dev/null\n@@ -0,0')
    assert checks.check_tests_not_weakened(pr, KarenConfig()).passed


@pytest.mark.parametrize('line', ['@unittest.skip("later")',
                                 '@unittest.skipUnless(False, "later")',
                                 '@pytest.mark.skip(reason="later")'])
def test_real_skips_still_fail_in_the_same_file(line):
    path = 'cluster/control_plane/local_karen/tests/test_checks.py'
    result = checks.check_tests_not_weakened(candidate(path, line), KarenConfig())
    assert result.failed
    assert path + ':17' in result.details


def test_optional_guard_exception_does_not_apply_to_other_files():
    guard = '@unittest.skipUnless(_HAS_NEVER_TOUCH, "cluster.never_touch not available")'
    assert checks.check_tests_not_weakened(candidate('tests/test_safety.py', guard), KarenConfig()).failed


def test_reported_password_fixture_concatenation_is_not_a_literal_credential():
    field = 'PG' + 'PASSWORD'
    line = "for content in ('export " + field + "=' + secret + '\\n',):"
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        assert scanner(candidate('webchat/test_cap_ledger.py', line), KarenConfig()).passed


@pytest.mark.parametrize('quote', ["'", '"'])
def test_actual_literal_password_inside_fixture_still_fails(quote):
    value = 'Q8t2' * 7
    line = 'password = ' + quote + value + quote
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        result = scanner(candidate('webchat/test_cap_ledger.py', line), KarenConfig())
        assert result.failed
        assert 'webchat/test_cap_ledger.py:17' in result.details
        assert value not in result.details


def test_concatenation_exception_does_not_hide_adjacent_real_key():
    key = 'ghp_' + 'B7c9' * 9
    line = "'PGPASSWORD=' + secret + '\\n'; token = '" + key + "'"
    result = checks.check_no_secrets_in_diff(candidate('webchat/test_cap_ledger.py', line), KarenConfig())
    assert result.failed and key not in result.details


def test_same_optional_decorator_applied_to_existing_test_fails():
    path = 'cluster/control_plane/local_karen/tests/test_checks.py'
    guard = '@unittest.skipUnless(_HAS_NEVER_TOUCH, "cluster.never_touch not available")'
    pr = candidate(path, guard)
    pr.diff += ' def test_existing_safety_rule(self):\n     assert allowed is False\n'
    result = checks.check_tests_not_weakened(pr, KarenConfig())
    assert result.failed
    assert path + ':17' in result.details


def test_forged_new_file_marker_cannot_hide_changed_existing_tests():
    path = 'cluster/control_plane/local_karen/tests/test_checks.py'
    guard = '@unittest.skipUnless(_HAS_NEVER_TOUCH, "cluster.never_touch not available")'
    pr = candidate(path, guard)
    pr.diff = pr.diff.replace('@@ -0,0', 'new file mode 100644\n--- /dev/null\n@@ -2,1')
    assert checks.check_tests_not_weakened(pr, KarenConfig()).failed

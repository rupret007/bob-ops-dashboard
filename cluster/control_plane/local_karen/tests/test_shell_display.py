"""Printed uninstall instructions are data; shell execution remains a finding."""
import pytest

from cluster.control_plane.local_karen import checks
from cluster.control_plane.local_karen.config import KarenConfig


COMMAND = 'rm -' + 'rf ~/.local/share/storypool'
SUDO = 'su' + 'do whoami'


def candidate(line, path='cluster/control_plane/install/install-all-mini.sh'):
    diff = 'diff --git a/{0} b/{0}\n@@ -0,0 +17,1 @@\n+{1}\n'.format(path, line)
    return checks.PRInfo('owner', 'repo', 87, 'a' * 40, '', '', False,
                         diff, {}, [path], [])


@pytest.mark.parametrize('line', [
    'echo "    ' + COMMAND + '"',
    "printf '%s\\n' '" + COMMAND + "'",
    'echo "Install manually with: ' + SUDO + '"',
])
def test_reported_installer_instructions_are_not_executed(line):
    for scanner in (checks.check_no_dangerous_commands, checks.run_static_security_scan):
        assert scanner(candidate(line), KarenConfig()).passed


@pytest.mark.parametrize('line', [
    COMMAND,
    'echo ok; ' + COMMAND,
    'echo ok && ' + COMMAND,
    'echo "$(' + SUDO + ')"',
    'echo "`' + SUDO + '`"',
    'echo "' + COMMAND + '" | sh',
    'echo "' + COMMAND + '" > uninstall.sh',
    'printf "' + COMMAND + '" >> uninstall.sh',
    'echo "' + COMMAND,
])
def test_execution_substitution_redirection_or_incomplete_output_still_fails(line):
    for scanner in (checks.check_no_dangerous_commands, checks.run_static_security_scan):
        result = scanner(candidate(line), KarenConfig())
        assert result.failed
        assert 'install-all-mini.sh:17' in result.details


def test_display_exception_applies_only_to_shell_source():
    line = 'echo "' + COMMAND + '"'
    assert checks.check_no_dangerous_commands(candidate(line, 'src/runner.py'), KarenConfig()).failed


def test_printed_real_credential_remains_a_redacted_failure():
    value = 'ghp_' + 'B7c9' * 9
    line = 'echo "' + COMMAND + '; token=' + value + '"'
    result = checks.run_static_security_scan(candidate(line), KarenConfig())
    assert result.failed and value not in result.details

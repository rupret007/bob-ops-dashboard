"""AST proof distinguishes regression inputs from executable neighboring code."""
import pytest

from cluster.control_plane.local_karen import checks
from cluster.control_plane.local_karen.config import KarenConfig


COMMAND_FIXTURE = 'COMMAND = "sudo whoami"'


def candidate(source, path='tests/test_commands.py'):
    diff = 'diff --git a/{0} b/{0}\n@@ -0,0 +1,{1} @@\n'.format(path, len(source.splitlines()))
    diff += '\n'.join('+' + line for line in source.splitlines()) + '\n'
    return checks.PRInfo('owner', 'repo', 1, 'a' * 40, '', '', False, diff, {}, [path], [])


@pytest.mark.parametrize('source', [
    'SUDO = "sudo whoami"',
    'COMMAND = "rm -rf /"',
    '@pytest.mark.parametrize("line", ["sudo whoami", "rm -rf /"])\ndef test_example(line):\n    assert line',
    'def test_example():\n    for reason in ("Authorization bypass", "other"):\n        assert reason',
])
def test_complete_literal_fixture_inputs_pass(source):
    for scanner in (checks.check_no_dangerous_commands, checks.check_security_patterns,
                    checks.run_static_security_scan):
        assert scanner(candidate(source), KarenConfig()).passed


@pytest.mark.parametrize('source', [
    'exec("sudo whoami")',
    'COMMAND = "sudo whoami"; exec(COMMAND)',
    'COMMAND = "sudo whoami"\nsubprocess.run(COMMAND, shell=True)',
    'COMMAND = dangerous("sudo whoami")',
    'COMMAND = f"sudo {dangerous()}"',
])
def test_executed_computed_or_neighboring_source_still_fails(source):
    assert checks.check_no_dangerous_commands(candidate(source), KarenConfig()).failed
    assert checks.run_static_security_scan(candidate(source), KarenConfig()).failed


def test_incomplete_context_and_production_sources_fail_closed():
    pr = candidate(COMMAND_FIXTURE)
    assert checks.check_no_dangerous_commands(candidate(COMMAND_FIXTURE, 'app.py'), KarenConfig()).failed
    pr.diff = pr.diff.replace('+1,', '+50,')
    assert checks.check_no_dangerous_commands(pr, KarenConfig()).failed


def test_fixture_literal_does_not_exempt_secrets():
    value = 'ghp_' + 'B7c9' * 9
    pr = candidate('COMMAND = "' + value + '"')
    for scanner in (checks.check_no_secrets_in_diff, checks.run_static_security_scan):
        result = scanner(pr, KarenConfig())
        assert result.failed and value not in result.details

"""Keep compiler-pin correction narrow and retain every other integrity guard."""
from pathlib import Path
import pytest
from cluster.control_plane.local_karen import assertion_policy as policy
from cluster.control_plane.local_karen.check_types import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig

DIFF = (Path(__file__).parent / 'fixtures/go-pin-forward-patch.diff').read_text()
OLD = 'go-version: "1.26.9"'
NEW = 'go-version: "1.26.10"'


def checked(diff):
    pr = PRInfo('fixture', 'project', 1, 'a' * 40, 'Compiler pin contract',
                'fixture', False, diff, {}, ['tests/test_desktop_installer_workflow.py'], [])
    return policy.check_tests_not_weakened(pr, KarenConfig())


def test_complete_correlated_forward_patch_retains_the_lane_count_and_rejects_old_pin():
    assert checked(DIFF).passed


@pytest.mark.parametrize('mutation', [
    'downgrade', 'minor_change', 'same_version', 'count_lowered', 'or_true',
    'missing_network_pin', 'one_stale_CI_pin', 'missing_old_rejection',
    'missing_network_count', 'different_workflow_read', 'added_extra_statement',
    'missing_CI_pin', 'zero_network_count', 'unrelated_test_removal',
    'old_security_assertion_removed', 'added_skip', 'second_test_hunk',
])
def test_incomplete_or_weaker_updates_are_not_exempted(mutation):
    diff = DIFF
    if mutation == 'downgrade': diff = diff.replace('1.26.10', '1.26.8')
    elif mutation == 'minor_change': diff = diff.replace('1.26.10', '1.27.10')
    elif mutation == 'same_version': diff = diff.replace('1.26.10', '1.26.9')
    elif mutation == 'count_lowered': diff = diff.replace("+    assert WORKFLOW.count('" + NEW + "') == 2", "+    assert WORKFLOW.count('" + NEW + "') == 1")
    elif mutation == 'or_true': diff = diff.replace("+    assert WORKFLOW.count('" + NEW + "') == 2", "+    assert WORKFLOW.count('" + NEW + "') == 2 or True")
    elif mutation == 'missing_network_pin':
        block = diff.split('diff --git a/.github/workflows/cross-network-acceptance.yml', 1)[1].split('diff --git ', 1)[0]
        diff = diff.replace(block, block.replace('+          ' + NEW + '\n', ''))
    elif mutation == 'one_stale_CI_pin': diff = diff.replace('+          ' + NEW, '+          ' + OLD, 1)
    elif mutation == 'missing_old_rejection': diff = diff.replace('+    assert "1.26.9" not in WORKFLOW + cross_network\n', '')
    elif mutation == 'missing_network_count': diff = diff.replace("+    assert cross_network.count('" + NEW + "') == 1\n", '')
    elif mutation == 'different_workflow_read': diff = diff.replace('+        ROOT / ".github" / "workflows" / "cross-network-acceptance.yml"', '+        ROOT / ".github" / "workflows" / "other.yml"')
    elif mutation == 'added_extra_statement': diff = diff.replace('+    cross_network = (', '+    WORKFLOW = ""\n+    cross_network = (')
    elif mutation == 'missing_CI_pin': diff = diff.replace('+          ' + NEW + '\n', '', 1)
    elif mutation == 'zero_network_count': diff = diff.replace("+    assert cross_network.count('" + NEW + "') == 1", "+    assert cross_network.count('" + NEW + "') == 0")
    elif mutation == 'unrelated_test_removal': diff += '\ndiff --git a/tests/test_security.py b/tests/test_security.py\n--- a/tests/test_security.py\n+++ b/tests/test_security.py\n@@ -1 +0,0 @@\n-assert signature_valid\n'
    elif mutation == 'old_security_assertion_removed': diff = diff.replace('     assert "1.25.12" not in WORKFLOW', '-    assert "1.25.12" not in WORKFLOW')
    elif mutation == 'added_skip': diff = diff.replace('+    assert WORKFLOW.count(', '+    @pytest.mark.skip\n+    assert WORKFLOW.count(')
    elif mutation == 'second_test_hunk':
        start = diff.index('diff --git a/tests/')
        end = diff.find('\ndiff --git ', start + 1)
        if end == -1: end = len(diff)
        diff = diff[:end] + '\n@@ -900 +905 @@\n-old = 1\n+old = 2\n' + diff[end:]
    assert diff != DIFF
    assert not checked(diff).passed


def test_literal_formatting_remains_supported_without_a_version_transition():
    diff = ('diff --git a/tests/test_pin.py b/tests/test_pin.py\n'
            '--- a/tests/test_pin.py\n+++ b/tests/test_pin.py\n@@ -1 +1 @@\n'
            '-assert value == "same"\n+assert value == \'same\'\n')
    assert checked(diff).passed

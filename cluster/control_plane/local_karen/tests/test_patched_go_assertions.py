"""Keep compiler-pin correction narrow and retain every other integrity guard."""
from difflib import unified_diff
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


@pytest.mark.parametrize('pin', ['1.26.9', '1.26.10'])
@pytest.mark.parametrize('component', range(3))
def test_oversized_version_components_fail_without_raising(pin, component):
    parts = pin.split('.')
    parts[component] = '9' * 4301
    diff = DIFF.replace(pin, '.'.join(parts))
    assert not checked(diff).passed


@pytest.mark.parametrize('binding', [
    '    cross_network = WORKFLOW\n',
    '    global cross_network\n',
    '    import workflow_snapshot as cross_network\n',
    '    def cross_network():\n        return WORKFLOW\n',
])
def test_existing_cross_network_bindings_are_not_rebound(binding):
    diff = DIFF.replace(
        ' def test_transport_uses_the_reviewed_patched_go_toolchain_everywhere() -> None:\n',
        ' def test_transport_uses_the_reviewed_patched_go_toolchain_everywhere() -> None:\n'
        + ''.join(' ' + line for line in binding.splitlines(keepends=True)))
    lines = len(binding.splitlines())
    diff = diff.replace('@@ -750,7 +750,12 @@',
                        f'@@ -750,{7 + lines} +750,{12 + lines} @@')
    diff = diff.replace('     assert "1.25.12" not in WORKFLOW',
                        '     assert "1.25.12" not in cross_network')
    assert diff != DIFF
    assert not checked(diff).passed


@pytest.mark.parametrize('name', ['cross_network', 'ｃｒｏｓｓ_ｎｅｔｗｏｒｋ'])
def test_existing_cross_network_parameter_is_not_rebound(name):
    diff = DIFF.replace('toolchain_everywhere() -> None:',
                        f'toolchain_everywhere({name}) -> None:')
    diff = diff.replace('     assert "1.25.12" not in WORKFLOW',
                        f'     assert "1.25.12" not in {name}')
    assert diff != DIFF
    assert not checked(diff).passed


@pytest.mark.parametrize('mutation', [
    'downgrade', 'minor_change', 'same_version', 'count_lowered', 'or_true',
    'missing_network_pin', 'one_stale_CI_pin', 'missing_old_rejection',
    'missing_network_count', 'different_workflow_read', 'added_extra_statement',
    'missing_CI_pin', 'zero_network_count', 'unrelated_test_removal',
    'old_security_assertion_removed', 'added_skip', 'second_test_hunk',
    'second_test_hunk_insertion_only',
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
    elif mutation in ('second_test_hunk', 'second_test_hunk_insertion_only'):
        start = diff.index('diff --git a/tests/')
        end = diff.find('\ndiff --git ', start + 1)
        if end == -1: end = len(diff)
        hunk = ('\n@@ -900 +905 @@\n-old = 1\n+old = 2\n'
                if mutation == 'second_test_hunk' else
                '\n@@ -900,0 +906 @@\n+# Separate insertion-only hunk.\n')
        diff = diff[:end] + hunk + diff[end:]
    assert diff != DIFF
    assert not checked(diff).passed


@pytest.mark.parametrize('context', ['docstring', 'uncalled_function',
                                    'unreachable_branch', 'after_return'])
def test_nonexecuting_replacements_do_not_exempt_an_executable_assertion(context):
    path = 'tests/test_desktop_installer_workflow.py'
    header = f'diff --git a/{path} b/{path}\n'
    workflows, test_diff = DIFF.split(header)
    replacement = ''.join(line[1:] + '\n' for line in test_diff.splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
    function = 'def test_compiler_pins():\n'
    old = f'    assert WORKFLOW.count({OLD!r}) == 2\n'
    tail = '    assert "1.25.12" not in WORKFLOW\n'
    if context == 'docstring':
        opening, closing = '    """Compiler pin notes.\n', '    """\n'
        before = function + opening + closing + old + tail
        after = function + opening + replacement + closing + tail
    elif context in ('uncalled_function', 'unreachable_branch'):
        opening = ('    def pin_notes():\n' if context == 'uncalled_function'
                   else '    if False:\n')
        closing = '        pass\n'
        before = function + opening + closing + old + tail
        nested = ''.join('    ' + line for line in replacement.splitlines(keepends=True))
        after = function + opening + nested + closing + tail
    else:
        before = function + old + '    return\n' + tail
        after = function + '    return\n' + replacement + tail
    diff = workflows + header + ''.join(unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile='a/' + path, tofile='b/' + path, n=20))
    assert len([row for row in policy._removed_lines(diff) if row[0] == path]) == 1
    assert diff.split(header)[1].count('\n@@ ') == 1
    assert not checked(diff).passed


@pytest.mark.parametrize('delimiter', ['"""', "'''"])
def test_docstring_replacement_with_only_three_context_lines_is_not_exempted(delimiter):
    path = 'tests/test_desktop_installer_workflow.py'
    header = f'diff --git a/{path} b/{path}\n'
    workflows, test_diff = DIFF.split(header)
    replacement = ''.join(line[1:] + '\n' for line in test_diff.splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
    opening = ('def test_compiler_pins():\n'
               f'    {delimiter}Compiler pin notes.\n'
               '\n\n\n\n'
               'def test_documented_pins():\n')
    closing = f'    # {delimiter}\n'
    old = f'    assert WORKFLOW.count({OLD!r}) == 2\n'
    tail = '    assert "1.25.12" not in WORKFLOW\n'
    before = opening + closing + old + tail
    after = opening + replacement + closing + tail
    # Both are valid complete tests, but the new checks are only docstring text.
    compile(before, path, 'exec')
    compile(after, path, 'exec')
    diff = workflows + header + ''.join(unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile='a/' + path, tofile='b/' + path, n=3))
    assert f'{delimiter}Compiler pin notes.' not in diff
    assert f' # {delimiter}\n' in diff
    assert len([row for row in policy._removed_lines(diff) if row[0] == path]) == 1
    assert diff.split(header)[1].count('\n@@ ') == 1
    assert not checked(diff).passed


def test_literal_formatting_remains_supported_without_a_version_transition():
    diff = ('diff --git a/tests/test_pin.py b/tests/test_pin.py\n'
            '--- a/tests/test_pin.py\n+++ b/tests/test_pin.py\n@@ -1 +1 @@\n'
            '-assert value == "same"\n+assert value == \'same\'\n')
    assert checked(diff).passed

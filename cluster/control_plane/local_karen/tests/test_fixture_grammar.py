"""Unknown binding forms cannot turn executable consumers into inert fixtures."""
import pytest

from cluster.control_plane.local_karen import checks


def candidate(source, path='cluster/control_plane/local_karen/tests/test_shell_display.py'):
    diff = 'diff --git a/{0} b/{0}\n@@ -0,0 +1,{1} @@\n'.format(path, len(source.splitlines()))
    diff += '\n'.join('+' + line for line in source.splitlines()) + '\n'
    return checks.PRInfo('owner', 'repo', 1, 'a' * 40, '', '', False, diff, {}, [path], [])


@pytest.mark.parametrize('source', [
    '''VALUE = "synthetic fixture"
def test_capture():
    match exec:
        case scanner:
            scanner(VALUE)
''',
    '''VALUE = "synthetic fixture"
def test_capture():
    match exec:
        case len:
            len(VALUE)
''',
    '''VALUE = "synthetic fixture"
def test_capture():
    match exec:
        case object(__call__=scanner):
            scanner(VALUE)
''',
    '''VALUE = "synthetic fixture"
class Unproven:
    pass
''',
    '''VALUE = "synthetic fixture"
async def unproven():
    pass
''',
    '''VALUE = "synthetic fixture"
try:
    pass
except Exception as scanner:
    scanner(VALUE)
''',
])
def test_unknown_fixture_grammar_proves_no_masking_for_any_literal(source):
    # This is independent of scanner patterns: no literal may be masked when
    # the complete module has an unsupported binding or execution form.
    assert checks._python_test_fixture_source(candidate(source).diff) == {}


def test_complete_pure_regression_harness_still_masks_literal_data():
    source = 'VALUE = "synthetic fixture"\n'
    assert checks._python_test_fixture_source(candidate(source).diff)

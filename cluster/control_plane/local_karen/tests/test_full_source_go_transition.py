import copy
import ast

import pytest

from cluster.control_plane.local_karen.full_source_go_transition import CI, NETWORK, TEST, prove_transition


def sources():
    old = {
        CI: 'jobs:\n  a:\n    go-version: "1.26.9"\n  b:\n    go-version: "1.26.9"\n',
        NETWORK: 'jobs:\n  a:\n    go-version: "1.26.9"\n',
        TEST: '''from pathlib import Path
ROOT = Path(__file__).parent
WORKFLOW = (ROOT / '.github/workflows/ci.yml').read_text()
def test_toolchain() -> None:
    assert WORKFLOW.count('go-version: "1.26.9"') == 2
    assert '1.25.12' not in WORKFLOW
    assert 'go 1.25.13' in 'go 1.25.13'
def test_other_security_contract():
    assert 'denied' != 'allowed'
''',
    }
    new = {p: t.replace('1.26.9', '1.26.10') for p, t in old.items()}
    new[TEST] = new[TEST].replace(
        '    assert \'1.25.12\' not in WORKFLOW',
        '''    cross_network = (ROOT / '.github' / 'workflows' / 'cross-network-acceptance.yml').read_text(encoding='utf-8')
    assert cross_network.count('go-version: "1.26.10"') == 1
    assert '1.26.9' not in WORKFLOW + cross_network
    assert '1.25.12' not in WORKFLOW''',
    )
    return old, new


def test_full_module_preserves_all_existing_contracts():
    old, new = sources()
    proof = prove_transition(old, new)
    assert proof['old_version'] == '1.26.9'
    assert proof['new_version'] == '1.26.10'
    assert proof['old_assertion_line'] == 5
    assert set(proof['source_sha256']) == {'old', 'new'}
    assert set(proof['source_sha256']['old']) == {CI, NETWORK, TEST}
    assert old == sources()[0] and new == sources()[1]


@pytest.mark.parametrize('mutation', [
    'downgrade', 'minor', 'huge', 'partial_ci', 'extra_ci', 'workflow_change',
    'wrong_network', 'no_network', 'no_old_rejection', 'wrong_count',
    'security_deleted', 'other_test_changed', 'setup_changed', 'fixture_binding',
    'distant_binding', 'unicode_binding', 'skipped', 'uncalled', 'docstring',
    'unreachable', 'partial_file', 'missing_file', 'extra_file', 'no_newline',
    'oversized', 'invalid_python', 'old_unmatched_context',
])
def test_ambiguous_or_weakened_source_is_rejected(mutation):
    old, new = sources()
    if mutation in ('downgrade', 'minor', 'huge'):
        version = {'downgrade': '1.26.8', 'minor': '1.27.10', 'huge': '1.26.' + '9' * 4301}[mutation]
        new = {p: t.replace('1.26.10', version) for p, t in new.items()}
    elif mutation == 'partial_ci': new[CI] = new[CI].replace('1.26.10', '1.26.9', 1)
    elif mutation == 'extra_ci': new[CI] += '    go-version: "1.26.10"\n'
    elif mutation == 'workflow_change': new[CI] += '    allow_unsafe: true\n'
    elif mutation == 'wrong_network': new[NETWORK] = old[NETWORK]
    elif mutation == 'no_network': new[TEST] = new[TEST].replace("    assert cross_network.count('go-version: \"1.26.10\"') == 1\n", '')
    elif mutation == 'no_old_rejection': new[TEST] = new[TEST].replace("    assert '1.26.9' not in WORKFLOW + cross_network\n", '')
    elif mutation == 'wrong_count': new[TEST] = new[TEST].replace('== 2', '== 1')
    elif mutation == 'security_deleted': new[TEST] = new[TEST].replace("    assert '1.25.12' not in WORKFLOW\n", '')
    elif mutation == 'other_test_changed': new[TEST] = new[TEST].replace("'denied' != 'allowed'", 'True')
    elif mutation == 'setup_changed': new[TEST] = new[TEST].replace('ROOT = Path(__file__).parent', "ROOT = Path('/tmp')")
    elif mutation == 'fixture_binding':
        old[TEST] = old[TEST].replace('def test_toolchain()', 'def test_toolchain(cross_network)')
        new[TEST] = new[TEST].replace('def test_toolchain()', 'def test_toolchain(cross_network)')
    elif mutation in ('distant_binding', 'unicode_binding'):
        name = 'cross_network' if mutation == 'distant_binding' else 'ｃｒｏｓｓ＿ｎｅｔｗｏｒｋ'
        for d in (old, new): d[TEST] = name + ' = None\n' + d[TEST]
    elif mutation == 'skipped': new[TEST] = new[TEST].replace('def test_toolchain()', '@pytest.mark.skip\ndef test_toolchain()')
    elif mutation in ('uncalled', 'unreachable', 'docstring'):
        start = new[TEST].index('    assert WORKFLOW.count')
        end = new[TEST].index("    assert '1.25.12'")
        added = new[TEST][start:end]
        if mutation == 'docstring': wrapped = '    """\n' + added + '    """\n'
        else: wrapped = ('    def uncalled():\n' if mutation == 'uncalled' else '    if False:\n') + ''.join('    ' + r + '\n' for r in added.splitlines())
        new[TEST] = new[TEST][:start] + wrapped + new[TEST][end:]
    elif mutation == 'partial_file': new[TEST] = new[TEST][:new[TEST].index('def test_other_security_contract')]
    elif mutation == 'missing_file': del old[NETWORK]
    elif mutation == 'extra_file': new['unrelated.py'] = 'pass\n'
    elif mutation == 'no_newline': new[TEST] = new[TEST].rstrip('\n')
    elif mutation == 'oversized': old[TEST] += '#' * 128000 + '\n'
    elif mutation == 'invalid_python': new[TEST] += 'def broken(\n'
    elif mutation == 'old_unmatched_context': old[TEST] = '"""\n' + old[TEST]
    frozen = copy.deepcopy((old, new))
    assert prove_transition(old, new) is None
    assert (old, new) == frozen


def test_comment_shaped_docstring_delimiter_outside_diff_is_not_source_evidence():
    old, new = sources()
    prefix = old[TEST][:old[TEST].index('def test_toolchain')]
    old[TEST] = prefix + '''def test_actual():
    """The opener is outside an ordinary three-line diff hunk.
def test_notes():
    # """
    assert WORKFLOW.count('go-version: "1.26.9"') == 2
    assert '1.25.12' not in WORKFLOW
'''
    replacement = new[TEST][new[TEST].index('    assert WORKFLOW.count'):new[TEST].index("    assert '1.25.12'")]
    new[TEST] = old[TEST].replace('    # """\n', replacement + '    # """\n').replace(
        '    assert WORKFLOW.count(\'go-version: "1.26.9"\') == 2\n', '')
    assert ast.parse(old[TEST]) and ast.parse(new[TEST])
    assert prove_transition(old, new) is None

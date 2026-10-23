"""Binding attacks must be refused without importing or executing submitted code."""
import ast

import pytest

from cluster.control_plane.local_karen.oracle_bindings import readonly_oracle_bindings


SOURCE = '''from __future__ import annotations
import os
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
TRANSPORT_GO_MOD = (ROOT / 'transport' / 'go.mod').read_text(encoding='utf-8')
def _job(name: str) -> str:
    match = re.search(re.escape(name), WORKFLOW)
    assert match is not None
    return match.group(0)
def test_toolchain() -> None:
    assert WORKFLOW.count('go-version: "1.26.9"') == 2
    assert re.search(r'(?m)^go 1\\.25\\.13$', TRANSPORT_GO_MOD)
'''


@pytest.mark.parametrize('root', ['Path(__file__).resolve().parents[1]', 'Path(__file__).parent'])
def test_readonly_contract_accepts_complete_standard_module_and_does_not_mutate_it(root):
    tree = ast.parse(SOURCE.replace('Path(__file__).resolve().parents[1]', root))
    before = ast.dump(tree, include_attributes=True)
    assert readonly_oracle_bindings(tree)
    assert ast.dump(tree, include_attributes=True) == before


@pytest.mark.parametrize('addition', [
    "re.search = lambda *args: True",
    "re = object()",
    "from types import SimpleNamespace\nre = SimpleNamespace(search=lambda *args: True)",
    "import sys\noriginal_search = re.search\nre.search = lambda p,s: 'cross_network' in sys._getframe(1).f_locals or original_search(p,s)",
    "def replace():\n    global re\n    re = None",
    "def replace():\n    re.search = None",
    "def replace():\n    globals()['re'] = None",
    "def replace():\n    import re as alias\n    alias.search = None",
    "alias = re\nalias.search = None",
    "alias = re.search\nalias.__globals__['search'] = None",
    "setattr(re, 'search', None)",
    "re.__dict__['search'] = None",
    "exec('re.search = None')",
    "unknown_hook()",
    "def hook(value):\n    return value\nhook(re)",
    "def re():\n    return None",
    "Path = None",
    "def hook(Path):\n    return Path",
    "class Replacer:\n    pass",
    "def hook():\n    def nested():\n        return None\n    return nested",
    "def hook():\n    del re",
    "def hook():\n    yield re",
    "async def hook():\n    pass",
    "ROOT = Path('/other')",
    "import re",
])
def test_rebinding_or_dynamic_execution_is_refused(addition):
    assert not readonly_oracle_bindings(ast.parse(SOURCE + addition + '\n'))


@pytest.mark.parametrize('replacement', [
    'def test_toolchain(re) -> None:',
    "def test_toolchain(value=unknown_hook()) -> None:",
    "@unknown_hook()\ndef test_toolchain() -> None:",
    'def test_toolchain(*args) -> None:',
])
def test_pytest_fixture_defaults_and_decorators_cannot_supply_hidden_bindings(replacement):
    changed = SOURCE.replace('def test_toolchain() -> None:', replacement)
    assert not readonly_oracle_bindings(ast.parse(changed))


def test_nonmodule_is_not_complete_source():
    assert not readonly_oracle_bindings(ast.parse('1', mode='eval'))


def test_namespace_must_be_imported_not_assumed_from_ambient_globals():
    assert not readonly_oracle_bindings(ast.parse(SOURCE.replace('import re\n', '')))

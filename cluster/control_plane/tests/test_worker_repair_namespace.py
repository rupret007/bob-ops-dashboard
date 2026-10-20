import importlib.util
import sys

import pytest

from cluster.control_plane import fix_worker


def test_repair_uses_its_executing_namespace_after_alias_is_removed(monkeypatch):
    name = 'cluster.control_plane._worker_alias_fixture'
    spec = importlib.util.spec_from_file_location(name, fix_worker.__file__)
    alias = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, alias)
    spec.loader.exec_module(alias)
    monkeypatch.delitem(sys.modules, name)

    class OwnNamespace(Exception):
        pass

    class WrongNamespace(Exception):
        pass

    def own():
        raise OwnNamespace()

    def wrong():
        raise WrongNamespace()

    monkeypatch.setattr(alias, '_find_codex', own)
    monkeypatch.setattr(fix_worker, '_find_codex', wrong)
    with pytest.raises(OwnNamespace):
        alias._fix_pr('fixture/project', 1, 'a' * 40, [])

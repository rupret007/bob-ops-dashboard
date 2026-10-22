"""The facade must preserve the namespace whose API the caller patched."""
import importlib.util
import sys

import pytest

from cluster.control_plane import worker_runtime


def test_runtime_validation_uses_its_executing_namespace(monkeypatch):
    name = 'cluster.control_plane._runtime_alias_fixture'
    spec = importlib.util.spec_from_file_location(name, worker_runtime.__file__)
    alias = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, alias)
    spec.loader.exec_module(alias)
    class OwnNamespace(Exception):
        pass
    class WrongNamespace(Exception):
        pass
    def own(condition):
        raise OwnNamespace()
    def wrong(condition):
        raise WrongNamespace()
    monkeypatch.setattr(alias, '_require', own)
    monkeypatch.setattr(worker_runtime, '_require', wrong)
    with pytest.raises(OwnNamespace):
        alias._qualified_runtime('a' * 40, None)

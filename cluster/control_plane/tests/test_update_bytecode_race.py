"""A concurrent bytecode rename must not abort runtime installation."""
import pytest

from cluster.control_plane.self_update import copy_karen_module


@pytest.mark.parametrize("package", ["local_karen", "dashboard"])
@pytest.mark.parametrize("artifact", ["__pycache__/module.cpython-314.pyc.123", "module.pyc", "module.pyc.123"])
def test_transient_bytecode_is_not_a_required_runtime_source(tmp_path, package, artifact):
    source, installed = tmp_path / "checkout", tmp_path / "installed"
    control = source / "cluster/control_plane"
    for name in ("local_karen", "dashboard"):
        directory = control / name
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("")
        (directory / "runtime.py").write_text("VALUE = 7\n")
    bytecode = control / package / artifact
    bytecode.parent.mkdir(parents=True, exist_ok=True)
    # A vanished temporary bytecode entry deterministically represents the
    # source-name race observed while the actual normal installer copied it.
    bytecode.symlink_to(tmp_path / "already-renamed-bytecode")
    assert copy_karen_module(source, installed) is True
    for name in ("local_karen", "dashboard"):
        assert (installed / "cluster/control_plane" / name / "runtime.py").read_text() == "VALUE = 7\n"
    assert not list(installed.rglob("__pycache__"))
    assert not list(installed.rglob("*.pyc*"))

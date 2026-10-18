"""Installed package must support poller updates without optional fix imports."""
import os
import subprocess
import sys
from pathlib import Path
from cluster.control_plane.self_update import copy_karen_module


def test_installed_dependency_closure(tmp_path):
    root = Path(__file__).resolve().parents[3]
    assert copy_karen_module(root, tmp_path / 'module')
    result = subprocess.run([sys.executable, '-c',
        'from cluster.control_plane.local_karen.poller import self_update, BACKLOG_AVAILABLE, FIX_DISPATCH_AVAILABLE; '
        'assert callable(self_update); assert BACKLOG_AVAILABLE; assert FIX_DISPATCH_AVAILABLE'],
        cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(tmp_path / 'module')}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_optional_fix_import_failure_does_not_disable_updates(tmp_path):
    root = Path(__file__).resolve().parents[3]
    assert copy_karen_module(root, tmp_path / 'module')
    (tmp_path / 'module/cluster/control_plane/fix_dispatcher.py').write_text('raise ImportError("optional fix failure")')
    result = subprocess.run([sys.executable, '-c',
        'from cluster.control_plane.local_karen.poller import self_update, BACKLOG_AVAILABLE, FIX_DISPATCH_AVAILABLE; '
        'assert callable(self_update); assert BACKLOG_AVAILABLE; assert not FIX_DISPATCH_AVAILABLE'],
        cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(tmp_path / 'module')}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

"""A global CLI upgrade must not replace StoryPool's qualified worker runtime."""
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from cluster.control_plane import cursor_worker as cursor


def installation(tmp_path, monkeypatch):
    versions = tmp_path / 'versions'
    current = versions / 'unqualified' / 'cursor-agent'
    pinned = versions / cursor.VERSION / 'cursor-agent'
    for path in [current, pinned]:
        path.parent.mkdir(parents=True)
        path.write_text('#!/bin/sh\nexit 99\n')
        path.chmod(0o755)
    link = tmp_path / 'cursor-agent'
    link.symlink_to(current)
    monkeypatch.setattr(cursor.shutil, 'which', lambda name: str(link))
    return link, current, pinned


@pytest.mark.parametrize('qualified_path', [False, True])
def test_check_and_launch_use_same_qualified_runtime(tmp_path, monkeypatch, qualified_path):
    link, current, pinned = installation(tmp_path, monkeypatch)
    if qualified_path:
        link.unlink()
        link.symlink_to(pinned)
    before = link.readlink()
    replies = [subprocess.CompletedProcess([], 0, cursor.VERSION),
               subprocess.CompletedProcess([], 0, '{"isAuthenticated":true}')]
    with patch.object(cursor.subprocess, 'run', side_effect=replies) as run:
        assert cursor.available()
    assert all(Path(call.args[0][0]).resolve() == pinned for call in run.call_args_list)
    assert cursor.command() == [str(pinned), '--print', '--trust', '--sandbox',
                                'enabled', '--output-format', 'json']
    assert link.readlink() == before
    assert current.read_text() == pinned.read_text() == '#!/bin/sh\nexit 99\n'


@pytest.mark.parametrize('unsafe', ['missing', 'directory', 'no-execute', 'writable',
                                   'file-symlink', 'directory-symlink'])
def test_unsafe_sibling_never_becomes_worker_capacity(tmp_path, monkeypatch, unsafe):
    _, _, pinned = installation(tmp_path, monkeypatch)
    if unsafe in ['missing', 'directory', 'file-symlink']:
        pinned.unlink()
        if unsafe == 'directory':
            pinned.mkdir()
        elif unsafe == 'file-symlink':
            outside = tmp_path / 'outside'
            outside.write_text('unchanged')
            outside.chmod(0o755)
            pinned.symlink_to(outside)
    elif unsafe == 'directory-symlink':
        original = pinned.parent
        renamed = original.with_name('outside-version')
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
    else:
        pinned.chmod(0o644 if unsafe == 'no-execute' else 0o775)
    with patch.object(cursor.subprocess, 'run', return_value=
                      subprocess.CompletedProcess([], 0, 'unqualified')) as run:
        assert not cursor.available()
        assert len(run.call_args_list) == 1
    with pytest.raises(RuntimeError, match='qualification'):
        cursor.command()


def test_wrong_binary_version_cannot_reach_auth_check(tmp_path, monkeypatch):
    _, _, pinned = installation(tmp_path, monkeypatch)
    with patch.object(cursor.subprocess, 'run', return_value=
                      subprocess.CompletedProcess([], 0, 'unqualified')) as run:
        assert not cursor.available()
    assert run.call_args.args[0] == [str(pinned), '--version']
    assert run.call_count == 1


def test_another_owners_sibling_is_not_selected(tmp_path, monkeypatch):
    _, _, pinned = installation(tmp_path, monkeypatch)
    with patch.object(cursor.os, 'getuid', return_value=os.getuid() + 1), patch.object(
            cursor.subprocess, 'run', return_value=
            subprocess.CompletedProcess([], 0, 'unqualified')) as run:
        assert not cursor.available()
        assert Path(run.call_args.args[0][0]).resolve() != pinned
        with pytest.raises(RuntimeError):
            cursor.command()


def test_unrelated_layout_is_not_searched_for_siblings(tmp_path, monkeypatch):
    _, current, pinned = installation(tmp_path, monkeypatch)
    other = tmp_path / 'bin' / 'unqualified' / 'cursor-agent'
    other.parent.mkdir(parents=True)
    current.rename(other)
    monkeypatch.setattr(cursor.shutil, 'which', lambda name: str(other))
    with patch.object(cursor.subprocess, 'run', return_value=
                      subprocess.CompletedProcess([], 0, 'unqualified')) as run:
        assert not cursor.available()
    assert run.call_args.args[0] == [str(other), '--version']
    with pytest.raises(RuntimeError):
        cursor.command()
    assert pinned.is_file()

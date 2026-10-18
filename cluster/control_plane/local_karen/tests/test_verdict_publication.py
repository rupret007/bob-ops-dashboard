"""Readers retain a complete LATEST marker when verdict publication fails."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cluster.control_plane import conductor
from cluster.control_plane.local_karen import verdict as storage
from cluster.control_plane.local_karen.config import KarenConfig


@pytest.fixture
def published(tmp_path):
    config = KarenConfig(verdict_dir=str(tmp_path))
    old = storage.Verdict('owner', 'repo', 7, 'a' * 40, False, 'Needs a fix',
        '2026-10-18T19:00:00+00:00', 'codex', 'low', 1.0, [], [])
    storage.save_verdict(old, config)
    latest = next(tmp_path.glob('*_LATEST.json'))
    new = replace(old, passed=True, reason='Reviewed',
        timestamp='2026-10-18T20:00:00+00:00', review_results=[
            {'reviewer': 'codex', 'verdict': 'PASS'},
            {'reviewer': 'gemini', 'verdict': 'PASS'}])
    return config, old, new, latest


def test_latest_is_never_unlinked_before_replacement(published):
    config, old, new, latest = published
    unlink = Path.unlink
    def checked(path, *args, **kwargs):
        assert path != latest, 'Publication must atomically replace LATEST'
        return unlink(path, *args, **kwargs)
    with patch.object(Path, 'unlink', checked):
        storage.save_verdict(new, config)
    assert json.loads(latest.read_text())['passed'] is True
    assert conductor._read_flat_verdict(latest.parent, 'owner/repo', 7, new.head_sha)['verdict'] == 'KARENPASS'


def test_pointer_creation_failure_preserves_previous_marker(published):
    config, old, new, latest = published
    with patch.object(Path, 'symlink_to', side_effect=OSError('injected link failure')):
        with pytest.raises(OSError):
            storage.save_verdict(new, config)
    assert latest.is_symlink() and json.loads(latest.read_text())['passed'] is False
    # A newer PASS sibling cannot bypass the retained FAIL marker.
    assert conductor._read_flat_verdict(latest.parent, 'owner/repo', 7, new.head_sha)['verdict'] == 'KARENFAIL'
    assert not list(latest.parent.glob('.verdict-*'))


def test_pointer_replace_failure_preserves_marker_and_cleans_temporary(published):
    config, old, new, latest = published
    original = storage.os.replace
    def fail_pointer(source, dest):
        if Path(dest) == latest:
            raise OSError('injected pointer replace failure')
        return original(source, dest)
    with patch.object(storage.os, 'replace', side_effect=fail_pointer):
        with pytest.raises(OSError):
            storage.save_verdict(new, config)
    assert json.loads(latest.read_text())['passed'] is False
    assert not list(latest.parent.glob('.verdict-*'))


@pytest.mark.parametrize('stage', ['serialize', 'artifact_replace'])
def test_artifact_failure_preserves_marker_and_cleans_temporary(published, stage):
    config, old, new, latest = published
    target = (patch.object(storage.Verdict, 'to_json', side_effect=ValueError('injected serialization failure'))
              if stage == 'serialize' else patch.object(storage.os, 'replace', side_effect=OSError('injected artifact replace failure')))
    with target, pytest.raises((ValueError, OSError)):
        storage.save_verdict(new, config)
    assert json.loads(latest.read_text())['passed'] is False
    assert not list(latest.parent.glob('.verdict-*'))


def test_newer_fail_remains_blocking_if_latest_replace_fails(published):
    config, old, new, latest = published
    storage.save_verdict(new, config)
    failing = replace(old, timestamp='2026-10-18T21:00:00+00:00')
    with patch.object(Path, 'symlink_to', side_effect=OSError('injected link failure')):
        with pytest.raises(OSError):
            storage.save_verdict(failing, config)
    assert latest.is_symlink()
    assert conductor._read_flat_verdict(latest.parent, 'owner/repo', 7, new.head_sha)['verdict'] == 'KARENFAIL'

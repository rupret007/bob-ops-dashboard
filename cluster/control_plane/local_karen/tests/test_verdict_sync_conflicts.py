"""Synchronization can add missing artifacts without rewriting local evidence."""
import json
import os
from unittest import mock

import pytest

from cluster.control_plane.local_karen import verdict_sync
from cluster.control_plane.local_karen.tests.test_verdict_sync import _Resp, _name, _verdict


@pytest.mark.parametrize('local_time,incoming_time', [
    ('2026-01-02T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
    ('2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00'),
])
def test_conflicting_artifact_is_preserved_while_missing_artifact_is_added(
        tmp_path, local_time, incoming_time, caplog):
    local = _verdict()
    local.timestamp = local_time
    local.reason = 'Existing completed observation'
    incoming = _verdict()
    incoming.timestamp = incoming_time
    incoming.reason = 'Different completed observation'
    missing = _verdict(pr=8)
    existing = tmp_path / _name(local)
    original = local.to_json()
    existing.write_text(original)
    body = json.dumps({'verdicts': {_name(incoming): incoming.to_json(),
                                   _name(missing): missing.to_json()}}).encode()
    with mock.patch.object(verdict_sync.urllib.request, 'urlopen', return_value=_Resp(body)):
        assert verdict_sync.pull('http://private-peer', tmp_path) == 1
    assert existing.read_text() == original
    assert (tmp_path / _name(missing)).read_text() == missing.to_json()
    assert 'conflicts with local evidence' in caplog.text
    assert incoming.reason not in caplog.text


def test_unreadable_existing_artifact_is_never_replaced(tmp_path, caplog):
    local = _verdict()
    existing = tmp_path / _name(local)
    existing.write_text(local.to_json())
    original = existing.read_bytes()
    body = json.dumps({'verdicts': {_name(local): local.to_json()}}).encode()
    with mock.patch.object(verdict_sync.urllib.request, 'urlopen', return_value=_Resp(body)), \
            mock.patch('pathlib.Path.read_text', side_effect=PermissionError):
        assert verdict_sync.pull('http://private-peer', tmp_path) == 0
    assert existing.read_bytes() == original
    assert 'unreadable' in caplog.text


def test_identical_existing_artifact_needs_no_write(tmp_path):
    local = _verdict()
    existing = tmp_path / _name(local)
    existing.write_text(local.to_json())
    before = existing.stat().st_ino
    body = json.dumps({'verdicts': {_name(local): local.to_json()}}).encode()
    with mock.patch.object(verdict_sync.urllib.request, 'urlopen', return_value=_Resp(body)):
        assert verdict_sync.pull('http://private-peer', tmp_path) == 0
    assert existing.stat().st_ino == before


def test_concurrent_publication_wins_without_leaving_temporary_files(tmp_path):
    incoming = _verdict()
    concurrent = _verdict()
    concurrent.reason = 'Published by the local review while sync was in progress'
    body = json.dumps({'verdicts': {_name(incoming): incoming.to_json()}}).encode()
    destination = tmp_path / _name(incoming)
    link = os.link

    def publish_before_link(source, target):
        destination.write_text(concurrent.to_json())
        return link(source, target)

    with mock.patch.object(verdict_sync.urllib.request, 'urlopen', return_value=_Resp(body)), \
            mock.patch.object(verdict_sync.os, 'link', side_effect=publish_before_link):
        assert verdict_sync.pull('http://private-peer', tmp_path) == 0
    assert destination.read_text() == concurrent.to_json()
    assert sorted(p.name for p in tmp_path.iterdir()) == [_name(incoming)]

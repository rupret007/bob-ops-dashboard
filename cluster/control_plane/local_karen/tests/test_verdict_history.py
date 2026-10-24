"""Authenticated replacement preserves observations without granting old authority."""
import fcntl
import hashlib
import io
import json
import os
from datetime import datetime, timezone

import pytest

from cluster.control_plane.local_karen import verdict_push as delivery
from cluster.control_plane.local_karen import verdict_sync as sync
from cluster.control_plane.local_karen import verdict as storage
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.tests.test_verdict_push import keys, signed, verdict


def observation(passed, when):
    value = verdict()
    value.passed = passed
    value.timestamp = datetime.fromtimestamp(when, timezone.utc).isoformat()
    if passed:
        value.review_results[0]['verdict'] = 'PASS'
        value.review_results.append(dict(value.review_results[0], reviewer='minimax'))
    return value


@pytest.fixture
def stored(tmp_path, keys):
    directory = tmp_path / 'verdicts'
    def deliver(value, now=1100):
        body, signature = signed(keys, value, issued=now)
        return delivery.receive(body, signature, directory, keys[1], now=now)
    old = observation(False, 1000)
    deliver(old, 1000)
    return directory, old, deliver


def archive_path(directory, value):
    return directory / 'history' / (hashlib.sha256(value.to_json().encode()).hexdigest() + '.json')


@pytest.mark.parametrize('before,after', [(False, True), (True, False), (False, False), (True, True)])
def test_replacement_keeps_old_bytes_and_only_new_current_authority(tmp_path, keys, before, after):
    directory = tmp_path / 'verdicts'
    for passed, when in [(before, 1000), (after, 1100)]:
        value = observation(passed, when)
        body, sig = signed(keys, value, issued=when)
        delivery.receive(body, sig, directory, keys[1], now=when)
    old = observation(before, 1000)
    assert archive_path(directory, old).read_bytes() == old.to_json().encode()
    assert archive_path(directory, old).stat().st_mode & 0o777 == 0o600
    current = storage.load_verdict('owner', 'repo', 1, 'a' * 40,
                                  KarenConfig(verdict_dir=str(directory)))
    assert current.to_dict() == value.to_dict()
    assert storage.load_latest_verdict('owner', 'repo', 1,
               KarenConfig(verdict_dir=str(directory))).to_dict() == value.to_dict()
    assert all('history' not in name for name in sync.collect_verdicts(directory))


def test_identical_delivery_does_not_create_fake_history(stored):
    directory, old, deliver = stored
    deliver(old)
    assert not (directory / 'history').exists()


@pytest.mark.parametrize('unsafe', ['directory_symlink', 'file_symlink', 'corrupt'])
def test_untrusted_archive_refuses_before_current_or_marker_change(stored, tmp_path, unsafe):
    directory, old, deliver = stored
    history = directory / 'history'
    if unsafe == 'directory_symlink':
        history.symlink_to(tmp_path / 'elsewhere', target_is_directory=True)
    else:
        history.mkdir(mode=0o700)
        target = archive_path(directory, old)
        if unsafe == 'file_symlink':
            target.symlink_to(tmp_path / 'missing')
        else:
            target.write_bytes(b'Synthetic corrupt history')
    before = {p.name: p.read_bytes() for p in directory.glob('*.json')}
    with pytest.raises((OSError, ValueError)):
        deliver(observation(True, 1100))
    assert {p.name: p.read_bytes() for p in directory.glob('*.json')} == before


def test_archive_write_failure_preserves_current_decision(stored, monkeypatch):
    directory, old, deliver = stored
    before = {p.name: p.read_bytes() for p in directory.glob('*.json')}
    def fail(*args, **kwargs):
        raise OSError('Synthetic archive publication failure')
    monkeypatch.setattr(delivery.os, 'link', fail)
    with pytest.raises(OSError):
        deliver(observation(True, 1100))
    assert {p.name: p.read_bytes() for p in directory.glob('*.json')} == before
    assert not list((directory / 'history').glob('.archive-*'))


def test_interrupted_current_publication_already_has_historical_bytes(stored, monkeypatch):
    directory, old, deliver = stored
    def fail(*args, **kwargs):
        raise OSError('Synthetic current publication failure')
    monkeypatch.setattr(delivery.os, 'replace', fail)
    with pytest.raises(OSError):
        deliver(observation(True, 1100))
    assert archive_path(directory, old).read_bytes() == old.to_json().encode()
    assert storage.load_latest_verdict('owner', 'repo', 1,
               KarenConfig(verdict_dir=str(directory))).to_dict() == old.to_dict()


def test_existing_identical_archive_is_not_replaced(stored):
    directory, old, deliver = stored
    (directory / 'history').mkdir(mode=0o700)
    path = archive_path(directory, old)
    path.write_bytes(old.to_json().encode())
    before = (path.stat().st_ino, path.stat().st_mtime_ns)
    deliver(observation(True, 1100))
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == before


@pytest.mark.parametrize('invalid', ['tampered', 'expired'])
def test_rejected_input_never_creates_history(stored, keys, invalid):
    directory, _, _ = stored
    body, sig = signed(keys, observation(True, 1100), issued=1100)
    with pytest.raises(PermissionError):
        delivery.receive(body + (b' ' if invalid == 'tampered' else b''), sig,
                         directory, keys[1], now=1500 if invalid == 'expired' else 1100)
    assert not (directory / 'history').exists()


def test_stale_input_does_not_archive_or_rollback_current(stored):
    directory, old, deliver = stored
    with pytest.raises(ValueError, match='older'):
        deliver(observation(True, 900))
    assert not (directory / 'history').exists()
    assert storage.load_latest_verdict('owner', 'repo', 1,
               KarenConfig(verdict_dir=str(directory))).to_dict() == old.to_dict()


def test_local_review_publication_preserves_overwritten_same_outcome(stored):
    directory, old, _ = stored
    storage.save_verdict(observation(False, 1100), KarenConfig(verdict_dir=str(directory)))
    assert archive_path(directory, old).read_bytes() == old.to_json().encode()


@pytest.mark.parametrize('writer', ['receive', 'save', 'pull'])
def test_all_writers_serialize_through_existing_store_lock(stored, monkeypatch, writer):
    directory, _, deliver = stored
    original = delivery.os.link if writer == 'pull' else delivery.os.replace
    locked = []
    def observe(*args, **kwargs):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        locked.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(delivery.os, 'link' if writer == 'pull' else 'replace', observe)
    value = observation(False, 1100)
    if writer == 'receive':
        deliver(value)
    elif writer == 'save':
        storage.save_verdict(value, KarenConfig(verdict_dir=str(directory)))
    else:
        value.head_sha = 'b' * 40
        name = storage._verdict_filename('owner', 'repo', 1, value.head_sha) + '_KARENFAIL.json'
        monkeypatch.setattr(sync.urllib.request, 'urlopen',
                            lambda *args, **kwargs: io.BytesIO(json.dumps({'verdicts': {name: value.to_json()}}).encode()))
        assert sync.pull('http://localhost/fixture', directory) == 1
    assert locked


@pytest.mark.parametrize('writer', ['save', 'pull'])
@pytest.mark.parametrize('mask', [0o000, 0o002, 0o077])
def test_first_write_creates_private_store_with_permissive_umask(tmp_path, monkeypatch, writer, mask):
    directory = tmp_path / 'new-store'
    value = observation(False, 1000)
    name = storage._verdict_filename('owner', 'repo', 1, value.head_sha) + '_KARENFAIL.json'
    monkeypatch.setattr(sync.urllib.request, 'urlopen',
                        lambda *args, **kwargs: io.BytesIO(json.dumps({'verdicts': {name: value.to_json()}}).encode()))
    previous = os.umask(mask)
    try:
        if writer == 'save':
            storage.save_verdict(value, KarenConfig(verdict_dir=str(directory)))
        else:
            assert sync.pull('http://localhost/fixture', directory) == 1
    finally:
        os.umask(previous)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert storage.load_verdict('owner', 'repo', 1, value.head_sha,
                               KarenConfig(verdict_dir=str(directory))).to_dict() == value.to_dict()

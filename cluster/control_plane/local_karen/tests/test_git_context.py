"""Transport isolation and finite resources, using only disposable local Git."""
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import socket
import threading

import pytest

from cluster.control_plane.local_karen import git_context as g
from cluster.control_plane.local_karen.tests.test_diff_context import packet


def test_read_disables_external_driver_and_ignores_foreign_git_environment(packet, monkeypatch):
    pr, base, candidate, _, native_git, directory = packet
    marker = directory / 'driver-ran'
    driver = directory / 'external-driver'
    driver.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
    driver.chmod(0o700)
    native_git('config', 'diff.external', str(driver))
    monkeypatch.setenv('GIT_DIR', str(directory / 'foreign'))
    monkeypatch.setenv('GIT_OBJECT_DIRECTORY', str(directory / 'foreign-objects'))
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'diff.external')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(driver))
    shared = SimpleNamespace(seconds=30)
    git = g.BoundedGit(directory, shared)
    output = git.run('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--full-index',
                     '--function-context', '--unified=16', base, pr.head_sha)
    assert output.decode() == candidate.diff
    assert not marker.exists() and 0 < shared.seconds < 30
    assert all(not k.startswith('GIT_CONFIG_') for k in git.environment)
    assert 'GIT_OBJECT_DIRECTORY' not in git.environment


def test_git_hashes_same_verified_blob_and_has_finite_output_bound(packet):
    _, _, candidate, _, _, directory = packet
    sha = candidate.blobs['src/runtime.py'][1].sha
    git = g.BoundedGit(directory, SimpleNamespace(seconds=30))
    assert git.run('cat-file', 'blob', sha) == candidate.blobs['src/runtime.py'][1].data
    with pytest.raises(g.ContextUnavailable, match='response_size'):
        git.run('cat-file', 'blob', sha, maximum=10)


def _https_connection_observed(packet, network):
    *_, native_git, directory = packet
    # Only this disposable fixture's config changes. No credential helper or
    # proxy is needed for a loopback transport-denial probe.
    native_git('config', 'credential.helper', '')
    native_git('config', 'http.proxy', '')
    connected = threading.Event()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(1)
        port = listener.getsockname()[1]
        def accept_once():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                return
            with client:
                connected.set()
        server = threading.Thread(target=accept_once)
        server.start()
        try:
            git = g.BoundedGit(directory, SimpleNamespace(seconds=3))
            with pytest.raises(g.ContextUnavailable, match='git_read'):
                git.run('fetch', 'https://127.0.0.1:{}/never.git'.format(port),
                        'a' * 40, network=network)
        finally:
            server.join(timeout=2)
        assert not server.is_alive()
    # Both invocations fail because the peer is no Git server. The observable
    # connection distinguishes protocol denial from a refused/invalid peer.
    # The allowed case is a real positive control for this same Git transport.
    return connected.is_set()


def test_network_is_denied_even_when_git_command_requests_https(packet):
    assert _https_connection_observed(packet, False) is False


def test_explicitly_admitted_prefetch_reaches_the_same_https_peer(packet):
    assert _https_connection_observed(packet, True) is True


def test_expired_time_budget_never_starts_a_transport(packet, monkeypatch):
    *_, directory = packet
    def forbidden(*args, **kwargs):
        raise AssertionError('expired budget must not spawn a child')
    monkeypatch.setattr(g.subprocess, 'Popen', forbidden)
    with pytest.raises(g.ContextUnavailable, match='time_budget'):
        g.BoundedGit(directory, SimpleNamespace(seconds=0)).run('status')


def test_local_git_template_cannot_inject_an_alternate_object_database(tmp_path, monkeypatch):
    template = tmp_path / 'foreign-template'
    (template / 'objects/info').mkdir(parents=True)
    (template / 'objects/info/alternates').write_text(str(tmp_path / 'foreign-objects') + '\n')
    original = g.BoundedGit.run
    checked = []
    def probe(git, *args, **options):
        if args[0] == 'init':
            # Simulate a configured template without changing HOME, global
            # config, a credential helper or any real repository.
            result = original(git, '-c', 'init.templateDir=' + str(template), *args, **options)
            assert not (Path(git.directory) / 'objects/info/alternates').exists()
            checked.append(True)
            return result
        if args[0] == 'fetch':
            raise g.ContextUnavailable('fixture_stop_before_network')
        return original(git, *args, **options)
    monkeypatch.setattr(g.BoundedGit, 'run', probe)
    pr = SimpleNamespace(owner='owner', repo='repo', head_sha='a' * 40)
    with pytest.raises(g.ContextUnavailable, match='fixture_stop_before_network'):
        g.fetch_context(pr, 'b' * 40, {'src/a.py': {}}, SimpleNamespace(seconds=30))
    assert checked == [True]


def test_timeout_kills_only_the_owned_transport_and_charges_budget(tmp_path, monkeypatch):
    # A fake git executable is a private child used to test bounded pipe reads;
    # this test never signals any existing service or unrelated process.
    executable = tmp_path / 'git'
    executable.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
    executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    shared = SimpleNamespace(seconds=.05)
    with pytest.raises(g.ContextUnavailable, match='time_budget'):
        g.BoundedGit(tmp_path, shared).run('status')
    assert shared.seconds <= 0


@pytest.mark.parametrize('owner,repo', [('owner;run', 'repo'), ('owner', '../repo'), ('owner', '..')])
def test_untrusted_repository_coordinates_never_create_temp_repository(owner, repo, monkeypatch):
    pr = SimpleNamespace(owner=owner, repo=repo, head_sha='a' * 40)
    def forbidden(*args, **kwargs):
        raise AssertionError('bad coordinates must not allocate/fetch')
    monkeypatch.setattr(g.tempfile, 'TemporaryDirectory', forbidden)
    with pytest.raises(g.ContextUnavailable, match='identity'):
        g.fetch_context(pr, 'b' * 40, {'src/a.py': {}}, SimpleNamespace(seconds=30))

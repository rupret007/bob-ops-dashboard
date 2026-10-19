"""Explicit SSH routing stays identical across reachability, quota and inference."""
import ast
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from cluster.control_plane import codex_quota_remote as remote, ssh_transport as ssh
from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.tests.test_codex_quota_remote import final_review, info, packet


def route_config():
    return KarenConfig(codex_host='nuc-wsl', codex_ssh_user='rupret',
                       codex_ssh_port=2222, codex_ssh_identity_file='~/.ssh/nuc_ed25519')


def test_explicit_route_has_only_fixed_flags_and_does_not_read_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    with patch.object(Path, 'read_text', side_effect=AssertionError('no key/config reads')):
        route = ssh.SSHTransport('nuc-wsl', 'rupret', 2222, '~/.ssh/nuc_ed25519')
        argv = route.argv()
    assert argv == ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                    '-i', str(tmp_path / '.ssh/nuc_ed25519'), '-o', 'IdentitiesOnly=yes',
                    '-p', '2222', 'rupret@nuc-wsl']


@pytest.mark.parametrize('host', ['nuc', 'user@nuc', '192.0.2.10', 'user@2001:db8::1'])
def test_defaults_preserve_existing_alias_or_account_routes(host):
    assert ssh.SSHTransport(host).argv() == ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host]


@pytest.mark.parametrize('values', [dict(host='-oProxyCommand=bad'), dict(host='nuc;bad'),
    dict(host='nuc\nother'), dict(host='ssh://nuc'), dict(host='nuc:2222'),
    dict(host='fe80::1%zone;bad'),
    dict(host='user@@nuc'), dict(host='user@nuc', user='other'), dict(user='-lroot'),
    dict(user='rupret;bad'), dict(user=None), dict(port=True), dict(port=0),
    dict(port=65536), dict(port='2222'), dict(port=22.0), dict(identity_file=None),
    dict(identity_file='-oProxyCommand=bad'), dict(identity_file='relative/key'),
    dict(identity_file='~other/key'), dict(identity_file='~/.ssh/../key'),
    dict(identity_file='/tmp/key\n-oProxyCommand=bad')])
def test_invalid_route_is_refused_before_any_process(values):
    with patch('subprocess.run') as run, patch('subprocess.Popen') as process:
        with pytest.raises(ValueError):
            ssh.SSHTransport(**dict(dict(host='nuc'), **values)).argv()
    run.assert_not_called()
    process.assert_not_called()


def test_key_path_spaces_or_shell_punctuation_remain_one_literal_argv_value():
    key = '/tmp/identity with spaces;$(no-command)'
    argv = ssh.SSHTransport('nuc', identity_file=key).argv()
    assert argv[argv.index('-i') + 1] == key
    assert len(argv) == 10


def test_preflight_quota_and_inference_share_one_frozen_route(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    config = route_config()
    calls, quota_routes = [], []
    def execute(argv, **kwargs):
        calls.append(argv)
        assert 'shell' not in kwargs
        if argv[-1].endswith('command -v codex >/dev/null'):
            return subprocess.CompletedProcess(argv, 0, '', '')
        assert kwargs['input'] and 'PRIVATE_PR_TEXT' in kwargs['input']
        assert 'PRIVATE_PR_TEXT' not in ' '.join(argv)
        assert '--sandbox read-only' in argv[-1] and argv[-1].endswith(' -')
        return subprocess.CompletedProcess(argv, 0, final_review(), '')
    def quota(route, **kwargs):
        quota_routes.append(route)
        # An external mutable config update cannot switch accounts mid-review.
        config.codex_host, config.codex_ssh_user, config.codex_ssh_port = 'other', 'other', 22
        config.codex_ssh_identity_file = '~/other_key'
        return {'weekly_pct': 21}
    pr = info()
    pr.description = 'PRIVATE_PR_TEXT'
    with patch.object(r.subprocess, 'run', side_effect=execute), \
         patch.object(r, 'read_remote_codex_quota', side_effect=quota), \
         patch.object(r, 'read_codex_quota', side_effect=AssertionError('wrong local quota')), \
         patch.object(Path, 'read_text', side_effect=AssertionError('no key/config reads')):
        result = r.CodexReviewer().review(pr, r.ReviewTier.LOW, config)
    assert result.verdict == r.ReviewVerdict.FAIL  # Mocked complete review parsed normally.
    assert len(calls) == 2 and len(quota_routes) == 1
    assert calls[0][:-1] == quota_routes[0].argv() == calls[1][:-1]
    assert calls[1][-2] == 'rupret@nuc-wsl'


def test_remote_quota_uses_explicit_route_and_fixed_reader_only(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    executable = tmp_path / 'fake-ssh'
    expected = ssh.SSHTransport('nuc-wsl', 'rupret', 2222, '~/.ssh/nuc_ed25519')
    executable.write_text('''#!/usr/bin/python3
import json,sys
assert sys.argv[1:-1] == PREFIX
command=sys.argv[-1]
assert command.startswith('exec "$HOME/.local/bin/storypool-codex-quota-read" --nonce ')
nonce=command.split()[-1]
assert len(nonce)==32 and all(c in '0123456789abcdef' for c in nonce)
value=PACKET
value['nonce']=nonce
print(json.dumps(value))
'''.replace('PREFIX', repr(expected.argv()[1:])).replace('PACKET', repr(packet())))
    executable.chmod(0o700)
    result = remote.read_remote_codex_quota(expected, executable=str(executable))
    assert result['weekly_pct'] == 29
    assert 'private' not in json.dumps(result)


def test_invalid_config_never_falls_back_or_borrows_local_quota():
    config = route_config()
    config.codex_ssh_port = True
    with patch('subprocess.run') as run, patch.object(r, 'read_codex_quota') as local, \
         patch.object(r, 'read_remote_codex_quota') as other:
        result = r.CodexReviewer().review(info(), r.ReviewTier.LOW, config)
    assert result.verdict == r.ReviewVerdict.ERROR
    run.assert_not_called()
    local.assert_not_called()
    other.assert_not_called()


def test_unknown_target_never_becomes_local_quota():
    with patch.object(r, 'read_codex_quota') as local:
        assert r.CodexReviewer()._get_weekly_usage_pct('unresolved-host') is None
    local.assert_not_called()


def test_config_fields_round_trip_without_changing_defaults(tmp_path):
    path = tmp_path / 'karen.json'
    config = route_config()
    config.save(path)
    loaded = KarenConfig.load(path)
    assert (loaded.codex_host, loaded.codex_ssh_user, loaded.codex_ssh_port, loaded.codex_ssh_identity_file) == (
        'nuc-wsl', 'rupret', 2222, '~/.ssh/nuc_ed25519')
    assert (KarenConfig().codex_ssh_user, KarenConfig().codex_ssh_port, KarenConfig().codex_ssh_identity_file) == ('', None, '')


def test_transport_module_supports_python39():
    ast.parse(Path(ssh.__file__).read_text(), feature_version=(3, 9))

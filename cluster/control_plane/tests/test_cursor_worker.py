"""An authenticated file editor never owns tests, Git publication or global policy."""
import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from cluster.control_plane import cursor_worker as cursor, dispatcher, fix_worker


@pytest.mark.parametrize('body,code,expected', [
    ('{"isAuthenticated":true}', 0, True), ('{"isAuthenticated":1}', 0, False),
    ('{"isAuthenticated":true}', 1, False), ('[]', 0, False), ('invalid', 0, False)])
def test_status_is_strict_and_does_not_return_account_data(body, code, expected, monkeypatch):
    monkeypatch.setenv('BOB_COORD_TOKEN', 'fictitious')
    with patch.object(cursor.shutil, 'which', return_value='/fixture/cursor'), patch.object(
            cursor.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, cursor.VERSION),
                subprocess.CompletedProcess([], code, body)]) as run:
        assert cursor.available() is expected
    assert run.call_args.args[0] == ['/fixture/cursor', 'status', '--format', 'json']
    assert 'BOB_COORD_TOKEN' not in run.call_args.kwargs['env']


def test_policy_restores_existing_configuration_even_when_provider_fails(tmp_path):
    folder = tmp_path / '.cursor'; folder.mkdir()
    original = b'{"permissions":{"deny":["Write(package.json)"]}}'
    (folder / 'cli.json').write_bytes(original)
    with pytest.raises(LookupError), cursor.policy(tmp_path) as env:
        deny = json.loads((folder / 'cli.json').read_text())['permissions']['deny']
        assert all(name in deny for name in ('Shell(*)', 'Mcp(*:*)', 'WebFetch(*)', 'Write(.cursor/**)'))
        assert 'Write(package.json)' in deny
        settings = Path(env['CURSOR_CONFIG_DIR'])
        assert json.loads((settings / 'cli-config.json').read_text())['sandbox']['readBoundary'] == 'workspace'
        assert json.loads((folder / 'sandbox.json').read_text())['readBoundary'] == 'workspace'
        raise LookupError()
    assert not settings.exists()
    assert (folder / 'cli.json').read_bytes() == original
    assert not (folder / 'sandbox.json').exists()


@pytest.mark.parametrize('linked', ['folder', 'file', 'tampered', 'replaced-folder'])
def test_policy_rejects_linked_or_modified_boundaries(tmp_path, linked):
    outside = tmp_path / 'outside'; outside.mkdir(); marker = outside / 'marker'; marker.write_text('unchanged')
    tree = tmp_path / 'tree'; tree.mkdir()
    if linked == 'folder': (tree / '.cursor').symlink_to(outside, target_is_directory=True)
    elif linked == 'file':
        (tree / '.cursor').mkdir(); (tree / '.cursor/cli.json').symlink_to(marker)
    with pytest.raises(RuntimeError), cursor.policy(tree):
        if linked == 'replaced-folder':
            (tree / '.cursor').rename(tree / 'previous-policy'); (tree / '.cursor').symlink_to(outside)
        else: (tree / '.cursor/cli.json').write_text('{}')
    assert marker.read_text() == 'unchanged'


def test_cursor_fills_nuc_capacity_while_codex_stays_capped(tmp_path):
    worker = dispatcher.Dispatcher('nucbox', tmp_path, budget_tracker=MagicMock())
    worker.budget.weekly_pct.return_value = 80
    with patch.object(cursor, 'available', return_value=True) as available:
        assert worker.check_provider_allowed('codex', {})[0] is False
        assert worker.select_provider({}, {'cursor': 4}) == 'cursor'
        assert worker.select_provider({}, {'cursor': 5}) == 'cursor'
        available.assert_called_once()
        worker.machine = 'mini'
        assert worker.check_provider_allowed('cursor', {})[0] is False
    with patch.object(dispatcher, '_has_codex_work_queued', return_value=False):
        worker.machine = 'nucbox'; assert worker.get_nucbox_lane_capacity({'cursor': 5}) == 2


@pytest.mark.parametrize('raw', ['invalid', '[]', '{}', '{"type":"result","subtype":"success","is_error":0}'])
def test_unknown_or_non_boolean_completion_cannot_authorize_tests(raw):
    with pytest.raises(RuntimeError): cursor.validate_reply(raw)


def test_unavailable_status_never_becomes_capacity():
    with patch.object(cursor.shutil, 'which', return_value=None): assert not cursor.available()
    with patch.object(cursor.shutil, 'which', return_value='/fixture/cursor'), pytest.raises(RuntimeError): cursor.command()
    with patch.object(cursor.shutil, 'which', return_value='/fixture/cursor'), patch.object(
            cursor.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture-cursor', 20)):
        assert not cursor.available()


@pytest.mark.parametrize('failure', [None, 'provider', 'tests'])
def test_cursor_retains_supervisor_test_and_publication_gates(tmp_path, failure):
    calls = []; prompts = []
    def run(args, cwd=None, **kwargs):
        calls.append(args)
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps(dict(headRefName='fix/one', headRefOid='before', isCrossRepository=False, state='OPEN', title='Ready', labels=[]))
        if args[:3] == ['git', 'worktree', 'add']:
            tree = Path(args[-2]); tree.mkdir(parents=True); (tree / 'test_real.py').write_text('def test_real(): assert True\n')
        if args[0] == 'fixture-cursor':
            prompts.append(kwargs['input'])
            assert 'Shell(*)' in (Path(cwd) / '.cursor/cli.json').read_text()
            return json.dumps(dict(type='result', subtype='success', is_error=failure == 'provider'))
        if '-m' in args and 'pytest' in args:
            assert not (Path(cwd) / '.cursor').exists()
            if failure == 'tests': raise subprocess.CalledProcessError(1, args, output='still broken')
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            return 'after' if any(c[:2] == ['git', 'commit'] for c in calls) else 'before'
        if args[:3] == ['git', 'diff', '--cached']: return 'implementation.py'
        if args[:2] == ['git', 'ls-remote']: return 'after refs/heads/fix/one'
        return ''
    token = fix_worker._provider.set('cursor')
    try:
        with patch.object(cursor, 'available', return_value=True), patch.object(cursor, 'command', return_value=['fixture-cursor']), patch.object(fix_worker, '_find_codex', side_effect=AssertionError('Codex must not run')), patch.object(fix_worker, 'run', side_effect=run):
            if failure:
                with pytest.raises(RuntimeError): fix_worker.fix_pr('owner/repo', 1, 'before', ['repair'])
            else: assert fix_worker.fix_pr('owner/repo', 1, 'before', ['repair']) == 'after'
    finally: fix_worker._provider.reset(token)
    commits = [i for i, c in enumerate(calls) if c[:2] == ['git', 'commit']]
    pushes = [i for i, c in enumerate(calls) if c[:2] == ['git', 'push']]
    if failure: assert not commits and not pushes
    else:
        tests = next(i for i, c in enumerate(calls) if '-m' in c and 'pytest' in c)
        assert tests < commits[0] < pushes[0] < len(calls)-1
        assert calls[pushes[0]][-1] == 'HEAD:refs/heads/fix/one'
    assert len(prompts) == (3 if failure == 'tests' else 1)

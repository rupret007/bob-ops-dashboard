"""Provider and supervisor probes must never write to live StoryPool fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from cluster.control_plane import cursor_worker, fix_worker


STATE_KEYS = (
    'STORYPOOL_JOBS_DIR', 'STORYPOOL_METRICS_DIR', 'STORYPOOL_DATA_DIR',
    'STORYPOOL_STATE_DIR', 'STORYPOOL_FIX_STATE_DIR',
)
PROBE = """import os,pathlib,sys
for key in sys.argv[1:]:
    folder = pathlib.Path(os.environ[key])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'probe').write_text('disposable test state')
"""


@pytest.mark.parametrize('provider', ['codex', 'cursor'])
@pytest.mark.parametrize('failures', [0, 2])
@pytest.mark.parametrize('node', [False, True])
def test_provider_and_test_retries_keep_state_disposable(
        tmp_path, monkeypatch, provider, failures, node):
    live = {}
    for key in STATE_KEYS:
        live[key] = tmp_path / 'live-fixture' / key
        live[key].mkdir(parents=True)
        monkeypatch.setenv(key, str(live[key]))
    monkeypatch.setenv('WORKER_TEST_SENTINEL', 'preserved fixture value')
    calls, provider_paths, test_paths, private_roots = [], [], [], []
    attempts = 0

    def probe(env, destinations):
        subprocess.run([sys.executable, '-c', PROBE, *STATE_KEYS],
                       env=env, check=True, capture_output=True, timeout=10)
        effective = os.environ if env is None else env
        destinations.append({key: effective[key] for key in STATE_KEYS})
        assert effective['WORKER_TEST_SENTINEL'] == 'preserved fixture value'
        assert effective['HOME'] == os.environ['HOME']

    def command(args, cwd=None, **kwargs):
        nonlocal attempts
        calls.append(args)
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps(dict(number=1, title='Repair fixture', labels=[],
                isDraft=False, headRefName='fix/one', headRefOid='before',
                isCrossRepository=False, state='OPEN'))
        if args[:3] == ['git', 'worktree', 'add']:
            tree = Path(args[-2])
            tree.mkdir(parents=True)
            private_roots.append(tree.parent)
            (tree / 'test_real.py').write_text('def test_real(): assert True\n')
            if node:
                (tree / 'package.json').write_text(json.dumps({'scripts': {'test': 'fixture'}}))
                (tree / 'package-lock.json').write_text('{}')
        if args[0] == 'fixture-codex':
            probe(kwargs.get('env'), provider_paths)
        if args[0] == 'fixture-cursor':
            env = kwargs['env']
            assert all(key not in env for key in STATE_KEYS)
            assert 'WORKER_TEST_SENTINEL' not in env
            assert env['HOME'] == os.environ['HOME']
            assert 'Shell(*)' in (Path(cwd) / '.cursor/cli.json').read_text()
            return json.dumps(dict(type='result', subtype='success', is_error=False))
        if args[:2] == ['npm', 'test'] or ('-m' in args and 'pytest' in args):
            probe(kwargs['env'], test_paths)
            attempts += 1
            if attempts <= failures:
                raise subprocess.CalledProcessError(1, args, output='fixture needs repair')
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            return 'after' if any(c[:2] == ['git', 'commit'] for c in calls) else 'before'
        if args[:3] == ['git', 'diff', '--cached']:
            return 'implementation.py'
        if args[:2] == ['git', 'ls-remote']:
            return 'after refs/heads/fix/one'
        return ''

    token = fix_worker._provider.set(provider)
    try:
        with patch.object(fix_worker, '_find_codex', return_value='fixture-codex'), \
                patch.object(fix_worker, 'qualified_runtime', return_value=None), \
                patch.object(cursor_worker, 'available', return_value=True), \
                patch.object(cursor_worker, 'command', return_value=['fixture-cursor']), \
                patch.object(fix_worker, 'run', side_effect=command):
            assert fix_worker.fix_pr('fixture/project', 1, 'before', ['repair']) == 'after'
    finally:
        fix_worker._provider.reset(token)

    assert attempts == failures + 1
    assert len(provider_paths) == (attempts if provider == 'codex' else 0)
    assert all(not (folder / 'probe').exists() for folder in live.values())
    assert all(paths == test_paths[0] for paths in provider_paths + test_paths)
    assert all(Path(value).parent == private_roots[0] for value in test_paths[0].values())
    assert all(not root.exists() for root in private_roots)

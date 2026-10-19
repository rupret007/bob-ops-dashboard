"""Qualified Webjam setup precedes reservation and inference; no runtime installs."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cluster.control_plane import dispatcher, fix_worker, worker_runtime as runtimes
from cluster.control_plane.coordination import ClaimClient
from cluster.control_plane.job_ledger import JobLedger
from cluster.control_plane.retry_policy import WorkerFailure


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def qualified(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setattr(runtimes.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(runtimes.platform, 'machine', lambda: 'x86_64')
    root = home / runtimes.PROFILE
    root.mkdir(parents=True, mode=0o700)
    for relative in ('venv/bin', 'native', 'receipts', 'config', 'cache', 'data', 'state', 'xdg-runtime'):
        (root / relative).mkdir(parents=True, mode=0o700)
    (root / 'config/empty-netrc').touch(mode=0o600)
    binary = root / 'python-real'
    binary.write_bytes(b'hermetic interpreter identity; probe is mocked')
    binary.chmod(0o700)
    (root / 'venv/bin/python').symlink_to(binary)
    (root / 'venv/pyvenv.cfg').write_text('include-system-site-packages = false\n')
    tree = tmp_path / 'source'
    (tree / '.github/workflows').mkdir(parents=True)
    (tree / 'requirements.txt').write_bytes(b'PySide6==6.11.1\n')
    (tree / '.github/workflows/ci.yml').write_bytes(b'python-version: "3.11"\n')
    (tree / 'test_project.py').write_text('def test_project(): assert 2 + 2 == 4\n')
    git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_COUNT='0', GIT_TEMPLATE_DIR=str(tmp_path / 'empty-template'))
    (tmp_path / 'empty-template').mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c',
            'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
            '-c', 'core.hooksPath=/dev/null', *args], cwd=tree, env=git_env, text=True).strip()
    git('init', '--quiet')
    git('add', '.')
    git('commit', '--quiet', '-m', 'controlled source fixture')
    head = dict(head_sha=git('rev-parse', 'HEAD'), tree_sha=git('rev-parse', 'HEAD^{tree}'),
                requirements_sha256=sha((tree / 'requirements.txt').read_bytes()),
                ci_sha256=sha((tree / '.github/workflows/ci.yml').read_bytes()),
                collection=dict(returncode=0, count=1))
    artifact = dict(filename='fixture.whl', sha256='c' * 64, bytes=123)
    wheels = dict(schema=1, requirements_sha256=head['requirements_sha256'], artifacts=[artifact])
    native = dict(schema=1, signature_verified=True, index_sha256='d' * 64,
                  artifacts=[dict(artifact, filename='fixture.deb', package='libfixture', version='1')])
    for name, data in (('wheels', wheels), ('native', native)):
        (root / ('receipts/' + name + '.json')).write_text(json.dumps(data))
    receipt = dict(schema=1, repo=runtimes.WEBJAM, machine='nucbox', platform='linux-x86_64',
        python=str(root / 'venv/bin/python'), python_realpath=str(binary), python_sha256=sha(binary.read_bytes()),
        python_version='3.11.17', native_prefix=str(root / 'native'),
        wheel_manifest_sha256=sha((root / 'receipts/wheels.json').read_bytes()),
        native_manifest_sha256=sha((root / 'receipts/native.json').read_bytes()),
        imports=dict(modules=list(runtimes.IMPORTS), returncode=0), qualified_heads=[head])
    def save():
        (root / 'qualification.json').write_text(json.dumps(receipt))
        (root / 'qualification.json').chmod(0o600)
    save()
    calls = []
    real_run = subprocess.run
    def probe(args, **kwargs):
        if args[:3] == [receipt['python'], '-I', '-c']:
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, json.dumps(
                dict(python='3.11.17', pyside6='6.11.1')), '')
        return real_run(args, **kwargs)
    monkeypatch.setattr(runtimes.subprocess, 'run', probe)
    return SimpleNamespace(root=root, tree=tree, receipt=receipt, head=head['head_sha'],
                           save=save, calls=calls, git=git)


def test_exact_source_fixture_and_fixed_probe_are_qualified(qualified):
    runtime = runtimes.qualified_runtime(runtimes.WEBJAM, qualified.head, qualified.tree)
    assert runtime.python == qualified.receipt['python']
    assert runtime.head['tree_sha'] == qualified.git('rev-parse', 'HEAD^{tree}')
    args, kwargs = qualified.calls[0]
    assert args == [runtime.python, '-I', '-c', runtimes._PROBE]
    assert kwargs['timeout'] == 15 and kwargs['capture_output'] is True
    assert kwargs['env']['QT_QPA_PLATFORM'] == 'offscreen'
    assert kwargs['env']['HOME'] == os.environ['HOME']


@pytest.mark.parametrize('mutation', [
    'unknown-head', 'schema-bool', 'collection-bool', 'collection-zero', 'collection-failed',
    'duplicate-head', 'missing-import', 'failed-import', 'wrong-platform', 'wrong-python',
    'arbitrary-native', 'binary-drift', 'manifest-drift', 'requirements-drift', 'ci-drift',
    'receipt-mode', 'parent-mode', 'receipt-link', 'native-link', 'source-link',
    'missing-receipt', 'malformed-json', 'duplicate-json-key', 'system-packages',
    'unverified-native', 'empty-wheels',
])
def test_invalid_evidence_fails_closed_without_raw_values(qualified, mutation):
    q = qualified
    value = q.receipt
    selected = value['qualified_heads'][0]
    head = q.head
    if mutation == 'unknown-head': head = 'f' * 40
    elif mutation == 'schema-bool': value['schema'] = True
    elif mutation == 'collection-bool': selected['collection']['count'] = True
    elif mutation == 'collection-zero': selected['collection']['count'] = 0
    elif mutation == 'collection-failed': selected['collection']['returncode'] = 2
    elif mutation == 'duplicate-head': value['qualified_heads'].append(dict(selected))
    elif mutation == 'missing-import': value['imports']['modules'].pop()
    elif mutation == 'failed-import': value['imports']['returncode'] = 1
    elif mutation == 'wrong-platform': value['platform'] = 'darwin-arm64'
    elif mutation == 'wrong-python': value['python'] = '/tmp/private-sensitive-python'
    elif mutation == 'arbitrary-native': value['native_prefix'] = '/tmp/private-sensitive-libs'
    elif mutation == 'binary-drift': (q.root / 'python-real').write_bytes(b'changed')
    elif mutation == 'manifest-drift': (q.root / 'receipts/wheels.json').write_text('{}')
    elif mutation == 'requirements-drift': (q.tree / 'requirements.txt').write_text('changed')
    elif mutation == 'ci-drift': (q.tree / '.github/workflows/ci.yml').write_text('changed')
    elif mutation == 'system-packages': (q.root / 'venv/pyvenv.cfg').write_text('include-system-site-packages = true\n')
    elif mutation in ('unverified-native', 'empty-wheels'):
        name = 'native' if mutation == 'unverified-native' else 'wheels'
        path = q.root / ('receipts/' + name + '.json')
        data = json.loads(path.read_text())
        if mutation == 'unverified-native': data['signature_verified'] = False
        else: data['artifacts'] = []
        path.write_text(json.dumps(data))
        value['native_manifest_sha256' if name == 'native' else 'wheel_manifest_sha256'] = sha(path.read_bytes())
    q.save()
    path = q.root / 'qualification.json'
    if mutation == 'receipt-mode': path.chmod(0o644)
    elif mutation == 'parent-mode': q.root.chmod(0o777)
    elif mutation == 'missing-receipt': path.unlink()
    elif mutation == 'malformed-json': path.write_text('private-sensitive-value')
    elif mutation == 'duplicate-json-key': path.write_text('{"schema":1,"schema":1}')
    elif mutation in ('receipt-link', 'native-link', 'source-link'):
        link = path if mutation == 'receipt-link' else q.root / 'native' if mutation == 'native-link' else q.tree / 'requirements.txt'
        target = link.with_name(link.name + '-original')
        link.rename(target)
        link.symlink_to(target)
    with pytest.raises(runtimes.RuntimeUnqualified) as error:
        runtimes.qualified_runtime(runtimes.WEBJAM, head, q.tree)
    assert str(error.value) == runtimes.REASON
    assert 'private-sensitive' not in str(error.value)
    assert not q.calls


def test_other_repositories_keep_default_node_and_python_commands(tmp_path):
    assert runtimes.qualified_runtime('owner/other', 'any-head') is None
    (tmp_path / 'test_real.py').write_text('def test_real(): assert True\n')
    assert fix_worker.test_command(tmp_path) == [sys.executable, '-m', 'pytest', '-q']
    (tmp_path / 'package.json').write_text('{"scripts":{"test":"jest"}}')
    assert fix_worker.test_command(tmp_path) == ['npm', 'test']


@pytest.mark.parametrize('failure', ['exit', 'json', 'version', 'oversize', 'timeout', 'oserror'])
def test_probe_failure_is_bounded_single_attempt_and_never_exposes_output(qualified, failure):
    import traceback
    private = 'private-sensitive-probe-output'
    error = (subprocess.TimeoutExpired([private], 15, output=private)
             if failure == 'timeout' else OSError(private))
    output = (private if failure == 'json' else 'x' * 1025 if failure == 'oversize'
              else json.dumps(dict(python='3.14.4', pyside6='6.11.1')))
    with patch.object(runtimes.subprocess, 'run', side_effect=error if failure in ('timeout', 'oserror')
                      else None, return_value=subprocess.CompletedProcess([], 1 if failure == 'exit' else 0,
                                                                         output, private)) as probe:
        with pytest.raises(runtimes.RuntimeUnqualified) as caught:
            runtimes.qualified_runtime(runtimes.WEBJAM, qualified.head)
    assert probe.call_count == 1
    assert probe.call_args.kwargs['timeout'] == 15
    assert private not in ''.join(traceback.format_exception(
        type(caught.value), caught.value, caught.value.__traceback__))


def test_actual_child_gets_scrubbed_environment_without_changing_home(qualified, tmp_path):
    runtime = runtimes.qualified_runtime(runtimes.WEBJAM, qualified.head)
    inherited = dict(os.environ, PYTHONPATH='private-sensitive', PYTHONHOME='private-sensitive',
                     LD_LIBRARY_PATH='private-sensitive', LD_PRELOAD='private-sensitive',
                     QT_PLUGIN_PATH='private-sensitive')
    env = runtime.environment(inherited, tmp_path)
    keys = ('HOME', 'QT_QPA_PLATFORM', 'PATH', 'LD_LIBRARY_PATH', 'XDG_DATA_HOME', 'NETRC',
            'PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'QT_PLUGIN_PATH')
    script = 'import json,os;print(json.dumps({k:os.environ[k] for k in ' + repr(keys) + ' if k in os.environ}))'
    child = subprocess.run([sys.executable, '-I', '-c', script], env=env,
                           text=True, capture_output=True, check=True, timeout=10)
    observed = json.loads(child.stdout)
    assert observed['HOME'] == os.environ['HOME']
    assert observed['QT_QPA_PLATFORM'] == 'offscreen'
    assert observed['PATH'].split(os.pathsep)[0] == str(Path(runtime.python).parent)
    assert observed['LD_LIBRARY_PATH'] == os.pathsep.join(str(qualified.root / 'native' / p)
        for p in ('usr/lib/x86_64-linux-gnu', 'lib/x86_64-linux-gnu',
                  'usr/lib/x86_64-linux-gnu/pulseaudio'))
    assert observed['XDG_DATA_HOME'] == str(tmp_path / 'data')
    assert observed['NETRC'] == str(tmp_path / 'config/empty-netrc')
    assert not any(key in observed for key in ('PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'QT_PLUGIN_PATH'))
    assert 'private-sensitive' not in json.dumps(observed)


def dispatch_fixture(tmp_path, head):
    ledger = JobLedger(tmp_path / 'jobs')
    instance = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=ledger,
                                     budget_tracker=MagicMock())
    client = MagicMock()
    client.acquire.return_value = {'sha': head}
    client.retry_state.return_value = dict(attempts=1, code='tests-failed', retry_after=0)
    return ledger, instance, client


@pytest.mark.parametrize('qualified_profile', [False, True])
def test_dispatch_checks_runtime_before_claim_reserve_or_retry_write(tmp_path, qualified, qualified_profile):
    if not qualified_profile:
        (qualified.root / 'qualification.json').unlink()
    ledger, instance, client = dispatch_fixture(tmp_path, qualified.head)
    with patch.object(instance, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(instance, 'select_provider', return_value='codex'), \
         patch.object(dispatcher, 'REPOS', [runtimes.WEBJAM]), \
         patch.object(dispatcher, '_get_open_prs', return_value=[dict(number=395, headRefOid=qualified.head)]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(dispatcher, '_has_codex_work_queued', return_value=False), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch('cluster.control_plane.ci_review_refresh.repair_preflight', return_value='repair'), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch('cluster.control_plane.retry_policy.reserve', wraps=__import__(
             'cluster.control_plane.retry_policy', fromlist=['reserve']).reserve) as reserve, \
         patch.object(dispatcher, '_spawn_worker', return_value=0) as spawn:
        decisions = instance.evaluate(live=True)
    if not qualified_profile:
        assert decisions[0].extra['reason_code'] == 'test-runtime-unqualified'
        client.acquire.assert_not_called()
        client.retry_state.assert_not_called()
        reserve.assert_not_called()
        spawn.assert_not_called()
        assert ledger._load_jobs() == []
    else:
        assert len(qualified.calls) == 1
        assert reserve.call_count == 1 and spawn.call_count == 1
        assert client.retry_state.call_args.args[1]['attempts'] == 2
        assert len(ledger._load_jobs()) == 1


def test_qualification_does_not_override_existing_retry_limit(qualified, tmp_path):
    ledger, instance, client = dispatch_fixture(tmp_path, qualified.head)
    client.retry_state.return_value = dict(attempts=3, code='tests-failed', retry_after=0)
    with patch.object(instance, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(instance, 'select_provider', return_value='codex'), \
         patch.object(dispatcher, 'REPOS', [runtimes.WEBJAM]), \
         patch.object(dispatcher, '_get_open_prs', return_value=[dict(number=395, headRefOid=qualified.head)]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(dispatcher, '_has_codex_work_queued', return_value=False), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch('cluster.control_plane.ci_review_refresh.repair_preflight', return_value='repair'), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker') as spawn:
        decisions = instance.evaluate(live=True)
    assert decisions[0].extra['reason_code'] == 'retry-blocked'
    assert 'retry limit' in decisions[0].reason
    assert len(client.retry_state.call_args_list) == 1  # read only, no reservation persisted
    spawn.assert_not_called()
    assert ledger._load_jobs() == []


@pytest.mark.parametrize('drift', ['none', 'missing-before', 'requirements', 'receipt', 'tree'])
def test_worker_uses_identical_env_and_rechecks_before_testing(qualified, drift):
    import shutil
    q = qualified
    if drift == 'missing-before': (q.root / 'qualification.json').unlink()
    calls = []
    pushed = 'b' * 40
    def command(args, cwd=None, **kwargs):
        calls.append((args, kwargs))
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps(dict(headRefName='fix/fixture', headRefOid=q.head, state='OPEN', isCrossRepository=False))
        if args[:3] == ['git', 'worktree', 'add']:
            shutil.copytree(q.tree, args[-2])
        if args[0] == '/fixture/codex':
            if drift == 'requirements': (Path(cwd) / 'requirements.txt').write_text('changed by provider')
            if drift == 'receipt':
                q.receipt['operator_note'] = 'private-sensitive-drift'
                q.save()
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            return pushed if any(c[:2] == ['git', 'commit'] for c, _ in calls) else q.head
        if args[:3] == ['git', 'rev-parse', 'HEAD^{tree}']:
            return 'e' * 40 if drift == 'tree' else q.receipt['qualified_heads'][0]['tree_sha']
        if args[:3] == ['git', 'diff', '--cached']: return 'changed.py'
        if args[:2] == ['git', 'ls-remote']: return pushed + ' refs/heads/fix/fixture'
        return ''
    with patch.object(fix_worker, '_find_codex', return_value=Path('/fixture/codex')), \
         patch.object(fix_worker, 'run', side_effect=command):
        if drift == 'none':
            assert fix_worker.fix_pr(runtimes.WEBJAM, 395, q.head, ['repair']) == pushed
        else:
            with pytest.raises(WorkerFailure) as error:
                fix_worker.fix_pr(runtimes.WEBJAM, 395, q.head, ['repair'])
            assert (error.value.code, error.value.stage) == ('test-setup-failed', 'test-setup')
    providers = [(args, kw) for args, kw in calls if args[0] == '/fixture/codex']
    tests = [(args, kw) for args, kw in calls if '-m' in args and 'pytest' in args]
    if drift == 'none':
        assert tests[0][0] == [q.receipt['python'], '-m', 'pytest', '-q']
        assert providers[0][1]['env'] == tests[0][1]['env']
        assert providers[0][1]['env']['HOME'] == os.environ['HOME']
        assert Path(providers[0][1]['env']['STORYPOOL_STATE_DIR']).name == 'test-state'
        assert providers[0][1]['env']['STORYPOOL_STATE_DIR'] != os.environ.get('STORYPOOL_STATE_DIR')
        assert len(q.calls) == 2
        assert Path(providers[0][1]['env']['XDG_DATA_HOME']).name == 'data'
    else:
        assert not tests
        assert len(providers) == (1 if drift in ('requirements', 'receipt') else 0)
        assert not any(args[:2] in (['git', 'commit'], ['git', 'push']) for args, _ in calls)


def test_runtime_race_after_reservation_keeps_exact_head_attempt_count(qualified, tmp_path):
    import shutil
    q = qualified
    assert runtimes.qualified_runtime(runtimes.WEBJAM, q.head) is not None
    (q.root / 'qualification.json').unlink()  # changed after dispatcher admission
    ledger = JobLedger(tmp_path / 'race-jobs')
    job = ledger.create_job(runtimes.WEBJAM, 395, q.head, 'codex', 'nucbox')
    lease = MagicMock()
    previous = dict(attempts=2, retry_after=123456, code='transient')
    lease.client.retry_state.return_value = previous
    calls = []
    def command(args, cwd=None, **kwargs):
        calls.append(args)
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps(dict(headRefName='fix/fixture', headRefOid=q.head,
                                   state='OPEN', isCrossRepository=False))
        if args[:3] == ['git', 'worktree', 'add']:
            shutil.copytree(q.tree, args[-2])
        return ''
    with patch.object(fix_worker, 'get_ledger', return_value=ledger), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch('cluster.control_plane.coordination.Lease', return_value=lease), \
         patch.object(ClaimClient, 'from_env'), \
         patch.object(fix_worker, 'model_label', return_value='fixture'), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch.object(fix_worker, '_find_codex', return_value=Path('/fixture/codex')), \
         patch.object(fix_worker, 'run', side_effect=command):
        assert fix_worker.execute(job.job_id) == 1
    failed = ledger._load_jobs()[0]
    assert failed.state == 'failed' and not failed.outcome_verified
    assert (failed.failure_code, failed.failure_stage) == ('test-setup-failed', 'test-setup')
    assert lease.client.retry_state.call_args.args[1] == dict(previous, code='test-setup-failed')
    assert not any(args[0] == '/fixture/codex' for args in calls)

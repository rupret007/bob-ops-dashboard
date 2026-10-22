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
    (root / 'config/alsa.conf').write_bytes(runtimes.ALSA_CONFIG)
    (root / 'config/alsa.conf').chmod(0o600)
    binary = root / 'python-real'
    binary.write_bytes(b'hermetic interpreter identity; probe is mocked')
    binary.chmod(0o700)
    (root / 'venv/bin/python').symlink_to(binary)
    (root / 'venv/pyvenv.cfg').write_text('include-system-site-packages = false\n')
    tree = tmp_path / 'source'
    (tree / '.github/workflows').mkdir(parents=True)
    (tree / 'requirements.txt').write_bytes(b'PySide6==6.11.1\n')
    (tree / '.github/workflows/ci.yml').write_bytes(b'python-version: "3.11"\n')
    (tree / 'tests').mkdir()
    (tree / 'tests/test_project.py').write_text('def test_project(): assert 2 + 2 == 4\n')
    (tree / 'pytest.ini').write_text('[pytest]\ntestpaths=tests\n')
    (tree / 'tools').mkdir()
    (tree / 'tools/test_existing.py').write_text('def test_manual_helper(): assert False\n')
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
                test_files_sha256=sha(git('ls-files', '-z', '--', 'tests/test_*.py').encode()),
                test_file_count=1,
                collection=dict(returncode=0, count=1))
    artifact = dict(filename='fixture.whl', sha256='c' * 64, bytes=123)
    wheels = dict(schema=1, requirements_sha256=head['requirements_sha256'], artifacts=[artifact])
    native = dict(schema=1, signature_verified=True, index_sha256='d' * 64,
                  artifacts=[dict(artifact, filename='fixture_1~native.deb', package='libfixture', version='1'),
                             dict(artifact, filename='fonts-liberation.deb', package='fonts-liberation', version='1')],
                  headless_audio=dict(backend='alsa-null', configuration='config/alsa.conf',
                    configuration_sha256=runtimes.ALSA_SHA256, includes=False,
                    hardware_audio=False, audio_server=False))
    font_dir = root / 'native' / runtimes.FONT_DIRECTORY
    font_dir.mkdir(parents=True, mode=0o700)
    font_files = []
    for index in range(12):
        path = font_dir / ('Fixture' + str(index) + '.ttf')
        path.write_bytes(b'controlled-font-fixture')
        font_files.append(dict(filename=path.name, sha256=sha(path.read_bytes()), bytes=path.stat().st_size))
    font_files.sort(key=lambda item: item['filename'])
    native['font_closure'] = dict(directory=runtimes.FONT_DIRECTORY,
        package_sha256=artifact['sha256'], files=font_files,
        sha256=sha(json.dumps(font_files, sort_keys=True, separators=(',', ':')).encode()))
    (root / 'data/fonts').symlink_to(font_dir)
    for name, data in (('wheels', wheels), ('native', native)):
        (root / ('receipts/' + name + '.json')).write_text(json.dumps(data))
    receipt = dict(schema=1, repo=runtimes.WEBJAM, machine='nucbox', platform='linux-x86_64',
        python=str(root / 'venv/bin/python'), python_realpath=str(binary), python_sha256=sha(binary.read_bytes()),
        python_version='3.11.17', native_prefix=str(root / 'native'),
        test_recipe=runtimes.TEST_RECIPE, headless_audio_config_sha256=runtimes.ALSA_SHA256,
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
    assert kwargs['env']['ALSA_CONFIG_PATH'] == str(qualified.root / 'config/alsa.conf')
    assert kwargs['env']['HOME'] == os.environ['HOME']


@pytest.mark.parametrize('mutation', [
    'unknown-head', 'schema-bool', 'collection-bool', 'collection-zero', 'collection-failed',
    'duplicate-head', 'missing-import', 'failed-import', 'wrong-platform', 'wrong-python',
    'arbitrary-native', 'binary-drift', 'manifest-drift', 'requirements-drift', 'ci-drift',
    'receipt-mode', 'parent-mode', 'receipt-link', 'native-link', 'source-link',
    'missing-receipt', 'malformed-json', 'duplicate-json-key', 'system-packages',
    'unverified-native', 'empty-wheels', 'alsa-tamper', 'alsa-link', 'alsa-mode',
    'alsa-receipt', 'alsa-manifest', 'recipe', 'file-count', 'file-hash',
    'font-tamper', 'font-link', 'font-redirect', 'font-manifest',
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
    elif mutation == 'alsa-tamper': (q.root / 'config/alsa.conf').write_bytes(b'pcm.!default { type hw }\n')
    elif mutation == 'alsa-receipt': value['headless_audio_config_sha256'] = '0' * 64
    elif mutation == 'recipe': value['test_recipe'] = 'unqualified-combined-process'
    elif mutation == 'file-count': selected['test_file_count'] = 0
    elif mutation == 'file-hash': selected['test_files_sha256'] = 'not-a-hash'
    elif mutation == 'font-tamper': (q.root / 'native' / runtimes.FONT_DIRECTORY / 'Fixture0.ttf').write_bytes(b'changed')
    elif mutation == 'font-link':
        path = q.root / 'native' / runtimes.FONT_DIRECTORY / 'Fixture0.ttf'
        path.unlink()
        path.symlink_to('/missing-font')
    elif mutation == 'font-redirect':
        (q.root / 'data/fonts').unlink()
        (q.root / 'data/fonts').symlink_to('/unqualified-font-directory')
    elif mutation in ('unverified-native', 'empty-wheels', 'alsa-manifest', 'font-manifest'):
        name = 'wheels' if mutation == 'empty-wheels' else 'native'
        path = q.root / ('receipts/' + name + '.json')
        data = json.loads(path.read_text())
        if mutation == 'unverified-native': data['signature_verified'] = False
        elif mutation == 'empty-wheels': data['artifacts'] = []
        elif mutation == 'alsa-manifest': data['headless_audio']['configuration_sha256'] = '0' * 64
        else: data['font_closure']['sha256'] = '0' * 64
        path.write_text(json.dumps(data))
        value['native_manifest_sha256' if name == 'native' else 'wheel_manifest_sha256'] = sha(path.read_bytes())
    q.save()
    path = q.root / 'qualification.json'
    if mutation == 'receipt-mode': path.chmod(0o644)
    elif mutation == 'parent-mode': q.root.chmod(0o777)
    elif mutation == 'missing-receipt': path.unlink()
    elif mutation == 'malformed-json': path.write_text('private-sensitive-value')
    elif mutation == 'duplicate-json-key': path.write_text('{"schema":1,"schema":1}')
    elif mutation == 'alsa-mode': (q.root / 'config/alsa.conf').chmod(0o644)
    elif mutation == 'alsa-link':
        audio = q.root / 'config/alsa.conf'
        audio.rename(audio.with_name('alsa-original.conf'))
        audio.symlink_to(audio.with_name('alsa-original.conf'))
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
                     QT_PLUGIN_PATH='private-sensitive', FONTCONFIG_FILE='private-sensitive')
    env = runtime.environment(inherited, tmp_path)
    keys = ('HOME', 'QT_QPA_PLATFORM', 'PATH', 'LD_LIBRARY_PATH', 'XDG_DATA_HOME', 'NETRC',
            'ALSA_CONFIG_PATH', 'PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'QT_PLUGIN_PATH', 'FONTCONFIG_FILE')
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
    assert observed['ALSA_CONFIG_PATH'] == str(tmp_path / 'config/alsa.conf')
    assert not any(key in observed for key in ('PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'QT_PLUGIN_PATH', 'FONTCONFIG_FILE'))
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


@pytest.mark.parametrize('drift', ['none', 'missing-before', 'requirements', 'receipt', 'tree',
                                    'drop-module', 'new-module', 'outside-module', 'outside-leading-space', 'alsa-job'])
def test_worker_uses_identical_env_and_rechecks_before_testing(qualified, drift):
    import shutil
    q = qualified
    if drift == 'missing-before': (q.root / 'qualification.json').unlink()
    calls = []
    pushed = 'b' * 40
    actual_run = fix_worker.run
    git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT='0')
    def command(args, cwd=None, **kwargs):
        calls.append((args, kwargs))
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps(dict(headRefName='fix/fixture', headRefOid=q.head, state='OPEN', isCrossRepository=False, title='Ready', labels=[]))
        if args[:3] == ['git', 'worktree', 'add']:
            shutil.copytree(q.tree, args[-2])
        if args[0] == '/fixture/codex':
            audio = Path(kwargs['env']['ALSA_CONFIG_PATH'])
            assert audio.read_bytes() == runtimes.ALSA_CONFIG
            assert audio.stat().st_mode & 0o777 == 0o600
            assert audio != q.root / 'config/alsa.conf'
            font_link = Path(kwargs['env']['XDG_DATA_HOME']) / 'fonts'
            assert font_link.is_symlink()
            assert os.readlink(font_link) == str(q.root / 'native' / runtimes.FONT_DIRECTORY)
            if drift == 'requirements': (Path(cwd) / 'requirements.txt').write_text('changed by provider')
            if drift == 'receipt':
                q.receipt['operator_note'] = 'private-sensitive-drift'
                q.save()
            if drift == 'alsa-job': audio.write_bytes(b'pcm.!default { type hw }\n')
            if drift in ('new-module', 'outside-module'):
                name = 'test_added.py' if drift == 'new-module' else 'extra_test.py'
                (Path(cwd) / 'tests' / name).write_text('def test_added(): assert False\n')
            if drift == 'outside-leading-space':
                (Path(cwd) / ' tests').mkdir()
                (Path(cwd) / ' tests/test_added.py').write_text('def test_added(): assert False\n')
                actual_run(['git', 'add', '--', ' tests/test_added.py'], cwd, env=git_env)
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            return pushed if any(c[:2] == ['git', 'commit'] for c, _ in calls) else q.head
        if args[:3] == ['git', 'rev-parse', 'HEAD^{tree}']:
            return 'e' * 40 if drift == 'tree' else q.receipt['qualified_heads'][0]['tree_sha']
        if args[:2] == ['git', 'ls-files']:
            if '--others' in args and drift == 'drop-module': return 'tests/test_other.py\0'
            return actual_run(args, cwd, env=git_env)
        if args[:3] == ['git', 'diff', '--cached']: return 'changed.py'
        if args[:2] == ['git', 'ls-remote']: return pushed + ' refs/heads/fix/fixture'
        return ''
    with patch.object(fix_worker, '_find_codex', return_value=Path('/fixture/codex')), \
         patch.object(fix_worker, 'run', side_effect=command):
        if drift in ('none', 'new-module'):
            assert fix_worker.fix_pr(runtimes.WEBJAM, 395, q.head, ['repair']) == pushed
        else:
            with pytest.raises(WorkerFailure) as error:
                fix_worker.fix_pr(runtimes.WEBJAM, 395, q.head, ['repair'])
            assert (error.value.code, error.value.stage) == ('test-setup-failed', 'test-setup')
    providers = [(args, kw) for args, kw in calls if args[0] == '/fixture/codex']
    tests = [(args, kw) for args, kw in calls if runtimes.MODULE_RUNNER in args]
    if drift in ('none', 'new-module'):
        expected_files = ['tests/test_added.py', 'tests/test_project.py'] if drift == 'new-module' else ['tests/test_project.py']
        assert tests[0][0] == [q.receipt['python'], '-I', '-c', runtimes.MODULE_RUNNER, *expected_files]
        prompt = providers[0][1]['input']
        assert 'run focused regression tests for the affected behavior' in prompt
        assert 'iterate on any failures' in prompt
        assert q.receipt['python'] in prompt
        assert 'leave that full-suite run to the supervisor' in prompt
        assert 'Run every Git-tracked' not in prompt
        test_index = next(i for i, (args, _) in enumerate(calls) if runtimes.MODULE_RUNNER in args)
        commit_index = next(i for i, (args, _) in enumerate(calls) if args[:2] == ['git', 'commit'])
        push_index = next(i for i, (args, _) in enumerate(calls) if args[:2] == ['git', 'push'])
        assert test_index < commit_index < push_index
        assert providers[0][1]['env'] == tests[0][1]['env']
        assert providers[0][1]['env']['HOME'] == os.environ['HOME']
        assert Path(providers[0][1]['env']['STORYPOOL_STATE_DIR']).name == 'test-state'
        assert providers[0][1]['env']['STORYPOOL_STATE_DIR'] != os.environ.get('STORYPOOL_STATE_DIR')
        assert len(q.calls) == 2
        assert Path(providers[0][1]['env']['XDG_DATA_HOME']).name == 'data'
        audio_path = Path(providers[0][1]['env']['ALSA_CONFIG_PATH'])
        assert audio_path.name == 'alsa.conf' and audio_path.parent.name == 'config'
    else:
        assert not tests
        assert len(providers) == (1 if drift in ('requirements', 'receipt', 'drop-module',
                                                'outside-module', 'outside-leading-space', 'alsa-job') else 0)
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
                                   state='OPEN', isCrossRepository=False, title='Ready', labels=[]))
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


@pytest.mark.parametrize('failure_index', [None, 1])
@pytest.mark.parametrize('shadow', [None, 'module', 'package', 'symlink'])
def test_real_recipe_runs_complete_order_in_fresh_processes_and_stops_on_failure(tmp_path, failure_index, shadow):
    import importlib.util
    import venv
    tree = tmp_path / 'project'
    (tree / 'tests').mkdir(parents=True)
    (tree / 'project_values.py').write_text('ANSWER=42\n')
    fake = 'from pathlib import Path\nPath("shadow-executed").touch()\nraise SystemExit(0)\n'
    if shadow == 'package':
        (tree / 'pytest').mkdir()
        (tree / 'pytest/__init__.py').write_text(fake)
    elif shadow == 'module': (tree / 'pytest.py').write_text(fake)
    elif shadow == 'symlink':
        (tmp_path / 'fake-pytest.py').write_text(fake)
        (tree / 'pytest.py').symlink_to(tmp_path / 'fake-pytest.py')
    names = ['tests/test_01_first.py', 'tests/test_02 space.py', 'tests/test_03_last.py']
    for index, name in enumerate(names):
        (tree / name).write_text(
            'import builtins,json,os\n'
            'from pathlib import Path\n'
            'from project_values import ANSWER\n'
            'def test_native_lifetime():\n'
            ' assert ANSWER==42\n'
            ' assert not hasattr(builtins,"_webjam_native_lifetime")\n'
            ' builtins._webjam_native_lifetime=True\n'
            ' with Path("executed.jsonl").open("a") as stream:\n'
            f'  stream.write(json.dumps({{"index":{index},"pid":os.getpid()}})+"\\n")\n'
            f' assert {index != failure_index!r}\n')
    child_venv = tmp_path / 'child-venv'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(child_venv)
    child_site = child_venv / ('lib/python' + str(sys.version_info.major) + '.' + str(sys.version_info.minor) + '/site-packages')
    # Reuse the already installed test dependency, without downloads or user-site discovery.
    dependencies = set()
    for module in ('pytest', 'pluggy', 'packaging', 'iniconfig', 'pygments',
                   'exceptiongroup', 'tomli', 'typing_extensions'):
        spec = importlib.util.find_spec(module)
        if spec is not None and spec.origin:
            path = Path(spec.origin).parent
            dependencies.add(str(path.parent if spec.submodule_search_locations else path))
    (child_site / 'pytest-fixture.pth').write_text('\n'.join(sorted(dependencies)) + '\n')
    runtime = runtimes.QualifiedRuntime(str(child_venv / 'bin/python'), str(tmp_path / 'native'), 'a' * 64, {})
    state = tmp_path / 'state'
    runtime.prepare_state(state)
    env = runtime.environment(dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'), state)
    command = runtime.test_command(runtimes.test_files('\0'.join(names) + '\0'))
    result = subprocess.run(command, cwd=tree, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == (0 if failure_index is None else 1), result.stdout + result.stderr
    executed = [json.loads(line) for line in (tree / 'executed.jsonl').read_text().splitlines()]
    assert [entry['index'] for entry in executed] == ([0, 1, 2] if failure_index is None else [0, 1])
    assert len({entry['pid'] for entry in executed}) == len(executed)
    assert result.returncode == (0 if failure_index is None else 1)
    assert not (tree / 'space.py').exists()
    assert not (tree / 'shadow-executed').exists()


@pytest.mark.parametrize('raw', ['', 'tests/test_ok.py', 'tests/../test_escape.py\0',
    '/tests/test_absolute.py\0', 'tests/test_one.py\0tests/test_one.py\0'])
def test_recipe_rejects_empty_unterminated_traversal_or_duplicate_path_data(raw):
    with pytest.raises(runtimes.RuntimeUnqualified):
        runtimes.test_files(raw)


def test_recipe_file_list_is_bound_to_receipt_count_and_exact_nul_bytes(qualified):
    runtime = runtimes.qualified_runtime(runtimes.WEBJAM, qualified.head)
    assert runtime.validate_test_files('tests/test_project.py\0') == ['tests/test_project.py']
    for raw in ('tests/test_other.py\0', 'tests/test_project.py\0tests/test_added.py\0'):
        with pytest.raises(runtimes.RuntimeUnqualified):
            runtime.validate_test_files(raw)


def test_new_standard_test_names_outside_proven_ci_scope_are_distinct():
    before = runtimes.outside_standard_tests('tools/test_existing.py\0tests/test_project.py\0')
    assert before == {'tools/test_existing.py'}
    assert runtimes.outside_standard_tests('tools/test_existing.py\0tests/test_added.py\0') == before
    for name in ('tests/extra_test.py', 'test_extra.py'):
        assert not runtimes.outside_standard_tests('tools/test_existing.py\0' + name + '\0').issubset(before)


def _local_checkout_git_environment(tmp_path):
    # Local fixtures never consult credential helpers, user Git config, or hooks.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    empty = tmp_path / 'empty-git-template'
    empty.mkdir(exist_ok=True)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_COUNT='0', GIT_TEMPLATE_DIR=str(empty))
    return env


@pytest.mark.parametrize(('mask', 'expected_mode', 'accepted'), [
    (0o002, 0o664, False), (0o077, 0o600, True),
])
def test_real_git_checkout_modes_keep_strict_recipe_validation(qualified, tmp_path, mask, expected_mode, accepted):
    q = qualified
    checkout, tree = tmp_path / 'local-clone', tmp_path / 'fresh-worktree'
    env = _local_checkout_git_environment(tmp_path)
    for args, cwd in [
        (['clone', '--no-checkout', str(q.tree), str(checkout)], None),
        (['worktree', 'add', '--detach', str(tree), q.head], checkout),
    ]:
        subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd,
                       env=env, umask=mask, check=True, capture_output=True, text=True)
    recipes = [tree / 'requirements.txt', tree / '.github/workflows/ci.yml']
    assert [path.stat().st_mode & 0o777 for path in recipes] == [expected_mode] * 2
    assert [sha(path.read_bytes()) for path in recipes] == [
        q.receipt['qualified_heads'][0]['requirements_sha256'],
        q.receipt['qualified_heads'][0]['ci_sha256'],
    ]
    if accepted:
        assert runtimes.qualified_runtime(runtimes.WEBJAM, q.head, tree).head['head_sha'] == q.head
        assert len(q.calls) == 1
    else:
        with pytest.raises(runtimes.RuntimeUnqualified):
            runtimes.qualified_runtime(runtimes.WEBJAM, q.head, tree)
        assert not q.calls  # The real recipe guard refuses before even the fixture import probe.


def test_worker_inherits_native_umask_but_creates_private_checkout_before_provider(qualified, tmp_path):
    q = qualified
    q.git('branch', 'fix/fixture')
    env = _local_checkout_git_environment(tmp_path)
    actual_run = fix_worker.run
    observed_modes = []
    providers = []

    class ProviderReached(BaseException):
        pass

    def command(args, cwd=None, **kwargs):
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps(dict(headRefName='fix/fixture', headRefOid=q.head,
                                   state='OPEN', isCrossRepository=False, title='Ready', labels=[]))
        if args[0] == '/fixture/codex':
            providers.append(args)
            raise ProviderReached()  # No model, tests, commit, push, or network executes.
        assert args[0] == 'git'
        local = list(args)
        if local[:2] == ['git', 'clone']:
            assert local[-2] == 'https://github.com/' + runtimes.WEBJAM + '.git'
            local[-2] = str(q.tree)
        result = actual_run(['git', '-c', 'core.hooksPath=/dev/null', *local[1:]],
                            cwd, env=env, **kwargs)
        if args[:3] == ['git', 'worktree', 'add']:
            tree = Path(args[-2])
            observed_modes.extend((tree / path).stat().st_mode & 0o777
                                  for path in ('requirements.txt', '.github/workflows/ci.yml'))
        return result

    previous = os.umask(0o002)  # Exact native systemd UMask; restored even on baseline failure.
    try:
        with patch.object(fix_worker, '_find_codex', return_value=Path('/fixture/codex')), \
             patch.object(fix_worker, 'run', side_effect=command):
            with pytest.raises(ProviderReached):
                fix_worker.fix_pr(runtimes.WEBJAM, 396, q.head, ['controlled fixture finding'])
        assert os.umask(0o002) == 0o002
    finally:
        os.umask(previous)
    assert observed_modes == [0o600, 0o600]
    assert len(providers) == len(q.calls) == 1

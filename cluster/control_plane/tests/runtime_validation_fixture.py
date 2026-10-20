"""Controlled closure for alternate-namespace validation integration tests.

The established fixture remains untouched in test_worker_runtime.py. Its setup
is preserved here with a plain local Git identity for source privacy checks.
No provider or hardware probe executes: only the fixed probe response is mocked.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cluster.control_plane import worker_runtime as runtimes


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
            'user.email=fixture.invalid', '-c', 'commit.gpgsign=false',
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


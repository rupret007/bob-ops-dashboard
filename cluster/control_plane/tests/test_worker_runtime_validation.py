"""Exercise the complete extracted validator with an alternate module namespace."""
import hashlib
import importlib.util
import os
import subprocess
import sys

import pytest

from cluster.control_plane import worker_runtime
from cluster.control_plane.tests.runtime_validation_fixture import qualified


@pytest.mark.parametrize('evidence', [
    'valid', 'schema', 'manifest', 'font', 'source', 'probe',
])
def test_full_validation_uses_executing_namespace(qualified, monkeypatch, evidence):
    name = 'cluster.control_plane._validation_integration_fixture'
    spec = importlib.util.spec_from_file_location(name, worker_runtime.__file__)
    alias = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, alias)
    spec.loader.exec_module(alias)
    # A retained function namespace must survive removal of its module alias.
    monkeypatch.delitem(sys.modules, name)
    q = qualified
    if evidence == 'schema':
        q.receipt['schema'] = True
        q.save()
    elif evidence == 'manifest':
        (q.root / 'receipts/wheels.json').write_text('{}')
    elif evidence == 'font':
        (q.root / 'native' / alias.FONT_DIRECTORY / 'Fixture0.ttf').write_bytes(b'changed')
    elif evidence == 'source':
        (q.tree / 'requirements.txt').write_text('unqualified dependency')
    elif evidence == 'probe':
        def failed_probe(args, **kwargs):
            q.calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 1, 'private-sensitive-probe-output', '')
        monkeypatch.setattr(alias.subprocess, 'run', failed_probe)

    if evidence == 'valid':
        runtime = alias.qualified_runtime(alias.WEBJAM, q.head, q.tree)
        assert isinstance(runtime, alias.QualifiedRuntime)
        assert not isinstance(runtime, worker_runtime.QualifiedRuntime)
        assert runtime.receipt_sha256 == hashlib.sha256(
            (q.root / 'qualification.json').read_bytes()).hexdigest()
        assert runtime.head['head_sha'] == q.head
        assert runtime.head['tree_sha'] == q.git('rev-parse', 'HEAD^{tree}')
        assert runtime.python == q.receipt['python']
        assert len(q.calls) == 1
        args, kwargs = q.calls[0]
        assert args == [runtime.python, '-I', '-c', alias._PROBE]
        assert kwargs['timeout'] == 15 and kwargs['capture_output'] is True
        assert kwargs['env']['HOME'] == os.environ['HOME']
        assert kwargs['env']['QT_QPA_PLATFORM'] == 'offscreen'
        assert kwargs['env']['NETRC'] == str(q.root / 'config/empty-netrc')
    else:
        with pytest.raises(alias.RuntimeUnqualified) as caught:
            alias.qualified_runtime(alias.WEBJAM, q.head, q.tree)
        assert str(caught.value) == alias.REASON
        assert 'private-sensitive' not in str(caught.value)
        assert len(q.calls) == (1 if evidence == 'probe' else 0)

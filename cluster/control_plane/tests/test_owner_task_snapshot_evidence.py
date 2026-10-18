"""Snapshot and trusted-runner evidence; never production authorization."""
from pathlib import Path

import pytest

from cluster.control_plane.coordination import CoordinationError
from cluster.control_plane.owner_task_runtime import check_draft_diff


def test_diff_requires_identifiable_executed_regression():
    diff = ('diff --git a/tests/test_chat.py b/tests/test_chat.py\n'
            '--- a/tests/test_chat.py\n+++ b/tests/test_chat.py\n@@ -0,0 +1,2 @@\n'
            '+def test_phone_chat():\n+    assert 1 == 1\n')
    def git(args):
        if '--diff-filter=D' in args:
            return ''
        return 'tests/test_chat.py' if '--name-only' in args else diff
    assert check_draft_diff(None, {'head_sha': 'b' * 40}, git) == frozenset({'tests/test_chat.py::test_phone_chat'})


def test_snapshot_retains_tested_bytes_after_source_changes(tmp_path):
    from cluster.control_plane.owner_task_snapshot import WorkspaceSnapshot
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'app.py').write_bytes(b'tested content\n')
    (source / '.git').write_text('trusted metadata pointer')
    snapshot = WorkspaceSnapshot.capture(source)
    original_digest = snapshot.digest
    (source / 'app.py').write_bytes(b'untested mutation\n')
    target = snapshot.materialize(tmp_path / 'publish')
    assert (target / 'app.py').read_bytes() == b'tested content\n'
    assert not (target / '.git').exists()
    assert snapshot.digest == original_digest
    assert WorkspaceSnapshot.capture(source) != snapshot


def test_snapshot_rejects_symlinks_and_preserves_executable_mode(tmp_path):
    from cluster.control_plane.owner_task_snapshot import WorkspaceSnapshot
    source = tmp_path / 'source'
    source.mkdir()
    executable = source / 'script.sh'
    executable.write_text('exit 0\n')
    executable.chmod(0o755)
    target = WorkspaceSnapshot.capture(source).materialize(tmp_path / 'publish')
    assert (target / 'script.sh').stat().st_mode & 0o111
    (source / 'link').symlink_to(executable)
    with pytest.raises(CoordinationError, match='Non-regular'):
        WorkspaceSnapshot.capture(source)


@pytest.mark.parametrize('attack', ['missing', 'wrong_digest', 'skip', 'error', 'unexecuted', 'no_baseline', 'untrusted', 'red_suite', 'wrong_baseline'])
def test_regression_evidence_requires_observed_fail_before_pass_after(attack):
    from cluster.control_plane.owner_task_snapshot import verify_regression_evidence
    test = 'tests/test_chat.py::test_phone_chat'
    evidence = dict(snapshot_sha256='a' * 64, observer='trusted-outside-sandbox-v1',
                    baseline_sha='b' * 40, candidate_suite_passed=True,
                    baseline={test: 'failed'}, candidate={test: 'passed'})
    verify_regression_evidence(evidence, 'a' * 64, {test}, 'b' * 40)
    if attack == 'missing':
        evidence = None
    elif attack == 'wrong_digest':
        evidence['snapshot_sha256'] = 'b' * 64
    elif attack in ('skip', 'error'):
        evidence['candidate'][test] = attack
    elif attack == 'unexecuted':
        evidence['candidate'] = {}
    elif attack == 'no_baseline':
        evidence['baseline'][test] = 'passed'
    elif attack == 'red_suite':
        evidence['candidate_suite_passed'] = False
    elif attack == 'wrong_baseline':
        evidence['baseline_sha'] = 'c' * 40
    else:
        evidence['observer'] = 'repository-result-file'
    with pytest.raises(CoordinationError, match='regression evidence'):
        verify_regression_evidence(evidence, 'a' * 64, {test}, 'b' * 40)

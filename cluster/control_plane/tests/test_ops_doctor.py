import json
from unittest.mock import patch
import pytest
from cluster.control_plane.ops_doctor import reserve_attempt


def test_daily_limit_reserved_before_repair(tmp_path):
    assert [reserve_attempt(tmp_path, '2026-10-18') for _ in range(5)] == [True, True, True, False, False]
    assert json.loads((tmp_path / 'daily.json').read_text())['attempts'] == 3
    assert reserve_attempt(tmp_path, '2026-10-19')


def test_doctor_uses_supported_codex_argv_and_stdin(tmp_path):
    from cluster.control_plane.ops_doctor import _repair_claimed
    from cluster.control_plane.fix_worker import run

    log = tmp_path / 'doctor.log'
    log.write_text('Conductor freshness check failed')
    codex = tmp_path / 'codex'
    # Stop at the paid CLI boundary: no model call, commit, push, or PR creation.
    with patch('cluster.control_plane.ops_doctor._find_codex', return_value=codex), \
            patch('cluster.control_plane.ops_doctor.run', side_effect=lambda args, *a, **kw:
                  run(args, *a, **kw) if args[0] == str(codex) else '') as command, \
            patch('cluster.control_plane.ops_doctor.subprocess.run',
                  side_effect=RuntimeError('CLI boundary')) as execute:
        with pytest.raises(RuntimeError, match='CLI boundary'):
            _repair_claimed(tmp_path, log, 'test', 'a' * 40)

    argv = execute.call_args.args[0]
    assert argv == [str(codex), 'exec', '-s', 'workspace-write',
                    '-c', 'model_reasoning_effort=xhigh',
                    '-c', 'sandbox_workspace_write.network_access=true', '-']
    assert '--full-auto' not in argv
    assert 'Conductor freshness check failed' in execute.call_args.kwargs['input']
    assert execute.call_args.kwargs['text'] is True
    assert execute.call_args.kwargs['check'] is True
    assert command.call_count == 3  # clone, pinned worktree, supported paid command
    assert command.call_args_list[1].args[0][-1] == 'a' * 40

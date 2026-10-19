"""Guard deferral must still report which source the poller loaded."""
from types import SimpleNamespace
from unittest.mock import patch

from cluster.control_plane.local_karen import poller
from cluster.control_plane.local_karen.config import KarenConfig


def test_swap_guard_records_loaded_source_without_starting_work(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    with patch.object(poller, 'record_runtime') as receipt, \
         patch('cluster.control_plane.guards.MachineGuards') as guards, \
         patch.object(poller, '_run_poll_once') as work:
        guards.return_value.check_all.return_value = SimpleNamespace(passed=False, reason='swap 84% >= 80%')
        assert poller.run_poll_once(config) == []
    receipt.assert_called_once_with(config)
    work.assert_not_called()

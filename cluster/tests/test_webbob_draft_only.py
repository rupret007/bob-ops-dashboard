"""CI and a Karen label cannot remove the Web Bob draft gate."""
from unittest.mock import patch
import pytest
from cluster.control_plane.backlog import undraft_green_prs


@pytest.mark.parametrize('repo', ['rupret007/bob-brain', 'rupret007/web-bob', 'RUPRET007/BOB-BRAIN'])
@pytest.mark.parametrize('dry_run', [True, False])
def test_webbob_is_never_automatically_undrafted(repo, dry_run):
    with patch('cluster.control_plane.backlog.get_open_prs') as read, \
         patch('cluster.control_plane.backlog.subprocess.run') as mutate:
        assert undraft_green_prs([repo], dry_run=dry_run) == []
        read.assert_not_called()
        mutate.assert_not_called()


def test_manual_webbob_gate_does_not_hide_other_queued_work():
    with patch('cluster.control_plane.backlog.get_open_prs', return_value=[]) as read:
        assert undraft_green_prs(['rupret007/bob-brain', 'rupret007/StoryBoard'], dry_run=True) == []
        read.assert_called_once_with('rupret007/StoryBoard')

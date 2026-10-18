"""Damaged local merge safeguards must never authorize another merge."""
import json
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import conductor as c
from cluster.control_plane.tests.test_deterministic_merge import artifact, pr


def runner(tmp_path):
    return c.Conductor(log_dir=tmp_path, verdict_dir=tmp_path,
                       budget_tracker=Mock(), metrics_tracker=Mock())


def state(**changes):
    return dict(dict(merges=[], consecutive_ci_failures=0,
                     paused=False, pause_reason=''), **changes)


@pytest.mark.parametrize('contents', [
    '{', 'null', '[]', '{}',
    json.dumps(state(paused='false')), json.dumps(state(paused=None)),
    json.dumps(state(merges=None)), json.dumps(state(merges=[None])),
    json.dumps(state(merges=[{}])), json.dumps(state(merges=[{'ts': '0'}])),
    json.dumps(state(merges=[{'ts': True}])),
    json.dumps(state(merges=[{'ts': float('nan')}])),
    json.dumps(state(merges=[{'ts': float('inf')}])),
    json.dumps(state(merges=[{'ts': -1}])),
    json.dumps(state(consecutive_ci_failures=-1)),
    json.dumps(state(consecutive_ci_failures=True)),
    json.dumps(state(pause_reason=None)),
])
def test_invalid_state_blocks_even_otherwise_eligible_merge(tmp_path, contents):
    worker = runner(tmp_path)
    artifact(tmp_path)
    worker._merge_state_file().write_text(contents)
    with patch.object(c, '_get_pr_details', return_value=pr()):
        result = worker.validate_action(c.ProposedAction(
            'merge_pr', repo='owner/repo', pr_number=7, head_sha='a'*40), None)
    assert not result.validated
    assert 'Merge safety state unavailable' in result.validation_reason
    assert worker._merge_state_file().read_text() == contents


def test_unreadable_state_blocks_and_hides_exception_details(tmp_path):
    worker = runner(tmp_path)
    worker._save_merge_state(state())
    with patch.object(c.Path, 'read_text', side_effect=PermissionError('private detail')):
        allowed, reason = worker._check_merge_rate_limit()
    assert not allowed and 'Merge safety state unavailable' in reason
    assert 'private detail' not in reason


def test_dangling_state_link_is_not_first_start(tmp_path):
    worker = runner(tmp_path)
    worker._merge_state_file().symlink_to(tmp_path/'missing')
    assert not worker._check_merge_rate_limit()[0]


def test_first_start_and_valid_state_keep_existing_limits(tmp_path):
    worker = runner(tmp_path)
    assert worker._check_merge_rate_limit() == (True, 'ok')
    now = c.datetime.now(c.timezone.utc).timestamp()
    worker._save_merge_state(state(merges=[{'ts': now}] * c.MAX_MERGES_PER_HOUR))
    assert not worker._check_merge_rate_limit()[0]
    worker._save_merge_state(state(paused=True, pause_reason='operator pause'))
    assert worker.is_paused() == (True, 'operator pause')


def test_failed_atomic_replacement_preserves_pause_and_cleans_temp(tmp_path):
    worker = runner(tmp_path)
    original = state(paused=True, pause_reason='main broken')
    worker._save_merge_state(original)
    with patch.object(c.os, 'replace', side_effect=OSError('disk error')):
        with pytest.raises(OSError):
            worker._save_merge_state(state())
    assert json.loads(worker._merge_state_file().read_text()) == original
    assert {p.name for p in tmp_path.iterdir()} == {'merge-state.json'}


def test_atomic_replacement_exposes_complete_old_then_new_state(tmp_path):
    worker = runner(tmp_path)
    worker._save_merge_state(state(paused=True, pause_reason='old'))
    real_replace = c.os.replace
    def replace(source, target):
        assert worker._load_merge_state()['pause_reason'] == 'old'
        assert json.loads(c.Path(source).read_text()) == state()
        return real_replace(source, target)
    with patch.object(c.os, 'replace', side_effect=replace) as swapped:
        worker._save_merge_state(state())
    swapped.assert_called_once()
    assert worker._load_merge_state() == state()
    assert {p.name for p in tmp_path.iterdir()} == {'merge-state.json'}


def test_resume_cannot_overwrite_damaged_history(tmp_path):
    worker = runner(tmp_path)
    worker._merge_state_file().write_text('{')
    with pytest.raises(ValueError, match='Merge safety state unavailable'):
        worker.resume()
    assert worker._merge_state_file().read_text() == '{'

"""Configured mixed-case never-touch names must retain their guard."""
import pytest
from cluster.never_touch import assert_not_never_touch, check_merge_allowed, is_never_touch, NeverTouchViolation


@pytest.mark.parametrize('repo', ['rupret007/HangOut', 'rupret007/hangout', 'RUPRET007/HANGOUT'])
def test_mixed_case_never_touch_is_blocked_for_writes_and_merges(repo):
    assert is_never_touch(repo)
    assert not check_merge_allowed(repo)[0]
    with pytest.raises(NeverTouchViolation):
        assert_not_never_touch(repo, 'queue review')

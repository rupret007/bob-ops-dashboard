"""Real public and legacy callers must share the same dataclass identities."""
import pytest

from cluster.control_plane.local_karen import check_types, checks, reviewers


@pytest.mark.parametrize("name", ["PRInfo", "ReviewScope", "ReviewMaterial"])
def test_public_and_legacy_review_types_are_the_canonical_objects(name):
    canonical = getattr(check_types, name)
    assert getattr(reviewers, name) is canonical
    assert getattr(checks, name) is canonical

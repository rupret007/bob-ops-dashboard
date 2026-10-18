from unittest.mock import patch
from cluster.control_plane.local_karen import checks
from cluster.control_plane.local_karen.checks import PRInfo, CheckVerdict, check_pages_exposure

DIFF = "diff --git a/docs/guide.md b/docs/guide.md\n+++ b/docs/guide.md\n+hello\n"


def _pr(diff=DIFF):
    return PRInfo(owner="o", repo="r", number=1, head_sha="a" * 40, title="t", author="a",
                  is_draft=False, diff=diff, ci_status={}, changed_files=[], deleted_files=[])


def test_docs_change_passes_when_repo_has_no_pages():
    with patch.object(checks, "_pages_source", return_value=None):
        assert check_pages_exposure(_pr(), None).verdict == CheckVerdict.PASS


def test_docs_change_fails_when_pages_publishes_root_or_docs():
    for src in ("/", "/docs"):
        with patch.object(checks, "_pages_source", return_value=src):
            assert check_pages_exposure(_pr(), None).verdict == CheckVerdict.FAIL


def test_unknown_pages_state_fails_closed():
    with patch.object(checks, "_pages_source", return_value=checks._UNKNOWN):
        assert check_pages_exposure(_pr(), None).verdict == CheckVerdict.FAIL


def test_cname_still_fails_without_pages_lookup():
    diff = "diff --git a/CNAME b/CNAME\n+++ b/CNAME\n+x.com\n"
    with patch.object(checks, "_pages_source", side_effect=AssertionError("no lookup")):
        assert check_pages_exposure(_pr(diff), None).verdict == CheckVerdict.FAIL

"""Compatibility exports for the complete literal and frozen-contract guard.

The implementation is in assertion_guard so its complete executable boundary
is visible in a single normal review chunk. Definitions, policy values and
public exports remain unchanged.
"""
from __future__ import annotations

from .assertion_guard import (
    re, KarenConfig, CheckVerdict, CheckResult, PRInfo, TEST_FILE_PATTERNS,
    _is_test_file, _removed_lines, _extract_diff_deletions,
    _renamed_js_test_headers, _literal_equivalent_line, _new_test_source_files,
    _added_lines, check_tests_not_weakened,
)

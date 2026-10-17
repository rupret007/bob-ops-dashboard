# PR #75: unknown usage regression evidence

Karen gate follow-up (01:21 CT). Tests were added before production fixes.
Pre-fix source commit (after merging origin/main): `2e3e1328a9ded8f3a7d75abc79b256f3d6aec090`.

Command: `python3 -m unittest -v test_usage_collect`

Exit status: **1**; 28 tests run, **12 failing assertions/subtests**.
Existing tests were retained unchanged. New tests exercise the publication path `usage-host-*.json -> load_merged_usage -> merge_host_snapshots -> normalize_usage_machines`.

```text
FAIL: test_committed_unknown_measurements_are_null (test_usage_collect.PublishedUsageRegressionTests.test_committed_unknown_measurements_are_null)
FAIL: test_optional_sum_requires_every_part (test_usage_collect.PublishedUsageRegressionTests.test_optional_sum_requires_every_part) (a=None, b=7)
FAIL: test_optional_sum_requires_every_part (test_usage_collect.PublishedUsageRegressionTests.test_optional_sum_requires_every_part) (a=7, b=None)
FAIL: test_optional_sum_requires_every_part (test_usage_collect.PublishedUsageRegressionTests.test_optional_sum_requires_every_part) (a=0, b=None)
FAIL: test_optional_sum_requires_every_part (test_usage_collect.PublishedUsageRegressionTests.test_optional_sum_requires_every_part) (a=None, b=0)
FAIL: test_published_mixed_hosts_remain_unknown_in_either_order (test_usage_collect.PublishedUsageRegressionTests.test_published_mixed_hosts_remain_unknown_in_either_order) (unknown={'total_tokens': 10}, order=['mini', 'nucbox'])
FAIL: test_published_mixed_hosts_remain_unknown_in_either_order (test_usage_collect.PublishedUsageRegressionTests.test_published_mixed_hosts_remain_unknown_in_either_order) (unknown={'total_tokens': 10}, order=['nucbox', 'mini'])
FAIL: test_published_mixed_hosts_remain_unknown_in_either_order (test_usage_collect.PublishedUsageRegressionTests.test_published_mixed_hosts_remain_unknown_in_either_order) (unknown={'total_tokens': 10, 'cached_tokens': None, 'output_tokens': None, 'reasoning_tokens': None}, order=['mini', 'nucbox'])
FAIL: test_published_mixed_hosts_remain_unknown_in_either_order (test_usage_collect.PublishedUsageRegressionTests.test_published_mixed_hosts_remain_unknown_in_either_order) (unknown={'total_tokens': 10, 'cached_tokens': None, 'output_tokens': None, 'reasoning_tokens': None}, order=['nucbox', 'mini'])
FAIL: test_published_mixed_sessions_remain_unknown (test_usage_collect.PublishedUsageRegressionTests.test_published_mixed_sessions_remain_unknown) (reverse=False)
FAIL: test_published_mixed_sessions_remain_unknown (test_usage_collect.PublishedUsageRegressionTests.test_published_mixed_sessions_remain_unknown) (reverse=True)
FAIL: test_published_unknown_machine_values_have_no_unit (test_usage_collect.PublishedUsageRegressionTests.test_published_unknown_machine_values_have_no_unit)
Ran 28 tests
FAILED (failures=12)
```

Observed failures include mixed known/unknown tokens becoming 7, committed unsupported cached/reasoning zeros, and `mem unknownG · swap unknownG`.

## Fixed behavior and manual mutation checks

Red test commits: `bfb2e36` and `ae79eec` (the latter adds the two-unknown-host case; the pre-fix run still reports 28 tests / 12 failing assertions).

Optional sums now return null if either observation is unknown. Session and host aggregation starts with the first observation, preserving fully known counts and explicit zero. Unsupported cached/reasoning zeros and older output zeros in the committed aggregate were nulled, along with mini memory/swap. No live collection or invented replacement values. Memory/swap units are added only for numeric values.

Each mutant below was applied alone to the fixed `usage_collect.py`, run with `python3 -B -m unittest -v test_usage_collect.PublishedUsageRegressionTests.<test>`, and reverted before applying the next. All failures were assertion failures, not import or syntax errors. Tests were not changed during mutation runs.

| Hand-applied mutant (one at a time) | Publication-path test that kills it | Result |
| --- | --- | --- |
| `coerce_cached_tokens_with_int_or_zero` | `test_published_missing_and_null_fields_remain_unknown` | exit 1; FAILED (failures=4) |
| `coerce_output_tokens_with_int_or_zero` | `test_published_missing_and_null_fields_remain_unknown` | exit 1; FAILED (failures=4) |
| `coerce_reasoning_tokens_with_int_or_zero` | `test_published_missing_and_null_fields_remain_unknown` | exit 1; FAILED (failures=4) |
| `none_plus_none_returns_zero` | `test_published_missing_and_null_fields_remain_unknown` | exit 1; FAILED (failures=2) |
| `default_cached_tokens_to_zero` | `test_published_default_token_fields_remain_unknown` | exit 1; FAILED (failures=1) |
| `default_output_tokens_to_zero` | `test_published_default_token_fields_remain_unknown` | exit 1; FAILED (failures=1) |
| `default_reasoning_tokens_to_zero` | `test_published_default_token_fields_remain_unknown` | exit 1; FAILED (failures=1) |
| `remove_multi_host_historical_machine_nulling` | `test_published_disjoint_host_days_null_historical_machines` | exit 1; FAILED (failures=1) |
| `mixed_unknown_treated_as_zero` | `test_published_mixed_hosts_remain_unknown_in_either_order` | exit 1; FAILED (failures=4) |

Restored source: `python3 -m unittest -v test_usage_collect` — **28 tests, OK**.

`python3 -m unittest -v test_board_meta test_pr_source_only` — **123 tests, OK**.

Local `python3 qa-offline.py` was blocked before fixture generation by the existing `/tmp/.git` ancestor: `ValueError: Fixture files must not overwrite a repository`. The safety guard is unchanged; the full offline smoke is required in CI.

Strict cluster suite: after installing the pinned dependencies into `/tmp/pr75-deps`, `PYTHONPATH=/tmp/pr75-deps:. BOB_REQUIRE_NATS=1 python3 -m cluster.tests.run_strict` — **105 tests, OK; skipped=0, failed=0, missing_required=[]**. The initial attempt lacked `nats-py`; no tests were loosened or skipped.

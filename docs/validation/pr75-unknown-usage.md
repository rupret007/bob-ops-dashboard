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

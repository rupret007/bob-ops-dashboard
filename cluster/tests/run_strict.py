import argparse
import sys
import unittest

# Pinned exact set — do not empty, shrink, or replace without updating
# TestStrictGuard.test_default_require_is_exactly_pinned.
DEFAULT_REQUIRE = [
    "cluster.tests.test_nats_integration.TestWorkerPermissions.test_template_allows_work_and_denies_administration",
    "cluster.tests.test_nats_integration.TestNatsIntegration.test_setup_and_publish",
    "cluster.tests.test_idle_timeout.TestFetchOrEmpty.test_non_idle_errors_propagate",
    "cluster.tests.test_idle_timeout.TestNatsDependentSkipGuard.test_non_idle_errors_runs_when_nats_available",
]


class StrictTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append(test.id())


def run_strict(start="cluster/tests", top_level=".", require=None):
    """Discover and run tests under strict rules. Returns process exit code."""
    required_ids = list(DEFAULT_REQUIRE if require is None else require)
    if required_ids == ["none"]:
        required_ids = []

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=start, top_level_dir=top_level)

    runner = unittest.TextTestRunner(verbosity=2, resultclass=StrictTestResult)
    result = runner.run(suite)

    missing_required = []
    for req in required_ids:
        if req not in result.successes:
            missing_required.append(req)

    failed = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    expected_failures = len(result.expectedFailures)
    unexpected_successes = len(result.unexpectedSuccesses)
    ran = result.testsRun

    print(
        f"STRICT ran={ran} skipped={skipped} failed={failed} "
        f"expectedFailures={expected_failures} "
        f"unexpectedSuccesses={unexpected_successes} "
        f"missing_required={missing_required}"
    )

    if (
        failed > 0
        or ran == 0
        or skipped > 0
        or expected_failures > 0
        or unexpected_successes > 0
        or missing_required
    ):
        if skipped > 0:
            print("Skipped tests:")
            for test, reason in result.skipped:
                print(f"  {test.id()}: {reason}")
        if expected_failures > 0:
            print("Expected failures (forbidden under strict):")
            for test, _err in result.expectedFailures:
                print(f"  {test.id()}")
        if unexpected_successes > 0:
            print("Unexpected successes (forbidden under strict):")
            for test in result.unexpectedSuccesses:
                print(f"  {test.id()}")
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="cluster/tests")
    parser.add_argument("--top-level", default=".")
    parser.add_argument("--require", nargs="+", default=None)
    args = parser.parse_args(argv)
    return run_strict(start=args.start, top_level=args.top_level, require=args.require)


if __name__ == "__main__":
    sys.exit(main())

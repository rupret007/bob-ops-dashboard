import argparse
import sys
import unittest

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="cluster/tests")
    parser.add_argument("--top-level", default=".")
    parser.add_argument("--require", nargs="+", default=DEFAULT_REQUIRE)
    args = parser.parse_args()

    required_ids = args.require
    if required_ids == ["none"]:
        required_ids = []

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=args.start, top_level_dir=args.top_level)

    runner = unittest.TextTestRunner(verbosity=2, resultclass=StrictTestResult)
    result = runner.run(suite)

    missing_required = []
    for req in required_ids:
        if req not in result.successes:
            missing_required.append(req)

    failed = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    ran = result.testsRun

    print(f"STRICT ran={ran} skipped={skipped} failed={failed} missing_required={missing_required}")

    if failed > 0 or ran == 0 or skipped > 0 or missing_required:
        if skipped > 0:
            print("Skipped tests:")
            for test, reason in result.skipped:
                print(f"  {test.id()}: {reason}")
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()

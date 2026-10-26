"""
test_e2e_runner.py - Master E2E Test Suite Aggregator and Runner for AsciiNeko Golden Architecture.
Executable via:
    python -m unittest tests/test_e2e_runner.py
    python tests/test_e2e_runner.py
    python -m unittest discover tests/ -p "test_*.py" -v

Executes:
- Tier 1: Feature Coverage (F01 through F30, >=5 assertions/feature)
- Tier 2: Boundaries, Concurrency & Vulnerabilities (VULN-01..07)
- Tier 3: Cross-Feature Combinations & Pairwise Interactions
- Tier 4: Real-World Workload Scenarios (5 production scenarios)
"""

import os
import sys
import time
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tests.test_tier1_features as tier1
import tests.test_tier2_boundaries as tier2
import tests.test_tier3_combinations as tier3
import tests.test_tier4_workloads as tier4


def build_master_suite() -> unittest.TestSuite:
    """Builds and returns the complete master test suite across all 4 tiers."""
    loader = unittest.TestLoader()
    master_suite = unittest.TestSuite()

    # Load Tier 1
    t1_suite = loader.loadTestsFromModule(tier1)
    master_suite.addTests(t1_suite)

    # Load Tier 2
    t2_suite = loader.loadTestsFromModule(tier2)
    master_suite.addTests(t2_suite)

    # Load Tier 3
    t3_suite = loader.loadTestsFromModule(tier3)
    master_suite.addTests(t3_suite)

    # Load Tier 4
    t4_suite = loader.loadTestsFromModule(tier4)
    master_suite.addTests(t4_suite)

    return master_suite


def load_tests(loader, standard_tests, pattern):
    """unittest discovery protocol: returns master suite when loaded as test module."""
    return build_master_suite()


class MasterE2ESuiteTest(unittest.TestCase):
    """
    Test case ensuring test suite integrity, module discovery, and tier availability.
    """

    def test_runner_discovers_all_tiers(self):
        suite = build_master_suite()
        total_tests = suite.countTestCases()
        # Tier 1 has 30 tests, Tier 2 has 9 tests, Tier 3 has 8 tests, Tier 4 has 5 tests -> total >= 52
        self.assertGreaterEqual(total_tests, 50, f"Expected >= 50 test cases, found {total_tests}")

    def test_tier_modules_exist(self):
        self.assertIsNotNone(tier1)
        self.assertIsNotNone(tier2)
        self.assertIsNotNone(tier3)
        self.assertIsNotNone(tier4)


def main():
    print("=" * 80)
    print("  ASCIINEKO GOLDEN ARCHITECTURE (PHASES 1, 2, 3) — MASTER E2E TEST RUNNER")
    print("=" * 80)
    start_time = time.time()

    suite = build_master_suite()
    total_count = suite.countTestCases()
    print(f"Loaded {total_count} test cases across Tiers 1-4.")
    print("-" * 80)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    elapsed = time.time() - start_time
    print("=" * 80)
    print(f"E2E Execution Finished in {elapsed:.2f} seconds.")
    print(f"Total Tests Run: {result.testsRun}")
    print(f"Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("=" * 80)

    if result.wasSuccessful():
        print(">> VERDICT: ALL E2E TEST TIERS PASSED [100% SUCCESS] <<")
        return 0
    else:
        print(">> VERDICT: E2E TEST SUITE REPORTED FAILURES <<")
        return 1


if __name__ == "__main__":
    sys.exit(main())

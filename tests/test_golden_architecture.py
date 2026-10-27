"""
test_golden_architecture.py - Master E2E runner adapter for AsciiNeko Golden Architecture (Phases 1, 2, and 3).
Aliases and executes the master E2E suite defined in tests.test_e2e_runner.
"""

import sys
import unittest
from tests.test_e2e_runner import build_master_suite, load_tests, MasterE2ESuiteTest, main

if __name__ == "__main__":
    sys.exit(main())

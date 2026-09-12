#!/usr/bin/env python3
"""Run the whole test suite with no arguments and no dependencies.

    python eval/tests/run_tests.py            # everything
    python eval/tests/run_tests.py validation # only modules matching a substring

Every test is offline and uses the fake LLM provider, so no API key is needed
and no call can be billed.  The runner refuses to start if the environment is
configured to use a paid provider, which makes an accidental billed test run
impossible rather than merely unlikely.
"""

from __future__ import annotations

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def guard_against_paid_calls() -> None:
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    enabled = os.environ.get("LLM_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
    if enabled and provider not in ("", "fake", "mock", "test", "disabled", "none"):
        print(f"refusing to run: LLM_ENABLED is set with provider {provider!r}.\n"
              f"The suite is designed to run against the fake provider; unset LLM_ENABLED "
              f"or set LLM_PROVIDER=fake.", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    guard_against_paid_calls()
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=TESTS_DIR, pattern="test_*.py", top_level_dir=PROJECT_ROOT)

    if pattern:
        filtered = unittest.TestSuite()

        def keep(item):
            if isinstance(item, unittest.TestSuite):
                for child in item:
                    keep(child)
            elif pattern in item.id():
                filtered.addTest(item)

        keep(suite)
        suite = filtered

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n{result.testsRun} tests, {len(result.failures)} failures, "
          f"{len(result.errors)} errors, {len(result.skipped)} skipped")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Guarded entry point for the G2-A Python v2 offline suite."""

from tars_phase1a.guard import activate_guards

activate_guards()

import json  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


def main() -> int:
    protocol_root = Path(__file__).resolve().parents[2]
    test_root = protocol_root / "python" / "tests"
    suite = unittest.TestSuite()
    for pattern in ("test_*_v2.py", "test_v2_*.py"):
        suite.addTests(unittest.defaultTestLoader.discover(str(test_root), pattern=pattern))
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    summary = {
        "errors": len(result.errors),
        "failures": len(result.failures),
        "phase": "2A-guard",
        "successful": result.wasSuccessful(),
        "testsRun": result.testsRun,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Run current offline regression fixtures and refresh handbook coverage.

The baseline audit is preserved in audit_results_baseline.json. Network
connections are blocked. No market profitability or actual venue parity is
asserted. Install the editable package before running this command.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import inspect
import json
import logging
from pathlib import Path
import platform
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch
import dotenv
from coverage import build_coverage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def blocked(*args, **kwargs):
    raise RuntimeError("network forbidden in offline audit")


class Result(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.rows = []

    def record(self, test, status, evidence):
        method = getattr(type(test), test._testMethodName)
        path = Path(inspect.getsourcefile(method))
        self.rows.append({"check": test.id(), "status": status, "evidence": evidence,
                          "source": {"path": str(path.relative_to(ROOT)), "line": inspect.getsourcelines(method)[1]}})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "PASS", test._testMethodName.removeprefix("test_").replace("_", " "))

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.record(test, "FAIL", self._exc_info_to_string(error, test))

    def addError(self, test, error):
        super().addError(test, error)
        self.record(test, "ERROR", self._exc_info_to_string(error, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "SKIP", reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "analysis" / "audit_results.json")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    with ExitStack() as guards:
        guards.enter_context(patch.object(socket.socket, "connect", blocked))
        guards.enter_context(patch.object(socket.socket, "connect_ex", blocked))
        guards.enter_context(patch.object(socket, "create_connection", blocked))
        guards.enter_context(patch.object(dotenv, "load_dotenv", return_value=False))
        guards.enter_context(patch.dict("os.environ", {"ROOSTOO_API_KEY": "", "ROOSTOO_API_SECRET": ""}))
        from roostoo_bot import config
        from roostoo_bot.main import DRY_RUN
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
        result = Result()
        suite.run(result)
        coverage = build_coverage(ROOT, config)
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(ROOT.rglob("*.py")) if not ({".venv", "build"} & set(p.parts))}
    import numpy, pandas, sklearn, requests, pyarrow
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree_changes_tested": True,
        "scope": "Offline deterministic regression fixtures, synthetic paper ledgers and mocked API responses. No actual orders, historical profitability or organizer metric parity verified.",
        "python": platform.python_version(),
        "dependencies": {"numpy": numpy.__version__, "pandas": pandas.__version__, "scikit-learn": sklearn.__version__, "requests": requests.__version__, "pyarrow": pyarrow.__version__},
        "handbook_sha256": hashlib.sha256((ROOT / "Crypto_Algorithmic_Trading_Hackathon_Handbook.md").read_bytes()).hexdigest(),
        "source_hashes": hashes,
        "baseline": {"path": "analysis/audit_results_baseline.json", "counts": {"PASS": 9, "FAIL": 24}},
        "live_flags": {"DRY_RUN": DRY_RUN, "LIVE_TRADING_ENABLED": config.LIVE_TRADING_ENABLED, "ALLOW_SHORTS": config.ALLOW_SHORTS},
        "counts": dict(Counter(r["status"] for r in result.rows)), "checks": result.rows, "coverage": coverage,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for row in result.rows:
        if row["status"] != "PASS":
            print(row["status"], row["check"], row["evidence"])
    print(f'{result.testsRun} offline fixtures: {report["counts"]}. Results: {args.output}')
    return 2 if result.errors else 1 if result.failures or result.skipped else 0


if __name__ == "__main__":
    sys.exit(main())

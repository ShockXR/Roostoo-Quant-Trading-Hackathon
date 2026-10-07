"""
Run from the project root:

    .venv/bin/python -m unittest discover -s tests -v
"""
import pathlib
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from common import LARGE_CAPS, MID_CAPS, register_package  # noqa: E402

register_package()
from run_backtest import context_raw  # noqa: E402


def closes(symbols):
    idx = pd.date_range("2026-01-01", periods=5, freq="1min", tz="UTC")
    return {s: pd.Series(range(1, 6), index=idx, dtype=float) for s in symbols}


class ContextTest(unittest.TestCase):
    def test_large_cap_context_is_the_five_majors(self):
        raw = context_raw("ETHUSDT", closes(LARGE_CAPS))
        self.assertEqual(list(raw), ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD"])

    def test_mid_cap_context_adds_the_symbol_to_the_majors(self):
        # same context scripts/backtest_sol.py gives any symbol: majors + itself
        raw = context_raw("UNIUSDT", closes(LARGE_CAPS + MID_CAPS))
        self.assertEqual(list(raw), ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "UNI/USD"])
        self.assertEqual(list(raw["UNI/USD"].columns), ["timestamp", "last_price"])


if __name__ == "__main__":
    unittest.main()

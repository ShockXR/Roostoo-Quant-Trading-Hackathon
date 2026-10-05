"""
Run from the project root:

    .venv/bin/python -m unittest discover -s tests -v
"""
import pathlib
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from common import register_package  # noqa: E402

register_package()
from roostoo.strategy import generate_targets  # noqa: E402


def candles(n=20000, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.standard_t(3, n) * 0.0008))
    return pd.DataFrame({"close": close}, index=idx)


def reversal(ohlcv, **params):
    return generate_targets("reversal", "X/USD", {}, None, ohlcv, params)


class ReversalTest(unittest.TestCase):
    def test_no_lookahead(self):
        # targets up to bar t must not change when bars after t change
        o = candles()
        cut = 12000
        full = reversal(o, lookback=720, k=1.0)
        self.assertGreater(full.sum(), 0)
        altered = o.copy()
        altered.iloc[cut + 1:, 0] *= 0.5
        part = reversal(altered, lookback=720, k=1.0)
        pd.testing.assert_series_equal(full.iloc[:cut + 1], part.iloc[:cut + 1])

    def test_entries_only_at_block_close_and_hold_length(self):
        o = candles()
        t = reversal(o, lookback=720, k=1.0, hold=300, every=60)
        entries = t.index[(t > 0) & (t.shift(fill_value=0) == 0)]
        self.assertTrue(len(entries) > 0)
        self.assertTrue(all(ts.minute == 59 for ts in entries))
        # a position lasts at least `hold` bars (fresh signals can only extend it)
        runs = (t > 0).astype(int).groupby(((t > 0) != (t > 0).shift()).cumsum()).sum()
        self.assertGreaterEqual(runs[runs > 0].min(), 300)

    def test_defaults_come_from_config(self):
        from roostoo import config
        o = candles(n=6000)
        self.assertTrue(reversal(o).equals(reversal(
            o, lookback=config.REVERSAL_LOOKBACK_BARS, k=config.REVERSAL_K,
            hold=config.REVERSAL_HOLD_BARS, every=config.REVERSAL_CHECK_EVERY)))
        self.assertLessEqual(reversal(o, k=0.1).max(), config.MAX_POSITION_PER_ASSET)


if __name__ == "__main__":
    unittest.main()

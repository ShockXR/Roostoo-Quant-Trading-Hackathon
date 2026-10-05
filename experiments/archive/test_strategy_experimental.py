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
    def test_loop_matches_vectorized_when_options_off(self):
        o = candles()
        base = {"lookback": 720, "k": 1.0, "hold": 720}
        vec = reversal(o, **base)
        # an unreachable stop and an unbinding budget take the loop path
        loop = reversal(o, **base, stop=1e9, risk_budget=1e9)
        self.assertGreater(vec.sum(), 0)
        pd.testing.assert_series_equal(vec, loop, check_names=False)

    def test_no_lookahead(self):
        # targets up to bar t must not change when bars after t change
        o = candles()
        cut = 12000
        full = reversal(o, lookback=720, k=1.0, stop=2.0, risk_budget=0.004)
        altered = o.copy()
        altered.iloc[cut + 1:, 0] *= 0.5
        part = reversal(altered, lookback=720, k=1.0, stop=2.0, risk_budget=0.004)
        pd.testing.assert_series_equal(full.iloc[:cut + 1], part.iloc[:cut + 1])

    def test_risk_budget_caps_weight(self):
        o = candles()
        t = reversal(o, lookback=720, k=1.0, risk_budget=0.0005)
        self.assertLessEqual(t.max(), 0.2)
        self.assertLess(t[t > 0].min(), 0.2)

    def test_stop_exits_early(self):
        o = candles()
        plain = reversal(o, lookback=720, k=1.0)
        stopped = reversal(o, lookback=720, k=1.0, stop=0.5)
        self.assertLess((stopped > 0).sum(), (plain > 0).sum())


if __name__ == "__main__":
    unittest.main()

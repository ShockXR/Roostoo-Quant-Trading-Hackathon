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
from roostoo.backtest.metrics import report  # noqa: E402
from roostoo.backtest.simulator import Costs, simulate  # noqa: E402
from roostoo.model.train import walk_forward_splits  # noqa: E402

NO_COSTS = Costs(fee=0.0, slippage=0.0)


def frame(values, name="X"):
    idx = pd.date_range("2026-01-01", periods=len(values), freq="1min", tz="UTC")
    return pd.DataFrame({name: np.asarray(values, float)}, index=idx)


class SimulatorTest(unittest.TestCase):
    def test_signal_executes_at_next_open(self):
        o = frame([100, 100, 110, 120, 130])
        c = frame([100, 105, 115, 125, 135])
        w = frame([0, 1, 1, 0, 0])  # decided at close of bar 1 -> filled at open of bar 2 (110)
        res = simulate(o, c, w, initial_capital=1000, costs=NO_COSTS)
        # bought 1000/110 units at 110, exit decided at close 3 -> sold at open 4 (130)
        self.assertAlmostEqual(res.equity.iloc[-1], 1000 * 130 / 110)
        self.assertEqual(res.trades.iloc[0]["entry_time"], o.index[2])
        self.assertEqual(res.trades.iloc[0]["exit_time"], o.index[4])
        self.assertEqual(res.trades.iloc[0]["bars"], 2)

    def test_last_bar_signal_never_fills(self):
        o = c = frame([100, 100, 100])
        res = simulate(o, c, frame([0, 0, 1]), initial_capital=1000, costs=NO_COSTS)
        self.assertEqual(res.traded_notional, 0)
        self.assertTrue(res.trades.empty)

    def test_round_trip_costs(self):
        o = c = frame([100, 100, 100, 100])
        fee, slip = 0.001, 0.0005
        res = simulate(o, c, frame([1, 1, 0, 0]), initial_capital=1000, costs=Costs(fee, slip))
        units = 1000 / 100
        buy_px, sell_px = 100 * (1 + slip), 100 * (1 - slip)
        expected = 1000 - units * buy_px - units * buy_px * fee + units * sell_px - units * sell_px * fee
        self.assertAlmostEqual(res.equity.iloc[-1], expected)
        self.assertAlmostEqual(res.fees, units * buy_px * fee + units * sell_px * fee)
        self.assertAlmostEqual(res.slippage, 2 * units * 100 * slip)
        self.assertAlmostEqual(res.trades["pnl"].sum(), expected - 1000)

    def test_holds_units_not_weights(self):
        # weight target stays 0.5: no rebalancing trade as price doubles
        o = frame([100, 100, 200, 200])
        c = frame([100, 100, 200, 200])
        res = simulate(o, c, frame([0.5, 0.5, 0.5, 0.5]), initial_capital=1000, costs=NO_COSTS)
        self.assertAlmostEqual(res.traded_notional, 500)  # only the entry
        self.assertAlmostEqual(res.equity.iloc[-1], 500 + 5 * 200)
        self.assertAlmostEqual(res.weights.iloc[-1, 0], 1000 / 1500)

    def test_trade_pnl_reconciles_with_equity(self):
        rng = np.random.default_rng(0)
        px = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 2000)))
        o = frame(np.r_[px[0], px[:-1]])
        c = frame(px)
        w = frame((rng.random(2000) > 0.7).astype(float) * 0.2)
        res = simulate(o, c, w, initial_capital=1e5, costs=Costs(0.001, 0.0002))
        self.assertAlmostEqual(res.trades["pnl"].sum(), res.equity.iloc[-1] - 1e5, places=6)

    def test_gross_exposure_cap(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="1min", tz="UTC")
        o = c = pd.DataFrame({"A": [10.0] * 3, "B": [10.0] * 3}, index=idx)
        w = pd.DataFrame({"A": [0.6] * 3, "B": [0.6] * 3}, index=idx)
        res = simulate(o, c, w, initial_capital=1000, costs=NO_COSTS, max_gross_exposure=0.7)
        self.assertAlmostEqual(res.weights.iloc[-1].sum(), 0.7)

    def test_report_fields(self):
        o = c = frame(np.linspace(100, 110, 3000))
        res = simulate(o, c, frame([0.2] * 3000), initial_capital=1e5, costs=NO_COSTS)
        r = report(res)
        self.assertEqual(r["n_trades"], 1)
        self.assertGreater(r["total_return"], 0)
        self.assertAlmostEqual(r["max_drawdown"], 0.0)


class WalkForwardTest(unittest.TestCase):
    def test_purge_gap(self):
        for tr, va in walk_forward_splits(5000, train_bars=2000, val_bars=500, step_bars=500, purge_bars=240):
            # last train label looks 240 bars ahead; it must not reach the val block
            self.assertLess(tr[-1] + 240, va[0])


if __name__ == "__main__":
    unittest.main()

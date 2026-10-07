"""
Hourly strategies (strategies/): Model 0 equivalence with the 1m rule,
no look-ahead for every model, and the behaviour each model promises.

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
from roostoo import strategies  # noqa: E402
from roostoo.strategies import m1_reversal_trend  # noqa: E402
from roostoo.strategies.features import fill_minute_grid, hourly_bars  # noqa: E402
from roostoo.strategy import generate_targets  # noqa: E402


def minute_candles(n_days=40, seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    n = n_days * 1440
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.standard_t(3, n) * 0.0008 + drift))
    opn = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": opn, "high": np.maximum(opn, close), "low": np.minimum(opn, close),
                         "close": close, "volume": 1.0}, index=idx)


def universe(n_assets=4, n_days=40):
    bars = {f"A{i}": hourly_bars(minute_candles(n_days, seed=i, drift=(i - 1.5) * 2e-6))
            for i in range(n_assets)}
    bars["BTCUSDT"] = bars["A0"].copy()     # market proxy for the regime filters
    return bars


class FeaturesTest(unittest.TestCase):
    def test_hourly_close_is_the_hh59_close(self):
        m = minute_candles(3)
        h = hourly_bars(m)
        self.assertTrue(np.allclose(h["close"].to_numpy(), m["close"][m.index.minute == 59].to_numpy()))
        self.assertTrue(np.allclose(h["open"].to_numpy(), m["open"][m.index.minute == 0].to_numpy()))

    def test_incomplete_last_hour_is_dropped(self):
        h = hourly_bars(minute_candles(2).iloc[:-30])
        self.assertEqual(h.index[-1], pd.Timestamp("2026-01-02 22:00", tz="UTC"))

    def test_gaps_are_filled_flat(self):
        m = minute_candles(1).drop(pd.date_range("2026-01-01 05:00", periods=10, freq="1min", tz="UTC"))
        g, missing = fill_minute_grid(m)
        self.assertEqual(missing, 10)
        self.assertEqual(len(g), 1440)
        self.assertTrue((g.loc["2026-01-01 05:00":"2026-01-01 05:09", "volume"] == 0).all())


class ModelZeroEquivalenceTest(unittest.TestCase):
    def test_hourly_m0_equals_the_1m_rule_sampled_at_hh59(self):
        m = minute_candles(30, seed=7)
        one_minute = generate_targets("reversal", "X/USD", {}, None, m, {"k": 1.0})
        at_59 = one_minute[m.index.minute == 59]
        at_59.index = at_59.index.floor("1h")
        hourly = strategies.generate("m0_reversal", {"X": hourly_bars(m)}, {"k": 1.0})["X"]
        self.assertGreater(hourly.sum(), 0)
        pd.testing.assert_series_equal(hourly, at_59, check_names=False, check_freq=False)
        # and between hh:59 marks the 1m target never changes
        changes = one_minute.diff().fillna(0) != 0
        self.assertTrue((m.index[changes].minute == 59).all())


class NoLookaheadTest(unittest.TestCase):
    PARAMS = {
        "m0_reversal": {"k": 1.0},
        "m1_reversal_trend": {"k": 1.0, "trend_days": 5},
        "m2_xs_momentum": {"lookback_days": 5, "top_n": 2},
        "buy_hold": {},
        "mc_momentum": {"lookback_days": 5, "top_n": 2, "skip_days": 1, "score": "sharpe", "weighting": "inv_vol",
                        "regime": "basket", "regime_sma_days": 3, "rebalance_days": 2},
    }

    def test_targets_up_to_t_ignore_bars_after_t(self):
        bars = universe()
        cut = bars["A0"].index[600]
        for name, params in self.PARAMS.items():
            with self.subTest(model=name):
                full = strategies.generate(name, bars, params)
                altered = {s: b.copy() for s, b in bars.items()}
                for b in altered.values():
                    b.loc[b.index > cut, ["open", "high", "low", "close"]] *= 0.5
                    b.loc[b.index > cut, "vol1m_1d"] *= 3
                part = strategies.generate(name, altered, params)
                pd.testing.assert_frame_equal(full.loc[:cut], part.loc[:cut])

    def test_limits(self):
        bars = universe()
        for name, params in self.PARAMS.items():
            t = strategies.generate(name, bars, params)
            self.assertTrue(((t >= 0) & (t <= 0.2 + 1e-12)).all(axis=None), name)


class MidcapMomentumTest(unittest.TestCase):
    def test_defaults_reproduce_model_three(self):
        bars = universe(n_assets=5, n_days=60)
        p = {"lookback_days": 5, "regime_sma_days": 10}
        m3 = strategies.generate("m3_momentum_regime", bars, {"lookback_days": 5, "market_sma_days": 10})
        pd.testing.assert_frame_equal(strategies.generate("mc_momentum", bars, p), m3)

    def test_eligibility_restricts_selection_and_closes_positions(self):
        bars = universe(n_assets=5)
        idx = bars["A0"].index
        elig = pd.DataFrame(True, index=idx, columns=list(bars))
        elig[["A1", "A2"]] = False
        t = strategies.generate("mc_momentum", bars, {"lookback_days": 5, "regime": None}, eligible=elig)
        self.assertTrue((t[["A1", "A2"]] == 0).all(axis=None))
        self.assertGreater(t.drop(columns=["A1", "A2"]).to_numpy().sum(), 0)

    def test_inverse_vol_keeps_total_and_cap(self):
        bars = universe(n_assets=5)
        t = strategies.generate("mc_momentum", bars, {"lookback_days": 5, "top_n": 3, "weighting": "inv_vol",
                                                      "regime": None})
        gross = t.sum(axis=1)
        self.assertLessEqual(t.max().max(), 0.2 + 1e-12)
        self.assertLessEqual(gross.max(), 0.6 + 1e-9)


class ModelOneTest(unittest.TestCase):
    def test_entries_are_a_subset_of_model_zero_and_only_in_uptrends(self):
        bars = universe()
        m0 = strategies.generate("m0_reversal", bars, {"k": 1.0})
        m1 = strategies.generate("m1_reversal_trend", bars, {"k": 1.0, "trend_days": 5})
        self.assertTrue((m1 <= m0).all(axis=None))
        entries = (m1 > 0) & (m1.shift(fill_value=0) == 0)
        up = m1_reversal_trend.uptrend(bars, 5)
        self.assertTrue(entries.to_numpy().sum() > 0)
        self.assertTrue(up[entries].fillna(True).all(axis=None))


class ModelTwoTest(unittest.TestCase):
    def test_holds_top_n_positive_momentum_and_changes_only_at_decision_hour(self):
        bars = universe(n_assets=5)
        t = strategies.generate("m2_xs_momentum", bars, {"lookback_days": 5, "top_n": 2})
        self.assertTrue(((t > 0).sum(axis=1) <= 2).all())
        changed = t.diff().abs().sum(axis=1) > 0
        self.assertTrue((t.index[changed].hour == 23).all())
        close = pd.DataFrame({s: b["close"] for s, b in bars.items()})
        score = close / close.shift(5 * 24) - 1
        decided = t.index.hour == 23
        held = (t > 0)[decided]
        self.assertTrue(held.to_numpy().sum() > 0)
        self.assertTrue((score[decided][held] > 0).sum().sum() == held.to_numpy().sum())

    def test_vol_target_scales_down_in_steps(self):
        bars = universe(n_assets=5)
        t = strategies.generate("m2_xs_momentum", bars, {"lookback_days": 5, "top_n": 2, "vol_target": 0.5})
        nz = t.where(t > 0).stack().dropna()
        self.assertGreater(len(nz), 0)
        self.assertLess(nz.max(), 0.2)  # vol ~0.8-1.0 here, so every weight is scaled down
        self.assertTrue(np.allclose(nz / 0.025, np.round(nz / 0.025)))


if __name__ == "__main__":
    unittest.main()

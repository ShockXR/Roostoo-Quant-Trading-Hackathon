"""Mid-cap portfolio backtest: timing, accounting and no-lookahead checks."""
from pathlib import Path
import numpy as np
import pandas as pd
from roostoo_bot.backtest import portfolio
from roostoo_bot.backtest.portfolio import HourlyPanel, simulate, load_hourly_panel
from roostoo_bot.strategies import midcap
from .test_regressions import OfflineCase

H0 = pd.Timestamp("2026-04-01", tz="UTC")


def panel_from(signal, execution=None, volume=None):
    n = len(next(iter(signal.values())))
    signal = pd.DataFrame(signal, index=pd.date_range(H0, periods=n, freq="h"))
    execution = signal.copy() if execution is None else pd.DataFrame(execution, index=signal.index, columns=signal.columns)
    volume = pd.DataFrame(1e6, index=signal.index, columns=signal.columns) if volume is None else volume
    return HourlyPanel(signal, execution, volume)


def random_panel(n=600, k=6, seed=7):
    rng = np.random.default_rng(seed)
    common = rng.normal(0, .006, (n, 1))
    prices = 10 * np.exp(np.cumsum(common + rng.normal(0, .008, (n, k)), axis=0))
    volume = pd.DataFrame(np.exp(rng.normal(13, .5, (n, k))), index=pd.date_range(H0, periods=n, freq="h"),
                          columns=[f"C{j}" for j in range(k)])
    return panel_from({f"C{j}": prices[:, j] for j in range(k)},
                      execution=prices * np.exp(rng.normal(0, .001, (n, k))), volume=volume)


class PortfolioSimulationTests(OfflineCase):
    def test_flat_weights_keep_capital(self):
        panel = random_panel()
        result = simulate(panel, pd.DataFrame(0., index=panel.signal.index, columns=panel.signal.columns))
        self.assertTrue((result.equity == 100_000.).all())
        self.assertEqual(result.fills.sum(), 0)

    def test_fill_at_delayed_price_and_mark_at_signal_price(self):
        panel = panel_from({"A": [100., 110., 121.]}, execution={"A": [101., 111., 122.]})
        weights = pd.DataFrame({"A": [1., np.nan, np.nan]}, index=panel.signal.index)
        result = simulate(panel, weights, cost_per_side=0., liquidate=False)
        units = 100_000. / 101.  # bought at the delayed price, not the signal price
        self.assertEqual(result.equity.iloc[0], 100_000.)  # marked before the first trade
        self.assertAlmostEqual(result.equity.iloc[1], units * 110.)
        self.assertAlmostEqual(result.equity.iloc[2], units * 121.)

    def test_costs_charged_on_entry_and_final_liquidation(self):
        panel = panel_from({"A": [100.] * 3})
        weights = pd.DataFrame({"A": [.5, np.nan, np.nan]}, index=panel.signal.index)
        result = simulate(panel, weights, cost_per_side=.001)
        self.assertAlmostEqual(result.equity.iloc[1], 100_000. - 50_000. * .001)
        self.assertAlmostEqual(result.equity.iloc[-1], 100_000. - 50. - 50.)  # 500 units worth 50k exit at 0.1%

    def test_small_rebalances_skipped_but_exits_always_trade(self):
        panel = panel_from({"A": [100.] * 4})
        weights = pd.DataFrame({"A": [.5, .505, 0., np.nan]}, index=panel.signal.index)
        result = simulate(panel, weights, cost_per_side=0., band=.01)
        self.assertEqual(list(result.fills), [1, 0, 1, 0])
        self.assertAlmostEqual(result.gross.iloc[-1], 0.)

    def test_gross_above_one_rejected(self):
        panel = panel_from({"A": [100.] * 2, "B": [100.] * 2})
        with self.assertRaises(ValueError):
            simulate(panel, pd.DataFrame({"A": [.6, .6], "B": [.6, .6]}, index=panel.signal.index))

    def test_drawdown_schedule_cuts_at_once_and_restores_only_at_rebalance(self):
        prices = [100., 100., 96., 94., 99., 100., 100.]
        panel = panel_from({"A": prices})
        weights = pd.DataFrame({"A": [1., np.nan, np.nan, np.nan, np.nan, 1., np.nan]}, index=panel.signal.index)
        result = simulate(panel, weights, cost_per_side=0., liquidate=False, drawdown_schedule=((.03, .5), (.05, .25)))
        # -4% halves at once, -5% quarters; recovery to -3.5% restores 0.5 only at the next rebalance.
        self.assertEqual(list(result.scale), [1., 1., .5, .25, .25, .5, .5])
        self.assertAlmostEqual(result.equity.iloc[3], 100_000. - 1000. * 4 - 500. * 2)  # 1000 units, halved at 96
        self.assertEqual(list(result.fills), [1, 0, 1, 1, 0, 1, 0])

    def test_drawdown_scale_levels(self):
        schedule = ((.03, .5), (.05, .25))
        self.assertEqual(midcap.drawdown_scale(-.01, schedule), 1.)
        self.assertEqual(midcap.drawdown_scale(-.03, schedule), .5)
        self.assertEqual(midcap.drawdown_scale(-.2, schedule), .25)
        self.assertEqual(midcap.drawdown_scale(-.2, None), 1.)
        with self.assertRaises(ValueError):
            midcap.drawdown_scale(-.2, ((.05, 0.),))

    def test_short_profits_when_price_falls(self):
        panel = panel_from({"A": [100., 90.]})
        weights = pd.DataFrame({"A": [-.5, np.nan]}, index=panel.signal.index)
        result = simulate(panel, weights, cost_per_side=0., liquidate=False)
        self.assertAlmostEqual(result.equity.iloc[1], 105_000.)


class PanelLoadingTests(OfflineCase):
    def test_hour_stamps_use_completed_candles_and_delayed_execution(self):
        minutes = pd.date_range(H0, periods=180, freq="min")
        frame = pd.DataFrame({"open_time": minutes, "close": np.arange(180, dtype=float) + 1,
                              "quote_asset_volume": np.ones(180)})
        frame.to_parquet(Path(self.temp.name) / "AUSDT_1m.parquet")
        panel = load_hourly_panel(["AUSDT"], self.temp.name, exec_delay_minutes=5)
        hour = H0 + pd.Timedelta(hours=1)
        # Candle opened 00:59 closes at 01:00 -> close value 60; candle opened 01:04 -> 65.
        self.assertEqual(panel.signal.loc[hour, "AUSDT"], 60.)
        self.assertEqual(panel.execution.loc[hour, "AUSDT"], 65.)
        self.assertEqual(panel.volume.loc[hour, "AUSDT"], 60.)
        self.assertNotIn(minutes[-1] + pd.Timedelta(minutes=1), panel.signal.index)  # no exec price after data ends


class NoLookaheadTests(OfflineCase):
    SPECS = [
        midcap.Spec("m", "momentum", {"lookback": 24, "top_k": 2}, every=4),
        midcap.Spec("ls", "momentum", {"lookback": 24, "top_k": 2, "long_short": True}, every=4),
        midcap.Spec("r", "reversal", {"lookback": 24, "top_k": 2}, every=4, gate={}),
        midcap.Spec("t", "trend", {"ma_hours": 48}, every=4, target_daily_vol=None),
        midcap.Spec("b", "breakout", {"channel_hours": 24}, every=1),
        midcap.Spec("bl", "blend", {"parts": [{"signal": "momentum", "params": {"lookback": 24, "top_k": 2}},
                                              {"signal": "momentum", "params": {"lookback": 72, "top_k": 2}}]},
                    every=24, target_daily_vol=.01),
    ]

    def test_weights_at_hour_do_not_change_when_future_data_is_removed(self):
        panel = random_panel()
        for spec in self.SPECS:
            full = midcap.build_weights(panel, spec)
            for cut in (300, 451, 599):
                part = HourlyPanel(panel.signal.iloc[:cut], panel.execution.iloc[:cut], panel.volume.iloc[:cut])
                truncated = midcap.build_weights(part, spec)
                pd.testing.assert_frame_equal(truncated, full.iloc[:cut], obj=f"{spec.name} cut={cut}")

    def test_built_weights_respect_caps_and_rebalance_schedule(self):
        panel = random_panel()
        weights = midcap.build_weights(panel, midcap.Spec("m", "momentum", {"lookback": 24, "top_k": 2}, every=8))
        live = weights.dropna(how="all")
        self.assertTrue((live.index.hour % 8 == 0).all())
        self.assertLessEqual(live.abs().max().max(), .2 + 1e-12)
        self.assertLessEqual(live.abs().sum(axis=1).max(), 1 + 1e-12)

    def test_crash_gate_never_zero(self):
        gate = midcap.crash_gate(random_panel())
        self.assertGreater(gate.min(), 0)

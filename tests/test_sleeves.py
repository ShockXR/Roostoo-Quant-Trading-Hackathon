"""Three-sleeve book: signals, no look-ahead, live/backtest parity and engine behaviour (offline)."""
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from roostoo_bot import config, sleeves_live
from roostoo_bot.backtest.portfolio import HourlyPanel
from roostoo_bot.data import binance_live
from roostoo_bot.execution import trader
from roostoo_bot.execution.broker import LiveBroker
from roostoo_bot.execution.portfolio import PaperBroker, Quote
from roostoo_bot.strategies import sleeves
from .fixtures import metadata
from .test_midcap import H0, panel_from
from .test_midcap_live import klines_session
from .test_regressions import OfflineCase

HOUR = 3_600_000
DAY = 24 * HOUR
DAY0 = 1_791_072_000_000  # 2026-10-04 00:00 UTC
LARGE = ["BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD"]
MID = ["UNI/USD", "AAVE/USD", "ENA/USD", "ARB/USD", "SEI/USD", "ONDO/USD"]
SMALL = ["PEPE/USD", "WIF/USD", "BONK/USD"]
PAIRS = {"large": LARGE, "mid": MID, "small": SMALL}


def trending_panel(columns, n, drift, seed=3, noise=.004):
    """Geometric random walks with a per-column hourly drift."""
    rng = np.random.default_rng(seed)
    steps = np.asarray(drift)[None, :] + rng.normal(0, noise, (n, len(columns)))
    prices = 10 * np.exp(np.cumsum(steps, axis=0))
    return panel_from({c: prices[:, j] for j, c in enumerate(columns)})


def small_history():
    """Shorter lookbacks so tests run on a few hundred hours."""
    return patch.multiple(config, LARGE_LOOKBACK_HOURS=72, REGIME_SMA_HOURS=200)


class SignalTests(OfflineCase):
    def test_regime_momentum_takes_top_three_positive_equal_weight(self):
        panel = panel_from({"A": [1., 1.5], "B": [1., 1.2], "C": [1., 1.1], "D": [1., 1.3], "E": [1., .9]})
        w = sleeves.regime_momentum(panel, lookback=1, top_k=3).iloc[-1]
        self.assertEqual(set(w[w > 0].index), {"A", "B", "D"})
        self.assertTrue(np.allclose(w[w > 0], 1 / 3))
        falling = panel_from({"A": [1., .9], "B": [1., .8]})
        self.assertEqual(sleeves.regime_momentum(falling, lookback=1, top_k=3).iloc[-1].sum(), 0.)

    def test_confirmed_momentum_needs_both_horizons_positive(self):
        # A: up over 3h but down over the last hour -> excluded. B: up on both.
        panel = panel_from({"A": [1., 1.3, 1.4, 1.35], "B": [1., 1.05, 1.08, 1.1], "C": [1., .9, .8, .7]})
        w = sleeves.confirmed_momentum(panel, lookback=3, confirm=1, top_k=2).iloc[-1]
        self.assertEqual(w.to_dict(), {"A": 0., "B": 1., "C": 0.})

    def test_large_sleeve_is_cash_when_btc_below_average_and_mid_is_halved(self):
        with small_history():
            n = 400
            up = trending_panel(LARGE, n, [.001] * 4)
            regime_on = pd.Series(True, index=up.signal.index)
            regime_off = pd.Series(False, index=up.signal.index)
            spec = sleeves.specs(config)
            rows = [n - 1]
            on = sleeves.sleeve_weights(up, "large", spec["large"], regime_on, config, rows).iloc[-1]
            off = sleeves.sleeve_weights(up, "large", spec["large"], regime_off, config, rows).iloc[-1]
            self.assertGreater(on.sum(), 0)
            self.assertEqual(off.sum(), 0)
            self.assertLessEqual(on.max(), spec["large"].max_weight + 1e-12)
            self.assertLessEqual(on.sum(), spec["large"].max_gross + 1e-12)
            mid = trending_panel(MID, n, [.001, .0008, .0006, .0004, .0002, .0001])
            mid_on = sleeves.sleeve_weights(mid, "mid", spec["mid"], regime_on, config, rows).iloc[-1]
            mid_off = sleeves.sleeve_weights(mid, "mid", spec["mid"], regime_off, config, rows).iloc[-1]
            self.assertGreater(mid_on.sum(), 0)
            pd.testing.assert_series_equal(mid_off, mid_on * spec["mid"].regime_off_scale)

    def test_no_lookahead_in_backtest_weights(self):
        with small_history():
            columns = LARGE + MID + SMALL
            panel = trending_panel(columns, 500, np.linspace(-.0005, .001, len(columns)), noise=.01)
            full = sleeves.backtest_weights(panel, config, PAIRS)
            cut = 430
            short = HourlyPanel(panel.signal.iloc[:cut], panel.execution.iloc[:cut], panel.volume.iloc[:cut])
            truncated = sleeves.backtest_weights(short, config, PAIRS)
            pd.testing.assert_frame_equal(full.iloc[:cut], truncated)
            self.assertTrue((full.dropna(how="all").sum(axis=1) <= .70 + 1e-9).all())

    def test_live_weights_match_backtest_rows(self):
        with small_history():
            columns = LARGE + MID + SMALL
            panel = trending_panel(columns, 700, np.linspace(-.0003, .0012, len(columns)), noise=.008)
            backtest = sleeves.backtest_weights(panel, config, PAIRS)
            session = klines_session(panel, columns)
            hours = {"large": 600, "mid": 400, "small": 400}
            source = sleeves_live.binance_sleeve_weights(PAIRS, hours, session=session)
            for hour in backtest.dropna(how="all").index[-2:]:
                live, regime = source(int(hour.value // 1_000_000))
                for sleeve, weights in live.items():
                    for pair, w in weights.items():
                        self.assertAlmostEqual(w, backtest.loc[hour, pair], places=12, msg=f"{sleeve} {pair} {hour}")

    def test_tolerant_panel_drops_missing_pairs_unless_held(self):
        panel = trending_panel(["AAA/USD", "BBB/USD"], 300, [0., 0.])
        session = klines_session(panel, ["AAA/USD", "BBB/USD"])
        end = int(panel.signal.index[-1].value // 1_000_000)
        # CCC has no klines: the fake session raises ValueError for an unknown symbol.
        def get(url, params, timeout):
            if params["symbol"] == "CCCUSDT":
                raise ValueError("unknown symbol")
            return session.get(url, params, timeout)
        fake = type("S", (), {"get": staticmethod(get)})
        out = binance_live.tolerant_panel(["AAA/USD", "BBB/USD", "CCC/USD"], end, 200, session=fake)
        self.assertEqual(list(out.signal.columns), ["AAA/USD", "BBB/USD"])
        self.assertEqual(len(out.signal), 200)
        with self.assertRaises(ValueError):
            binance_live.tolerant_panel(["AAA/USD", "CCC/USD"], end, 200, required={"CCC/USD"}, session=fake)

    def test_history_pages_past_the_kline_limit(self):
        panel = trending_panel(["AAA/USD"], 2600, [0.])
        session = klines_session(panel, ["AAA/USD"])
        end = int(panel.signal.index[-1].value // 1_000_000)
        out = binance_live.tolerant_panel(["AAA/USD"], end, 2500, session=session)
        self.assertEqual(len(out.signal), 2500)
        np.testing.assert_allclose(out.signal["AAA/USD"].to_numpy(), panel.signal["AAA/USD"].to_numpy()[-2500:])


class SleeveEngineTests(OfflineCase):
    ALL = LARGE + MID + SMALL

    def setUp(self):
        super().setUp()
        self.weights = {"large": {"BTC/USD": .1, "ETH/USD": .1}, "mid": {"UNI/USD": .05}, "small": {"PEPE/USD": .02}}
        self.regime = True

    def source(self, hour_ms, held=()):
        return {s: dict(w) for s, w in self.weights.items()}, self.regime

    def engine(self, broker=None, extra_pairs=()):
        return sleeves_live.SleeveEngine(broker or PaperBroker(100_000.), metadata(self.ALL + list(extra_pairs)),
                                         self.source, PAIRS)

    def quotes(self, now_ms, prices=None, extra=()):
        prices = prices or {}
        return {p: Quote(prices.get(p, 10.), prices.get(p, 10.) * .9999, prices.get(p, 10.) * 1.0001, now_ms - 1000)
                for p in self.ALL + list(extra)}

    def value(self, engine, pair, price=10.):
        return engine.broker.state.longs.get(pair, 0.) * price

    def test_rebalance_buys_each_sleeve_to_target(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        events = engine.step(self.quotes(now), now)
        self.assertEqual({e["pair"] for e in events}, {"BTC/USD", "ETH/USD", "UNI/USD", "PEPE/USD"})
        self.assertAlmostEqual(self.value(engine, "BTC/USD") / 100_000, .1, delta=.003)
        self.assertAlmostEqual(self.value(engine, "PEPE/USD") / 100_000, .02, delta=.001)
        self.assertTrue(engine.rebalance.done)
        self.assertIn("PEPE/USD", engine.rebalance.entries)

    def test_leftover_coin_outside_every_sleeve_is_sold(self):
        broker = PaperBroker(90_000.)
        broker.state.longs["CFXOLD/USD"] = 1000.
        engine = self.engine(broker, extra_pairs=["CFXOLD/USD"])
        now = DAY0 + DAY + 3 * 60_000
        events = engine.step(self.quotes(now, extra=["CFXOLD/USD"]), now)
        self.assertIn(("CFXOLD/USD", "SELL"), {(e["pair"], e["action"]) for e in events})
        self.assertNotIn("CFXOLD/USD", engine.broker.state.longs)

    def test_untradable_leftover_does_not_block_the_book(self):
        broker = PaperBroker(90_000.)
        broker.state.longs["DEAD/USD"] = 5.
        meta = metadata(self.ALL + ["DEAD/USD"])
        meta["DEAD/USD"]["CanTrade"] = False
        engine = sleeves_live.SleeveEngine(broker, meta, self.source, PAIRS)
        now = DAY0 + DAY + 3 * 60_000
        events = engine.step(self.quotes(now, extra=["DEAD/USD"]), now)
        self.assertIn("BTC/USD", {e["pair"] for e in events})
        self.assertEqual(engine.broker.state.longs["DEAD/USD"], 5.)

    def test_small_cap_stop_sells_and_bars_reentry(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        later = now + 60_000
        events = engine.step(self.quotes(later, {"PEPE/USD": 8.9}), later)
        self.assertEqual([(e["pair"], e["action"]) for e in events], [("PEPE/USD", "SELL")])
        self.assertNotIn("PEPE/USD", engine.broker.state.longs)
        self.assertGreater(engine.rebalance.blocked["PEPE/USD"], later)
        # Next day's signal still wants PEPE, but the lockout holds.
        next_day = DAY0 + 2 * DAY + 3 * 60_000
        events = engine.step(self.quotes(next_day), next_day)
        self.assertNotIn("PEPE/USD", {e["pair"] for e in events})
        # After the lockout it can be bought again.
        free = DAY0 + 5 * DAY + 3 * 60_000
        events = engine.step(self.quotes(free), free)
        self.assertIn(("PEPE/USD", "BUY"), {(e["pair"], e["action"]) for e in events})

    def test_wide_spread_blocks_new_small_cap_entry(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        quotes = self.quotes(now)
        quotes["PEPE/USD"] = Quote(10., 9.98, 10.02, now - 1000)  # 0.4% spread
        events = engine.step(quotes, now)
        self.assertNotIn("PEPE/USD", {e["pair"] for e in events})

    def test_push_mode_scales_large_and_mid_once_equity_is_above_start(self):
        engine = self.engine(PaperBroker(101_000.))
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        self.assertTrue(engine.rebalance.push)
        self.assertAlmostEqual(self.value(engine, "BTC/USD") / 101_000, .1 * config.PUSH_MULTIPLIER["large"], delta=.004)
        self.assertAlmostEqual(self.value(engine, "UNI/USD") / 101_000, .05 * config.PUSH_MULTIPLIER["mid"], delta=.003)
        self.assertAlmostEqual(self.value(engine, "PEPE/USD") / 101_000, .02, delta=.001)

    def test_recovery_mode_below_start(self):
        engine = self.engine(PaperBroker(97_500.))
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        self.assertFalse(engine.rebalance.push)
        self.assertAlmostEqual(self.value(engine, "BTC/USD") / 97_500, .1, delta=.003)

    def test_activity_trade_once_after_the_activity_hour_when_nothing_filled(self):
        engine = self.engine(PaperBroker(97_500.))  # stays below the push threshold
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        # Next day: signal unchanged, prices drift a little, so the rebalance band skips every trade.
        drift = {"BTC/USD": 10.3}
        morning = DAY0 + 2 * DAY + 3 * 60_000
        self.assertEqual(engine.step(self.quotes(morning, drift), morning), [])
        evening = DAY0 + 2 * DAY + config.ACTIVITY_HOUR_UTC * HOUR + 60_000
        events = engine.step(self.quotes(evening, drift), evening)
        self.assertEqual([(e["pair"], e["action"]) for e in events], [("BTC/USD", "SELL")])
        later = evening + 60_000
        self.assertEqual(engine.step(self.quotes(later, drift), later), [])

    def test_drawdown_schedule_still_cuts_at_once(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        crash = {p: 6. for p in LARGE + MID}
        crash["PEPE/USD"] = 9.5
        later = now + 60_000
        events = engine.step(self.quotes(later, crash), later)
        self.assertTrue(events and all(e["action"] == "SELL" for e in events))
        self.assertLess(engine.rebalance.scale, 1.)

    def test_state_round_trip(self):
        path = f"{self.temp.name}/sleeves_state.json"
        engine = self.engine()
        engine.state_path = path
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        restored = sleeves_live.restore_sleeve_engine(PaperBroker(), metadata(self.ALL), self.source, PAIRS,
                                                      path, None, None)
        self.assertEqual(restored.rebalance, engine.rebalance)
        self.assertEqual(restored.broker.state.longs, engine.broker.state.longs)
        later = now + 60_000
        self.assertEqual(restored.step(self.quotes(later), later), [])


class WalletLayoutTests(OfflineCase):
    def test_spot_wallet_with_frozen_is_read(self):
        quotes = {"BTC/USD": Quote(100., 99.9, 100.1, 0)}
        layouts = [{"SpotWallet": {"USD": {"Free": 500., "Frozen": 0.}, "BTC": {"Free": 2., "Frozen": 0.}}},
                   {"Wallet": {"USD": {"Free": 500., "Lock": 0.}, "BTC": {"Free": 2., "Lock": 0.}}}]
        for layout in layouts:
            with patch.object(config, "LIVE_TRADING_ENABLED", True), \
                    patch.object(trader, "get_balance", return_value={"Success": True, **layout}), \
                    patch.object(trader, "get_pending_count", return_value={"Success": True, "TotalPending": 0}), \
                    patch.object(trader, "get_short_positions", return_value={"Success": True, "Positions": []}):
                snapshot = LiveBroker().snapshot(quotes)
            self.assertEqual(snapshot.cash, 500.)
            self.assertEqual(snapshot.longs, {"BTC/USD": 2.})

    def test_frozen_balance_blocks_trading(self):
        quotes = {"BTC/USD": Quote(100., 99.9, 100.1, 0)}
        wallet = {"Success": True, "SpotWallet": {"USD": {"Free": 500., "Frozen": 10.}}}
        with patch.object(config, "LIVE_TRADING_ENABLED", True), \
                patch.object(trader, "get_balance", return_value=wallet), \
                patch.object(trader, "get_pending_count", return_value={"Success": True, "TotalPending": 0}), \
                patch.object(trader, "get_short_positions", return_value={"Success": True, "Positions": []}):
            with self.assertRaises(Exception):
                LiveBroker().snapshot(quotes)


class TradeReviewTests(OfflineCase):
    def test_round_trips_reasons_and_post_exit_moves(self):
        from roostoo_bot.scripts import review_live_trades as review
        trades = Path(self.temp.name) / "trades_log.csv"
        trades.write_text("timestamp,pair,side,quantity,price,execution_type,order_id\n"
                          "2026-10-06T10:00:00,BTC/USD,BUY,1.0,100,LIVE,11\n"
                          "2026-10-07T10:00:30,BTC/USD,SELL,1.0,90,LIVE,12\n"
                          "2026-10-07T12:00:00,ENA/USD,BUY,10,MARKET,LIVE,\n")
        log = Path(self.temp.name) / "bot.log"
        log.write_text("2026-10-07 10:00:25,001 [WARNING] main: \U0001F6A8 [LARGECAP RULE 1: STOP-LOSS] BTC/USD: Price $90 "
                       "dropped below Stop-Loss $91 (3x Vol). Executing SELL.\n")
        trips = review.round_trips(review.load_trades(trades), review.load_exit_reasons(log))
        self.assertEqual(list(trips.reason), ["stop-loss", "open"])
        self.assertFalse(trips.confirmed.iloc[1])
        start = pd.Timestamp("2026-10-05", tz="UTC")

        def klines(symbol, end_ms, limit):
            price = {"BTCUSDT": 95., "ENAUSDT": 2.}[symbol]
            hours = range(int(start.value // 1_000_000), end_ms, HOUR)
            return [[h, 0, 0, 0, str(price), 0, h + HOUR - 1, "1"] for h in hours][-limit:]
        with patch.object(review, "fetch_klines", side_effect=klines):
            rich = review.enrich(trips, pd.Timestamp("2026-10-10 12:00", tz="UTC"))
        btc = rich.iloc[0]
        self.assertAlmostEqual(btc.pnl_pct, -10.2, places=6)
        self.assertAlmostEqual(btc.post_24h_pct, (95 / 90 - 1) * 100)
        self.assertEqual(rich.iloc[1].entry_price, 2.)  # MARKET row priced from Binance
        text = review.report(rich, pd.Timestamp("2026-10-10 12:00", tz="UTC"))
        self.assertIn("| large | stop-loss | 1 |", text)


class SleevesBacktestScriptTests(OfflineCase):
    def test_runs_end_to_end_on_synthetic_history(self):
        import sys
        from roostoo_bot.scripts import sleeves_backtest as script
        rng = np.random.default_rng(11)

        def download(pair, start, end):
            index = pd.date_range(start + pd.Timedelta(hours=1), end, freq="h")
            drift = .0004 if pair == "BTC/USD" else rng.normal(0, .0003)
            close = 10 * np.exp(np.cumsum(drift + rng.normal(0, .006, len(index))))
            return pd.DataFrame({"close": close, "volume": 1e6}, index=index)
        out = Path(self.temp.name) / "report.md"
        argv = ["x", "--start", "2026-08-01", "--end", "2026-10-09", "--out", str(out)]
        with patch.object(script, "download", side_effect=download), patch.object(sys, "argv", argv), \
                patch("builtins.print"):
            script.main()
        text = out.read_text()
        for name in ("sleeves (recovery sizing", "large sleeve alone", "previous blend_v10", "since Oct 04"):
            self.assertIn(name, text)

"""Mid-cap live engine: backtest parity, rebalance schedule, de-risk and restarts (offline)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import requests
from roostoo_bot import config, midcap_live
from roostoo_bot.data import binance_live
from roostoo_bot.execution.portfolio import PaperBroker, Quote
from roostoo_bot.main import TradingEngine
from roostoo_bot.strategies import midcap
from .fixtures import metadata
from .test_midcap import random_panel
from .test_regressions import OfflineCase

DAY = 86_400_000
HOUR = 3_600_000
DAY0 = 1_791_072_000_000  # 2026-10-04 00:00 UTC
PAIRS = ["AAA/USD", "BBB/USD", "CCC/USD"]


def klines_session(panel, pairs):
    """Fake Binance session serving the panel's signal prices as 1h klines."""
    def get(url, params, timeout):
        column = pairs[[binance_live.binance_symbol(p) for p in pairs].index(params["symbol"])]
        series = panel.signal[column]
        opens = series.index.as_unit("ns").asi8 // 1_000_000 - HOUR
        rows = [[int(o), 0, 0, 0, str(c), 0, int(o) + HOUR - 1, str(v)]
                for o, c, v in zip(opens, series, panel.volume[column]) if o <= params["endTime"]]
        response = MagicMock()
        response.json.return_value = rows[-params["limit"]:]
        response.raise_for_status.return_value = None
        return response
    return MagicMock(get=get)


class BinanceLiveTests(OfflineCase):
    def test_live_weights_match_backtest_weights_at_rebalance_hour(self):
        panel = random_panel(n=500, k=3)
        panel.signal.columns = panel.execution.columns = panel.volume.columns = PAIRS
        spec = midcap.Spec("bl", "blend", {"parts": [{"signal": "momentum", "params": {"lookback": 24, "top_k": 1}},
                                                     {"signal": "momentum", "params": {"lookback": 168, "top_k": 1}}]},
                           every=24, target_daily_vol=.015, max_gross=.7)
        backtest = midcap.build_weights(panel, spec)
        hours = backtest.dropna(how="all").index[-3:]
        session = klines_session(panel, PAIRS)
        for hour in hours:
            hour_ms = int(hour.value // 1_000_000)
            live_panel = binance_live.hourly_panel(PAIRS, hour_ms, hours=400, session=session)
            row = midcap.build_weights(live_panel, spec).iloc[-1]
            pd.testing.assert_series_equal(row, backtest.loc[hour], check_names=False)
            with patch.object(binance_live.requests, "get", session.get):
                live = midcap_live.binance_weights(PAIRS, spec)(hour_ms)
            self.assertEqual(live, {p: float(backtest.loc[hour, p]) for p in PAIRS})

    def test_latest_close_missing_or_gap_raises(self):
        panel = random_panel(n=300, k=3)
        panel.signal.columns = panel.execution.columns = panel.volume.columns = PAIRS
        session = klines_session(panel, PAIRS)
        last = int(panel.signal.index[-1].value // 1_000_000)
        with self.assertRaises(ValueError):
            binance_live.hourly_panel(PAIRS, last + HOUR, hours=100, session=session)
        with self.assertRaises(ValueError):
            binance_live.hourly_panel(PAIRS, last + 1, hours=100, session=session)

    def test_falls_back_to_second_kline_host(self):
        good = MagicMock()
        good.json.return_value = [[0, 0, 0, 0, "1", 0, HOUR - 1, "5"]]
        session = MagicMock()
        session.get.side_effect = [requests.exceptions.ConnectionError("blocked"), good]
        rows = binance_live.fetch_klines("AAAUSDT", HOUR, 10, session)
        self.assertEqual(len(rows), 1)
        self.assertEqual(session.get.call_args_list[1].args[0], config.BINANCE_KLINE_URLS[1])


class MidcapEngineTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.weights = {"AAA/USD": .2, "BBB/USD": .1, "CCC/USD": 0.}
        self.calls = []

    def source(self, hour_ms):
        self.calls.append(hour_ms)
        return dict(self.weights)

    def engine(self, broker=None, state_path=None, **kwargs):
        return midcap_live.MidcapEngine(broker or PaperBroker(100_000.), metadata(PAIRS), self.source, pairs=PAIRS,
                                        state_path=state_path, **kwargs)

    def quotes(self, now_ms, prices=(10., 20., 30.)):
        return {p: Quote(v, v * .9999, v * 1.0001, now_ms - 1_000) for p, v in zip(PAIRS, prices)}

    def holdings_value(self, engine, quotes):
        return {p: q * quotes[p].price for p, q in engine.broker.state.longs.items()}

    def test_rebalances_once_after_the_daily_close_then_holds(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        events = engine.step(self.quotes(now), now)
        self.assertEqual(self.calls, [DAY0 + DAY])
        self.assertEqual({e["pair"] for e in events}, {"AAA/USD", "BBB/USD"})
        value = self.holdings_value(engine, self.quotes(now))
        self.assertAlmostEqual(value["AAA/USD"] / 100_000., .2, delta=.005)
        later = now + 60_000
        self.assertEqual(engine.step(self.quotes(later), later), [])
        self.assertTrue(engine.rebalance.done)
        self.assertEqual(len(self.calls), 1)

    def test_waits_for_kline_close_delay(self):
        engine = self.engine()
        engine.rebalance.hour_ms = DAY0
        now = DAY0 + DAY + 60_000  # inside the 2-minute delay: still yesterday's rebalance
        self.assertEqual(engine.step(self.quotes(now), now), [])
        self.assertEqual(self.calls, [])

    def test_signal_failure_holds_and_retries_next_cycle(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        with patch.object(engine, "signal_source", side_effect=ValueError("not ready")):
            with self.assertRaises(ValueError):
                engine.step(self.quotes(now), now)
        self.assertEqual(engine.broker.state.longs, {})
        self.assertTrue(engine.step(self.quotes(now + 60_000), now + 60_000))

    def test_drawdown_cuts_size_at_once_and_never_latches_halt(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        held = dict(engine.broker.state.longs)
        crash = (6.5, 13., 30.)  # about -10% equity on 30% gross with these moves
        later = now + 60_000
        events = engine.step(self.quotes(later, crash), later)
        self.assertTrue(events and all(e["action"] == "SELL" for e in events))
        self.assertLess(engine.rebalance.scale, 1.)
        self.assertFalse(engine.state.halted)
        for pair, quantity in engine.broker.state.longs.items():
            self.assertLess(quantity, held[pair])

    def test_size_returns_only_at_next_rebalance(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        engine.step(self.quotes(now + 60_000, (6.5, 13., 30.)), now + 60_000)
        cut = engine.rebalance.scale
        recovered = now + 2 * 60_000
        self.assertEqual(engine.step(self.quotes(recovered), recovered), [])
        self.assertEqual(engine.rebalance.scale, cut)
        next_day = DAY0 + 2 * DAY + 3 * 60_000
        engine.step(self.quotes(next_day, (10., 20., 30.)), next_day)
        self.assertEqual(engine.rebalance.scale, cut)  # still in drawdown: stays reduced
        day_after = DAY0 + 3 * DAY + 3 * 60_000
        engine.step(self.quotes(day_after, (20., 40., 30.)), day_after)  # new equity high
        self.assertEqual(engine.rebalance.scale, 1.)

    def test_restart_restores_state_and_catches_up_missed_rebalance(self):
        path = Path(self.temp.name) / "midcap.json"
        engine = self.engine(state_path=str(path))
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        restored = midcap_live.restore_midcap_engine(PaperBroker(), metadata(PAIRS), self.source, str(path), None, None)
        restored.pairs = PAIRS
        self.assertEqual(restored.broker.state.longs, engine.broker.state.longs)
        self.assertEqual(restored.rebalance.hour_ms, DAY0 + DAY)
        self.assertEqual(restored.step(self.quotes(now + 60_000), now + 60_000), [])
        missed = DAY0 + 2 * DAY + 5 * HOUR  # restarted hours after the next daily close
        restored.step(self.quotes(missed), missed)
        self.assertEqual(self.calls[-1], DAY0 + 2 * DAY)

    def test_gives_up_on_a_rebalance_that_cannot_finish(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        with patch.object(engine, "submit_orders", return_value=[]) as submit:
            for minute in range(config.MIDCAP_MAX_REBALANCE_CYCLES + 2):
                engine.step(self.quotes(now + minute * 60_000), now + minute * 60_000)
        self.assertEqual(submit.call_count, config.MIDCAP_MAX_REBALANCE_CYCLES)
        self.assertTrue(engine.rebalance.done)

    def test_missing_or_stale_quotes_stop_the_cycle(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        quotes = self.quotes(now)
        quotes.pop("CCC/USD")
        with self.assertRaises(ValueError):
            engine.step(quotes, now)
        with self.assertRaises(ValueError):
            engine.step(self.quotes(now - 10 * 60_000), now)
        self.assertEqual(engine.broker.state.longs, {})

    def test_server_clock_slightly_ahead_of_ours_is_tolerated_but_far_ahead_is_not(self):
        engine = self.engine()
        now = DAY0 + DAY + 3 * 60_000
        ahead = {p: Quote(v, v * .9999, v * 1.0001, now + 3_000) for p, v in zip(PAIRS, (10., 20., 30.))}
        self.assertTrue(engine.step(ahead, now))
        far = {p: Quote(v, v * .9999, v * 1.0001, now + 5 * 60_000) for p, v in zip(PAIRS, (10., 20., 30.))}
        with self.assertRaises(ValueError):
            self.engine().step(far, now)

    def test_state_file_is_written_atomically_and_round_trips(self):
        path = Path(self.temp.name) / "midcap.json"
        engine = self.engine(state_path=str(path))
        now = DAY0 + DAY + 3 * 60_000
        engine.step(self.quotes(now), now)
        self.assertFalse(list(path.parent.glob("midcap.json.tmp")))
        saved = json.loads(path.read_text())
        self.assertEqual(saved["rebalance"]["hour_ms"], DAY0 + DAY)
        self.assertIn("portfolio", saved)
        with patch("roostoo_bot.main.Path.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                engine.persist()
        self.assertEqual(json.loads(path.read_text()), saved)  # the old file survives a failed write

    def test_hourly_order_limit_is_wide_enough_for_a_rebalance_plus_a_de_risk(self):
        self.assertGreaterEqual(config.MIDCAP_MAX_TRADES_PER_HOUR, 30)
        self.assertEqual(midcap_live.MidcapEngine.trades_per_hour, config.MIDCAP_MAX_TRADES_PER_HOUR)
        self.assertIsNone(TradingEngine.trades_per_hour)

    def test_rebalance_hour_rounding(self):
        self.assertEqual(midcap_live.latest_rebalance_hour(DAY0 + DAY + 119_999, 24, 120_000), DAY0)
        self.assertEqual(midcap_live.latest_rebalance_hour(DAY0 + DAY + 120_000, 24, 120_000), DAY0 + DAY)

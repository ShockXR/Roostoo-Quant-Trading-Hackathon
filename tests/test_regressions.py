"""Regressions for research leakage, accounting and pre-live behavior."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import requests
from roostoo_bot import config, costs, sizing
from roostoo_bot.backtest import engine, ablation
from roostoo_bot.data import store
from roostoo_bot.data.validation import clean_ticks, validate_recent_history
from roostoo_bot.execution import trader
from roostoo_bot.execution.auth import sign
from roostoo_bot.execution.broker import LiveBroker, UnknownOrderState
from roostoo_bot.execution.portfolio import PaperBroker, ExecutionState, Short, Snapshot, Quote, Order, plan_orders
from roostoo_bot.features import momentum, mean_reversion, cross_sectional
from roostoo_bot.features.build_features import build_price_panel, build_features_for_pair, assert_no_lookahead
from roostoo_bot.main import TradingEngine, restore_engine, run_once
from roostoo_bot.model import targets, train
from roostoo_bot.strategy import model_target
from .fixtures import EPOCH, ticks, scenario, metadata, bundle

ROOT = Path(__file__).resolve().parents[1]


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="roostoo-fixture-")
        self.addCleanup(self.temp.cleanup)
        for operation in ("connect", "connect_ex"):
            guard = patch.object(socket.socket, operation, side_effect=AssertionError("network forbidden in fixtures"))
            guard.start()
            self.addCleanup(guard.stop)
        cfg = patch.object(config, "STORE_DIR", self.temp.name)
        cfg.start()
        self.addCleanup(cfg.stop)
        self.raw = {"BTC/USD": scenario(), "ETH/USD": scenario("trend")}
        self.now = int(self.raw["BTC/USD"].timestamp.iloc[-1])

    def runner(self, raw=None, p=.9, capital=100_000., **kwargs):
        raw = self.raw if raw is None else raw
        broker = PaperBroker(capital)
        models = {pair: bundle(p, pair) for pair in raw}
        return TradingEngine(broker, metadata(raw), models, **kwargs)


class ResearchTests(OfflineCase):
    def test_momentum_direction_and_horizons(self):
        frame = momentum.add_momentum_features(scenario("trend"))
        for h in config.MOMENTUM_HORIZONS:
            self.assertAlmostEqual(frame[f"ret_{h}"].iloc[-1], 1.001 ** h - 1)
        self.assertGreater(frame.ma_ratio.iloc[-1], 0)

    def test_mean_reversion_spikes(self):
        dip = mean_reversion.add_mean_reversion_features(ticks([100.] * 39 + [90.]))
        high = mean_reversion.add_mean_reversion_features(ticks([100.] * 39 + [110.]))
        self.assertLess(dip.zscore.iloc[-1], -1.5)
        self.assertGreater(high.zscore.iloc[-1], 1.5)

    def test_rsi_all_gains_losses_and_flat(self):
        for values, expected in [(np.arange(1., 61.), 100.), (np.arange(61., 1., -1), 0.), (np.full(60, 100.), 50.)]:
            self.assertEqual(mean_reversion._rsi(pd.Series(values), 20).iloc[-1], expected)

    def test_features_invariant_to_future_shocks(self):
        full_panel = build_price_panel(self.raw)
        for cutoff in [65, 90]:
            prefix = {p: f.iloc[:cutoff] for p, f in self.raw.items()}
            shocked = {p: f.copy() for p, f in self.raw.items()}
            for f in shocked.values():
                f.loc[cutoff:, "last_price"] *= 9
            for pair in self.raw:
                small = build_features_for_pair(pair, prefix, build_price_panel(prefix))
                large = build_features_for_pair(pair, shocked, build_price_panel(shocked)).iloc[:cutoff]
                pd.testing.assert_frame_equal(small, large)

    def test_features_exclude_optional_raw_null_fields(self):
        frame = build_features_for_pair("BTC/USD", self.raw, build_price_panel(self.raw))
        self.assertNotIn("coin_trade_value", frame)
        X, y, returns = train.training_frame(frame, self.raw["BTC/USD"].set_index("timestamp").last_price)
        self.assertGreater(len(X), 0)
        self.assertEqual(list(X.index), list(y.index))

    def test_all_feature_switches_off(self):
        frame = build_features_for_pair("BTC/USD", self.raw, build_price_panel(self.raw), dict.fromkeys(config.COMPONENTS, False))
        self.assertEqual(frame.shape[1], 0)

    def test_unknown_outcomes_remain_unknown(self):
        for mode in ["direction", "threshold", "regression"]:
            y = targets.get_target(self.raw["BTC/USD"].last_price, mode=mode)
            self.assertTrue(y.tail(10).isna().all())

    def test_missing_future_price_stays_unknown(self):
        price = pd.Series([100., 101., np.nan, 102.])
        self.assertTrue(pd.isna(targets.get_target(price, horizon=1).iloc[1]))

    def test_split_purges_horizon(self):
        for tr, va in train.walk_forward_splits(3600):
            self.assertLess(tr[-1] + config.TARGET_HORIZON, va[0])

    def test_split_defaults_follow_runtime_config(self):
        with patch.object(config, "TARGET_HORIZON", 30):
            tr, va = next(train.walk_forward_splits(3000))
            self.assertEqual(va[0] - tr[-1], 31)

    def test_overlapping_validation_windows_rejected(self):
        with self.assertRaises(ValueError):
            list(train.walk_forward_splits(3000, step_bars=10, val_bars=500))

    def test_provenance_guard_requires_availability(self):
        features = pd.DataFrame({"unknown": [1., 2.]}, index=[10, 20])
        with self.assertRaises(ValueError):
            assert_no_lookahead(features, features)
        with self.assertRaises(ValueError):
            assert_no_lookahead(features, features, pd.Series([11, 21], index=features.index))
        self.assertTrue(assert_no_lookahead(features, features, pd.Series([10, 20], index=features.index)))

    def test_cross_sectional_ranks(self):
        panel = build_price_panel(self.raw)
        output = cross_sectional.build_cross_sectional_features(panel)
        expected = panel.pct_change(5, fill_method=None).rank(axis=1, pct=True)
        self.assertEqual(output["ETH/USD"].cs_percentile.iloc[-1], expected["ETH/USD"].iloc[-1])

    def test_group_rank_does_not_mix_tokenized_equities(self):
        raw = {**self.raw, "TSLAB/USD": ticks(100 * 1.5 ** np.arange(120))}
        clean = build_features_for_pair("BTC/USD", self.raw, build_price_panel(self.raw))
        mixed = build_features_for_pair("BTC/USD", raw, build_price_panel(raw))
        pd.testing.assert_series_equal(clean.cs_percentile, mixed.cs_percentile)

    def test_fixed_schema_reorders_and_rejects_missing(self):
        model = bundle()
        frame = pd.DataFrame({"realized_vol": [.01], "ret_5": [.02], "unused": [1.]})
        model.predict(frame)
        self.assertEqual(model.estimator.seen[-1], ["ret_5", "realized_vol"])
        for invalid in [frame.drop(columns="ret_5"), frame.assign(ret_5=np.nan)]:
            with self.assertRaises(ValueError):
                model.predict(invalid)

    def test_negative_expected_payoff_cannot_pass_cost_gate(self):
        model = bundle(.6, mean_hit=.003, mean_miss=-.008)
        frame = pd.DataFrame({"ret_5": [.01], "realized_vol": [.005]})
        p, expected = model.predict(frame)
        self.assertAlmostEqual(expected[0], -.0014)
        self.assertEqual(model_target(model, frame), 0.)

    def test_temporal_classifier_training_and_payoff_bounds(self):
        frame = scenario("noisy", 2700).set_index("timestamp")
        X = pd.DataFrame({"ret_1": frame.last_price.pct_change(), "ret_5": frame.last_price.pct_change(5)}, index=frame.index)
        X, y, returns = train.training_frame(X, frame.last_price)
        folds = train.train_walk_forward(X, y, returns=returns)
        self.assertEqual(len(folds), 1)
        fold = folds[0]
        self.assertTrue(np.isfinite(fold["val_proba"]).all())
        fitted = fold["fitted_model"]
        self.assertAlmostEqual(fitted.mean_hit_return, returns.loc[fold["train_index"]][y.loc[fold["train_index"]] == 1].mean())
        self.assertLess(fold["train_index"][-1] + 10 * 60_000, fold["val_index"][0])

    def test_regression_target_uses_regressor(self):
        frame = scenario("noisy", 300).set_index("timestamp")
        X = pd.DataFrame({"ret_1": frame.last_price.pct_change()}, index=frame.index)
        X, y, returns = train.training_frame(X, frame.last_price, mode="regression")
        fitted = train.fit_bundle(X, y, returns, "ridge", mode="regression")
        p, expected = fitted.predict(X.iloc[[-1]])
        self.assertIsNone(p)
        self.assertTrue(np.isfinite(expected).all())

    def test_artifact_checksum_and_configuration_guard(self):
        model = bundle(pair="BTC/USD")
        path = Path(self.temp.name) / "model.joblib"
        train.save_bundle(model, path)
        self.assertEqual(train.load_bundle(path).pair, "BTC/USD")
        with patch.object(config, "MEAN_REV_WINDOW", 99):
            with self.assertRaises(ValueError):
                train.load_bundle(path)
        path.write_bytes(path.read_bytes() + b"corrupt")
        with self.assertRaises(ValueError):
            train.load_bundle(path)

    def test_ablation_changes_one_factor_on_common_oos_dates(self):
        frame = scenario("noisy", 2800).set_index("timestamp")
        feature = pd.DataFrame({"ret_5": frame.last_price.pct_change(5), "zscore": frame.last_price.pct_change(20),
                                "cs_percentile": .9, "realized_vol": .005, "trend_strength": .6}, index=frame.index).dropna()
        received = []
        def fake_train(X, y, model_name="logistic", returns=None, **kwargs):
            received.append((model_name, list(X.columns)))
            tr, va = next(train.walk_forward_splits(len(X)))
            model = bundle()
            model.columns = tuple(X.columns)
            return [{"fitted_model": model, "val_index": X.index[va]}]
        with patch.object(ablation, "train_walk_forward", side_effect=fake_train):
            result = ablation.run_ablation(frame.last_price, feature)
        self.assertNotIn("realized_vol", received[0][1])
        self.assertIn("realized_vol", received[1][1])
        self.assertEqual(received[0][0], received[1][0])
        self.assertEqual(result.n_oos.nunique(), 1)
        self.assertEqual(result.oos_start.nunique(), 1)
        self.assertIn("8_ml_with_cost_filter", result.index)
        self.assertIn("9_full_sized", result.index)

    def test_insufficient_ablation_data_raises(self):
        with self.assertRaises(ValueError):
            ablation.run_ablation(pd.Series([100., 101.]), pd.DataFrame({"ret_5": [.1, .2]}))

    def test_training_cli_artifact_loads_in_another_process(self):
        root = Path(self.temp.name)
        data = root / "data_store"
        data.mkdir()
        fixture = scenario("noisy", 2700)
        fixture.to_parquet(data / "BTC_USD.parquet", index=False)
        path = root / "model.joblib"
        cutoff = int(fixture.timestamp.iloc[-1])
        process = subprocess.run([sys.executable, "-m", "roostoo_bot.model.train", "--pair", "BTC/USD",
                                  "--train-end-ms", str(cutoff), "--output", str(path)], cwd=root, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        loaded = train.load_bundle(path)
        self.assertEqual(type(loaded).__module__, "roostoo_bot.model.artifact")
        self.assertLessEqual(loaded.trained_until, cutoff)

    def test_model_runtime_mismatch_rejected_before_deserialization(self):
        path = Path(self.temp.name) / "model.joblib"
        train.save_bundle(bundle(), path)
        manifest_path = path.with_suffix(".joblib.json")
        manifest = json.loads(manifest_path.read_text())
        manifest["runtime"]["scikit-learn"] = "incompatible-fixture-version"
        manifest_path.write_text(json.dumps(manifest))
        with patch.object(train.joblib, "load") as deserialize:
            with self.assertRaises(ValueError):
                train.load_bundle(path)
            deserialize.assert_not_called()


class DataAndAccountingTests(OfflineCase):
    def test_duplicate_ticks_are_idempotent(self):
        row = self.raw["BTC/USD"].iloc[0].to_dict()
        store.append_tick("BTC/USD", row)
        store.append_tick("BTC/USD", row)
        self.assertEqual(len(store.load("BTC/USD")), 1)

    def test_conflicting_duplicate_raises(self):
        row = self.raw["BTC/USD"].iloc[0].to_dict()
        store.append_tick("BTC/USD", row)
        with self.assertRaises(ValueError):
            store.append_tick("BTC/USD", {**row, "last_price": 999.})
        self.assertEqual(store.load("BTC/USD").last_price.iloc[0], row["last_price"])

    def test_gap_and_stale_history_rejected(self):
        frame = self.raw["BTC/USD"].copy()
        with self.assertRaises(ValueError):
            validate_recent_history(frame, self.now + 180_000, 61)
        frame.loc[90:, "timestamp"] += 180_000
        with self.assertRaises(ValueError):
            validate_recent_history(frame, self.now + 180_000, 61)

    def test_stale_panel_quote_expires(self):
        raw = {"BTC/USD": self.raw["BTC/USD"], "ETH/USD": self.raw["ETH/USD"].iloc[:1]}
        self.assertTrue(pd.isna(build_price_panel(raw)["ETH/USD"].iloc[-1]))

    def test_last_row_liquidation_fee_included(self):
        result = engine.simulate(pd.Series([100., 100., 100.]), pd.Series([.2, .2, 0.]))
        self.assertAlmostEqual(result.equity.iloc[-1], .99954, places=10)
        self.assertGreater(result.fees.iloc[-1], 0.)
        self.assertEqual(result.n_fills.sum(), 2)

    def test_drawdown_includes_starting_equity(self):
        result = engine.simulate(pd.Series([100., 100., 90.]), pd.Series([.2, .2, .2]))
        self.assertLess(result.drawdown.min(), -.02)
        self.assertAlmostEqual(result.drawdown.min(), result.equity.min() - 1)

    def test_signal_cannot_earn_previous_price_move(self):
        with patch.object(config, "TAKER_FEE", 0.), patch.object(config, "SLIPPAGE_ESTIMATE", 0.):
            result = engine.simulate(pd.Series([100., 200., 400.]), pd.Series([.2, 0., 0.]))
        self.assertEqual(result.equity.iloc[1], 1.)
        self.assertAlmostEqual(result.equity.iloc[-1], 1.2)

    def test_zero_lag_and_missing_price_rejected(self):
        for price, lag in [(pd.Series([100., 101.]), 0), (pd.Series([100., np.nan]), 1)]:
            with self.assertRaises(ValueError):
                engine.simulate(price, pd.Series([.2, .2]), execution_lag=lag)

    def test_sortino_one_loss_finite(self):
        net = pd.Series([.01, -.01, .02])
        expected = net.mean() / np.sqrt(.01 ** 2 / 3) * np.sqrt(365)
        self.assertAlmostEqual(engine._sortino_ratio(net, 365), expected)

    def test_metrics_do_not_mislabel_bars_as_closed_trades(self):
        result = engine.simulate(pd.Series([100., 101., 102.]), pd.Series([.2, .2, 0.]))
        summary = engine.summarize(result, periods_per_year=365)
        self.assertNotIn("win_rate", summary)
        self.assertEqual(summary["n_fills"], 2)
        json.dumps(summary, allow_nan=False)


class ExecutionTests(OfflineCase):
    def test_signed_gross_and_per_asset_limits(self):
        result = sizing.enforce_portfolio_limits({"A": .6, "B": -.6, "C": .6, "D": -.6})
        self.assertLessEqual(sum(abs(v) for v in result.values()), config.MAX_PORTFOLIO_EXPOSURE + 1e-12)
        self.assertTrue(all(abs(v) <= config.MAX_POSITION_PER_ASSET for v in result.values()))

    def test_sizing_rejects_nonfinite_and_responds_to_volatility(self):
        self.assertEqual(sizing.confidence_to_size(.9, np.nan), 0.)
        self.assertEqual(sizing.confidence_to_size(np.nan, .01), 0.)
        self.assertLess(sizing.confidence_to_size(.9, .04), sizing.confidence_to_size(.9, .005))

    def test_fraction_converts_to_units_and_rounds_down(self):
        self.assertEqual(sizing.capital_to_quantity(.2, 100_000., 50_000., 6), .4)
        self.assertEqual(sizing.round_quantity(.1234569, 6), .123456)

    def test_portfolio_limits_integrated_for_twenty_bullish_assets(self):
        raw = {p: scenario() for p in config.UNIVERSE}
        runner = self.runner(raw)
        events = runner.step(raw, self.now)
        self.assertEqual(len(events), 20)
        quotes = {p: Quote(float(f.last_price.iloc[-1]), float(f.max_bid.iloc[-1]), float(f.min_ask.iloc[-1]), self.now) for p, f in raw.items()}
        snapshot = runner.broker.state
        equity = snapshot.equity(quotes)
        gross = sum(v * quotes[p].price for p, v in snapshot.longs.items()) / equity
        self.assertLessEqual(gross, config.MAX_PORTFOLIO_EXPOSURE)
        self.assertTrue(all(v * quotes[p].price / equity <= config.MAX_POSITION_PER_ASSET for p, v in snapshot.longs.items()))

    def test_identical_snapshot_does_not_repeat_buy(self):
        runner = self.runner()
        self.assertTrue(runner.step(self.raw, self.now))
        self.assertEqual(runner.step(self.raw, self.now), [])

    def test_small_target_drift_does_not_repeat_buy_on_next_quote(self):
        raw = {"BTC/USD": scenario()}
        runner = self.runner(raw)
        runner.step(raw, self.now)
        next_raw = {p: f.copy() for p, f in raw.items()}
        for f in next_raw.values():
            f.loc[len(f)] = f.iloc[-1]
            f.loc[f.index[-1], "timestamp"] += 60_000
        self.assertEqual(runner.step(next_raw, self.now + 60_000), [])

    def test_low_probability_exits_existing_long(self):
        runner = self.runner()
        runner.broker.state.longs["BTC/USD"] = 100.
        runner.models["BTC/USD"] = bundle(.1, "BTC/USD")
        events = runner.step(self.raw, self.now)
        self.assertTrue(any(e["pair"] == "BTC/USD" and e["action"] == "SELL" for e in events))
        self.assertNotIn("BTC/USD", runner.broker.state.longs)

    def test_hourly_limit_and_expiry(self):
        runner = self.runner(state=ExecutionState(trades=[self.now] * 20))
        self.assertEqual(runner.step(self.raw, self.now), [])
        self.assertEqual(runner.state.slots(self.now + 3_600_000), 20)

    def test_drawdown_halt_latched_and_flattens(self):
        runner = self.runner(state=ExecutionState(high_water=150_000.))
        runner.broker.state.longs["BTC/USD"] = 10.
        events = runner.step(self.raw, self.now)
        self.assertTrue(runner.state.halted)
        self.assertTrue(all(e["action"] == "SELL" for e in events))
        self.assertFalse(runner.broker.state.longs)

    def test_disabled_ml_no_new_risk(self):
        runner = self.runner()
        with patch.dict(config.COMPONENTS, {"ml_model": False}):
            self.assertEqual(runner.step(self.raw, self.now), [])

    def test_cost_and_position_switches_respected(self):
        frame = pd.DataFrame({"ret_5": [.01], "realized_vol": [.04]})
        model = bundle(mean_hit=.001, mean_miss=.0002)
        self.assertEqual(model_target(model, frame), 0.)
        with patch.dict(config.COMPONENTS, {"cost_filter": False, "position_sizing": False}):
            self.assertEqual(model_target(model, frame), .2)

    def test_missing_schema_creates_no_prediction_or_order(self):
        runner = self.runner()
        runner.models["BTC/USD"].columns = ("nonexistent",)
        events = runner.step(self.raw, self.now)
        self.assertFalse(any(e["pair"] == "BTC/USD" for e in events))
        self.assertEqual(runner.models["BTC/USD"].estimator.seen, [])

    def test_stale_quote_blocks_cycle(self):
        runner = self.runner()
        with self.assertRaises(ValueError):
            runner.step(self.raw, self.now + 180_000)
        self.assertFalse(runner.broker.events)

    def test_uncertain_order_persists_and_blocks_retries(self):
        path = Path(self.temp.name) / "state.json"
        runner = self.runner(state_path=path)
        with patch.object(runner.broker, "submit", side_effect=UnknownOrderState("ambiguous timeout")) as submit:
            with self.assertRaises(UnknownOrderState):
                runner.step(self.raw, self.now)
            self.assertTrue(json.loads(path.read_text())["execution"]["uncertain"])
            with self.assertRaises(UnknownOrderState):
                runner.step(self.raw, self.now + 60_000)
            self.assertEqual(submit.call_count, 1)

    def test_paper_state_and_rate_budget_survive_restart(self):
        path = Path(self.temp.name) / "state.json"
        runner = self.runner(state_path=path)
        runner.step(self.raw, self.now)
        restored = restore_engine(PaperBroker(), metadata(self.raw), runner.models, path, None)
        self.assertEqual(restored.broker.state.longs, runner.broker.state.longs)
        self.assertEqual(restored.state.trades, runner.state.trades)
        self.assertEqual(restored.step(self.raw, self.now), [])

    def test_shorts_use_collateral_and_close_separate_from_spot_sell(self):
        raw = {"BTC/USD": scenario()}
        runner = self.runner(raw, p=.1)
        runner.models["BTC/USD"] = bundle(.1, "BTC/USD", mean_miss=-.008)
        with patch.object(config, "ALLOW_SHORTS", True):
            opened = runner.step(raw, self.now)
            self.assertEqual(opened[0]["action"], "SHORT_OPEN")
            self.assertFalse(runner.broker.state.longs)
            short = runner.broker.state.shorts["BTC/USD"]
            self.assertGreater(short.collateral, 0.)
            self.assertLess(runner.broker.state.cash, 100_000. - short.collateral + 1e-8)
            next_raw = {"BTC/USD": raw["BTC/USD"].copy()}
            next_raw["BTC/USD"].loc[len(next_raw["BTC/USD"])] = next_raw["BTC/USD"].iloc[-1]
            next_raw["BTC/USD"].loc[120, "timestamp"] += 60_000
            runner.models["BTC/USD"] = bundle(.9, "BTC/USD")
            closed = runner.step(next_raw, self.now + 60_000)
            self.assertEqual([e["action"] for e in closed], ["SHORT_CLOSE"])
            self.assertFalse(runner.broker.state.shorts)

    def test_dry_run_cannot_enable_live_broker(self):
        with self.assertRaises(RuntimeError):
            LiveBroker()

    def test_minimum_order_filter(self):
        quotes = {"BTC/USD": Quote(100., 100., 100., self.now)}
        with patch.object(config, "MIN_REBALANCE_FRACTION", 0.):
            orders = plan_orders({"BTC/USD": .001}, Snapshot(100.), quotes, metadata(quotes))
        self.assertEqual(orders, [])

    def test_failed_poll_cannot_trade_cached_data(self):
        runner = self.runner()
        with patch("roostoo_bot.main.poll_once", return_value=None), patch("roostoo_bot.main.load_all") as load:
            self.assertEqual(run_once(runner), [])
            load.assert_not_called()

    def test_empty_acknowledgement_stays_uncertain(self):
        runner = self.runner()
        with patch.object(runner.broker, "submit", return_value=None):
            with self.assertRaises(UnknownOrderState):
                runner.step(self.raw, self.now)
        self.assertTrue(runner.state.uncertain)

    def test_insufficient_api_budget_defers_orders_without_uncertainty(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True), patch("roostoo_bot.main.DRY_RUN", False):
            broker = LiveBroker()
            runner = TradingEngine(broker, metadata(self.raw), {p: bundle(pair=p) for p in self.raw})
            with patch.object(broker, "snapshot", return_value=Snapshot(100_000.)), \
                 patch.object(broker, "can_submit", return_value=False), patch.object(broker, "submit") as submit:
                self.assertEqual(runner.step(self.raw, self.now), [])
                submit.assert_not_called()
                self.assertFalse(runner.state.uncertain)
                self.assertEqual(runner.state.trades, [])

    def live_runner(self, state=None):
        broker = LiveBroker()
        return TradingEngine(broker, metadata(self.raw), {p: bundle(pair=p) for p in self.raw}, state=state)

    def test_live_uncertain_order_clears_after_clean_reconciliation(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True), patch("roostoo_bot.main.DRY_RUN", False):
            runner = self.live_runner(ExecutionState(uncertain=True))
            with patch.object(runner.broker, "snapshot", return_value=Snapshot(100_000.)), \
                 patch.object(runner.broker, "can_submit", return_value=False):
                runner.step(self.raw, self.now)
            self.assertFalse(runner.state.uncertain)

    def test_live_uncertain_order_stays_while_exchange_has_pending_orders(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True), patch("roostoo_bot.main.DRY_RUN", False):
            runner = self.live_runner(ExecutionState(uncertain=True))
            with patch.object(runner.broker, "snapshot", side_effect=UnknownOrderState("pending")), \
                 patch.object(runner.broker, "submit") as submit:
                with self.assertRaises(UnknownOrderState):
                    runner.step(self.raw, self.now)
                submit.assert_not_called()
            self.assertTrue(runner.state.uncertain)

    def test_live_wallet_ignores_negative_rounding_dust_only(self):
        quotes = {"BTC/USD": Quote(100., 99.9, 100.1, self.now)}
        def wallet(btc):
            return {"Success": True, "Wallet": {"USD": {"Free": 1000., "Lock": 0.}, "BTC": {"Free": btc, "Lock": 0.}}}
        with patch.object(config, "LIVE_TRADING_ENABLED", True), \
             patch.object(trader, "get_pending_count", return_value={"Success": False, "TotalPending": 0}), \
             patch.object(trader, "get_short_positions", return_value={"Success": True, "Positions": []}):
            with patch.object(trader, "get_balance", return_value=wallet(-.001)):
                self.assertEqual(LiveBroker().snapshot(quotes).longs, {})
            with patch.object(trader, "get_balance", return_value=wallet(-1.)):
                with self.assertRaises(UnknownOrderState):
                    LiveBroker().snapshot(quotes)

    def test_invalid_reconciled_holdings_rejected(self):
        with self.assertRaises(ValueError):
            Snapshot(1000., {"BTC/USD": -1.})

    def test_risk_cap_overrides_small_rebalance_deadband(self):
        snapshot = Snapshot(7999., {"BTC/USD": 20.01})
        quotes = {"BTC/USD": Quote(100., 100., 100., self.now)}
        # Current allocation is just over 20%; a hard cap cannot be hidden by
        # the small-trade deadband. Keep the venue minimum low for this case.
        info = metadata(quotes)
        info["BTC/USD"]["MiniOrder"] = 0.
        orders = plan_orders({"BTC/USD": .2}, snapshot, quotes, info)
        self.assertEqual(orders[0].action, "SELL")


class TransportTests(OfflineCase):
    def signed_context(self):
        return patch.multiple(config, API_KEY="offline-fixture-key", SECRET="offline-fixture-secret")

    def test_order_timeout_not_retried(self):
        with self.signed_context(), patch.object(trader._LIMITER, "reserve"), patch.object(trader._SESSION, "post", side_effect=requests.exceptions.Timeout) as post:
            self.assertIsNone(trader.place_order("BTC/USD", "BUY", .01))
            self.assertEqual(post.call_count, 1)

    def test_read_retry_refreshes_signature(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"Success": True, "Wallet": {}}
        with self.signed_context(), patch.object(trader._LIMITER, "reserve"), patch.object(trader.time, "sleep"), \
             patch("roostoo_bot.execution.auth.get_timestamp", side_effect=["1000", "2000"]), \
             patch.object(trader._SESSION, "get", side_effect=[requests.exceptions.Timeout, response]) as get:
            self.assertTrue(trader.get_balance()["Success"])
            self.assertEqual(get.call_args_list[0].kwargs["params"]["timestamp"], "1000")
            self.assertEqual(get.call_args_list[1].kwargs["params"]["timestamp"], "2000")

    def test_api_rate_budget(self):
        now = [0.]
        limiter = trader.RateLimiter(lambda: now[0])
        for _ in range(config.MAX_API_CALLS_PER_MINUTE):
            limiter.reserve()
        with self.assertRaises(RuntimeError):
            limiter.reserve()
        now[0] = 60.
        limiter.reserve()

    def test_signing_reads_current_secret_and_does_not_mutate_payload(self):
        payload = {"pair": "BTC/USD", "side": "BUY"}
        with patch("roostoo_bot.execution.auth.get_timestamp", return_value="123"):
            first = sign(payload, secret="one")[0]["MSG-SIGNATURE"]
            second = sign(payload, secret="two")[0]["MSG-SIGNATURE"]
        self.assertNotEqual(first, second)
        self.assertNotIn("timestamp", payload)

    def test_live_partial_fill_blocks_further_orders(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            broker = LiveBroker()
            with patch.object(trader, "place_order", return_value={"Success": True, "OrderDetail": {"Status": "PARTIALLY_FILLED", "FilledQuantity": .1}}):
                with self.assertRaises(UnknownOrderState):
                    broker.submit(Order("BTC/USD", "BUY", 1.), {}, {})

    def test_live_wallet_reconciliation(self):
        wallet = {"Success": True, "Wallet": {"USD": {"Free": 1000., "Lock": 0.}, "BTC": {"Free": .5, "Lock": 0.}}}
        quotes = {"BTC/USD": Quote(100., 100., 100., self.now)}
        with patch.object(config, "LIVE_TRADING_ENABLED", True), patch.object(trader, "get_balance", return_value=wallet), \
             patch.object(trader, "get_pending_count", return_value={"TotalPending": 0}), \
             patch.object(trader, "get_short_positions", return_value={"Success": True, "Positions": []}):
            snapshot = LiveBroker().snapshot(quotes)
            self.assertEqual(snapshot.equity(quotes), 1050.)

    def test_live_merged_short_logs_only_incremental_fill(self):
        response = {"Success": True, "Status": "OPEN", "ID": 1, "ShortQty": 3., "EntryPrice": 110., "OpenFee": .13}
        with patch.object(config, "LIVE_TRADING_ENABLED", True), patch.object(config, "ALLOW_SHORTS", True):
            broker = LiveBroker()
            broker._snapshot = Snapshot(1000., {}, {"BTC/USD": Short(2., 100., 200.)})
            with patch.object(trader, "short_open", return_value=response) as submit:
                event = broker.submit(Order("BTC/USD", "SHORT_OPEN", 130.), {}, {})
            submit.assert_called_once_with("BTC/USD", 130.)
            self.assertEqual(event["quantity"], 1.)
            self.assertEqual(event["price"], 130.)

    def test_documented_module_resolves_after_install(self):
        self.assertIsNotNone(importlib.util.find_spec("roostoo_bot.main"))
        result = subprocess.run([sys.executable, "-m", "roostoo_bot.main", "--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--model-dir", result.stdout)

    def test_missing_models_fail_before_any_api_call(self):
        from roostoo_bot import main
        with patch.object(sys, "argv", ["roostoo_bot.main", "--model-dir", self.temp.name]), patch.object(main, "get_exchange_info") as exchange:
            with self.assertRaises(SystemExit) as stopped:
                main.main()
            self.assertEqual(stopped.exception.code, 2)
            exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()

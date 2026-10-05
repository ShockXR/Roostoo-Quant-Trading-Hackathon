"""Offline checks for the Samyak research-tool integration."""
import datetime
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import numpy as np
import pandas as pd
from roostoo_bot import config, binance_downloader
from roostoo_bot.features.build_features import build_features_for_pair, build_price_panel, model_inputs
from roostoo_bot.model import train
from roostoo_bot.backtest import ablation
from roostoo_bot.scripts import backtest_sol, sweep
from .test_regressions import OfflineCase, ROOT
from .fixtures import scenario


class IntegrationTests(OfflineCase):
    def candles(self, count=30, drop=None, offset=0):
        opens = pd.date_range("2026-07-01", periods=count, freq="min", tz="UTC") + pd.Timedelta(minutes=offset)
        frame = pd.DataFrame({"open_time": opens, "close": 100. + np.arange(count)})
        if drop is not None:
            frame = frame.drop(drop)
        frame.to_parquet(Path(self.temp.name) / "SOLUSDT_1m.parquet", index=False)
        return frame

    def test_binance_close_is_available_after_one_minute(self):
        frame = self.candles()
        raw = backtest_sol.load_binance("SOLUSDT", "1m", None, None, self.temp.name)
        expected = int((frame.open_time.iloc[0] + pd.Timedelta(minutes=1)).value // 1_000_000)
        self.assertEqual(raw.timestamp.iloc[0], expected)
        self.assertEqual(raw.last_price.iloc[0], frame.close.iloc[0])
        panel = build_price_panel({"SOL/USD": raw})
        self.assertEqual(panel.index[0], expected)

    def test_coarse_binance_bars_use_complete_past_candles(self):
        frame = self.candles()
        raw = backtest_sol.load_binance("SOLUSDT", "5m", None, None, self.temp.name)
        self.assertEqual(len(raw), 6)
        self.assertEqual(raw.last_price.iloc[0], frame.close.iloc[4])
        self.assertEqual(raw.timestamp.iloc[0], int((frame.open_time.iloc[0] + pd.Timedelta(minutes=5)).value // 1_000_000))

    def test_binance_partial_boundary_bars_are_discarded(self):
        self.candles(12, offset=2)
        raw = backtest_sol.load_binance("SOLUSDT", "5m", None, None, self.temp.name)
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw.last_price.iloc[0], 107.)

    def test_binance_interior_missing_candle_is_rejected(self):
        self.candles(drop=7)
        for bar in ("1m", "5m"):
            with self.assertRaisesRegex(ValueError, "missing|incomplete"):
                backtest_sol.load_binance("SOLUSDT", bar, None, None, self.temp.name)

    def test_binance_duplicate_candle_is_rejected(self):
        frame = self.candles()
        pd.concat([frame, frame.iloc[[0]]]).to_parquet(Path(self.temp.name) / "SOLUSDT_1m.parquet", index=False)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            backtest_sol.load_binance("SOLUSDT", "1m", None, None, self.temp.name)

    def test_raw_price_and_ma_levels_are_excluded_everywhere(self):
        features = build_features_for_pair("BTC/USD", self.raw, build_price_panel(self.raw))
        self.assertNotIn("ma_fast", features)
        self.assertNotIn("ma_slow", features)
        selected = model_inputs(pd.DataFrame({"last_price": [1.], "ma_fast": [1.], "ma_slow": [1.], "ma_ratio": [.1]}))
        self.assertEqual(list(selected), ["ma_ratio"])

    def test_single_class_folds_remain_flat_with_explicit_status(self):
        index = scenario("flat", 170).timestamp
        X = pd.DataFrame({"ret_1": 0.}, index=index)
        y = pd.Series(0., index=index)
        returns = pd.Series(-.001, index=index)
        returns.attrs["available_at"] = {int(i): int(i) + 60_000 for i in index}
        folds = train.train_walk_forward(X, y, returns=returns, train_bars=100, val_bars=20, step_bars=20, horizon=1)
        self.assertGreater(len(folds), 0)
        self.assertTrue(all(f["status"] == "skipped" and f["fitted_model"] is None for f in folds))
        result = ablation._signal_from_model(X, pd.Series(100., index=index), folds=folds)
        self.assertTrue((result == 0).all())
        with self.assertRaisesRegex(ValueError, "unknown model"):
            train.train_walk_forward(X, y, model_name="invalid", returns=returns)

    def test_downloader_parses_millisecond_and_microsecond_archives(self):
        instant = pd.Timestamp("2026-07-01", tz="UTC")
        for divisor in (1_000_000, 1_000):
            start = instant.value // divisor
            close = (instant.value + 60_000_000_000 - 1_000_000) // divisor
            path = Path(self.temp.name) / "candles.csv"
            path.write_text(f"{start},100,102,99,101,2,{close},202,3,1,101,0\n")
            frame = binance_downloader.parse_kline_csv(path)
            self.assertEqual(frame.open_time.iloc[0], instant)
            self.assertEqual(frame.close.iloc[0], 101.)

    def test_binance_research_cli_runs_combined_pipeline_on_fixture(self):
        frame = scenario("noisy", 1050)
        candles = pd.DataFrame({"open_time": pd.to_datetime(frame.timestamp, unit="ms", utc=True), "close": frame.last_price})
        candles.to_parquet(Path(self.temp.name) / "SOLUSDT_1m.parquet", index=False)
        out = Path(self.temp.name) / "result.csv"
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "backtest_sol.py"),
                               "--data-dir", self.temp.name, "--no-context", "--bar", "5m",
                               "--horizon", "3", "--train-bars", "100", "--val-bars", "30", "--out", str(out)],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        results = pd.read_csv(out).set_index("baseline")
        self.assertIn("9_full_sized", results.index)
        self.assertEqual(results.n_oos.nunique(), 1)
        self.assertTrue(np.isfinite(results.total_net_return).all())
        self.assertIn("n_skipped_folds", results)

    def test_maker_research_mode_fails_before_any_download_or_trade(self):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "backtest_sol.py"), "--maker"],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("resting-order fill model", proc.stderr)

    def test_sweep_collects_new_full_strategy_and_fill_metrics(self):
        row = pd.DataFrame({"baseline": ["9_full_sized"], "total_net_return": [.001],
                            "composite_score": [.2], "sortino_like": [.3], "max_drawdown": [-.01], "n_fills": [2]})
        with patch.object(sweep, "RESULTS_DIR", Path(self.temp.name)), patch.object(sweep, "run_cell", return_value=row.assign(symbol="SOLUSDT", bar="5m", horizon_min=30)), patch.object(sys, "argv", ["sweep", "--symbols", "SOLUSDT", "--bars", "5m", "--horizons", "30", "--jobs", "1"]):
            sweep.main()
        self.assertTrue((Path(self.temp.name) / "sweep_taker.csv").exists())

"""
Offline backtest on Binance 1m history (binance_data/processed/parquet),
produced by binance_downloader.py. Adapts the Binance kline columns to the
live-ticker schema (timestamp, last_price) the feature pipeline expects,
optionally resamples to coarser bars, then runs the full 9-stage ablation,
ending in the cost-aware bot.

Run from the project root:

    .venv/bin/python scripts/backtest_sol.py
    .venv/bin/python scripts/backtest_sol.py --start 2026-09-20            # quick smoke test
    .venv/bin/python scripts/backtest_sol.py --bar 15m --horizon 4
"""
import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "binance_data" / "processed" / "parquet"
RESULTS_DIR = ROOT / "binance_data" / "results"

BAR_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}

# downloaded symbols that are also in the "majors" universe group, used as
# cross-sectional / market-proxy context for the target symbol
CONTEXT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]


def to_pair(symbol: str) -> str:
    """SOLUSDT -> SOL/USD (the Roostoo pair naming used throughout config)."""
    return symbol.removesuffix("USDT") + "/USD"


def load_binance(symbol: str, bar: str, start: str | None, end: str | None, data_dir=None) -> pd.DataFrame:
    """Use completed, contiguous candles labeled by their availability time."""
    data_dir = DATA_DIR if data_dir is None else pathlib.Path(data_dir)
    df = pd.read_parquet(data_dir / f"{symbol}_1m.parquet", columns=["open_time", "close"])
    s = df.set_index(pd.to_datetime(df["open_time"], utc=True))["close"].sort_index()
    if s.index.hasnans or not s.index.is_unique or not np.isfinite(s).all() or (s <= 0).any():
        raise ValueError("invalid or duplicate Binance candles")
    # A 1m candle beginning at t cannot expose its close before t + 1m.
    s.index = s.index + pd.Timedelta(minutes=1)
    if bar != "1m":
        rule = f"{BAR_MINUTES[bar]}min"
        grouped = s.resample(rule, closed="right", label="right")
        counts = grouped.count()
        complete = grouped.last().where(counts == BAR_MINUTES[bar])
        # Partial boundary bars are discarded; interior holes fail below.
        s = complete.loc[complete.first_valid_index():complete.last_valid_index()] if complete.notna().any() else complete.iloc[:0]
    if start:
        s = s[s.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        s = s[s.index < pd.Timestamp(end, tz="UTC")]
    if s.isna().any() or (len(s) > 1 and not (s.index.to_series().diff().dropna() == pd.Timedelta(minutes=BAR_MINUTES[bar])).all()):
        raise ValueError("history contains missing or incomplete candles; choose a contiguous window")
    # Also reject missing source minutes hidden inside a seemingly complete bar.
    if len(s):
        source = pd.DatetimeIndex(pd.to_datetime(df.open_time, utc=True)).sort_values() + pd.Timedelta(minutes=1)
        source = source[(source > s.index[0] - pd.Timedelta(minutes=BAR_MINUTES[bar])) & (source <= s.index[-1])]
        if len(source) > 1 and not (source.to_series().diff().dropna() == pd.Timedelta(minutes=1)).all():
            raise ValueError("history contains missing source minutes")
    return pd.DataFrame({"timestamp": s.index.as_unit("ns").asi8 // 1_000_000, "last_price": s.to_numpy()})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--start", help="inclusive UTC date, e.g. 2026-09-01")
    parser.add_argument("--end", help="exclusive UTC date")
    parser.add_argument("--bar", choices=BAR_MINUTES, default="1m", help="bar size to resample to")
    parser.add_argument("--horizon", type=int, help="target horizon in bars (default: config.TARGET_HORIZON)")
    parser.add_argument("--maker", action="store_true",
                        help="unsupported until resting-order fills are modeled (fails explicitly)")
    parser.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    parser.add_argument("--train-bars", type=int, default=None)
    parser.add_argument("--val-bars", type=int, default=None)
    parser.add_argument("--no-context", action="store_true",
                        help="use only the target symbol (no cross-sectional / market-proxy features)")
    parser.add_argument("--out", type=pathlib.Path, help="results CSV path (default: binance_data/results/)")
    args = parser.parse_args()

    if args.maker:
        parser.error("maker backtests require a resting-order fill model; only taker execution is supported")
    if args.horizon is not None and args.horizon <= 0:
        parser.error("horizon must be positive")
    if any(v is not None and v <= 0 for v in (args.train_bars, args.val_bars)):
        parser.error("training and validation windows must be positive")

    from roostoo_bot import config
    bar_minutes = BAR_MINUTES[args.bar]
    config.POLL_INTERVAL_SECONDS = 60 * bar_minutes
    config.BARS_PER_YEAR = (365 * 24 * 60) // bar_minutes
    config.MAX_QUOTE_AGE_MS = config.MAX_HISTORY_GAP_MS = 120_000 * bar_minutes
    if args.horizon is not None:
        config.TARGET_HORIZON = args.horizon
    if args.train_bars is not None:
        config.WALKFORWARD_TRAIN_BARS = args.train_bars
    if args.val_bars is not None:
        config.WALKFORWARD_VAL_BARS = config.WALKFORWARD_STEP_BARS = args.val_bars

    from roostoo_bot.backtest.ablation import run_ablation
    from roostoo_bot.features.build_features import build_features_for_pair, build_price_panel

    symbols = [args.symbol] if args.no_context else sorted(set(CONTEXT_SYMBOLS) | {args.symbol})
    raw = {to_pair(s): load_binance(s, args.bar, args.start, args.end, args.data_dir) for s in symbols
           if (args.data_dir / f"{s}_1m.parquet").exists()}
    pair = to_pair(args.symbol)
    if pair not in raw:
        sys.exit(f"no data for {args.symbol} in {args.data_dir}")
    print(f"loaded {', '.join(f'{p} ({len(d)})' for p, d in raw.items())}")

    panel = build_price_panel(raw)
    features = build_features_for_pair(pair, raw, panel)

    # ratio features can blow up to +-inf on flat bars; sklearn rejects those
    price = raw[pair].set_index("timestamp").last_price
    if features.empty:
        parser.error("no completed candles in requested window")
    print(f"{pair}: {len(features)} x {args.bar} bars after warm-up, "
          f"{features.index[0]} -> {features.index[-1]}, {features.shape[1]} features, "
          f"horizon={config.TARGET_HORIZON} bars, fees={'maker' if args.maker else 'taker'}")

    try:
        results = run_ablation(price, features)
    except ValueError as exc:
        parser.error(str(exc))

    out_path = args.out or RESULTS_DIR / f"{args.symbol}_{args.bar}_h{config.TARGET_HORIZON}_ablation.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(out_path)

    with pd.option_context("display.width", 200, "display.max_columns", None, "display.float_format", "{:.4f}".format):
        print(results)
    print(f"\nsaved -> {out_path}")
    print(f"(buy/hold uses a fixed {config.MAX_POSITION_PER_ASSET:.0%} position, not 100%)")


if __name__ == "__main__":
    main()

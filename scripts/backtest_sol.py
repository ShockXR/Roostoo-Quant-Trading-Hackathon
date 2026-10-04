"""
Offline backtest on Binance 1m history (binance_data/processed/parquet),
produced by binance_downloader.py. Adapts the Binance kline columns to the
live-ticker schema (timestamp, last_price) the feature pipeline expects,
optionally resamples to coarser bars, then runs the full 8-stage ablation,
ending in the cost-aware bot.

Run from the project root:

    .venv/bin/python scripts/backtest_sol.py
    .venv/bin/python scripts/backtest_sol.py --start 2026-09-20            # quick smoke test
    .venv/bin/python scripts/backtest_sol.py --bar 15m --horizon 4 --maker
"""
import argparse
import importlib.util
import pathlib
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning

warnings.filterwarnings("ignore", category=ConvergenceWarning)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "binance_data" / "processed" / "parquet"
RESULTS_DIR = ROOT / "binance_data" / "results"

BAR_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}

# downloaded symbols that are also in the "majors" universe group, used as
# cross-sectional / market-proxy context for the target symbol
CONTEXT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]


def _register_package():
    """The repo root is itself a package using relative imports, but its
    directory name has hyphens -- register it under an importable name."""
    spec = importlib.util.spec_from_file_location(
        "roostoo", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["roostoo"] = pkg
    spec.loader.exec_module(pkg)


def to_pair(symbol: str) -> str:
    """SOLUSDT -> SOL/USD (the Roostoo pair naming used throughout config)."""
    return symbol.removesuffix("USDT") + "/USD"


def load_binance(symbol: str, bar: str, start: str | None, end: str | None) -> pd.DataFrame:
    df = pd.read_parquet(DATA_DIR / f"{symbol}_1m.parquet", columns=["open_time", "close"])
    s = df.set_index(pd.to_datetime(df["open_time"], utc=True))["close"].sort_index()
    if start:
        s = s[s.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        s = s[s.index < pd.Timestamp(end, tz="UTC")]
    if bar != "1m":
        # label each bar by its open time and take the last 1m close inside it
        s = s.resample(f"{BAR_MINUTES[bar]}min").last().dropna()
    return s.rename("last_price").rename_axis("timestamp").reset_index()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--start", help="inclusive UTC date, e.g. 2026-09-01")
    parser.add_argument("--end", help="exclusive UTC date")
    parser.add_argument("--bar", choices=BAR_MINUTES, default="1m", help="bar size to resample to")
    parser.add_argument("--horizon", type=int, help="target horizon in bars (default: config.TARGET_HORIZON)")
    parser.add_argument("--maker", action="store_true",
                        help="charge maker instead of taker fees (assumes every limit order fills)")
    parser.add_argument("--no-context", action="store_true",
                        help="use only the target symbol (no cross-sectional / market-proxy features)")
    parser.add_argument("--out", type=pathlib.Path, help="results CSV path (default: binance_data/results/)")
    args = parser.parse_args()

    _register_package()

    # Override config BEFORE importing anything else from the package: many
    # functions bind config values as default arguments at import time
    # (get_target's horizon, walk_forward_splits' purge_bars, summarize's
    # periods_per_year), so changing config afterwards would be silently ignored.
    from roostoo import config
    bar_minutes = BAR_MINUTES[args.bar]
    config.POLL_INTERVAL_SECONDS = 60 * bar_minutes
    config.BARS_PER_YEAR = (365 * 24 * 60) // bar_minutes
    if args.horizon:
        config.TARGET_HORIZON = args.horizon

    from roostoo.backtest.ablation import run_ablation
    from roostoo.features.build_features import build_features_for_pair, build_price_panel

    symbols = [args.symbol] if args.no_context else sorted(set(CONTEXT_SYMBOLS) | {args.symbol})
    raw = {to_pair(s): load_binance(s, args.bar, args.start, args.end) for s in symbols
           if (DATA_DIR / f"{s}_1m.parquet").exists()}
    pair = to_pair(args.symbol)
    if pair not in raw:
        sys.exit(f"no data for {args.symbol} in {DATA_DIR}")
    print(f"loaded {', '.join(f'{p} ({len(d)})' for p, d in raw.items())}")

    panel = build_price_panel(raw)
    features = build_features_for_pair(pair, raw, panel)

    # ratio features can blow up to +-inf on flat bars; sklearn rejects those
    features = features.replace([np.inf, -np.inf], np.nan).dropna()
    price = features["last_price"]
    print(f"{pair}: {len(features)} x {args.bar} bars after warm-up, "
          f"{features.index[0]} -> {features.index[-1]}, {features.shape[1]} features, "
          f"horizon={config.TARGET_HORIZON} bars, fees={'maker' if args.maker else 'taker'}")

    results = run_ablation(price, features, taker=not args.maker)

    out_path = args.out or RESULTS_DIR / f"{args.symbol}_{args.bar}_h{config.TARGET_HORIZON}_ablation.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(out_path)

    with pd.option_context("display.width", 200, "display.max_columns", None, "display.float_format", "{:.4f}".format):
        print(results)
    print(f"\nsaved -> {out_path}")
    print(f"(buy/hold uses a fixed {config.MAX_POSITION_PER_ASSET:.0%} position, not 100%)")


if __name__ == "__main__":
    main()

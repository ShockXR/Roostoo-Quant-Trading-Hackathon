"""
Offline backtest on Binance 1m history (binance_data/processed/parquet),
produced by binance_downloader.py. Adapts the Binance kline columns to the
live-ticker schema (timestamp, last_price) the feature pipeline expects,
then runs the full 8-stage ablation, ending in the cost-aware bot.

Run from the project root:

    .venv/bin/python scripts/backtest_sol.py
    .venv/bin/python scripts/backtest_sol.py --start 2026-09-20   # quick smoke test
"""
import argparse
import importlib.util
import pathlib
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning

# logistic baseline on unscaled features hits max_iter every fold; the
# warning repeats ~260 times on a full run and buries the results table
warnings.filterwarnings("ignore", category=ConvergenceWarning)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "binance_data" / "processed" / "parquet"
RESULTS_DIR = ROOT / "binance_data" / "results"

# The repo root is itself a package using relative imports, but its
# directory name has hyphens -- register it under an importable name.
_spec = importlib.util.spec_from_file_location(
    "roostoo", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["roostoo"] = _pkg
_spec.loader.exec_module(_pkg)

from roostoo import config  # noqa: E402
from roostoo.backtest.ablation import run_ablation  # noqa: E402
from roostoo.features.build_features import build_features_for_pair, build_price_panel  # noqa: E402

# downloaded symbols that are also in the "majors" universe group, used as
# cross-sectional / market-proxy context for the target symbol
CONTEXT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]


def to_pair(symbol: str) -> str:
    """SOLUSDT -> SOL/USD (the Roostoo pair naming used throughout config)."""
    return symbol.removesuffix("USDT") + "/USD"


def load_binance(symbol: str, start: str | None, end: str | None) -> pd.DataFrame:
    df = pd.read_parquet(DATA_DIR / f"{symbol}_1m.parquet", columns=["open_time", "close"])
    df = df.rename(columns={"open_time": "timestamp", "close": "last_price"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] < pd.Timestamp(end, tz="UTC")]
    return df.sort_values("timestamp").reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--start", help="inclusive UTC date, e.g. 2026-09-01")
    parser.add_argument("--end", help="exclusive UTC date")
    parser.add_argument("--no-context", action="store_true",
                        help="use only the target symbol (no cross-sectional / market-proxy features)")
    args = parser.parse_args()

    symbols = [args.symbol] if args.no_context else sorted(set(CONTEXT_SYMBOLS) | {args.symbol})
    raw = {to_pair(s): load_binance(s, args.start, args.end) for s in symbols
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
    print(f"{pair}: {len(features)} bars after warm-up, "
          f"{features.index[0]} -> {features.index[-1]}, {features.shape[1]} features")

    results = run_ablation(price, features)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.symbol}_ablation.csv"
    results.to_csv(out_path)

    with pd.option_context("display.width", 200, "display.max_columns", None, "display.float_format", "{:.4f}".format):
        print(results)
    print(f"\nsaved -> {out_path.relative_to(ROOT)}")
    print(f"(buy/hold uses a fixed {config.MAX_POSITION_PER_ASSET:.0%} position, not 100%)")


if __name__ == "__main__":
    main()

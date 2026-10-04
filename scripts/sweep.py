"""
Grid search over bar size x target horizon x symbol using backtest_sol.py,
restricted to a research window so a later holdout month stays untouched.

Each cell runs in its own subprocess (config is overridden per process at
import time, so cells can't share one interpreter). Results are collected
into one long table.

Run from the project root:

    .venv/bin/python scripts/sweep.py                 # taker fees, Jul-Aug
    .venv/bin/python scripts/sweep.py --maker --jobs 4
"""
import argparse
import itertools
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "binance_data" / "results"
BAR_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


def run_cell(bar: str, horizon_min: int, symbol: str, args, cell_dir: pathlib.Path) -> pd.DataFrame | None:
    horizon = horizon_min // BAR_MINUTES[bar]
    out = cell_dir / f"{symbol}_{bar}_{horizon_min}m.csv"
    cmd = [sys.executable, str(ROOT / "scripts" / "backtest_sol.py"),
           "--symbol", symbol, "--bar", bar, "--horizon", str(horizon), "--out", str(out)]
    if args.start:
        cmd += ["--start", args.start]
    if args.end:
        cmd += ["--end", args.end]
    if args.maker:
        cmd.append("--maker")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"FAILED {symbol} {bar} {horizon_min}m:\n{proc.stderr[-2000:]}", file=sys.stderr)
        return None
    df = pd.read_csv(out)
    df.insert(0, "symbol", symbol)
    df.insert(1, "bar", bar)
    df.insert(2, "horizon_min", horizon_min)
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", nargs="+", default=["SOLUSDT", "ETHUSDT", "BTCUSDT"])
    parser.add_argument("--bars", nargs="+", default=list(BAR_MINUTES), choices=BAR_MINUTES)
    parser.add_argument("--horizons", nargs="+", type=int, default=[30, 60, 240],
                        help="target horizons in MINUTES (converted to bars per bar size)")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default="2026-09-01", help="exclusive; default keeps September as holdout")
    parser.add_argument("--maker", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()

    fees = "maker" if args.maker else "taker"
    cell_dir = RESULTS_DIR / f"sweep_{fees}"
    cell_dir.mkdir(parents=True, exist_ok=True)

    # horizons shorter than one bar make no sense for that bar size
    cells = [(b, h, s) for b, h, s in itertools.product(args.bars, args.horizons, args.symbols)
             if h >= BAR_MINUTES[b]]
    print(f"{len(cells)} cells, {args.jobs} parallel, fees={fees}, window {args.start or 'start'} -> {args.end}", flush=True)

    frames = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_cell, *cell, args, cell_dir): cell for cell in cells}
        for i, fut in enumerate(as_completed(futures), 1):
            df = fut.result()
            print(f"[{i}/{len(cells)}] {' '.join(map(str, futures[fut]))} {'ok' if df is not None else 'FAILED'}", flush=True)
            if df is not None:
                frames.append(df)

    table = pd.concat(frames, ignore_index=True)
    out_path = RESULTS_DIR / f"sweep_{fees}.csv"
    table.to_csv(out_path, index=False)

    bot = table[table["baseline"] == "8_full_cost_aware"]
    cols = ["symbol", "bar", "horizon_min", "total_net_return", "composite_score",
            "sortino_like", "max_drawdown", "n_trades_proxy"]
    with pd.option_context("display.width", 200, "display.float_format", "{:.4f}".format):
        print("\nfull bot (baseline 8), by composite score:")
        print(bot[cols].sort_values("composite_score", ascending=False).to_string(index=False))

        # a config only counts if it holds up across symbols, not on one lucky coin
        by_cfg = bot.groupby(["bar", "horizon_min"]).agg(
            positive_symbols=("composite_score", lambda s: int((s > 0).sum())),
            mean_composite=("composite_score", "mean"),
            mean_return=("total_net_return", "mean"),
        ).sort_values("mean_composite", ascending=False)
        print("\nper config across symbols:")
        print(by_cfg.to_string())
    print(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()

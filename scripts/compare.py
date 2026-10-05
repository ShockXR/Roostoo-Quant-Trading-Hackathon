"""
Side-by-side comparison of experiments, per asset and portfolio, straight
from their saved metrics.csv. Prints Markdown tables (pasted into
TRADING_BOT_OPTIMIZATION_REPORT.md) and writes experiments/final_comparison.csv.

Run from the project root:

    .venv/bin/python scripts/compare.py E01_baseline F01_final B00_buy_hold_20pct
"""
import argparse
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import EXPERIMENTS_DIR  # noqa: E402

COLUMNS = {  # metric -> (header, format)
    "total_return": ("Return", "{:+.2%}"),
    "total_pnl": ("PnL $", "{:+,.0f}"),
    "max_drawdown": ("Max DD", "{:.2%}"),
    "sharpe_daily": ("Sharpe", "{:.2f}"),
    "sortino_daily": ("Sortino", "{:.2f}"),
    "profit_factor": ("PF", "{:.2f}"),
    "win_rate": ("Win %", "{:.0%}"),
    "n_trades": ("Trades", "{:.0f}"),
    "expectancy": ("Avg trade $", "{:+,.1f}"),
    "fees_paid": ("Fees $", "{:,.0f}"),
    "slippage_cost": ("Slippage $", "{:,.0f}"),
    "avg_gross_exposure": ("Exposure", "{:.1%}"),
    "alpha_tstat": ("Alpha t", "{:+.2f}"),
}


def _fmt(v, f):
    if pd.isna(v):
        return "n/a"
    if v == float("inf"):
        return "∞"
    return f.format(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--periods", nargs="+", default=["test", "full"])
    args = ap.parse_args()

    frames = []
    for exp in args.ids:
        m = pd.read_csv(EXPERIMENTS_DIR / exp / "metrics.csv")
        m.insert(0, "experiment", exp)
        frames.append(m)
    all_m = pd.concat(frames, ignore_index=True)
    all_m.to_csv(EXPERIMENTS_DIR / "final_comparison.csv", index=False)

    for period in args.periods:
        sub = all_m[all_m.period == period]
        print(f"\n#### {period}\n")
        print("| Asset | Strategy | " + " | ".join(h for h, _ in COLUMNS.values()) + " |")
        print("|---|---|" + "---|" * len(COLUMNS))
        for asset in list(dict.fromkeys(sub.asset)):
            for exp in args.ids:
                row = sub[(sub.asset == asset) & (sub.experiment == exp)]
                if row.empty:
                    continue
                r = row.iloc[0]
                cells = [_fmt(r[c], f) for c, (_, f) in COLUMNS.items()]
                print(f"| {asset} | {exp} | " + " | ".join(cells) + " |")
    print(f"\nsaved -> {(EXPERIMENTS_DIR / 'final_comparison.csv').relative_to(EXPERIMENTS_DIR.parent)}")


if __name__ == "__main__":
    main()

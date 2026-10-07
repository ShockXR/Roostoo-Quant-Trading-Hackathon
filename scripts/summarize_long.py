"""
Standardized comparison table of long-history runs (scripts/run_long.py),
portfolio level, one row per (experiment, period). Writes
experiments/long/comparison.csv and prints Markdown.

Run from the project root:

    .venv/bin/python scripts/summarize_long.py L00_m0 L01_m1 L02_m2 B00_buy_hold \
        --periods train validation test
"""
import argparse
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_long import LONG_DIR  # noqa: E402

COLUMNS = {  # metric -> (header, format)
    "total_return": ("Return", "{:+.1%}"),
    "annualized_return": ("CAGR", "{:+.1%}"),
    "sharpe_daily": ("Sharpe", "{:.2f}"),
    "sortino_daily": ("Sortino", "{:.2f}"),
    "max_drawdown": ("Max DD", "{:.1%}"),
    "calmar": ("Calmar", "{:.2f}"),
    "volatility_annual": ("Vol", "{:.1%}"),
    "win_rate": ("Win %", "{:.0%}"),
    "profit_factor": ("PF", "{:.2f}"),
    "n_trades": ("Trades", "{:.0f}"),
    "turnover": ("Turnover", "{:.0f}x"),
    "total_costs": ("Costs $", "{:,.0f}"),
    "avg_gross_exposure": ("Exposure", "{:.0%}"),
    "alpha_tstat": ("Alpha t", "{:+.2f}"),
    "w14_pct_positive": ("14d win %", "{:.0%}"),
    "w14_p05_return": ("14d p5", "{:+.1%}"),
}


def _fmt(v, f):
    if pd.isna(v):
        return "n/a"
    if v == float("inf"):
        return "inf"
    return f.format(v)


def load(ids: list[str]) -> pd.DataFrame:
    frames = []
    for exp in ids:
        m = pd.read_csv(LONG_DIR / exp / "metrics.csv")
        m = m[m.asset == "PORTFOLIO"].copy()
        meta = json.loads((LONG_DIR / exp / "meta.json").read_text())
        m.insert(0, "experiment", exp)
        m.insert(1, "model", meta["model"])
        frames.append(m)
    return pd.concat(frames, ignore_index=True)


def markdown(df: pd.DataFrame, period: str) -> str:
    sub = df[df.period == period]
    head = "| Model | " + " | ".join(h for h, _ in COLUMNS.values()) + " |"
    sep = "|---" * (len(COLUMNS) + 1) + "|"
    lines = [head, sep]
    for _, r in sub.iterrows():
        lines.append(f"| {r.experiment} | " + " | ".join(_fmt(r[c], f) for c, (_, f) in COLUMNS.items()) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--periods", nargs="+", default=["train", "validation"])
    ap.add_argument("--out", default="comparison.csv")
    args = ap.parse_args()
    df = load(args.ids)
    df.to_csv(LONG_DIR / args.out, index=False)
    for period in args.periods:
        print(f"\n#### {period}\n\n{markdown(df, period)}")


if __name__ == "__main__":
    main()

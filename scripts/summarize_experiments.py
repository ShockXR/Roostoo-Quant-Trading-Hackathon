"""
Collects every experiments/<id>/{meta.json,metrics.csv} into one table,
experiments/registry.csv: one row per experiment x period with portfolio
metrics plus per-asset consistency. Decisions (ACCEPTED / REJECTED) and
reasoning live in experiments/EXPERIMENT_LOG.md; this file is the numbers.

Run from the project root:

    .venv/bin/python scripts/summarize_experiments.py
"""
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import EXPERIMENTS_DIR  # noqa: E402

PORTFOLIO_COLS = ["total_return", "max_drawdown", "sharpe_daily", "sortino_daily", "sharpe_1m",
                  "sortino_1m", "calmar", "profit_factor", "win_rate", "n_trades", "expectancy",
                  "avg_gross_exposure", "turnover", "fees_paid", "slippage_cost"]


BENCHMARKS = {"large_caps": "B00_buy_hold_20pct", "mid_caps": "midcaps/MB00_buy_hold_20pct"}


def main():
    rows = []
    for meta_path in sorted(EXPERIMENTS_DIR.rglob("meta.json")):
        if "long" in meta_path.relative_to(EXPERIMENTS_DIR).parts:
            continue  # hourly long-history runs: scripts/summarize_long.py
        meta = json.loads(meta_path.read_text())
        m = pd.read_csv(meta_path.parent / "metrics.csv")
        for period, g in m.groupby("period", sort=False):
            port = g[g.asset == "PORTFOLIO"].iloc[0]
            assets = g[g.asset != "PORTFOLIO"]
            row = {"id": meta["id"], "universe": meta.get("universe", "large_caps"),
                   "strategy": meta["strategy"], "params": json.dumps(meta["params"]),
                   "fee": meta["fee_per_side"], "slippage": meta["slippage_per_side"], "period": period}
            row.update({c: port[c] for c in PORTFOLIO_COLS})
            row["assets_positive_return"] = int((assets.total_return > 0).sum())
            row["assets_sharpe_gt_buyhold"] = None
            row.update({f"ret_{a}": r for a, r in zip(assets.asset, assets.total_return)})
            row.update({f"sharpe_{a}": r for a, r in zip(assets.asset, assets.sharpe_daily)})
            rows.append(row)
    reg = pd.DataFrame(rows)
    # per-asset Sharpe vs the 20% buy & hold benchmark of the same universe and period
    sharpe_cols = [c for c in reg.columns if c.startswith("sharpe_") and c.endswith("USDT")]
    bench = {u: reg[reg.id == i].set_index("period") for u, i in BENCHMARKS.items()}

    def beats(r):
        bh = bench.get(r.universe)
        if bh is None or bh.empty or r.period not in bh.index:
            return None
        return int(sum(r[c] > bh.loc[r.period, c] for c in sharpe_cols if pd.notna(r[c])))
    reg["assets_sharpe_gt_buyhold"] = reg.apply(beats, axis=1)
    out = EXPERIMENTS_DIR / "registry.csv"
    reg.to_csv(out, index=False)
    with pd.option_context("display.width", 250, "display.max_columns", None,
                           "display.float_format", "{:.4f}".format):
        print(reg[["id", "universe", "period", "total_return", "max_drawdown", "sharpe_daily", "sortino_daily",
                   "profit_factor", "n_trades", "avg_gross_exposure", "assets_positive_return"]].to_string(index=False))
    print(f"\nsaved -> {out.relative_to(EXPERIMENTS_DIR.parent)}")


if __name__ == "__main__":
    main()

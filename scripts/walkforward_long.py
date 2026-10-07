"""
Walk-forward test of a model's parameter selection: every `step_months`,
choose the grid cell with the best daily Sharpe over an expanding window
of everything before the block (2022-01 .. block start), trade it for the
block, and stitch the blocks into one out-of-sample equity curve. Compared
with the frozen default over the same span.

Answers: would re-tuning on past data have beaten simply freezing the
parameters, and is the frozen choice an outlier of the selection process?

Run from the project root:

    .venv/bin/python scripts/walkforward_long.py --id W01_m3 --model m2_xs_momentum \
        --grid lookback_days=[14,21,28,42,56] --grid market_sma_days=[50,75,100,150,200] \
        --fixed top_n=3 --frozen '{"lookback_days": 28, "market_sma_days": 100}'
"""
import argparse
import itertools
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import register_package  # noqa: E402
from run_long import LONG_DIR, UNIVERSE_CHOICES, default_slippage, load_any, minute_equity  # noqa: E402


def _sharpe(daily: pd.Series) -> float:
    r = daily.pct_change().dropna()
    return float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--grid", action="append", required=True)
    ap.add_argument("--fixed", action="append")
    ap.add_argument("--frozen", required=True, help="JSON params of the frozen model (merged with --fixed)")
    ap.add_argument("--start", default="2023-01-01", help="first out-of-sample block")
    ap.add_argument("--end", default="2026-07-01")
    ap.add_argument("--step-months", type=int, default=6)
    ap.add_argument("--universe", default="large_caps_2022", choices=UNIVERSE_CHOICES)
    args = ap.parse_args()

    register_package()
    from roostoo import config, strategies
    from roostoo.backtest.metrics import report
    from roostoo.backtest.simulator import Costs, simulate

    grid = {k: json.loads(v) for k, v in (g.split("=", 1) for g in args.grid)}
    fixed = {k: json.loads(v) for k, v in (f.split("=", 1) for f in args.fixed or [])}
    bars, tradable, eligible, close_1m = load_any(args.universe)
    opens = pd.DataFrame({s: b["open"] for s, b in bars.items()})
    closes = pd.DataFrame({s: b["close"] for s, b in bars.items()})
    costs = Costs(config.TAKER_FEE, default_slippage(args.universe))

    def tgt(params):
        t = strategies.generate(args.model, bars, params, eligible=eligible)
        return t.reindex(columns=list(bars)).where(tradable, 0.0).fillna(0.0)

    def run(t):
        return simulate(opens, closes, t, costs=costs, max_gross_exposure=config.MAX_PORTFOLIO_EXPOSURE)

    combos = [{**fixed, **dict(zip(grid, c))} for c in itertools.product(*grid.values())]
    targets = {json.dumps(c, sort_keys=True): tgt(c) for c in combos}
    daily = {k: run(t).equity.resample("1D").last() for k, t in targets.items()}

    blocks = pd.date_range(args.start, args.end, freq=f"{args.step_months}MS", tz="UTC")
    stitched = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    picks = []
    for b0, b1 in zip(blocks[:-1], blocks[1:]):
        scores = {k: _sharpe(d[d.index < b0]) for k, d in daily.items()}
        best = max(scores, key=scores.get)
        picks.append({"block_start": str(b0.date()), "chosen": best, "train_sharpe": round(scores[best], 3)})
        sl = (closes.index >= b0) & (closes.index < b1)
        stitched.loc[sl] = targets[best].loc[sl]

    frozen = targets.get(json.dumps({**fixed, **json.loads(args.frozen)}, sort_keys=True))
    if frozen is None:
        frozen = tgt({**fixed, **json.loads(args.frozen)})
    span = (closes.index >= blocks[0]) & (closes.index < blocks[-1])
    rows = []
    for name, t in [("walk_forward", stitched), ("frozen", frozen)]:
        res = simulate(opens[span], closes[span], t[span], costs=costs,
                       max_gross_exposure=config.MAX_PORTFOLIO_EXPOSURE)
        r = report(res, bar_minutes=60)
        if close_1m is not None:
            e = pd.concat([pd.Series([res.initial_capital]), minute_equity(res, close_1m).reset_index(drop=True)])
            r["max_drawdown"] = float((e / e.cummax() - 1).min())
        rows.append({"run": name, **{k: r[k] for k in ["total_return", "annualized_return", "sharpe_daily",
                                                      "sortino_daily", "max_drawdown", "n_trades"]}})
    out = LONG_DIR / args.id
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(picks).to_csv(out / "picks.csv", index=False)
    pd.DataFrame(rows).to_csv(out / "summary.csv", index=False)
    (out / "meta.json").write_text(json.dumps({"id": args.id, "model": args.model, "grid": grid, "fixed": fixed,
                                               "frozen": json.loads(args.frozen), "start": args.start,
                                               "end": args.end, "kind": "walkforward"}, indent=2))
    print(pd.DataFrame(picks).to_string(index=False))
    print(pd.DataFrame(rows).round(3).to_string(index=False))


if __name__ == "__main__":
    main()

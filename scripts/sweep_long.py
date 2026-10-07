"""
Parameter grid for one hourly model on the long history, portfolio only,
loading the data once. Writes experiments/long/<id>/summary.csv: one row
per (parameter set, period).

Run from the project root:

    .venv/bin/python scripts/sweep_long.py --id G01_m1_trend --model m1_reversal_trend \
        --grid trend_days=[20,50,100,200]
    .venv/bin/python scripts/sweep_long.py --id G02_m2 --model m2_xs_momentum \
        --grid lookback_days=[7,14,28,56] --grid top_n=[2,3,4] --periods train validation
"""
import argparse
import itertools
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import LONG_PERIODS, register_package  # noqa: E402
from run_long import LONG_DIR, UNIVERSE_CHOICES, default_slippage, evaluate, load_any  # noqa: E402

COLS = ["total_return", "annualized_return", "max_drawdown", "sharpe_daily", "sortino_daily", "calmar",
        "volatility_annual", "profit_factor", "win_rate", "n_trades", "avg_gross_exposure", "turnover",
        "total_costs", "alpha_tstat", "beta", "w14_pct_positive", "w14_median_return", "w14_p05_return",
        "w14_median_score"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--universe", default="large_caps_2022", choices=UNIVERSE_CHOICES)
    ap.add_argument("--grid", action="append", required=True, help="key=[v1,v2,...] (JSON list), repeatable")
    ap.add_argument("--fixed", action="append", help="key=value held constant (JSON value)")
    ap.add_argument("--periods", nargs="+", default=["train"], choices=LONG_PERIODS)
    ap.add_argument("--fee", type=float, default=None)
    ap.add_argument("--slippage", type=float, default=None)
    args = ap.parse_args()

    register_package()
    from roostoo import config, strategies

    grid = {k: json.loads(v) for k, v in (g.split("=", 1) for g in args.grid)}
    fixed = {k: json.loads(v) for k, v in (f.split("=", 1) for f in args.fixed or [])}
    fee = config.TAKER_FEE if args.fee is None else args.fee
    slippage = default_slippage(args.universe) if args.slippage is None else args.slippage
    bars, tradable, eligible, close_1m = load_any(args.universe)

    rows = []
    for combo in itertools.product(*grid.values()):
        params = {**fixed, **dict(zip(grid, combo))}
        t = strategies.generate(args.model, bars, params, eligible=eligible)
        t = t.reindex(columns=list(bars)).where(tradable, 0.0).fillna(0.0)
        m, _, _ = evaluate(t, bars, fee, slippage, args.periods, per_asset=False, close_1m=close_1m,
                           eligible=eligible)
        for _, r in m.iterrows():
            rows.append({**params, "period": r["period"], **{c: r.get(c) for c in COLS}})
        print(json.dumps(params), " ".join(f"{r['period']}: SR {r['sharpe_daily']:.2f} ret {r['total_return']:+.1%}"
                                           f" dd {r['max_drawdown']:.1%}" for _, r in m.iterrows()), flush=True)

    out = LONG_DIR / args.id
    out.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "summary.csv", index=False)
    (out / "meta.json").write_text(json.dumps({
        "id": args.id, "model": args.model, "universe": args.universe, "grid": grid, "fixed": fixed,
        "periods": args.periods, "fee_per_side": fee, "slippage_per_side": slippage, "kind": "sweep"}, indent=2))
    for period in args.periods:
        sub = df[df.period == period].set_index(list(grid))
        print(f"\n{period}: daily Sharpe\n" + sub["sharpe_daily"].round(2).to_string())
    print(f"\nsaved -> {out / 'summary.csv'}")


if __name__ == "__main__":
    main()

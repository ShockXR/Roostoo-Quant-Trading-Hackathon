"""Mid-cap strategy grid on Binance hourly history, scored like the competition.

Periods (UTC): research 2026-04-08 to 08-01 (after a 7-day indicator warm-up),
validation 08-01 to 09-01, holdout 09-01 to 10-05. Ranking uses research
only. --validate re-runs the top configurations on validation; --holdout adds
the holdout and should be run once, for the final choice.

Every configuration is scored on its full window and on fresh, flat-start
14-day blocks (the competition length), at base cost and at 2x cost.

    python -m roostoo_bot.scripts.midcap_research
    python -m roostoo_bot.scripts.midcap_research --validate 5
"""
import argparse
import math
from pathlib import Path
import pandas as pd
from ..backtest.portfolio import DEFAULT_COST_PER_SIDE, block_scores, evaluate, load_hourly_panel, simulate
from ..strategies.midcap import Spec, build_weights

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "binance_data" / "processed" / "parquet"
RESULTS_DIR = ROOT / "binance_data" / "results"
SYMBOLS = ["ENAUSDT", "FETUSDT", "WLDUSDT", "UNIUSDT", "TAOUSDT", "ONDOUSDT", "AAVEUSDT", "VIRTUALUSDT", "HBARUSDT",
           "ARBUSDT", "ICPUSDT", "FILUSDT", "SEIUSDT", "APTUSDT", "ASTERUSDT", "XLMUSDT", "PUMPUSDT"]
BLEND_PARTS = [{"signal": "momentum", "params": {"lookback": 24, "top_k": 5}},
               {"signal": "momentum", "params": {"lookback": 168, "top_k": 3}}]
PERIODS = {"research": ("2026-04-08", "2026-08-01"), "validation": ("2026-08-01", "2026-09-01"),
           "holdout": ("2026-09-01", "2026-10-05")}


def grid():
    specs = [Spec("ew_hold", "equal_weight", target_daily_vol=None), Spec("ew_vol", "equal_weight")]
    for lookback in (24, 72, 168):
        for top_k in (3, 5):
            for every in (8, 24):
                specs.append(Spec(f"mom_{lookback}_{top_k}_{every}", "momentum", {"lookback": lookback, "top_k": top_k}, every))
    for lookback in (24, 72):
        for top_k in (3, 5):
            for every in (8, 24):
                specs.append(Spec(f"rev_{lookback}_{top_k}_{every}", "reversal", {"lookback": lookback, "top_k": top_k}, every))
    for ma in (72, 168):
        for every in (8, 24):
            specs.append(Spec(f"trend_{ma}_{every}", "trend", {"ma_hours": ma}, every))
    for channel in (48, 168):
        specs.append(Spec(f"brk_{channel}", "breakout", {"channel_hours": channel}, every=1))
    # Added after the first grid: a 1% volatility benchmark and a two-lookback momentum blend.
    specs.append(Spec("ew_v10", "equal_weight", target_daily_vol=.01))
    for vol in (.01, .015):
        specs.append(Spec(f"blend_v{int(vol * 1000)}", "blend", {"parts": BLEND_PARTS}, 24, vol))
    return specs


def score(panel, spec, weights, period, cost):
    start, end = PERIODS[period]
    full = evaluate(simulate(panel, weights, start, end, cost_per_side=cost), start=start)
    blocks = block_scores(panel, weights, start, end, cost_per_side=cost)
    composite = pd.to_numeric(blocks.composite, errors="coerce")
    return {"name": spec.name, "label": spec.label(), "period": period, "cost": cost,
            **{f"full_{k}": v for k, v in full.items()},
            "blocks": len(blocks), "block_composite_median": composite.median(),
            "block_composite_min": composite.min(), "blocks_positive": int((composite > 0).sum()),
            "blocks_undefined": int(composite.isna().sum()),
            "block_return_median": blocks.total_return.median(), "block_drawdown_worst": blocks.max_drawdown.min(),
            "block_active_days_min": float((blocks.active_day_fraction * blocks.n_days).min())}


def run(panel, specs, periods, costs):
    rows = []
    for number, spec in enumerate(specs, 1):
        weights = build_weights(panel, spec)
        for period in periods:
            for cost in costs:
                rows.append(score(panel, spec, weights, period, cost))
        print(f"[{number}/{len(specs)}] {spec.name}", flush=True)
    return pd.DataFrame(rows)


def show(table, period, cost):
    view = table[(table.period == period) & (table.cost == cost)].sort_values("block_composite_median", ascending=False)
    cols = ["name", "block_composite_median", "blocks_positive", "blocks", "block_return_median", "block_drawdown_worst",
            "full_composite", "full_total_return", "full_max_drawdown", "full_avg_gross", "full_fills_per_day",
            "full_turnover_per_day", "full_cost_fraction"]
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.float_format", "{:.4f}".format):
        print(f"\n{period}, cost {cost:.4f}/side, ranked by median 14-day composite:")
        print(view[cols].to_string(index=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--validate", type=int, default=0, help="also run the top N research configurations on validation")
    parser.add_argument("--holdout", nargs="*", default=None, help="config names to run once on the holdout")
    parser.add_argument("--extra", nargs="*", default=None,
                        help="config names to test with overlay and long/short variants (default: top 3)")
    args = parser.parse_args(argv)

    panel = load_hourly_panel(SYMBOLS, args.data_dir)
    print(f"panel: {panel.signal.shape[1]} symbols, {panel.signal.index[0]} -> {panel.signal.index[-1]}")
    costs = (DEFAULT_COST_PER_SIDE, 2 * DEFAULT_COST_PER_SIDE)
    specs = grid()
    table = run(panel, specs, ["research"], costs)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS_DIR / "midcap_research.csv", index=False)
    show(table, "research", costs[0])

    base = table[(table.period == "research") & (table.cost == costs[0])].sort_values("block_composite_median", ascending=False)
    by_name = {s.name: s for s in specs}
    if args.extra is not None:
        extra = []
        for name in args.extra or list(base.name.head(3)):
            spec = by_name[name]
            for vol in (0.01, 0.02):
                extra.append(Spec(f"{name}_v{int(vol * 1000)}", spec.signal, spec.params, spec.every, vol))
            extra.append(Spec(f"{name}_gate", spec.signal, spec.params, spec.every, spec.target_daily_vol, gate={}))
            if spec.signal == "momentum":
                extra.append(Spec(f"{name}_ls", spec.signal, {**spec.params, "long_short": True}, spec.every))
        more = run(panel, extra, ["research"], costs)
        more.to_csv(RESULTS_DIR / "midcap_research_extra.csv", index=False)
        show(more, "research", costs[0])
        by_name.update({s.name: s for s in extra})
    if args.validate:
        chosen = [by_name[n] for n in base.name.head(args.validate)]
        if args.extra is not None:
            chosen += [s for s in extra if s.name not in {c.name for c in chosen}]
        valid = run(panel, chosen, ["validation"], costs)
        valid.to_csv(RESULTS_DIR / "midcap_validation.csv", index=False)
        show(valid, "validation", costs[0])
    if args.holdout:
        held = run(panel, [by_name[n] for n in args.holdout], ["holdout"], costs)
        held.to_csv(RESULTS_DIR / "midcap_holdout.csv", index=False)
        show(held, "holdout", costs[0])
        show(held, "holdout", costs[1])


if __name__ == "__main__":
    main()

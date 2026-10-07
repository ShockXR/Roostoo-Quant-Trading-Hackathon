"""
Runs one strategy configuration over the five large caps and writes a
full, machine-readable report to experiments/<id>/.

For every period in scripts/common.PERIODS (train / validation / test /
full) it simulates:
  - each asset standalone ($100k, the asset's own target weights), and
  - PORTFOLIO: one $100k account trading all five at once, gross
    exposure capped at config.MAX_PORTFOLIO_EXPOSURE.

Signals are generated once over the whole history. That is legitimate
because every target is causal (a target at t only uses bars
<= t); periods are then evaluated by slicing with fresh capital.

Run from the project root:

    # final strategy (parameters from config.REVERSAL_*), every period
    .venv/bin/python scripts/run_backtest.py --id F01_final --periods train validation test full
    # original bot (slow: walk-forward random forest)
    .venv/bin/python scripts/run_backtest.py --id E01_baseline --strategy baseline \
        --periods train validation test full
    # one parameter changed, train + validation only (the default)
    .venv/bin/python scripts/run_backtest.py --id X --strategy reversal --param k=1.75
    # mid caps (results kept apart under experiments/midcaps/)
    .venv/bin/python scripts/run_backtest.py --id midcaps/MF01_final --universe mid_caps \
        --periods train validation test full
    # stress test: re-simulate cached targets with higher costs
    .venv/bin/python scripts/run_backtest.py --id S --targets-from F01_final --fee 0.002 \
        --periods train validation test full
"""
import argparse
import datetime as dt
import json
import multiprocessing as mp
import subprocess
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import (EXPERIMENTS_DIR, LARGE_CAPS, PERIODS, UNIVERSES, load_ohlcv,  # noqa: E402
                    period_slice, register_package, to_pair)

warnings.filterwarnings("ignore", category=ConvergenceWarning)

_STATE: dict = {}


def _parse_params(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def context_raw(symbol: str, closes: dict) -> dict:
    """Price history the ML features of `symbol` are built from: the five
    majors plus the symbol itself, as scripts/backtest_sol.py does (its
    CONTEXT_SYMBOLS). BTC is config.MARKET_PROXY. For a large cap this is
    exactly the five large caps."""
    symbols = LARGE_CAPS + ([symbol] if symbol not in LARGE_CAPS else [])
    return {to_pair(s): closes[s].rename("last_price").rename_axis("timestamp").reset_index()
            for s in symbols}


def _signal_worker(symbol: str) -> tuple[str, pd.Series]:
    from roostoo import strategy
    from roostoo.features.build_features import build_price_panel
    closes, ohlcv, name, params = (_STATE[k] for k in ("closes", "ohlcv", "strategy", "params"))
    raw = context_raw(symbol, closes)
    target = strategy.generate_targets(name, to_pair(symbol), raw, build_price_panel(raw),
                                       ohlcv[symbol], params)
    return symbol, target.reindex(ohlcv[symbol].index).fillna(0.0)


def build_targets(name: str, params: dict, ohlcv: dict, jobs: int) -> pd.DataFrame:
    closes = {s: load_ohlcv(s)["close"] for s in LARGE_CAPS if s not in ohlcv}
    closes.update({s: df["close"] for s, df in ohlcv.items()})
    _STATE.update(closes=closes, ohlcv=ohlcv, strategy=name, params=params)
    if jobs <= 1:
        results = dict(map(_signal_worker, list(ohlcv)))
    else:
        # fork: children inherit the config overrides and the loaded data
        with mp.get_context("fork").Pool(min(jobs, len(ohlcv))) as pool:
            results = dict(pool.map(_signal_worker, list(ohlcv)))
    return pd.DataFrame({s: results[s] for s in ohlcv})


def evaluate(targets: pd.DataFrame, ohlcv: dict, fee: float, slippage: float,
             periods: list[str], capital: float = 100_000.0) -> tuple[pd.DataFrame, dict]:
    from roostoo import config
    from roostoo.backtest import engine
    from roostoo.backtest.metrics import report
    from roostoo.backtest.simulator import Costs, simulate

    opens = pd.DataFrame({s: d["open"] for s, d in ohlcv.items()})
    closes = pd.DataFrame({s: d["close"] for s, d in ohlcv.items()})
    costs = Costs(fee=fee, slippage=slippage)
    rows, trades = [], {}
    for period in periods:
        o, c, w = (period_slice(x, period) for x in (opens, closes, targets))
        for sym in list(ohlcv) + ["PORTFOLIO"]:
            cols = list(ohlcv) if sym == "PORTFOLIO" else [sym]
            res = simulate(o[cols], c[cols], w[cols], initial_capital=capital, costs=costs,
                           max_gross_exposure=config.MAX_PORTFOLIO_EXPOSURE if sym == "PORTFOLIO" else None)
            # benchmark: the asset itself, or an equal-weight basket for the portfolio
            bench = (c[cols] / c[cols].iloc[0]).mean(axis=1)
            row = {"period": period, "asset": sym, **report(res, bench)}
            if sym != "PORTFOLIO":
                # what the original vectorized engine says for the same signal
                legacy = engine.summarize(engine.simulate(c[sym], w[sym]))
                row["legacy_total_return"] = legacy["total_net_return"]
                row["legacy_max_drawdown"] = legacy["max_drawdown"]
                row["legacy_n_trades_proxy"] = int(legacy["n_trades_proxy"])
                row["buy_hold_return"] = float(c[sym].iloc[-1] / c[sym].iloc[0] - 1)
            rows.append(row)
            trades[(period, sym)] = res.trades
    return pd.DataFrame(rows), trades


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True, help="experiment id, also the output folder name")
    ap.add_argument("--universe", default="large_caps", choices=UNIVERSES,
                    help="assets to trade (scripts/common.UNIVERSES)")
    ap.add_argument("--strategy", default="reversal", help="see strategy.STRATEGIES")
    ap.add_argument("--param", action="append", help="key=value (JSON value), repeatable")
    ap.add_argument("--periods", nargs="+", default=["train", "validation"], choices=PERIODS,
                    help="test is excluded by default -- it is only read for the final comparison")
    ap.add_argument("--fee", type=float, default=None, help="per side; default config.TAKER_FEE")
    ap.add_argument("--slippage", type=float, default=None,
                    help="per side; default config.SLIPPAGE_ESTIMATE / 2")
    ap.add_argument("--reuse-targets", action="store_true", help="reuse cached targets.parquet")
    ap.add_argument("--targets-from", help="experiment id whose cached targets to reuse (cost stress tests)")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--delay", type=int, default=0,
                    help="extra bars between decision and fill (latency stress test)")
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--note", default="", help="hypothesis / free text stored with the result")
    args = ap.parse_args()

    params = _parse_params(args.param)
    register_package()
    from roostoo import config
    # config values are bound as default args at import time in several
    # modules, so overrides must happen before importing them
    if "horizon" in params:
        config.TARGET_HORIZON = int(params["horizon"])
    for k, v in params.get("config", {}).items():
        setattr(config, k, v)

    out = EXPERIMENTS_DIR / args.id
    out.mkdir(parents=True, exist_ok=True)
    ohlcv = {s: load_ohlcv(s) for s in UNIVERSES[args.universe]}

    cache_dir = EXPERIMENTS_DIR / (args.targets_from or args.id)
    cache = cache_dir / "targets.parquet"
    started = dt.datetime.now()
    if (args.reuse_targets or args.targets_from) and cache.exists():
        targets = pd.read_parquet(cache)
    else:
        targets = build_targets(args.strategy, params, ohlcv, args.jobs)
        targets.to_parquet(out / "targets.parquet")
    fee = config.TAKER_FEE if args.fee is None else args.fee
    slippage = config.SLIPPAGE_ESTIMATE / 2 if args.slippage is None else args.slippage

    if args.delay:
        targets = targets.shift(args.delay).fillna(0.0)
    metrics, trades = evaluate(targets, ohlcv, fee, slippage, args.periods, args.capital)
    metrics.to_csv(out / "metrics.csv", index=False)
    for (period, sym), tr in trades.items():
        if sym == "PORTFOLIO" and not tr.empty:
            tr.to_csv(out / f"trades_{period}.csv", index=False)

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    meta = {
        "id": args.id, "universe": args.universe, "strategy": args.strategy, "params": params, "periods": args.periods,
        "fee_per_side": fee, "slippage_per_side": slippage,
        "initial_capital": args.capital, "execution_delay_bars": args.delay,
        "max_position_per_asset": config.MAX_POSITION_PER_ASSET,
        "max_portfolio_exposure": config.MAX_PORTFOLIO_EXPOSURE,
        "target_horizon": config.TARGET_HORIZON, "targets_from": args.targets_from,
        "run_at": started.isoformat(timespec="seconds"),
        "runtime_s": round((dt.datetime.now() - started).total_seconds(), 1),
        "git_commit": commit, "note": args.note,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    cols = ["period", "asset", "total_return", "max_drawdown", "sharpe_daily", "sortino_daily",
            "profit_factor", "win_rate", "n_trades", "avg_gross_exposure", "beta", "alpha_annual",
            "alpha_tstat", "fees_paid"]
    with pd.option_context("display.width", 220, "display.max_columns", None,
                           "display.float_format", "{:.4f}".format):
        print(metrics[cols].to_string(index=False))
    print(f"\nsaved -> {out.relative_to(EXPERIMENTS_DIR.parent)}  ({meta['runtime_s']}s)")


if __name__ == "__main__":
    main()

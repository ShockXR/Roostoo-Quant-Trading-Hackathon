"""
Long-history backtest (2022-01 .. 2026-09) of an hourly strategy from
strategies.MODELS on the point-in-time large-cap universe, written to
experiments/long/<id>/.

Same execution model as scripts/run_backtest.py: backtest/simulator.py
holds units, fills a target decided at bar t's close at bar t+1's open,
charges fee and slippage per side, and caps gross exposure at
config.MAX_POSITION_PER_ASSET / MAX_PORTFOLIO_EXPOSURE. Bars are hourly
(built from 1m candles, strategies/features.py); every model only changes
its target at hourly closes, so fills are the same as on 1m bars.

Signals are generated once over the whole history (every target is causal)
and each period is simulated with fresh capital. LUNA is force-closed at
its last Binance bar and excluded afterwards (delisting).

Run from the project root:

    .venv/bin/python scripts/run_long.py --id L00_m0 --model m0_reversal
    .venv/bin/python scripts/run_long.py --id L01_m1 --model m1_reversal_trend --param trend_days=100
    .venv/bin/python scripts/run_long.py --id S --targets-from L00_m0 --fee 0.002
"""
import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (EXPERIMENTS_DIR, HOURLY_CACHE_DIR, LONG_DATA_DIR, LONG_PERIODS,  # noqa: E402
                    LONG_UNIVERSES, load_ohlcv, period_slice, register_package)

LONG_DIR = EXPERIMENTS_DIR / "long"
START, END = pd.Timestamp(LONG_PERIODS["full"][0], tz="UTC"), pd.Timestamp(LONG_PERIODS["full"][1], tz="UTC")


def _parse_params(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def load_hourly(symbol: str) -> pd.DataFrame:
    """Hourly bars from the long 1m history, cached (gitignored)."""
    from roostoo.strategies.features import fill_minute_grid, hourly_bars
    cache = HOURLY_CACHE_DIR / f"{symbol}_1h.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    m1, filled = fill_minute_grid(load_ohlcv(symbol, LONG_DATA_DIR))
    bars = hourly_bars(m1)
    HOURLY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    bars.to_parquet(cache)
    log = HOURLY_CACHE_DIR / "filled_minutes.json"
    stats = json.loads(log.read_text()) if log.exists() else {}
    stats[symbol] = {"filled_minutes": filled, "first": str(m1.index[0]), "last": str(m1.index[-1])}
    log.write_text(json.dumps(stats, indent=2))
    return bars


def load_universe(universe: str) -> tuple[dict, pd.DataFrame]:
    """bars on one hourly index over the full window, plus a tradable mask.
    Outside an asset's own history its prices are held flat (ffill/bfill) and
    it is not tradable."""
    idx = pd.date_range(START, END, freq="1h", inclusive="left")
    bars, tradable = {}, {}
    for s in LONG_UNIVERSES[universe]:
        b = load_hourly(s)
        live = pd.Series((idx >= b.index[0]) & (idx <= b.index[-1]), index=idx)
        b = b.reindex(idx)
        b["close"] = b["close"].ffill().bfill()
        for col in ("open", "high", "low"):
            b[col] = b[col].fillna(b["close"])
        b["volume"] = b["volume"].fillna(0.0)
        bars[s], tradable[s] = b, live
    return bars, pd.DataFrame(tradable)


def minute_closes(universe: str) -> pd.DataFrame:
    """1m close panel over the full window (cached, float32), for marking
    hourly runs to the minute."""
    from roostoo.strategies.features import fill_minute_grid
    cache = HOURLY_CACHE_DIR / f"{universe}_close_1m.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    idx = pd.date_range(START, END, freq="1min", inclusive="left")
    cols = {s: fill_minute_grid(load_ohlcv(s, LONG_DATA_DIR))[0]["close"].reindex(idx).ffill().bfill()
            .astype("float32") for s in LONG_UNIVERSES[universe]}
    out = pd.DataFrame(cols)
    out.to_parquet(cache)
    return out


def minute_equity(res, close_1m: pd.DataFrame) -> pd.Series:
    """Equity marked every minute. The simulator fills at the hour's first
    open and holds units through the hour, so the units and cash held at an
    hourly close were also held for each minute of that hour."""
    c = res.weights.index
    px = close_1m.loc[c + pd.Timedelta("59min"), res.weights.columns].to_numpy(float)  # the bar close
    units = res.weights.to_numpy() * res.equity.to_numpy()[:, None] / px
    cash = res.equity.to_numpy() - (units * px).sum(axis=1)
    m = close_1m.loc[c[0]:c[-1] + pd.Timedelta("59min"), res.weights.columns]
    hour = m.index.floor("1h")
    pos = c.get_indexer(hour)
    return pd.Series(cash[pos] + (units[pos] * m.to_numpy(float)).sum(axis=1), index=m.index)


def window_stats(equity: pd.Series, days: int = 14) -> dict:
    """Distribution of `days`-day windows (one per start day) of a continuous
    equity curve: the competition is scored on a 14-day run."""
    d = equity.resample("1D").last().dropna()
    rows = []
    for i in range(len(d) - days):
        w = d.iloc[i:i + days + 1]
        r = w.pct_change().dropna()
        ret = w.iloc[-1] / w.iloc[0] - 1
        mdd = (w / w.cummax() - 1).min()
        sd, dd = r.std(), np.sqrt((np.minimum(r, 0) ** 2).mean())
        sharpe = r.mean() / sd * np.sqrt(365) if sd > 0 else 0.0
        sortino = r.mean() / dd * np.sqrt(365) if dd > 0 else 0.0
        ann = (1 + ret) ** (365 / days) - 1
        calmar = ann / abs(mdd) if mdd < 0 else 0.0
        rows.append((ret, mdd, sharpe, sortino, min(calmar, 100.0)))
    if not rows:
        return {}
    a = pd.DataFrame(rows, columns=["ret", "mdd", "sharpe", "sortino", "calmar"])
    score = 0.4 * a.sortino + 0.3 * a.sharpe + 0.3 * a.calmar
    return {
        f"w{days}_n": len(a),
        f"w{days}_pct_positive": float((a.ret > 0).mean()),
        f"w{days}_median_return": float(a.ret.median()),
        f"w{days}_p05_return": float(a.ret.quantile(0.05)),
        f"w{days}_median_mdd": float(a.mdd.median()),
        f"w{days}_p05_mdd": float(a.mdd.quantile(0.05)),
        f"w{days}_median_score": float(score.median()),
        f"w{days}_mean_score": float(score.mean()),
    }


def evaluate(targets: pd.DataFrame, bars: dict, fee: float, slippage: float, periods: list[str],
             per_asset: bool, close_1m: pd.DataFrame, capital: float = 100_000.0):
    from roostoo import config
    from roostoo.backtest.metrics import report
    from roostoo.backtest.simulator import Costs, simulate

    opens = pd.DataFrame({s: b["open"] for s, b in bars.items()})
    closes = pd.DataFrame({s: b["close"] for s, b in bars.items()})
    costs = Costs(fee=fee, slippage=slippage)
    rows, trades, curves = [], {}, {}
    for period in periods:
        o, c, w = (period_slice(x, period, LONG_PERIODS) for x in (opens, closes, targets))
        syms = list(bars) + ["PORTFOLIO"] if per_asset else ["PORTFOLIO"]
        for sym in syms:
            cols = list(bars) if sym == "PORTFOLIO" else [sym]
            res = simulate(o[cols], c[cols], w[cols], initial_capital=capital, costs=costs,
                           max_gross_exposure=config.MAX_PORTFOLIO_EXPOSURE if sym == "PORTFOLIO" else None)
            bench = (c[cols] / c[cols].iloc[0]).mean(axis=1)
            row = {"period": period, "asset": sym, **report(res, bench, bar_minutes=60)}
            if sym == "PORTFOLIO":
                # drawdown and Calmar from minute marks (hourly marks hide intra-hour dips)
                eq1m = minute_equity(res, close_1m)
                row["max_drawdown_hourly"] = row["max_drawdown"]
                e = pd.concat([pd.Series([capital]), eq1m.reset_index(drop=True)])
                row["max_drawdown"] = float((e / e.cummax() - 1).min())
                row["calmar"] = row["annualized_return"] / abs(row["max_drawdown"]) if row["max_drawdown"] < 0 else 0.0
                row.update(window_stats(res.equity))
                trades[period] = res.trades
                curves[period] = res.equity
            else:
                row["buy_hold_return"] = float(c[sym].iloc[-1] / c[sym].iloc[0] - 1)
            rows.append(row)
    return pd.DataFrame(rows), trades, curves


def monthly_returns(equity: pd.Series) -> pd.Series:
    m = equity.resample("ME").last()
    return m.pct_change().fillna(m.iloc[0] / equity.iloc[0] - 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True, help="experiment id; output under experiments/long/<id>")
    ap.add_argument("--model", default="m0_reversal", help="see strategies.MODELS")
    ap.add_argument("--universe", default="large_caps_2022", choices=LONG_UNIVERSES)
    ap.add_argument("--param", action="append", help="key=value (JSON value), repeatable")
    ap.add_argument("--periods", nargs="+", default=["train", "validation"], choices=LONG_PERIODS,
                    help="test and holdout are excluded by default")
    ap.add_argument("--fee", type=float, default=None, help="per side; default config.TAKER_FEE")
    ap.add_argument("--slippage", type=float, default=None, help="per side; default config.SLIPPAGE_ESTIMATE / 2")
    ap.add_argument("--targets-from", help="experiment id whose cached targets to reuse")
    ap.add_argument("--delay", type=int, default=0, help="extra hours between decision and fill")
    ap.add_argument("--no-per-asset", action="store_true", help="portfolio only (faster)")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    params = _parse_params(args.param)
    register_package()
    from roostoo import config, strategies

    out = LONG_DIR / args.id
    out.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now()
    bars, tradable = load_universe(args.universe)
    cache = LONG_DIR / (args.targets_from or args.id) / "targets.parquet"
    if args.targets_from:
        targets = pd.read_parquet(cache)
    else:
        targets = strategies.generate(args.model, bars, params)
        # not tradable -> flat; for a delisted asset this is the forced exit,
        # filled at the next bar's open = its last price
        targets = targets.reindex(columns=list(bars)).where(tradable, 0.0).fillna(0.0)
        targets.to_parquet(out / "targets.parquet")
    if args.delay:
        targets = targets.shift(args.delay).fillna(0.0)

    fee = config.TAKER_FEE if args.fee is None else args.fee
    slippage = config.SLIPPAGE_ESTIMATE / 2 if args.slippage is None else args.slippage
    metrics, trades, curves = evaluate(targets, bars, fee, slippage, args.periods, not args.no_per_asset,
                                       minute_closes(args.universe))
    metrics.to_csv(out / "metrics.csv", index=False)
    for period, tr in trades.items():
        tr.to_csv(out / f"trades_{period}.csv", index=False)
    eq = pd.DataFrame(curves)
    eq.to_csv(out / "equity.csv")
    if "full" in curves:
        monthly_returns(curves["full"]).rename("return").to_csv(out / "monthly_full.csv")

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    meta = {
        "id": args.id, "universe": args.universe, "model": args.model, "params": params,
        "periods": args.periods, "fee_per_side": fee, "slippage_per_side": slippage,
        "execution_delay_hours": args.delay, "targets_from": args.targets_from,
        "max_position_per_asset": config.MAX_POSITION_PER_ASSET,
        "max_portfolio_exposure": config.MAX_PORTFOLIO_EXPOSURE, "bar": "1h",
        "run_at": started.isoformat(timespec="seconds"),
        "runtime_s": round((dt.datetime.now() - started).total_seconds(), 1),
        "git_commit": commit, "note": args.note,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    port = metrics[metrics.asset == "PORTFOLIO"]
    cols = ["period", "total_return", "annualized_return", "max_drawdown", "sharpe_daily", "sortino_daily",
            "calmar", "profit_factor", "win_rate", "n_trades", "avg_gross_exposure", "alpha_tstat",
            "w14_pct_positive", "w14_median_score"]
    with pd.option_context("display.width", 250, "display.max_columns", None,
                           "display.float_format", "{:.4f}".format):
        print(port[cols].to_string(index=False))
        if len(metrics) > len(port):
            per = metrics[metrics.asset != "PORTFOLIO"].pivot(index="asset", columns="period", values="total_return")
            print("\nper-asset standalone return:\n" + per.to_string())
    print(f"\nsaved -> {out.relative_to(EXPERIMENTS_DIR.parent)}  ({meta['runtime_s']}s)")


if __name__ == "__main__":
    main()

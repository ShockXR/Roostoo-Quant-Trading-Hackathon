"""
Regime and drawdown breakdown of one or more experiments' PORTFOLIO
equity over the full window: daily PnL by market direction and by
volatility tercile (of the equal-weight basket of the experiment's own
assets, taken from its targets), losing
streaks, worst rolling windows, drawdown episodes and monthly returns.

Re-simulates from each experiment's cached targets.parquet with the same
costs the experiment used.

Run from the project root:

    .venv/bin/python scripts/regime_report.py F01_final E01_baseline B00_buy_hold_20pct
    .venv/bin/python scripts/regime_report.py midcaps/MF01_final midcaps/ME01_baseline \
        midcaps/MB00_buy_hold_20pct

Output: regime_report.json next to the first experiment's folder.
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import EXPERIMENTS_DIR, load_ohlcv, register_package  # noqa: E402


def _streak(flags) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def main():
    ids = sys.argv[1:] or ["F01_final", "E01_baseline", "B00_buy_hold_20pct"]
    register_package()
    from roostoo import config
    from roostoo.backtest.simulator import Costs, simulate

    targets_by_id = {exp: pd.read_parquet(EXPERIMENTS_DIR / exp / "targets.parquet") for exp in ids}
    symbols = list(targets_by_id[ids[0]].columns)
    if any(list(t.columns) != symbols for t in targets_by_id.values()):
        sys.exit("all experiments must trade the same assets (one universe per report)")
    ohlcv = {s: load_ohlcv(s) for s in symbols}
    opens = pd.DataFrame({s: d["open"] for s, d in ohlcv.items()})
    closes = pd.DataFrame({s: d["close"] for s, d in ohlcv.items()})
    basket = (closes / closes.iloc[0]).mean(axis=1)
    b_daily = basket.resample("1D").last().pct_change()
    b_daily.iloc[0] = basket.resample("1D").last().iloc[0] / basket.iloc[0] - 1
    # intraday realized vol of the basket per day (1m returns), a regime label
    # known only at the end of that day -- used for attribution, not trading
    b_vol = basket.pct_change().groupby(basket.index.floor("1D")).std()
    direction = pd.cut(b_daily, [-np.inf, -0.01, 0.01, np.inf], labels=["down >1%", "flat ±1%", "up >1%"])
    vol_regime = pd.qcut(b_vol, 3, labels=["low vol", "mid vol", "high vol"])

    report = {}
    for exp in ids:
        meta = json.loads((EXPERIMENTS_DIR / exp / "meta.json").read_text())
        targets = targets_by_id[exp]
        res = simulate(opens, closes, targets, costs=Costs(meta["fee_per_side"], meta["slippage_per_side"]),
                       max_gross_exposure=config.MAX_PORTFOLIO_EXPOSURE)
        eq = res.equity
        daily = eq.resample("1D").last()
        d_ret = daily.pct_change()
        d_ret.iloc[0] = daily.iloc[0] / res.initial_capital - 1
        d_pnl = daily.diff()
        d_pnl.iloc[0] = daily.iloc[0] - res.initial_capital
        dd = eq / eq.cummax() - 1
        underwater = dd < 0
        episodes = (underwater != underwater.shift()).cumsum()[underwater]
        durations = episodes.groupby(episodes).size() / 60.0  # hours

        frame = pd.DataFrame({"pnl": d_pnl, "ret": d_ret, "direction": direction, "vol": vol_regime})
        by = {}
        for key in ["direction", "vol"]:
            g = frame.groupby(key, observed=True)
            by[key] = pd.DataFrame({"days": g.size(), "pnl": g["pnl"].sum().round(0),
                                    "mean_daily_ret_bp": (g["ret"].mean() * 1e4).round(1),
                                    "positive_days": g["ret"].apply(lambda s: (s > 0).mean()).round(2)})
        weekly = daily.resample("W").last()
        rolling7 = daily.pct_change(7)
        monthly = daily.resample("MS").last()
        monthly_ret = monthly.pct_change()
        monthly_ret.iloc[0] = monthly.iloc[0] / res.initial_capital - 1
        stats = {
            "total_return": float(eq.iloc[-1] / res.initial_capital - 1),
            "max_drawdown": float(dd.min()),
            "drawdown_episodes": int(len(durations)),
            "longest_underwater_hours": float(durations.max()) if len(durations) else 0.0,
            "losing_days": int((d_ret < 0).sum()),
            "longest_losing_day_streak": _streak((d_ret < 0).to_numpy()),
            "worst_day": float(d_ret.min()),
            "worst_7d_window": float(rolling7.min()),
            "losing_weeks": int((weekly.pct_change().dropna() < 0).sum()),
            "monthly_returns": {k.strftime("%Y-%m"): float(v) for k, v in monthly_ret.items()},
        }
        report[exp] = {"stats": stats, **{k: v.reset_index().astype({k: str}).to_dict(orient="records")
                                          for k, v in by.items()}}
        print(f"\n===== {exp}")
        for k, v in stats.items():
            print(f"  {k:28s} {v}")
        for k, v in by.items():
            print(f"  -- by basket {k}\n" + v.to_string().replace("\n", "\n  "))

    out = (EXPERIMENTS_DIR / ids[0]).parent / "regime_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nsaved -> {out.relative_to(EXPERIMENTS_DIR.parent)}")


if __name__ == "__main__":
    main()

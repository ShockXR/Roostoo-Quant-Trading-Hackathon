"""
Diagnoses where an experiment makes and loses money, from its saved
portfolio trades (experiments/<id>/trades_<period>.csv).

Breakdowns: asset, month, hour of day (UTC), weekday, trend and
volatility regime at entry (both measured with data available at entry),
holding time, plus MFE/MAE (to judge stop / take-profit levels) and an
entry event study (to judge whether entries are early or late).

Run from the project root:

    .venv/bin/python scripts/analyze_trades.py --id E01_baseline --period train
"""
import argparse
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import EXPERIMENTS_DIR, load_ohlcv  # noqa: E402

TREND_BARS = 24 * 60     # 1-day trend: close vs its 1440-bar mean at entry
VOL_BARS = 24 * 60       # 1-day realized vol of 1m returns at entry
EVENT_WINDOW = (-240, 480)


def _group(trades: pd.DataFrame, key) -> pd.DataFrame:
    g = trades.groupby(key, observed=True)["pnl"]
    return pd.DataFrame({
        "trades": g.size(),
        "pnl": g.sum().round(2),
        "win_rate": g.apply(lambda s: (s > 0).mean()).round(3),
        "avg_pnl": g.mean().round(2),
    })


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True)
    ap.add_argument("--period", default="train")
    args = ap.parse_args()

    exp = EXPERIMENTS_DIR / args.id
    trades = pd.read_csv(exp / f"trades_{args.period}.csv", parse_dates=["entry_time", "exit_time"])
    if trades.empty:
        sys.exit("no trades")
    closes = {s: load_ohlcv(s)["close"] for s in trades["symbol"].unique()}

    rows = []
    paths = []
    for tr in trades.itertuples():
        c = closes[tr.symbol]
        i0 = c.index.get_loc(tr.entry_time)
        i1 = c.index.get_loc(tr.exit_time)
        # entry fills at open[i0] ~ close[i0-1]; use close[i0-1] as the reference
        ref = c.iloc[i0 - 1]
        path = c.iloc[i0:i1 + 1] / ref - 1
        hist = c.iloc[max(0, i0 - TREND_BARS):i0]
        rets = hist.pct_change().dropna()
        rows.append({
            "mfe": path.max(), "mae": path.min(),
            "gross_move": c.iloc[i1] / ref - 1,
            "trend_1d": ref / hist.mean() - 1 if len(hist) else np.nan,
            "ret_prior_4h": ref / c.iloc[max(0, i0 - 241)] - 1,
            "vol_1d": rets.std() if len(rets) > 10 else np.nan,
        })
        lo, hi = i0 - 1 + EVENT_WINDOW[0], i0 - 1 + EVENT_WINDOW[1]
        if lo >= 0 and hi < len(c):
            paths.append((c.iloc[lo:hi + 1].to_numpy() / ref - 1))
    trades = pd.concat([trades, pd.DataFrame(rows)], axis=1)
    trades["month"] = trades["entry_time"].dt.strftime("%Y-%m")
    trades["hour"] = trades["entry_time"].dt.hour
    trades["weekday"] = trades["entry_time"].dt.day_name().str[:3]
    trades["trend_regime"] = np.where(trades["trend_1d"] > 0, "up (close > 1d mean)", "down (close < 1d mean)")
    # vol tercile per symbol, so high-vol coins don't dominate one bucket
    trades["vol_regime"] = trades.groupby("symbol")["vol_1d"].transform(
        lambda s: pd.qcut(s, 3, labels=["low", "mid", "high"]) if s.nunique() >= 3 else "n/a")
    trades["hold_bucket"] = pd.cut(trades["bars"], [0, 30, 120, 240, 480, 1440, 10**6],
                                   labels=["<=30m", "30m-2h", "2-4h", "4-8h", "8-24h", ">24h"])
    trades["prior_4h_regime"] = pd.qcut(trades["ret_prior_4h"], 3, labels=["fell", "flat", "rose"])

    out = {}
    pd.set_option("display.width", 200)
    for name, key in [("asset", "symbol"), ("month", "month"), ("trend_regime", "trend_regime"),
                      ("vol_regime", "vol_regime"), ("prior_4h_move", "prior_4h_regime"),
                      ("hold_time", "hold_bucket"), ("weekday", "weekday"), ("hour_utc", "hour")]:
        t = _group(trades, key)
        out[name] = t.reset_index().astype({t.index.name or key: str}).to_dict(orient="records")
        print(f"\n== by {name} ==\n{t.to_string()}")

    win, loss = trades[trades.pnl > 0], trades[trades.pnl <= 0]
    excursions = {
        "median_mfe_winners": float(win["mfe"].median()) if len(win) else None,
        "median_mae_winners": float(win["mae"].median()) if len(win) else None,
        "median_mfe_losers": float(loss["mfe"].median()) if len(loss) else None,
        "median_mae_losers": float(loss["mae"].median()) if len(loss) else None,
        "losers_that_were_up_0.5pct": float((loss["mfe"] > 0.005).mean()) if len(loss) else None,
        "winners_that_were_down_0.5pct": float((win["mae"] < -0.005).mean()) if len(win) else None,
        "mean_gross_move": float(trades["gross_move"].mean()),
        "median_gross_move": float(trades["gross_move"].median()),
    }
    out["excursions"] = excursions
    print("\n== excursions (price move from entry reference) ==")
    for k, v in excursions.items():
        print(f"  {k:32s} {v:.5f}" if v is not None else f"  {k:32s} n/a")

    if paths:
        ev = pd.Series(np.mean(paths, axis=0), index=range(EVENT_WINDOW[0], EVENT_WINDOW[1] + 1))
        marks = [-240, -60, -10, 0, 10, 60, 120, 240, 480]
        out["event_study"] = {str(m): float(ev[m]) for m in marks}
        print("\n== event study: mean price move vs entry reference (bars relative to signal) ==")
        print("  " + "  ".join(f"{m:+d}:{ev[m]*1e4:+.1f}bp" for m in marks))

    (exp / f"analysis_{args.period}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nsaved -> {(exp / f'analysis_{args.period}.json').relative_to(EXPERIMENTS_DIR.parent)}")


if __name__ == "__main__":
    main()

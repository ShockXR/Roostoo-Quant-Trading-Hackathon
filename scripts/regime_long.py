"""
Regime breakdown of long-history runs, from each experiment's continuous
full-window portfolio equity (experiments/long/<id>/equity.csv, column
`full`). Regimes are labelled ex post from the equal-weight basket of the
universe; they describe the market, they are never used by a strategy.

  - year
  - month type: basket month return > +5% bull, < -5% bear, else sideways
  - volatility: tercile of the basket's trailing 30-day realized vol
  - trend: basket trailing 90-day return > +20% (strong uptrend), < -20%
    (strong downtrend), else neither
  - shock days: basket day return < -5% (crash) or > +5% (rally)

Run from the project root:

    .venv/bin/python scripts/regime_long.py L00_m0 L01_m1 L02_m2 B00_buy_hold

Output: experiments/long/regimes.csv (+ printed tables).
"""
import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import register_package  # noqa: E402
from run_long import LONG_DIR, load_any  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--universe", default="large_caps_2022")
    ap.add_argument("--out", default="regimes.csv")
    args = ap.parse_args()
    register_package()

    bars, tradable, eligible, _ = load_any(args.universe)
    close = pd.DataFrame({s: b["close"] for s, b in bars.items()})
    # basket of what was in the universe each day (eligible members for the
    # point-in-time mid caps, every listed asset for the large caps)
    daily_px = close.resample("1D").last()
    live = (tradable if eligible is None else eligible).resample("1D").last()
    daily_px, live = daily_px.loc["2022-01-01":], live.loc["2022-01-01":]
    basket_r = daily_px.pct_change().where(live).mean(axis=1).fillna(0.0)
    basket = (1 + basket_r).cumprod()

    month_r = basket.resample("ME").last().pct_change()
    month_r.iloc[0] = basket.resample("ME").last().iloc[0] - 1
    month_type = pd.cut(month_r, [-np.inf, -0.05, 0.05, np.inf], labels=["bear", "sideways", "bull"])
    vol30 = basket_r.rolling(30).std() * np.sqrt(365)
    vol_terc = pd.qcut(vol30, 3, labels=["low vol", "mid vol", "high vol"])
    tr90 = basket / basket.shift(90) - 1
    trend = pd.Series(np.where(tr90 > 0.2, "strong up", np.where(tr90 < -0.2, "strong down", "neither")),
                      index=basket.index).where(tr90.notna())
    shock = pd.Series(np.where(basket_r < -0.05, "crash day", np.where(basket_r > 0.05, "rally day", "normal")),
                      index=basket.index)

    labels = pd.DataFrame({"year": basket.index.year.astype(str), "volatility": vol_terc, "trend": trend,
                           "shock": shock}, index=basket.index)
    rows = []
    for exp in args.ids:
        eq = pd.read_csv(LONG_DIR / exp / "equity.csv", index_col=0, parse_dates=True)["full"].dropna()
        d = eq.resample("1D").last()
        r = d.pct_change()
        r.iloc[0] = d.iloc[0] / 100_000.0 - 1
        m = d.resample("ME").last().pct_change()
        m.iloc[0] = d.resample("ME").last().iloc[0] / 100_000.0 - 1
        for col in labels:
            for lab, g in r.groupby(labels[col].reindex(r.index)):
                rows.append({"experiment": exp, "dimension": col, "regime": lab, "days": len(g),
                             "mean_day_bp": g.mean() * 1e4, "total": float((1 + g).prod() - 1),
                             "sharpe": g.mean() / g.std() * np.sqrt(365) if g.std() > 0 else 0.0})
        for lab, g in m.groupby(month_type.reindex(m.index)):
            rows.append({"experiment": exp, "dimension": "month type", "regime": lab, "days": len(g),
                         "mean_day_bp": np.nan, "total": float((1 + g).prod() - 1),
                         "mean_month": g.mean(), "hit_rate": float((g > 0).mean())})
    out = pd.DataFrame(rows)
    out.to_csv(LONG_DIR / args.out, index=False)
    with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format):
        for dim in ["year", "month type", "trend", "volatility", "shock"]:
            sub = out[out.dimension == dim]
            val = "mean_month" if dim == "month type" else "total"
            print(f"\n== {dim}: {val}")
            print(sub.pivot(index="regime", columns="experiment", values=val)[args.ids].to_string())
            print(sub.pivot(index="regime", columns="experiment", values="days")[args.ids[0]].rename("n").to_string())
    print(f"\nsaved -> {LONG_DIR / args.out}")


if __name__ == "__main__":
    main()

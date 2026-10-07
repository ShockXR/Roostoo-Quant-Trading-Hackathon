"""
Checks the live loop's decision log (config.LIVE_DECISION_LOG) against the
backtest code path, hour by hour.

  1. targets: recomputes config.PRODUCTION_MODEL on Binance hourly bars for
     every logged bar (same fetch_hourly + strategies.generate as the live
     loop, history fetched once) and lists hours where live != recomputed;
  2. coverage: hours between the first and last logged bar with no "ok"
     record (stale data, errors, downtime);
  3. prices: Roostoo price the order was sized at vs the Binance open of the
     next bar (the backtest's fill price), in basis points;
  4. equity: logged equity vs a simulator replay of the recomputed targets
     from the first logged equity (fee config.TAKER_FEE, slippage
     config.SLIPPAGE_ESTIMATE / 2).

Run from the project root (needs network for Binance data):

    .venv/bin/python scripts/reconcile_live.py [--log data_store/live_decisions.jsonl]
"""
import argparse
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import register_package  # noqa: E402


def load_log(path: str) -> pd.DataFrame:
    rows = [json.loads(line) for line in pathlib.Path(path).read_text().splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    if "bar" in df:
        df["bar"] = pd.to_datetime(df["bar"], utc=True)
    return df


def recompute(bars_from: pd.Timestamp, bars_to: pd.Timestamp):
    """Targets (capped, Roostoo pair columns) and Binance opens/closes for
    every hour in [bars_from, bars_to], with enough history before it."""
    from roostoo import config, strategies
    from roostoo.data.binance_bars import fetch_hourly
    from roostoo.execution.rebalance import cap_gross
    span = (bars_to - bars_from).days + 1
    now_ms = int((bars_to + pd.Timedelta("2h")).timestamp() * 1000)   # include the bar after the last
    bars = {s: fetch_hourly(s, config.LIVE_HISTORY_DAYS + span + 1, now_ms) for s in config.PRODUCTION_SYMBOLS}
    idx = sorted(set.intersection(*(set(b.index) for b in bars.values())))
    bars = {s: b.loc[idx] for s, b in bars.items()}
    t = strategies.generate(config.PRODUCTION_MODEL, bars, config.PRODUCTION_PARAMS)
    t = t.rename(columns=config.PRODUCTION_SYMBOLS)
    t = pd.DataFrame([cap_gross(r.to_dict()) for _, r in t.iterrows()], index=t.index)
    opens = pd.DataFrame({config.PRODUCTION_SYMBOLS[s]: b["open"] for s, b in bars.items()})
    closes = pd.DataFrame({config.PRODUCTION_SYMBOLS[s]: b["close"] for s, b in bars.items()})
    return t, opens, closes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default=None, help="default config.LIVE_DECISION_LOG")
    ap.add_argument("--tolerance", type=float, default=1e-9, help="max |live - recomputed| target weight")
    args = ap.parse_args()
    register_package()
    from roostoo import config
    from roostoo.backtest.simulator import Costs, simulate

    df = load_log(args.log or config.LIVE_DECISION_LOG)
    ok = df[df.status == "ok"].drop_duplicates("bar", keep="last").set_index("bar").sort_index()
    print(f"log: {len(df)} records -- ok {len(ok)}, stale {int((df.status == 'stale').sum())}, "
          f"error {int((df.status == 'error').sum())}")
    if ok.empty:
        sys.exit("no ok records to reconcile")
    first, last = ok.index[0], ok.index[-1]
    t, opens, closes = recompute(first, last)

    # 1. targets
    mismatches = []
    for bar, rec in ok.iterrows():
        if bar not in t.index:
            mismatches.append((bar, "bar not in Binance data", {}))
            continue
        want = t.loc[bar]
        diff = {p: (w, float(want.get(p, 0.0))) for p, w in rec["targets"].items()
                if abs(w - float(want.get(p, 0.0))) > args.tolerance}
        if diff:
            mismatches.append((bar, "targets differ", diff))
    print(f"\n1. targets: {len(ok) - len(mismatches)}/{len(ok)} hours match the backtest code path")
    for bar, why, diff in mismatches[:20]:
        print(f"   {bar}  {why}  " + ", ".join(f"{p}: live {a:.4f} vs {b:.4f}" for p, (a, b) in diff.items()))

    # 2. coverage
    expected = pd.date_range(first, last, freq="1h")
    missing = expected.difference(ok.index)
    print(f"\n2. coverage: {len(expected) - len(missing)}/{len(expected)} hours with an ok record")
    if len(missing):
        print("   missing: " + ", ".join(str(b) for b in missing[:24]) + (" ..." if len(missing) > 24 else ""))

    # 3. prices
    gaps = []
    for bar, rec in ok.iterrows():
        nxt = bar + pd.Timedelta("1h")
        for o in rec.get("orders") or []:
            if nxt in opens.index and o["pair"] in opens:
                px = rec["prices"][o["pair"]]
                gaps.append(1e4 * (px / opens.loc[nxt, o["pair"]] - 1) * (1 if o["side"] == "BUY" else -1))
    if gaps:
        g = pd.Series(gaps)
        print(f"\n3. prices: {len(g)} orders; Roostoo vs Binance next open, cost-signed bp: "
              f"mean {g.mean():+.1f}, median {g.median():+.1f}, worst {g.max():+.1f} "
              f"(backtest assumes {1e4 * config.SLIPPAGE_ESTIMATE / 2:.1f} bp slippage)")
    else:
        print("\n3. prices: no orders in the log")

    # 4. equity
    span = (t.index >= first) & (t.index <= last + pd.Timedelta("1h"))
    capital = float(ok["equity"].iloc[0])
    res = simulate(opens[span], closes[span], t[span], initial_capital=capital,
                   costs=Costs(config.TAKER_FEE, config.SLIPPAGE_ESTIMATE / 2),
                   max_gross_exposure=config.MAX_PORTFOLIO_EXPOSURE)
    # a decision at bar b is logged with equity measured just after b closed, i.e. replay equity at b
    rep = res.equity.reindex(ok.index)
    live_eq = ok["equity"].astype(float)
    drift = (live_eq / rep - 1).dropna()
    print(f"\n4. equity: live {live_eq.iloc[0]:,.2f} -> {live_eq.iloc[-1]:,.2f} "
          f"({live_eq.iloc[-1] / live_eq.iloc[0] - 1:+.2%}); replay -> {rep.iloc[-1]:,.2f} "
          f"({rep.iloc[-1] / capital - 1:+.2%}); max |drift| {drift.abs().max():.2%}")


if __name__ == "__main__":
    main()

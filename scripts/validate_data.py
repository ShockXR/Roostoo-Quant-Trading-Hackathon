"""
Read-only audit of the processed Binance 1m candles for the five large caps.
Nothing is repaired: the report says what it finds and the backtest decides
what to do about it.

Run from the project root:

    .venv/bin/python scripts/validate_data.py                       # large caps
    .venv/bin/python scripts/validate_data.py --universe mid_caps
"""
import argparse
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "binance_data" / "processed" / "parquet"
sys.path.insert(0, str(ROOT / "scripts"))
from common import UNIVERSES  # noqa: E402

OUT = {"large_caps": ROOT / "experiments" / "data_validation.json",
       "mid_caps": ROOT / "experiments" / "midcaps" / "data_validation.json"}
OHLCV = ["open", "high", "low", "close", "volume"]


def audit(symbol: str) -> dict:
    df = pd.read_parquet(DATA_DIR / f"{symbol}_1m.parquet")
    t = df["open_time"]
    step = t.diff().dropna()
    expected = pd.date_range(t.min(), t.max(), freq="1min")
    ret = df["close"].pct_change()
    vol = df["volume"]
    # next bar's open vs this bar's close: tells us whether "fill at the
    # observed close" and "fill at the next open" are materially different
    gap = (df["open"].shift(-1) / df["close"] - 1).dropna()

    return {
        "symbol": symbol,
        "rows": len(df),
        "start": str(t.min()),
        "end": str(t.max()),
        "interval_mode": str(step.mode().iloc[0]),
        "irregular_intervals": int((step != pd.Timedelta("1min")).sum()),
        "missing_candles": int(len(expected.difference(t))),
        "duplicate_timestamps": int(t.duplicated().sum()),
        "monotonic_increasing": bool(t.is_monotonic_increasing),
        "null_values": int(df[OHLCV].isna().sum().sum()),
        "non_positive_prices": int((df[["open", "high", "low", "close"]] <= 0).sum().sum()),
        "negative_volume": int((vol < 0).sum()),
        "zero_volume_bars": int((vol == 0).sum()),
        "high_below_open_or_close": int((df["high"] < df[["open", "close"]].max(axis=1)).sum()),
        "low_above_open_or_close": int((df["low"] > df[["open", "close"]].min(axis=1)).sum()),
        "high_below_low": int((df["high"] < df["low"]).sum()),
        "close_time_mismatch": int(((df["close_time"] - df["open_time"]) > pd.Timedelta("1min")).sum()),
        "abs_ret_gt_2pct_bars": int((ret.abs() > 0.02).sum()),
        "max_abs_1m_return": float(ret.abs().max()),
        "max_abs_1m_return_at": str(t[ret.abs().idxmax()]),
        "ret_std_1m": float(ret.std()),
        "volume_gt_20x_median_bars": int((vol > 20 * vol.median()).sum()),
        "flat_bars_high_eq_low": int((df["high"] == df["low"]).sum()),
        "next_open_ne_close_frac": float((gap.abs() > 0).mean()),
        "next_open_vs_close_abs_mean_bps": float(gap.abs().mean() * 1e4),
        "first_close": float(df["close"].iloc[0]),
        "last_close": float(df["close"].iloc[-1]),
        "buy_hold_return": float(df["close"].iloc[-1] / df["close"].iloc[0] - 1),
        "monthly_returns": {
            m.strftime("%Y-%m"): float(g.iloc[-1] / g.iloc[0] - 1)
            for m, g in df.set_index("open_time")["close"].groupby(pd.Grouper(freq="MS"))
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default="large_caps", choices=UNIVERSES)
    args = ap.parse_args()
    out = OUT[args.universe]
    rows = [audit(s) for s in UNIVERSES[args.universe]]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    table = pd.DataFrame(rows).set_index("symbol").drop(columns=["monthly_returns"])
    with pd.option_context("display.width", 250, "display.max_columns", None):
        print(table.T.to_string())
        print("\nmonthly close-to-close returns:")
        print(pd.DataFrame({r["symbol"]: r["monthly_returns"] for r in rows}).round(4).to_string())
    print(f"\nsaved -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

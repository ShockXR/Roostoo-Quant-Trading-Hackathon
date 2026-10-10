"""Review the Oct 6+ live bot's trades: round trips, exit reasons, and what prices did before and after.

Reads the files the bot on `main` wrote (run on the EC2 instance, from the directory that holds them):
  data_store/trades_log.csv   timestamp (UTC ISO), pair, side, quantity, price ("MARKET" if unknown), execution_type, order_id
  logs/bot.log                exit triggers ("RULE 1: STOP-LOSS", "TRAILING RATCHET PROFIT", ...)
and Binance public 1h klines for the price path. Writes a markdown report.

  python -m roostoo_bot.scripts.review_live_trades --trades data_store/trades_log.csv --log logs/bot.log \
      --out logs/trade_review.md

Caveat: that bot logged an order even when the exchange refused it, so rows without an order id are
flagged "unconfirmed" and the reconstructed P&L is an estimate; the Roostoo app is the ground truth.
"""
import argparse
from datetime import datetime, timezone
import re
import time
import pandas as pd
import requests
from .. import config
from ..data.binance_live import binance_symbol, fetch_klines

HOUR_MS = 3_600_000
REASONS = [("STOP-LOSS", "stop-loss"), ("TRAILING RATCHET", "trailing profit"), ("TAKE-PROFIT", "take-profit"),
           ("RANK ROTATION", "rank rotation"), ("24H HOLD", "time expiry"), ("EXPIRY", "time expiry")]
LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def load_trades(path):
    frame = pd.read_csv(path)
    frame["time"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
    frame["side"] = frame["side"].str.upper()
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    order_id = frame["order_id"] if "order_id" in frame else pd.Series("", index=frame.index)
    frame["confirmed"] = ~order_id.fillna("").astype(str).str.strip().isin(["", "nan", "None"])
    return frame.sort_values("time").reset_index(drop=True)


def load_exit_reasons(path, offset_hours=0.):
    """[(utc time, pair, reason)] from SELL trigger lines in bot.log."""
    out = []
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        if "SELL" not in line:
            continue
        match = LOG_LINE.match(line)
        pair = re.search(r"\] ([A-Z0-9]+/USD)", line)
        if not match or not pair:
            continue
        reason = next((label for key, label in REASONS if key in line), "other")
        stamp = pd.Timestamp(match.group(1), tz="UTC") - pd.Timedelta(hours=offset_hours)
        out.append((stamp, pair.group(1), reason))
    return out


def hourly_closes(pair, start, end, cache):
    if pair in cache:
        return cache[pair]
    rows, cursor = [], int(start.value // 1_000_000)
    end_ms = int(end.value // 1_000_000)
    try:
        while cursor < end_ms:
            batch_end = min(end_ms, cursor + 1000 * HOUR_MS)
            batch = fetch_klines(binance_symbol(pair), batch_end, 1000)
            batch = [r for r in batch if int(r[0]) >= cursor]
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + HOUR_MS
            time.sleep(.1)
    except ValueError:
        rows = []
    series = pd.Series({pd.Timestamp(int(r[0]) + HOUR_MS, unit="ms", tz="UTC"): float(r[4]) for r in rows}).sort_index()
    cache[pair] = series
    return series


def price_at(series, when):
    if series.empty:
        return float("nan")
    after = series[series.index >= when]
    return float(after.iloc[0]) if len(after) else float("nan")


def round_trips(trades, reasons):
    """FIFO-pair buys and sells per coin. Partial sells split lots."""
    out = []
    for pair, group in trades.groupby("pair"):
        lots = []
        for row in group.itertuples():
            if row.side == "BUY":
                lots.append([row.time, row.price, row.quantity, row.confirmed])
                continue
            remaining = row.quantity
            reason = next((r for t, p, r in sorted(reasons, key=lambda x: abs((x[0] - row.time).total_seconds()))
                           if p == pair and abs((t - row.time).total_seconds()) <= 300), "unknown")
            while remaining > 1e-12 and lots:
                lot = lots[0]
                take = min(remaining, lot[2])
                out.append({"pair": pair, "entry_time": lot[0], "entry_price": lot[1], "exit_time": row.time,
                            "exit_price": row.price, "quantity": take, "reason": reason,
                            "confirmed": bool(lot[3] and row.confirmed)})
                lot[2] -= take
                remaining -= take
                if lot[2] <= 1e-12:
                    lots.pop(0)
        for lot in lots:
            out.append({"pair": pair, "entry_time": lot[0], "entry_price": lot[1], "exit_time": pd.NaT,
                        "exit_price": float("nan"), "quantity": lot[2], "reason": "open", "confirmed": bool(lot[3])})
    return pd.DataFrame(out)


def sleeve_of(pair):
    for sleeve, pairs in config.SLEEVE_PAIRS.items():
        if pair in pairs:
            return sleeve
    return "other"


def enrich(trips, now):
    cache = {}
    rows = []
    for trip in trips.to_dict("records"):
        start = trip["entry_time"] - pd.Timedelta(hours=30)
        series = hourly_closes(trip["pair"], start.floor("h"), now.floor("h"), cache)
        entry, exit_ = trip["entry_price"], trip["exit_price"]
        if pd.isna(entry):
            entry = price_at(series, trip["entry_time"])
        if trip["reason"] != "open" and pd.isna(exit_):
            exit_ = price_at(series, trip["exit_time"])
        last = float(series.iloc[-1]) if len(series) else float("nan")
        mark = exit_ if trip["reason"] != "open" else last
        cost = 2 * config.TAKER_FEE
        trip.update(entry_price=entry, exit_price=exit_, sleeve=sleeve_of(trip["pair"]),
                    pnl_pct=(mark / entry - 1 - cost) * 100, pnl_usd=trip["quantity"] * (mark - entry) - cost * trip["quantity"] * entry,
                    hold_h=((trip["exit_time"] if trip["reason"] != "open" else now) - trip["entry_time"]).total_seconds() / 3600,
                    pre_24h_pct=(entry / price_at(series, trip["entry_time"] - pd.Timedelta(hours=24)) - 1) * 100)
        if trip["reason"] != "open":
            for h in (24, 72):
                later = trip["exit_time"] + pd.Timedelta(hours=h)
                trip[f"post_{h}h_pct"] = (price_at(series, later) / exit_ - 1) * 100 if later <= now else float("nan")
            trip["now_vs_exit_pct"] = (last / exit_ - 1) * 100
        rows.append(trip)
    return pd.DataFrame(rows)


def report(trips, now):
    lines = [f"# Live trade review ({now:%Y-%m-%d %H:%M} UTC)", "",
             "P&L is reconstructed from the bot's own log at 0.20% round-trip fees; check totals against the Roostoo app.", ""]
    closed = trips[trips.reason != "open"]
    lines += ["## By sleeve (closed round trips)", "",
              "| Sleeve | Trips | Win rate | Avg win % | Avg loss % | Total P&L $ | Unconfirmed |", "|---|---|---|---|---|---|---|"]
    for sleeve, g in closed.groupby("sleeve"):
        wins, losses = g[g.pnl_pct > 0], g[g.pnl_pct <= 0]
        lines.append(f"| {sleeve} | {len(g)} | {len(wins) / len(g):.0%} | {wins.pnl_pct.mean():.2f} | {losses.pnl_pct.mean():.2f} | "
                     f"{g.pnl_usd.sum():,.0f} | {(~g.confirmed).sum()} |")
    lines += ["", "## By exit reason", "", "| Sleeve | Reason | Trips | Total P&L $ | Avg % | Avg move 24h after exit % | Avg move 72h after exit % |",
              "|---|---|---|---|---|---|---|"]
    for (sleeve, reason), g in closed.groupby(["sleeve", "reason"]):
        lines.append(f"| {sleeve} | {reason} | {len(g)} | {g.pnl_usd.sum():,.0f} | {g.pnl_pct.mean():.2f} | "
                     f"{g.get('post_24h_pct', pd.Series(dtype=float)).mean():.2f} | {g.get('post_72h_pct', pd.Series(dtype=float)).mean():.2f} |")
    lines += ["", "A positive move after a stop-loss exit means the stop sold before a rebound; a positive move after a "
              "profit exit means the take-profit left money on the table.", ""]
    lines += ["## Last time each large cap was traded", "", "| Pair | Last entry | 24h move before entry % | Exit | Reason | P&L % | Since exit % |",
              "|---|---|---|---|---|---|---|"]
    large = trips[trips.sleeve == "large"].sort_values("entry_time").groupby("pair").tail(1)
    for t in large.itertuples():
        lines.append(f"| {t.pair} | {t.entry_time:%m-%d %H:%M} | {t.pre_24h_pct:.2f} | "
                     f"{'' if pd.isna(t.exit_time) else f'{t.exit_time:%m-%d %H:%M}'} | {t.reason} | {t.pnl_pct:.2f} | "
                     f"{getattr(t, 'now_vs_exit_pct', float('nan')):.2f} |")
    lines += ["", "## Open lots", "", "| Pair | Sleeve | Entry | Entry price | Unrealized % |", "|---|---|---|---|---|"]
    for t in trips[trips.reason == "open"].itertuples():
        lines.append(f"| {t.pair} | {t.sleeve} | {t.entry_time:%m-%d %H:%M} | {t.entry_price:.6g} | {t.pnl_pct:.2f} |")
    lines += ["", "## Every round trip", "", "```", trips.round({c: 3 for c in trips.select_dtypes("number").columns}).to_string(index=False), "```"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trades", default="data_store/trades_log.csv")
    parser.add_argument("--log", default="logs/bot.log")
    parser.add_argument("--log-utc-offset-hours", type=float, default=0., help="bot.log timestamps minus UTC (0 on EC2)")
    parser.add_argument("--out", default="logs/trade_review.md")
    args = parser.parse_args()
    now = pd.Timestamp(datetime.now(timezone.utc))
    trips = round_trips(load_trades(args.trades), load_exit_reasons(args.log, args.log_utc_offset_hours))
    if trips.empty:
        raise SystemExit("no trades found")
    try:
        trips = enrich(trips, now)
    except requests.exceptions.RequestException as exc:
        raise SystemExit(f"Binance unreachable: {exc}")
    text = report(trips, now)
    with open(args.out, "w") as out:
        out.write(text)
    print(text)


if __name__ == "__main__":
    main()

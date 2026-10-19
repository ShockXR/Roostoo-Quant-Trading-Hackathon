"""
Standalone Offline Trades & PnL Audit Tool.
Requires NO active API connection and NO external dependencies (uses local parquet data).
Reconstructs all trade cycles from data_store/trades_log.csv, active_positions.json,
and data_store/*.parquet files.
"""
import glob
import json
import os
import sys
import numpy as np
import pandas as pd

STORE_DIR = "data_store"
TRADES_CSV = os.path.join(STORE_DIR, "trades_log.csv")
POS_FILE = os.path.join(STORE_DIR, "active_positions.json")
LOG_FILES = [os.path.join("logs", "bot.log"), "bot.log"]

# Cache for parquet dataframes
PARQUET_CACHE = {}


def get_parquet_price_at(pair: str, ts_val) -> float:
    safe = pair.replace("/", "_")
    p_file = os.path.join(STORE_DIR, f"{safe}.parquet")
    if not os.path.exists(p_file):
        return 0.0

    if pair not in PARQUET_CACHE:
        try:
            df = pd.read_parquet(p_file)
            if not df.empty and "timestamp" in df.columns:
                max_ts = df["timestamp"].max()
                unit = "s" if max_ts < 2e9 else "ms"
                df["dt"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
                df = df.sort_values("dt").reset_index(drop=True)
                PARQUET_CACHE[pair] = df
        except Exception:
            return 0.0

    df = PARQUET_CACHE.get(pair)
    if df is None or df.empty:
        return 0.0

    col = "close" if "close" in df.columns else ("last_price" if "last_price" in df.columns else df.columns[1])
    try:
        ts_utc = pd.to_datetime(ts_val, utc=True)
        idx = (df["dt"] - ts_utc).abs().idxmin()
        val = float(df.loc[idx, col])
        if val > 0:
            return val
    except Exception:
        pass

    return float(df[col].iloc[-1])


def get_latest_parquet_price(pair: str) -> float:
    safe = pair.replace("/", "_")
    p_file = os.path.join(STORE_DIR, f"{safe}.parquet")
    if not os.path.exists(p_file):
        return 0.0
    try:
        df = pd.read_parquet(p_file)
        if not df.empty:
            col = "close" if "close" in df.columns else ("last_price" if "last_price" in df.columns else df.columns[1])
            return float(df[col].iloc[-1])
    except Exception:
        pass
    return 0.0


def audit_trades():
    print("=" * 105)
    print("📊 COMPLETE OFFLINE HACKATHON PERFORMANCE & POST-MORTEM AUDIT")
    print("=" * 105)

    if not os.path.exists(TRADES_CSV):
        print(f"❌ Error: {TRADES_CSV} not found!")
        return

    trades_df = pd.read_csv(TRADES_CSV)
    if trades_df.empty:
        print("❌ Trades log is empty!")
        return

    trades_df["timestamp"] = pd.to_datetime(trades_df["timestamp"], utc=True)
    trades_df["side"] = trades_df["side"].str.upper()

    # Reconstruct filled prices
    clean_trades = []
    for _, row in trades_df.iterrows():
        p_raw = row["price"]
        pair = row["pair"]
        ts = row["timestamp"]
        qty = float(row["quantity"])
        
        try:
            price = float(p_raw)
        except (ValueError, TypeError):
            price = get_parquet_price_at(pair, ts)

        if price <= 0:
            price = get_latest_parquet_price(pair)

        clean_trades.append({
            "timestamp": ts,
            "pair": pair,
            "side": row["side"],
            "quantity": qty,
            "price": price,
            "cost_usd": qty * price,
        })

    clean_df = pd.DataFrame(clean_trades).sort_values("timestamp").reset_index(drop=True)

    # Pair FIFO buys and sells
    closed_cycles = []
    inventory = {}  # pair -> list of [qty, price, ts]

    for _, t in clean_df.iterrows():
        pair = t["pair"]
        side = t["side"]
        qty = t["quantity"]
        px = t["price"]
        ts = t["timestamp"]

        if side == "BUY":
            if pair not in inventory:
                inventory[pair] = []
            inventory[pair].append({"qty": qty, "price": px, "ts": ts})
        elif side == "SELL":
            if pair not in inventory or not inventory[pair]:
                continue
            rem_sell_qty = qty
            while rem_sell_qty > 0 and inventory[pair]:
                lot = inventory[pair][0]
                matched_qty = min(rem_sell_qty, lot["qty"])
                buy_px = lot["price"]
                buy_ts = lot["ts"]

                buy_val = matched_qty * buy_px
                sell_val = matched_qty * px
                fee = (buy_val + sell_val) * 0.001  # 0.10% each side = 0.20% round trip
                net_pnl = (sell_val - buy_val) - fee
                ret_pct = ((px - buy_px) / buy_px) * 100.0

                closed_cycles.append({
                    "pair": pair,
                    "buy_time": buy_ts,
                    "sell_time": ts,
                    "quantity": matched_qty,
                    "buy_price": buy_px,
                    "sell_price": px,
                    "buy_val": buy_val,
                    "sell_val": sell_val,
                    "net_pnl": net_pnl,
                    "ret_pct": ret_pct,
                    "duration_hrs": (ts - buy_ts).total_seconds() / 3600.0,
                })

                lot["qty"] -= matched_qty
                rem_sell_qty -= matched_qty
                if lot["qty"] <= 1e-6:
                    inventory[pair].pop(0)

    cycles_df = pd.DataFrame(closed_cycles)

    print(f"Total Trade Rows in Log: {len(clean_df)} (BUYS: {(clean_df['side']=='BUY').sum()}, SELLS: {(clean_df['side']=='SELL').sum()})")
    print(f"Total Closed Round-Trip Positions: {len(cycles_df)}\n")

    if cycles_df.empty:
        print("No completed round-trip trades found.")
        return

    # Total Overall Metrics
    tot_pnl = cycles_df["net_pnl"].sum()
    wins = cycles_df[cycles_df["net_pnl"] > 0]
    losses = cycles_df[cycles_df["net_pnl"] <= 0]
    win_rate = (len(wins) / len(cycles_df)) * 100.0
    profit_factor = (wins["net_pnl"].sum() / abs(losses["net_pnl"].sum())) if abs(losses["net_pnl"].sum()) > 0 else float("inf")

    print("-" * 105)
    print("📈 OVERALL TOURNAMENT REALIZED METRICS:")
    print("-" * 105)
    print(f"• Total Realized Net PnL (after fees): ${tot_pnl:+,.2f}")
    print(f"• Win Rate: {win_rate:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"• Profit Factor: {profit_factor:.2f}")
    print(f"• Gross Profit: +${wins['net_pnl'].sum():,.2f} | Gross Loss: -${abs(losses['net_pnl'].sum()):,.2f}")
    print(f"• Average Trade PnL: ${cycles_df['net_pnl'].mean():+,.2f} (Avg Ret: {cycles_df['ret_pct'].mean():+.2f}%)")
    print(f"• Average Holding Duration: {cycles_df['duration_hrs'].mean():.1f} hours")
    print("-" * 105)

    # Split Comparison: Phase 1 (Pre-Oct 11) vs Phase 2 (Post-Oct 11)
    split_date = pd.to_datetime("2026-10-11 12:00:00", utc=True)
    p1 = cycles_df[cycles_df["sell_time"] < split_date]
    p2 = cycles_df[cycles_df["sell_time"] >= split_date]

    print("\n" + "=" * 105)
    print("⚖️ PHASE 1 (Oct 6 - Oct 11: Pre-Shreyan / 1m Trailing Ratchets) vs PHASE 2 (Oct 11 - Oct 19: Post-Shreyan)")
    print("=" * 105)

    def print_phase(name, df_sub):
        if df_sub.empty:
            print(f"{name}: No trades completed in this period.")
            return
        p_pnl = df_sub["net_pnl"].sum()
        p_wins = df_sub[df_sub["net_pnl"] > 0]
        p_losses = df_sub[df_sub["net_pnl"] <= 0]
        p_wr = (len(p_wins) / len(df_sub)) * 100.0
        p_pf = (p_wins["net_pnl"].sum() / abs(p_losses["net_pnl"].sum())) if abs(p_losses["net_pnl"].sum()) > 0 else float("inf")
        print(f"▶ {name}:")
        print(f"  • Total Trades: {len(df_sub)}")
        print(f"  • Net Realized PnL: ${p_pnl:+,.2f}")
        print(f"  • Win Rate: {p_wr:.1f}% ({len(p_wins)} Wins / {len(p_losses)} Losses)")
        print(f"  • Profit Factor: {p_pf:.2f}")
        print(f"  • Avg Return per Trade: {df_sub['ret_pct'].mean():+.2f}%")
        print(f"  • Avg Holding Duration: {df_sub['duration_hrs'].mean():.1f} hrs")

    print_phase("PHASE 1 (Pre-Oct 11: Active 1m Trailing Ratchet Engine)", p1)
    print()
    print_phase("PHASE 2 (Post-Oct 11: Small-Cap Memes & Overhaul)", p2)
    print("=" * 105)

    # Performance per Coin
    by_coin = cycles_df.groupby("pair").agg(
        trades=("net_pnl", "count"),
        total_pnl=("net_pnl", "sum"),
        avg_ret=("ret_pct", "mean"),
        win_rate=("net_pnl", lambda x: (x > 0).mean() * 100.0)
    ).sort_values("total_pnl", ascending=False)

    print("\n" + "=" * 105)
    print("🏅 PnL BREAKDOWN BY ASSET / COIN:")
    print("=" * 105)
    print(f"{'Coin Pair':<14} | {'Trades':<8} | {'Win Rate':<10} | {'Avg Ret (%)':<12} | {'Net Realized PnL ($)':<20}")
    print("-" * 105)
    for pair, row in by_coin.iterrows():
        print(f"{pair:<14} | {int(row['trades']):<8} | {row['win_rate']:>8.1f}% | {row['avg_ret']:>10.2f}% | ${row['total_pnl']:>+18.2f}")
    print("=" * 105)

    # Active Open Holdings and Unrealized PnL
    if os.path.exists(POS_FILE):
        try:
            with open(POS_FILE) as f:
                active_pos = json.load(f)
            if active_pos:
                print("\n" + "=" * 105)
                print("💼 FINAL ACTIVE OPEN POSITIONS & UNREALIZED PnL (as of Oct 19):")
                print("=" * 105)
                print(f"{'Coin Pair':<14} | {'Quantity':<12} | {'Entry Price':<14} | {'Latest Price':<14} | {'Unrealized PnL ($)':<20}")
                print("-" * 105)
                total_open_cost = 0.0
                total_open_val = 0.0
                for p, info in active_pos.items():
                    qty = float(info.get("quantity", 0.0))
                    entry = float(info.get("entry_price", 0.0))
                    curr = get_latest_parquet_price(p)
                    if curr <= 0:
                        curr = entry
                    cost = qty * entry
                    curr_val = qty * curr
                    u_pnl = curr_val - cost
                    total_open_cost += cost
                    total_open_val += curr_val
                    print(f"{p:<14} | {qty:<12.4f} | ${entry:<13.4f} | ${curr:<13.4f} | ${u_pnl:>+18.2f} ({(curr/entry-1)*100:+.2f}%)")
                print("-" * 105)
                print(f"Total Open Portfolio Value: ${total_open_val:,.2f} | Total Unrealized PnL: ${total_open_val - total_open_cost:+,.2f}")
                print("=" * 105)
        except Exception as e:
            print("Error loading active positions:", e)

    # Best & Worst Trades
    print("\n" + "=" * 105)
    print("🏆 TOP 3 MOST PROFITABLE TRADES:")
    print("=" * 105)
    for _, t in cycles_df.sort_values("net_pnl", ascending=False).head(3).iterrows():
        print(f"• {t['pair']}: +${t['net_pnl']:,.2f} (+{t['ret_pct']:.2f}%) | Bought: {t['buy_time'].strftime('%b %d %H:%M')} @ ${t['buy_price']:.4f} -> Sold: {t['sell_time'].strftime('%b %d %H:%M')} @ ${t['sell_price']:.4f}")
    
    print("\n" + "=" * 105)
    print("💥 TOP 3 BIGGEST LOSING TRADES:")
    print("=" * 105)
    for _, t in cycles_df.sort_values("net_pnl", ascending=True).head(3).iterrows():
        print(f"• {t['pair']}: -${abs(t['net_pnl']):,.2f} ({t['ret_pct']:.2f}%) | Bought: {t['buy_time'].strftime('%b %d %H:%M')} @ ${t['buy_price']:.4f} -> Sold: {t['sell_time'].strftime('%b %d %H:%M')} @ ${t['sell_price']:.4f}")
    print("=" * 105)


if __name__ == "__main__":
    audit_trades()

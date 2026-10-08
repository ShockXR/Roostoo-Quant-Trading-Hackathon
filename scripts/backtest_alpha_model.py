"""
10-Day High-Frequency Backtest Engine Comparison:
- Option A: 1-Minute Micro-Dip Scalper Engine
- Option B: 3-Minute Dual Breakout Engine

Evaluates the last 10 days of price data (ending yesterday) across all 15 target coins.
Generates a side-by-side comparative markdown report: ALPHA_MODEL_3MONTH_BACKTEST_REPORT.md
"""
import os
import sys
import glob
import math
import numpy as np
import pandas as pd

# Target Universes
LARGECAP_COINS = ["NEAR/USD", "ICP/USD", "HBAR/USD", "FIL/USD"]
MIDCAP_COINS = [
    "UNI/USD", "AAVE/USD", "CRV/USD", "PENDLE/USD", "CAKE/USD",
    "ONDO/USD", "ENA/USD", "ARB/USD", "POL/USD", "SEI/USD", "CFX/USD"
]
ALL_COINS = LARGECAP_COINS + MIDCAP_COINS

DATA_DIRS = ["data_store", "data_store/processed", "binance_data", "binance_data/processed"]

def load_coin_data(symbol: str) -> pd.DataFrame:
    """Finds and loads raw 1m data for a given symbol, filtering to last 10 days (14,400 bars)."""
    safe_symbol = symbol.replace("/", "_")  # NEAR_USD
    raw_symbol = symbol.replace("/USD", "USDT")  # NEARUSDT
    
    candidates = []
    for d in DATA_DIRS:
        if os.path.exists(d):
            candidates.extend(glob.glob(f"{d}/**/{safe_symbol}*.parquet", recursive=True))
            candidates.extend(glob.glob(f"{d}/**/{raw_symbol}*.parquet", recursive=True))
            candidates.extend(glob.glob(f"{d}/**/{safe_symbol}*.csv", recursive=True))
            candidates.extend(glob.glob(f"{d}/**/{raw_symbol}*.csv", recursive=True))
        
    if not candidates:
        return pd.DataFrame()
        
    filepath = candidates[0]
    try:
        if filepath.endswith(".parquet"):
            df = pd.read_parquet(filepath)
        else:
            df = pd.read_csv(filepath)
            
        # Standardize price & volume columns
        if "last_price" in df.columns and "close" not in df.columns:
            df["close"] = df["last_price"]
        elif "close" in df.columns:
            df["close"] = df["close"].astype(float)
            
        if "volume" not in df.columns:
            df["volume"] = 1.0
        else:
            df["volume"] = df["volume"].astype(float)
            
        if "open" not in df.columns:
            df["open"] = df["close"]
        if "high" not in df.columns:
            df["high"] = df["close"]
        if "low" not in df.columns:
            df["low"] = df["close"]
            
        ts_col = "timestamp" if "timestamp" in df.columns else ("open_time" if "open_time" in df.columns else None)
        if ts_col:
            if pd.api.types.is_datetime64_any_dtype(df[ts_col]):
                df["dt"] = df[ts_col]
            else:
                first_val = df[ts_col].iloc[0]
                if isinstance(first_val, (int, float, np.number)):
                    unit = "ms" if first_val > 1e11 else "s"
                    df["dt"] = pd.to_datetime(df[ts_col], unit=unit, utc=True)
                else:
                    df["dt"] = pd.to_datetime(df[ts_col], utc=True)
                    
            df = df.sort_values("dt").reset_index(drop=True)
            
        # Slice to Last 10 Days (14,400 1-minute bars)
        if len(df) > 14400:
            df = df.iloc[-14400:].reset_index(drop=True)
            
        return df
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return pd.DataFrame()


# ==============================================================================
# OPTION A: 1-MINUTE MICRO-DIP SCALPER ENGINE
# ==============================================================================
def run_option_a_scalper(symbol: str, df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 30:
        return {"symbol": symbol, "trades": 0, "win_rate": 0.0, "net_profit": 0.0, "pf": 0.0, "trades_per_day": 0.0}
        
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    
    # Calculate 14-bar RSI
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14, min_periods=5).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14, min_periods=5).mean()
    rs = gain / loss.replace(0, np.nan).fillna(0.001)
    rsi = (100 - (100 / (1 + rs))).fillna(50.0).values
    
    # Calculate Z-score of 20-bar return
    ret_20 = df["close"].pct_change(20)
    vol_60 = df["close"].pct_change().rolling(60, min_periods=10).std().replace(0, np.nan).fillna(0.002)
    z_score = (ret_20 / (vol_60 * np.sqrt(20))).fillna(0.0).values
    
    is_largecap = symbol in LARGECAP_COINS
    long_alloc = 8000.0 if is_largecap else 5000.0
    short_alloc = 1000.0  # Capped at $1k
    reentry_sep = 0.0050  # 0.5% separation
    
    position = None
    entry_price = 0.0
    last_exit_price = 0.0
    trades = []
    
    for i in range(20, len(close)):
        p = close[i]
        c_rsi = rsi[i]
        c_z = z_score[i]
        
        # 1. Manage Active Position
        if position == "LONG":
            ret = (p - entry_price) / entry_price
            if ret >= 0.0070:  # Take profit +0.70% (+0.50% net)
                pnl = (p - entry_price) * (long_alloc / entry_price) - (long_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "LONG"})
                position = None
                last_exit_price = p
            elif ret <= -0.0150:  # Stop loss -1.50%
                pnl = (p - entry_price) * (long_alloc / entry_price) - (long_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "LONG"})
                position = None
                last_exit_price = p
                
        elif position == "SHORT":
            ret = (entry_price - p) / entry_price
            if ret >= 0.0070:  # Take profit +0.70%
                pnl = (entry_price - p) * (short_alloc / entry_price) - (short_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "SHORT"})
                position = None
                last_exit_price = p
            elif ret <= -0.0150:  # Stop loss +1.50% against short
                pnl = (entry_price - p) * (short_alloc / entry_price) - (short_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "SHORT"})
                position = None
                last_exit_price = p
                
        # 2. Evaluate New Entries
        if position is None:
            sep_ok = True
            if last_exit_price > 0 and abs(p - last_exit_price) / last_exit_price < reentry_sep:
                sep_ok = False
                
            if sep_ok:
                if c_rsi <= 30 or c_z <= -1.8:
                    position = "LONG"
                    entry_price = p
                elif c_rsi >= 70 or c_z >= 1.8:
                    position = "SHORT"
                    entry_price = p

    num_trades = len(trades)
    if num_trades == 0:
        return {"symbol": symbol, "trades": 0, "win_rate": 0.0, "net_profit": 0.0, "pf": 0.0, "trades_per_day": 0.0}
        
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    net_profit = sum(t["pnl"] for t in trades)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)
    
    return {
        "symbol": symbol,
        "trades": num_trades,
        "trades_per_day": round(num_trades / 10.0, 1),
        "win_rate": round(len(wins) / num_trades * 100.0, 2),
        "net_profit": round(net_profit, 2),
        "pf": round(pf, 2)
    }


# ==============================================================================
# OPTION B: 3-MINUTE DUAL BREAKOUT ENGINE
# ==============================================================================
def run_option_b_3m_engine(symbol: str, df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 60:
        return {"symbol": symbol, "trades": 0, "win_rate": 0.0, "net_profit": 0.0, "pf": 0.0, "trades_per_day": 0.0}
        
    # Resample to 3m bars
    try:
        df_3m = df.set_index("dt").resample("3min").agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
        }).dropna().reset_index()
    except Exception:
        df_3m = df
        
    close = df_3m["close"]
    volume = df_3m["volume"]
    open_p = df_3m["open"]
    high_p = df_3m["high"]
    low_p = df_3m["low"]
    
    # Calculate S_alpha
    candle_dir = (close - open_p) * volume
    candle_range = (high_p - low_p).replace(0, np.nan).fillna(0.001) * volume
    vol_imbalance = (candle_dir.rolling(10, min_periods=3).sum() / candle_range.rolling(10, min_periods=3).sum()).fillna(0.0).clip(-1.0, 1.0)
    
    ret_15m = close.pct_change(5)  # 5 * 3m = 15m return
    vol_60m = close.pct_change().rolling(20, min_periods=5).std().replace(0, np.nan).fillna(0.001)
    rel_strength = (ret_15m / (vol_60m * np.sqrt(5))).clip(-3.0, 3.0) / 3.0
    
    ret_3m = close.pct_change(1)
    mom_slope = ((ret_3m - ret_15m) / vol_60m).clip(-3.0, 3.0) / 3.0
    
    ma5 = close.rolling(5, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=5).mean()
    trend_align = np.sign(ma5 - ma20) * ((ma5 - ma20).abs() / (ma20 * 0.005)).clip(0.0, 1.0)
    
    s_alpha = (0.35 * vol_imbalance + 0.35 * rel_strength + 0.20 * mom_slope + 0.10 * trend_align).clip(-1.0, 1.0).values
    
    bb_mid = ma20
    bb_std = close.rolling(15, min_periods=5).std()
    bb_width = ((2.0 * bb_std / bb_mid).fillna(0.05)).values
    is_squeeze = bb_width <= 0.0120
    
    c_arr = close.values
    is_largecap = symbol in LARGECAP_COINS
    long_alloc = 8000.0 if is_largecap else 5000.0
    short_alloc = 1000.0
    reentry_sep = 0.0080
    
    position = None
    entry_price = 0.0
    peak_price = 0.0
    trough_price = 0.0
    last_exit_price = 0.0
    trades = []
    
    for i in range(20, len(c_arr)):
        p = c_arr[i]
        alpha = s_alpha[i]
        in_sq = is_squeeze[i]
        
        if position == "LONG":
            peak_price = max(peak_price, p)
            ret = (p - entry_price) / entry_price
            peak_ret = (peak_price - entry_price) / entry_price
            
            if ret >= 0.0080:  # TP +0.80%
                pnl = (p - entry_price) * (long_alloc / entry_price) - (long_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "LONG"})
                position = None
                last_exit_price = p
            elif peak_ret >= 0.0050 and ret <= 0.0030: # Ratchet lock +0.30%
                pnl = (p - entry_price) * (long_alloc / entry_price) - (long_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "LONG"})
                position = None
                last_exit_price = p
            elif ret <= -0.0180: # SL -1.80%
                pnl = (p - entry_price) * (long_alloc / entry_price) - (long_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "LONG"})
                position = None
                last_exit_price = p
                
        elif position == "SHORT":
            trough_price = min(trough_price, p)
            ret = (entry_price - p) / entry_price
            max_drop = (entry_price - trough_price) / entry_price
            
            if ret >= 0.0080:  # TP +0.80%
                pnl = (entry_price - p) * (short_alloc / entry_price) - (short_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "SHORT"})
                position = None
                last_exit_price = p
            elif max_drop >= 0.0050 and ret <= 0.0030: # Ratchet lock +0.30%
                pnl = (entry_price - p) * (short_alloc / entry_price) - (short_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "SHORT"})
                position = None
                last_exit_price = p
            elif ret <= -0.0180: # SL +1.80%
                pnl = (entry_price - p) * (short_alloc / entry_price) - (short_alloc * 0.002)
                trades.append({"pnl": pnl, "win": pnl > 0, "side": "SHORT"})
                position = None
                last_exit_price = p
                
        if position is None:
            sep_ok = True
            if last_exit_price > 0 and abs(p - last_exit_price) / last_exit_price < reentry_sep:
                sep_ok = False
                
            if sep_ok and in_sq:
                if alpha >= 0.30:
                    position = "LONG"
                    entry_price = p
                    peak_price = p
                elif alpha <= -0.30:
                    position = "SHORT"
                    entry_price = p
                    trough_price = p

    num_trades = len(trades)
    if num_trades == 0:
        return {"symbol": symbol, "trades": 0, "win_rate": 0.0, "net_profit": 0.0, "pf": 0.0, "trades_per_day": 0.0}
        
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    net_profit = sum(t["pnl"] for t in trades)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)
    
    return {
        "symbol": symbol,
        "trades": num_trades,
        "trades_per_day": round(num_trades / 10.0, 1),
        "win_rate": round(len(wins) / num_trades * 100.0, 2),
        "net_profit": round(net_profit, 2),
        "pf": round(pf, 2)
    }


def generate_comparison_report(res_a: list[dict], res_b: list[dict], report_path: str = "ALPHA_MODEL_3MONTH_BACKTEST_REPORT.md"):
    total_trades_a = sum(r["trades"] for r in res_a)
    total_pnl_a = sum(r["net_profit"] for r in res_a)
    avg_wr_a = np.mean([r["win_rate"] for r in res_a if r["trades"] > 0]) if total_trades_a > 0 else 0.0
    
    total_trades_b = sum(r["trades"] for r in res_b)
    total_pnl_b = sum(r["net_profit"] for r in res_b)
    avg_wr_b = np.mean([r["win_rate"] for r in res_b if r["trades"] > 0]) if total_trades_b > 0 else 0.0

    md_lines = [
        "# ⚔️ High-Frequency 10-Day Strategy Backtest Comparison",
        "",
        "## Executive Summary",
        f"- **Option A (1m Micro-Dip Scalper)**: Net Profit **+${total_pnl_a:,.2f} USD** | Total Trades: **{total_trades_a}** ({total_trades_a/10:.1f}/day) | Avg Win Rate: **{avg_wr_a:.1f}%**",
        f"- **Option B (3m Dual Breakout Engine)**: Net Profit **+${total_pnl_b:,.2f} USD** | Total Trades: **{total_trades_b}** ({total_trades_b/10:.1f}/day) | Avg Win Rate: **{avg_wr_b:.1f}%**",
        "",
        "## Per-Coin Strategy Comparison",
        "",
        "| Symbol | Option A Trades | Opt A Trades/Day | Opt A Win% | Opt A PnL ($) | Option B Trades | Opt B Trades/Day | Opt B Win% | Opt B PnL ($) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    
    dict_b = {r["symbol"]: r for r in res_b}
    for r_a in res_a:
        sym = r_a["symbol"]
        r_b = dict_b.get(sym, {"trades": 0, "trades_per_day": 0, "win_rate": 0, "net_profit": 0})
        md_lines.append(
            f"| {sym} | {r_a['trades']} | {r_a['trades_per_day']} | {r_a['win_rate']}% | ${r_a['net_profit']:,.2f} | {r_b['trades']} | {r_b['trades_per_day']} | {r_b['win_rate']}% | ${r_b['net_profit']:,.2f} |"
        )
        
    md_lines.extend([
        "",
        "## Strategy Verdict",
        "This comparison details trade frequency, win rates, and net returns between 1-minute Micro-Dip Scalping vs 3-minute Dual Breakout expansion engines.",
        "",
        "*Report auto-generated by `scripts/backtest_alpha_model.py`.*"
    ])
    
    with open(report_path, "w") as f:
        f.write("\n".join(md_lines))
        
    print(f"\n✅ Comparison Markdown Report generated at: {report_path}")


def main():
    print("================================================================================")
    print("⚔️ 10-DAY HIGH-FREQUENCY STRATEGY BACKTEST COMPARISON (OPTION A VS OPTION B)")
    print("================================================================================")
    
    results_a = []
    results_b = []
    
    for coin in ALL_COINS:
        df = load_coin_data(coin)
        res_a = run_option_a_scalper(coin, df)
        res_b = run_option_b_3m_engine(coin, df)
        
        results_a.append(res_a)
        results_b.append(res_b)
        
        print(f"[{coin}] Opt A: {res_a['trades']} trades ({res_a['trades_per_day']}/day), WR {res_a['win_rate']}%, PnL ${res_a['net_profit']:,.2f} | Opt B: {res_b['trades']} trades ({res_b['trades_per_day']}/day), WR {res_b['win_rate']}%, PnL ${res_b['net_profit']:,.2f}")
        
    generate_comparison_report(results_a, results_b)


if __name__ == "__main__":
    main()

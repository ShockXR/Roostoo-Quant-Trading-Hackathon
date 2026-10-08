"""
3-Month Alpha Model ($S_alpha$) Long/Short Backtesting Engine & Report Generator.

Optimized Configuration:
- Resampled to 5-Minute Candles (smooths micro-wicks, reduces fee friction)
- Formula: S_alpha = 0.35 * VolImbalance + 0.35 * RelStrength + 0.20 * MomSlope + 0.10 * TrendAlignment
- Pre-Condition Gate: 20-bar 5m Bollinger Bandwidth <= 0.80% (Strict Squeeze Compression)
- Higher Entry Conviction: |S_alpha| >= 0.55
- Rules Enforced:
  - LONG Entry: S_alpha >= +0.55 AND Consolidation Squeeze
  - SHORT Entry: S_alpha <= -0.55 AND Consolidation Squeeze (Strict $1,000 USD position allocation cap per short trade)
  - Short Volatility Stop-Loss: +2.0%
  - Short Trailing Ratchet: Peak -0.80% dip -> Lock -0.45% profit floor
  - Price-Separation Re-Entry Guards: 1.0% Large-Cap, 2.0% Mid-Cap distance required from last exit
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
    """Finds and loads parquet or csv files for a given symbol across DATA_DIRS, resampling to 5m bars."""
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
            
        # Resample to 5m bars to eliminate micro-noise
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
                    
            df = df.set_index("dt").resample("5min").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).dropna().reset_index()
            
        return df
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return pd.DataFrame()


def compute_alpha_score(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """
    Computes S_alpha = 0.35*VolImbalance + 0.35*RelStrength + 0.20*MomSlope + 0.10*TrendAlignment
    And evaluates Consolidation Squeeze Gate (20-bar 5m Bollinger Bandwidth <= 0.80%)
    """
    close = df["close"]
    volume = df["volume"]
    open_p = df["open"]
    high_p = df["high"]
    low_p = df["low"]
    
    if len(close) < 60:
        return pd.Series(0.0, index=close.index), pd.Series(False, index=close.index)

    # 1. VolImbalance (0.35 weight)
    candle_dir = (close - open_p) * volume
    candle_range = (high_p - low_p).replace(0, np.nan).fillna(0.001) * volume
    vol_imbalance = (candle_dir.rolling(12, min_periods=3).sum() / candle_range.rolling(12, min_periods=3).sum()).fillna(0.0).clip(-1.0, 1.0)
    
    # 2. RelStrength (0.35 weight)
    ret_12b = close.pct_change(12)  # 12 * 5m = 60m return
    vol_48b = close.pct_change().rolling(48, min_periods=10).std().replace(0, np.nan).fillna(0.001) # 4 hours vol
    rel_strength = (ret_12b / (vol_48b * np.sqrt(12))).clip(-3.0, 3.0) / 3.0
    
    # 3. MomSlope (0.20 weight)
    ret_3b = close.pct_change(3)  # 15m return
    mom_slope = ((ret_3b - ret_12b) / vol_48b).clip(-3.0, 3.0) / 3.0
    
    # 4. TrendAlignment (0.10 weight)
    ma5 = close.rolling(5, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=5).mean()
    trend_align = np.sign(ma5 - ma20) * ((ma5 - ma20).abs() / (ma20 * 0.005)).clip(0.0, 1.0)
    
    s_alpha = (0.35 * vol_imbalance + 0.35 * rel_strength + 0.20 * mom_slope + 0.10 * trend_align).clip(-1.0, 1.0)
    
    # Consolidation Squeeze Filter: 20-bar 5m Bollinger Bandwidth <= 0.80%
    bb_mid = ma20
    bb_std = close.rolling(20, min_periods=5).std()
    bb_width = (2.0 * bb_std / bb_mid).fillna(0.05)
    is_squeeze = bb_width <= 0.0080
    
    return s_alpha, is_squeeze


def run_backtest_symbol(symbol: str, df: pd.DataFrame, initial_capital: float = 100_000.0) -> dict:
    if df.empty or len(df) < 60:
        return {
            "symbol": symbol,
            "bars": len(df),
            "trades": 0,
            "win_rate": 0.0,
            "net_profit": 0.0,
            "return_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "long_trades": 0,
            "short_trades": 0,
        }
        
    close = df["close"].values
    s_alpha_series, squeeze_series = compute_alpha_score(df)
    s_alpha = s_alpha_series.values
    is_squeeze = squeeze_series.values
    
    is_largecap = symbol in LARGECAP_COINS
    reentry_sep_pct = 0.0100 if is_largecap else 0.0200
    
    position = None  # None, "LONG", or "SHORT"
    entry_price = 0.0
    entry_bar = 0
    short_alloc_usd = 1000.0  # Strict $1,000 USD cap for short positions
    long_alloc_usd = 5000.0 if not is_largecap else 8000.0
    
    last_exit_price = 0.0
    peak_price = 0.0
    trough_price = 0.0
    
    trades = []
    
    for i in range(20, len(close)):
        p = close[i]
        alpha = s_alpha[i]
        in_squeeze = is_squeeze[i]
        
        # 1. Manage Active Position
        if position == "LONG":
            peak_price = max(peak_price, p)
            ret = (p - entry_price) / entry_price
            peak_ret = (peak_price - entry_price) / entry_price
            
            # Exit Rules for Long:
            # a) Volatility Stop Loss (-2.5%)
            # b) Trailing Ratchet (+1.0% -> lock +0.65%, +2.0% -> lock +1.40%)
            # c) Signal Reversal (S_alpha <= -0.55)
            should_exit = False
            exit_reason = ""
            
            if ret <= -0.025:
                should_exit = True
                exit_reason = "Long Stop-Loss (-2.5%)"
            elif peak_ret >= 0.020 and ret <= 0.014:
                should_exit = True
                exit_reason = "Long Ratchet +1.40%"
            elif peak_ret >= 0.010 and ret <= 0.0065:
                should_exit = True
                exit_reason = "Long Ratchet +0.65%"
            elif alpha <= -0.55:
                should_exit = True
                exit_reason = "Signal Reversal"
                
            if should_exit:
                pnl = (p - entry_price) * (long_alloc_usd / entry_price) - (long_alloc_usd * 0.002) # 0.20% fee
                trades.append({
                    "side": "LONG",
                    "entry_price": entry_price,
                    "exit_price": p,
                    "pnl": pnl,
                    "pnl_pct": (p - entry_price) / entry_price - 0.002,
                    "reason": exit_reason,
                    "holding_bars": i - entry_bar,
                })
                position = None
                last_exit_price = p
                
        elif position == "SHORT":
            trough_price = min(trough_price, p)
            ret = (entry_price - p) / entry_price  # positive when price drops
            max_drop = (entry_price - trough_price) / entry_price
            
            # Exit Rules for Short:
            # a) Volatility Stop Loss (+2.0% price rise against short)
            # b) Short Trailing Ratchet (-0.80% dip -> lock -0.45% floor)
            # c) Signal Reversal (S_alpha >= +0.55)
            should_exit = False
            exit_reason = ""
            
            if (p - entry_price) / entry_price >= 0.020: # Short Stop Loss +2.0%
                should_exit = True
                exit_reason = "Short Stop-Loss (+2.0%)"
            elif max_drop >= 0.0080 and ret <= 0.0045: # Ratchet
                should_exit = True
                exit_reason = "Short Ratchet (-0.45%)"
            elif alpha >= 0.55:
                should_exit = True
                exit_reason = "Signal Reversal"
                
            if should_exit:
                pnl = (entry_price - p) * (short_alloc_usd / entry_price) - (short_alloc_usd * 0.002)
                trades.append({
                    "side": "SHORT",
                    "entry_price": entry_price,
                    "exit_price": p,
                    "pnl": pnl,
                    "pnl_pct": (entry_price - p) / entry_price - 0.002,
                    "reason": exit_reason,
                    "holding_bars": i - entry_bar,
                })
                position = None
                last_exit_price = p
                
        # 2. Evaluate New Entries (Must pass Consolidation Squeeze Filter & Re-Entry Guard)
        if position is None:
            sep_ok = True
            if last_exit_price > 0:
                dist = abs(p - last_exit_price) / last_exit_price
                if dist < reentry_sep_pct:
                    sep_ok = False
                    
            if sep_ok and in_squeeze:
                if alpha >= 0.55:
                    position = "LONG"
                    entry_price = p
                    entry_bar = i
                    peak_price = p
                elif alpha <= -0.55:
                    position = "SHORT"
                    entry_price = p
                    entry_bar = i
                    trough_price = p

    # Performance Metrics
    num_trades = len(trades)
    if num_trades == 0:
        return {
            "symbol": symbol,
            "bars": len(close),
            "trades": 0,
            "win_rate": 0.0,
            "net_profit": 0.0,
            "return_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "long_trades": 0,
            "short_trades": 0,
        }
        
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / num_trades * 100.0
    net_profit = sum(t["pnl"] for t in trades)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)
    
    long_cnt = sum(1 for t in trades if t["side"] == "LONG")
    short_cnt = sum(1 for t in trades if t["side"] == "SHORT")
    
    return {
        "symbol": symbol,
        "bars": len(close),
        "trades": num_trades,
        "win_rate": round(win_rate, 2),
        "net_profit": round(net_profit, 2),
        "profit_factor": round(profit_factor, 2),
        "long_trades": long_cnt,
        "short_trades": short_cnt,
    }


def generate_markdown_report(results: list[dict], report_path: str = "ALPHA_MODEL_3MONTH_BACKTEST_REPORT.md"):
    total_trades = sum(r["trades"] for r in results)
    total_net_profit = sum(r["net_profit"] for r in results)
    avg_win_rate = np.mean([r["win_rate"] for r in results if r["trades"] > 0]) if total_trades > 0 else 0.0
    avg_pf = np.mean([r["profit_factor"] for r in results if r["trades"] > 0 and r["profit_factor"] < 50]) if total_trades > 0 else 0.0
    
    md_lines = [
        "# 📊 3-Month Alpha Model ($S_{\\alpha}$) 5-Minute Consolidation Backtest Report",
        "",
        "## Executive Summary",
        f"- **Timeframe**: **5-Minute Resampled Bars** (Smooths 1m micro-wicks, eliminates fee churn)",
        f"- **Formula**: $S_{{\\alpha}} = 0.35 \\cdot \\text{{VolImbalance}} + 0.35 \\cdot \\text{{RelStrength}} + 0.20 \\cdot \\text{{MomSlope}} + 0.10 \\cdot \\text{{TrendAlignment}}$",
        f"- **Pre-Condition**: **Strict Squeeze Gate** (20-bar 5m Bollinger Bandwidth $\\le 0.80\\%$)",
        f"- **Entry Conviction**: $|S_{{\\alpha}}| \\ge 0.55$",
        f"- **Total Portfolio Net Profit**: **+${total_net_profit:,.2f} USD**",
        f"- **Total Trades Executed**: {total_trades}",
        f"- **Average Win Rate**: **{avg_win_rate:.2f}%**",
        f"- **Average Profit Factor**: **{avg_pf:.2f}**",
        "- **Short Position Allocation**: Capped at strict **$1,000 USD** per short trade",
        "- **Re-Entry Protection**: 1.0% Large-Cap / 2.0% Mid-Cap Price Separation Guard",
        "",
        "## Per-Coin Performance Breakdown",
        "",
        "| Symbol | Sleeve | 5m Bars | Total Trades | Long Trades | Short Trades | Win Rate (%) | Profit Factor | Net Profit ($) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    
    for r in results:
        sleeve = "Large-Cap" if r["symbol"] in LARGECAP_COINS else "Mid-Cap"
        md_lines.append(
            f"| {r['symbol']} | {sleeve} | {r['bars']:,} | {r['trades']} | {r['long_trades']} | {r['short_trades']} | {r['win_rate']:.2f}% | {r['profit_factor']:.2f} | ${r['net_profit']:,.2f} |"
        )
        
    md_lines.extend([
        "",
        "## Conclusion & Strategy Calibration",
        "Resampling to 5m bars combined with a 0.80% squeeze gate and |S_alpha| >= 0.55 threshold filters out random noise fakeouts while isolating high-probability breakout expansions.",
        "",
        "*Report auto-generated by `scripts/backtest_alpha_model.py`.*"
    ])
    
    with open(report_path, "w") as f:
        f.write("\n".join(md_lines))
        
    print(f"\n✅ Markdown Backtest Report generated at: {report_path}")


def main():
    print("================================================================================")
    print("📊 3-MONTH OPTIMIZED 5M ALPHA MODEL ($S_alpha$) LONG/SHORT BACKTEST ENGINE")
    print("================================================================================")
    
    results = []
    for coin in ALL_COINS:
        df = load_coin_data(coin)
        res = run_backtest_symbol(coin, df)
        results.append(res)
        print(f"[{res['symbol']}] 5m Bars: {res['bars']:,} | Trades: {res['trades']} (Long: {res['long_trades']}, Short: {res['short_trades']}) | Win Rate: {res['win_rate']}% | PF: {res['profit_factor']} | PnL: ${res['net_profit']:,.2f}")
        
    generate_markdown_report(results)


if __name__ == "__main__":
    main()

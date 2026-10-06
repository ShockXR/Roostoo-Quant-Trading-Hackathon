"""
Live trading loop: polling -> compute quantitative strategy signal -> size -> execute.
Runs pure rule-based strategies (e.g. Mean-Reversion / Reversal) without any ML models.
"""
import logging
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config, costs, sizing, strategy
    from data.collector import poll_once
    from data.store import load_all
    from execution.trader import place_order, get_balance
except ImportError:
    from . import config, costs, sizing, strategy
    from .data.collector import poll_once
    from .data.store import load_all
    from .execution.trader import place_order, get_balance

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Global dry-run fallback flag
DRY_RUN = False  

# Live Trading White-list: 12 coins configured in config.UNIVERSE
LIVE_PAIRS = config.UNIVERSE

SELECTED_STRATEGY = "reversal"  # Options: 'reversal', 'momentum', 'mean_reversion', 'buy_hold'


# Track active open positions per pair: 
# {pair: {"entry_time": float, "entry_price": float, "stop_price": float, "take_profit_price": float, "quantity": float}}
ACTIVE_POSITIONS = {}


def run_once():
    """Single execution step: fetch latest prices, check Rule 1 & Rule 2 exits, compute strategy signals, execute trades."""
    poll_once()
    raw = load_all(config.UNIVERSE)

    if not raw:
        log.warning("No market data available yet.")
        return

    # Focus processing strictly on Whitelisted LIVE_PAIRS
    active_pairs = LIVE_PAIRS if "ALL" not in LIVE_PAIRS else config.UNIVERSE

    for pair in active_pairs:
        if pair not in raw or raw[pair].empty:
            continue

        df = raw[pair]
        price_col = "close" if "close" in df.columns else "last_price"
        latest_price = df[price_col].iloc[-1]

        # Check Active Position Exits (Rule 1: Stop-Loss, Rule 2: Take-Profit, Rule 3: 24h Hold)
        if pair in ACTIVE_POSITIONS:
            pos = ACTIVE_POSITIONS[pair]
            if isinstance(pos, (int, float)):
                # Legacy timestamp fallback
                pos = {"entry_time": float(pos), "entry_price": latest_price, "stop_price": latest_price * 0.95, "take_profit_price": latest_price * 1.05, "quantity": 0.0}

            entry_time = pos.get("entry_time", time.time())
            stop_price = pos.get("stop_price", latest_price * 0.95)
            tp_price = pos.get("take_profit_price", latest_price * 1.05)
            quantity = pos.get("quantity", 0.0)

            # Compute 24h Moving Average as Take-Profit reference
            ma_24h = df[price_col].iloc[-1440:].mean() if len(df) >= 30 else latest_price

            hold_secs = getattr(config, "REVERSAL_HOLD_BARS", 1440) * 60
            time_elapsed = time.time() - entry_time

            # Rule 1: Volatility Stop-Loss Exit (2 * Volatility distance)
            if latest_price <= stop_price:
                log.warning("🚨 [RULE 1: STOP-LOSS TRIGGERED] %s: Price $%.4f dropped below Stop-Loss threshold $%.4f (2x Volatility). Executing SELL.", pair, latest_price, stop_price)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                continue

            # Rule 2: Take-Profit / Mean-Reversion Target Exit
            elif latest_price >= tp_price or latest_price >= ma_24h:
                log.info("🎯 [RULE 2: TAKE-PROFIT TRIGGERED] %s: Price $%.4f reached Take-Profit target $%.4f / 24h MA $%.4f. Executing SELL.", pair, latest_price, tp_price, ma_24h)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                continue

            # Rule 3: Time-Based Holding Limit Expiry
            elif time_elapsed >= hold_secs:
                log.info("⏰ [RULE 3: 24H HOLD COMPLETED] %s: 24h holding limit reached. Executing SELL to free capital.", pair)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                continue

            else:
                log.info("%s: Position active (Holding: %dm, Price: $%.4f, Stop: $%.4f, TP: $%.4f)", pair, int(time_elapsed // 60), latest_price, stop_price, tp_price)
                continue

        min_bars_needed = int(getattr(config, "MIN_BARS_TO_TRADE", 60))
        if len(df) < min_bars_needed:
            log.info("%s: accumulating price history (%d/%d bars)", pair, len(df), min_bars_needed)
            continue

        # Evaluate selected quantitative strategy
        strat_fn = strategy.STRATEGIES.get(SELECTED_STRATEGY, strategy._reversal)
        signals = strat_fn(pair, raw, None, df, {})

        if signals is None or signals.empty:
            continue

        raw_signal = signals.iloc[-1]
        if raw_signal <= 0:
            log.info("%s: strategy %s signal=%.4f (no action)", pair, SELECTED_STRATEGY, raw_signal)
            continue

        # Calculate realized volatility for multi-factor sizing & stop loss
        realized_vol = df[price_col].pct_change().rolling(30, min_periods=5).std().iloc[-1]
        if realized_vol is None or np.isnan(realized_vol) or realized_vol <= 0:
            realized_vol = getattr(config, "SIZING_REFERENCE_VOL", 0.0006)

        # Calculate Volatility Distance for Rule 1 & Rule 2
        vol_24h = df[price_col].pct_change().rolling(1440, min_periods=30).std().iloc[-1]
        if vol_24h is None or np.isnan(vol_24h) or vol_24h <= 0:
            vol_24h = realized_vol

        vol_distance = latest_price * vol_24h * np.sqrt(1440)
        stop_loss_k = getattr(config, "STOP_LOSS_K", 2.0)
        take_profit_k = getattr(config, "TAKE_PROFIT_K", 1.5)

        stop_price = latest_price - (stop_loss_k * vol_distance)
        take_profit_price = latest_price + (take_profit_k * vol_distance)

        # Apply Multi-Factor Position Sizing Formula
        final_size = sizing.calculate_position_size(raw_signal, realized_vol)
        if final_size <= 0:
            continue

        log.info("%s: STRATEGY SIGNAL TRIGGERED (%s) size=%.4f (raw=%.4f, vol=%.6f, Stop: $%.4f, TP: $%.4f)", pair, SELECTED_STRATEGY, final_size, raw_signal, realized_vol, stop_price, take_profit_price)

        # Convert capital fraction into target USD cost
        account_equity = getattr(config, "INITIAL_CAPITAL", 100000.0)
        target_usd_cost = account_equity * final_size

        # Cash Balance Safeguard: Verify available free USD cash in wallet
        try:
            bal_resp = get_balance()
            if isinstance(bal_resp, dict) and bal_resp.get("Success"):
                free_usd = float(bal_resp.get("SpotWallet", {}).get("USD", {}).get("Free", account_equity))
                if target_usd_cost > free_usd:
                    log.warning("%s: Target cost $%.2f exceeds free USD $%.2f; trimming trade size", pair, target_usd_cost, free_usd)
                    target_usd_cost = max(0.0, free_usd * 0.95)
        except Exception:
            pass

        if target_usd_cost <= 0:
            log.info("%s: insufficient USD cash to open trade, skipping", pair)
            continue

        coin_quantity = target_usd_cost / max(1e-6, latest_price)

        # Mark position active with exact exit criteria
        ACTIVE_POSITIONS[pair] = {
            "entry_time": time.time(),
            "entry_price": latest_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "quantity": coin_quantity,
        }

        # Per-coin Live vs Paper-trade logic:
        if not DRY_RUN and (pair in LIVE_PAIRS or "ALL" in LIVE_PAIRS):
            log.info("🚀 [LIVE ORDER] Executing REAL order on Roostoo for %s: $%.2f USD (%.4f coins @ $%.2f)", pair, target_usd_cost, coin_quantity, latest_price)
            place_order(pair, "BUY", quantity=coin_quantity)
        else:
            log.info("📝 [PAPER TRADE / DRY RUN] Simulated BUY order for %s: $%.2f USD (%.4f coins @ $%.2f)", pair, target_usd_cost, coin_quantity, latest_price)



def run_forever(interval: int = config.POLL_INTERVAL_SECONDS):
    log.info("Starting live strategy loop (Strategy: %s, DRY_RUN=%s)", SELECTED_STRATEGY, DRY_RUN)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Error in main strategy loop")
        time.sleep(interval)


if __name__ == "__main__":
    run_forever()

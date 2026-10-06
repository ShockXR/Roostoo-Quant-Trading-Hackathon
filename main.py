"""
Live trading loop: polling -> compute quantitative strategy signals -> size -> execute.
Runs Dual-Sleeve Quantitative Trading System:
- Sleeve 1: 12 Large-Cap Coins (Dip Reversal + Volatility Exits, Max $50,000 Allocation)
- Sleeve 2: 11 Mid-Cap Coins (blend_v10 Dual Momentum + Profit-Gated Rank Rotation, Max $20,000 Allocation)
- Sleeve 3: Cash Reserve Buffer (Preserved $30,000 Cash Buffer)
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
    from strategies.midcap import get_top_midcap_leaders
except ImportError:
    from . import config, costs, sizing, strategy
    from .data.collector import poll_once
    from .data.store import load_all
    from .execution.trader import place_order, get_balance
    from .strategies.midcap import get_top_midcap_leaders

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Global dry-run fallback flag
DRY_RUN = False  

# Combined Active Trading Universe
LIVE_PAIRS = getattr(config, "UNIVERSE", config.LARGECAP_UNIVERSE + config.MIDCAP_UNIVERSE)

SELECTED_STRATEGY = "reversal"  # Large-cap default strategy

import json

POSITIONS_FILE = os.path.join(getattr(config, "STORE_DIR", "data_store"), "active_positions.json")


def load_active_positions() -> dict:
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                data = json.load(f)
                log.info("💾 Loaded %d active positions from %s", len(data), POSITIONS_FILE)
                return data
        except Exception as e:
            log.error("Failed to load active positions file: %s", e)
    return {}


def save_active_positions():
    try:
        os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
        with open(POSITIONS_FILE, "w") as f:
            json.dump(ACTIVE_POSITIONS, f, indent=2)
    except Exception as e:
        log.error("Failed to save active positions file: %s", e)


# Track active open positions per pair (loaded from disk on startup)
ACTIVE_POSITIONS = load_active_positions()


def process_largecap_sleeve(raw_data: dict[str, pd.DataFrame]):
    """Processes Sleeve 1 (12 Large-Cap Coins) with Rule 1 Stop Loss & Rule 2 Fee-Protected Take Profit."""
    largecap_pairs = getattr(config, "LARGECAP_UNIVERSE", [])
    account_equity = getattr(config, "INITIAL_CAPITAL", 100000.0)

    for pair in largecap_pairs:
        if pair not in raw_data or raw_data[pair].empty:
            continue

        df = raw_data[pair]
        price_col = "close" if "close" in df.columns else "last_price"
        latest_price = df[price_col].iloc[-1]

        # Active Position Exits
        if pair in ACTIVE_POSITIONS:
            pos = ACTIVE_POSITIONS[pair]
            if isinstance(pos, (int, float)):
                pos = {"entry_time": float(pos), "entry_price": latest_price, "stop_price": latest_price * 0.95, "take_profit_price": latest_price * 1.05, "quantity": 0.0, "sleeve": "largecap"}

            entry_time = pos.get("entry_time", time.time())
            entry_price = pos.get("entry_price", latest_price)
            stop_price = pos.get("stop_price", latest_price * 0.95)
            tp_price = pos.get("take_profit_price", latest_price * 1.05)
            quantity = pos.get("quantity", 0.0)

            hold_secs = getattr(config, "REVERSAL_HOLD_BARS", 1440) * 60
            time_elapsed = time.time() - entry_time

            # Rule 1: Volatility Stop-Loss Exit (2 * Volatility distance)
            if latest_price <= stop_price:
                log.warning("🚨 [LARGECAP RULE 1: STOP-LOSS] %s: Price $%.4f dropped below Stop-Loss $%.4f (2x Vol). Executing SELL.", pair, latest_price, stop_price)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                continue

            # Rule 2: Take-Profit Target Exit (Guarantees fee coverage: +0.30% minimum)
            min_fee_tp = entry_price * 1.003
            target_sell_price = max(tp_price, min_fee_tp)

            if latest_price >= target_sell_price:
                log.info("🎯 [LARGECAP RULE 2: TAKE-PROFIT] %s: Price $%.4f reached Take-Profit target $%.4f (Entry: $%.4f). Executing SELL.", pair, latest_price, target_sell_price, entry_price)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                continue

            # Rule 3: Time Expiry
            elif time_elapsed >= hold_secs:
                log.info("⏰ [LARGECAP RULE 3: 24H HOLD COMPLETED] %s: Holding limit reached. Executing SELL.", pair)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                continue

            else:
                log.info("[LARGECAP] %s: Position active (Holding: %dm, Price: $%.4f, Stop: $%.4f, TP: $%.4f)", pair, int(time_elapsed // 60), latest_price, stop_price, tp_price)
                continue

        # Evaluate Strategy Entry Signal
        min_bars_needed = int(getattr(config, "MIN_BARS_TO_TRADE", 60))
        if len(df) < min_bars_needed:
            continue

        strat_fn = strategy.STRATEGIES.get(SELECTED_STRATEGY, strategy._reversal)
        signals = strat_fn(pair, raw_data, None, df, {})

        if signals is None or signals.empty:
            continue

        raw_signal = signals.iloc[-1]
        if raw_signal <= 0:
            continue

        realized_vol = df[price_col].pct_change().rolling(30, min_periods=5).std().iloc[-1]
        if realized_vol is None or np.isnan(realized_vol) or realized_vol <= 0:
            realized_vol = getattr(config, "SIZING_REFERENCE_VOL", 0.0006)

        vol_24h = df[price_col].pct_change().rolling(1440, min_periods=30).std().iloc[-1]
        if vol_24h is None or np.isnan(vol_24h) or vol_24h <= 0:
            vol_24h = realized_vol

        vol_distance = latest_price * vol_24h * np.sqrt(1440)
        stop_loss_k = getattr(config, "STOP_LOSS_K", 2.0)
        take_profit_k = getattr(config, "TAKE_PROFIT_K", 1.5)

        stop_price = latest_price - (stop_loss_k * vol_distance)
        take_profit_price = latest_price + (take_profit_k * vol_distance)

        final_size = sizing.calculate_position_size(raw_signal, realized_vol, max_position=getattr(config, "MAX_POSITION_PER_ASSET", 0.08))
        if final_size <= 0:
            continue

        target_usd_cost = account_equity * final_size

        try:
            bal_resp = get_balance()
            if isinstance(bal_resp, dict) and bal_resp.get("Success"):
                free_usd = float(bal_resp.get("SpotWallet", {}).get("USD", {}).get("Free", account_equity))
                if target_usd_cost > free_usd:
                    target_usd_cost = max(0.0, free_usd * 0.95)
        except Exception:
            pass

        if target_usd_cost <= 0:
            continue

        coin_quantity = target_usd_cost / max(1e-6, latest_price)

        ACTIVE_POSITIONS[pair] = {
            "entry_time": time.time(),
            "entry_price": latest_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "quantity": coin_quantity,
            "sleeve": "largecap",
        }
        save_active_positions()

        if not DRY_RUN:
            log.info("🚀 [LARGECAP LIVE ORDER] Executing BUY for %s: $%.2f USD (%.4f coins @ $%.2f)", pair, target_usd_cost, coin_quantity, latest_price)
            place_order(pair, "BUY", quantity=coin_quantity)
        else:
            log.info("📝 [LARGECAP DRY RUN] Simulated BUY for %s: $%.2f USD (%.4f coins @ $%.2f)", pair, target_usd_cost, coin_quantity, latest_price)


def process_midcap_sleeve(raw_data: dict[str, pd.DataFrame]):
    """Processes Sleeve 2 (11 Mid-Cap Coins) using blend_v10 Dual Momentum + Profit-Gated Rank Rotation."""
    midcap_pairs = getattr(config, "MIDCAP_UNIVERSE", [])
    account_equity = getattr(config, "INITIAL_CAPITAL", 100000.0)

    top_leaders, returns_24h = get_top_midcap_leaders(raw_data, midcap_pairs)

    for pair in midcap_pairs:
        if pair not in raw_data or raw_data[pair].empty:
            continue

        df = raw_data[pair]
        price_col = "close" if "close" in df.columns else "last_price"
        latest_price = df[price_col].iloc[-1]

        # Active Position Exits for Mid-Caps
        if pair in ACTIVE_POSITIONS:
            pos = ACTIVE_POSITIONS[pair]
            if pos.get("sleeve") == "midcap":
                entry_time = pos.get("entry_time", time.time())
                entry_price = pos.get("entry_price", latest_price)
                stop_price = pos.get("stop_price", latest_price * 0.95)
                tp_price = pos.get("take_profit_price", latest_price * 1.05)
                quantity = pos.get("quantity", 0.0)

                time_elapsed = time.time() - entry_time
                is_in_top_rank = pair in top_leaders

                # Rule 1: Mid-Cap Volatility Stop Loss (2.5 * Volatility)
                if latest_price <= stop_price:
                    log.warning("🚨 [MIDCAP RULE 1: STOP-LOSS] %s: Price $%.4f dropped below Stop-Loss $%.4f. Executing SELL.", pair, latest_price, stop_price)
                    if not DRY_RUN and quantity > 0:
                        place_order(pair, "SELL", quantity=quantity)
                    del ACTIVE_POSITIONS[pair]
                    save_active_positions()
                    continue

                # Rule 2: Mid-Cap Fee-Protected Take Profit
                min_fee_tp = entry_price * 1.003
                target_sell_price = max(tp_price, min_fee_tp)
                if latest_price >= target_sell_price:
                    log.info("🎯 [MIDCAP RULE 2: TAKE-PROFIT] %s: Price $%.4f reached Take-Profit target $%.4f. Executing SELL.", pair, latest_price, target_sell_price)
                    if not DRY_RUN and quantity > 0:
                        place_order(pair, "SELL", quantity=quantity)
                    del ACTIVE_POSITIONS[pair]
                    save_active_positions()
                    continue

                # Rule 3: Profit-Gated Rank Rotation (Rank-out + Net Profit >= +0.30%)
                if not is_in_top_rank and latest_price >= min_fee_tp:
                    log.info("🔄 [MIDCAP RULE 3: PROFIT-GATED RANK ROTATION] %s: Left top rank & reached net profit ($%.4f >= $%.4f). Executing SELL.", pair, latest_price, min_fee_tp)
                    if not DRY_RUN and quantity > 0:
                        place_order(pair, "SELL", quantity=quantity)
                    del ACTIVE_POSITIONS[pair]
                    save_active_positions()
                    continue

                # Rule 4: 4-Hour Post 00:00 UTC Recovery Cutoff
                expiry_secs = getattr(config, "MIDCAP_RANK_OUT_EXPIRY_HOURS", 4) * 3600
                if not is_in_top_rank and time_elapsed >= expiry_secs:
                    log.info("⏰ [MIDCAP RULE 4: 4-HOUR EXPIRY] %s: 4-hour recovery window expired. Executing SELL.", pair)
                    if not DRY_RUN and quantity > 0:
                        place_order(pair, "SELL", quantity=quantity)
                    del ACTIVE_POSITIONS[pair]
                    save_active_positions()
                    continue

                log.info("[MIDCAP] %s: Position active (In Top Rank: %s, Holding: %dm, Price: $%.4f, Stop: $%.4f, TP: $%.4f)", pair, is_in_top_rank, int(time_elapsed // 60), latest_price, stop_price, tp_price)
                continue

        # Strategy Entry Signal: Only buy if pair is in top_leaders
        if pair not in top_leaders:
            continue

        realized_vol = df[price_col].pct_change().rolling(30, min_periods=5).std().iloc[-1]
        if realized_vol is None or np.isnan(realized_vol) or realized_vol <= 0:
            realized_vol = getattr(config, "SIZING_REFERENCE_VOL", 0.0006)

        vol_24h = df[price_col].pct_change().rolling(1440, min_periods=30).std().iloc[-1]
        if vol_24h is None or np.isnan(vol_24h) or vol_24h <= 0:
            vol_24h = realized_vol

        vol_distance = latest_price * vol_24h * np.sqrt(1440)
        stop_loss_k = getattr(config, "MIDCAP_STOP_LOSS_K", 2.5)
        take_profit_k = getattr(config, "MIDCAP_TAKE_PROFIT_K", 2.0)

        stop_price = latest_price - (stop_loss_k * vol_distance)
        take_profit_price = latest_price + (take_profit_k * vol_distance)

        # Mid-Cap Allocation (5% = $5,000 USD max per coin)
        target_usd_cost = account_equity * getattr(config, "MIDCAP_MAX_POSITION_PER_ASSET", 0.05)

        try:
            bal_resp = get_balance()
            if isinstance(bal_resp, dict) and bal_resp.get("Success"):
                free_usd = float(bal_resp.get("SpotWallet", {}).get("USD", {}).get("Free", account_equity))
                if target_usd_cost > free_usd:
                    target_usd_cost = max(0.0, free_usd * 0.95)
        except Exception:
            pass

        if target_usd_cost <= 0:
            continue

        coin_quantity = target_usd_cost / max(1e-6, latest_price)

        ACTIVE_POSITIONS[pair] = {
            "entry_time": time.time(),
            "entry_price": latest_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "quantity": coin_quantity,
            "sleeve": "midcap",
        }
        save_active_positions()

        if not DRY_RUN:
            log.info("🚀 [MIDCAP LIVE ORDER] Executing BUY for %s: $%.2f USD (%.4f coins @ $%.2f)", pair, target_usd_cost, coin_quantity, latest_price)
            place_order(pair, "BUY", quantity=coin_quantity)
        else:
            log.info("📝 [MIDCAP DRY RUN] Simulated BUY for %s: $%.2f USD (%.4f coins @ $%.2f)", pair, target_usd_cost, coin_quantity, latest_price)


def run_once():
    """Single execution step: fetch latest prices for all 23 coins and process Large-Cap & Mid-Cap sleeves."""
    poll_once()
    raw = load_all(getattr(config, "UNIVERSE", LIVE_PAIRS))

    if not raw:
        log.warning("No market data available yet.")
        return

    # Process Sleeve 1 (12 Large-Cap Coins)
    process_largecap_sleeve(raw)

    # Process Sleeve 2 (11 Mid-Cap Coins)
    process_midcap_sleeve(raw)


def run_forever(interval: int = config.POLL_INTERVAL_SECONDS):
    log.info("Starting live dual-sleeve strategy loop (DRY_RUN=%s)", DRY_RUN)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Error in main strategy loop")
        time.sleep(interval)


if __name__ == "__main__":
    run_forever()

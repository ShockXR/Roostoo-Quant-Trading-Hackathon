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


def get_true_entry_price(pair: str, fallback_price: float) -> float:
    """Reads data_store/trades_log.csv to retrieve the true historical purchase fill price."""
    trades_file = os.path.join(getattr(config, "STORE_DIR", "data_store"), "trades_log.csv")
    if os.path.exists(trades_file):
        try:
            df = pd.read_csv(trades_file)
            buy_df = df[(df["pair"] == pair) & (df["side"].str.upper() == "BUY")]
            if not buy_df.empty:
                prices_series = buy_df["price"]
                for p_val in reversed(prices_series.tolist()):
                    try:
                        p_float = float(p_val)
                        if p_float > 0:
                            log.info("🎯 [TRUE ENTRY PRICE RESOLVED] %s: Found true historical purchase price $%.4f from trades_log.csv", pair, p_float)
                            return p_float
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            log.error("Failed to read true entry price for %s: %s", pair, e)
    return fallback_price


def adopt_wallet_holdings(raw_data: dict[str, pd.DataFrame]):
    """
    Queries live Roostoo wallet balance (/v3/balance) and automatically adopts any existing 
    wallet coin holdings (e.g. BNB, POL, ONDO, SEI, CFX) into ACTIVE_POSITIONS tracking.
    Uses get_true_entry_price() to preserve true historical cost basis across bot restarts.
    """
    try:
        resp = get_balance()
        if not resp or not isinstance(resp, dict) or not resp.get("Success"):
            return
        wallet = resp.get("SpotWallet", {})
        adopted_count = 0
        for coin, info in wallet.items():
            if coin == "USD":
                continue
            pair = f"{coin}/USD"
            free_q = float(info.get("Free", 0.0) or info.get("free", 0.0))
            froz_q = float(info.get("Frozen", 0.0) or info.get("frozen", 0.0))
            total_qty = free_q + froz_q
            if total_qty <= 0:
                continue

            if pair not in ACTIVE_POSITIONS or ACTIVE_POSITIONS[pair].get("quantity", 0.0) < total_qty * 0.95:
                latest_p = 1.0
                if pair in raw_data and not raw_data[pair].empty:
                    price_col = "close" if "close" in raw_data[pair].columns else "last_price"
                    latest_p = float(raw_data[pair][price_col].iloc[-1])
                    vol_24h = raw_data[pair][price_col].pct_change().rolling(1440, min_periods=5).std().iloc[-1]
                    if vol_24h is None or np.isnan(vol_24h) or vol_24h <= 0:
                        vol_24h = 0.001
                else:
                    vol_24h = 0.001

                sleeve = "largecap" if pair in getattr(config, "LARGECAP_UNIVERSE", []) else "midcap"
                sl_k = getattr(config, "STOP_LOSS_K", 3.0) if sleeve == "largecap" else getattr(config, "MIDCAP_STOP_LOSS_K", 3.5)
                tp_k = getattr(config, "TAKE_PROFIT_K", 1.5) if sleeve == "largecap" else getattr(config, "MIDCAP_TAKE_PROFIT_K", 2.0)

                true_entry_p = get_true_entry_price(pair, fallback_price=latest_p)
                vol_dist = latest_p * vol_24h * np.sqrt(1440)
                stop_p = true_entry_p - (sl_k * vol_dist)
                tp_p = true_entry_p + (tp_k * vol_dist)

                ACTIVE_POSITIONS[pair] = {
                    "entry_time": time.time(),
                    "entry_price": true_entry_p,
                    "stop_price": stop_p,
                    "take_profit_price": tp_p,
                    "quantity": total_qty,
                    "sleeve": sleeve,
                    "is_adopted": True,
                }
                adopted_count += 1
                log.info("🛡️ [ADOPTED HOLDING] Adopted live wallet holding for %s: %.4f coins @ True Entry $%.4f (Stop: $%.4f, TP: $%.4f)", pair, total_qty, true_entry_p, stop_p, tp_p)

        if adopted_count > 0:
            save_active_positions()
    except Exception as e:
        log.error("Failed to adopt wallet holdings: %s", e)


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

            # Track highest peak price reached
            highest_price = max(pos.get("highest_price", entry_price), latest_price)
            pos["highest_price"] = highest_price

            hold_secs = getattr(config, "REVERSAL_HOLD_BARS", 1440) * 60
            time_elapsed = time.time() - entry_time

            # Rule 1: Volatility Stop-Loss Exit (3.0 * Volatility distance)
            if latest_price <= stop_price:
                log.warning("🚨 [LARGECAP RULE 1: STOP-LOSS] %s: Price $%.4f dropped below Stop-Loss $%.4f (3x Vol). Executing SELL.", pair, latest_price, stop_price)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                save_active_positions()
                record_last_exit_price(pair, latest_price)
                continue

            # Volatility-Adjusted Dynamic Trailing Profit Ratchet Check for Large-Caps
            vol_24h = df[price_col].pct_change().rolling(1440, min_periods=30).std().iloc[-1]
            if vol_24h is None or np.isnan(vol_24h) or vol_24h <= 0:
                vol_24h = 0.001
            vol_dist_pct = max(0.006, vol_24h * np.sqrt(1440))

            highest_gain_pct = (highest_price - entry_price) / max(1e-6, entry_price)
            trailing_floor_price = 0.0
            active_tier_name = ""

            # Dynamic Tiers based on Volatility Distance
            dynamic_tiers = [
                (3.5 * vol_dist_pct, 2.8 * vol_dist_pct),
                (2.0 * vol_dist_pct, 1.5 * vol_dist_pct),
                (1.0 * vol_dist_pct, 0.7 * vol_dist_pct),
                (0.5 * vol_dist_pct, max(0.0025, 0.3 * vol_dist_pct)),
            ]

            for peak_req, floor_lock in dynamic_tiers:
                if highest_gain_pct >= peak_req:
                    trailing_floor_price = entry_price * (1.0 + floor_lock)
                    active_tier_name = f"+{peak_req*100:.2f}% Peak -> +{(floor_lock - 0.002)*100:.2f}% Net Floor"
                    break

            if trailing_floor_price > 0.0 and latest_price <= trailing_floor_price:
                locked_net_pct = ((latest_price - entry_price) / max(1e-6, entry_price) - 0.0020) * 100.0
                log.info("🎯 [LARGECAP TRAILING RATCHET PROFIT] %s: Price $%.4f hit Trailing Floor $%.4f (%s, Entry: $%.4f). Executing SELL (Net PnL: +%.2f%%).", pair, latest_price, trailing_floor_price, active_tier_name, entry_price, locked_net_pct)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                save_active_positions()
                record_last_exit_price(pair, latest_price)
                continue

            # Rule 2: Take-Profit Target Exit
            min_fee_tp = entry_price * 1.003
            target_sell_price = max(tp_price, min_fee_tp)

            if latest_price >= target_sell_price:
                log.info("🎯 [LARGECAP RULE 2: TAKE-PROFIT] %s: Price $%.4f reached Take-Profit target $%.4f (Entry: $%.4f). Executing SELL.", pair, latest_price, target_sell_price, entry_price)
                if not DRY_RUN and quantity > 0:
                    place_order(pair, "SELL", quantity=quantity)
                del ACTIVE_POSITIONS[pair]
                save_active_positions()
                record_last_exit_price(pair, latest_price)
                continue

            else:
                log.info("[LARGECAP] %s: Position active (Holding: %dm, Price: $%.4f, Stop: $%.4f, TP: $%.4f)", pair, int(time_elapsed // 60), latest_price, stop_price, tp_price)
                continue

        # Check Price-Separation Re-Entry Guard (Requires 1.0% distance from last exit price)
        last_exit_p = load_last_exit_prices().get(pair, 0.0)
        if last_exit_p > 0.0:
            price_diff_pct = abs(latest_price - last_exit_p) / last_exit_p
            req_diff = getattr(config, "LARGECAP_REENTRY_SEPARATION_PCT", 0.0100)
            if price_diff_pct < req_diff:
                log.info("⏳ [LARGECAP PRICE GUARD] %s: Current price $%.4f is within %.1f%% of last exit $%.4f (Diff: %.2f%%). Skipping re-entry.", pair, latest_price, req_diff * 100.0, last_exit_p, price_diff_pct * 100.0)
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


LAST_EXIT_FILE = os.path.join(getattr(config, "STORE_DIR", "data_store"), "last_exit_prices.json")


def load_last_exit_prices() -> dict:
    if os.path.exists(LAST_EXIT_FILE):
        try:
            with open(LAST_EXIT_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def record_last_exit_price(pair: str, price: float):
    exits = load_last_exit_prices()
    exits[pair] = price
    try:
        os.makedirs(os.path.dirname(LAST_EXIT_FILE), exist_ok=True)
        with open(LAST_EXIT_FILE, "w") as f:
            json.dump(exits, f, indent=2)
        log.info("💾 [LAST EXIT PRICE RECORDED] %s: Recorded last exit price $%.4f", pair, price)
    except Exception as e:
        log.error("Failed to save last exit price file: %s", e)


COOLDOWN_FILE = os.path.join(getattr(config, "STORE_DIR", "data_store"), "cooldowns.json")


def load_cooldowns() -> dict:
    if os.path.exists(COOLDOWN_FILE):
        try:
            with open(COOLDOWN_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def record_cooldown(pair: str, hours: float = 2.0):
    cooldowns = load_cooldowns()
    cooldowns[pair] = time.time() + (hours * 3600)
    try:
        os.makedirs(os.path.dirname(COOLDOWN_FILE), exist_ok=True)
        with open(COOLDOWN_FILE, "w") as f:
            json.dump(cooldowns, f, indent=2)
    except Exception as e:
        log.error("Failed to save cooldown file: %s", e)


def is_in_cooldown(pair: str) -> bool:
    cooldowns = load_cooldowns()
    expiry = cooldowns.get(pair, 0)
    if time.time() < expiry:
        rem_mins = int((expiry - time.time()) / 60)
        return True, rem_mins
    return False, 0


MIDCAP_LEADERS_CACHE = {"timestamp": 0.0, "leaders": set(), "returns": {}}


def get_cached_midcap_leaders(raw_data: dict[str, pd.DataFrame], pairs: list[str]) -> tuple[set[str], dict[str, float]]:
    """Caches mid-cap momentum leaders for 1 hour to filter out 1m micro-noise while enabling 24/7 intraday profit recycling."""
    now = time.time()
    interval_secs = getattr(config, "MIDCAP_RANK_UPDATE_INTERVAL_MINUTES", 60) * 60
    if (now - MIDCAP_LEADERS_CACHE["timestamp"] >= interval_secs) or not MIDCAP_LEADERS_CACHE["leaders"]:
        leaders, returns = get_top_midcap_leaders(raw_data, pairs)
        if leaders:
            MIDCAP_LEADERS_CACHE["timestamp"] = now
            MIDCAP_LEADERS_CACHE["leaders"] = leaders
            MIDCAP_LEADERS_CACHE["returns"] = returns
            log.info("📊 [HOURLY MIDCAP RANK UPDATE] Refreshed top momentum leaders: %s", sorted(list(leaders)))
    return MIDCAP_LEADERS_CACHE["leaders"], MIDCAP_LEADERS_CACHE["returns"]


def process_midcap_sleeve(raw_data: dict[str, pd.DataFrame]):
    """Processes Sleeve 2 (11 Mid-Cap Coins) using blend_v10 Dual Momentum + Profit-Gated Rank Rotation."""
    midcap_pairs = getattr(config, "MIDCAP_UNIVERSE", [])
    account_equity = getattr(config, "INITIAL_CAPITAL", 100000.0)

    top_leaders, returns_24h = get_cached_midcap_leaders(raw_data, midcap_pairs)

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

                # Track highest peak price reached for Mid-Cap
                highest_price = max(pos.get("highest_price", entry_price), latest_price)
                pos["highest_price"] = highest_price

                time_elapsed = time.time() - entry_time
                is_in_top_rank = pair in top_leaders

                # Rule 1: Mid-Cap Volatility Stop Loss (3.5 * Volatility)
                if latest_price <= stop_price:
                    log.warning("🚨 [MIDCAP RULE 1: STOP-LOSS] %s: Price $%.4f dropped below Stop-Loss $%.4f (3.5x Vol). Executing SELL & setting 2h cool-down.", pair, latest_price, stop_price)
                    if not DRY_RUN and quantity > 0:
                        place_order(pair, "SELL", quantity=quantity)
                    del ACTIVE_POSITIONS[pair]
                    save_active_positions()
                    record_last_exit_price(pair, latest_price)
                    record_cooldown(pair, hours=getattr(config, "POST_STOP_LOSS_COOLDOWN_HOURS", 2.0))
                    continue

                # Volatility-Adjusted Dynamic Trailing Profit Ratchet Check for Mid-Caps
                vol_24h = df[price_col].pct_change().rolling(1440, min_periods=30).std().iloc[-1]
                if vol_24h is None or np.isnan(vol_24h) or vol_24h <= 0:
                    vol_24h = 0.001
                vol_dist_pct = max(0.007, vol_24h * np.sqrt(1440))

                highest_gain_pct = (highest_price - entry_price) / max(1e-6, entry_price)
                trailing_floor_price = 0.0
                active_tier_name = ""

                # Dynamic Tiers based on Volatility Distance
                dynamic_tiers = [
                    (3.5 * vol_dist_pct, 2.8 * vol_dist_pct),
                    (2.0 * vol_dist_pct, 1.5 * vol_dist_pct),
                    (1.0 * vol_dist_pct, 0.7 * vol_dist_pct),
                    (0.5 * vol_dist_pct, max(0.0025, 0.3 * vol_dist_pct)),
                ]

                for peak_req, floor_lock in dynamic_tiers:
                    if highest_gain_pct >= peak_req:
                        trailing_floor_price = entry_price * (1.0 + floor_lock)
                        active_tier_name = f"+{peak_req*100:.2f}% Peak -> +{(floor_lock - 0.002)*100:.2f}% Net Floor"
                        break

                if trailing_floor_price > 0.0 and latest_price <= trailing_floor_price:
                    locked_net_pct = ((latest_price - entry_price) / max(1e-6, entry_price) - 0.0020) * 100.0
                    log.info("🎯 [MIDCAP TRAILING RATCHET PROFIT] %s: Price $%.4f hit Trailing Floor $%.4f (%s, Entry: $%.4f). Executing SELL (Net PnL: +%.2f%%).", pair, latest_price, trailing_floor_price, active_tier_name, entry_price, locked_net_pct)
                    if not DRY_RUN and quantity > 0:
                        place_order(pair, "SELL", quantity=quantity)
                    del ACTIVE_POSITIONS[pair]
                    save_active_positions()
                    record_last_exit_price(pair, latest_price)
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
                    record_last_exit_price(pair, latest_price)
                    continue

                # Rule 3: Profit-Gated Rank Rotation (Rank-out + Net Profit >= +0.30%)
                if not is_in_top_rank and latest_price >= min_fee_tp:
                    log.info("🔄 [MIDCAP RULE 3: PROFIT-GATED RANK ROTATION] %s: Left top rank & reached net profit ($%.4f >= $%.4f). Executing SELL.", pair, latest_price, min_fee_tp)
                    if not DRY_RUN and quantity > 0:
                        place_order(pair, "SELL", quantity=quantity)
                    del ACTIVE_POSITIONS[pair]
                    save_active_positions()
                    record_last_exit_price(pair, latest_price)
                    continue

                log.info("[MIDCAP] %s: Position active (In Top Rank: %s, Holding: %dm, Price: $%.4f, Stop: $%.4f, TP: $%.4f)", pair, is_in_top_rank, int(time_elapsed // 60), latest_price, stop_price, tp_price)
                continue

        # Strategy Entry Signal: Only buy if pair is in top_leaders
        if pair not in top_leaders:
            continue

        # Check Price-Separation Re-Entry Guard (Requires 2.0% distance from last exit price)
        last_exit_p = load_last_exit_prices().get(pair, 0.0)
        if last_exit_p > 0.0:
            price_diff_pct = abs(latest_price - last_exit_p) / last_exit_p
            req_diff = getattr(config, "MIDCAP_REENTRY_SEPARATION_PCT", 0.0200)
            if price_diff_pct < req_diff:
                log.info("⏳ [MIDCAP PRICE GUARD] %s: Current price $%.4f is within %.1f%% of last exit $%.4f (Diff: %.2f%%). Skipping re-entry.", pair, latest_price, req_diff * 100.0, last_exit_p, price_diff_pct * 100.0)
                continue

        # Check Post-Exit Cool-Down Lockout
        in_cd, rem_m = is_in_cooldown(pair)
        if in_cd:
            log.info("⏳ [MIDCAP COOL-DOWN] %s is in 2-hour post-exit lockout (%d mins remaining). Skipping re-entry.", pair, rem_m)
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

    # Adopt any existing live wallet holdings into ACTIVE_POSITIONS without forced selling
    adopt_wallet_holdings(raw)

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

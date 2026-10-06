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

# Live Trading White-list: Coins listed here place REAL live orders on Roostoo exchange.
LIVE_PAIRS = [
    "BTC/USD", 
    "ETH/USD", 
    "SOL/USD", 
    "BNB/USD", 
    "XRP/USD", 
    "ADA/USD"
]

SELECTED_STRATEGY = "reversal"  # Options: 'reversal', 'momentum', 'mean_reversion', 'buy_hold'


# Track active open positions per pair: {pair: entry_timestamp}
ACTIVE_POSITIONS = {}


def run_once():
    """Single execution step: fetch latest prices, compute strategy signals, execute trades."""
    poll_once()
    raw = load_all(config.UNIVERSE)

    if not raw:
        log.warning("No market data available yet.")
        return

    # Focus processing strictly on Whitelisted LIVE_PAIRS (or all if 'ALL' is specified)
    active_pairs = LIVE_PAIRS if "ALL" not in LIVE_PAIRS else config.UNIVERSE

    for pair in active_pairs:
        if pair not in raw or raw[pair].empty:
            continue

        # Prevent duplicate orders if position is already active
        if pair in ACTIVE_POSITIONS:
            entry_time = ACTIVE_POSITIONS[pair]
            hold_secs = getattr(config, "REVERSAL_HOLD_BARS", 1440) * 60
            if time.time() - entry_time >= hold_secs:
                log.info("%s: 24h hold period completed, resetting position", pair)
                del ACTIVE_POSITIONS[pair]
            else:
                log.info("%s: Position already active (holding), skipping duplicate buy", pair)
                continue

        df = raw[pair]
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

        # Calculate realized volatility for multi-factor sizing
        price_col = "close" if "close" in df.columns else "last_price"
        realized_vol = df[price_col].pct_change().rolling(30, min_periods=5).std().iloc[-1]

        # Apply Multi-Factor Position Sizing Formula
        final_size = sizing.calculate_position_size(raw_signal, realized_vol)
        if final_size <= 0:
            continue

        log.info("%s: STRATEGY SIGNAL TRIGGERED (%s) size=%.4f (raw=%.4f, vol=%.6f)", pair, SELECTED_STRATEGY, final_size, raw_signal, realized_vol if (realized_vol is not None and not np.isnan(realized_vol)) else 0.0)

        # Convert capital fraction into target USD cost
        latest_price = df[price_col].iloc[-1]
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

        # Mark position active to block duplicate buys
        ACTIVE_POSITIONS[pair] = time.time()

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

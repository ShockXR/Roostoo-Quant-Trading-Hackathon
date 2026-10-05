"""
Live trading loop: polling -> compute quantitative strategy signal -> size -> execute.
Runs pure rule-based strategies (e.g. Mean-Reversion / Reversal) without any ML models.
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config, costs, sizing, strategy
    from data.collector import poll_once
    from data.store import load_all
    from execution.trader import place_order, log_trade_to_csv
except ImportError:
    from . import config, costs, sizing, strategy
    from .data.collector import poll_once
    from .data.store import load_all
    from .execution.trader import place_order, log_trade_to_csv

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


def run_once():
    """Single execution step: fetch latest prices, compute strategy signals, execute trades."""
    poll_once()
    raw = load_all(config.UNIVERSE)

    if not raw:
        log.warning("No market data available yet.")
        return

    for pair in config.UNIVERSE:
        if pair not in raw or raw[pair].empty:
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

        latest_signal = signals.iloc[-1]
        if latest_signal <= 0:
            log.info("%s: strategy %s signal=%.4f (no action)", pair, SELECTED_STRATEGY, latest_signal)
            continue

        log.info("%s: STRATEGY SIGNAL TRIGGERED (%s) target_weight=%.4f", pair, SELECTED_STRATEGY, latest_signal)

        # Per-coin Live vs Paper-trade logic:
        if not DRY_RUN and (pair in LIVE_PAIRS or "ALL" in LIVE_PAIRS):
            log.info("🚀 [LIVE ORDER] Executing REAL order on Roostoo for %s with size=%.4f", pair, latest_signal)
            place_order(pair, "BUY", quantity=latest_signal)
        else:
            log.info("📝 [PAPER TRADE / DRY RUN] Simulated BUY order for %s with size=%.4f", pair, latest_signal)
            log_trade_to_csv(pair, "BUY", quantity=latest_signal, execution_type="PAPER_TRADE")


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

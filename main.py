"""
Live loop for the production model (config.PRODUCTION_MODEL).

Once an hour, shortly after the hour closes:
  1. fetch finished hourly bars for config.PRODUCTION_SYMBOLS (Binance public
     data, data/binance_bars.py);
  2. compute target weights with the same strategies.generate() the backtest
     uses, and take the latest bar's row;
  3. cap gross exposure, turn changed targets into orders
     (execution/rebalance.py) and send them to Roostoo as MARKET orders.

DRY_RUN (the default) trades a paper wallet seeded from the exchange's
InitialWallet and logs every intended order; set ROOSTOO_LIVE=1 to send
real orders. Start with scripts/run_live.py.
"""
import json
import logging
import os
import pathlib
import time

import pandas as pd

from . import config, strategies
from .data.binance_bars import fetch_hourly
from .execution import rebalance

log = logging.getLogger("main")

DRY_RUN = os.environ.get("ROOSTOO_LIVE", "") != "1"


class PaperBroker:
    """Fills market orders at the Roostoo last price with the taker fee."""

    def __init__(self, cash: float):
        self.holdings = {"USD": cash}

    def balance(self) -> dict[str, float]:
        return dict(self.holdings)

    def place(self, order: rebalance.Order, price: float) -> bool:
        coin = order.pair.split("/")[0]
        sign = 1 if order.side == "BUY" else -1
        self.holdings[coin] = self.holdings.get(coin, 0.0) + sign * order.quantity
        self.holdings["USD"] -= sign * order.quantity * price + order.quantity * price * config.TAKER_FEE
        return True


class RoostooBroker:
    def balance(self) -> dict[str, float]:
        from .execution.trader import get_balance
        resp = get_balance()
        if not resp or not resp.get("Success"):
            raise RuntimeError(f"balance failed: {resp}")
        return rebalance.parse_balance(resp)

    def place(self, order: rebalance.Order, price: float) -> bool:
        from .execution.trader import place_order
        resp = place_order(order.pair, order.side, quantity=order.quantity)
        ok = bool(resp and resp.get("Success"))
        log.info("order %s %s %s -> %s", order.side, order.quantity, order.pair, resp)
        return ok


def latest_targets(now_ms: int | None = None) -> tuple[pd.Timestamp, dict[str, float]]:
    """Target weight per Roostoo pair from the latest finished hourly bar."""
    bars = {s: fetch_hourly(s, config.LIVE_HISTORY_DAYS, now_ms) for s in config.PRODUCTION_SYMBOLS}
    idx = sorted(set.intersection(*(set(b.index) for b in bars.values())))
    bars = {s: b.loc[idx] for s, b in bars.items()}
    t = strategies.generate(config.PRODUCTION_MODEL, bars, config.PRODUCTION_PARAMS)
    last = t.iloc[-1]
    return t.index[-1], {config.PRODUCTION_SYMBOLS[s]: float(last[s]) for s in t.columns}


def _prices() -> dict[str, float]:
    from .execution.trader import get_ticker
    resp = get_ticker()
    if not resp or not resp.get("Success"):
        raise RuntimeError(f"ticker failed: {resp}")
    return {p: float(resp["Data"][p]["LastPrice"]) for p in config.PRODUCTION_SYMBOLS.values()}


def _load_state(path: pathlib.Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def run_once(broker, state: dict, pair_info: dict, now_ms: int | None = None) -> dict:
    bar_time, targets = latest_targets(now_ms)
    expected = pd.Timestamp(now_ms or time.time() * 1000, unit="ms", tz="UTC").floor("1h") - pd.Timedelta("1h")
    if bar_time < expected:
        log.warning("latest bar %s is stale (expected %s); not trading this hour", bar_time, expected)
        return state
    targets = rebalance.cap_gross(targets)
    prices = _prices()
    holdings = broker.balance()
    equity = holdings.get("USD", 0.0) + sum(holdings.get(p.split("/")[0], 0.0) * prices[p] for p in targets)

    peak = max(state.get("peak_equity", equity), equity)
    halted = state.get("halted", False)
    ks = config.LIVE_KILL_SWITCH_DRAWDOWN
    if ks is not None and equity < peak * (1 - ks):
        if not halted:
            log.error("equity %.2f is %.1f%% below peak %.2f: kill switch, going flat", equity,
                      100 * (1 - equity / peak), peak)
        halted = True
    if halted:
        targets = {p: 0.0 for p in targets}

    orders = rebalance.plan_orders(targets, holdings, prices, pair_info, state.get("targets"))
    done = dict(state.get("targets") or {})
    failed = set()
    for o in orders:
        log.info("%s%s %s %s (~$%.2f)", "[DRY RUN] " if DRY_RUN else "", o.side, o.quantity, o.pair, o.notional)
        if not broker.place(o, prices[o.pair]):
            failed.add(o.pair)
    # remember a target only once it is acted on, so a failed order is retried next hour
    for p, w in targets.items():
        if p not in failed:
            done[p] = w
    held = {p: round(w, 4) for p, w in targets.items() if w > 0}
    log.info("bar %s equity %.2f targets %s orders %d", bar_time, equity, held or "cash", len(orders))
    return {"targets": done, "peak_equity": peak, "halted": halted, "last_bar": str(bar_time),
            "equity": equity, "paper_holdings": broker.balance() if DRY_RUN else None}


def run_forever():
    from .execution.trader import get_exchange_info
    info = get_exchange_info()
    pair_info = info["TradePairs"]
    path = pathlib.Path(config.LIVE_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state(path)
    if DRY_RUN:
        broker = PaperBroker(float(info.get("InitialWallet", {}).get("USD", config.INITIAL_CAPITAL)))
        if state.get("paper_holdings"):
            broker.holdings = state["paper_holdings"]
    else:
        broker = RoostooBroker()
    log.info("model %s, %d pairs, DRY_RUN=%s", config.PRODUCTION_MODEL, len(config.PRODUCTION_SYMBOLS), DRY_RUN)
    while True:
        try:
            state = run_once(broker, state, pair_info)
            path.write_text(json.dumps(state, indent=2))
        except Exception:
            log.exception("error in live loop; retrying next hour")
        now = time.time()
        time.sleep(3600 - now % 3600 + 30)   # 30 s after the next hour closes


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_forever()

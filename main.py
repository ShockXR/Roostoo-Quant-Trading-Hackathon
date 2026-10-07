"""
Live loop for the production model (config.PRODUCTION_MODEL, the large-cap
book) or, with book="mid", the mid-cap model (config.MIDCAP_MODEL).

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


def latest_targets(now_ms: int | None = None, book: str = "large",
                   pair_info: dict | None = None) -> tuple[pd.Timestamp, dict[str, float]]:
    """Target weight per Roostoo pair from the latest finished hourly bar."""
    if book == "mid":
        return _latest_mid_targets(now_ms, pair_info or {})
    bars = {s: fetch_hourly(s, config.LIVE_HISTORY_DAYS, now_ms) for s in config.PRODUCTION_SYMBOLS}
    idx = sorted(set.intersection(*(set(b.index) for b in bars.values())))
    bars = {s: b.loc[idx] for s, b in bars.items()}
    t = strategies.generate(config.PRODUCTION_MODEL, bars, config.PRODUCTION_PARAMS)
    last = t.iloc[-1]
    return t.index[-1], {config.PRODUCTION_SYMBOLS[s]: float(last[s]) for s in t.columns}


def _latest_mid_targets(now_ms: int | None, pair_info: dict) -> tuple[pd.Timestamp, dict[str, float]]:
    """Mid-cap book: this month's liquidity ranks 21-40 that Roostoo lists,
    ranked and regime-filtered exactly as in the backtest (past months use
    their own membership), BTC as the market proxy."""
    from .data.binance_universe import mid_membership
    listed = {p.split("/")[0] for p, v in pair_info.items()
              if v.get("CanTrade", True) and v.get("AssetType", "crypto") == "crypto"}
    months = {m: [s for s in syms if s[:-4] in listed] for m, syms in mid_membership(now_ms).items()}
    symbols = sorted({s for syms in months.values() for s in syms})
    market = "BTCUSDT"
    raw = {s: fetch_hourly(s, config.MIDCAP_HISTORY_DAYS, now_ms) for s in symbols + [market]}
    idx = raw[market].index
    bars, live = {}, {}
    for s, b in raw.items():
        live[s] = pd.Series((idx >= b.index[0]) & (idx <= b.index[-1]), index=idx) if len(b) else \
            pd.Series(False, index=idx)
        b = b.reindex(idx)
        b["close"] = b["close"].ffill().bfill()
        for col in ("open", "high", "low"):
            b[col] = b[col].fillna(b["close"])
        bars[s] = b.fillna({"volume": 0.0})
    month = pd.Series(idx.strftime("%Y-%m"), index=idx)
    eligible = pd.DataFrame({s: month.map(lambda m, s=s: s in months.get(m, [])) & live[s] for s in bars})
    eligible[market] = False
    t = strategies.generate(config.MIDCAP_MODEL, bars, config.MIDCAP_PARAMS, eligible=eligible)
    last = t.iloc[-1]
    return t.index[-1], {f"{s[:-4]}/USD": float(last[s]) for s in symbols}


def _prices(pairs) -> dict[str, float]:
    from .execution.trader import get_ticker
    resp = get_ticker()
    if not resp or not resp.get("Success"):
        raise RuntimeError(f"ticker failed: {resp}")
    data = resp["Data"]
    return {p: float(data[p]["LastPrice"]) for p in pairs if p in data}


def _load_state(path: pathlib.Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def run_once(broker, state: dict, pair_info: dict, now_ms: int | None = None, book: str = "large") -> dict:
    bar_time, targets = latest_targets(now_ms, book, pair_info)
    # a pair dropped from the universe since the last run is closed, not forgotten
    targets = {**{p: 0.0 for p in (state.get("targets") or {})}, **targets}
    expected = pd.Timestamp(now_ms or time.time() * 1000, unit="ms", tz="UTC").floor("1h") - pd.Timedelta("1h")
    if bar_time < expected:
        log.warning("latest bar %s is stale (expected %s); not trading this hour", bar_time, expected)
        return state
    targets = rebalance.cap_gross(targets)
    prices = _prices(targets)
    targets = {p: w for p, w in targets.items() if p in prices}   # pair no longer quoted: skip
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


def run_forever(book: str = config.LIVE_BOOK):
    """book: "large" (config.PRODUCTION_MODEL, the competition default) or
    "mid" (config.MIDCAP_MODEL). Each book keeps its own state file."""
    from .execution.trader import get_exchange_info
    info = get_exchange_info()
    pair_info = info["TradePairs"]
    path = pathlib.Path(config.LIVE_STATE_FILE.replace(".json", f"_{book}.json") if book != "large"
                        else config.LIVE_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state(path)
    if DRY_RUN:
        broker = PaperBroker(float(info.get("InitialWallet", {}).get("USD", config.INITIAL_CAPITAL)))
        if state.get("paper_holdings"):
            broker.holdings = state["paper_holdings"]
    else:
        broker = RoostooBroker()
    model = config.MIDCAP_MODEL if book == "mid" else config.PRODUCTION_MODEL
    log.info("book %s, model %s, DRY_RUN=%s", book, model, DRY_RUN)
    while True:
        try:
            state = run_once(broker, state, pair_info, book=book)
            path.write_text(json.dumps(state, indent=2))
        except Exception:
            log.exception("error in live loop; retrying next hour")
        now = time.time()
        time.sleep(3600 - now % 3600 + 30)   # 30 s after the next hour closes


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_forever()

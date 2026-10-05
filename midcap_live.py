"""Live/paper runner for the mid-cap sleeve (docs/MIDCAP_RESEARCH.md).

Every cycle (one minute): read Roostoo quotes and holdings, mark equity, and
apply the staged drawdown de-risk. Once a day, after 00:00 UTC closes on
Binance, compute target weights from hourly history and trade toward them.
Between rebalances the bot holds, as in the backtest. A cut in size from the
drawdown schedule trades at once; size only comes back at the next daily
rebalance. After a restart the engine catches up on a missed rebalance.
"""
from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import time
from . import config
from .data.binance_live import hourly_panel
from .execution.broker import LiveBroker
from .execution.portfolio import PaperBroker, Quote, ExecutionState, Snapshot, Short, plan_orders
from .execution.trader import get_ticker
from .main import TradingEngine
from .strategies.midcap import build_weights, drawdown_scale, live_spec

log = logging.getLogger("midcap")
HOUR_MS = 3_600_000


@dataclass
class RebalanceState:
    hour_ms: int = 0           # rebalance hour these weights belong to
    weights: dict = field(default_factory=dict)  # unscaled target fractions per pair
    scale: float = 1.          # drawdown multiplier currently applied
    done: bool = True
    cycles: int = 0            # cycles spent trying to reach the current targets
    rejections: dict = field(default_factory=dict)  # pair -> times the exchange refused its order


def latest_rebalance_hour(now_ms, every_hours, delay_ms):
    """Most recent rebalance hour whose Binance kline has closed, as UTC ms."""
    period = every_hours * HOUR_MS
    return (now_ms - delay_ms) // period * period


def binance_weights(pairs, spec):
    """Signal source: target weights at a rebalance hour from Binance hourly closes."""
    def compute(hour_ms):
        panel = hourly_panel(pairs, hour_ms)
        row = build_weights(panel, spec).iloc[-1]
        if row.isna().all():
            raise ValueError("no rebalance row at the requested hour")
        return {pair: float(row[pair]) for pair in pairs}
    return compute


class MidcapEngine(TradingEngine):
    kill_switch = 1.0  # the latched 12% halt is replaced by the staged schedule below

    def __init__(self, broker, metadata, signal_source, pairs=None, rebalance=None, state=None,
                 state_path=None, event_path=None, equity_path=None):
        super().__init__(broker, metadata, strategy="midcap", state=state, state_path=state_path,
                         event_path=event_path, equity_path=equity_path)
        self.signal_source = signal_source
        self.pairs = list(config.MIDCAP_PAIRS if pairs is None else pairs)
        self.rebalance = rebalance or RebalanceState()

    def persist(self):
        if self.state_path:
            super().persist()
            path = Path(self.state_path)
            value = json.loads(path.read_text())
            value["rebalance"] = asdict(self.rebalance)
            path.write_text(json.dumps(value, allow_nan=False) + "\n")

    def step(self, quotes, now_ms):
        if self.state.uncertain and not isinstance(self.broker, LiveBroker):
            raise RuntimeError("previous paper order state is uncertain")
        quotes = {p: q for p, q in quotes.items() if p in self.pairs}
        if set(quotes) != set(self.pairs):
            raise ValueError(f"missing quotes: {sorted(set(self.pairs) - set(quotes))}")
        for q in quotes.values():
            if not 0 <= now_ms - q.timestamp <= config.MAX_QUOTE_AGE_MS:
                raise ValueError("stale/future quote; stop entire cycle")
        snapshot, equity = self.reconcile(quotes, now_ms)
        scale = drawdown_scale(equity / self.state.high_water - 1, config.MIDCAP_DRAWDOWN_SCHEDULE)
        hour = latest_rebalance_hour(now_ms, config.MIDCAP_REBALANCE_EVERY_HOURS, config.MIDCAP_REBALANCE_DELAY_MS)
        r = self.rebalance
        if hour != r.hour_ms:
            weights = self.signal_source(hour)  # raises until Binance has the closed kline
            self.rebalance = r = RebalanceState(hour, weights, scale, done=False)
            log.info("rebalance %s scale=%.2f weights=%s", hour, scale, {p: round(w, 4) for p, w in weights.items() if w})
        elif scale < r.scale:
            log.warning("drawdown de-risk: scale %.2f -> %.2f", r.scale, scale)
            r.scale, r.done, r.cycles, r.rejections = scale, False, 0, {}
        if r.done:
            self.persist()
            return []
        targets = {p: r.weights.get(p, 0.) * r.scale for p in self.pairs}
        orders = plan_orders(targets, snapshot, quotes, self.metadata, min_fraction=config.MIDCAP_REBALANCE_BAND)
        orders = [o for o in orders if r.rejections.get(o.pair, 0) < config.MIDCAP_MAX_REJECTIONS_PER_PAIR]
        r.cycles += 1
        if not orders or r.cycles > config.MIDCAP_MAX_REBALANCE_CYCLES:
            if orders:
                log.error("rebalance %s unfinished after %s cycles; holding until next", r.hour_ms, r.cycles - 1)
            r.done = True
            self.persist()
            return []
        events = self.submit_orders(orders, targets, quotes, now_ms)
        for pair in self.rejected:
            r.rejections[pair] = r.rejections.get(pair, 0) + 1
        # Trade once per rebalance, as in the backtest: done when every planned order
        # filled. Orders held back by rate, cash or exposure limits retry next cycle;
        # a pair the exchange keeps refusing is given up on for this rebalance.
        if len(events) == len(orders):
            r.done = True
            self.persist()
        return events


def ticker_quotes(pairs):
    response = get_ticker()
    if not response or not response.get("Success"):
        raise ValueError("ticker poll failed")
    stamp = int(response["ServerTime"])
    data = response.get("Data", {})
    return {p: Quote(float(data[p]["LastPrice"]), float(data[p]["MaxBid"]), float(data[p]["MinAsk"]), stamp)
            for p in pairs if p in data}


def restore_midcap_engine(broker, metadata, signal_source, state_path, event_path, equity_path):
    state, rebalance = ExecutionState(), None
    path = Path(state_path)
    if path.exists():
        saved = json.loads(path.read_text())
        if saved["mode"] != ("paper" if isinstance(broker, PaperBroker) else "live"):
            raise ValueError("paper/live state files must be separate")
        state = ExecutionState(**saved["execution"])
        if isinstance(broker, PaperBroker):
            portfolio = saved["portfolio"]
            broker.state = Snapshot(portfolio["cash"], portfolio["longs"],
                                    {p: Short(**s) for p, s in portfolio["shorts"].items()})
        if "rebalance" in saved:
            rebalance = RebalanceState(**saved["rebalance"])
    return MidcapEngine(broker, metadata, signal_source, state=state, rebalance=rebalance, state_path=state_path,
                        event_path=event_path, equity_path=equity_path)


def run_forever(engine, interval=None):
    interval = config.POLL_INTERVAL_SECONDS if interval is None else interval
    while True:
        started = time.monotonic()
        try:
            engine.step(ticker_quotes(engine.pairs), int(time.time() * 1000))
        except Exception:
            log.exception("mid-cap cycle failed; no assumed fills")
        time.sleep(max(0., interval - (time.monotonic() - started)))


def build(metadata, live, state_path, event_path, equity_path):
    pairs = config.MIDCAP_PAIRS
    missing = [p for p in pairs if not metadata.get(p, {}).get("CanTrade")]
    if missing:
        raise ValueError(f"mid-cap pairs not tradable on Roostoo: {missing}")
    broker = LiveBroker() if live else PaperBroker()
    return restore_midcap_engine(broker, metadata, binance_weights(pairs, live_spec(config)),
                                 state_path, event_path, equity_path)

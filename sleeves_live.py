"""Live/paper runner for the three-sleeve book (docs/SLEEVES.md).

Every cycle (one minute): read Roostoo quotes and the wallet, mark equity,
apply the staged drawdown de-risk and the small-cap stops. Once a day, after
the 00:00 UTC hourly kline closes on Binance, compute each sleeve's targets and
trade toward them. Between rebalances the book is held. If nothing has filled
by ACTIVITY_HOUR_UTC, the largest drift back to target is traded so every
competition day has a strategy trade.

Coins held in the wallet but outside every sleeve (left over from an earlier
bot) are quoted, valued and sold at the next rebalance.
"""
from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import time
from . import config
from .data.binance_live import tolerant_panel
from .execution.broker import LiveBroker
from .execution.portfolio import PaperBroker, Quote, ExecutionState, Snapshot, Short, plan_orders
from .execution.trader import get_ticker
from .midcap_live import MidcapEngine, latest_rebalance_hour
from .strategies.midcap import drawdown_scale
from .strategies.sleeves import LARGE, SLEEVES, SMALL, latest_weights, specs

log = logging.getLogger("sleeves")
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS


@dataclass
class SleeveState:
    hour_ms: int = 0
    weights: dict = field(default_factory=dict)   # sleeve -> {pair: weight} before scaling
    scale: float = 1.                              # drawdown multiplier currently applied
    push: bool = False                             # push-mode multipliers on for this rebalance
    regime: bool = False                           # BTC above its average at this rebalance
    done: bool = True
    cycles: int = 0
    rejections: dict = field(default_factory=dict)
    entries: dict = field(default_factory=dict)    # small-cap pair -> average entry price
    blocked: dict = field(default_factory=dict)    # pair -> ms until which new buys are barred
    last_fill_day: int = -1                        # UTC day number of the latest fill
    activity_day: int = -1                         # UTC day the activity check last ran


def binance_sleeve_weights(sleeve_pairs, history_hours=None, session=None):
    """Signal source: ({sleeve: {pair: weight}}, regime) at a rebalance hour from Binance hourly closes."""
    history_hours = history_hours or config.SLEEVE_HISTORY_HOURS

    def compute(hour_ms, held=()):
        kwargs = {} if session is None else {"session": session}
        panels = {sleeve: tolerant_panel(pairs, hour_ms, history_hours[sleeve],
                                         required=set(held) & set(pairs) | ({"BTC/USD"} if sleeve == LARGE else set()),
                                         **kwargs)
                  for sleeve, pairs in sleeve_pairs.items()}
        return latest_weights(panels, config)
    return compute


class SleeveEngine(MidcapEngine):
    def __init__(self, broker, metadata, signal_source, sleeve_pairs=None, sleeve=None, state=None,
                 state_path=None, event_path=None, equity_path=None):
        self.sleeve_pairs = {k: list(v) for k, v in (sleeve_pairs or config.SLEEVE_PAIRS).items()}
        pairs = [p for sleeve_list in self.sleeve_pairs.values() for p in sleeve_list]
        super().__init__(broker, metadata, signal_source, pairs=pairs, state=state, state_path=state_path,
                         event_path=event_path, equity_path=equity_path)
        self.sleeve_of = {p: s for s, ps in self.sleeve_pairs.items() for p in ps}
        self.specs = specs(config)
        self.rebalance = sleeve or SleeveState()

    def targets(self, r, quotes, now_ms):
        """Final target fractions: sleeve weights x drawdown scale x push multiplier, re-capped per sleeve."""
        out = {}
        for sleeve, weights in r.weights.items():
            spec = self.specs[sleeve]
            mult = r.scale * (config.PUSH_MULTIPLIER.get(sleeve, 1.) if r.push else 1.)
            sized = {p: min(w * mult, spec.max_weight) for p, w in weights.items() if p in quotes}
            gross = sum(sized.values())
            factor = min(1., spec.max_gross / gross) if gross > 0 else 1.
            out.update({p: w * factor for p, w in sized.items()})
        for pair, until in r.blocked.items():
            if now_ms < until and pair in out:
                out[pair] = 0.
        return out

    def step(self, quotes, now_ms):
        if self.state.uncertain and not isinstance(self.broker, LiveBroker):
            raise RuntimeError("previous paper order state is uncertain")
        r = self.rebalance
        missing = sorted(set(self.pairs) - set(quotes))
        if missing:
            log.warning("no Roostoo quote for %s; they are skipped this cycle", missing)
        snapshot, equity = self.reconcile(quotes, now_ms)  # live: every held coin must be quoted
        stuck = {p for p, q in snapshot.longs.items() if q > 0 and not self.metadata.get(p, {}).get("CanTrade")}
        if stuck:
            # Valued in equity but left out of planning: plan_orders refuses untradable pairs.
            log.warning("held but not tradable on Roostoo, left alone: %s", sorted(stuck))
            snapshot = Snapshot(snapshot.cash, {p: q for p, q in snapshot.longs.items() if p not in stuck}, snapshot.shorts)
        held = {p for p, q in snapshot.longs.items() if q > 0}
        active = {p: quotes[p] for p in (set(self.pairs) | held | stuck) if p in quotes}  # stuck: valued, never targeted
        for q in active.values():
            # Roostoo stamps quotes with its own clock; a small lead over ours is normal.
            if not -config.MAX_CLOCK_SKEW_MS <= now_ms - q.timestamp <= config.MAX_QUOTE_AGE_MS:
                raise ValueError("stale/future quote; stop entire cycle")
        scale = drawdown_scale(equity / self.state.high_water - 1, config.MIDCAP_DRAWDOWN_SCHEDULE)
        hour = latest_rebalance_hour(now_ms, config.MIDCAP_REBALANCE_EVERY_HOURS, config.MIDCAP_REBALANCE_DELAY_MS)
        signal = None
        if hour != r.hour_ms:
            try:
                signal = self.signal_source(hour, held)  # raises until Binance has the closed kline
            except Exception:
                if not r.hour_ms:
                    raise  # no earlier targets to hold: trade nothing
                # Stops, de-risk and the activity trade carry on against the last targets; retried next cycle.
                log.exception("signals for %s unavailable; holding the %s targets", hour, r.hour_ms)
        if signal is not None:
            weights, regime = signal
            push = equity > config.PUSH_MODE_EQUITY
            r = self.rebalance = SleeveState(hour, weights, scale, push, regime, done=False, entries=r.entries,
                                             blocked={p: t for p, t in r.blocked.items() if t > now_ms},
                                             last_fill_day=r.last_fill_day, activity_day=r.activity_day)
            log.info("rebalance %s regime=%s push=%s scale=%.2f equity=%.0f %s", hour, regime, push, scale, equity,
                     {s: {p: round(w, 4) for p, w in ws.items() if w} for s, ws in weights.items()})
        elif scale < r.scale:
            log.warning("drawdown de-risk: scale %.2f -> %.2f", r.scale, scale)
            r.scale, r.done, r.cycles, r.rejections = scale, False, 0, {}
        if self.check_small_stops(r, snapshot, active, now_ms):
            r.done, r.cycles, r.rejections = False, 0, {}
        targets = self.targets(r, active, now_ms)
        # A wide spread blocks a new small-cap entry (adding to a holding is allowed).
        for pair, w in list(targets.items()):
            if self.sleeve_of.get(pair) == SMALL and w > 0 and pair not in held and active[pair].spread > config.SMALL_MAX_ENTRY_SPREAD:
                log.info("%s spread %.2f%% too wide for a small-cap entry", pair, 100 * active[pair].spread)
                targets[pair] = 0.
        for pair in held:
            targets.setdefault(pair, 0.)  # leftovers outside every sleeve are sold
        if r.done:
            events = self.activity_trade(r, targets, snapshot, active, now_ms)
            self.persist()
            return events
        orders = plan_orders(targets, snapshot, active, self.metadata, min_fraction=config.MIDCAP_REBALANCE_BAND)
        orders = [o for o in orders if r.rejections.get(o.pair, 0) < config.MIDCAP_MAX_REJECTIONS_PER_PAIR]
        r.cycles += 1
        if not orders or r.cycles > config.MIDCAP_MAX_REBALANCE_CYCLES:
            if orders:
                log.error("rebalance %s unfinished after %s cycles; holding until next", r.hour_ms, r.cycles - 1)
            r.done = True
            self.persist()
            return []
        before = dict(snapshot.longs)  # the paper ledger is mutated in place by fills
        events = self.submit_orders(orders, targets, active, now_ms)
        self.after_fills(r, events, before, now_ms)
        for pair in self.rejected:
            r.rejections[pair] = r.rejections.get(pair, 0) + 1
        if len(events) == len(orders):
            r.done = True
        self.persist()
        return events

    def check_small_stops(self, r, snapshot, quotes, now_ms):
        """Bar a small-cap holding whose bid fell SMALL_STOP_LOSS below its average entry. True if any fired."""
        fired = False
        for pair, quantity in snapshot.longs.items():
            if self.sleeve_of.get(pair) != SMALL or quantity <= 0 or pair not in quotes:
                continue
            if quantity * quotes[pair].bid <= self.metadata.get(pair, {}).get("MiniOrder", 1.):
                r.entries.pop(pair, None)  # dust a stop sale leaves behind: unsellable (as in plan_orders), so flat
                continue
            entry = r.entries.setdefault(pair, quotes[pair].price)  # unknown entry (e.g. after a switch): start now
            if quotes[pair].bid <= entry * (1 - config.SMALL_STOP_LOSS) and r.blocked.get(pair, 0) <= now_ms:
                r.blocked[pair] = now_ms + config.SMALL_STOP_LOCKOUT_HOURS * HOUR_MS
                log.warning("small-cap stop %s: bid %.8g <= %.0f%% below entry %.8g; selling, barred %sh", pair,
                            quotes[pair].bid, 100 * config.SMALL_STOP_LOSS, entry, config.SMALL_STOP_LOCKOUT_HOURS)
                fired = True
        for pair in [p for p in r.entries if snapshot.longs.get(p, 0) <= 0]:
            r.entries.pop(pair)
        return fired

    def after_fills(self, r, events, before_longs, now_ms):
        """Record the fill day and keep small-cap average entry prices (before_longs = holdings before the trades)."""
        for event in events:
            r.last_fill_day = now_ms // DAY_MS
            pair = event["pair"]
            if self.sleeve_of.get(pair) != SMALL or event["action"] != "BUY":
                continue
            before = before_longs.get(pair, 0.)
            old = r.entries.get(pair)
            if old is None or before <= 0:
                r.entries[pair] = event["price"]
            else:
                r.entries[pair] = (old * before + event["price"] * event["quantity"]) / (before + event["quantity"])

    def activity_trade(self, r, targets, snapshot, quotes, now_ms):
        """Once a day after ACTIVITY_HOUR_UTC, if nothing filled today, trade the largest drift to target."""
        today = now_ms // DAY_MS
        if (now_ms % DAY_MS) < config.ACTIVITY_HOUR_UTC * HOUR_MS or r.last_fill_day == today or r.activity_day == today:
            return []
        r.activity_day = today
        orders = plan_orders(targets, snapshot, quotes, self.metadata, min_fraction=config.ACTIVITY_BAND)
        orders = [o for o in orders if r.rejections.get(o.pair, 0) < config.MIDCAP_MAX_REJECTIONS_PER_PAIR]
        if not orders:
            log.warning("activity check: book already on target, no strategy trade available today")
            return []
        # A sell is never held back by the cash or exposure checks in submit_orders, so prefer one.
        candidates = [o for o in orders if o.action == "SELL"] or orders
        largest = max(candidates, key=lambda o: o.amount * quotes[o.pair].price if o.action in {"BUY", "SELL"} else o.amount)
        log.info("activity trade: %s %s toward target", largest.action, largest.pair)
        before = dict(snapshot.longs)
        events = self.submit_orders([largest], targets, quotes, now_ms)  # an unconfirmed order raises: no retry today
        for pair in self.rejected:
            r.rejections[pair] = r.rejections.get(pair, 0) + 1
        if not events:
            r.activity_day = -1  # refused or held back: try again next cycle
        self.after_fills(r, events, before, now_ms)
        return events


def ticker_quotes():
    """Every Roostoo pair's quote (held leftovers included), stamped with the server time."""
    response = get_ticker()
    if not response or not response.get("Success"):
        raise ValueError("ticker poll failed")
    stamp = int(response["ServerTime"])
    quotes = {}
    for pair, row in response.get("Data", {}).items():
        try:
            quotes[pair] = Quote(float(row["LastPrice"]), float(row["MaxBid"]), float(row["MinAsk"]), stamp)
        except (KeyError, TypeError, ValueError):
            continue  # an untradable or empty book; only a problem if we hold it (reconcile raises then)
    return quotes


def restore_sleeve_engine(broker, metadata, signal_source, sleeve_pairs, state_path, event_path, equity_path):
    state, sleeve = ExecutionState(), None
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
            sleeve = SleeveState(**saved["rebalance"])
    return SleeveEngine(broker, metadata, signal_source, sleeve_pairs, sleeve=sleeve, state=state,
                        state_path=state_path, event_path=event_path, equity_path=equity_path)


def tradable_sleeves(metadata):
    """config.SLEEVE_PAIRS restricted to pairs Roostoo lists as tradable (others are logged and skipped)."""
    out = {}
    for sleeve in SLEEVES:
        pairs = config.SLEEVE_PAIRS.get(sleeve, [])
        dropped = [p for p in pairs if not metadata.get(p, {}).get("CanTrade")]
        if dropped:
            log.warning("%s sleeve: not tradable on Roostoo, skipped: %s", sleeve, dropped)
        out[sleeve] = [p for p in pairs if p not in dropped]
    if "BTC/USD" not in out[LARGE]:
        raise ValueError("BTC/USD must be tradable: it sets the market regime")
    return out


def run_forever(engine, interval=None):
    interval = config.POLL_INTERVAL_SECONDS if interval is None else interval
    while True:
        started = time.monotonic()
        try:
            engine.step(ticker_quotes(), int(time.time() * 1000))
        except Exception:
            log.exception("sleeve cycle failed; no assumed fills")
        time.sleep(max(0., interval - (time.monotonic() - started)))


def build(metadata, live, state_path, event_path, equity_path):
    sleeve_pairs = tradable_sleeves(metadata)
    broker = LiveBroker() if live else PaperBroker()
    return restore_sleeve_engine(broker, metadata, binance_sleeve_weights(sleeve_pairs), sleeve_pairs,
                                 state_path, event_path, equity_path)

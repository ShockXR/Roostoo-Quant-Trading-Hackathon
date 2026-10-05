"""Portfolio snapshots, paper fills and target-to-delta order planning."""
from dataclasses import dataclass, field, asdict
import math
from .. import config, sizing


@dataclass(frozen=True)
class Quote:
    price: float
    bid: float
    ask: float
    timestamp: int

    def __post_init__(self):
        if not all(math.isfinite(v) and v > 0 for v in (self.price, self.bid, self.ask)) or self.ask < self.bid:
            raise ValueError("invalid/crossed quote")

    @property
    def spread(self):
        return (self.ask - self.bid) / self.price


@dataclass
class Short:
    quantity: float
    entry: float
    collateral: float


@dataclass
class Snapshot:
    cash: float
    longs: dict = field(default_factory=dict)
    shorts: dict = field(default_factory=dict)

    def __post_init__(self):
        if not math.isfinite(self.cash) or any(not math.isfinite(v) or v < 0 for v in self.longs.values()):
            raise ValueError("invalid cash/spot balance")
        for short in self.shorts.values():
            if not all(math.isfinite(v) and v > 0 for v in (short.quantity, short.entry, short.collateral)):
                raise ValueError("invalid short state")

    def equity(self, quotes):
        value = self.cash + sum(q * quotes[p].price for p, q in self.longs.items())
        for p, short in self.shorts.items():
            value += short.collateral + max(-short.collateral, short.quantity * (short.entry - quotes[p].ask))
        if not math.isfinite(value) or value <= 0:
            raise ValueError("nonpositive/invalid portfolio equity")
        return value

    def quantities(self):
        if set(self.longs) & set(self.shorts):
            raise ValueError("simultaneous long/short holdings require reconciliation")
        return {**self.longs, **{p: -s.quantity for p, s in self.shorts.items()}}


@dataclass(frozen=True)
class Order:
    pair: str
    action: str
    amount: float  # asset units, except SHORT_OPEN which uses USD collateral


@dataclass
class ExecutionState:
    high_water: float = 0.
    halted: bool = False
    uncertain: bool = False
    trades: list = field(default_factory=list)
    last_order_quote: dict = field(default_factory=dict)

    def mark(self, equity, kill_switch=None):
        self.high_water = max(self.high_water, equity)
        threshold = config.MAX_DRAWDOWN_KILL_SWITCH if kill_switch is None else kill_switch
        if equity / self.high_water - 1 <= -threshold:
            self.halted = True  # latched across recovery/restart; do not auto-resume risk

    def slots(self, now):
        self.trades = [t for t in self.trades if now - t < 3_600_000]
        return max(0, config.MAX_TRADES_PER_HOUR - len(self.trades))


class PaperBroker:
    """Cash/units ledger. Shorts lock collateral rather than credit sale cash.

    Fills use bid/ask plus half the configured round-trip slippage. This is
    a research assumption, not a claim of exact simulator parity.
    """
    def __init__(self, capital=None):
        self.state = Snapshot(config.INITIAL_CAPITAL if capital is None else capital)
        self.events = []

    def snapshot(self, quotes):
        self.state.equity(quotes)
        return self.state

    def submit(self, order, quotes, metadata):
        q = quotes[order.pair]
        slip = config.SLIPPAGE_ESTIMATE / 2
        amount = order.amount
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("invalid order amount")
        price = (q.ask * (1 + slip) if order.action in {"BUY", "SHORT_CLOSE"} else q.bid * (1 - slip))
        fee_rate = config.SHORT_FEE if order.action.startswith("SHORT") else config.TAKER_FEE
        if order.action == "BUY":
            fee = amount * price * fee_rate
            debit = amount * price + fee
            if debit > self.state.cash + 1e-9:
                raise ValueError("insufficient paper cash")
            self.state.cash -= debit
            self.state.longs[order.pair] = self.state.longs.get(order.pair, 0.) + amount
        elif order.action == "SELL":
            held = self.state.longs.get(order.pair, 0.)
            if amount > held + 1e-9:
                raise ValueError("spot sell exceeds holdings")
            fee = amount * price * fee_rate
            self.state.cash += amount * price - fee
            remaining = max(0., held - amount)
            if remaining < 1e-12:
                self.state.longs.pop(order.pair, None)
            else:
                self.state.longs[order.pair] = remaining
        elif order.action == "SHORT_OPEN":
            quantity = sizing.round_quantity(amount / price, metadata[order.pair]["AmountPrecision"])
            if quantity <= 0:
                raise ValueError("short quantity rounds to zero")
            fee = quantity * price * fee_rate
            if amount + fee > self.state.cash + 1e-9:
                raise ValueError("insufficient paper collateral")
            self.state.cash -= amount + fee
            previous = self.state.shorts.get(order.pair, Short(0., price, 0.))
            total = previous.quantity + quantity
            self.state.shorts[order.pair] = Short(total, (previous.quantity * previous.entry + quantity * price) / total,
                                                  previous.collateral + amount)
            amount = quantity
        elif order.action == "SHORT_CLOSE":
            previous = self.state.shorts[order.pair]
            amount = min(amount, previous.quantity)
            released = previous.collateral * amount / previous.quantity
            pnl = max(-released, amount * (previous.entry - price))
            fee = amount * price * fee_rate
            self.state.cash += released + pnl - fee
            if amount >= previous.quantity - 1e-12:
                self.state.shorts.pop(order.pair)
            else:
                previous.quantity -= amount
                previous.collateral -= released
        else:
            raise ValueError("unknown order action")
        event = {"pair": order.pair, "action": order.action, "quantity": amount, "price": price,
                 "fee": fee, "timestamp": q.timestamp, "status": "FILLED", "paper": True}
        self.events.append(event)
        return event


def plan_orders(targets, snapshot, quotes, metadata, min_fraction=None):
    """Reduce existing risk first; a direction flip closes before next-cycle entry.

    min_fraction: per-pair deadband as a fraction of equity (default config.MIN_REBALANCE_FRACTION).
    """
    band = config.MIN_REBALANCE_FRACTION if min_fraction is None else min_fraction
    equity = snapshot.equity(quotes)
    quantities = snapshot.quantities()
    current_gross = sum(abs(v) * quotes[p].price for p, v in quantities.items()) / equity
    targets = sizing.enforce_portfolio_limits(targets)
    # Reserve a conservative allowance for spread, fees and slippage so new
    # allocations don't immediately breach caps when equity pays entry costs.
    friction = max((q.spread for q in quotes.values()), default=0.) + max(config.TAKER_FEE, config.SHORT_FEE) + config.SLIPPAGE_ESTIMATE
    usable = equity / (1 + 2 * friction)
    reductions, additions = [], []
    for pair in sorted(set(targets) | set(quantities)):
        if pair not in quotes or pair not in metadata:
            raise ValueError(f"missing quote/metadata for held or targeted {pair}")
        meta, q = metadata[pair], quotes[pair]
        if not meta.get("CanTrade", False):
            raise ValueError(f"pair not tradable: {pair}")
        fraction = targets.get(pair, 0.)
        desired = sizing.capital_to_quantity(fraction, usable, q.price, meta["AmountPrecision"])
        desired *= 1 if fraction >= 0 else -1
        current = quantities.get(pair, 0.)
        flip = current * desired < 0
        if flip:
            desired = 0.
        delta = desired - current
        excess_reduction = abs(desired) < abs(current) and (
            abs(current) * q.price / equity > config.MAX_POSITION_PER_ASSET or current_gross > config.MAX_PORTFOLIO_EXPOSURE)
        if abs(delta) * q.price < band * equity and desired != 0 and not excess_reduction:
            continue
        if current > 0 and delta < 0:
            quantity = sizing.round_quantity(min(current, -delta), meta["AmountPrecision"])
            if quantity > 0 and quantity * q.bid > meta["MiniOrder"]:
                reductions.append(Order(pair, "SELL", quantity))
        elif current < 0 and delta > 0:
            quantity = sizing.round_quantity(min(-current, delta), meta["AmountPrecision"])
            if quantity > 0:
                reductions.append(Order(pair, "SHORT_CLOSE", quantity))
        elif not flip and delta > 0 and desired > 0:
            quantity = sizing.round_quantity(delta, meta["AmountPrecision"])
            if quantity > 0 and quantity * q.ask > meta["MiniOrder"]:
                additions.append(Order(pair, "BUY", quantity))
        elif not flip and delta < 0 and desired < 0:
            collateral = (-delta) * q.bid * (1 - config.SLIPPAGE_ESTIMATE / 2)
            if collateral >= 1.:
                additions.append(Order(pair, "SHORT_OPEN", collateral))
    return reductions + additions

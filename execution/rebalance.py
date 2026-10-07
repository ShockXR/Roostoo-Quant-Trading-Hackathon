"""
Turns target weights into Roostoo orders. Pure functions -- no network --
so the live loop's sizing is unit-tested.

Mirrors backtest/simulator.py: targets are fractions of equity, scaled
down pro rata when their sum exceeds config.MAX_PORTFOLIO_EXPOSURE, and a
symbol is only traded when its target CHANGES (its weight is otherwise
left to drift, as in the backtest). Without a previous target (first run
or lost state), any position more than `tolerance` away from its target
is reconciled.
"""
import math
from dataclasses import dataclass

from .. import config


@dataclass
class Order:
    pair: str
    side: str        # "BUY" | "SELL"
    quantity: float
    notional: float


def cap_gross(targets: dict[str, float], cap: float = config.MAX_PORTFOLIO_EXPOSURE) -> dict[str, float]:
    gross = sum(abs(w) for w in targets.values())
    if gross <= cap or gross == 0:
        return dict(targets)
    return {p: w * cap / gross for p, w in targets.items()}


def parse_balance(resp: dict) -> dict[str, float]:
    """Roostoo /v3/balance -> {coin: free quantity}. Accepts the `Wallet` and
    `SpotWallet` layouts the API has used."""
    wallet = resp.get("SpotWallet") or resp.get("Wallet") or {}
    return {coin: float(v.get("Free", 0.0)) for coin, v in wallet.items()}


def _floor(x: float, decimals: int) -> float:
    f = 10 ** decimals
    return math.floor(x * f + 1e-9) / f


def plan_orders(targets: dict[str, float], holdings: dict[str, float], prices: dict[str, float],
                pair_info: dict[str, dict], prev_targets: dict[str, float] | None,
                tolerance: float = config.LIVE_RECONCILE_TOLERANCE) -> list[Order]:
    """targets: pair -> weight (already gross-capped). holdings: coin -> qty
    incl. "USD". prices: pair -> last price. pair_info: exchangeInfo
    TradePairs. Returns sells first, then buys (buys trimmed to available cash
    net of the taker fee)."""
    cash = holdings.get("USD", 0.0)
    coin = {p: p.split("/")[0] for p in targets}
    equity = cash + sum(holdings.get(coin[p], 0.0) * prices[p] for p in targets)
    sells, buys = [], []
    for pair, w in targets.items():
        qty = holdings.get(coin[pair], 0.0)
        cur_w = qty * prices[pair] / equity if equity > 0 else 0.0
        if prev_targets is not None and pair in prev_targets:
            if abs(w - prev_targets[pair]) < 1e-12:
                continue                       # target unchanged: hold, let it drift
        elif abs(w - cur_w) <= tolerance:
            continue
        info = pair_info[pair]
        if w == 0:
            delta = -qty                       # full exit, never leave dust by rounding
        else:
            delta = w * equity / prices[pair] - qty
        q = _floor(abs(delta), int(info["AmountPrecision"]))
        notional = q * prices[pair]
        if q <= 0 or notional < float(info.get("MiniOrder", 1.0)):
            continue
        (buys if delta > 0 else sells).append(Order(pair, "BUY" if delta > 0 else "SELL", q, notional))

    cash_after = cash + sum(o.notional * (1 - config.TAKER_FEE) for o in sells)
    need = sum(o.notional * (1 + config.TAKER_FEE) for o in buys)
    if need > cash_after > 0:
        scale = cash_after / need
        trimmed = []
        for o in buys:
            q = _floor(o.quantity * scale, int(pair_info[o.pair]["AmountPrecision"]))
            if q > 0 and q * prices[o.pair] >= float(pair_info[o.pair].get("MiniOrder", 1.0)):
                trimmed.append(Order(o.pair, "BUY", q, q * prices[o.pair]))
        buys = trimmed
    elif cash_after <= 0:
        buys = []
    return sells + buys

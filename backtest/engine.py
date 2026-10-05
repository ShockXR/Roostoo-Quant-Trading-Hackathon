"""Sequential paper-ledger backtest with delayed signals and terminal costs."""
import math
import numpy as np
import pandas as pd
from .. import config, sizing
from ..scoring import score_equity
from ..execution.portfolio import PaperBroker, Quote, Order


def simulate(price, position, taker=True, execution_lag=1, liquidate=True, bid=None, ask=None):
    """Snapshot signal at t trades at t+lag; default lag is one observation.

    Positions are desired equity fractions. Fills use observed bid/ask if
    supplied, plus a slippage assumption. Equity includes starting capital;
    final liquidation is explicit. Synthetic fills do not establish venue parity.
    """
    if not taker:
        raise ValueError("maker resting/fill model not implemented; use taker=True")
    if not isinstance(execution_lag, int) or execution_lag < 1:
        raise ValueError("at least one observation execution delay required")
    if not price.index.equals(position.index) or not price.index.is_unique or not price.index.is_monotonic_increasing:
        raise ValueError("price/position must have identical, unique chronological indexes")
    if not np.isfinite(price).all() or (price <= 0).any() or not np.isfinite(position).all() or (position.abs() > 1).any():
        raise ValueError("invalid prices or target weights")
    if bid is not None and not bid.index.equals(price.index) or ask is not None and not ask.index.equals(price.index):
        raise ValueError("bid/ask index mismatch")
    delayed = position.shift(execution_lag).fillna(0.)
    capital = config.INITIAL_CAPITAL
    broker = PaperBroker(capital=capital)
    meta = {"asset": {"AmountPrecision": 12, "MiniOrder": 0., "CanTrade": True}}
    rows, peak = [], capital
    for offset, (timestamp, value) in enumerate(price.items()):
        q = Quote(float(value), float(bid.loc[timestamp]) if bid is not None else float(value),
                  float(ask.loc[timestamp]) if ask is not None else float(value), offset)
        quotes = {"asset": q}
        previous = rows[-1]["equity"] * capital if rows else capital
        before = broker.state.equity(quotes)
        fraction = float(delayed.loc[timestamp])
        if liquidate and offset == len(price) - 1:
            fraction = 0.
        current = broker.state.quantities().get("asset", 0.)
        desired = fraction * before / q.price
        if current * desired < 0:
            desired = 0.  # same close-first policy as the runner
        orders = []
        if current > 0 and (desired < current or desired < 0):
            amount = min(current, current - max(0., desired))
            orders.append(Order("asset", "SELL", amount))
            current -= amount
        elif current < 0 and (desired > current or desired > 0):
            amount = min(-current, -current - max(0., -desired))
            orders.append(Order("asset", "SHORT_CLOSE", amount))
            current += amount
        for order in orders:
            broker.submit(order, quotes, meta)
        if desired > max(0., current):
            amount = desired - max(0., current)
            affordable = max(0., broker.state.cash) / (q.ask * (1 + config.SLIPPAGE_ESTIMATE / 2) * (1 + config.TAKER_FEE))
            amount = min(amount, affordable)
            if amount > 1e-12:
                broker.submit(Order("asset", "BUY", amount), quotes, meta)
        elif desired < min(0., current):
            collateral = (min(0., current) - desired) * q.bid * (1 - config.SLIPPAGE_ESTIMATE / 2)
            collateral = min(collateral, max(0., broker.state.cash) / (1 + config.SHORT_FEE))
            if collateral > 1e-12:
                broker.submit(Order("asset", "SHORT_OPEN", collateral), quotes, meta)
        equity = broker.state.equity(quotes)
        peak = max(peak, equity)
        fills = [e for e in broker.events if e["timestamp"] == offset]
        rows.append({"gross_return": before / previous - 1., "net_return": equity / previous - 1.,
                     "equity": equity / capital, "drawdown": equity / peak - 1.,
                     "turnover": sum(e["quantity"] * e["price"] for e in fills) / previous,
                     "fees": sum(e["fee"] for e in fills), "n_fills": len(fills)})
    out = pd.DataFrame(rows, index=price.index, columns=["gross_return", "net_return", "equity", "drawdown", "turnover", "fees", "n_fills"])
    out.attrs["fills"] = broker.events
    return out


def _sortino_ratio(net, periods_per_year):
    if not len(net):
        return 0.
    downside = np.minimum(net.to_numpy(dtype=float), 0.)
    deviation = float(np.sqrt(np.mean(downside ** 2)))
    return float(net.mean() / deviation * np.sqrt(periods_per_year)) if deviation else 0.


def _calmar_ratio(total_return, max_drawdown, years):
    if not max_drawdown or years <= 0:
        return 0.
    exponent = math.log1p(total_return) / years
    if exponent > 700:
        return None  # insufficient-duration annualization must not invent a score
    result = math.expm1(exponent) / abs(max_drawdown)
    return result if math.isfinite(result) else None


def _daily_score(sim_result):
    """Competition-style score on daily closes; needs a UTC epoch-ms index."""
    keys = ("daily_composite", "daily_sortino", "daily_sharpe", "daily_calmar", "n_days")
    index = sim_result.index
    if not pd.api.types.is_integer_dtype(index) or index.min() < 10 ** 12:
        return dict.fromkeys(keys)  # bar counters, not timestamps
    report = score_equity(sim_result.equity * config.INITIAL_CAPITAL, config.INITIAL_CAPITAL)
    return dict(zip(keys, (report.composite, report.sortino, report.sharpe, report.calmar, report.n_days)))


def summarize(sim_result, periods_per_year=None, bars_per_year=None):
    annual = config.BARS_PER_YEAR if periods_per_year is None else periods_per_year
    if annual <= 0:
        raise ValueError("positive annualization frequency required")
    if sim_result.empty:
        raise ValueError("no backtest observations")
    net = sim_result.net_return
    volatility = float(net.std(ddof=1)) if len(net) > 1 else 0.
    sharpe = float(net.mean() / volatility * np.sqrt(annual)) if volatility > 0 else 0.
    total = float(sim_result.equity.iloc[-1] - 1.)
    max_dd = float(sim_result.drawdown.min())
    years = max(1, len(net) - 1) / (bars_per_year or annual)
    sortino = _sortino_ratio(net, annual)
    calmar = _calmar_ratio(total, max_dd, years)
    composite = .4 * sortino + .3 * sharpe + .3 * calmar if calmar is not None else None
    daily = _daily_score(sim_result)
    return {**daily, "total_net_return": total, "sharpe_like": sharpe, "sortino_like": sortino, "calmar_like": calmar,
            "composite_score": composite, "metric_convention": "zero-target RMS downside; undefined/no-downside ratios use 0; overflow annualization uses null; organizer parity unverified",
            "max_drawdown": max_dd, "avg_turnover": float(sim_result.turnover.mean()),
            "n_fills": int(sim_result.n_fills.sum()), "total_fees": float(sim_result.fees.sum()),
            "positive_bar_fraction": float((net > 0).mean())}

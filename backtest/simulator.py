"""
Execution-level backtest: holds units (not weights), fills at the NEXT bar's
open, charges fee and slippage separately on every fill, and records each
round trip as a trade.

Why this exists next to engine.simulate(): that function multiplies a
weight by the bar return, which silently rebalances to the target weight
every bar for free, fills at the same close the signal was computed from,
and can only report bar-level proxies for trade counts and win rate.

Timing contract (no look-ahead):
    target[t] is decided with information up to and including close[t].
    It is executed at open[t+1]. Equity is marked at every close.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


TRADE_COLUMNS = ["symbol", "entry_time", "exit_time", "bars", "pnl", "return_on_equity",
                 "costs", "max_weight", "side", "open_at_end"]


@dataclass
class Costs:
    fee: float = 0.001        # per side, fraction of notional (Roostoo taker)
    slippage: float = 0.00015  # per side, fraction of price (half of config.SLIPPAGE_ESTIMATE round trip)


@dataclass
class SimResult:
    equity: pd.Series                 # marked at each bar close
    weights: pd.DataFrame             # actual held weight per symbol at each close
    trades: pd.DataFrame              # one row per round trip
    fees: float
    slippage: float
    traded_notional: float
    initial_capital: float
    per_symbol_fees: dict = field(default_factory=dict)


def simulate(
    open_: pd.DataFrame,
    close: pd.DataFrame,
    target: pd.DataFrame,
    initial_capital: float = 100_000.0,
    costs: Costs = Costs(),
    max_gross_exposure: float | None = None,
    rebalance_tolerance: float = 0.0,
) -> SimResult:
    """
    open_, close, target: same index and columns (one column per symbol).
    target: desired weight of portfolio equity per symbol (negative = short).
    A symbol is only traded when its target CHANGES (by more than
    rebalance_tolerance) -- between changes the position is held in units
    and its weight drifts with price, as it would live.
    max_gross_exposure: if set, targets are scaled down proportionally
    when sum(|w|) exceeds it (config.MAX_PORTFOLIO_EXPOSURE).
    """
    symbols = list(close.columns)
    idx = close.index
    o = open_.reindex(index=idx, columns=symbols).to_numpy(float)
    c = close.to_numpy(float)
    tgt = target.reindex(index=idx, columns=symbols).fillna(0.0).to_numpy(float)
    if max_gross_exposure is not None:
        gross = np.abs(tgt).sum(axis=1, keepdims=True)
        scale = np.where(gross > max_gross_exposure, max_gross_exposure / np.maximum(gross, 1e-12), 1.0)
        tgt = tgt * scale

    n, k = c.shape
    units = np.zeros(k)
    cash = initial_capital
    equity = np.empty(n)
    weights = np.zeros((n, k))
    last_tgt = np.zeros(k)
    fees = slip = notional = 0.0
    sym_fees = np.zeros(k)

    # open-trade bookkeeping per symbol
    entry_bar = np.full(k, -1)
    entry_equity = np.zeros(k)
    trade_pnl = np.zeros(k)       # running $ PnL of the open trade incl. costs
    trade_cost = np.zeros(k)
    trade_peak_w = np.zeros(k)
    entry_side = np.zeros(k)
    mark = np.zeros(k)            # last price the position was marked at
    trades = []

    def _trade(j: int, t: int, open_at_end: bool) -> dict:
        return {
            "symbol": symbols[j],
            "entry_time": idx[entry_bar[j]],
            "exit_time": idx[t],
            "bars": int(t - entry_bar[j]),
            "pnl": float(trade_pnl[j]),
            "return_on_equity": float(trade_pnl[j] / entry_equity[j]),
            "costs": float(trade_cost[j]),
            "max_weight": float(trade_peak_w[j]),
            "side": "long" if entry_side[j] > 0 else "short",
            "open_at_end": open_at_end,
        }

    for t in range(n):
        if t > 0:
            # 1) execute orders decided at close[t-1] at open[t]
            pend = tgt[t - 1]
            px_open = o[t]
            # mark to the open first so sizing uses current equity
            gap_pnl = units * (px_open - mark)
            trade_pnl += gap_pnl
            mark = px_open.copy()
            eq_open = cash + float(units @ px_open)
            changed = np.abs(pend - last_tgt) > rebalance_tolerance
            if changed.any():
                for j in np.flatnonzero(changed):
                    want_units = pend[j] * eq_open / px_open[j]
                    d = want_units - units[j]
                    if d == 0:
                        last_tgt[j] = pend[j]
                        continue
                    fill = px_open[j] * (1 + np.sign(d) * costs.slippage)
                    slip_cost = abs(d) * px_open[j] * costs.slippage
                    fee_cost = abs(d) * fill * costs.fee
                    was_flat = units[j] == 0
                    cash -= d * fill + fee_cost
                    units[j] = want_units if abs(pend[j]) > 0 else 0.0
                    fees += fee_cost
                    slip += slip_cost
                    sym_fees[j] += fee_cost
                    notional += abs(d) * fill
                    if was_flat and units[j] != 0:
                        entry_bar[j] = t
                        entry_equity[j] = eq_open
                        trade_pnl[j] = 0.0
                        trade_cost[j] = 0.0
                        trade_peak_w[j] = 0.0
                        entry_side[j] = np.sign(units[j])
                    # slippage is already inside `fill`; book both as trade cost
                    trade_pnl[j] -= fee_cost + slip_cost
                    trade_cost[j] += fee_cost + slip_cost
                    trade_peak_w[j] = max(trade_peak_w[j], abs(pend[j]))
                    if units[j] == 0 and entry_bar[j] >= 0:
                        trades.append(_trade(j, t, open_at_end=False))
                        entry_bar[j] = -1
                    last_tgt[j] = pend[j]
        # 2) mark to close
        bar_pnl = units * (c[t] - mark)
        trade_pnl += bar_pnl
        mark = c[t].copy()
        equity[t] = cash + float(units @ c[t])
        weights[t] = units * c[t] / equity[t]

    # positions still open at the end are closed at the final close (no costs)
    # so the trade table reconciles with final equity
    for j in range(k):
        if entry_bar[j] >= 0:
            trades.append(_trade(j, n - 1, open_at_end=True))

    return SimResult(
        equity=pd.Series(equity, index=idx, name="equity"),
        weights=pd.DataFrame(weights, index=idx, columns=symbols),
        trades=pd.DataFrame(trades, columns=TRADE_COLUMNS),
        fees=fees,
        slippage=slip,
        traded_notional=notional,
        initial_capital=initial_capital,
        per_symbol_fees=dict(zip(symbols, sym_fees)),
    )

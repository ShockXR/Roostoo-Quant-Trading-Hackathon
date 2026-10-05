"""Multi-asset hourly backtest for the mid-cap sleeve.

Timing: a weight row stamped at hour H may use prices up to H (the close of
the 1m candle ending at H). It trades at the 1m close `exec_delay` minutes
later, so the fill price is never the price the signal saw. Equity is marked
at each hour's signal price before that hour's trades.

Costs are a flat fraction of traded notional per side (fee + half spread +
slippage). Short positions use negative units at 1x; the simulator's
collateral mechanics are not modeled, only their P&L. These are research
assumptions, not venue parity.
"""
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from ..scoring import score_equity

DEFAULT_COST_PER_SIDE = 0.0015  # 0.10% taker fee + ~0.02% half spread + 0.03% slippage


@dataclass
class HourlyPanel:
    signal: pd.DataFrame   # price at the top of each hour (UTC)
    execution: pd.DataFrame  # price exec_delay minutes later
    volume: pd.DataFrame   # quote volume traded in the hour ending at each stamp

    def __post_init__(self):
        for frame in (self.execution, self.volume):
            if not frame.index.equals(self.signal.index) or list(frame.columns) != list(self.signal.columns):
                raise ValueError("panel frames must share index and columns")


def load_hourly_panel(symbols, data_dir, exec_delay_minutes=5):
    """Build the hourly panel from Binance 1m parquet files (one per symbol)."""
    if not 1 <= exec_delay_minutes < 60:
        raise ValueError("exec_delay_minutes must be within the hour")
    closes, volumes = {}, {}
    for symbol in symbols:
        frame = pd.read_parquet(Path(data_dir) / f"{symbol}_1m.parquet", columns=["open_time", "close", "quote_asset_volume"])
        available = pd.DatetimeIndex(frame.open_time).tz_convert("UTC") + pd.Timedelta(minutes=1)
        closes[symbol] = pd.Series(frame.close.to_numpy(float), index=available)
        volumes[symbol] = pd.Series(frame.quote_asset_volume.to_numpy(float), index=available)
    close = pd.DataFrame(closes).sort_index()
    volume = pd.DataFrame(volumes).sort_index()
    hours = close.index[close.index == close.index.floor("h")]
    signal = close.loc[hours]
    execution = close.reindex(hours + pd.Timedelta(minutes=exec_delay_minutes))
    execution.index = hours
    hourly_volume = volume.resample("1h", label="right", closed="right").sum().reindex(hours)
    complete = signal.notna().all(axis=1) & execution.notna().all(axis=1)
    keep = hours[complete.to_numpy()]
    return HourlyPanel(signal.loc[keep], execution.loc[keep], hourly_volume.loc[keep])


def _utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def simulate(panel, weights, start=None, end=None, capital=100_000., cost_per_side=DEFAULT_COST_PER_SIDE,
             band=0.01, liquidate=True):
    """Run target weights through a cash/units ledger.

    weights: DataFrame on the panel's index/columns. A row of NaN means "no
    rebalance this hour"; otherwise the row is the full target (missing
    symbols are 0). band: per-asset trades smaller than this fraction of
    equity are skipped unless they close the position.
    Window is [start, end).
    """
    index = panel.signal.index
    window = index[(index >= _utc(start)) if start is not None else np.ones(len(index), bool)]
    if end is not None:
        window = window[window < _utc(end)]
    if len(window) == 0:
        raise ValueError("empty backtest window")
    columns = list(panel.signal.columns)
    w = weights.reindex(index=window, columns=columns).to_numpy(dtype=float)
    rebalance = ~np.isnan(w).all(axis=1)
    w = np.nan_to_num(w)
    if (np.abs(w).sum(axis=1) > 1 + 1e-9).any():
        raise ValueError("gross target exposure above 1x")
    sig = panel.signal.loc[window].to_numpy(dtype=float)
    exe = panel.execution.loc[window].to_numpy(dtype=float)
    cash, units = float(capital), np.zeros(len(columns))
    equity, fills, turnover, gross, costs = (np.zeros(len(window)) for _ in range(5))
    for i in range(len(window)):
        equity[i] = cash + units @ sig[i]
        gross[i] = np.abs(units * sig[i]).sum() / equity[i]
        if not rebalance[i]:
            continue
        before = cash + units @ exe[i]
        if before <= 0:
            raise ValueError("equity exhausted")
        desired = w[i] * before / exe[i]
        trade = desired - units
        notional = np.abs(trade * exe[i])
        skip = (notional < band * before) & (desired != 0)
        trade[skip] = 0.
        notional[skip] = 0.
        cost = notional.sum() * cost_per_side
        cash -= trade @ exe[i] + cost
        units += trade
        fills[i] = int((notional > 0).sum())
        turnover[i] = notional.sum() / before
        costs[i] = cost
    if liquidate:
        # The competition liquidates at the end; charge exit costs at the last mark.
        exit_cost = np.abs(units * sig[-1]).sum() * cost_per_side
        equity[-1] -= exit_cost
        costs[-1] += exit_cost
    return pd.DataFrame({"equity": equity, "fills": fills, "turnover": turnover, "gross": gross, "costs": costs},
                        index=window)


def evaluate(result, capital=100_000., start=None):
    """Competition score plus activity and cost statistics for one simulation."""
    stamps = result.index.as_unit("ns").asi8 // 1_000_000
    report = score_equity(pd.Series(result.equity.to_numpy(), index=stamps), capital,
                          start=None if start is None else _utc(start))
    days = result.fills.groupby(result.index.floor("D")).sum()
    return {"composite": report.composite, "sortino": report.sortino, "sharpe": report.sharpe, "calmar": report.calmar,
            "total_return": report.total_return, "max_drawdown": report.max_drawdown, "n_days": report.n_days,
            "active_day_fraction": float((days > 0).mean()), "fills_per_day": float(days.mean()),
            "avg_gross": float(result.gross.mean()), "turnover_per_day": float(result.turnover.sum() / max(1, len(days))),
            "cost_fraction": float(result.costs.sum() / capital)}


def block_scores(panel, weights, start, end, days=14, **kwargs):
    """Score fresh, flat-start blocks of `days` (the competition length) within [start, end)."""
    rows = []
    edges = pd.date_range(_utc(start), _utc(end), freq=f"{days}D")
    for left, right in zip(edges[:-1], edges[1:]):
        result = simulate(panel, weights, left, right, **kwargs)
        stats = evaluate(result, kwargs.get("capital", 100_000.), left)
        stats.update(block_start=left)
        rows.append(stats)
    return pd.DataFrame(rows)

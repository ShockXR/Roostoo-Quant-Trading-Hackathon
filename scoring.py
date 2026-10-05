"""Competition score from an equity curve: 0.4*Sortino + 0.3*Sharpe + 0.3*Calmar.

Ratios use daily returns. Organizer conventions (day boundary, annualization,
risk-free rate, Sortino target, Calmar return, drawdown basis) are unconfirmed;
each is a ScoreConventions field so a ruling changes a setting, not the code.
See docs/ORGANIZER_QUESTIONS.md. Undefined ratios (zero denominators, too few
days) are reported as None with a reason instead of being replaced by 0.
"""
from dataclasses import dataclass, asdict, field
import math
import numpy as np
import pandas as pd

WEIGHTS = {"sortino": .4, "sharpe": .3, "calmar": .3}


@dataclass(frozen=True)
class ScoreConventions:
    day_offset_hours: float = 0.        # scoring day ends at midnight UTC+offset
    periods_per_year: int = 365
    risk_free_annual: float = 0.
    sortino_target_daily: float = 0.
    annual_return: str = "compound"     # "compound" | "simple"
    drawdown_basis: str = "daily"       # "daily" | "intraday"

    def __post_init__(self):
        if self.annual_return not in {"compound", "simple"}:
            raise ValueError("annual_return must be 'compound' or 'simple'")
        if self.drawdown_basis not in {"daily", "intraday"}:
            raise ValueError("drawdown_basis must be 'daily' or 'intraday'")
        if self.periods_per_year <= 0:
            raise ValueError("positive periods_per_year required")


@dataclass
class ScoreReport:
    composite: float | None
    sortino: float | None
    sharpe: float | None
    calmar: float | None
    total_return: float
    annual_return: float | None
    max_drawdown: float
    n_days: int
    daily_returns: list
    undefined: dict = field(default_factory=dict)
    conventions: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def _as_utc_index(equity):
    if not isinstance(equity, pd.Series) or equity.empty:
        raise ValueError("non-empty equity Series required")
    index = equity.index
    if isinstance(index, pd.DatetimeIndex):
        index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    else:
        index = pd.to_datetime(np.asarray(index, dtype="int64"), unit="ms", utc=True)
    values = pd.to_numeric(equity, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("equity must be finite and positive")
    out = pd.Series(values, index=index).sort_index()
    if out.index.duplicated().any():
        raise ValueError("duplicate equity timestamps")
    return out


def _scoring_day(timestamps, conventions):
    return (timestamps + pd.Timedelta(hours=conventions.day_offset_hours)).floor("D")


def daily_closes(equity, conventions=ScoreConventions(), start=None, fill=None):
    """Last mark of each scoring day; days without marks carry the prior close.

    start (UTC ms or Timestamp) extends the series back to the first scoring
    day; days before the first mark take `fill` (normally starting capital).
    """
    marks = _as_utc_index(equity)
    closes = pd.Series(marks.to_numpy(), index=_scoring_day(marks.index, conventions)).groupby(level=0).last()
    first = closes.index[0]
    if start is not None:
        start = pd.Timestamp(start, unit="ms", tz="UTC") if isinstance(start, (int, np.integer)) else pd.Timestamp(start)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        first = min(first, _scoring_day(pd.DatetimeIndex([start]), conventions)[0])
    full = pd.date_range(first, closes.index[-1], freq="D")
    out = closes.reindex(full).ffill()
    if out.isna().any():
        if fill is None:
            raise ValueError("fill value required for days before the first mark")
        out = out.fillna(fill)
    return out


def _max_drawdown(path):
    path = np.asarray(path, dtype=float)
    return float((path / np.maximum.accumulate(path) - 1.).min())


def score_equity(equity, initial_capital, conventions=ScoreConventions(), start=None):
    """Score an equity curve (timestamps in UTC ms or a DatetimeIndex).

    Day 1's return is measured against initial_capital; the last mark of a
    partial final day counts as that day's close. With start, days before the
    first mark are zero-return days at initial_capital.
    """
    if not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("positive initial_capital required")
    closes = daily_closes(equity, conventions, start, initial_capital)
    path = np.concatenate([[initial_capital], closes.to_numpy()])
    returns = path[1:] / path[:-1] - 1.
    n = len(returns)
    ppy = conventions.periods_per_year
    undefined = {}

    rf_daily = (1. + conventions.risk_free_annual) ** (1. / ppy) - 1.
    excess = returns - rf_daily
    sharpe = None
    if n < 2:
        undefined["sharpe"] = "needs at least two daily returns"
    else:
        sd = float(np.std(excess, ddof=1))
        if sd == 0:
            undefined["sharpe"] = "zero volatility of daily returns"
        else:
            sharpe = float(np.mean(excess) / sd * math.sqrt(ppy))

    active = returns - conventions.sortino_target_daily
    downside = math.sqrt(float(np.mean(np.minimum(active, 0.) ** 2)))
    sortino = None
    if downside == 0:
        undefined["sortino"] = "no day below the Sortino target"
    else:
        sortino = float(np.mean(active) / downside * math.sqrt(ppy))

    total = float(path[-1] / path[0] - 1.)
    if conventions.annual_return == "compound":
        exponent = math.log1p(total) * ppy / n
        annual = math.expm1(exponent) if exponent < 700 else None
    else:
        annual = float(np.mean(returns) * ppy)
    if conventions.drawdown_basis == "daily":
        max_dd = _max_drawdown(path)
    else:
        marks = _as_utc_index(equity).to_numpy()
        max_dd = _max_drawdown(np.concatenate([[initial_capital], marks]))
    calmar = None
    if annual is None:
        undefined["calmar"] = "annualized return overflows"
    elif max_dd == 0:
        undefined["calmar"] = "no drawdown"
    else:
        calmar = float(annual / abs(max_dd))

    parts = {"sortino": sortino, "sharpe": sharpe, "calmar": calmar}
    composite = None if any(v is None for v in parts.values()) else sum(WEIGHTS[k] * v for k, v in parts.items())
    return ScoreReport(composite, sortino, sharpe, calmar, total, annual, max_dd, n,
                       [float(r) for r in returns], undefined, asdict(conventions))

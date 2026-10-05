"""
Performance report for a simulator.SimResult: equity-curve statistics,
trade-level statistics and cost breakdown.

Conventions:
- Sharpe / Sortino are reported two ways: on daily returns (annualized by
  sqrt(365), the conventional figure) and on 1-minute bar returns
  (annualized by sqrt(525600), the convention engine.summarize() and the
  competition score use). With ~92 days of data, both are noisy.
- Sortino uses downside deviation sqrt(mean(min(r, 0)^2)) over ALL periods.
- Annualized return over a quarter of a year is shown but mostly
  meaningless -- do not compare it with multi-year figures.
"""
import numpy as np
import pandas as pd

from .simulator import SimResult

MINUTES_PER_YEAR = 365 * 24 * 60


def _sharpe(r: pd.Series, periods_per_year: float) -> float:
    sd = r.std()
    return float(r.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0


def _sortino(r: pd.Series, periods_per_year: float) -> float:
    dd = np.sqrt((np.minimum(r, 0) ** 2).mean())
    return float(r.mean() / dd * np.sqrt(periods_per_year)) if dd > 0 else 0.0


def _max_streak(flags: np.ndarray) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def _drawdown_episodes(dd: pd.Series) -> list[float]:
    """Depth of each distinct underwater episode (peak -> new peak)."""
    depths, cur = [], 0.0
    for v in dd.to_numpy():
        if v < 0:
            cur = min(cur, v)
        elif cur < 0:
            depths.append(cur)
            cur = 0.0
    if cur < 0:
        depths.append(cur)
    return depths


def equity_stats(equity: pd.Series, initial_capital: float) -> dict:
    eq = pd.concat([pd.Series([initial_capital], index=[equity.index[0] - pd.Timedelta("1min")]), equity])
    r1m = eq.pct_change().dropna()
    daily = eq.resample("1D").last().dropna()
    daily = pd.concat([pd.Series([initial_capital], index=[daily.index[0] - pd.Timedelta("1D")]), daily])
    rd = daily.pct_change().dropna()
    dd = eq / eq.cummax() - 1
    episodes = _drawdown_episodes(dd)
    years = len(r1m) / MINUTES_PER_YEAR
    total = float(eq.iloc[-1] / initial_capital - 1)
    ann = float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else float("nan")
    max_dd = float(dd.min())
    return {
        "initial_capital": initial_capital,
        "final_capital": float(eq.iloc[-1]),
        "total_pnl": float(eq.iloc[-1] - initial_capital),
        "total_return": total,
        "annualized_return": ann,
        "max_drawdown": max_dd,
        "avg_drawdown_episode": float(np.mean(episodes)) if episodes else 0.0,
        "time_underwater": float((dd < 0).mean()),
        "sharpe_daily": _sharpe(rd, 365),
        "sortino_daily": _sortino(rd, 365),
        "sharpe_1m": _sharpe(r1m, MINUTES_PER_YEAR),
        "sortino_1m": _sortino(r1m, MINUTES_PER_YEAR),
        "calmar": float(ann / abs(max_dd)) if max_dd < 0 else 0.0,
        "worst_day": float(rd.min()),
        "best_day": float(rd.max()),
        "positive_days": float((rd > 0).mean()),
    }


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n_trades": 0, "winning_trades": 0, "losing_trades": 0, "win_rate": float("nan"),
                "avg_win": float("nan"), "avg_loss": float("nan"), "profit_factor": float("nan"),
                "expectancy": float("nan"), "expectancy_pct": float("nan"),
                "largest_win": float("nan"), "largest_loss": float("nan"),
                "avg_trade_minutes": float("nan"), "max_consecutive_wins": 0,
                "max_consecutive_losses": 0}
    pnl = trades["pnl"].to_numpy()
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_loss = -losses.sum()
    return {
        "n_trades": int(len(pnl)),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "win_rate": float(len(wins) / len(pnl)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": float(pnl.mean()),
        "expectancy_pct": float(trades["return_on_equity"].mean()),
        "largest_win": float(pnl.max()),
        "largest_loss": float(pnl.min()),
        "avg_trade_minutes": float(trades["bars"].mean()),
        "max_consecutive_wins": _max_streak(pnl > 0),
        "max_consecutive_losses": _max_streak(pnl <= 0),
    }


def alpha_beta(equity: pd.Series, initial_capital: float, benchmark: pd.Series) -> dict:
    """Daily-return regression on a buy & hold benchmark price series.
    In a market that rose 40-70% over the sample, return and Sharpe
    mostly measure exposure; alpha is what timing added beyond that."""
    eq = equity.resample("1D").last().dropna()
    eq = pd.concat([pd.Series([initial_capital], index=[eq.index[0] - pd.Timedelta("1D")]), eq])
    b = benchmark.resample("1D").last().dropna()
    b = pd.concat([pd.Series([benchmark.iloc[0]], index=[b.index[0] - pd.Timedelta("1D")]), b])
    df = pd.concat([eq.pct_change(), b.pct_change()], axis=1, keys=["s", "b"]).dropna()
    if len(df) < 5 or df["b"].var() == 0:
        return {"beta": float("nan"), "alpha_annual": float("nan"), "alpha_tstat": float("nan")}
    beta = df["s"].cov(df["b"]) / df["b"].var()
    resid = df["s"] - beta * df["b"]
    se = resid.std() / np.sqrt(len(resid))
    return {
        "beta": float(beta),
        "alpha_annual": float(resid.mean() * 365),
        "alpha_tstat": float(resid.mean() / se) if se > 0 else float("nan"),
    }


def report(res: SimResult, benchmark: pd.Series | None = None) -> dict:
    """benchmark: buy & hold price (or basket value) series over the same bars."""
    eq = equity_stats(res.equity, res.initial_capital)
    tr = trade_stats(res.trades)
    gross_w = res.weights.abs().sum(axis=1)
    out = {**eq, **tr}
    if benchmark is not None:
        out.update(alpha_beta(res.equity, res.initial_capital, benchmark))
    out.update({
        "exposure_time": float((gross_w > 1e-9).mean()),        # fraction of bars holding anything
        "avg_gross_exposure": float(gross_w.mean()),            # average fraction of equity deployed
        "turnover": float(res.traded_notional / res.equity.mean()),  # traded notional / average equity
        "fees_paid": float(res.fees),
        "slippage_cost": float(res.slippage),
        "total_costs": float(res.fees + res.slippage),
        "costs_pct_of_gross_pnl": float((res.fees + res.slippage) /
                                        max(1e-9, abs(eq["total_pnl"] + res.fees + res.slippage))),
    })
    return out

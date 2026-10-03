"""
Backtest engine. Takes a signal series (position size per timestamp, in
[-1, 1] fraction of capital, negative = short) and simulates PnL with
realistic costs applied on every position change.
"""
import numpy as np
import pandas as pd

from .. import config, costs


def simulate(
    price: pd.Series,
    position: pd.Series,
    taker: bool = True,
) -> pd.DataFrame:
    """
    price, position: aligned on the same index. position[t] is the
    fraction of capital held from t to t+1 (decided using info at t).
    Costs are charged on every change in position size (turnover).
    """
    df = pd.DataFrame({"price": price, "position": position}).dropna()
    asset_ret = df["price"].pct_change().shift(-1)  # return realized while holding position set at t

    turnover = df["position"].diff().abs().fillna(df["position"].abs())
    cost_per_unit = costs.estimate_round_trip_cost(taker) / 2  # charged per side, turnover already reflects one side
    fee_drag = turnover * cost_per_unit

    gross_ret = df["position"] * asset_ret
    net_ret = gross_ret - fee_drag

    equity = (1 + net_ret.fillna(0)).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0

    out = pd.DataFrame({
        "gross_return": gross_ret,
        "net_return": net_ret,
        "equity": equity,
        "drawdown": drawdown,
        "turnover": turnover,
    })
    return out


def _sortino_ratio(net: pd.Series, periods_per_year: int) -> float:
    """Like Sharpe, but only penalizes downside volatility -- upside swings
    don't count against the bot. This is 40% of the competition's composite
    score, the single heaviest-weighted metric."""
    downside = net[net < 0]
    if len(net) == 0 or len(downside) == 0 or downside.std() == 0:
        return 0.0
    return (net.mean() / downside.std()) * np.sqrt(periods_per_year)


def _calmar_ratio(total_return: float, max_drawdown: float, years: float) -> float:
    """Annualized return / max drawdown. Rewards steady compounding and
    directly punishes deep drawdowns -- the metric most sensitive to our
    MAX_DRAWDOWN_KILL_SWITCH setting in config.py."""
    if max_drawdown == 0 or years <= 0:
        return 0.0
    annualized_return = (1 + total_return) ** (1 / years) - 1
    return annualized_return / abs(max_drawdown)


def summarize(
    sim_result: pd.DataFrame,
    periods_per_year: int = config.BARS_PER_YEAR,
    bars_per_year: int | None = None,
) -> dict:
    net = sim_result["net_return"].dropna()
    if len(net) == 0 or net.std() == 0:
        sharpe = 0.0
    else:
        sharpe = (net.mean() / net.std()) * np.sqrt(periods_per_year)

    total_return = sim_result["equity"].iloc[-1] - 1.0 if len(sim_result) else float("nan")
    max_dd = sim_result["drawdown"].min() if len(sim_result) else float("nan")

    bars_per_year = bars_per_year or periods_per_year
    years = len(sim_result) / bars_per_year if bars_per_year else float("nan")

    sortino = _sortino_ratio(net, periods_per_year)
    calmar = _calmar_ratio(total_return, max_dd, years) if not np.isnan(total_return) else 0.0

    composite_score = 0.4 * sortino + 0.3 * sharpe + 0.3 * calmar

    return {
        "total_net_return": total_return,
        "sharpe_like": sharpe,
        "sortino_like": sortino,
        "calmar_like": calmar,
        "composite_score": composite_score,   # 0.4*Sortino + 0.3*Sharpe + 0.3*Calmar, matches competition formula
        "max_drawdown": max_dd,
        "avg_turnover": sim_result["turnover"].mean(),
        "n_trades_proxy": (sim_result["turnover"] > 1e-9).sum(),
        "win_rate": (net > 0).mean() if len(net) else float("nan"),
    }

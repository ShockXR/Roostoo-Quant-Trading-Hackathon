"""
Transaction-cost model. A trade is only a candidate if expected edge
clears realistic costs -- this is a hard gate applied AFTER the model's
prediction, not baked into the target threshold alone (the threshold
target already biases the model to avoid marginal trades, but this
filter is the final, explicit check using current settings).
"""
import math
from . import config


def estimate_round_trip_cost(taker: bool = True, spread: float = 0., short: bool = False) -> float:
    fee = config.SHORT_FEE if short else (config.TAKER_FEE if taker else config.MAKER_FEE)
    if not math.isfinite(spread) or spread < 0:
        raise ValueError("invalid spread")
    return 2 * fee + config.SLIPPAGE_ESTIMATE + spread


def expected_net_return(expected_gross_return: float, taker: bool = True) -> float:
    return expected_gross_return - estimate_round_trip_cost(taker) - config.SAFETY_MARGIN


def passes_cost_filter(expected_gross_return: float, taker: bool = True, spread: float = 0., short: bool = False) -> bool:
    return math.isfinite(expected_gross_return) and expected_gross_return > estimate_round_trip_cost(taker, spread, short) + config.SAFETY_MARGIN


def proba_to_expected_return(proba: float, avg_win: float, avg_loss: float) -> float:
    """
    Converts a classifier's P(target hit) into an expected-return estimate
    using empirically observed average win/loss magnitudes (compute these
    from backtest fold results, not guessed). Simple expectation:
    E[R] = p * avg_win - (1-p) * avg_loss
    """
    return proba * avg_win - (1 - proba) * avg_loss


def conditional_expected_return(proba, mean_hit_return, mean_miss_return):
    """Class 0 includes small gains too, so preserve its signed mean return."""
    if not all(math.isfinite(v) for v in (proba, mean_hit_return, mean_miss_return)) or not 0 <= proba <= 1:
        raise ValueError("invalid probability/payoff estimates")
    return proba * mean_hit_return + (1 - proba) * mean_miss_return

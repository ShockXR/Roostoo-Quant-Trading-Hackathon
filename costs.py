"""
Transaction-cost model. A trade is only a candidate if expected edge
clears realistic costs -- this is a hard gate applied AFTER the model's
prediction, not baked into the target threshold alone (the threshold
target already biases the model to avoid marginal trades, but this
filter is the final, explicit check using current settings).
"""
from . import config


def estimate_round_trip_cost(taker: bool = True) -> float:
    fee = config.TAKER_FEE if taker else config.MAKER_FEE
    return 2 * fee + config.SLIPPAGE_ESTIMATE


def expected_net_return(expected_gross_return: float, taker: bool = True) -> float:
    return expected_gross_return - estimate_round_trip_cost(taker) - config.SAFETY_MARGIN


def passes_cost_filter(expected_gross_return: float, taker: bool = True) -> bool:
    return expected_net_return(expected_gross_return, taker) > 0


def proba_to_expected_return(proba: float, avg_win: float, avg_loss: float) -> float:
    """
    Converts a classifier's P(target hit) into an expected-return estimate
    using empirically observed average win/loss magnitudes (compute these
    from backtest fold results, not guessed). Simple expectation:
    E[R] = p * avg_win - (1-p) * avg_loss
    """
    return proba * avg_win - (1 - proba) * avg_loss

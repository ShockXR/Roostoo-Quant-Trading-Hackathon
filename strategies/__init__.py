"""
Hourly portfolio strategies.

Each model is a function targets(bars, params) -> DataFrame of target
weights (hourly index x symbol, fraction of equity, long only), where bars
maps symbol -> strategies.features.hourly_bars() output on a shared index.
A target on bar t may only use bars <= t; it is filled at bar t+1's open.

MODELS is the research registry; config.PRODUCTION_MODEL names the one the
live loop trades.
"""
import pandas as pd

from . import (m0_reversal, m1_reversal_trend, m2_xs_momentum, m3_momentum_regime, m4_momentum_longshort,
               mc_momentum)


def _buy_hold(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    from .. import config
    from .features import panel
    close = panel(bars, "close")
    return pd.DataFrame(float(params.get("weight", config.MAX_POSITION_PER_ASSET)),
                        index=close.index, columns=close.columns)


MODELS = {
    "buy_hold": _buy_hold,
    "m0_reversal": m0_reversal.targets,
    "m1_reversal_trend": m1_reversal_trend.targets,
    "m2_xs_momentum": m2_xs_momentum.targets,
    "m3_momentum_regime": m3_momentum_regime.targets,
    "m4_momentum_longshort": m4_momentum_longshort.targets,
    "mc_momentum": mc_momentum.targets,
}


def generate(name: str, bars: dict[str, pd.DataFrame], params: dict | None = None,
             eligible: pd.DataFrame | None = None) -> pd.DataFrame:
    """eligible: optional (time x symbol) bool mask of the point-in-time
    universe. Models only select eligible assets, and any position in an
    asset that is not eligible is closed."""
    params = dict(params or {})
    if eligible is not None:
        params["_eligible"] = eligible
    t = MODELS[name](bars, params)
    if eligible is not None:
        t = t.where(eligible.reindex_like(t).fillna(False).astype(bool), 0.0)
    return t

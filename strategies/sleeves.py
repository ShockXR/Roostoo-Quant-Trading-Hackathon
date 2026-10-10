"""Three-sleeve target weights: large caps, mid caps, small caps (docs/SLEEVES.md).

Each sleeve is a separate universe with its own signal, volatility target and
gross cap. Every function returns weights on the panel's hourly index, and row
H uses only prices stamped at or before H, so live and backtest share the same
code. Sleeve universes are disjoint, so a coin's weight comes from one sleeve.

- large: 28-day cross-sectional momentum, top 3, equal weight, only while BTC
  is above its 100-day average (Model 3 from the large-cap research branch,
  2018-2026 out of sample). It replaces the 24h dip-buyer, which lost 55% over
  2022-26 in that research.
- mid: the 24h/168h momentum blend from docs/MIDCAP_RESEARCH.md, at half size
  while the BTC regime is off.
- small: 168h momentum confirmed by a positive 24h return, top 2, only while
  the BTC regime is on. Tiny budget; the live engine adds a hard stop.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .midcap import BLEND_PARTS, blend, hourly_returns, rebalance_mask, vol_target

LARGE, MID, SMALL = "large", "mid", "small"
SLEEVES = (LARGE, MID, SMALL)
REGIME_PAIR = "BTC/USD"


@dataclass(frozen=True)
class SleeveSpec:
    name: str
    target_daily_vol: float  # predicted daily volatility of the sleeve's holdings, as a fraction of equity
    max_gross: float         # sleeve exposure cap, fraction of equity
    max_weight: float        # per-coin cap, fraction of equity
    regime_off_scale: float  # exposure multiplier while BTC is below its moving average


def specs(config):
    return {name: SleeveSpec(name, **params) for name, params in config.SLEEVE_PARAMS.items()}


def btc_regime(prices, sma_hours):
    """True while the price is above its trailing sma_hours mean (False until the window fills)."""
    sma = prices.rolling(sma_hours, min_periods=sma_hours).mean()
    return prices > sma


def _equal_weight(selected):
    count = selected.sum(axis=1)
    return selected.astype(float).div(count.where(count > 0), axis=0).fillna(0.)


def regime_momentum(panel, lookback=672, top_k=3):
    """Top top_k coins by positive trailing return, equal weight (unit gross or 0)."""
    score = panel.signal / panel.signal.shift(lookback) - 1
    selected = (score.rank(axis=1, ascending=False) <= top_k) & (score > 0)
    return _equal_weight(selected)


def confirmed_momentum(panel, lookback=168, confirm=24, top_k=2):
    """Top top_k by lookback return, kept only if both the lookback and the confirm return are positive."""
    long_score = panel.signal / panel.signal.shift(lookback) - 1
    short_score = panel.signal / panel.signal.shift(confirm) - 1
    selected = (long_score.rank(axis=1, ascending=False) <= top_k) & (long_score > 0) & (short_score > 0)
    return _equal_weight(selected)


def raw_signal(panel, sleeve, config):
    if sleeve == LARGE:
        return regime_momentum(panel, config.LARGE_LOOKBACK_HOURS, config.LARGE_TOP_K)
    if sleeve == MID:
        return blend(panel, BLEND_PARTS)
    if sleeve == SMALL:
        return confirmed_momentum(panel, config.SMALL_LOOKBACK_HOURS, config.SMALL_CONFIRM_HOURS, config.SMALL_TOP_K)
    raise ValueError(f"unknown sleeve {sleeve}")


def sleeve_weights(panel, sleeve, spec, regime, config, rows=None):
    """Signal -> volatility target -> per-coin cap -> regime scale, on the given row positions.

    regime: boolean Series (True = BTC above its average) on or covering the panel's index.
    rows: positions to compute (default: every rebalance hour); other rows are NaN.
    """
    raw = raw_signal(panel, sleeve, config)
    if rows is None:
        rows = np.flatnonzero(rebalance_mask(panel.signal.index, config.MIDCAP_REBALANCE_EVERY_HOURS).to_numpy())
    sized = vol_target(raw, hourly_returns(panel), spec.target_daily_vol, max_gross=spec.max_gross, rows=rows)
    sized = sized.clip(0., spec.max_weight)
    on = regime.reindex(panel.signal.index).fillna(False).astype(bool)
    sized = sized.mul(np.where(on, 1., spec.regime_off_scale), axis=0)
    keep = np.zeros(len(sized), dtype=bool)
    keep[list(rows)] = True
    return sized.where(pd.Series(keep, index=sized.index), np.nan, axis=0)


def regime_from(large_panel, config):
    if REGIME_PAIR not in large_panel.signal.columns:
        raise ValueError(f"{REGIME_PAIR} is required for the market regime")
    return btc_regime(large_panel.signal[REGIME_PAIR], config.REGIME_SMA_HOURS)


def latest_weights(panels, config):
    """Live: {sleeve: {pair: weight}} at the last row of each sleeve's panel, plus the regime flag.

    panels: {sleeve: HourlyPanel}; all must end at the same hour and the large panel must hold BTC.
    """
    sleeve_specs = specs(config)
    regime = regime_from(panels[LARGE], config)
    out = {}
    for sleeve, panel in panels.items():
        if panel.signal.shape[1] == 0:
            out[sleeve] = {}
            continue
        if panel.signal.index[-1] != panels[LARGE].signal.index[-1]:
            raise ValueError("sleeve panels end at different hours")
        row = sleeve_weights(panel, sleeve, sleeve_specs[sleeve], regime, config, rows=[len(panel.signal) - 1]).iloc[-1]
        if row.isna().any():
            raise ValueError(f"{sleeve} weights incomplete at the rebalance hour")
        out[sleeve] = {pair: float(w) for pair, w in row.items()}
    return out, bool(regime.iloc[-1])


def backtest_weights(panel, config, sleeve_pairs=None, scale=None):
    """Research: one weight table over a joint panel, every sleeve on its own columns.

    sleeve_pairs: {sleeve: [pairs]} (default config.SLEEVE_PAIRS, restricted to the panel's columns).
    scale: optional {sleeve: multiplier} applied after the sleeve cap, e.g. the push-mode multipliers.
    """
    from ..backtest.portfolio import HourlyPanel
    sleeve_specs = specs(config)
    sleeve_pairs = sleeve_pairs or config.SLEEVE_PAIRS
    regime = btc_regime(panel.signal[REGIME_PAIR], config.REGIME_SMA_HOURS)
    tables = []
    for sleeve in SLEEVES:
        columns = [p for p in sleeve_pairs.get(sleeve, ()) if p in panel.signal.columns]
        if not columns:
            continue
        sub = HourlyPanel(panel.signal[columns], panel.execution[columns], panel.volume[columns])
        table = sleeve_weights(sub, sleeve, sleeve_specs[sleeve], regime, config)
        multiplier = (scale or {}).get(sleeve, 1.)
        if multiplier != 1.:
            table = cap_sleeve(table * multiplier, sleeve_specs[sleeve])
        tables.append(table)
    weights = pd.concat(tables, axis=1).reindex(columns=panel.signal.columns)
    filled = weights.notna().any(axis=1)
    return weights.where(~filled, weights.fillna(0.), axis=0)


def cap_sleeve(table, spec):
    """Re-apply the per-coin and sleeve gross caps after a multiplier (NaN rows stay NaN)."""
    capped = table.clip(upper=spec.max_weight)
    gross = capped.sum(axis=1)
    factor = (spec.max_gross / gross).clip(upper=1.).where(gross > 0, 1.)
    return capped.mul(factor, axis=0)

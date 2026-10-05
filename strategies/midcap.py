"""Mid-cap sleeve strategies as hourly target-weight tables.

Every function returns weights on the panel's hourly index. Row H uses only
prices and volumes stamped at or before H (rolling windows and shifts are
backward-looking). Rows that are not rebalance hours are NaN, meaning "hold".
BTC and ETH are excluded from this sleeve; the market reference is an
equal-weighted index of the sleeve's own coins.
"""
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

HOURS_PER_DAY = 24


def hourly_returns(panel):
    return panel.signal.pct_change()


def rebalance_mask(index, every):
    if every not in {1, 2, 3, 4, 6, 8, 12, 24}:
        raise ValueError("every must divide 24 hours")
    return pd.Series(index.hour % every == 0, index=index)


def market_index(panel):
    """Equal-weighted index level of the sleeve, rebalanced hourly."""
    return (1 + hourly_returns(panel).mean(axis=1).fillna(0.)).cumprod()


def _inverse_vol(selected, returns, lookback=168):
    vol = returns.rolling(lookback, min_periods=lookback).std()
    raw = selected.astype(float) / vol
    raw = raw.where(selected, 0.).replace([np.inf, -np.inf], np.nan)
    total = raw.sum(axis=1)
    out = raw.div(total.where(total > 0), axis=0)
    return out.where(vol.notna(), np.nan).fillna(0.)


# --- signals: unit-gross weights (sum of |w| is 1 or 0) ---

def momentum(panel, lookback, top_k, absolute=True, long_short=False):
    """Long the top_k by trailing return; absolute=True also requires that return > 0."""
    score = panel.signal / panel.signal.shift(lookback) - 1
    returns = hourly_returns(panel)
    rank = score.rank(axis=1, ascending=False)
    longs = (rank <= top_k) & score.notna()
    if absolute:
        longs &= score > 0
    weights = _inverse_vol(longs, returns)
    if long_short:
        shorts = (score.rank(axis=1, ascending=True) <= top_k) & score.notna()
        if absolute:
            shorts &= score < 0
        weights = .5 * weights - .5 * _inverse_vol(shorts, returns)
    return weights.where(score.notna().sum(axis=1) >= 2 * top_k, 0.)


def reversal(panel, lookback, top_k):
    """Long the top_k laggards by return relative to the sleeve index (short-horizon reversal)."""
    index = market_index(panel)
    residual = (panel.signal / panel.signal.shift(lookback)).div(index / index.shift(lookback), axis=0) - 1
    longs = (residual.rank(axis=1, ascending=True) <= top_k) & residual.notna()
    return _inverse_vol(longs, hourly_returns(panel))


def trend(panel, ma_hours):
    """Long every coin trading above its own moving average, inverse-vol weighted."""
    above = panel.signal > panel.signal.rolling(ma_hours, min_periods=ma_hours).mean()
    return _inverse_vol(above, hourly_returns(panel))


def breakout(panel, channel_hours, volume_z=1.0):
    """Enter on a close above the prior channel high with above-normal volume; exit below the half-channel mean."""
    price = panel.signal
    prior_high = price.shift(1).rolling(channel_hours, min_periods=channel_hours).max()
    exit_line = price.rolling(channel_hours // 2, min_periods=channel_hours // 2).mean()
    log_volume = np.log1p(panel.volume)
    z = (log_volume - log_volume.rolling(168, min_periods=168).mean()) / log_volume.rolling(168, min_periods=168).std()
    enter = ((price > prior_high) & (z > volume_z)).to_numpy()
    leave = (price < exit_line).to_numpy()
    state = np.zeros(price.shape, dtype=bool)
    holding = np.zeros(price.shape[1], dtype=bool)
    for i in range(len(price)):
        holding = (holding & ~leave[i]) | enter[i]
        state[i] = holding
    return _inverse_vol(pd.DataFrame(state, index=price.index, columns=price.columns), hourly_returns(panel))


def blend(panel, parts):
    """Average of several unit-gross signals, e.g. two momentum lookbacks."""
    tables = [SIGNALS[part["signal"]](panel, **part.get("params", {})) for part in parts]
    return sum(tables) / len(tables)


def equal_weight(panel):
    return pd.DataFrame(1. / panel.signal.shape[1], index=panel.signal.index, columns=panel.signal.columns)


# --- overlays ---

def vol_target(weights, returns, target_daily, lookback=168, max_gross=1.0, rows=None):
    """Scale each row so predicted daily portfolio volatility is at most target_daily."""
    out = weights.copy()
    values = returns.to_numpy(dtype=float)
    rows = range(len(weights)) if rows is None else rows
    for i in rows:
        w = weights.iloc[i].to_numpy(dtype=float)
        gross = np.abs(w).sum()
        if gross == 0 or i + 1 < lookback:
            out.iloc[i] = 0.
            continue
        window = values[i + 1 - lookback:i + 1]
        cov = np.cov(window[~np.isnan(window).any(axis=1)], rowvar=False)
        sigma = float(np.sqrt(max(w @ cov @ w, 0.) * HOURS_PER_DAY))
        scale = min(max_gross / gross, target_daily / sigma if sigma > 0 else max_gross / gross)
        out.iloc[i] = w * scale
    return out


def crash_gate(panel, drop=0.08, low=0.25, below_trend=0.5, trend_hours=168):
    """Exposure multiplier from the sleeve index: `low` after a 24h fall beyond `drop`,
    `below_trend` while the index is under its moving average, else 1. Never 0, so the
    bot keeps trading (8-active-day rule)."""
    index = market_index(panel)
    fall = index / index.shift(24) - 1 < -drop
    weak = index < index.rolling(trend_hours, min_periods=trend_hours).mean()
    return pd.Series(np.where(fall, low, np.where(weak, below_trend, 1.)), index=index.index)


def drawdown_scale(drawdown, schedule):
    """Exposure multiplier for the current drawdown (a negative fraction).

    schedule: ((threshold, scale), ...) with thresholds as positive fractions,
    e.g. ((0.03, 0.5), (0.05, 0.25)) halves exposure beyond a 3% drawdown and
    quarters it beyond 5%. Scales never reach 0, so the bot keeps trading.
    """
    scale = 1.
    for threshold, level in sorted(schedule or ()):
        if not 0 < level <= 1:
            raise ValueError("drawdown scales must be in (0, 1]")
        if -drawdown >= threshold:
            scale = level
    return scale


@dataclass(frozen=True)
class Spec:
    name: str
    signal: str
    params: dict = field(default_factory=dict)
    every: int = 24
    target_daily_vol: float | None = 0.015
    max_weight: float = 0.20
    max_gross: float = 1.0
    gate: dict | None = None

    def label(self):
        params = self.params if self.signal != "blend" else {
            "parts": "+".join(f"{p['signal']}({','.join(f'{k}={v}' for k, v in p.get('params', {}).items())})"
                              for p in self.params["parts"])}
        bits = [self.signal] + [f"{k}={v}" for k, v in params.items()] + [f"every={self.every}h"]
        bits.append(f"vol={self.target_daily_vol}" if self.target_daily_vol else "vol=off")
        if self.gate is not None:
            bits.append("gate")
        return " ".join(bits)


SIGNALS = {"momentum": momentum, "reversal": reversal, "trend": trend, "breakout": breakout,
           "equal_weight": equal_weight, "blend": blend}


def build_weights(panel, spec):
    """Signal -> volatility target -> per-coin cap -> crash gate, on rebalance hours only."""
    raw = SIGNALS[spec.signal](panel, **spec.params)
    mask = rebalance_mask(panel.signal.index, spec.every).to_numpy()
    rows = np.flatnonzero(mask)
    if spec.target_daily_vol:
        sized = vol_target(raw, hourly_returns(panel), spec.target_daily_vol, max_gross=spec.max_gross, rows=rows)
    else:
        gross = raw.abs().sum(axis=1)
        sized = raw.mul((spec.max_gross / gross).clip(upper=1.).where(gross > 0, 0.), axis=0)
    sized = sized.clip(-spec.max_weight, spec.max_weight)
    if spec.gate is not None:
        sized = sized.mul(crash_gate(panel, **spec.gate), axis=0)
    out = sized.where(pd.Series(mask, index=sized.index), np.nan, axis=0)
    return out


BLEND_PARTS = [{"signal": "momentum", "params": {"lookback": 24, "top_k": 5}},
               {"signal": "momentum", "params": {"lookback": 168, "top_k": 3}}]


def live_spec(config):
    """The strategy the live bot runs: two-lookback momentum blend, daily, volatility-targeted."""
    return Spec("blend_live", "blend", {"parts": BLEND_PARTS}, every=config.MIDCAP_REBALANCE_EVERY_HOURS,
                target_daily_vol=config.MIDCAP_TARGET_DAILY_VOL, max_weight=config.MAX_POSITION_PER_ASSET,
                max_gross=config.MAX_PORTFOLIO_EXPOSURE)

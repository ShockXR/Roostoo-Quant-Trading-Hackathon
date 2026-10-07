"""
Hourly panel for the point-in-time mid-cap universe built by
scripts/build_midcap_universe.py.

Returns (bars, tradable, eligible):
  bars      symbol -> hourly OHLCV on one index (2021-06 .. 2026-09, so
            indicators are warm when membership starts in 2022-01), plus
            vol1m_1d estimated from hourly returns (1m data is not
            downloaded for ~150 symbols): std of the last 24 hourly returns
            / sqrt(60), so vol1m_1d * sqrt(60 L) = hourly std * sqrt(L).
  tradable  listed on Binance at that hour (first .. last bar)
  eligible  a mid cap that month (membership fixed from data before the
            month); BTCUSDT is included in bars as the market proxy but is
            never eligible.
Prices outside an asset's own history are held flat; missing hours inside
it (exchange halts) are forward-filled.
"""
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOURLY = ROOT / "binance_data" / "universe" / "hourly"
MEMBERSHIP = ROOT / "experiments" / "long" / "mid" / "membership.csv"
START, END = pd.Timestamp("2021-06-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC")
MARKET = "BTCUSDT"


def load_membership(tier: str = "mid") -> pd.DataFrame:
    mem = pd.read_csv(MEMBERSHIP)
    return mem[mem.tier == tier]


def eligible_mask(mem: pd.DataFrame, idx: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    month = pd.Series(idx.strftime("%Y-%m"), index=idx)
    out = pd.DataFrame(False, index=idx, columns=symbols)
    for m, g in mem.groupby("month"):
        rows = (month == m).to_numpy()
        out.loc[rows, [s for s in g.symbol if s in out.columns]] = True
    return out


def load_mid_universe(exclude: list[str] | None = None,
                      ranks: tuple[int, int] | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Cached (pickle next to the hourly files, rebuilt if membership changes).
    ranks: keep only members whose liquidity rank that month is in [lo, hi]."""
    cache = HOURLY.parent / "mid_panel.pkl"
    if cache.exists() and cache.stat().st_mtime > MEMBERSHIP.stat().st_mtime:
        bars, tradable, eligible = pd.read_pickle(cache)
    else:
        bars, tradable, eligible = _build()
        pd.to_pickle((bars, tradable, eligible), cache)
    if ranks:
        mem = load_membership()
        mem = mem[(mem["rank"] >= ranks[0]) & (mem["rank"] <= ranks[1])]
        eligible = eligible & eligible_mask(mem, eligible.index, list(bars))
    if exclude:
        keep = [s for s in bars if s not in exclude]
        bars = {s: bars[s] for s in keep}
        tradable, eligible = tradable[keep], eligible[keep]
    return bars, tradable, eligible


def _build() -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    mem = load_membership()
    symbols = sorted(set(mem.symbol))
    idx = pd.date_range(START, END, freq="1h", inclusive="left")
    bars, tradable = {}, {}
    for s in symbols + [MARKET]:
        f = HOURLY / f"{s}_1h.parquet"
        if not f.exists():
            continue
        b = pd.read_parquet(f)
        b = b[(b.index >= START) & (b.index < END)]
        live = pd.Series((idx >= b.index[0]) & (idx <= b.index[-1]), index=idx)
        b = b.reindex(idx)
        b["close"] = b["close"].ffill().bfill()
        for col in ("open", "high", "low"):
            b[col] = b[col].fillna(b["close"])
        b["volume"] = b["volume"].fillna(0.0)
        b["qvol"] = b["qvol"].fillna(0.0)
        b["vol1m_1d"] = b["close"].pct_change().where(live).rolling(24).std() / np.sqrt(60)
        bars[s], tradable[s] = b, live
    tradable = pd.DataFrame(tradable)
    eligible = eligible_mask(mem, idx, list(bars)) & tradable
    eligible[MARKET] = False
    return bars, tradable, eligible

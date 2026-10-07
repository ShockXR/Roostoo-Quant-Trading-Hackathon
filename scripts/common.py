"""
Shared helpers for the offline research scripts: package bootstrap,
Binance 1m loading and the chronological evaluation periods.
"""
import importlib.util
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "binance_data" / "processed" / "parquet"
EXPERIMENTS_DIR = ROOT / "experiments"

LARGE_CAPS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MID_CAPS = ["UNIUSDT", "AAVEUSDT", "CRVUSDT", "ARBUSDT", "ENAUSDT"]
UNIVERSES = {"large_caps": LARGE_CAPS, "mid_caps": MID_CAPS}

# --- Long-history research (scripts/run_long.py) ---------------------------
LONG_DATA_DIR = ROOT / "binance_data" / "long" / "processed" / "parquet"
HOURLY_CACHE_DIR = ROOT / "binance_data" / "long" / "hourly"

# Point-in-time large-cap universe: the ten largest non-stablecoin assets by
# market cap on 2022-01-01 (CoinMarketCap snapshot; stablecoins USDT, USDC,
# BUSD and UST excluded), fixed at the START of the sample so later winners
# are not selected with hindsight. It includes LUNA, which collapsed and was
# delisted in May 2022; it trades until its last Binance bar and is then
# force-closed. Later entrants (TRX, TON, SUI, ...) are deliberately absent.
LARGE_CAPS_2022 = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT",
                   "XRPUSDT", "LUNAUSDT", "DOTUSDT", "AVAXUSDT", "DOGEUSDT"]
LONG_UNIVERSES = {"large_caps_2022": LARGE_CAPS_2022}
# Last real bar (exclusive end) of assets that left Binance during the sample.
# LUNAUSDT stopped trading 2022-05-13 00:39 UTC; the ticker reappears on
# 2022-05-31 as a different token (LUNA 2.0), which must not be spliced on.
DELISTED = {"LUNAUSDT": "2022-05-13 00:40"}

# Chronological split for the long history, fixed before any model was run
# on it. Parameters are chosen on TRAIN (2022 bear + 2023 recovery), checked
# on VALIDATION (2024), and TEST (2025-01 .. 2026-06) is read only for the
# frozen candidates. HOLDOUT is the original Jul-Sep 2026 window, read once
# at the very end; Model 0 was tuned on its first two months, so it is
# contaminated in Model 0's favour.
LONG_PERIODS = {
    "train": ("2022-01-01", "2024-01-01"),
    "validation": ("2024-01-01", "2025-01-01"),
    "test": ("2025-01-01", "2026-07-01"),
    "holdout": ("2026-07-01", "2026-10-01"),
    "full": ("2022-01-01", "2026-10-01"),
}

# Chronological split, fixed before any experiment was run. Strategy
# changes are chosen on TRAIN and confirmed on VALIDATION; TEST is only
# read for the original baseline and the final candidate.
PERIODS = {
    "train": ("2026-07-01", "2026-08-01"),
    "validation": ("2026-08-01", "2026-09-01"),
    "test": ("2026-09-01", "2026-10-01"),
    "full": ("2026-07-01", "2026-10-01"),
}


def register_package():
    """The repo root is itself a package using relative imports, but its
    directory name has hyphens -- register it under an importable name."""
    if "roostoo" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "roostoo", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["roostoo"] = pkg
    spec.loader.exec_module(pkg)


def to_pair(symbol: str) -> str:
    """SOLUSDT -> SOL/USD (the Roostoo pair naming used throughout config)."""
    return symbol.removesuffix("USDT") + "/USD"


def load_ohlcv(symbol: str, data_dir: pathlib.Path = DATA_DIR) -> pd.DataFrame:
    df = pd.read_parquet(data_dir / f"{symbol}_1m.parquet",
                         columns=["open_time", "open", "high", "low", "close", "volume"])
    return df.set_index(pd.to_datetime(df.pop("open_time"), utc=True)).sort_index()


def period_slice(obj, period: str, periods: dict = PERIODS):
    start, end = periods[period]
    return obj[(obj.index >= pd.Timestamp(start, tz="UTC")) & (obj.index < pd.Timestamp(end, tz="UTC"))]

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


def load_ohlcv(symbol: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_DIR / f"{symbol}_1m.parquet",
                         columns=["open_time", "open", "high", "low", "close", "volume"])
    return df.set_index(pd.to_datetime(df.pop("open_time"), utc=True)).sort_index()


def period_slice(obj, period: str):
    start, end = PERIODS[period]
    return obj[(obj.index >= pd.Timestamp(start, tz="UTC")) & (obj.index < pd.Timestamp(end, tz="UTC"))]

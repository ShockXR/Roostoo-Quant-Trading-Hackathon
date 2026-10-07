"""
Point-in-time liquidity membership (strategies/universe.py) and the live
mid-cap book's target construction (main._latest_mid_targets).

Run from the project root:

    .venv/bin/python -m unittest discover -s tests -v
"""
import pathlib
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from common import register_package  # noqa: E402

register_package()
from roostoo import main as live  # noqa: E402
from roostoo.strategies import universe  # noqa: E402

DAYS = pd.date_range("2025-01-01", "2025-12-31", freq="1D", tz="UTC")


def daily(volumes: dict[str, float], listed: dict[str, str] | None = None, gaps: dict[str, str] | None = None):
    qvol = pd.DataFrame({s: v for s, v in volumes.items()}, index=DAYS)
    for s, start in (listed or {}).items():
        qvol.loc[qvol.index < pd.Timestamp(start, tz="UTC"), s] = np.nan
    for s, day in (gaps or {}).items():
        qvol.loc[pd.Timestamp(day, tz="UTC"), s] = 0.0
    close = qvol.notna().astype(float).replace(0, np.nan)
    return qvol, close


class RanksTest(unittest.TestCase):
    def test_candidates(self):
        self.assertTrue(universe.is_candidate("UNIUSDT"))
        for s in ("USDCUSDT", "BTCUPUSDT", "WBTCUSDT", "UNIBTC", "EURUSDT"):
            self.assertFalse(universe.is_candidate(s), s)

    def test_rank_by_volume_with_age_and_activity_rules(self):
        qvol, close = daily({"A": 30.0, "B": 20.0, "C": 10.0, "D": 50.0, "E": 40.0},
                            listed={"D": "2025-08-15"}, gaps={"E": "2025-09-20"})
        r = universe.ranks(qvol, close, pd.DatetimeIndex([pd.Timestamp("2025-10-01", tz="UTC")]))
        row = r.iloc[0]
        self.assertTrue(np.isnan(row.get("D", np.nan)))   # listed < 90 days before the month
        self.assertTrue(np.isnan(row.get("E", np.nan)))   # did not trade on one of the last 30 days
        self.assertEqual(row[["A", "B", "C"]].tolist(), [1, 2, 3])

    def test_uses_only_data_before_the_month(self):
        qvol, close = daily({"A": 30.0, "B": 20.0})
        m = pd.DatetimeIndex([pd.Timestamp("2025-10-01", tz="UTC")])
        before = universe.ranks(qvol, close, m)
        qvol.loc[qvol.index >= m[0], "B"] = 1e9      # B explodes from the 1st on
        pd.testing.assert_frame_equal(before, universe.ranks(qvol, close, m))

    def test_members_by_rank_band(self):
        rank = pd.DataFrame({"A": [1.0], "B": [21.0], "C": [40.0], "D": [41.0]},
                            index=[pd.Timestamp("2025-10-01", tz="UTC")])
        self.assertEqual(universe.members(rank, 21, 40), {"2025-10": ["B", "C"]})


def hourly(symbol_seed: int, n_days=200, drift=0.0):
    idx = pd.date_range("2025-03-20", periods=n_days * 24, freq="1h", tz="UTC")
    rng = np.random.default_rng(symbol_seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, len(idx))))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0,
                         "vol1m_1d": np.nan}, index=idx)


class MidBookTest(unittest.TestCase):
    def test_only_listed_members_of_the_current_month_get_weight(self):
        bars = {"BTCUSDT": hourly(0, drift=0.001), "AAAUSDT": hourly(1, drift=0.002),
                "BBBUSDT": hourly(2, drift=0.002), "CCCUSDT": hourly(3, drift=0.002)}
        last = bars["BTCUSDT"].index[-1]
        cur, prev = last.strftime("%Y-%m"), (last - pd.Timedelta(days=40)).strftime("%Y-%m")
        membership = {prev: ["AAAUSDT", "BBBUSDT", "CCCUSDT"], cur: ["AAAUSDT", "CCCUSDT", "NOTLISTEDUSDT"]}
        pair_info = {"AAA/USD": {"CanTrade": True, "AssetType": "crypto"},
                     "BBB/USD": {"CanTrade": True, "AssetType": "crypto"},
                     "CCC/USD": {"CanTrade": True, "AssetType": "stock"}}
        with mock.patch("roostoo.data.binance_universe.mid_membership", return_value=membership), \
             mock.patch.object(live, "fetch_hourly", side_effect=lambda s, d, now: bars[s]):
            t_bar, targets = live._latest_mid_targets(None, pair_info)
        self.assertEqual(t_bar, last)
        self.assertEqual(set(targets), {"AAA/USD", "BBB/USD"})   # stock and unlisted pairs excluded
        self.assertEqual(targets["BBB/USD"], 0.0)                 # not a member this month
        self.assertEqual(targets["AAA/USD"], 0.2)                 # uptrending, regime on


if __name__ == "__main__":
    unittest.main()

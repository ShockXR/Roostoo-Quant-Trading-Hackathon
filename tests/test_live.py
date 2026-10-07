"""
Live order logic (execution/rebalance.py) and one pass of the live loop
(main.run_once) with the network replaced by fakes.

Run from the project root:

    .venv/bin/python -m unittest discover -s tests -v
"""
import pathlib
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from common import register_package  # noqa: E402

register_package()
from roostoo import config  # noqa: E402
from roostoo import main as live  # noqa: E402
from roostoo.execution import rebalance  # noqa: E402

INFO = {"BTC/USD": {"AmountPrecision": 5, "MiniOrder": 1}, "DOGE/USD": {"AmountPrecision": 0, "MiniOrder": 1},
        "ETH/USD": {"AmountPrecision": 4, "MiniOrder": 1}}
PRICES = {"BTC/USD": 80_000.0, "DOGE/USD": 0.2, "ETH/USD": 4_000.0}


class RebalanceTest(unittest.TestCase):
    def test_cap_gross(self):
        out = rebalance.cap_gross({"a": 0.2, "b": 0.2, "c": 0.2, "d": 0.2}, cap=0.7)
        self.assertAlmostEqual(sum(out.values()), 0.7)
        self.assertEqual(rebalance.cap_gross({"a": 0.2}, cap=0.7), {"a": 0.2})

    def test_parse_balance_layouts(self):
        for key in ("Wallet", "SpotWallet"):
            resp = {"Success": True, key: {"USD": {"Free": 100.0, "Lock": 0}, "BTC": {"Free": 0.5, "Lock": 0}}}
            self.assertEqual(rebalance.parse_balance(resp), {"USD": 100.0, "BTC": 0.5})

    def test_buys_from_cash_floored_to_precision(self):
        orders = rebalance.plan_orders({"BTC/USD": 0.2, "DOGE/USD": 0.2, "ETH/USD": 0.0},
                                       {"USD": 50_000.0}, PRICES, INFO, prev_targets=None)
        self.assertEqual([(o.pair, o.side) for o in orders], [("BTC/USD", "BUY"), ("DOGE/USD", "BUY")])
        self.assertEqual(orders[0].quantity, 0.125)
        self.assertEqual(orders[1].quantity, 50_000.0)
        self.assertTrue(all(o.quantity * PRICES[o.pair] <= 10_000 + 1e-9 for o in orders))

    def test_unchanged_target_is_not_traded_even_after_drift(self):
        holdings = {"USD": 40_000.0, "BTC": 0.2}          # BTC now 16k / 56k = 29% vs 20% target
        orders = rebalance.plan_orders({"BTC/USD": 0.2}, holdings, PRICES, INFO, prev_targets={"BTC/USD": 0.2})
        self.assertEqual(orders, [])

    def test_exit_sells_everything_and_sells_come_first(self):
        holdings = {"USD": 0.0, "BTC": 0.123456, "ETH": 0.0}
        orders = rebalance.plan_orders({"BTC/USD": 0.0, "ETH/USD": 0.2}, holdings, PRICES, INFO,
                                       prev_targets={"BTC/USD": 0.2, "ETH/USD": 0.0})
        self.assertEqual(orders[0].pair, "BTC/USD")
        self.assertEqual(orders[0].side, "SELL")
        self.assertEqual(orders[0].quantity, 0.12345)       # floored, not rounded up
        self.assertEqual(orders[1].side, "BUY")

    def test_buys_trimmed_to_available_cash(self):
        holdings = {"USD": 5_000.0, "BTC": 0.5}            # 45k equity, 40k of it in BTC
        orders = rebalance.plan_orders({"BTC/USD": 0.6, "ETH/USD": 0.2}, holdings, PRICES, INFO,
                                       prev_targets={"BTC/USD": 0.6, "ETH/USD": 0.0})
        self.assertEqual(len(orders), 1)
        self.assertLessEqual(orders[0].notional * (1 + config.TAKER_FEE), 5_000.0)

    def test_tiny_orders_skipped(self):
        orders = rebalance.plan_orders({"DOGE/USD": 0.2}, {"USD": 4.0}, PRICES, INFO, prev_targets=None)
        self.assertEqual(orders, [])                        # 0.80 USD < MiniOrder

    def test_fresh_start_reconciles_only_beyond_tolerance(self):
        holdings = {"USD": 40_100.0, "BTC": 0.125}          # 10k of 50.1k = 19.96% vs 20%
        self.assertEqual(rebalance.plan_orders({"BTC/USD": 0.2}, holdings, PRICES, INFO, None), [])
        holdings = {"USD": 50_000.0, "BTC": 0.0}
        self.assertEqual(len(rebalance.plan_orders({"BTC/USD": 0.2}, holdings, PRICES, INFO, None)), 1)


class FakeBroker(live.PaperBroker):
    def __init__(self, cash, fail=()):
        super().__init__(cash)
        self.fail, self.sent = set(fail), []

    def place(self, order, price):
        self.sent.append(order)
        return order.pair not in self.fail and super().place(order, price)


class RunOnceTest(unittest.TestCase):
    def run_once(self, broker, state, targets, bar="2026-10-01 10:00"):
        now = int(pd.Timestamp("2026-10-01 11:00:30", tz="UTC").timestamp() * 1000)
        with mock.patch.object(live, "latest_targets", return_value=(pd.Timestamp(bar, tz="UTC"), targets)), \
             mock.patch.object(live, "_prices", return_value=PRICES):
            return live.run_once(broker, state, INFO, now_ms=now)

    def test_trades_new_targets_and_remembers_them(self):
        b = FakeBroker(50_000.0)
        state = self.run_once(b, {}, {"BTC/USD": 0.2, "ETH/USD": 0.2, "DOGE/USD": 0.0})
        self.assertEqual(len(b.sent), 2)
        self.assertEqual(state["targets"]["BTC/USD"], 0.2)
        again = FakeBroker(0.0)
        again.holdings = b.balance()
        self.run_once(again, state, {"BTC/USD": 0.2, "ETH/USD": 0.2, "DOGE/USD": 0.0})
        self.assertEqual(again.sent, [])                    # nothing changed -> no orders

    def test_failed_order_is_retried_next_hour(self):
        b = FakeBroker(50_000.0, fail={"ETH/USD"})
        state = self.run_once(b, {}, {"BTC/USD": 0.2, "ETH/USD": 0.2, "DOGE/USD": 0.0})
        self.assertNotIn("ETH/USD", state["targets"])
        b.fail = set()
        b.sent = []
        self.run_once(b, state, {"BTC/USD": 0.2, "ETH/USD": 0.2, "DOGE/USD": 0.0})
        self.assertEqual([o.pair for o in b.sent], ["ETH/USD"])

    def test_stale_bar_does_not_trade(self):
        b = FakeBroker(50_000.0)
        self.run_once(b, {}, {"BTC/USD": 0.2, "ETH/USD": 0.0, "DOGE/USD": 0.0}, bar="2026-10-01 07:00")
        self.assertEqual(b.sent, [])

    def test_kill_switch_goes_flat_when_enabled(self):
        b = FakeBroker(0.0)
        b.holdings = {"USD": 30_000.0, "BTC": 0.125}         # equity 40k
        state = {"targets": {"BTC/USD": 0.2}, "peak_equity": 50_000.0}
        with mock.patch.object(config, "LIVE_KILL_SWITCH_DRAWDOWN", 0.12):
            out = self.run_once(b, state, {"BTC/USD": 0.2, "ETH/USD": 0.0, "DOGE/USD": 0.0})
        self.assertTrue(out["halted"])
        self.assertEqual([(o.pair, o.side) for o in b.sent], [("BTC/USD", "SELL")])


if __name__ == "__main__":
    unittest.main()

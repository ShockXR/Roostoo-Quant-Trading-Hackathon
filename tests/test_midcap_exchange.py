"""Mid-cap engine on the live broker against a stateful fake Roostoo exchange.

Goes through the real trader wrappers and rate limiter (only the HTTP layer is
replaced), so API budget, response parsing, rounding and lost-response recovery
are exercised without network access or credentials.
"""
from unittest.mock import patch
from roostoo_bot import config, midcap_live
from roostoo_bot.execution import trader
from roostoo_bot.execution.broker import LiveBroker, UnknownOrderState
from roostoo_bot.execution.portfolio import Quote
from .test_midcap_live import DAY, DAY0
from .test_regressions import OfflineCase

PAIRS = ["AAA/USD", "BBB/USD", "CCC/USD", "DDD/USD", "EEE/USD", "FFF/USD", "GGG/USD"]
PRICES = dict(zip(PAIRS, (10., 20., .5, 150., .08, 3., 42.)))
PRECISION = dict(zip(PAIRS, (2, 3, 0, 4, 0, 1, 3)))
FEE = .001


class FakeClock:
    def __init__(self):
        self.now = 1_000.

    def __call__(self):
        return self.now


class FakeExchange:
    """Market orders fill at the ask, instantly, with a taker fee; can drop one response."""
    def __init__(self, cash=100_000.):
        self.wallet = {"USD": cash}
        self.orders, self.calls, self.next_id = [], [], 1
        self.drop_response_for = None   # pair whose next order fills but returns no response
        self.reject_pair = None         # pair whose orders are rejected

    def request(self, method, path, payload, params, retries=3, signed=True):
        trader._LIMITER.reserve()
        self.calls.append(path)
        if path == "/v3/balance":
            return {"Success": True, "ErrMsg": "", "Wallet": {c: {"Free": v, "Lock": 0.} for c, v in self.wallet.items()}}
        if path == "/v3/pending_count":
            return {"Success": False, "ErrMsg": "no pending order under this account", "TotalPending": 0, "OrderPairs": {}}
        if path == "/v6/short_positions":
            return {"Success": True, "ErrMsg": "", "Positions": []}
        if path == "/v3/place_order":
            return self.place(payload)
        raise AssertionError(f"unexpected endpoint {path}")

    def place(self, payload):
        pair, quantity = payload["pair"], float(payload["quantity"])
        if pair == self.reject_pair:
            return {"Success": False, "ErrMsg": "order rejected"}
        coin = pair.split("/")[0]
        price = PRICES[pair] * 1.0001
        if payload["side"] == "BUY":
            cost = quantity * price * (1 + FEE)
            assert cost <= self.wallet["USD"] + 1e-9, "insufficient USD"
            self.wallet["USD"] -= cost
            self.wallet[coin] = self.wallet.get(coin, 0.) + quantity
        else:
            assert quantity <= self.wallet.get(coin, 0.) + 1e-9, "selling more than held"
            self.wallet["USD"] += quantity * price * (1 - FEE)
            self.wallet[coin] -= quantity
        assert abs(quantity * 10 ** PRECISION[pair] - round(quantity * 10 ** PRECISION[pair])) < 1e-6, "bad precision"
        self.orders.append((pair, payload["side"], quantity))
        if pair == self.drop_response_for:
            self.drop_response_for = None
            return None  # timeout: filled on the exchange, caller never hears
        order_id, self.next_id = self.next_id, self.next_id + 1
        return {"Success": True, "ErrMsg": "", "OrderDetail": {
            "Pair": pair, "OrderID": order_id, "Status": "FILLED", "Role": "TAKER", "Side": payload["side"],
            "Type": "MARKET", "Quantity": quantity, "FilledQuantity": quantity, "FilledAverPrice": price,
            "CommissionCoin": "USD", "CommissionChargeValue": quantity * price * FEE, "CommissionPercent": FEE}}


class MidcapExchangeTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.exchange, self.clock = FakeExchange(), FakeClock()
        self.targets = dict(zip(PAIRS, (.06, .05, .07, .04, .03, .05, .02)))
        self.weights = dict(self.targets)
        for patcher in (patch.object(trader, "_request", self.exchange.request),
                        patch.object(trader, "_LIMITER", trader.RateLimiter(clock=self.clock)),
                        patch.object(config, "LIVE_TRADING_ENABLED", True),
                        patch("roostoo_bot.main.DRY_RUN", False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        meta = {p: {"CanTrade": True, "AmountPrecision": PRECISION[p], "PricePrecision": 4, "MiniOrder": 1.} for p in PAIRS}
        self.engine = midcap_live.MidcapEngine(LiveBroker(), meta, lambda hour: dict(self.weights), pairs=PAIRS)
        self.cycle_calls = []

    def quotes(self, now):
        return {p: Quote(v, v * .9999, v * 1.0001, now - 1_000) for p, v in PRICES.items()}

    def cycle(self, minute, now_base=DAY0 + DAY + 3 * 60_000):
        """One minute of the live loop: a ticker call plus engine.step; returns fills, API calls used."""
        self.clock.now += 60.
        before = len(self.exchange.calls)
        trader._LIMITER.reserve()  # the ticker poll
        now = now_base + minute * 60_000
        try:
            events = self.engine.step(self.quotes(now), now)
        finally:
            self.cycle_calls.append(len(self.exchange.calls) - before + 1)
        return events

    def run_until_done(self, limit=10, **kwargs):
        for minute in range(limit):
            self.cycle(minute, **kwargs)
            if self.engine.rebalance.done:
                return minute + 1
        self.fail("rebalance did not finish")

    def equity(self):
        return self.exchange.wallet["USD"] + sum(
            q * PRICES[c + "/USD"] for c, q in self.exchange.wallet.items() if c != "USD")

    def test_full_rebalance_completes_within_api_budget(self):
        minutes = self.run_until_done()
        self.assertLessEqual(minutes, 4)
        self.assertEqual(sorted(p for p, _, _ in self.exchange.orders), sorted(PAIRS))
        self.assertTrue(all(side == "BUY" for _, side, _ in self.exchange.orders))
        self.assertLessEqual(max(self.cycle_calls), config.MAX_API_CALLS_PER_MINUTE)
        equity = self.equity()
        for pair, target in self.targets.items():
            held = self.exchange.wallet[pair.split("/")[0]] * PRICES[pair] / equity
            self.assertAlmostEqual(held, target, delta=.004, msg=pair)
        self.assertFalse(self.engine.state.uncertain)

    def test_no_orders_after_the_rebalance_is_done(self):
        self.run_until_done()
        orders = len(self.exchange.orders)
        for minute in range(10, 15):
            self.assertEqual(self.cycle(minute), [])
        self.assertEqual(len(self.exchange.orders), orders)
        self.assertLessEqual(max(self.cycle_calls[-5:]), 3)  # idle cycles: ticker + balance + pending (long only: no short read)

    def test_lost_order_response_is_reconciled_without_a_duplicate_buy(self):
        self.exchange.drop_response_for = "CCC/USD"
        with self.assertRaises(UnknownOrderState):
            for minute in range(3):
                self.cycle(minute)
        self.assertTrue(self.engine.state.uncertain)
        bought = sum(1 for p, _, _ in self.exchange.orders if p == "CCC/USD")
        self.assertEqual(bought, 1)  # it did fill on the exchange
        self.run_until_done(limit=10, now_base=DAY0 + DAY + 10 * 60_000)
        self.assertFalse(self.engine.state.uncertain)
        self.assertEqual(sum(1 for p, _, _ in self.exchange.orders if p == "CCC/USD"), 1)
        self.assertEqual(sorted({p for p, _, _ in self.exchange.orders}), sorted(PAIRS))

    def test_rejected_pair_is_retried_then_skipped_and_the_rest_still_trade(self):
        self.exchange.reject_pair = "DDD/USD"
        minutes = self.run_until_done()
        self.assertLessEqual(minutes, 6)
        self.assertNotIn("DDD", self.exchange.wallet)
        self.assertEqual({p for p, _, _ in self.exchange.orders}, set(PAIRS) - {"DDD/USD"})
        self.assertEqual(self.engine.rebalance.rejections, {"DDD/USD": config.MIDCAP_MAX_REJECTIONS_PER_PAIR})
        self.assertFalse(self.engine.state.uncertain)
        self.assertLessEqual(max(self.cycle_calls), config.MAX_API_CALLS_PER_MINUTE)

    def test_drawdown_cut_sells_proportionally_on_the_exchange(self):
        self.run_until_done()
        before = {c: q for c, q in self.exchange.wallet.items() if c != "USD"}
        for pair in PRICES:
            PRICES[pair] *= .92  # about -2.5% equity on 31% gross: below the first threshold
        self.addCleanup(lambda: [PRICES.__setitem__(p, v) for p, v in zip(PAIRS, (10., 20., .5, 150., .08, 3., 42.))])
        self.assertEqual(self.cycle(20), [])
        for pair in PRICES:
            PRICES[pair] *= .75  # now past 6% drawdown
        sold = []
        for minute in range(21, 26):
            sold += self.cycle(minute)
            if self.engine.rebalance.done:
                break
        self.assertTrue(sold and all(e["action"] == "SELL" for e in sold))
        self.assertEqual(self.engine.rebalance.scale, .3)
        equity = self.equity()
        for coin, quantity in before.items():
            pair = coin + "/USD"
            excess = (quantity - self.exchange.wallet[coin] * 1.) * PRICES[pair]
            if excess == 0:  # only allowed when the excess over target is inside the 1% no-trade band
                target_value = self.targets[pair] * .3 * equity
                self.assertLess(quantity * PRICES[pair] - target_value, config.MIDCAP_REBALANCE_BAND * equity, pair)
            self.assertGreater(self.exchange.wallet[coin], 0)
        self.assertLess(sum(self.exchange.wallet[c] * PRICES[c + "/USD"] for c in before) / equity, .2)

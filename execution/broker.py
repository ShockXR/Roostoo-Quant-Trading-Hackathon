"""Live adapter: reconcile account state; refuse uncertain acknowledgements."""
from . import trader
from .portfolio import Snapshot, Short
from .. import config


class UnknownOrderState(RuntimeError):
    pass


class OrderRejected(RuntimeError):
    """The exchange answered Success=false: nothing was placed or filled."""


class LiveBroker:
    def __init__(self):
        if not config.LIVE_TRADING_ENABLED:
            raise RuntimeError("Live trading is disabled in config; use PaperBroker")

    def can_submit(self):
        # Pre-order balance/pending/short reads, one mutation, and the three
        # post-order reconciliation reads must all fit in the current budget.
        return trader._LIMITER.available() >= 7

    def snapshot(self, quotes):
        balance = trader.get_balance()
        pending = trader.get_pending_count()
        shorts = trader.get_short_positions()
        if not balance or not balance.get("Success") or not pending or pending.get("TotalPending") != 0:
            raise UnknownOrderState("balance or pending orders require reconciliation")
        if not shorts or not shorts.get("Success") or not isinstance(shorts.get("Positions"), list):
            raise UnknownOrderState("short position state unavailable")
        # The bot that traded on main read SpotWallet/Frozen; the API docs show Wallet/Lock. Accept both.
        wallet = balance.get("SpotWallet") or balance.get("Wallet")
        if not isinstance(wallet, dict) or "USD" not in wallet:
            raise UnknownOrderState("balance response has no wallet")
        if any(float(v.get("Lock", 0) or 0) != 0 or float(v.get("Frozen", 0) or 0) != 0 for v in wallet.values()):
            raise UnknownOrderState("locked balances require reconciliation")
        cash = float(wallet["USD"]["Free"])
        longs = {}
        for coin, amounts in wallet.items():
            if coin == "USD":
                continue
            quantity = float(amounts.get("Free", 0))
            pair = coin + "/USD"
            if quantity < 0:
                # FAQ Q24: sells can leave tiny negative rounding residue; ignore it below the venue minimum.
                if pair in quotes and -quantity * quotes[pair].price < 1.:
                    continue
                raise UnknownOrderState("negative balance requires reconciliation")
            if quantity:
                if pair not in quotes:
                    raise UnknownOrderState("held asset missing from quoted universe")
                longs[pair] = quantity
        positions = {v["Pair"]: Short(float(v["ShortQty"]), float(v["EntryPrice"]), float(v["Collateral"])) for v in shorts["Positions"]}
        snapshot = Snapshot(cash, longs, positions)
        snapshot.quantities()
        snapshot.equity(quotes)
        self._snapshot = snapshot
        return snapshot

    def submit(self, order, quotes, metadata):
        if not config.LIVE_TRADING_ENABLED:
            raise RuntimeError("live trading disabled")
        if order.action in {"BUY", "SELL"}:
            response = trader.place_order(order.pair, order.action, order.amount)
            if response and response.get("Success"):
                detail = response.get("OrderDetail", {})
                filled = float(detail.get("FilledQuantity", 0))
                if detail.get("Status") == "FILLED" and abs(filled - order.amount) <= 1e-9 * max(1., order.amount):
                    return {"pair": order.pair, "action": order.action, "quantity": order.amount,
                            "price": float(detail["FilledAverPrice"]), "fee": float(detail.get("CommissionChargeValue", 0)),
                            "order_id": detail["OrderID"], "status": "FILLED", "paper": False}
        elif order.action == "SHORT_OPEN":
            if not config.ALLOW_SHORTS:
                raise RuntimeError("short entries disabled")
            previous = getattr(self, "_snapshot", Snapshot(0.)).shorts.get(order.pair)
            response = trader.short_open(order.pair, order.amount)
            if response and response.get("Success") and response.get("Status") == "OPEN":
                total = float(response["ShortQty"])
                added = total - (previous.quantity if previous else 0.)
                if added <= 0:
                    raise UnknownOrderState("short acknowledgement has no positive added quantity")
                entry = (total * float(response["EntryPrice"]) - (previous.quantity * previous.entry if previous else 0.)) / added
                return {"pair": order.pair, "action": order.action, "quantity": added,
                        "price": entry, "fee": float(response.get("OpenFee", 0)),
                        "order_id": response["ID"], "status": "FILLED", "paper": False}
        elif order.action == "SHORT_CLOSE":
            response = trader.short_close(order.pair, close_qty=order.amount)
            if response and response.get("Success") and float(response.get("ClosedQty", 0)) >= order.amount:
                return {"pair": order.pair, "action": order.action, "quantity": float(response["ClosedQty"]),
                        "price": float(response["ClosePrice"]), "fee": float(response.get("CloseFee", 0)),
                        "status": "FILLED", "paper": False}
        else:
            raise ValueError("unknown action")
        if isinstance(response, dict) and response.get("Success") is False:
            # A definitive refusal (bad size, pair not tradable, no short permission...).
            raise OrderRejected(str(response.get("ErrMsg", "order rejected")))
        # Partial fills, missing fields and timeouts end the cycle. Never update a
        # local ledger by assuming the requested fill.
        raise UnknownOrderState("order not fully confirmed; reconcile before further orders")

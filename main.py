"""Paper-first runner with shared signals, stateful risk and reconciled deltas."""
import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import time
import numpy as np
from . import config
from .data.collector import poll_once
from .data.store import load_all
from .data.validation import validate_recent_history
from .execution.portfolio import PaperBroker, Quote, ExecutionState, Snapshot, Short, plan_orders
from .execution.broker import LiveBroker, UnknownOrderState
from .execution.trader import get_exchange_info
from .features.build_features import build_features_for_pair, build_price_panel
from .model.train import load_bundle
from .strategy import model_target, rule_target

log = logging.getLogger("main")
DRY_RUN = True


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, allow_nan=False) + "\n")
    temporary.replace(path)


class TradingEngine:
    def __init__(self, broker, metadata, models=None, strategy="model", state=None, state_path=None, event_path=None,
                 equity_path=None):
        if isinstance(broker, LiveBroker) and (DRY_RUN or not config.LIVE_TRADING_ENABLED):
            raise RuntimeError("live trading disabled")
        self.broker, self.metadata = broker, metadata
        self.models, self.strategy = models or {}, strategy
        self.state = state or ExecutionState()
        self.state_path, self.event_path, self.equity_path = state_path, event_path, equity_path

    def persist(self):
        if self.state_path:
            value = {"execution": asdict(self.state), "mode": "paper" if isinstance(self.broker, PaperBroker) else "live"}
            if isinstance(self.broker, PaperBroker):
                value["portfolio"] = asdict(self.broker.state)
            atomic_json(self.state_path, value)

    def log_equity(self, now_ms, equity):
        # One mark per cycle; scripts/live_score.py turns these into the daily composite score.
        if self.equity_path:
            path = Path(self.equity_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "paper" if isinstance(self.broker, PaperBroker) else "live"
            with path.open("a") as out:
                out.write(json.dumps({"timestamp": now_ms, "equity": equity, "mode": mode}, allow_nan=False) + "\n")

    def step(self, raw, now_ms):
        if self.state.uncertain:
            raise UnknownOrderState("previous order state is uncertain; reconcile before resuming")
        quotes = {}
        for pair, frame in raw.items():
            if frame.empty:
                continue
            row = frame.sort_values("timestamp").iloc[-1]
            age = now_ms - int(row.timestamp)
            if age < 0 or age > config.MAX_QUOTE_AGE_MS:
                raise ValueError("stale/future quote; stop entire cycle")
            quotes[pair] = Quote(float(row.last_price), float(row.max_bid), float(row.min_ask), int(row.timestamp))
        if not quotes:
            return []
        snapshot = self.broker.snapshot(quotes)
        equity = snapshot.equity(quotes)
        self.state.mark(equity)
        self.log_equity(now_ms, equity)
        panel = build_price_panel(raw)
        lookback = max(max(config.MOMENTUM_HORIZONS) + 1, 2 * config.MA_WINDOWS["slow"],
                       config.MEAN_REV_WINDOW + 1, 2 * config.REGIME_VOL_WINDOW + 1, config.REGIME_TREND_WINDOW + 1)
        targets = {}
        for pair in quotes:
            if self.state.halted:
                targets[pair] = 0.
                continue
            try:
                history = validate_recent_history(raw[pair], now_ms, lookback)
                frame = build_features_for_pair(pair, {**raw, pair: history}, panel)
                latest = frame.iloc[[-1]]
                if self.strategy == "model":
                    bundle = self.models.get(pair)
                    if bundle is None or bundle.pair not in {None, pair}:
                        raise ValueError("missing/mismatched trained model")
                    if int(latest.index[-1]) <= bundle.trained_until:
                        raise ValueError("prediction timestamp overlaps model training period")
                    targets[pair] = model_target(bundle, latest, quotes[pair].spread, allow_shorts=config.ALLOW_SHORTS)
                else:
                    if latest.empty or not np.isfinite(latest.to_numpy(dtype=float)).all():
                        raise ValueError("missing rule features")
                    if config.COMPONENTS.get("cost_filter", False):
                        raise ValueError("rule mode requires cost_filter=False; no calibrated rule payoff model")
                    targets[pair] = rule_target(latest.iloc[0], self.strategy, config.ALLOW_SHORTS)
            except (ValueError, KeyError, IndexError) as exc:
                # No inference from incomplete data. Hold existing exposure,
                # and block additions everywhere if a held pair cannot be scored.
                log.warning("%s: %s", pair, exc)
                if pair in snapshot.quantities():
                    self.persist()
                    return []
                targets[pair] = 0.
        orders = plan_orders(targets, snapshot, quotes, self.metadata)
        events = []
        for order in orders:
            if not self.state.slots(now_ms):
                break
            if isinstance(self.broker, LiveBroker) and not self.broker.can_submit():
                break
            q = quotes[order.pair]
            if self.state.last_order_quote.get(order.pair) == q.timestamp:
                continue
            current = self.broker.snapshot(quotes)
            equity = current.equity(quotes)
            self.state.mark(equity)
            if self.state.halted and order.action in {"BUY", "SHORT_OPEN"}:
                continue
            if order.action in {"BUY", "SHORT_OPEN"}:
                held = current.quantities()
                gross = sum(abs(v) * quotes[p].price for p, v in held.items()) / equity
                over_asset_cap = any(abs(v) * quotes[p].price / equity > config.MAX_POSITION_PER_ASSET for p, v in held.items())
                if gross > config.MAX_PORTFOLIO_EXPOSURE or over_asset_cap:
                    continue  # reductions must restore budgets before adding new risk
            # Check spendable cash after each confirmed reduction/fill.
            required = (order.amount * q.ask * (1 + config.TAKER_FEE + config.SLIPPAGE_ESTIMATE)
                        if order.action == "BUY" else order.amount * (1 + config.SHORT_FEE)
                        if order.action == "SHORT_OPEN" else 0.)
            if required > current.cash:
                continue
            self.state.trades.append(now_ms)  # reserve quota even for rejected/ambiguous attempts
            self.state.last_order_quote[order.pair] = q.timestamp
            self.state.uncertain = True
            self.persist()  # survive a crash immediately after order submission
            try:
                event = self.broker.submit(order, quotes, self.metadata)
                if not isinstance(event, dict) or event.get("status") != "FILLED":
                    raise UnknownOrderState("broker returned no confirmed fill")
                after = self.broker.snapshot(quotes)
                self.state.mark(after.equity(quotes))
            except Exception:
                self.persist()
                raise
            self.state.uncertain = False
            event.update(timestamp=now_ms, target_fraction=targets.get(order.pair, 0.), equity=after.equity(quotes))
            events.append(event)
            if self.event_path:
                path = Path(self.event_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as out:
                    out.write(json.dumps(event, allow_nan=False) + "\n")
            self.persist()
        self.persist()
        return events


def restore_engine(broker, metadata, models, state_path, event_path, strategy="model", equity_path=None):
    state = ExecutionState()
    path = Path(state_path)
    if path.exists():
        saved = json.loads(path.read_text())
        expected_mode = "paper" if isinstance(broker, PaperBroker) else "live"
        if saved["mode"] != expected_mode:
            raise ValueError("paper/live state files must be separate")
        state = ExecutionState(**saved["execution"])
        if isinstance(broker, PaperBroker):
            portfolio = saved["portfolio"]
            broker.state = Snapshot(portfolio["cash"], portfolio["longs"], {p: Short(**s) for p, s in portfolio["shorts"].items()})
    return TradingEngine(broker, metadata, models, strategy, state, state_path, event_path, equity_path)


def run_once(engine):
    if poll_once() is None:
        log.warning("ticker poll failed; skipping trading cycle")
        return []
    return engine.step(load_all(config.UNIVERSE), int(time.time() * 1000))


def run_forever(engine, interval=None):
    interval = config.POLL_INTERVAL_SECONDS if interval is None else interval
    while True:
        started = time.monotonic()
        try:
            run_once(engine)
        except Exception:
            log.exception("cycle failed; no assumed fills")
        time.sleep(max(0., interval - (time.monotonic() - started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default=config.MODEL_DIR)
    parser.add_argument("--state", default=config.PAPER_STATE_PATH)
    parser.add_argument("--events", default=config.EVENT_LOG_PATH)
    parser.add_argument("--equity", default=config.EQUITY_LOG_PATH)
    parser.add_argument("--strategy", choices=["model", "momentum", "mean_reversion", "cross_sectional"], default="model")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    models = {}
    if args.strategy == "model":
        for pair in config.UNIVERSE:
            path = Path(args.model_dir) / (pair.replace("/", "_") + ".joblib")
            if path.exists():
                models[pair] = load_bundle(path)
        if not models:
            parser.error("no trained model artifacts; train with roostoo_bot.model.train first")
    elif config.COMPONENTS["cost_filter"]:
        parser.error("rule-only research requires explicitly disabling cost_filter; no estimated payoff gate available")
    info = get_exchange_info()
    if not info or not info.get("IsRunning"):
        parser.error("exchange metadata unavailable/not running")
    broker = PaperBroker() if DRY_RUN else LiveBroker()
    engine = restore_engine(broker, info["TradePairs"], models, args.state, args.events, args.strategy, args.equity)
    log.info("Starting %s mode with %s model artifacts", "paper" if DRY_RUN else "live", len(models))
    run_forever(engine)


if __name__ == "__main__":
    main()

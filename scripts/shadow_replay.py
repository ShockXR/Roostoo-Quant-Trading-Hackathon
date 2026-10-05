"""Replay history through the live mid-cap engine and compare it with the backtest.

Feeds one-minute Binance closes to MidcapEngine (paper broker) as if they were
live Roostoo quotes, with target weights from the same hourly panel the
backtest uses. Differences from the backtest are real engine behavior:
fills one-to-a-few minutes after the hour, the paper broker's own fee and
slippage, rounding to Roostoo lot sizes, cash/exposure checks and the
engine's drawdown handling.

    python -m roostoo_bot.scripts.shadow_replay --start 2026-09-01 --end 2026-10-05
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .. import config
from ..backtest.portfolio import load_hourly_panel, simulate
from ..execution.portfolio import PaperBroker, Quote
from ..midcap_live import MidcapEngine
from ..scoring import score_equity
from ..strategies.midcap import build_weights, live_spec
from .midcap_research import DATA_DIR, SYMBOLS

PAIRS = [s.replace("USDT", "/USD") for s in SYMBOLS]
HOUR_MS = 3_600_000


def load_minutes(data_dir, start, end):
    frames = {}
    for symbol in SYMBOLS:
        frame = pd.read_parquet(Path(data_dir) / f"{symbol}_1m.parquet", columns=["open_time", "close"])
        stamps = (pd.DatetimeIndex(frame.open_time).tz_convert("UTC") + pd.Timedelta(minutes=1)).as_unit("ns").asi8 // 1_000_000
        frames[symbol.replace("USDT", "/USD")] = pd.Series(frame.close.to_numpy(float), index=stamps)
    table = pd.DataFrame(frames).sort_index()
    lo, hi = (int(pd.Timestamp(v, tz="UTC").value // 1_000_000) for v in (start, end))
    return table[(table.index >= lo) & (table.index < hi)]


def replay(start, end, data_dir=DATA_DIR, spread=0.0):
    spec = live_spec(config)
    panel = load_hourly_panel(SYMBOLS, data_dir)
    panel.signal.columns = panel.execution.columns = panel.volume.columns = PAIRS
    weights = build_weights(panel, spec)
    by_hour = {int(h.value // 1_000_000): weights.loc[h] for h in weights.index[weights.notna().any(axis=1)]}

    def source(hour_ms):
        if hour_ms not in by_hour:
            raise ValueError("no weights for hour")
        return {p: float(by_hour[hour_ms][p]) for p in PAIRS}

    minutes = load_minutes(data_dir, start, end)
    meta = {p: {"CanTrade": True, "AmountPrecision": 4, "PricePrecision": 4, "MiniOrder": 1.} for p in PAIRS}
    engine = MidcapEngine(PaperBroker(config.INITIAL_CAPITAL), meta, source, pairs=PAIRS)
    # Start flat at the first rebalance at/after `start`, like a bot started for the window.
    first = next(h for h in sorted(by_hour) if h >= int(minutes.index[0]))
    engine.rebalance.hour_ms = first - 24 * HOUR_MS
    equity, fills = {}, 0
    for stamp, row in minutes.iterrows():
        if stamp < first:
            continue
        quotes = {p: Quote(v, v * (1 - spread / 2), v * (1 + spread / 2), int(stamp)) for p, v in row.items()}
        fills += len(engine.step(quotes, int(stamp)))
        equity[int(stamp)] = engine.broker.state.equity(quotes)
    return pd.Series(equity), fills, engine, panel, weights, first


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2026-09-01")
    parser.add_argument("--end", default="2026-10-05")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--spread", type=float, default=0.0004, help="bid/ask spread fraction used for replay quotes")
    args = parser.parse_args(argv)

    equity, fills, engine, panel, weights, first = replay(args.start, args.end, args.data_dir, args.spread)
    window_start = pd.Timestamp(first, unit="ms", tz="UTC")
    back = simulate(panel, weights, window_start, args.end, drawdown_schedule=config.MIDCAP_DRAWDOWN_SCHEDULE)
    back_equity = pd.Series(back.equity.to_numpy(), index=back.index.as_unit("ns").asi8 // 1_000_000)
    live_score = score_equity(equity, config.INITIAL_CAPITAL, start=window_start)
    back_score = score_equity(back_equity, config.INITIAL_CAPITAL, start=window_start)
    daily = pd.DataFrame({"engine": live_score.daily_returns, "backtest": back_score.daily_returns})
    gap = daily.engine - daily.backtest
    print(f"window {window_start} -> {args.end}, spread {args.spread:.4%}")
    print(f"engine fills: {fills}   backtest fills: {int(back.fills.sum())}")
    print(f"{'':10}{'return':>10}{'max DD':>10}{'composite':>11}{'sortino':>9}{'sharpe':>8}{'calmar':>9}")
    for name, rep in (("engine", live_score), ("backtest", back_score)):
        f = lambda v: "undef" if v is None else f"{v:.2f}"
        print(f"{name:10}{rep.total_return:10.2%}{rep.max_drawdown:10.2%}{f(rep.composite):>11}{f(rep.sortino):>9}"
              f"{f(rep.sharpe):>8}{f(rep.calmar):>9}")
    print(f"daily return gap engine-backtest: mean {gap.mean():+.4%}, worst abs {gap.abs().max():.4%}, "
          f"corr {daily.corr().iloc[0, 1]:.3f}")
    print(f"rebalance state: done={engine.rebalance.done} scale={engine.rebalance.scale}")
    return {"engine": live_score, "backtest": back_score}


if __name__ == "__main__":
    main()

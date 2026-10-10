"""Backtest the three-sleeve book on Binance hourly closes and compare it with its parts.

Run where Binance is reachable (the EC2 instance), before switching the live bot:

  python -m roostoo_bot.scripts.sleeves_backtest --start 2026-04-01 --out logs/sleeves_backtest.md

Downloads 1h klines for every sleeve pair (cached under binance_data/hourly/), starting 104 days
before --start so the 100-day BTC regime is warm. Pairs without full history are dropped and listed.
Scores are the competition composite on daily equity (scoring.py), for the whole window, fresh
14-day blocks, and the live competition window.

Not modelled here: the small-cap stop and lockout, the spread guard and the daily activity trade
(all live-engine only), and Roostoo fills (fills are at the hourly close with a flat cost per side).
"""
import argparse
from pathlib import Path
import time
from types import SimpleNamespace
import pandas as pd
from .. import config
from ..backtest.portfolio import HourlyPanel, block_scores, evaluate, simulate
from ..data.binance_live import binance_symbol, fetch_history
from ..strategies import midcap, sleeves

HOUR_MS = 3_600_000
CACHE = Path("binance_data/hourly")


def download(pair, start, end):
    """Hourly closes and quote volumes for [start, end], cached per symbol and range."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{binance_symbol(pair)}_{start:%Y%m%d}_{end:%Y%m%d%H}.csv"
    if path.exists():
        frame = pd.read_csv(path, index_col=0)
        frame.index = pd.to_datetime(frame.index, utc=True)
        return frame
    end_ms = int(end.value // 1_000_000)
    hours = int((end - start) / pd.Timedelta(hours=1))
    rows = fetch_history(binance_symbol(pair), end_ms, hours)
    frame = pd.DataFrame({"close": [float(r[4]) for r in rows], "volume": [float(r[7]) for r in rows]},
                         index=pd.to_datetime([int(r[0]) + HOUR_MS for r in rows], unit="ms", utc=True))
    frame.to_csv(path)
    time.sleep(.2)
    return frame


def joint_panel(pairs, start, end):
    closes, volumes, dropped = {}, {}, []
    expected = pd.date_range(start + pd.Timedelta(hours=1), end, freq="h")
    for pair in pairs:
        try:
            frame = download(pair, start, end)
        except ValueError as exc:
            dropped.append(f"{pair} (no data: {exc})")
            continue
        if len(frame) < len(expected) * .999 or frame.index[0] > expected[0] + pd.Timedelta(hours=2):
            dropped.append(f"{pair} (history starts {frame.index[0]:%Y-%m-%d})")
            continue
        closes[pair] = frame["close"].reindex(expected).ffill()
        volumes[pair] = frame["volume"].reindex(expected).fillna(0.)
    signal = pd.DataFrame(closes).dropna()
    volume = pd.DataFrame(volumes).loc[signal.index]
    return HourlyPanel(signal, signal.copy(), volume), dropped


def settings(**overrides):
    values = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    values.update(overrides)
    return SimpleNamespace(**values)


def variants(panel):
    pairs = {s: [p for p in config.SLEEVE_PAIRS[s] if p in panel.signal.columns] for s in sleeves.SLEEVES}
    only = lambda keep: {s: (pairs[s] if s == keep else []) for s in sleeves.SLEEVES}
    no_gate = {**config.SLEEVE_PARAMS, "mid": {**config.SLEEVE_PARAMS["mid"], "regime_off_scale": 1.}}
    out = {
        "sleeves (recovery sizing, live default)": sleeves.backtest_weights(panel, config, pairs),
        "sleeves (push sizing)": sleeves.backtest_weights(panel, config, pairs, scale=config.PUSH_MULTIPLIER),
        "large sleeve alone": sleeves.backtest_weights(panel, config, only("large")),
        "mid sleeve alone": sleeves.backtest_weights(panel, config, only("mid")),
        "small sleeve alone": sleeves.backtest_weights(panel, config, only("small")),
        "mid sleeve, no BTC gate": sleeves.backtest_weights(panel, settings(SLEEVE_PARAMS=no_gate), only("mid")),
    }
    mid = pairs["mid"]
    sub = HourlyPanel(panel.signal[mid], panel.execution[mid], panel.volume[mid])
    # Other columns are NaN on rebalance rows, which the simulator reads as 0.
    out["previous blend_v10 on the mid universe (1.5% vol)"] = midcap.build_weights(
        sub, midcap.live_spec(config)).reindex(columns=panel.signal.columns)
    return out


def fmt(stats):
    def f(key, spec):
        value = stats.get(key)
        return "n/a" if value is None or value != value else format(value, spec)  # scorer gives None when undefined
    return " | ".join([f("total_return", "+.2%"), f("max_drawdown", ".2%"), f("composite", ".2f"), f("sortino", ".2f"),
                       f("sharpe", ".2f"), f("calmar", ".2f"), f("avg_gross", ".0%"), f("active_day_fraction", ".0%")])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2026-04-01", help="first scored day (UTC)")
    parser.add_argument("--end", default=None, help="last hour (UTC); default: the latest closed hour")
    parser.add_argument("--competition-start", default="2026-10-04")
    parser.add_argument("--cost", type=float, default=0.0015, help="cost per side (fee + spread + slippage)")
    parser.add_argument("--out", default="logs/sleeves_backtest.md")
    args = parser.parse_args()
    start = pd.Timestamp(args.start, tz="UTC")
    end = (pd.Timestamp(args.end, tz="UTC") if args.end else pd.Timestamp.now(tz="UTC")).floor("h")
    warm = start - pd.Timedelta(days=104)
    pairs = [p for s in sleeves.SLEEVES for p in config.SLEEVE_PAIRS[s]]
    panel, dropped = joint_panel(pairs, warm, end)
    comp = pd.Timestamp(args.competition_start, tz="UTC")
    header = "| Variant | Return | Max DD | Composite | Sortino | Sharpe | Calmar | Avg gross | Active days |\n|---|---|---|---|---|---|---|---|---|"
    lines = [f"# Three-sleeve backtest {start:%Y-%m-%d} to {end:%Y-%m-%d %H:%M} UTC", "",
             f"Cost {args.cost:.2%} per side; drawdown schedule {config.MIDCAP_DRAWDOWN_SCHEDULE}; "
             f"{panel.signal.shape[1]} pairs.", "", "Dropped: " + (", ".join(dropped) or "none"), ""]
    for name, weights in variants(panel).items():
        kwargs = dict(cost_per_side=args.cost, drawdown_schedule=config.MIDCAP_DRAWDOWN_SCHEDULE)
        lines += [f"## {name}", "", header]
        full = simulate(panel, weights, start, end, **kwargs)
        lines.append(f"| whole window | {fmt(evaluate(full, start=start))} |")
        stress = simulate(panel, weights, start, end, **{**kwargs, "cost_per_side": 2 * args.cost})
        lines.append(f"| whole window, 2x cost | {fmt(evaluate(stress, start=start))} |")
        if end - comp > pd.Timedelta(days=1):
            live = simulate(panel, weights, comp, end, **kwargs)
            lines.append(f"| since {comp:%b %d} | {fmt(evaluate(live, start=comp))} |")
        blocks = block_scores(panel, weights, start, end, **kwargs)
        if len(blocks):
            lines += ["", f"14-day blocks: median return {blocks.total_return.median():+.2%}, positive "
                      f"{(blocks.total_return > 0).sum()} of {len(blocks)}, worst drawdown {blocks.max_drawdown.min():.2%}, "
                      f"median composite {blocks.composite.median():.2f}"]
        lines.append("")
    text = "\n".join(lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()

"""Running competition score and active-day count from the bot's own logs.

Reads logs/equity.jsonl (one equity mark per cycle) and logs/events.jsonl
(one line per confirmed fill). Compare the composite with the Roostoo app
leaderboard; a gap points to a convention mismatch (docs/ORGANIZER_QUESTIONS.md).

    python -m roostoo_bot.scripts.live_score --mode live
"""
import argparse
import json
from pathlib import Path
import pandas as pd
from .. import config
from ..scoring import ScoreConventions, score_equity, _scoring_day


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number} is not valid JSON") from exc
    return rows


def equity_series(rows, mode):
    rows = [r for r in rows if r.get("mode") == mode]
    if not rows:
        raise ValueError(f"no {mode} equity marks")
    frame = pd.DataFrame(rows).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    return pd.Series(frame.equity.to_numpy(dtype=float), index=frame.timestamp.to_numpy(dtype="int64"))


def fills_per_day(events, mode, conventions, start_ms, end_ms):
    paper = mode == "paper"
    stamps = [int(e["timestamp"]) for e in events if bool(e.get("paper")) == paper and e.get("status") == "FILLED"]
    days = _scoring_day(pd.date_range(pd.Timestamp(start_ms, unit="ms", tz="UTC"),
                                      pd.Timestamp(end_ms, unit="ms", tz="UTC"), freq="D"), conventions)
    counts = pd.Series(0, index=days.unique())
    if stamps:
        counted = pd.Series(1, index=_scoring_day(pd.to_datetime(stamps, unit="ms", utc=True), conventions)).groupby(level=0).sum()
        counts = counts.add(counted, fill_value=0).astype(int)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--equity", default=config.EQUITY_LOG_PATH)
    parser.add_argument("--events", default=config.EVENT_LOG_PATH)
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument("--initial-capital", type=float, default=config.INITIAL_CAPITAL)
    parser.add_argument("--start-ms", type=int, default=config.COMPETITION_START_MS,
                        help="first scoring day; earlier days without marks count as zero-return days")
    parser.add_argument("--min-fills", type=int, default=1,
                        help="fills needed for a day to count as active (organizer threshold unknown)")
    parser.add_argument("--day-offset-hours", type=float, default=0.)
    parser.add_argument("--periods-per-year", type=int, default=365)
    parser.add_argument("--risk-free-annual", type=float, default=0.)
    parser.add_argument("--annual-return", choices=["compound", "simple"], default="compound")
    parser.add_argument("--drawdown-basis", choices=["daily", "intraday"], default="daily")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)

    conventions = ScoreConventions(args.day_offset_hours, args.periods_per_year, args.risk_free_annual,
                                   0., args.annual_return, args.drawdown_basis)
    equity = equity_series(read_jsonl(args.equity), args.mode)
    report = score_equity(equity, args.initial_capital, conventions, start=args.start_ms)
    fills = fills_per_day(read_jsonl(args.events), args.mode, conventions, args.start_ms, int(equity.index[-1]))
    active = int((fills >= args.min_fills).sum())

    if args.json:
        print(json.dumps({**report.to_dict(), "fills_per_day": {str(k.date()): int(v) for k, v in fills.items()},
                          "active_days": active}, indent=2))
        return report

    fmt = lambda v: "undefined" if v is None else f"{v:.4f}"
    print(f"mode={args.mode}  days={report.n_days}  last mark={pd.Timestamp(int(equity.index[-1]), unit='ms', tz='UTC')}")
    print(f"equity {equity.iloc[-1]:,.2f}  total return {report.total_return:+.4%}  max drawdown {report.max_drawdown:.4%}")
    print(f"composite {fmt(report.composite)} = 0.4*Sortino {fmt(report.sortino)} + 0.3*Sharpe {fmt(report.sharpe)} "
          f"+ 0.3*Calmar {fmt(report.calmar)}")
    for name, reason in report.undefined.items():
        print(f"  {name} undefined: {reason}")
    print(f"active days (>= {args.min_fills} fill): {active} of {len(fills)}; need 8 by Oct 17")
    for day, count in fills.items():
        print(f"  {day.date()}  fills={count}")
    return report


if __name__ == "__main__":
    main()

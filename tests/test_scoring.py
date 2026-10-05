"""Composite score against hand-derived answers."""
import contextlib
import io
import json
import math
from pathlib import Path
import unittest
import pandas as pd
from roostoo_bot.scoring import ScoreConventions, daily_closes, score_equity
from roostoo_bot.scripts import live_score
from .test_regressions import OfflineCase

DAY = 86_400_000
T0 = 1_791_072_000_000  # 2026-10-04 00:00 UTC


def curve(*points):
    """points: (ms offset from T0, equity)."""
    return pd.Series([v for _, v in points], index=[T0 + t for t, _ in points])


class ScoringTests(unittest.TestCase):
    def setUp(self):
        # Daily returns +2%, -1%, +1% from 100.
        self.closes = [102., 102. * .99, 102. * .99 * 1.01]
        self.equity = curve(*[(i * DAY + 3_600_000, v) for i, v in enumerate(self.closes)])

    def test_ratios_match_closed_form(self):
        report = score_equity(self.equity, 100.)
        root = math.sqrt(365)
        # mean 2/300; sample sd sqrt(21)/300; RMS downside 1/(100 sqrt 3)
        self.assertAlmostEqual(report.sharpe, 2 / math.sqrt(21) * root, places=9)
        self.assertAlmostEqual(report.sortino, 2 / math.sqrt(3) * root, places=9)
        self.assertAlmostEqual(report.max_drawdown, -.01, places=12)
        total = 1.02 * .99 * 1.01 - 1
        self.assertAlmostEqual(report.total_return, total, places=12)
        self.assertAlmostEqual(report.annual_return, (1 + total) ** (365 / 3) - 1, places=6)
        self.assertAlmostEqual(report.calmar, report.annual_return / .01, places=6)
        expected = .4 * report.sortino + .3 * report.sharpe + .3 * report.calmar
        self.assertAlmostEqual(report.composite, expected, places=9)
        self.assertEqual(report.n_days, 3)

    def test_simple_annual_return_option(self):
        report = score_equity(self.equity, 100., ScoreConventions(annual_return="simple"))
        self.assertAlmostEqual(report.annual_return, 2 / 300 * 365, places=12)

    def test_last_mark_of_day_is_the_close_and_gap_days_carry(self):
        equity = curve((1_000, 100.), (DAY - 1, 101.), (2 * DAY + 5, 103.))
        closes = daily_closes(equity)
        self.assertEqual(list(closes), [101., 101., 103.])
        report = score_equity(equity, 100.)
        self.assertAlmostEqual(report.daily_returns[1], 0.)

    def test_day_offset_moves_late_utc_marks_to_next_day(self):
        equity = curve((23 * 3_600_000, 101.), (DAY + 1_000, 102.))
        self.assertEqual(len(daily_closes(equity)), 2)
        self.assertEqual(list(daily_closes(equity, ScoreConventions(day_offset_hours=8))), [102.])

    def test_start_adds_zero_return_days_before_first_mark(self):
        equity = curve((2 * DAY + 1_000, 101.))
        report = score_equity(equity, 100., start=T0)
        self.assertEqual(report.daily_returns, [0., 0., report.daily_returns[2]])
        self.assertAlmostEqual(report.daily_returns[2], .01)

    def test_intraday_drawdown_basis_sees_intraday_dip(self):
        equity = curve((1_000, 90.), (DAY - 1, 101.), (DAY + 1_000, 102.))
        self.assertEqual(score_equity(equity, 100.).max_drawdown, 0.)
        intraday = score_equity(equity, 100., ScoreConventions(drawdown_basis="intraday"))
        self.assertAlmostEqual(intraday.max_drawdown, -.1)

    def test_undefined_ratios_are_reported_not_zeroed(self):
        rising = curve((1_000, 101.), (DAY + 1_000, 102.))
        report = score_equity(rising, 100.)
        self.assertIsNone(report.sortino)
        self.assertIsNone(report.calmar)
        self.assertIsNone(report.composite)
        self.assertIn("sortino", report.undefined)
        self.assertIn("calmar", report.undefined)
        single = score_equity(curve((1_000, 99.)), 100.)
        self.assertIsNone(single.sharpe)

    def test_risk_free_rate_lowers_sharpe(self):
        base = score_equity(self.equity, 100.)
        with_rf = score_equity(self.equity, 100., ScoreConventions(risk_free_annual=.05))
        self.assertLess(with_rf.sharpe, base.sharpe)

    def test_datetime_index_matches_millisecond_index(self):
        as_dates = self.equity.copy()
        as_dates.index = pd.to_datetime(as_dates.index, unit="ms")
        self.assertAlmostEqual(score_equity(as_dates, 100.).composite, score_equity(self.equity, 100.).composite)

    def test_invalid_inputs_raise(self):
        for bad in (pd.Series(dtype=float), curve((0, 0.)), curve((0, float("nan"))), curve((0, 1.), (0, 2.))):
            with self.assertRaises(ValueError):
                score_equity(bad, 100.)
        with self.assertRaises(ValueError):
            score_equity(self.equity, 0.)
        with self.assertRaises(ValueError):
            ScoreConventions(annual_return="log")


class LiveScoreTests(OfflineCase):
    def write(self, name, rows):
        path = Path(self.temp.name) / name
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return str(path)

    def test_engine_logs_one_equity_mark_per_cycle(self):
        path = Path(self.temp.name) / "equity.jsonl"
        engine = self.runner(equity_path=str(path))
        engine.step(self.raw, self.now)
        engine.step(self.raw, self.now + 60_000)
        marks = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([m["timestamp"] for m in marks], [self.now, self.now + 60_000])
        self.assertTrue(all(m["mode"] == "paper" and m["equity"] > 0 for m in marks))

    def test_report_matches_scorer_and_counts_active_days(self):
        marks = [{"timestamp": T0 + i * DAY + 3_600_000, "equity": v, "mode": "live"}
                 for i, v in enumerate([102_000., 100_980., 101_989.8])]
        marks.append({"timestamp": T0 + 1_000, "equity": 1., "mode": "paper"})  # other mode ignored
        fills = [{"timestamp": T0 + 5_000, "status": "FILLED", "paper": False},
                 {"timestamp": T0 + 2 * DAY + 5_000, "status": "FILLED", "paper": False},
                 {"timestamp": T0 + DAY + 5_000, "status": "FILLED", "paper": True}]
        args = ["--equity", self.write("equity.jsonl", marks), "--events", self.write("events.jsonl", fills),
                "--initial-capital", "100000", "--start-ms", str(T0), "--json"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            report = live_score.main(args)
        expected = score_equity(pd.Series([102_000., 100_980., 101_989.8],
                                          index=[T0 + i * DAY + 3_600_000 for i in range(3)]), 100_000.)
        self.assertAlmostEqual(report.composite, expected.composite)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["active_days"], 2)
        self.assertEqual(list(printed["fills_per_day"].values()), [1, 0, 1])

    def test_missing_marks_for_mode_is_an_error(self):
        args = ["--equity", self.write("equity.jsonl", [{"timestamp": T0, "equity": 1., "mode": "paper"}]),
                "--events", self.write("events.jsonl", [])]
        with self.assertRaises(ValueError):
            live_score.main(args)


class BacktestDailyScoreTests(OfflineCase):
    def test_summarize_reports_daily_competition_score(self):
        from roostoo_bot.backtest import engine
        days = 6
        index = pd.Index([T0 + i * 3_600_000 for i in range(days * 24)], dtype="int64")
        steps = [.004 if (i // 24) % 3 else -.003 for i in range(len(index))]
        price = pd.Series(100 * pd.Series(steps).add(1).cumprod().to_numpy(), index=index)
        sim = engine.simulate(price, pd.Series(.2, index=index))
        stats = engine.summarize(sim)
        expected = score_equity(sim.equity * 100_000., 100_000.)
        self.assertEqual(stats["n_days"], days)
        self.assertAlmostEqual(stats["daily_composite"], expected.composite)
        bars = engine.summarize(sim.reset_index(drop=True))
        self.assertIsNone(bars["daily_composite"])


if __name__ == "__main__":
    unittest.main()

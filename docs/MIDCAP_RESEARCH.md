# Mid-cap strategy research (phase 3, 2026-10-05)

Backtests of candidate strategies for the 17-coin mid-cap sleeve (BTC and ETH excluded), scored like the competition. Results are historical simulations with assumed costs; they are not evidence of live performance.

## Setup

- **Data:** Binance spot 1m klines, 2026-04-01 to 2026-10-04, resampled to hourly. Roostoo prices stream from Binance (FAQ Q17).
- **Timing:** signals use the price at the top of each hour; trades fill at the price 5 minutes later. Equity is marked hourly. Code: `backtest/portfolio.py`, `strategies/midcap.py`.
- **Costs:** 0.15% of traded notional per side (0.10% fee, about 0.02% half spread, 0.03% slippage), plus a 2x stress run at 0.30%. Exit costs are charged at the end, since the competition liquidates.
- **Sizing:** inverse-volatility weights within the selection, scaled so predicted daily portfolio volatility is at most the target (1%, 1.5% or 2%), capped at 20% per coin and 100% gross. Long-only unless noted.
- **Periods:** research 2026-04-08 to 08-01 (selection), validation 08-01 to 09-01, holdout 09-01 to 10-05 (run once, for the pre-chosen strategy).
- **Score:** composite `0.4 Sortino + 0.3 Sharpe + 0.3 Calmar` on daily closes (`scoring.py` defaults: UTC days, 365-day annualization, compounded Calmar return), on the full window and on fresh, flat-start 14-day blocks (the competition length).
- **Leakage check:** for every strategy, weights at each hour are identical with or without later data (`tests/test_midcap.py`).

Reproduce with `python -m roostoo_bot.scripts.midcap_research --validate 8`. Holdout runs are in `--holdout <names>`.

## What the grid showed (research period, 1x cost)

The first grid had 28 configurations: cross-sectional momentum (24h, 72h, 168h lookbacks; top 3 or 5; rebalance every 8h or 24h), market-adjusted reversal, time-series trend, volume-confirmed breakout, and equal-weight benchmarks. A further 19 variants (volatility targets, crash gate, long/short, blends, one benchmark) followed, 47 in total.

- **Momentum with daily rebalancing worked across all three lookbacks.** Best median 14-day composite: `mom_24_3_24` 14.5, `mom_24_5_24` 12.6, `mom_168_3_24` 5.3 (positive in 7 of 8 blocks, the most consistent).
- **Reversal lost in every configuration** (median composite -3.7 to -7.2, at most 1 of 8 blocks positive). Mid-caps trended rather than mean-reverted over hours to days.
- **Rebalancing every 8 hours mostly lost to costs** (turnover roughly doubled).
- **Breakout and trend** were weak to modest; benchmarks were near zero or negative (equal-weight hold: -5.7% with a -27% drawdown).
- **At 2x cost**, daily momentum stayed positive; `mom_168_*` held up best because it trades least.
- **Volatility target barely changes the ratios** (12.7, 12.6, 12.3 for 1%, 1.5%, 2% on `mom_24_5_24`) but scales drawdown roughly in proportion. Sortino and Sharpe do not depend on position size, so a lower target costs little score and buys a smaller drawdown.
- **The crash gate did not help** on these periods, and **long/short momentum** was worse than long-only in validation. Shorts are also unconfirmed on our keys. Neither is in the chosen strategy.

## Chosen strategy: `blend_v10`

Average of two long-only momentum signals, rebalanced daily at 00:00 UTC, targeting 1% predicted daily volatility:

- 24h lookback, top 5 coins by return, and 168h lookback, top 3; only coins with a positive return over the lookback qualify, otherwise that share stays in cash.
- Inverse-volatility weights, at most 20% per coin.

Chosen before the holdout was run, for consistency across lookbacks, the lowest drawdowns, and daily activity, in line with the team's preference for small, stable bets.

| Period | Market (equal-weight hold) | blend_v10 return | Max drawdown | 14-day blocks positive | Median 14-day composite | 2x cost return | Fewest active days in a 14-day block |
|---|---|---|---|---|---|---|---|
| Research (Apr 8 - Aug 1) | -5.7% | +14.3% | -4.8% | 6 of 8 | 10.2 | +9.2% | 13 |
| Validation (Aug) | +21.3% | +8.1% | -3.6% | 1 of 2 | 98.7 | +6.3% | 14 |
| Holdout (Sep 1 - Oct 4) | +48.3% | +6.7% | -5.1% | 2 of 2 | 33.7 | +5.5% | 14 |

## Read this before relying on it

1. **It protects in falling markets and lags badly in rallies.** In research the market fell and the blend made +14%; in August and September the market rose 21% and 48% and the blend made 7-8%, because at a 1% volatility target it held only about 21-35% of capital in coins on average. Whether that is the right trade depends on the next two weeks, which nobody knows.
2. **The 14-day composite is dominated by Calmar under compounded annualization.** A 14-day return compounded to a year is huge, so Calmar values of hundreds or thousands swamp Sortino and Sharpe. Under this convention, a rally makes high exposure score enormously (equal-weight hold: 4858 median in the holdout). If the organizers use simple annualization, the ranking could change. This is organizer question 6 and matters more than any parameter here.
3. **Selection bias.** 47 configurations were tried, and the best research numbers are inflated by that. The holdout is now used up; any further tuning has no clean test left except live trading.
4. **Costs are assumptions.** Fees of 0.10% per side are unconfirmed (organizer question 13); if real fees are lower, every momentum variant improves.
5. **Universe chosen with hindsight.** The 17 coins were picked by today's volume, which flatters the backtest slightly.

## Options for the team

- **Keep `blend_v10`** for the fast lane: consistent, low drawdown, trades every day.
- **Raise the volatility target to 1.5-2%** to hold more in rallies. Research and validation suggest ratios stay similar and drawdowns grow roughly in proportion (worst 14-day block about -7% to -8% at 1.5-2%, versus about -5% at 1%).
- Any other change would be chosen after seeing the holdout, so it should be tested in paper trading first.

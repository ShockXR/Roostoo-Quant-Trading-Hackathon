# Model Research Report — branch `model-research`

Base branch `Samyak` (untouched). All numbers come from runs saved under
`experiments/long/<id>/` (`metrics.csv`, `equity.csv`, `trades_*.csv`,
`meta.json`); the running log with every hypothesis is
`experiments/long/RESEARCH_LOG.md`.

## Summary

The existing model (Model 0, buy a >1.5σ one-day dip and hold 24h) was
chosen on three bull-market months (Jul–Sep 2026). On 4¾ years of history
(2022-01 → 2026-09) it **loses 55%** (daily Sharpe −0.47, max drawdown −66%):
it buys every falling knife (LUNA, the June 2022 contagion, FTX), and no
nearby parameter setting is positive on the 2022–23 training data.

The winner is **Model 3: cross-sectional momentum with a market regime
filter**. Once a day it holds the three large caps with the highest positive
28-day return at 20% each, only while BTC is above its 100-day average;
otherwise it holds cash. Over the same history it makes **+312%** (CAGR 35%,
daily Sharpe 1.08, Sortino 1.76, max drawdown −38%). It is the only model
that held up in the 2025–26 out-of-sample bear market (−8.8% vs −43% for
buy & hold). It trades rarely (329 trades in 4¾ years), so doubling costs or
filling a day late leaves it working.

Two risks dominate. **Its returns are concentrated**: November 2024 alone
was +124%, and without that month the full-period Sharpe falls to 0.58
(+83%). And **it is a trend follower with long flat or slightly losing
stretches**: only 16 of 57 months were positive, and in a 14-day
competition window it is more often flat or slightly down than up, with
occasional large gains.

## 1. Git branch

`model-research`, created from `Samyak` at `d68ec11`. The uncommitted
mid-cap work in the `Samyak` working tree was carried over and committed
first, as its own commit (`922dd8c`), so the research starts from the code
that produced it. Nothing was merged.

## 2. Original baseline (Model 0)

`strategy.py::_reversal` / `config.REVERSAL_*`, unchanged. Reproduced on
this branch: `experiments/R00_baseline_repro` is identical to `F01_final`
(Jul–Sep 2026: +8.39%, daily Sharpe 4.31, max DD −2.36%, 54 trades). The
hourly re-implementation used for the long history
(`strategies/m0_reversal.py`) produces the 1m rule's targets exactly
(unit test plus a check on real data: same targets, equity within $1e-10).

## 3–5. Models

| # | Module | Idea | Status |
|---|---|---|---|
| M0 | `strategies/m0_reversal.py` | Existing dip-reversal | Baseline — rejected |
| M1 | `strategies/m1_reversal_trend.py` | M0 entries only when the asset is above its 50-day SMA (avoid falling knives) | Rejected |
| M2 | `strategies/m2_xs_momentum.py` | Daily cross-sectional dual momentum: top 3 by 28-day return, positive only, 20% each | Initial winner, superseded |
| M3 | `strategies/m3_momentum_regime.py` | M2 + hold nothing unless BTC > 100-day SMA | **WINNER** |
| M4 | `strategies/m4_momentum_longshort.py` | M3 + short the 3 weakest when BTC < 100-day SMA | Rejected |
| — | M2 options `vol_target`, list `lookback_days` | Vol targeting; lookback ensemble | Rejected |

You left Models 1 and 2 as placeholders and asked me to choose them. M1
tests the most likely failure of M0 (bear markets); M2 tests its biggest
structural limit (14% average exposure, idle in rallies).

## 6. Backtesting methodology

- `backtest/simulator.py` (existing, unchanged): holds units, fills the
  target decided at bar t's close at bar t+1's open, fee and slippage
  charged per side, trades only when a target changes, 20% per asset, gross
  exposure ≤ 70% (scaled pro rata), long only, cash earns nothing.
- Long history runs on **hourly bars** built from 1m candles
  (`strategies/features.py`). Every model changes targets only at hourly
  closes, so fills are the same as on 1m bars. **Drawdowns are re-marked on
  minute closes** (`scripts/run_long.py::minute_equity`); hourly marks alone
  understated M0's Jul–Sep 2026 drawdown (−1.61% vs −2.36%).
- $100k fresh capital per period; signals computed once over the whole
  history (all causal) and sliced.
- Tools: `scripts/run_long.py` (one model, all metrics), `sweep_long.py`
  (grids), `walkforward_long.py`, `regime_long.py`, `summarize_long.py`.

## 7–8. Universe and period

`LARGE_CAPS_2022` (`scripts/common.py`): the ten largest non-stablecoin
assets on 2022-01-01 — BTC, ETH, BNB, SOL, ADA, XRP, **LUNA**, DOT, AVAX,
DOGE — fixed at the start of the sample, so it contains a collapse (LUNA,
delisted 2022-05-13 and force-closed at its last bar; the later `LUNAUSDT`
is a different token and is not spliced on) and assets that lost rank.
Later large caps (TRX, TON, SUI, …) are excluded on purpose. Limitations:
membership is not re-ranked over time, and ranks 9–12 on the snapshot date
were close.

Binance spot 1m candles, 2022-01-01 → 2026-09-30 (2.5M candles per asset).
The only gap, an 80-minute Binance outage on 2023-03-24, is filled flat
(zero-volume minutes at the last close). All assets are listed for the whole
window except LUNA.

## 9–10. Costs and slippage

0.10% fee + 0.015% slippage per side for every model (Roostoo taker fee;
the existing backtest's slippage). Stress tests below.

## 11. Validation methodology

Split fixed in `scripts/common.LONG_PERIODS` before any long run:

| Period | Dates | Market | Use |
|---|---|---|---|
| Train | 2022-01 → 2023-12 | bear, then recovery | design and parameter choice |
| Validation | 2024-01 → 2024-12 | bull | confirm |
| Test | 2025-01 → 2026-06 | bear (buy & hold −43%) | frozen candidates only, read once |
| Holdout | 2026-07 → 2026-09 | bull | original window; M0 was tuned on it |

Rules: a change must improve train and hold on validation; the centre of a
positive plateau is preferred to the best cell. Disclosure: runs over the
"full" period contain the test and holdout, so both were read at the same
time; no design decision was made after that (M4 was rejected on
train/validation only).

## 12. Out-of-sample results (test, 2025-01 → 2026-06)

| Model | Return | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| Buy & hold (EW, 70%) | −43.4% | −0.71 | −51.1% | 9 |
| M0 | −49.9% | −1.58 | −53.6% | 680 |
| M1 | −25.3% | −1.85 | −25.9% | 182 |
| M2 | −41.1% | −1.10 | −44.5% | 174 |
| **M3** | **−8.8%** | **−0.16** | **−20.7%** | 108 |

Every model lost money in this bear market; M3 lost the least, by far.
It lost −13.7% in Jan–Feb 2025 (the market rolled over while BTC was still
above its average) and then sat in cash through the Oct 2025 – Mar 2026 decline.

Holdout (Jul–Sep 2026, bull, M0 tuned on it): buy & hold +36.9% (Sharpe
3.51), **M0 +7.2% (2.57, DD −3.7%)**, M3 +8.3% (1.25, DD −15.9%), M2 +4.5%.
M0 has the better risk-adjusted result in the window it was tuned on.

Walk-forward (`W01_m3`): re-selecting lookback × SMA every 6 months by
trailing Sharpe (2023-01 → 2026-06) gave Sharpe 1.16 vs 1.32 for the
frozen M3. The chosen cell moved around because the region is flat;
re-tuning adds nothing, so the frozen parameters stay.

## 13. Regime analysis (`experiments/long/regimes.csv`, full window)

| Regime (basket, ex post) | Buy & hold | M0 | M2 | M3 |
|---|---|---|---|---|
| 2022 | −59% | −11% | −32% | −13% |
| 2023 | +72% | +7% | +144% | +124% |
| 2024 | +78% | −12% | +108% | +114% |
| 2025 | −23% | −36% | −22% | −6% |
| 2026 (to Sep) | −10% | −17% | −21% | +5% |
| Strong uptrend days (442) | +360% | +11% | +470% | +426% |
| Strong downtrend days (487) | −80% | −37% | −56% | −7% |
| Neither (715) | −1% | −40% | −11% | −16% |
| High-vol tercile | −41% | −36% | −22% | +17% |

M3 makes its money in sustained uptrends, mostly avoids downtrends, and
bleeds slowly in directionless markets (whipsaw). It depends on trending
regimes recurring — they have every year so far, but that is a dependency.

## 14. Robustness of M3 (full window unless stated)

| Test | Result |
|---|---|
| Fee 0.15% / 0.20% per side | Sharpe 1.03 / 0.99 |
| Slippage 0.05% / 0.10% | 1.05 / 1.00 |
| Fee 0.20% + slippage 0.10% | 0.92 (+223%) |
| Maker fee 0.05% | 1.12 |
| Fill 1h / 6h / 24h late | 1.06 / 1.01 / 0.90 |
| Decision hour 0/5/11/17/23 UTC | 0.98 / 0.97 / 0.97 / 0.87 / **1.08** — the a-priori 23:00 is the best of five; expect ~0.95 |
| 25-cell grid, lookback 14–56 × SMA 50–200 | train 0.67–1.46, validation 1.48–2.74, full 0.92–1.28 — all positive; test −0.73…+0.47 |
| Drop any one asset (9 runs; BTC is the filter) | full 0.93–1.22, train and validation positive in all |
| Top-5 assets only | 0.93, max DD −23% |
| Without Nov 2024 | 0.58 (+83%) |
| 12% drawdown kill switch (14-day windows) | fires in 7.6% of windows; mean 14-day return +1.48% → +0.90%, worst 5% −7.0% → −7.3% → **left off** |

## 15. Final comparison (full window 2022-01 → 2026-09)

| Model | Return | CAGR | Sharpe | Sortino | Max DD | Calmar | Trades | Turnover | OOS result (test) | Robustness | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Buy & hold | −14.2% | −3.2% | 0.18 | 0.25 | −60.9% | −0.05 | 10 | 1× | −43.4% | — | Benchmark |
| M0 reversal | −55.3% | −15.6% | −0.47 | −0.62 | −66.0% | −0.24 | 1,811 | 684× | −49.9% | 15/16 grid cells negative on train | Rejected |
| M1 reversal + trend | −17.0% | −3.8% | −0.23 | −0.30 | −39.1% | −0.10 | 558 | 226× | −25.3% | Train-only edge | Rejected |
| M2 dual momentum | +113.6% | +17.3% | 0.60 | 0.92 | −58.8% | 0.29 | 543 | 216× | −41.1% | 19/20 train, 20/20 validation cells positive | Superseded |
| **M3 momentum + regime** | **+311.6%** | **+34.7%** | **1.08** | **1.76** | **−37.8%** | **0.92** | **329** | **139×** | **−8.8%** | 25/25 train and validation cells positive; cost/delay/universe stable | **WINNER** |
| M4 long/short | train/validation only | | | | | | | | not run | Worse validation | Rejected |

Validation (2024): M3 +118.6%, Sharpe 1.89, max DD −29.9%; train
(2022–23): +94.6%, 1.26, −22.4%.

## 16–17. Winning model and why

**Model 3** (`config.PRODUCTION_MODEL = "m3_momentum_regime"`).

- **Genuine predictive value:** positive alpha against the equal-weight
  basket over the full window (t = +2.45), train t = +2.04.
- **Generalizes to the bear test period** better than any alternative,
  because the regime filter moves it to cash in downtrends.
- **No parameter knife-edge:** every one of 25 neighbouring settings is
  positive on train and validation, and the chosen cell is mid-pack.
- **Cheap to run:** about 70 trades a year, so costs and latency barely matter.
- **Simple:** about 15 lines of signal logic, three parameters fixed a
  priori (28-day lookback was the initial M2 default; 100-day SMA is the
  centre of the region that worked).

Where it does badly: directionless markets, market tops (it exits only
after BTC breaks its 100-day average), and crash days while invested.
Main risks: return concentration in a few trend bursts; long flat periods;
60% of capital in three correlated alts while risk-on (a −13% single-day
loss happened); the regime filter depends on BTC.

## 18. Models rejected and why

- **M0:** no edge outside 2026; the losses are falling knives. The 3-month
  result was a single-regime artefact.
- **M1:** the trend filter cuts M0's bear losses (train +15.5%) but
  validation is negative (−7.9%) and test −25%; little exposure, no stable
  edge.
- **M2:** good, but −59% max drawdown and −41% on test; strictly dominated
  by M3.
- **Vol targeting (G03):** lowers drawdown only in proportion to exposure;
  Sharpe unchanged → a size dial, not an edge.
- **Lookback ensemble (G05):** worse train, better validation — inconsistent.
- **M4 short leg (G06):** better train (helped by shorting LUNA into zero),
  worse validation in every cell; squeezes in 2024; borrow cost unknown.
- Not tried, on purpose: a faster exit at tops. The only evidence for it
  is the test period's Jan 2025 losses, and building on that would mean
  fitting to the test.

## 19. Known limitations

- 4¾ years contain only a few bull/bear cycles: the effective sample is small.
- The universe is fixed in 2022 and not re-ranked; nine of ten assets
  survived, which may still flatter the results.
- Binance prices are assumed equal to Roostoo's (checked live: BTC within $2).
  Roostoo fills, liquidity and minimum-order behaviour are not modelled
  beyond precision and the $1 minimum.
- No funding or borrow costs (M3 is long-only, so this only affects M4).
- For a **14-day competition**, M3 is often flat (cash), and when invested
  it is volatile: positive in 28% of all 14-day windows, worst 5% of
  windows −6.9%. Its expected value comes from a minority of trending windows.
- The decision hour (23:00 UTC) matters more than other parameters; the
  realistic Sharpe expectation is about 0.95, not 1.08.

## 20. Final production configuration

```
config.PRODUCTION_MODEL   = "m3_momentum_regime"   # strategies/m3_momentum_regime.py
lookback_days = 28, top_n = 3, weight = 0.20, market_sma_days = 100 (BTC), decision 23:00 UTC bar
config.PRODUCTION_SYMBOLS = BTC ETH BNB SOL ADA XRP DOT AVAX DOGE (/USD on Roostoo)
MAX_POSITION_PER_ASSET = 0.20, MAX_PORTFOLIO_EXPOSURE = 0.70 (unchanged)
LIVE_KILL_SWITCH_DRAWDOWN = None
```

Run: `.venv/bin/python scripts/run_live.py --once` (one paper decision),
`scripts/run_live.py` (hourly loop, paper), `ROOSTOO_LIVE=1 …` (real
orders; needs `ROOSTOO_API_KEY` / `ROOSTOO_API_SECRET`). The systemd unit
runs the launcher. Live and backtest targets were checked to agree for
2026-09-30 23:00 (Binance 1h klines equal the backtest's hourly bars exactly).
The ML pipeline and Model 0's 1m research path (`strategy.py`,
`scripts/run_backtest.py`) remain for comparison; neither is wired to the
live loop.

## 21. Recommended next steps

1. Run `scripts/run_live.py` in paper mode for a few days on the server
   before setting `ROOSTOO_LIVE=1`; check fills and balances against the plan.
2. Run `scripts/test_min_order.py` with real keys to confirm whether
   MiniOrder is $ notional (assumed) or units.
3. Treat `/v3/balance`'s exact layout as unverified (the parser accepts
   `Wallet` and `SpotWallet`); check it with keys.
4. If a longer history becomes available (2018–2021 Binance data exists
   for most of the universe), re-run the frozen M3 on it as a second
   out-of-sample test before changing anything.
5. Before trying to reduce M3's whipsaw, gather out-of-sample evidence
   that does not come from the test period.

Reproduce: see `experiments/long/RESEARCH_LOG.md` §Reproduce.

# Long-history model research log (branch `model-research`)

Written as the work happened. Numbers come from `experiments/long/<id>/`
(`metrics.csv`, `equity.csv`, `trades_<period>.csv`, `meta.json`).

## 1. Repository understanding (before any change)

- **Model 0 (production rule)** — `strategy.py::_reversal`, parameters in
  `config.REVERSAL_*`: per asset, at each hh:59 close, if the 1-day return is
  below −1.5 × (1-day std of 1m returns × √1440), hold 20% of equity for 24h
  after the last signal. Long only, no stop, no take-profit.
- **Original ML bot** (`strategy.py::_baseline`, `model/`, `features/`):
  walk-forward random forest on a 10-minute horizon; lost money in every
  period (costs > edge). It was already replaced by Model 0 in the backtest
  path; it is not a candidate here.
- **Backtester** — `backtest/simulator.py`: holds units, fills the target
  decided on bar t's close at bar t+1's open, 0.10% fee + 0.015% slippage per
  side, trades only when the target changes, gross exposure capped at 70%,
  20% per asset (`config`). Metrics in `backtest/metrics.py`.
- **Runner** — `scripts/run_backtest.py`: Jul/Aug/Sep 2026 =
  train/validation/test on five large caps, 1m bars.
- **Live loop** — `main.py` still runs the ML model in DRY_RUN; Model 0 has
  never been wired to Roostoo. `execution/` (signed Roostoo REST),
  `data/collector.py` (ticker poller → parquet).
- **Data** — 3 months (Jul–Sep 2026) of Binance 1m candles, all assets
  +40–70%: one bull regime, 54 trades in total. Model 0's parameters were
  chosen on Jul/Aug 2026.

Baseline reproduced on this branch: `experiments/R00_baseline_repro` is
identical to `F01_final` (+8.39%, daily Sharpe 4.31, max DD −2.36%, 54
trades over Jul–Sep 2026).

## 2. Why a longer history

Three bull-market months cannot tell a dip-buying edge from "being long in
a bull market", and nothing has been tested in a bear market, the regime
in which buying dips is known to fail. Binance Data Vision has 1m candles
for every universe member back to 2022, covering the 2022 bear market
(LUNA in May, FTX in November), the 2023 recovery, the 2024 bull market
and 2025–26.

## 3. Universe (survivorship)

`LARGE_CAPS_2022` (`scripts/common.py`): the ten largest non-stablecoin
assets by market cap on 2022-01-01 — BTC, ETH, BNB, SOL, ADA, XRP, LUNA,
DOT, AVAX, DOGE. It is fixed at the start of the sample, so it includes
assets that later collapsed (LUNA, −99.99% and delisted in May 2022) or
lost rank (DOT, AVAX, ADA). Later large caps (TRX, TON, SUI, ...) are
deliberately excluded: choosing them would use hindsight.

Limitations: membership is not re-ranked over time (no market-cap history
in the repo). The snapshot is taken from the public CoinMarketCap ranking;
ranks 9–12 were close on that date. The universe does not change while
a backtest runs.

LUNA: `LUNAUSDT` on Binance after May 2022 is a different token (LUNA 2.0),
so only the history up to the end of May 2022 was downloaded; the asset is
force-closed at its last bar and is untradable afterwards.

## 4. Protocol (fixed before any model was run on the long history)

| Period | Dates | Use |
|---|---|---|
| train | 2022-01-01 → 2023-12-31 | choose parameters (bear 2022, recovery 2023) |
| validation | 2024-01-01 → 2024-12-31 | confirm; choose between finalists |
| test | 2025-01-01 → 2026-06-30 | read only for frozen candidates |
| holdout | 2026-07-01 → 2026-09-30 | read once at the very end (Model 0 was tuned on Jul/Aug 2026) |

- Hourly bars from 1m candles. Every model changes targets only at hourly
  closes, and fills at the next 1m open, as the 1m backtest does
  (equivalence tested: identical targets, equity within 1e-10 of the 1m
  simulation on Jul–Sep 2026). Drawdowns are marked on minute closes.
- Same costs for every model: 0.10% fee + 0.015% slippage per side; same
  limits: 20% per asset, 70% gross, long only; $100k per period.
- Benchmark: equal-weight buy & hold of the same universe, 70% gross
  (`buy_hold`).
- Decision metric: daily Sharpe and Sortino, then max drawdown / Calmar,
  then the distribution of 14-day windows (the competition runs 14 days
  and scores 0.4·Sortino + 0.3·Sharpe + 0.3·Calmar), then trade count,
  costs and per-asset consistency. Total return never decides alone.
- Parameter grids are evaluated on train; the centre of a positive plateau
  is preferred over the best cell; validation must agree.

## 5. Experiments

Portfolio daily Sharpe / total return / max DD (minute-marked) unless stated.

| ID | Hypothesis | Change | Train | Validation | Decision |
|---|---|---|---|---|---|
| B00 | — | Equal-weight buy & hold, 70% gross | −0.17 / −30% / −61% | 1.33 / +68% / −39% | Benchmark |
| L00 | — | Model 0, frozen | 0.08 / −5% / −35% | −0.47 / −12% / −30% | Baseline. Losses = LUNA (May 22), June 22, FTX (Nov 22) dips |
| G00 | Is M0's failure a parameter choice? | lookback 12–48h × k 1.5–3 | 15/16 cells negative | mixed around 0 | **Reversal family rejected** — no plateau |
| L01 / G01 | Dip buying fails in downtrends | M0 + close > N-day SMA, N 20–200, k 1.5/2 | all k=1.5 cells positive (0.28–0.56) | k=1.5 all negative | **Rejected** — not confirmed; k=2 cells tiny |
| L02 / G02 | Cross-sectional + absolute momentum | top-n by L-day return, L 7–90, n 2–5 | 19/20 positive (default 28/3: 0.83 / +67% / −37%) | 20/20 positive (1.78 / +112% / −32%) | **Initial winner** |
| G03 | Vol-managed momentum cuts crashes | per-asset weight × min(1, target/σ30) | Sharpe ±0.1 vs none | ±0.2 | **Rejected** — de-levers only |
| G04 | Momentum whipsaws in bear rallies | hold nothing unless BTC > N-day SMA, N 20–200 | N 50–200 better in 12/12 cells | better in 10/12 | **Accepted, N=100 → Model 3** (centre); N=20 hurts |
| G05 | Diversify lookback / timing luck | average of 2–5 lookback portfolios | 0.92–1.06 (< 1.26) | 1.69–2.27 (> 1.89) | **Rejected** — inconsistent |
| G06 | Momentum works short in risk-off | M3 + short 1–3 weakest when BTC < SMA100 | 1.01–1.64 (LUNA short helps) | 1.35–2.27, below M3 at 28d | **Rejected** |
| L03 | — | Model 3 frozen; test read | 1.26 / +95% / −22% | 1.89 / +119% / −30% | **Test −0.16 / −8.8% / −21%** (B&H −43%) → **WINNER** |
| R03 | Survives worse assumptions? | costs, delay, decision hour, drop-one universe, top-5 | | | Passed (full Sharpe 0.87–1.22) |
| G07 | Post-freeze neighbourhood | lookback 14–56 × SMA 50–200, all periods | 25/25 positive | 25/25 positive | Plateau; test ≈ 0 everywhere |
| W01 | Does re-tuning help? | 6-month walk-forward re-selection 2023–26 | | | 1.16 vs frozen 1.32 → keep frozen |
| H_* | Holdout Jul–Sep 2026 | frozen models | | | M0 2.57 (tuned here), M3 1.25, B&H 3.51 |

## Reproduce

```bash
# data (≈1.2 GB of archives; parquet in binance_data/long/processed/parquet)
.venv/bin/python binance_downloader.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT ADAUSDT XRPUSDT \
    DOTUSDT AVAXUSDT DOGEUSDT --start 2022-01-01 --end 2026-09-30 --output-dir binance_data/long
.venv/bin/python binance_downloader.py --symbols LUNAUSDT --start 2022-01-01 --end 2022-05-31 \
    --output-dir binance_data/long
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_long.py --id L03_m3 --model m3_momentum_regime --periods train validation test full
.venv/bin/python scripts/sweep_long.py --id G04_m2_market --model m2_xs_momentum \
    --grid 'market_sma_days=[null,20,50,100,150,200]' --grid 'lookback_days=[14,28,56]' --periods train validation
.venv/bin/python scripts/summarize_long.py B00_buy_hold L00_m0 L01_m1 L02_m2 L03_m3 --periods train validation test full
.venv/bin/python scripts/regime_long.py B00_buy_hold L00_m0 L01_m1 L02_m2 L03_m3
.venv/bin/python scripts/walkforward_long.py --id W01_m3 --model m2_xs_momentum \
    --grid 'lookback_days=[14,21,28,42,56]' --grid 'market_sma_days=[50,75,100,150,200]' --fixed top_n=3 \
    --frozen '{"lookback_days": 28, "market_sma_days": 100}'
.venv/bin/python scripts/run_live.py --once
```

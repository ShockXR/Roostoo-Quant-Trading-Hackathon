# Trading Bot Optimization Report

Five large-cap Binance spot pairs (BTC, ETH, SOL, BNB, XRP / USDT), 1-minute
candles, 2026-07-01 → 2026-09-30. Every number below comes from a run
saved under `experiments/` (per-run `metrics.csv`, `meta.json`, trades);
`experiments/registry.csv` collects them and `experiments/EXPERIMENT_LOG.md`
records each hypothesis and decision.

---

## 1. Executive Summary

**What the original bot did.** A per-asset machine-learning timer: 26
features (multi-horizon momentum, z-score/RSI/Bollinger mean-reversion,
volatility/trend regime, BTC context, cross-sectional rank) feed a
walk-forward random forest that predicts *P(10-minute return > 0.29%)*. A
cost filter and confidence/volatility sizing turn that probability into a
long position of at most 20% of capital, held for the 10-bar horizon.

**What I found.**

1. **It loses money in every period and on every asset** (full window:
   −1.50%, daily Sharpe −6.29, profit factor 0.34, 257 trades). It is
   not a bug: gross of costs it made **+$508**, then paid **$2,007** in
   fees and slippage. A 10-minute move on these coins is 7–10 bp (median)
   against a ~23 bp round-trip cost.
2. **The model buys after pumps.** Price had already risen 46–64 bp in
   the hour before the average entry and went nowhere afterwards. The
   threshold target rises with *volatility* regardless of direction, so
   the forest largely learned "volatility just spiked".
3. **Four ML fixes did not create an edge** (longer horizon, direction
   target, 5× longer training window). Each one turned positive only in
   the August rally, by holding more of the market; alpha against
   buy-and-hold was negative on the training month every time.
4. **One simple, explainable effect survived:** after an unusually large
   one-day drop (more than 1.5× the asset's typical one-day move), prices
   tended to rebound over the next day. This was profitable on train,
   validation and the untouched test month, on all five assets over the
   full window, under doubled costs and with an hour of execution delay.

**What changed.** The ML timing layer is replaced (in the backtest path) by
that rule — `strategy.py` `reversal`, parameters in `config.REVERSAL_*` —
and the backtester was rebuilt to hold units, fill at the next bar's open,
and report trade-level statistics.

| Portfolio ($100k, 5 assets) | Original bot | Final strategy | Buy & hold (20%/asset) |
|---|---|---|---|
| **Test (Sep, untouched)** return / Sharpe / max DD | −0.45% / −8.47 / −0.45% | **+3.87% / 4.54 / −2.36%** | +6.84% / 2.47 / −5.90% |
| Full window return / Sharpe / max DD | −1.50% / −6.29 / −1.50% | **+8.39% / 4.31 / −2.36%** | +35.95% / 3.73 / −6.95% |

**Is it better?** Yes, by a wide margin, against the original bot — on
every period, every risk-adjusted metric and every asset over the full
window. Its Sharpe and drawdown are also better than buy-and-hold's, but
it makes far less money than simply holding through a 40–70% rally, and
with 54 trades in three months the evidence that it has genuine timing
skill is **suggestive, not statistically significant** (alpha t-stat 1.1
on test, 1.7 full window, inside the ±2 noise floor measured on passive
buy-and-hold itself). It has never been tested in a falling market.

---

## 2. Repository Analysis

| Component | Where | Notes |
|---|---|---|
| Config / risk limits | `config.py` | 20% max per asset, 70% max gross, 0.10% taker / 0.05% maker fee, slippage estimate 0.03% round trip, 12% drawdown kill switch (live only) |
| Features | `features/` (`momentum`, `mean_reversion`, `regime`, `cross_sectional`, `build_features`) | All rolling/`pct_change` — causal. Price levels excluded from model inputs |
| Target | `model/targets.py` | `TARGET_MODE="threshold"`: 1 if return over `TARGET_HORIZON` bars > 0.29% |
| Model / walk-forward | `model/train.py` | RF (200 trees, depth 5); 2000-bar train, 500-bar val/step; purge = horizon |
| Signal → position | `backtest/ablation.py::_signal_from_model` | cost filter `p·avg_win − (1−p)·avg_loss > costs`, `sizing.confidence_to_size`, hold = rolling max over horizon |
| Cost model | `costs.py` | round trip = 2·fee + slippage |
| Position sizing | `sizing.py` | size ∝ (p − 0.55)/0.45 · 20%, shrunk when vol > `SIZING_REFERENCE_VOL` |
| Original backtester | `backtest/engine.py` | vectorized weight × return, costs on turnover; `summarize()` |
| Stop-loss / take-profit / leverage | — | none in the original bot; spot, long-only, no leverage |
| Original runners | `scripts/backtest_sol.py`, `scripts/sweep.py` | 8-stage ablation per symbol |
| Live loop | `main.py`, `execution/`, `data/` | DRY_RUN; Roostoo API; uses the ML model |
| Tests | — | none existed |

**New in this work**

| Component | File |
|---|---|
| Execution-level simulator (units, next-open fills, fee/slippage split, trades, portfolio cap) | `backtest/simulator.py` |
| Full metric set incl. trade stats, alpha/beta vs buy-and-hold | `backtest/metrics.py` |
| Strategy registry (`baseline` = untouched original, `buy_hold`, `reversal` = final) | `strategy.py` |
| Experiment runner (train / validation / test / full, standalone + portfolio) | `scripts/run_backtest.py` |
| Data audit, trade diagnostics, regime report, comparison, registry | `scripts/validate_data.py`, `analyze_trades.py`, `regime_report.py`, `compare.py`, `summarize_experiments.py` |
| Shared loader / split definition | `scripts/common.py` |
| Tests (11) | `tests/test_simulator.py`, `tests/test_strategy.py` |

---

## 3. Dataset

Binance spot 1m klines from `binance_downloader.py`
(`binance_data/processed/parquet/<SYMBOL>_1m.parquet`). The repo also
holds UNI, AAVE, CRV, ARB, ENA — mid-caps, excluded here; the downloader
itself labels the five below as the large caps. Audit:
`scripts/validate_data.py` → `experiments/data_validation.json`.

| Symbol | Timeframe | Range (UTC) | Candles | Missing / dup / NaN / OHLC violations | Largest 1m move | Buy & hold | Jul / Aug / Sep |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 1m | 2026-07-01 00:00 → 09-30 23:59 | 132,480 | 0 / 0 / 0 / 0 | 2.18% (08-19 15:27) | +42.4% | +7.1% / +24.9% / +6.4% |
| ETHUSDT | 1m | same | 132,480 | 0 / 0 / 0 / 0 | 2.54% (08-19 15:27) | +70.7% | +18.4% / +32.4% / +9.0% |
| SOLUSDT | 1m | same | 132,480 | 0 / 0 / 0 / 0 | 5.51% (08-22 05:10) | +60.3% | −1.1% / +41.4% / +14.7% |
| BNBUSDT | 1m | same | 132,480 | 0 / 0 / 0 / 0 | 2.62% (08-22 05:10) | +40.7% | +7.3% / +17.7% / +11.3% |
| XRPUSDT | 1m | same | 132,480 | 0 / 0 / 0 / 0 | 4.95% (08-22 05:10) | +43.2% | +2.1% / +29.9% / +8.1% |

Observations:

- Timestamps strictly increasing at exactly 1 minute; no zero-volume or
  non-positive-price bars. XRP has 17 bars with high = low (genuinely
  quiet minutes, kept).
- 792–1,268 bars per asset have volume > 20× the median — ordinary crypto
  bursts, kept.
- The 2026-08-22 05:10 move hit SOL, BNB and XRP in the same minute: a
  real market-wide event, not corruption. Kept. The final strategy had no
  trades open around it.
- Next bar's open differs from this bar's close in ~48% of bars, but by
  0.0015–0.55 bp on average — fills at "next open" vs "this close" are
  nearly the same thing on this data.
- **No preprocessing or repair was applied.** The only transformation is
  using `open`/`close` in UTC as stored.
- **Regime caveat:** all five assets rose 41–71% in three months
  (August alone +18% to +41%). There is no sustained bear phase in the data.

---

## 4. Backtester Validation

### Issues found in `backtest/engine.py` and the pipeline

| # | Issue | Effect | Fix |
|---|---|---|---|
| 1 | `position × pct_change` holds a **constant weight**, i.e. rebalances every bar for free | Unrealistic; small on the baseline (BTC full: −0.178% legacy vs −0.177% new) | `backtest/simulator.py` holds **units**; trades only when the target changes; weights drift with price |
| 2 | Signal computed from close[t] is **filled at close[t]** | Same-bar fill | Fill at **open[t+1]** ± slippage. Measured impact small (§3) and stress-tested with 1/5/60-bar extra delay (§9) |
| 3 | Sortino used `std()` of negative returns (subtracts their mean) | Understates downside risk | Downside deviation `sqrt(mean(min(r,0)²))` over all periods |
| 4 | `n_trades_proxy` and `win_rate` were **bar-level** | BTC: 138 "trades" vs 38 real round trips; win rate over bars ≠ per trade | Trades are flat→position→flat round trips with $ PnL incl. costs; trade PnL reconciles to the equity change (tested) |
| 5 | Fees and slippage lumped together | Can't see cost structure | Charged and reported separately |
| 6 | No benchmark-relative measure | In a +40–70% market, return/Sharpe mostly measure exposure | Daily-return beta and alpha (t-stat) vs the asset / equal-weight basket |
| 7 | `SIZING_REFERENCE_VOL` was tuned on SOL **Jul–Sep**, i.e. including the test month | Mild test leakage in a baseline parameter | **Documented, not changed** — it's part of the original bot; affects only the baseline |
| 8 | September was already read once by the previous author for the ML h=240 config (`docs/BACKTEST_RESULTS.md`) | That config family is not "untouched" on September | Disclosed. The final strategy family was never run on September before F01 |

**Checked and found correct:** walk-forward purge (`TARGET_HORIZON` rows
dropped before each val block — now unit-tested); features are backward
rolling windows; the price panel is forward-filled only; the target is
only ever a label; the cost filter's win/loss calibration uses the fold's
train window only; the hold logic (`rolling(h).max()`) is causal; the
parallel runner is deterministic (a rerun with a different worker count
produced byte-identical metrics).

### Execution model (all experiments unless stated)

- Target weight decided with data up to and including bar t's close;
  executed at **bar t+1's open**.
- **Fee 0.10% per side** of notional (Roostoo taker, `config.TAKER_FEE`).
- **Slippage 0.015% per side** of price (`config.SLIPPAGE_ESTIMATE / 2`,
  matching the original engine's assumption).
- Positions held in units; traded only when the target changes; marked at
  every close; cash earns nothing.
- Per asset ≤ 20% of equity; portfolio gross ≤ 70% (targets scaled down
  pro rata when exceeded). Long-only, no leverage.
- **Standalone** runs: $100k per asset. **PORTFOLIO**: one $100k account
  for all five assets.
- **Not modelled:** minimum order size / lot rounding, market impact,
  partial or missed fills, exchange outages. The 12% drawdown kill switch
  and `MAX_TRADES_PER_HOUR` (20) are live-loop features; neither would have
  triggered (max DD ≤ 2.4%; at most 12 fills in any hour for the original
  bot and 4 for the final strategy).

---

## 5. Original Baseline

`E01_baseline` — the original bot unchanged (config defaults: 1m bars,
`TARGET_HORIZON=10`). The repo's own engine output for the same signal
(`E00_original_repo_output`, `scripts/backtest_sol.py`) agrees: full-window
returns −0.15% to −0.54% per asset.

**Per asset, full window** ($100k standalone each):

| Asset | Return | PnL $ | Max DD | Sharpe | Sortino | PF | Win % | Trades | Avg trade $ | Fees $ | Slippage $ | Exposure | Buy & hold (100%) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | −0.18% | −178 | −0.21% | −3.32 | −3.40 | 0.35 | 29% | 38 | −4.7 | 290 | 44 | 0.0% | +42.4% |
| ETH | −0.22% | −219 | −0.26% | −3.87 | −4.15 | 0.35 | 35% | 37 | −5.9 | 278 | 42 | 0.0% | +70.7% |
| SOL | −0.43% | −430 | −0.43% | −3.92 | −3.93 | 0.31 | 36% | 61 | −7.1 | 455 | 68 | 0.0% | +60.3% |
| BNB | −0.15% | −146 | −0.19% | −2.84 | −3.12 | 0.25 | 19% | 21 | −6.9 | 173 | 26 | 0.0% | +40.7% |
| XRP | −0.53% | −534 | −0.60% | −4.75 | −5.00 | 0.37 | 35% | 100 | −5.3 | 558 | 84 | 0.0% | +43.2% |
| **Portfolio** | **−1.50%** | **−1,499** | **−1.50%** | **−6.29** | **−6.18** | **0.34** | **33%** | **257** | **−5.8** | **1,745** | **262** | **0.1%** | +35.95% (20%/asset) |

**Portfolio detail, full window:** initial $100,000 → final $98,501;
annualized −5.8% (92 days — not meaningful); 85 winners / 172 losers;
average win $9.06, average loss −$13.19; expectancy −$5.83/trade;
largest win $62, largest loss −$109; average hold 16 minutes; time in
market 2.2%; average capital deployed 0.09%; turnover 17.6× average
equity; max consecutive wins 4 / losses 15; average drawdown episode
−1.50% (one continuous decline); 1m-bar Sharpe −6.75 / Sortino −8.80;
Calmar −3.88; beta 0.001; alpha −6.2%/yr (t −3.29).

**By period** (portfolio): train −0.25% (Sharpe −8.13), validation −0.80%
(−6.35), test −0.45% (−8.47).

---

## 6. Experiments

Protocol (fixed before the first experiment): **train = July, validation
= August, test = September** (`scripts/common.PERIODS`). Changes are
judged on train, must hold on validation; test was read for the baseline,
the benchmark and the final candidate only. The primary metric is
portfolio daily Sharpe / Sortino and alpha, then per-asset consistency,
drawdown and trade count. Full table with per-run numbers:
`experiments/EXPERIMENT_LOG.md`; raw numbers: `experiments/registry.csv`.

**Diagnosis of the baseline (train-period trades, `analyze_trades.py`).**
Event study around entries: +48 bp at −240 bars, +64 bp at −60, +46 bp at
−10, then +1 bp at +10, −6 bp at +60. Trades entered after the market rose
over the prior 4h lost most (−$13.9 avg) and after it fell least (−$1.9).
Mean gross move per trade 6.4 bp vs ~23 bp cost. Separately, on July
data: median |move| is 7–10 bp over 10 min, 33–47 bp over 4h, 81–146 bp
over 1 day; 4h returns are negatively autocorrelated on 4/5 assets
(−0.07 to −0.15), 1-day returns weakly positive on 4/5 (n=29 — weak).

| ID | Hypothesis | Change | Train (Sharpe / return) | Validation | Decision & reasoning |
|---|---|---|---|---|---|
| E02 | 10-bar moves can't cover costs; 240 bars can | `TARGET_HORIZON` 10 → 240 | −5.88 / −3.61% | 2.99 / +6.19% | **Rejected.** Validation gain is beta (0.41; average exposure 165–380× the baseline's); alpha negative in both (t −3.2 / −1.5); PF 0.38 on train |
| E03 | Threshold target learns volatility, not direction | E02 + direction target (r > 0) | −4.19 / −4.32% | 2.67 / +6.13% | **Rejected.** Alpha t −2.7 / −2.2 |
| E04 | 2000-bar window has ~8 independent labels at h=240 | E02 + 7-day train window, 1-day step | −5.35 / −0.95% | 4.64 / +6.46% | **Rejected.** Best ML variant (val alpha ≈ 0, PF 5.7), but train alpha t −2.9. ML timing never showed positive alpha on train |
| E05 ×10 | Daily momentum; hourly decisions limit turnover | Rule: long while close > EMA(span), span 6h–4d, band 0 / 0.5% | −0.8 to −13.5 | not run | **Rejected.** Every cell negative alpha on train; whipsaw (221 round trips, $8.6k fees at span 1d) |
| E06 ×9 | 4h mean reversion; baseline lost after rises | Rule: buy when lookback return < −k·σ, hold lookback; lookback 1h/4h/12h × k 1/1.5/2 | 1h: all < −12; 4h: < 0 except k=2; 12h, k≥1.5: +0.7 to +1.1 | 12h/k1.5: 7.36 / +4.48% | **Promising.** Only the region positive on train was validated; positive there with beta ≈ 0 |
| E07 ×18 | Is it a lone spike? | Neighbourhood lookback 8h–24h × k 1.25–2, hold 6h/24h | Positive plateau at lookback ≥ 12h, k ≥ 1.5 | Positive in all 16 cells | **Accepted: lookback 24h, k 1.5, hold 24h** — the centre of the plateau (12h/1.5 sat on its edge next to a −2.45 cell). Train 3.94 / +2.27%, val 4.56 / +1.94% |
| E08 ×3 | Stops limit falling-knife losses | + exit after a further stop·σ drop; stop 1 / 1.5 / 2 | 0.59 / 3.18 / 3.94 | 3.57 / 4.56 / 4.56 | **Rejected.** Hurts or never triggers; deeper dips usually still reverted |
| E09 ×3 | Equalize risk across assets | weight = min(20%, budget/σ_daily) | 4.43 / 4.36 / 4.03 | 3.52 / 3.42 / 3.74 | **Rejected.** Train slightly up, validation down, lower return |
| E10 ×3 | Winners peak (+2.3%) and give back | + take-profit at tp·σ, tp 0.5 / 0.75 / 1 | 6.01 / 4.07 / 4.58 | 3.77 / 4.12 / 5.10 | **Rejected.** Non-monotonic in tp; train and validation disagree; within noise |
| F01 | — | Final candidate, test read once | 3.94 / +2.27% | 4.56 / +1.94% | **Test: 4.54 / +3.87%**, max DD −2.36% |
| E11 ×12 | Survives worse assumptions? | costs, latency, capital (§9) | | | **Passed** |
| E12 ×20 | Post-freeze sensitivity (reporting only) | grid on all periods (§9) | | | Lookbacks ≤ 12h fail on test; ≥ 24h robust |

---

## 7. Final Strategy

`strategy.py::_reversal`, parameters in `config.py`:

```
REVERSAL_LOOKBACK_BARS = 1440   # 1 day of 1m bars
REVERSAL_K             = 1.5
REVERSAL_HOLD_BARS     = 1440
REVERSAL_CHECK_EVERY   = 60     # minutes
MAX_POSITION_PER_ASSET = 0.20   # unchanged
MAX_PORTFOLIO_EXPOSURE = 0.70   # unchanged
```

For each asset independently:

1. Once per hour, at the close of the hh:59 bar, compute the asset's
   **one-day return** `close / close 1440 bars ago − 1`.
2. Compute its **expected one-day move**: standard deviation of the last
   1440 one-minute returns × √1440.
3. If the one-day return is **below −1.5 × expected move**, signal a buy.
4. Buy **20% of equity** at the next bar's open and **hold for 1440 bars
   (24h)** after the last signal; a new signal while holding extends the
   hold. Exit at the next open after the hold expires.
5. No stop-loss, no take-profit, no leverage, long only. The portfolio
   gross cap (70%) scales positions down if four or more assets signal
   together.

**Why it might work:** large one-day drops in liquid majors are often
liquidation/overshoot moves that partially retrace within a day. Trading
on a daily scale makes the typical move (80–146 bp) several times the
23 bp round-trip cost, where the original 10-minute horizon had costs
larger than the move. Trading only after unusual drops keeps turnover low
(54 trades in 3 months).

**What it is not:** a forecasting model. The ML pipeline remains in the
repository (it defines the baseline and the ablation harness), but the
final strategy does not use it.

---

## 8. Before vs After

Generated by `scripts/compare.py E01_baseline F01_final B00_buy_hold_20pct`
(→ `experiments/final_comparison.csv`). Standalone assets use $100k each;
buy & hold is a constant 20% weight, the same size the bot is allowed.

#### Test period (September — untouched until the final run)

| Asset | Strategy | Return | PnL $ | Max DD | Sharpe | Sortino | PF | Win % | Trades | Avg trade $ | Fees $ | Slippage $ | Exposure |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | Original | −0.03% | −29 | −0.03% | −4.43 | −4.71 | 0.24 | 29% | 7 | −4.2 | 49 | 7 | 0.0% |
| BTC | **Final** | +0.49% | +488 | −0.68% | 3.56 | 10.06 | 5.20 | 75% | 4 | +122.1 | 161 | 24 | 3.0% |
| BTC | Buy & hold 20% | +1.27% | +1,266 | −1.77% | 1.82 | 3.87 | — | — | 1 | — | 20 | 3 | 20.4% |
| ETH | Original | −0.03% | −30 | −0.09% | −1.51 | −1.95 | 0.72 | 40% | 10 | −3.0 | 93 | 14 | 0.0% |
| ETH | **Final** | +1.10% | +1,095 | −0.56% | 5.90 | 58.86 | ∞ | 100% | 3 | +365.0 | 122 | 18 | 2.3% |
| ETH | Buy & hold 20% | +1.77% | +1,766 | −2.36% | 2.39 | 4.44 | — | — | 1 | — | 20 | 3 | 20.6% |
| SOL | Original | −0.13% | −134 | −0.14% | −5.45 | −5.44 | 0.26 | 33% | 18 | −7.4 | 120 | 18 | 0.0% |
| SOL | **Final** | +1.92% | +1,917 | −0.69% | 6.01 | 19.67 | 30.92 | 83% | 6 | +319.4 | 244 | 37 | 4.3% |
| SOL | Buy & hold 20% | +2.92% | +2,917 | −2.19% | 2.69 | 5.29 | — | — | 1 | — | 20 | 3 | 20.8% |
| BNB | Original | +0.01% | +11 | −0.03% | 0.66 | 1.13 | 1.27 | 60% | 5 | +2.1 | 36 | 5 | 0.0% |
| BNB | **Final** | +0.02% | +23 | −1.00% | 0.12 | 0.17 | 1.13 | 50% | 4 | +5.7 | 160 | 24 | 3.6% |
| BNB | Buy & hold 20% | +2.23% | +2,234 | −2.12% | 2.93 | 5.39 | — | — | 1 | — | 20 | 3 | 21.2% |
| XRP | Original | −0.27% | −271 | −0.30% | −7.90 | −7.52 | 0.19 | 32% | 31 | −8.7 | 194 | 29 | 0.0% |
| XRP | **Final** | +1.08% | +1,081 | −1.23% | 2.47 | 4.56 | 2.31 | 60% | 5 | +216.2 | 203 | 30 | 4.5% |
| XRP | Buy & hold 20% | +1.59% | +1,590 | −3.41% | 1.35 | 2.10 | — | — | 1 | — | 20 | 3 | 20.5% |
| **Portfolio** | Original | −0.45% | −453 | −0.45% | −8.47 | −8.94 | 0.35 | 35% | 71 | −6.4 | 490 | 74 | 0.1% |
| **Portfolio** | **Final** | **+3.87%** | **+3,873** | **−2.36%** | **4.54** | **10.48** | **4.07** | **73%** | **22** | **+176.0** | **873** | **131** | **15.9%** |
| **Portfolio** | Buy & hold 20% | +6.84% | +6,841 | −5.90% | 2.47 | 4.51 | — | — | 5 | — | 70 | 10 | 70.9% |

#### Full window (Jul–Sep)

| Asset | Strategy | Return | PnL $ | Max DD | Sharpe | Sortino | PF | Win % | Trades | Avg trade $ | Fees $ | Slippage $ | Exposure |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | Original | −0.18% | −178 | −0.21% | −3.32 | −3.40 | 0.35 | 29% | 38 | −4.7 | 290 | 44 | 0.0% |
| BTC | **Final** | +2.41% | +2,410 | −0.68% | 4.51 | 18.87 | 8.38 | 75% | 12 | +200.8 | 490 | 74 | 3.3% |
| ETH | Original | −0.22% | −219 | −0.26% | −3.87 | −4.15 | 0.35 | 35% | 37 | −5.9 | 278 | 42 | 0.0% |
| ETH | **Final** | +0.74% | +737 | −0.82% | 1.72 | 3.99 | 2.03 | 62% | 8 | +92.2 | 321 | 48 | 2.1% |
| SOL | Original | −0.43% | −430 | −0.43% | −3.92 | −3.93 | 0.31 | 36% | 61 | −7.1 | 455 | 68 | 0.0% |
| SOL | **Final** | +2.67% | +2,668 | −0.87% | 3.86 | 8.56 | 8.08 | 82% | 11 | +242.5 | 447 | 67 | 3.1% |
| BNB | Original | −0.15% | −146 | −0.19% | −2.84 | −3.12 | 0.25 | 19% | 21 | −6.9 | 173 | 26 | 0.0% |
| BNB | **Final** | +1.21% | +1,214 | −1.00% | 2.29 | 4.21 | 7.87 | 78% | 9 | +134.8 | 365 | 55 | 2.6% |
| XRP | Original | −0.53% | −534 | −0.60% | −4.75 | −5.00 | 0.37 | 35% | 100 | −5.3 | 558 | 84 | 0.0% |
| XRP | **Final** | +2.36% | +2,356 | −1.23% | 2.43 | 4.82 | 2.60 | 64% | 14 | +168.3 | 567 | 85 | 3.8% |
| **Portfolio** | Original | −1.50% | −1,499 | −1.50% | −6.29 | −6.18 | 0.34 | 33% | 257 | −5.8 | 1,745 | 262 | 0.1% |
| **Portfolio** | **Final** | **+8.39%** | **+8,389** | **−2.36%** | **4.31** | **9.57** | **3.54** | **72%** | **54** | **+155.3** | **2,240** | **336** | **14.0%** |
| **Portfolio** | Buy & hold 20% | +35.95% | +35,946 | −6.95% | 3.73 | 7.75 | — | — | 5 | — | 70 | 10 | 74.0% |

**Portfolio by period**

| Period | Original return / Sharpe | Final return / Sharpe / max DD | Buy & hold return / Sharpe / max DD |
|---|---|---|---|
| Train (Jul) | −0.25% / −8.13 | +2.27% / 3.94 / −2.16% | +4.65% / 2.31 / −4.67% |
| Validation (Aug) | −0.80% / −6.35 | +1.94% / 4.56 / −1.10% | +20.40% / 5.76 / −6.19% |
| Test (Sep) | −0.45% / −8.47 | +3.87% / 4.54 / −2.36% | +6.84% / 2.47 / −5.90% |

**Other portfolio metrics, full window (original → final):** final capital
$98,501 → $108,389; 85/172 → 39/15 winning/losing trades; average win
$9.06 → $300; average loss −$13.19 → −$220; expectancy per trade −$5.83
(−0.006%) → +$155 (+0.15%); largest win $62 → $1,367; largest loss −$109 →
−$434; average hold 16 min → 30.5 h; time in market 2.2% → 32.7%; turnover
17.6× → 21.7×; max consecutive wins 4 → 9, losses 15 → 3; gross PnL before
costs +$508 → +$10,965; worst day −0.36% → −0.98%; 1m-bar Sharpe / Sortino
−6.75 / −8.80 → 2.94 / 4.26; Calmar −3.9 → 16.0; competition composite
(0.4·Sortino + 0.3·Sharpe + 0.3·Calmar, 1m bars) −6.7 → 7.4 (buy & hold
13.6); beta 0.001 → 0.05; alpha t −3.29 → +1.66.

---

## 9. Robustness Analysis

**Out-of-sample.** Parameters were set from July (train) and confirmed on
August (validation); September (test) was read once. Test results
(Sharpe 4.54, +3.87%) are in line with train (3.94) and validation
(4.56). The previous author's ML config, by contrast, was profitable on
3/3 symbols in its research window and on 1/3 out of sample
(`docs/BACKTEST_RESULTS.md`).

**Parameter sensitivity** (`E12_sensitivity`, run after freezing; not
used for selection). Portfolio daily Sharpe, hold = lookback:

| Lookback \ k | 1.25 | 1.5 | 1.75 | 2.0 | | 1.25 | 1.5 | 1.75 | 2.0 |
|---|---|---|---|---|---|---|---|---|---|
| | **train** | | | | | **test** | | | |
| 8h | −4.65 | −2.76 | −1.11 | −0.86 | | −4.51 | −1.02 | −1.80 | 0.50 |
| 12h | −2.45 | 1.11 | 1.75 | 0.71 | | −3.27 | −4.58 | −3.39 | −3.75 |
| 18h | −0.56 | 1.53 | 5.39 | 4.37 | | 2.36 | 3.04 | 0.96 | −3.24 |
| **24h** | 1.90 | **3.94** | 3.65 | 3.00 | | 6.58 | **4.54** | 4.61 | 3.66 |
| 36h | 6.11 | 4.02 | 5.11 | 6.84 | | 5.70 | 3.89 | 2.16 | 2.16 |

Validation was positive in all 20 cells (0.58–7.36). Lookbacks of 24h and
36h are positive in **every cell of every period**; lookbacks ≤ 12h are not
robust and fail on test. The first promising cell (12h, k 1.5; +4.48% on
validation) would have lost on test (Sharpe −4.58). Preferring the centre
of the plateau over its edge made the difference.

**Costs, latency, capital** (`E11_stress`, re-simulated frozen targets;
portfolio):

| Scenario | Train | Validation | Test | Full return / Sharpe / max DD |
|---|---|---|---|---|
| Base (0.10% fee, 0.015% slip) | +2.27% | +1.94% | +3.87% | +8.39% / 4.31 / −2.36% |
| Fee 0.15%/side | +1.83% | +1.68% | +3.43% | +7.23% / 3.78 / −2.44% |
| Fee 0.20%/side (2×) | +1.39% | +1.43% | +2.99% | +6.08% / 3.23 / −2.52% |
| Slippage 0.05%/side | +1.96% | +1.76% | +3.56% | +7.57% / 3.94 / −2.41% |
| Slippage 0.10%/side | +1.52% | +1.51% | +3.12% | +6.42% / 3.39 / −2.49% |
| Fee 0.15% + slippage 0.05% | +1.52% | +1.51% | +3.12% | +6.42% / 3.39 / −2.49% |
| Maker fee 0.05% (optimistic) | +2.71% | +2.20% | +4.32% | +9.56% / 4.82 / −2.28% |
| Fill 1 bar later | +2.19% | +1.83% | +3.68% | +7.99% / 4.13 / −2.29% |
| Fill 5 bars later | +2.47% | +2.14% | +3.36% | +8.27% / 4.18 / −2.38% |
| Fill 60 bars (1h) later | +1.70% | +2.14% | +4.46% | +8.60% / 3.79 / −2.18% |
| $10k / $1M capital | identical % (+8.39%) | | | the simulator has no lot sizes or market impact, so this is expected, not evidence |
| *Original bot, fee 0.15% + slip 0.05%* | −0.55% | −1.57% | −0.87% | −2.96% / −8.71 / −2.96% |

**Assets.** Full window: all five positive (+0.74% ETH to +2.67% SOL).
Test: four clearly positive, BNB flat (+0.02%). ETH is the weakest: it lost
on train (−0.30%) and validation (−0.09%) and only won on test.

**Market regimes** (`scripts/regime_report.py` → `experiments/regime_report.json`;
days classified by the equal-weight basket, full window, portfolio):

| Basket day | Days | Final PnL | Final mean day | Buy & hold mean day | Original mean day |
|---|---|---|---|---|---|
| Down > 1% | 22 | −$3,061 | −13.2 bp | −159.3 bp | −3.0 bp |
| Flat ±1% | 41 | +$2,459 | +5.8 bp | +6.4 bp | −1.2 bp |
| Up > 1% | 29 | +$8,990 | +29.9 bp | +222.7 bp | −1.2 bp |
| Low-vol tercile | 31 | −$183 | −0.5 bp | +9.5 bp | −0.3 bp |
| Mid-vol tercile | 30 | +$2,358 | +7.8 bp | −10.2 bp | −2.6 bp |
| High-vol tercile | 31 | +$6,214 | +19.3 bp | +104.2 bp | −2.0 bp |

It loses on sharp down days (it buys into them) but far less than holding
does. It earns on rebound days and in volatile markets, and is idle in quiet ones.

**Drawdowns.** Max drawdown −2.36% (vs −6.95% buy & hold, −1.50% original);
worst day −0.98%; worst 7-day window −1.25%; longest losing streak 2 days
(original: 6); 2 losing weeks of 13 (original: 11). Monthly returns
(continuous run): Jul +2.27%, Aug +2.03%, Sep +3.87%. Longest stretch
below a previous equity high: ~28 days — the strategy can go weeks without
a new high because it trades rarely.

**Concentration.** The top 5 of 54 trades produced 53% of PnL; without
them the full-window PnL is still +$3,907. Entries cluster on market-wide
dip days (up to 4 assets on the same day), so there are fewer than 54
independent bets.

---

## 10. Remaining Weaknesses

1. **Small sample.** 54 trades (22 in the test month), effectively fewer
   independent events. Alpha t-stats are +1.1 (test) / +1.7 (full); passive
   buy & hold scores −1.1 to −1.8 against its own basket, so ±2 is
   noise. The edge is plausible and consistent, **not proven**.
2. **One market regime.** All five assets rose 40–70%. Dip-buying is the
   textbook strategy that works in uptrends and fails in sustained
   downtrends (each dip is followed by a lower one). The stop-loss
   experiment could not test that because there was no such phase; a
   falling-knife guard (e.g. a longer-term trend filter) is untested and
   was deliberately not added without evidence.
3. **It makes much less money than holding** in a bull market (full window
   +8.4% vs +36.0%), because it is in the market a third of the time with
   ~14% of capital on average. Its advantage is risk-adjusted (Sharpe 4.31
   vs 3.73; max DD −2.4% vs −7.0%), not absolute. On the competition
   composite score it beats buy & hold on test (10.3 vs 8.6), ties on train
   and loses on the August rally and the full window.
4. **ETH and BNB are weak** (ETH negative in two of three months; BNB flat
   on test).
5. **Loses on sharp market-wide down days** (−$3,061 over 22 days) and
   **sits idle in quiet markets**; long flat stretches (~28 days without a
   new equity high).
6. **Execution assumptions.** Next-open fills at 0.015% slippage are
   realistic for these pairs on Binance; the Roostoo mock exchange's
   fills, lot sizes and minimum orders were not modelled (stress tests
   show the edge survives 2× fees and 0.10% slippage).
7. **The live loop (`main.py`) still runs the ML model.** The final
   strategy exists in the backtest path only; porting it to live is a
   separate, deliberate change (it needs ≥ 1 day of 1m history per asset
   and hourly evaluation).
8. **The original ML pipeline is unrepaired.** Its core flaw (a
   volatility-driven target at a horizon shorter than costs) is diagnosed,
   and four fixes were tried without success; it was not redesigned.

---

## 11. Reproducibility

Python 3.12, dependencies unchanged (`requirements.txt`). From the project
root (`fish`/`bash` alike):

```bash
# 1. environment
uv venv .venv && uv pip install -r requirements.txt
#    (or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)

# 2. data (only if binance_data/ is absent) -- end date is inclusive
.venv/bin/python binance_downloader.py --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT \
    --start 2026-07-01 --end 2026-09-30

# 3. data audit
.venv/bin/python scripts/validate_data.py

# 4. tests (11)
.venv/bin/python -m unittest discover -s tests -v

# 5. original baseline (~10-20 min: walk-forward random forest)
.venv/bin/python scripts/run_backtest.py --id E01_baseline --strategy baseline \
    --periods train validation test full

# 6. final strategy (~30 s; parameters from config.REVERSAL_*)
.venv/bin/python scripts/run_backtest.py --id F01_final --periods train validation test full

# 7. benchmark, comparison tables, regimes, stress, registry
.venv/bin/python scripts/run_backtest.py --id B00_buy_hold_20pct --strategy buy_hold \
    --periods train validation test full
.venv/bin/python scripts/compare.py E01_baseline F01_final B00_buy_hold_20pct \
    --periods train validation test full
.venv/bin/python scripts/regime_report.py F01_final E01_baseline B00_buy_hold_20pct
.venv/bin/python scripts/run_backtest.py --id E11_stress/F01_final_S02_fee20 \
    --targets-from F01_final --fee 0.002 --periods train validation test full
.venv/bin/python scripts/analyze_trades.py --id F01_final --period full
.venv/bin/python scripts/summarize_experiments.py

# original repo output with the original engine (E00)
.venv/bin/python scripts/backtest_sol.py --symbol SOLUSDT
```

Metrics land in `experiments/<id>/metrics.csv` (every asset × period),
trades in `trades_<period>.csv`, run settings in `meta.json`. Cached
target weights (`targets.parquet`) are gitignored and regenerated by
the runner. Runs are deterministic: rerunning the baseline with a
different worker count and rerunning the final strategy after the code
cleanup (`F02_final_repro`) produced identical metrics. Rejected
experiments E05 and E08–E10 used options since removed from
`strategy.py`; that exact code is in
`experiments/archive/strategy_experimental.py`.

---

## 12. Files Changed

| File | Status | Why |
|---|---|---|
| `backtest/simulator.py` | new | Execution-level backtester (§4 fixes 1, 2, 4, 5) |
| `backtest/metrics.py` | new | Full metric set, correct Sortino, trade stats, alpha/beta (§4 fixes 3, 6) |
| `strategy.py` | new | Strategy registry: `baseline` (original bot, untouched code path), `buy_hold`, `reversal` (final) |
| `config.py` | modified | Added the documented `REVERSAL_*` parameters; nothing existing changed |
| `scripts/run_backtest.py` | new | Experiment runner: periods, portfolio + standalone, stress options, legacy-engine cross-check |
| `scripts/common.py` | new | Package bootstrap, OHLCV loader, fixed chronological split |
| `scripts/validate_data.py` | new | Read-only data audit |
| `scripts/analyze_trades.py` | new | Trade diagnostics (asset/time/regime breakdowns, MFE/MAE, event study) |
| `scripts/regime_report.py` | new | Regime, streak and drawdown report |
| `scripts/compare.py` | new | Before/after tables from saved metrics |
| `scripts/summarize_experiments.py` | new | Builds `experiments/registry.csv` |
| `tests/test_simulator.py`, `tests/test_strategy.py` | new | Fill timing, costs, unit holding, PnL reconciliation, exposure cap, purge gap, no-look-ahead, config defaults |
| `experiments/` | new | All results, `EXPERIMENT_LOG.md`, `registry.csv`, `data_validation.json`, `regime_report.json`, `final_comparison.csv`, archived experimental code |
| `TRADING_BOT_OPTIMIZATION_REPORT.md` | new | This report |
| `docs/BACKTEST_RESULTS.md` | modified | One-line pointer to this report (its conclusions are superseded) |

Not changed: the ML pipeline (`features/`, `model/`, `costs.py`,
`sizing.py`, `backtest/engine.py`, `backtest/ablation.py`), the original
runners, the live loop and execution code, and the data.

---

## 13. Final Recommendation

**The final strategy is a real improvement over the original bot.** The
original loses after costs in every period and on every asset because
its 10-minute horizon is shorter than its costs can support, and the ML
fixes tried here did not change that. The replacement is three lines of
logic, uses two parameters chosen from the centre of a stable region,
was positive on training, validation and an untouched test month, on all
five assets over the full window, and keeps working under doubled costs
and an hour of execution delay.

**It is not evidence of a reliable live edge.** Three months, one
bull regime and 54 trades cannot establish one; the alpha estimate is
inside the noise floor; and the strategy has never seen a sustained
decline, which is exactly where buying dips fails. Before risking
capital: port it to the live loop in DRY_RUN, extend the backtest to
longer history that includes a bear market (2022 Binance data is
available through the same downloader), and treat the 12% drawdown kill
switch as mandatory. Historical profitability here does not imply future
profitability.

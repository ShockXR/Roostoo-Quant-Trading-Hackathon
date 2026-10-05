# Experiment log

Numbers for every run are in `experiments/<id>/metrics.csv` (all assets ×
periods) and collected in `experiments/registry.csv`
(`scripts/summarize_experiments.py`). This file records the hypothesis,
the change and the decision. Nothing is deleted: rejected experiments stay.

**Protocol.** Chronological split fixed before the first experiment:
train = Jul 2026, validation = Aug 2026, test = Sep 2026
(`scripts/common.PERIODS`). A change is judged on train, then must hold on
validation. Test is read only for the baseline and the final candidate.

**Costs in every run** unless stated: 0.10% fee + 0.015% slippage per
side (config.TAKER_FEE, config.SLIPPAGE_ESTIMATE / 2), fills at the next
bar's open. Portfolio = one $100k account across the five assets, ≤20%
per asset, gross exposure capped at 70%.

**Primary decision metric:** portfolio daily Sharpe and Sortino on train
and validation, then per-asset consistency (how many of 5 assets
improve), max drawdown and trade count. Total return alone never decides.

| ID | Date | Hypothesis | Change | Train Sharpe / ret | Val Sharpe / ret | Decision |
|---|---|---|---|---|---|---|
| E00 | 2026-10-05 | — | Original repo output, original engine (`scripts/backtest_sol.py`), full window, h=10 and h=240 | — | — | Reference |
| B00 | 2026-10-05 | — | Buy & hold, 20% per asset (benchmark) | 2.31 / +4.65% | 5.76 / +20.40% | Benchmark |
| E01 | 2026-10-05 | — | Original bot, unchanged, new simulator | −8.13 / −0.25% | −6.35 / −0.80% | **Baseline** |
| E02 | 2026-10-05 | 10-bar moves (7–10 bp median) can't cover ~23 bp round-trip costs; 240 bars (33–47 bp) can | `horizon=240` (purge + hold follow) | −5.88 / −3.61% | 2.99 / +6.19% | **REJECTED** — validation gain is beta (0.41); alpha negative in both periods (t −3.2 / −1.5); PF 0.38 on train |
| E03 | 2026-10-05 | Threshold target P(r > 0.29%) rises with volatility regardless of direction, so the RF learned "vol spiked" (event study: entries after +46–64 bp, flat afterwards) | E02 + `TARGET_MODE=direction` | −4.19 / −4.32% | 2.67 / +6.13% | **REJECTED** — alpha negative both periods (t −2.7 / −2.2); validation return is beta (0.47) |
| E04 | 2026-10-05 | At h=240 a 2000-bar window holds ~8 independent labels; a 7-day window gives ~42 | E02 + walk-forward train 10080 bars, val/step 1440 | −5.35 / −0.95% | 4.64 / +6.46% | **REJECTED** — best ML variant (val alpha ≈ 0, PF 5.7) but train alpha still significantly negative (t −2.9). Across E01–E04 the ML timing layer never shows positive alpha on train |
| E05 (10 runs) | 2026-10-05 | Daily momentum (1d autocorr > 0 on 4/5 assets in July); hourly decisions keep turnover low | New `trend` rule: long 20% while close > EMA(span); span ∈ {6h,12h,1d,2d,4d} × band ∈ {0, 0.5%}; train only | −0.84…−13.5 (band 0: all < −5) | not run | **REJECTED** — every cell negative alpha on train; whipsaw: 221 round trips / $8.6k fees at span 1d. No region worth validating |
| E06 (9+2 runs) | 2026-10-05 | 4h returns mean-revert in July (autocorr −0.07…−0.15); baseline lost most on entries after a rise | New `reversal` rule: buy 20% when lookback return < −k·σ, hold lookback bars, hourly checks; lookback ∈ {1h,4h,12h} × k ∈ {1,1.5,2}; train | 1h: all < −12; 4h: < 0 except k=2; 12h k≥1.5: 0.7–1.1 (alpha t ≈ 0.2) | 12h/k=1.5: 7.36 / +4.48%; 12h/k=2: 4.56 / +1.70% | **PROMISING** — only region positive on train sent to validation; positive there with beta ≈ 0, alpha t 2.2 |
| E07 (18 runs) | 2026-10-05 | Robustness: is E06 a lone spike? | Neighbourhood lookback ∈ {8h,12h,18h,24h} × k ∈ {1.25,1.5,1.75,2}, plus hold ∈ {6h,24h} | Contiguous positive plateau at lookback ≥ 12h, k ≥ 1.5 (and all k at 24h); negative at 8h and k=1.25 | Positive in all 16 cells (1.1–7.4) | **ACCEPTED: C1 = lookback 1440, k 1.5, hold 1440** — centre of the train plateau with all neighbours positive in both periods (12h/k=1.5 sits on the plateau edge). Train 3.94 / +2.27%, val 4.56 / +1.94%. Validation informed the edge-vs-centre choice |
| E08 (3 runs) | 2026-10-05 | Catastrophic stop limits falling-knife losses | C1 + exit if price falls a further stop·σ below entry, sit out one hold; stop ∈ {1, 1.5, 2} | 0.59 / 3.18 / 3.94 | 3.57 / 4.56 / 4.56 | **REJECTED** — stops hurt or never trigger; dips that deepen usually still revert (losers' MAE −2.3% vs winners' −0.7%) |
| E09 (3 runs) | 2026-10-05 | Equalize risk across assets (XRP/SOL ~2× BTC vol) | C1 + weight = min(20%, budget / daily σ); budget ∈ {0.25%, 0.35%, 0.5%} | 4.43 / 4.36 / 4.03 | 3.52 / 3.42 / 3.74 | **REJECTED** — train up slightly, validation down; lower return; not consistent |
| E10 (3 runs) | 2026-10-05 | Winners peak at median +2.3% but close near +0.8%: bank the reversion | C1 + exit at +tp·σ above entry; tp ∈ {0.5, 0.75, 1.0} | 6.01 / 4.07 / 4.58 | 3.77 / 4.12 / 5.10 | **REJECTED** — non-monotonic in tp (0.75 worse than both neighbours on train), opposite moves on train vs validation at tp 0.5; within noise for 23 / 12 trades |
| F01 | 2026-10-06 | — | C1 frozen; **first and only read of the test period** for it | 3.94 / +2.27% | 4.56 / +1.94% | **FINAL.** Test: Sharpe 4.54, +3.87%, max DD −2.36%, PF 4.07, 22 trades, 5/5 assets ≥ 0. Full: 4.31 / +8.39% / −2.36% |
| E11 (12 runs) | 2026-10-06 | Does F01 survive worse assumptions? | Re-simulated F01 targets: fee 0.15% / 0.20%; slippage 0.05% / 0.10%; fee 0.15% + slip 0.05%; maker 0.05%; 1 / 5 / 60-bar execution delay; $10k / $1M capital. Baseline under harsh costs | all > 2.5 | all > 3.3 | **PASSED** — full period stays +6.1% to +9.6%, Sharpe ≥ 3.2, every period positive. Baseline under harsh costs: −2.96% |
| E12 (20 runs) | 2026-10-06 | Post-freeze sensitivity (reporting only) | Reversal grid lookback {8h,12h,18h,24h,36h} × k {1.25…2} on all four periods | see `E12_sensitivity/summary.csv` | | **INFORMATIVE** — lookback ≤ 12h is negative on test (12h/k=1.5: −4.58); lookback 24h and 36h positive in every cell of every period. The edge is a daily-scale effect |
| F02 | 2026-10-06 | — | Final code after cleanup, parameters from `config.REVERSAL_*`, no CLI params | | | **REPRODUCED** — targets and all metrics identical to F01 |

## Code provenance

E05 (`trend`) and E08–E10 (`stop`, `take_profit`, `risk_budget`) ran with
options that were removed from `strategy.py` after they were rejected. The
exact code is preserved in `experiments/archive/strategy_experimental.py`
(copy it over `strategy.py` to rerun them). Everything else runs on the
current code.

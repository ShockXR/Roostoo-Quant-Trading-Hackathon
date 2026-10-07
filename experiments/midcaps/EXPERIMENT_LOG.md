# Mid-cap experiment log

Same procedure as `experiments/EXPERIMENT_LOG.md` (large caps), on
UNI, AAVE, CRV, ARB, ENA (USDT). Numbers: `experiments/midcaps/<id>/metrics.csv`,
collected in `experiments/registry.csv` (`universe = mid_caps`).

**Protocol.** Identical split: train = Jul 2026, validation = Aug, test = Sep.
Test read only for the baseline, the benchmark and the final candidate.

**Costs.** Fee 0.10% per side (unchanged) + **slippage 0.05% per side**
(large caps: 0.015%). Reason (`validate_data.py --universe mid_caps` and the
tick/liquidity check): half a price tick alone is ~5.5–6 bp for ARB and ENA
and ~2 bp for CRV in Jul–Aug, and median traded value is $0.6k–8.6k per
minute (BTC: $396k). Fills at the next bar's open, portfolio ≤ 20% per
asset, ≤ 70% gross — unchanged.

**Disclosure.** The candidate rule family (`reversal`) was designed on the
large caps before any mid-cap experiment; ME05 tests it unchanged first.

| ID | Hypothesis | Change | Train Sharpe / ret | Val Sharpe / ret | Decision |
|---|---|---|---|---|---|
| ME00 | — | Original repo runner + engine (`backtest_sol.py`), h=10 and h=240, per symbol, full window | — | — | Reference |
| MB00 | — | Buy & hold 20% per asset | 3.93 / +12.92% | 5.17 / +33.47% | Benchmark |
| ME01 | — | Original bot unchanged (features from majors + symbol, as `backtest_sol.py`) | −12.32 / −0.90% | −12.79 / −1.31% | **Baseline.** Full −4.25%, 1,051 trades, gross ≈ +$92 vs $4,338 costs. Event study: +50–55 bp in the hour before entry, ~0 after |
| ME01b | — | ME01 targets at the large-cap slippage (0.015%) | −11.08 / −0.71% | −10.10 / −0.94% | Like-for-like reference: still −3.25% full |
| ME05 | Large-cap rule transfers | `reversal` with config defaults (24h lookback, k 1.5, hold 24h), **unchanged** | 2.59 / +3.04% | 1.63 / +2.68% | **Positive, weaker than large caps** (alpha t 0.21 / 0.06; UNI −2.8% in Aug) |
| ME06 ×30 | Mid-cap reversal parameters | lookback {4h…36h} × k {1…2}, train only | Mostly positive, no clean plateau; 12h row positive at every k (2.5–5.2) | — | 12h region sent to validation |
| ME07 ×9 | Neighbourhood | lookback {8h,12h,18h} × k {1.25,1.5,1.75} | 12h/1.5: 4.43 / +4.5% | 12h: 1.56 / 4.87 / 1.67; **18h: −3.7 / −4.2 / −2.4**; 8h mixed | **12h/1.5 rejected for robustness**: best in-sample, but its neighbour row fails at every k on validation. Candidate stays the unfitted large-cap rule (ME05) |
| ME06b ×10 | Trend following | `trend` span 6h–4d × band 0/0.5%, train | −0.8 … −9.8 (all negative) | — | **Rejected** — whipsaw, up to $17k fees |
| ME08 ×3 | Stop limits falling knives | ME05 + stop 1 / 1.5 / 2 σ | 3.19 / 2.97 / 2.58 | 1.75 / 2.06 / 1.49 | **Rejected (lead)** — small gains both periods (DD −5.3% → −4.3%) but 14–15 trades/period, alpha t ≤ 0.4, and the opposite of the large-cap result |
| ME09 ×3 | Equalize risk | ME05 + vol budget 0.25 / 0.35 / 0.5% | 2.79 / 2.79 / 2.79 | 0.76 / 0.75 / 0.70 | **Rejected** — validation Sharpe roughly halves |
| ME10 ×3 | Bank the reversion | ME05 + take-profit 0.5 / 0.75 / 1 σ | 4.67 / 4.37 / 2.43 | −1.62 / −0.30 / −0.72 | **Rejected** — train up, validation negative at every level (same as large caps) |

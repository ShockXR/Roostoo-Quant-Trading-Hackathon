# Mid-cap model research log (branch `midcap-model-research`)

Branched from `model-research` (large-cap research, frozen). Large-cap
results were re-run after every shared-code change and are unchanged.
Numbers: `experiments/long/mid/<id>/` (`metrics.csv`, `summary.csv`,
`equity.csv`, `trades_*.csv`, `meta.json`).

## 1. Starting point

- **Large-cap winner (Model 3)**: daily, hold the top 3 large caps by
  positive 28-day return at 20% each while BTC > 100-day SMA. It works
  because large caps trend for weeks, the trend is broad-based, and BTC
  leads the cycle. Things that may differ for mid caps: more idiosyncratic
  pumps (fast mean reversion of the latest days), higher volatility and
  dispersion (equal weights mean unequal risk), alt cycles that lag or
  diverge from BTC, ~3× higher trading costs, and listings and delistings.
- **Original baseline (Model 0)**: buy a 1.5σ one-day dip, hold 24h.
  Failed on large caps 2022–26.
- Earlier mid-cap work (`experiments/midcaps/`): 5 hand-picked current mid
  caps, Jul–Sep 2026 only, Model 0 positive but weak. Survivorship-biased
  and single-regime; used here only for its cost estimate (0.05% slippage
  per side).

## 2. Universe (fixed before any backtest)

`scripts/build_midcap_universe.py`:

- Candidates: every Binance spot `*USDT` pair that has ever had monthly
  klines on Data Vision, **including delisted pairs**, minus stablecoins,
  fiat, leveraged tokens (UP/DOWN/BULL/BEAR) and wrapped/staked receipts.
- At the first day of each month M (2022-01 … 2026-09), using only daily
  bars **before** M: eligible pairs were listed ≥ 90 days before M and traded on
  each of the last 30 days; they are ranked by mean daily USDT quote volume
  over the last 90 days. **Ranks 21–60 are the mid caps** (40 per month);
  ranks 1–20 are the large-cap tier.
- Revision before any backtest: the first draft (ranks 11–40, 30-day
  volume) put ADA, AVAX, DOT and DOGE (2022 market-cap top 10) in the mid
  tier and churned with every pump (median stay 2 months, 10 new members a
  month). Volume ranks sit below market-cap ranks, and 90-day volume
  is a steadier size proxy. Final: 307 distinct mid caps over 57 months,
  median stay 4 months, ~9 new members a month, **64 later delisted**.
- Membership is held for the month. A member that is delisted mid-month is
  closed at its last bar; a position in an asset that leaves the universe
  is closed at the month boundary.
- Market-cap history is not available, so liquidity rank stands in for
  size. Volume rank favours actively traded coins (memes, new narratives)
  over dormant ones of the same market cap.
- Pairs that changed ticker (e.g. MATIC→POL) are treated as two assets: the
  old one leaves the universe, the new one can enter after 90 days.

## 3. Protocol

Same split as the large-cap research: train 2022–23, validation 2024, test
2025-01 → 2026-06 (read only for frozen candidates), holdout Jul–Sep 2026
(read once at the end). Fees 0.10% + **slippage 0.05% per side** (base).
Same simulator, limits (20% per asset, 70% gross), next-bar-open fills.
Hourly bars from Binance 1h klines; drawdowns marked hourly (on large caps
minute marks were ~0.75 pp deeper over 3 months; see the report).

## 4. Experiments

Train / validation daily Sharpe unless stated (selection on train; validation must agree).

| ID | Hypothesis | Change | Train | Validation | Decision |
|---|---|---|---|---|---|
| MB00 | — | Buy & hold, members, 70% gross | −0.42 (−51%) | 0.52 (+15%) | Benchmark |
| MA | Large-cap winner transfers | M3 unchanged | 0.30, DD −53% | 1.11, DD −47% | Initial leader |
| MB | Original model | M0, hourly-vol variant | −1.62 | −0.95 | Rejected |
| MC | Other models | M2 (no filter) / M1 | −1.04 / −1.24 | 1.20 / 0.42 | Rejected |
| G10 | Mid caps over-react and revert | dip-reversal lookback 24/72h × k 1.5–3 | only inactive k ≥ 2.5 near 0 | inconsistent | Rejected |
| G11 | Mid-cap optimal lookback / breadth differ | lookback 7–90 × top 3/5/8 | no plateau | 28/3 only solid cell | Keep 28/3 |
| G12 | Alt cycles diverge from BTC | regime BTC / basket / none × SMA 50–200 | none −1.04; basket 200 0.20 | basket 200 1.32, DD −32% | Lead: market-own regime |
| G13 | Pump-and-dump: skip recent days; vol-adjusted score | skip 0/1/3/7 × return/sharpe | skip 1 + sharpe 0.57 | 1.04 | Rejected (no consistent gain, see G17) |
| G14 | Unequal risk / cost of daily trading | equal vs inverse vol × rebalance 1/3/7 | inv-vol 0.41 | 0.99 | Rejected; rebalance 3 d fragile |
| G15 | BTC trend AND mid-cap trend | dual filter, basket 50–200 × BTC 50–200 | basket ≥ 100: 0.23–0.78 | 1.04–1.32; DD 25–41% | **Accepted (MID1: 150/100, centre)** |
| G16 | Is basket-only enough? | basket SMA 150–300 × lookback | 250–300 negative | weaker | Rejected (G12 cell was an edge) |
| G17 | Components on MID1 | inv-vol / skip 1 / sharpe × lookback 14/28/56 | each up on one period | down on the other | Rejected |
| G18 | Thin coins are noise + costly | ranks 21–40 vs 41–60 | 21–40: 0.41/0.78/0.89; 41–60 < 0 | 0.90/1.36/0.76 | **Accepted (MID2)** |
| G19 | Volume confirms momentum | 14 d > 60 d volume | mixed | mixed | Rejected |
| G20 | Plateau inside 21–40 | regime grid | 0.37–1.05 (9/9 > 0) | 1.30–1.67 | Confirmed |
| G21 | Breadth / lookback inside 21–40 | top 2–5 × 21/28/42 | 21 d < 0 at every top_n | all > 0 | Flag: noisy surface |
| G22 | Ensemble removes lookback risk | 3–5 lookbacks averaged | 0.38–0.74 | 0.98–1.08 | Rejected (worse than 28 d) |
| Test | Frozen MA / MID1 / MID2 | first read | | | −39% / −13.5% / −17.3% (B&H −73%) |
| R_* | Robustness | costs, delay, hour, rebalance, size, halves | | | MID2 more robust except own 10-coin halves |
| W_MID2 | Re-tuning | 6-month walk-forward | | | 0.77 vs frozen 0.81 |
| Holdout | Final, once | Jul–Sep 2026 | | | **MID2 −6.2%** (B&H +30.3%) |
| Combo | Large + mid in one account | 50/50 daily | | | Lower Sharpe than large alone in every period → rejected |

## 5. Reproduce

```bash
.venv/bin/python scripts/build_midcap_universe.py list daily members hourly   # ~1 GB, gitignored
.venv/bin/python scripts/run_long.py --universe mid_caps_pit_21_40 --id mid/MID2_full --model mid_momentum \
    --periods train validation test holdout full --no-per-asset
.venv/bin/python scripts/sweep_long.py --universe mid_caps_pit --id mid/G15_regime_both --model mc_momentum \
    --grid 'regime=["both"]' --grid 'regime_sma_days=[50,100,150,200]' --grid 'btc_sma_days=[50,100,200]' \
    --periods train validation
.venv/bin/python scripts/robustness_mid.py MID2
.venv/bin/python scripts/walkforward_long.py --id mid/W_MID2 --universe mid_caps_pit_21_40 --model mid_momentum \
    --grid 'lookback_days=[14,28,56]' --grid 'regime_sma_days=[100,150,200]' --grid 'btc_sma_days=[50,100,200]' \
    --frozen '{}'
.venv/bin/python scripts/run_live.py --once --book mid
```

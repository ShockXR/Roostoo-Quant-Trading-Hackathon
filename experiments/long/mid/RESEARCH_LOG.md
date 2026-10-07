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

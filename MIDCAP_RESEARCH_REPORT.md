# Mid-Cap Model Research Report — branch `midcap-model-research`

Branched from `model-research` (large-cap research). The large-cap model
and its results are unchanged: every shared-code change was followed by
a re-run of the large-cap winner, with identical metrics. Raw numbers:
`experiments/long/mid/`; every hypothesis is logged in
`experiments/long/mid/RESEARCH_LOG.md`.

## Summary

**Mid-cap winner: `mid_momentum` (MID2).** Once a day, hold the 3 mid caps
with the highest positive 28-day return at 20% each, but only while BTC
is above its 100-day average **and** the mid-cap basket is above its
150-day average. It trades only the more liquid half of the mid-cap tier
(Binance liquidity ranks 21–40).

| 2022-01 → 2026-09 | MID2 | Mid-cap buy & hold | Large-cap model transferred |
|---|---|---|---|
| Return / CAGR | **+131% / +19.3%** | −80% / −28.9% | +23% / +4.5% |
| Sharpe / Sortino | **0.64 / 1.05** | −0.37 / −0.50 | 0.34 / 0.54 |
| Max drawdown | **−44%** | −86% | −59% |
| Test 2025-01 → 2026-06 | −17.3% | −73.1% | −39.2% |
| **Final holdout Jul–Sep 2026** | **−6.2%** | +30.3% | +11.1% |

The verdict is **B: the large-cap strategy needs adaptation**, not a
different strategy. The momentum core (28 days, top 3) transfers. Mid caps
need two additions: a second regime filter on the mid-cap market itself,
and a liquidity floor.

The evidence for a mid-cap edge is **weaker than for large caps**:

- Without its best 3 months, MID2 loses 35%.
- It lost money in the out-of-sample test and in the final holdout.
- Combining it 50/50 with the large-cap model did not improve on the
  large-cap model alone.

**The large-cap model remains the competition default**; the mid-cap model
is available as a separate book (`scripts/run_live.py --book mid`).

## A. Git branch

`midcap-model-research`, from `model-research` at `6dc967a`. Not merged.

## B. Baseline models (unchanged, on mid caps)

| Model | Train 2022–23 | Validation 2024 | Test 2025–26H1 |
|---|---|---|---|
| Buy & hold (mid tier, 70% gross) | −51% / Sharpe −0.42 | +15% / 0.52 | −73% / −1.38 |
| A: large-cap winner M3 | +6% / 0.30 / DD −53% | +68% / 1.11 / −47% | −39% / −0.45 |
| B: original dip-buyer M0 (hourly variant) | −87% / −1.62 | −47% / −0.95 | −91% / −2.75 |
| C: M2 momentum, no filter | −86% / −1.04 | +86% / 1.20 | −83% / −1.55 |
| C: M1 dip-buyer + trend filter | −64% / −1.24 | +10% / 0.42 | −73% / −2.39 |

M0 needs the 1-day volatility of 1-minute returns; with hourly data only, it
is approximated from hourly returns. On large caps the approximation trades
~45% more often and does worse (train Sharpe −0.38 vs 0.08 exact), so the
M0 numbers are for a disclosed variant. The whole dip-buying family
(lookback 24/72h × k 1.5–3, G10) fails on mid caps either way.

## C. Mid-cap universe

`scripts/build_midcap_universe.py` → `experiments/long/mid/membership.csv`;
the rule lives in `strategies/universe.py`, shared with the live loop.

- **Candidates:** every Binance spot USDT pair that ever had Data Vision
  klines (680 after excluding stablecoins, fiat, leveraged tokens and
  wrapped/staked receipts), **including delisted pairs**.
- **Monthly, point-in-time:** at the first day of each month, using daily
  bars from before the month only. A pair is eligible if listed ≥ 90 days
  and traded on each of the last 30 days. Eligible pairs are ranked by
  90-day mean USDT volume. **Large = ranks 1–20, mid = ranks 21–60**
  (40 per month); the winner trades ranks 21–40.
- **Size:** 307 distinct mid caps over 57 months; median stay 4 months;
  ~9 new members a month; **64 members were later delisted** and are
  traded until their last bar.
- **Revised once, before any backtest:** ranks 11–40 on 30-day volume put
  2022's market-cap top 10 (ADA, AVAX, DOT, DOGE) in the mid tier and
  churned with every pump.
- **Limitations:** liquidity rank stands in for market cap (no market-cap
  history available); it favours actively traded coins. Ticker changes
  (MATIC→POL) count as two assets. No corporate-action data beyond what
  Binance prices reflect.

## D–E. Data period and quality

Binance 1h klines 2021-06 → 2026-09 for all 307 mid caps plus BTC (2021
used only to warm indicators; membership starts 2022-01); daily klines for
664 pairs (ranking). One parsing defect was fixed: Binance switched
archives to microsecond timestamps in 2025, and one KLAY file mixes both
units. Missing hours inside a coin's life are forward-filled (flat
price). Drawdowns are marked hourly; on large caps, minute marks were about 0.75 pp
deeper over three months, so mid-cap drawdowns are slightly understated.

## F–K. What was tested

| ID | Dimension | Variants | Result |
|---|---|---|---|
| G10 | Dip-reversal family | lookback 24/72h × k 1.5–3 | Rejected — no stable region |
| G11 | Momentum core | lookback 7–90 d × top 3/5/8 | No plateau; 28 d / top 3 the only cell good on validation |
| G12 | Regime proxy | BTC / mid basket / none × SMA 50–200 | Filter essential (none: −86% train) |
| G13 | Signal shape | skip 0/1/3/7 d × return / risk-adjusted score | Rejected |
| G14 | Portfolio | equal / inverse-vol × rebalance 1/3/7 d | Rebalance 3 d much worse than 1 or 7 — timing luck |
| **G15** | **Dual regime** | basket SMA 50–200 × BTC SMA 50–200 | **Accepted** — 9-cell plateau, DD ~50% → 25–40% |
| G16 | Basket-only regime | SMA 150–300 × lookback | Breaks at 250–300 → rejected |
| G17 | Components on MID1 | inverse vol, skip 1, risk-adjusted × lookback | Each helps one period, hurts the other → rejected |
| **G18/G20** | **Liquidity tier** | ranks 21–40 vs 41–60 | **Accepted** — 21–40 better in 5/6 cells; 41–60 negative on train everywhere |
| G19 | Volume confirmation | 14 d vs 60 d volume | Mixed → rejected |
| G21 | top_n × lookback in 21–40 | 2–5 × 21/28/42 | Top 3 / 28 d; **21 d negative on train** (isolated hole) |
| G22 | Lookback ensembles | 3–5 lookbacks averaged | Worse than single 28 d in both periods → rejected |

Features tested: return, risk-adjusted return, recent-return skip,
volatility (weighting), volume trend, liquidity rank, BTC trend, mid-cap
market trend. Kept: return, BTC trend, mid-cap market trend, liquidity rank.

## L–N. Execution and costs

Same simulator as large caps: target decided at the bar close and filled
at the next bar's open; 20% per asset, 70% gross, long only. **Fee 0.10%
+ slippage 0.05% per side** (base), from the earlier tick-size analysis
(half a tick ≈ 5–6 bp on ARB/ENA); large caps use 0.015%. Stressed up to
0.30% per side.

## O. Walk-forward

`W_MID2`: re-selecting lookback × basket SMA × BTC SMA every 6 months by
trailing Sharpe (2023-01 → 2026-06) gave Sharpe 0.77 vs **0.81 frozen**.
From 2024 on, the re-tuning chose a 28-day lookback every time.

## P. Out-of-sample (test 2025-01 → 2026-06, read once for frozen candidates)

| Frozen model | Return | Sharpe | Max DD |
|---|---|---|---|
| Buy & hold | −73.1% | −1.38 | −76.0% |
| MA (M3 transferred) | −39.2% | −0.45 | −58.5% |
| MID1 (dual regime, ranks 21–60) | **−13.5%** | **−0.24** | **−32.8%** |
| MID2 (dual regime, ranks 21–40) | −17.3% | −0.33 | −35.9% |

The dual regime filter generalized; the liquidity filter did not add value
out of sample.

## Q. Regimes (full window, labelled from the point-in-time mid-cap basket)

| Regime | Buy & hold | MA | MID1 | MID2 |
|---|---|---|---|---|
| 2022 | −70% | −30% | −17% | **−3%** |
| 2023 | +64% | +51% | +44% | +59% |
| 2024 | +17% | +70% | +65% | **+91%** |
| 2025 | −59% | −17% | −13% | −17% |
| Strong uptrend days | +245% | +375% | +252% | **+478%** |
| Strong downtrend days | −91% | −70% | −9% | **−7%** |
| Neither (transitions) | −24% | −13% | −49% | **−57%** |

MID2 avoids crashes and captures alt seasons, but loses in transitions:
whipsaw around turning points and late re-entry after bottoms (the holdout).

## R. Liquidity

At competition size ($50k, $10k orders) a MID2 order is a median **0.024%**
of the coin's 30-day average daily volume (max 0.30%). The thinnest coin
ever bought traded $3.4M a day. Market impact is negligible, and the spread
is covered by the slippage assumption. At $1M capital the largest orders
reach 6% of daily volume, so capacity is limited.

## S. Robustness (full-window Sharpe; base 0.64)

| Test | MID2 | MID1 |
|---|---|---|
| Slippage 0.02 / 0.10 / 0.20 / 0.30% per side | 0.66 / 0.61 / 0.55 / 0.48 | 0.46 / 0.40 / 0.34 / 0.28 |
| Fee 0.20% | 0.58 | 0.37 |
| Fill 1h / 6h / 24h late | 0.59 / 0.59 / 0.45 | 0.29 / 0.30 / 0.26 |
| Decision hour 0 / 5 / 11 / 17 (base 23) | 0.49 / 0.73 / 0.57 / 0.57 | 0.34 / 0.52 / 0.55 / 0.47 |
| Rebalance every 3 / 7 days | 0.41 / 0.32 | 0.50 / 0.07 |
| 10% per position | 0.62, DD −25% | 0.41, DD −25% |
| 6 positions × 10% | 0.46 | 0.37 |
| Random half of its universe (×3) | 0.31 / 0.11 / 0.11 (≈10 coins) | 0.29 / 0.49 / 0.48 (≈20 coins) |
| Regime grid, 9 cells (train / validation) | 0.37–1.05 / 1.30–1.67 | 0.23–0.78 / 1.04–1.32 |
| Only coins Roostoo lists today (survivor-biased) | 1.18; test −14%; holdout +7.8% | 1.10 |
| Without best 1 / 3 months | 0.40 (+46%) / −0.10 (−35%) | — |

- **The liquid 20 coins (MID2) beat all three random 20-coin halves of the
  mid tier** on the full window (0.64 vs 0.29 / 0.49 / 0.48). The
  liquidity effect looks real, not a lucky subset.
- **Ten-coin universes are too small:** random halves of MID2 collapse.
  Production trades only Roostoo-listed members (9–13 coins recently);
  the survivor-biased Roostoo-only backtest did fine, but this remains a
  risk.

## T. Failed experiments

Dip reversal (all variants), raw M2 without a filter, basket-only regime,
risk-adjusted score, skipping recent days, inverse-vol weighting, volume
confirmation, lookback ensembles, less frequent rebalancing, the illiquid
half (ranks 41–60), and a 50/50 large + mid book (correlation 0.59;
Sharpe 0.94 vs 1.08 for large alone over the full window, lower in every period).

## U–V. Winner and why

**MID2 (`strategies/mid_momentum.py`, universe ranks 21–40).**

- **Best average across train, validation and test** (Sharpe 0.78 / 1.36 /
  −0.33 vs MID1 0.42 / 1.10 / −0.24).
- **Most robust to costs and delays:** still positive at 0.30% slippage and
  with a 24-hour fill delay.
- **Beats random universes of the same size.**
- **Walk-forward confirms the frozen parameters.**
- **Cheapest to trade in practice** (most liquid coins).

MID1 had the slightly better test result (−13.5% vs −17.3%) and is the
simpler universe; it is the runner-up.

## W. Remaining weaknesses

1. **Concentration:** without its best 3 months (Feb–Mar and Nov 2024),
   MID2 is −35%. Only 13 of 57 months were positive; it is in cash ~69% of
   the time.
2. **Late at bottoms:** the 150-day basket filter missed the start of the
   Jul–Sep 2026 rebound (holdout −6% vs +30% for buy & hold).
3. **Transitions:** whipsaw in directionless markets (−57% on "neither"
   days).
4. **Timing luck:** decision-hour Sharpe ranges 0.49–0.73.
5. **Small live universe:** only ~10 Roostoo-listed members each month.
6. **High volatility while invested:** worst month −22.9% (Apr 2024),
   max drawdown −44%. Halving position size to 10% keeps Sharpe (0.62)
   and halves drawdown (−25%), which is worth considering if it is ever run.

## X. Final frozen out-of-sample result

**Holdout Jul–Sep 2026 (untouched until after the model was frozen):
MID2 −6.2%, Sharpe −0.89, max DD −14.3%**, vs mid-cap buy & hold +30.3%.
The model was not changed after this result.

## Final comparison (full window 2022-01 → 2026-09)

| Model | Variant | CAGR | Return | Sharpe | Sortino | Max DD | Calmar | Volatility | Trades | Turnover | Costs $ | OOS (test / holdout) | Robustness | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Buy & hold | mid tier 21–60 | −28.9% | −80% | −0.37 | −0.50 | −86% | −0.34 | 53% | 569 | 22× | 1,350 | −73.1% / +30.3% | — | Benchmark |
| M0 dip-buyer | hourly-vol variant | −67.2% | −99% | −1.85 | −2.34 | −100% | −0.68 | 53% | 12,267 | 2,583× | 64,660 | −91.1% / −18.3% | Whole family fails | Rejected |
| M1 dip + trend | defaults | −37.1% | −89% | −1.09 | −1.42 | −91% | −0.41 | 36% | 3,279 | 1,071× | 62,373 | −72.6% / +2.0% | — | Rejected |
| M2 momentum | no filter | −46.4% | −95% | −0.57 | −0.82 | −96% | −0.48 | 69% | 878 | 356× | 11,126 | −83.3% / +11.6% | — | Rejected |
| MA = large-cap M3 | transferred unchanged | +4.5% | +23% | 0.34 | 0.54 | −59% | 0.08 | 53% | 500 | 210× | 38,122 | −39.2% / +11.1% | No plateau (G11) | Superseded |
| MID1 | + basket > 150 d, ranks 21–60 | +10.2% | +59% | 0.44 | 0.70 | −44% | 0.23 | 42% | 324 | 134× | 27,314 | **−13.5%** / −8.2% | Regime plateau; stable halves | Runner-up |
| **MID2** | **+ liquid half, ranks 21–40** | **+19.3%** | **+131%** | **0.64** | **1.05** | **−44%** | **0.44** | 40% | 291 | 122× | 34,977 | −17.3% / −6.2% | Costs, delays, walk-forward, beats random halves | **FINAL MIDCAP WINNER** |

MID2 trade stats (full): win rate 41%, profit factor 1.23, average trade
+0.42% of equity, average hold 4.9 days, average exposure 18%. Best month
+58.2% (Mar 2024), worst −22.9% (Apr 2024).

## Production

- `strategies/mid_momentum.py`: the frozen mid-cap model.
- `strategies/universe.py`: the membership rule, shared by research and live.
- `data/binance_universe.py`: live ranking from Binance's public API,
  recomputed daily. Live and research membership agreed on 19–20 of 20
  coins for each month they overlap (Apr–Sep 2026).
- `main.py` / `scripts/run_live.py --book mid`: trades this month's
  Roostoo-listed crypto members, with past months' membership for the basket
  filter. Separate state file; paper trading unless `ROOSTOO_LIVE=1`.
- `config.LIVE_BOOK = "large"`: **the large-cap model stays the default**,
  `config.MIDCAP_MODEL = "mid_momentum"`.

Verified: 42 unit tests pass; an end-to-end mid-book paper decision ran
against live Binance and Roostoo data; the large-cap results and the
large-cap live decision are unchanged.

## Recommendation

For the competition, keep running the large-cap model alone. Run the
mid-cap book only as a deliberate second account or experiment, ideally at
10% per position, and paper-trade it first. Do not merge the two books:
the data do not support combining them.

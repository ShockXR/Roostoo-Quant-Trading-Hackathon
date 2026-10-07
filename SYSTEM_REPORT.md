# How the Roostoo Trading Bot Works — Current State

Branch `live-readiness` (deploy candidate). Written 2026-10-08.
Detailed research: `MODEL_RESEARCH_REPORT.md` (large caps, this branch),
`MIDCAP_RESEARCH_REPORT.md` (branch `midcap-model-research`), and the
2018–2021 test (branch `oos-2018-2021`, `experiments/long/oos/`).

## 1. In one paragraph

The bot trades a **trend-following momentum strategy on nine large-cap
coins** (BTC, ETH, BNB, SOL, ADA, XRP, DOT, AVAX, DOGE) on Roostoo. Once a
day it holds the **three coins with the strongest positive 28-day return,
20% of the account each**, but only while **Bitcoin is above its 100-day
average**; otherwise it holds cash. It checks every hour and trades about
once or twice a week. It is built to avoid bear markets and ride sustained
rallies, not to trade every move. It currently runs in **paper mode**; real
orders need API keys and one switch.

## 2. The strategy (`strategies/m3_momentum_regime.py`)

Every day at the close of the 23:00 UTC hourly bar:

1. **Market filter.** Is BTC's close above its 100-day simple moving
   average? If not, the target is 0% in everything (cash).
2. **Score.** For each of the nine coins: return over the last 28 days.
3. **Select.** Keep coins with a positive score; take the three highest.
4. **Size.** 20% of equity each (60% total; hard caps 20% per coin, 70% gross).
5. **Hold** until the next daily decision. A coin is only traded when its
   target changes; between decisions positions drift with price, without
   rebalancing.

Three parameters (28 days, top 3, 100-day filter), all chosen before
seeing out-of-sample data and not re-tuned since. Long only, no leverage,
no stop-loss (tested: it hurt).

**Why it should work:** large-cap crypto trends for weeks at a time, so the
leaders of the last month tend to keep leading. Bear markets are where
momentum strategies lose most, and BTC's 100-day trend is a slow, reliable
"is the market in a bear phase" signal, so the filter keeps the bot out of
most of them.

## 3. How the live bot runs (`main.py`, `scripts/run_live.py`)

```
every hour, 30 s after the hour closes
  ├─ data      Binance public API → last 130 days of finished hourly bars, 9 coins
  │            (data/binance_bars.py; Roostoo prices track Binance within a few $)
  ├─ signal    strategies.generate("m3_momentum_regime") → target weights
  │            (exactly the backtest code; verified identical on the same bars)
  ├─ guard     stale data (latest bar not the previous hour) → skip this hour
  ├─ account   Roostoo balance + ticker → equity
  ├─ orders    execution/rebalance.py → MARKET orders for changed targets only:
  │            quantity floored to the pair's precision, skip < $1 MiniOrder,
  │            sells before buys, buys trimmed to available cash
  ├─ execute   PaperBroker (default) or Roostoo /v3/place_order (ROOSTOO_LIVE=1)
  └─ record    data_store/live_state.json (targets, peak equity)
               data_store/live_decisions.jsonl (one line per hour)
```

- **When it trades:** targets change only at the daily decision, so orders go
  out at about **00:00:30 UTC** when the top three change or the regime flips.
  Other hours do nothing, unless an order failed (retried next hour) or the
  bot just started and holdings are more than 2% off target.
- **Failure handling:** API errors are retried. A failed order's target isn't
  recorded as done, so it's retried. Any exception is logged and the loop
  continues next hour. systemd restarts the process if it dies
  (`deploy/roostoo-bot.service`).
- **Kill switch:** implemented and **off**. A 12% drawdown stop cut the
  average 14-day return in backtests without improving the worst cases.
- **Current decision** (bar 2026-10-07 18:00 UTC): BTC above its 100-day
  average, holding **SOL, ADA, AVAX at 20% each**, 40% cash.

## 4. Evidence it works

All results: $100k fresh capital, fee 0.10% + slippage 0.015% per side, fills at the
next bar's open, drawdowns marked on minute prices (2022–26).

| Window | Universe | Return | CAGR | Sharpe | Max DD | Buy & hold (same coins) |
|---|---|---|---|---|---|---|
| **2018-04 → 2021-12** (second out-of-sample, never used for design) | point-in-time top 10 by volume | **+1,208%** | +98% | **1.56** | **−37.8%** | +398%, Sharpe 0.95, DD −73.7% |
| 2022-01 → 2023-12 (train) | 2022 top 10 incl. LUNA | +95% | +40% | 1.26 | −22% | −30% |
| 2024 (validation) | same | +119% | +118% | 1.89 | −30% | +68% |
| **2025-01 → 2026-06** (test, read once) | same | **−8.8%** | −6% | −0.16 | −21% | **−43%** |
| Jul–Sep 2026 (holdout) | same | +8.3% | — | 1.25 | −16% | +37% |
| **2022-01 → 2026-09 (all)** | same | **+312%** | +35% | **1.08** | **−38%** | −14%, Sharpe 0.18, DD −61% |

By year (2018–21 then 2022–26): 2018 **−20%** (market −50%), 2019 +44%,
2020 +119%, 2021 +414%, 2022 **−13%** (market −59%), 2023 +124%, 2024
+114%, 2025 **−6%** (market −23%), 2026 to Sep +5%.

**Robustness**

- **No knife-edge settings:** all 25 nearby parameter settings were profitable
  in both 2022–23 and 2024.
- **Survives harsher execution:** doubled fees with 0.10% slippage gives
  Sharpe 0.92; fills a full day late give 0.90.
- **Doesn't depend on any one coin:** dropping any single coin keeps Sharpe
  between 0.93 and 1.22.
- **Re-tuning doesn't help:** re-picking the parameters every 6 months did
  worse than freezing them (1.16 vs 1.32).
- **The edge is statistically meaningful but not huge:** alpha t-stat +2.40
  (2018–21) and +2.45 (2022–26).
- **Not driven by one lucky month:** without its 3 best months, 2018–21 is
  still +186% (Sharpe 0.89). In 2022–26, without Nov 2024 it is +83%
  (Sharpe 0.58).

**Alternatives that lost:** the original ML bot (lost money in every
period), the 2026 dip-buying rule (−55% over 2022–26; Sharpe 0.26 in
2018–21), momentum without the BTC filter (−59% drawdown), a version with
shorts, volatility targeting, lookback averaging, a 12% kill switch, and
combining with the mid-cap strategy.

## 5. What to expect in a 14-day competition

The competition scores 0.4·Sortino + 0.3·Sharpe + 0.3·Calmar over 14 days.

| Over all 14-day windows | 2018–21 | 2022–26 |
|---|---|---|
| Windows with a positive return | 38% | 28% |
| Worst 5% of windows | −9.2% or worse | −6.9% or worse |
| Average 14-day return | — | +1.5% |

- **Trending up:** likely a good result. It holds the leaders with only 60% invested.
- **Choppy / directionless:** likely a small loss from whipsaw. This is its
  weakest regime.
- **Falling:** mostly cash after the BTC filter turns off. Small loss or flat,
  where buy & hold loses heavily.
- **Strong rally from a low:** it lags, because the 100-day filter turns on late.

The bot is in cash for much of the time, so many 14-day windows show near-zero
return; the 14-day win rate understates it. Its advantage is lower risk, not
beating buy & hold in every rally.

## 6. Known risks and limitations

1. **Returns come in bursts:** a few trending months produce most of the gains.
2. **Late at turning points:** it exits only after BTC breaks its 100-day
   average, and re-enters late after bottoms.
3. **Concentration:** 60% in three correlated coins; the worst day seen was −13%.
4. **Decision-time luck:** the 23:00 UTC decision time was the best of five
   tested; expect a Sharpe nearer 0.95 than 1.08 going forward.
5. **Roostoo execution not yet verified with keys:** balance format and the
   meaning of `MiniOrder` ($ vs units) are assumptions (step 7).
6. **Data source:** signals use Binance prices; Roostoo's prices tracked them
   within a few dollars on the day this was checked.
7. **Backtest gaps:** no market impact (negligible at $50k: orders are about
   0.02% of daily volume), no exchange outages, no lot sizes beyond precision.

## 7. Status

| Item | State |
|---|---|
| Strategy frozen and tested on three separate periods | done |
| Live loop wired to the strategy (paper mode) | done: end-to-end decision runs against live Binance + Roostoo |
| Live targets = backtest targets on the same bars | verified (2026-09-30 23:00; 48-hour simulated log: 48/48) |
| Hourly decision log + reconciliation tool | done (`scripts/reconcile_live.py`) |
| Tests | 35 unit tests passing on this branch |
| API keys on the server; `scripts/check_account.py` | **pending (you)** |
| `scripts/test_min_order.py` (MiniOrder meaning) | **pending (you)** |
| 2–3 days paper trading + `reconcile_live.py` | **pending** |
| Merge to deploy branch (`git merge --squash live-readiness`) | **your decision** |
| `ROOSTOO_LIVE=1` before the competition | **pending** |

## 8. Commands

```bash
.venv/bin/python scripts/run_live.py --once      # one paper decision, printed
.venv/bin/python scripts/run_live.py             # hourly loop (paper unless ROOSTOO_LIVE=1)
.venv/bin/python scripts/check_account.py        # keys + balance format, no orders
.venv/bin/python scripts/test_min_order.py       # tiny orders on the mock exchange
.venv/bin/python scripts/reconcile_live.py       # paper/live log vs backtest
.venv/bin/python -m unittest discover -s tests   # tests
```

## 9. Branches

| Branch | Contents |
|---|---|
| `Samyak` | your original work (untouched) |
| `model-research` | large-cap research; Model 3 chosen and wired to live |
| `live-readiness` | model-research + decision log, reconciliation, account check (**deploy candidate**) |
| `midcap-model-research` | mid-cap research and optional `--book mid` (not recommended for the competition) |
| `oos-2018-2021` | second out-of-sample test (frozen Model 3 on 2018–2021) |

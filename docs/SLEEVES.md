# Three-sleeve book (2026-10-10)

Replaces the dual-sleeve bot that traded live from `main` between Oct 6 and Oct 10 (about -2.5% on the leaderboard at day 6). Runs with `--strategy sleeves` (`sleeves_live.py`, `strategies/sleeves.py`). Everything here is built from components that earlier research on this repository tested; the two pieces that were not tested are marked.

## Why the live bot lost money

Read from the code on `main` (`main.py`, `config.py` at `e90d151`). The fill-level evidence is in `data_store/trades_log.csv` and `logs/bot.log` on the EC2 instance; `scripts/review_live_trades.py` turns them into a per-trade report.

1. **Large caps used the dip-buyer that research had already rejected.** Entries fired when a coin fell more than 1.5 standard deviations in a day (`strategy._reversal`). On 2022-2026 history this rule lost 55% (Sharpe -0.47, drawdown -66%) and had no positive parameter setting on the training years (`MODEL_RESEARCH_REPORT.md` on branch `live-readiness`). It buys falling knives.
2. **Exits gave small wins and large losses.** Profit was locked once a coin rose about 0.5 daily standard deviations (often under +0.5% net), while stops sat 3 daily standard deviations below entry for large caps and 3.5 for mid caps (about -6% for BTC, more than -10% for the alts). With wins of +0.4% and losses of -6% or worse, it needs a win rate above about 90% to break even after fees.
3. **The deep-dip booster averaged down.** After a 5% daily fall, AVAX, ADA, SUI and ENA were topped up to 7% of capital, and the stop was recomputed from the lower average price, so the position got bigger and the stop got lower in the same move.
4. **Execution bookkeeping trusted itself instead of the exchange.** A sell removed the position from tracking whether or not the order filled, the next cycle re-adopted the wallet balance with a new stop, every trade was sized from a fixed $100k rather than real equity, and order quantities were rounded with `%.1f`/`%.2f`, which can round a sell above the balance.

BTC and ETH show zero holdings because large caps were only ever bought after a sharp fall and sold on the first small gain or the wide stop. `review_live_trades.py` shows which exit happened for each trade and how prices moved 24h and 72h afterwards.

## The new book

Rebalanced once a day after the 00:00 UTC hourly close on Binance. Long only, no leverage, market orders, at most 70% of equity invested.

| Sleeve | Universe | Signal | Sizing (recovery mode) | Source of evidence |
|---|---|---|---|---|
| Large | BTC ETH SOL BNB XRP ADA DOGE LINK AVAX DOT LTC SUI | Top 3 by 28-day return, positive only, equal weight. Held only while BTC is above its 100-day average; otherwise cash. | 0.8% daily-vol target, at most 15% per coin, 35% for the sleeve | Model 3, branch `live-readiness`: Sharpe 1.08 over 2022-26 (25 of 25 nearby settings positive on train, validation and the full period), but the 2025-26 out-of-sample test was **-8.8%** (Sharpe -0.16, buy and hold -43%). Runs at about 0.2-0.26 gross here vs 0.6 in research, so expect roughly a third of research returns and drawdowns |
| Mid | 22 coins: the 17 blend_v10 was researched on (ENA FET WLD UNI TAO ONDO AAVE VIRTUAL HBAR ARB ICP FIL SEI APT ASTER XLM PUMP) plus 5 thinner coins the old bot traded (CRV PENDLE CAKE POL CFX) | `blend_v10`: average of top 5 by 24h return and top 3 by 168h return, positive only, inverse-volatility weights. Half size while BTC is below its 100-day average. | 0.8% daily-vol target, at most 8% per coin, 30% for the sleeve | [MIDCAP_RESEARCH.md](MIDCAP_RESEARCH.md): positive in research, validation and holdout, still positive at 2x costs |
| Small | PEPE SHIB BONK WIF FLOKI PENGU TRUMP 1000CHEEMS EIGEN BIO | Top 2 by 168h return, kept only if the 24h return is also positive. Only while BTC is above its 100-day average. | 0.25% daily-vol target, at most 2% per coin, 5% for the sleeve | Same momentum family; **sleeve itself untested** |

Current holdings that belong to a sleeve are re-scored by that sleeve's signal at the first rebalance: kept or topped up if they rank, sold if they don't. Coins held outside every sleeve are sold.

**Risk controls**
- *Volatility targets* size each sleeve from the covariance of the last 168 hourly returns, so a calm market gets more capital and a volatile one less. In research this changed drawdown in proportion to size but left the ratios unchanged, which is what a Sortino/Calmar-weighted score wants.
- *Staged drawdown de-risk* (unchanged): beyond 3% below the bot's equity high, positions are cut to half; beyond 6%, to 30%. Applied at once, restored at the next daily rebalance, never zero. The high is the new engine's own, starting from equity at the switch. Whether the 3% stage helps the score is open (see the table and step 4 below).
- *No stop-loss on large or mid caps.* Model 3 research tested one and it hurt; the old bot's stops are part of why it lost.
- *Small caps only:* a hard stop 10% below the average entry price (checked every minute), then a 72-hour lockout; no new entry when Roostoo's bid/ask spread is wider than 0.30%. Worst case on the full 5% sleeve is about 0.5% of equity per stop.
- *Recovery and push modes:* sizes above apply while equity is at or below $100k. Once equity is above $100k at a daily rebalance, large-cap sizes are multiplied by 1.5 and mid-cap by 1.25 (caps still apply); small caps never scale up. Decided once a day so it cannot flip back and forth intraday. **The multipliers are a team choice, not a research result.**
- *Activity:* if nothing has filled by 12:00 UTC, the largest drift from target is traded (0.1% deadband), preferring a sell because the cash and exposure checks never hold one back; a refused or held-back order is retried next minute, so every day has a strategy trade. 12:00 UTC is before the end of the day in UTC, HKT (UTC+8) and AEDT (UTC+11), so the trade counts whichever of these the organizers use (US time zones are not covered). On a day when the book is exactly on target, or entirely in cash because every sleeve is off, this logs a warning and does nothing. The first day of an all-cash spell still trades (the sell-down), so only a second consecutive all-cash day goes without a fill.

**Execution** is the mid-cap engine's: the wallet is re-read from Roostoo every minute, a fill is never assumed, sells go before buys, quantities are floored to the pair's precision, an order the exchange refuses is skipped, and state survives restarts. The balance parser now accepts both the `SpotWallet`/`Frozen` layout the live account returned to the old bot and the documented `Wallet`/`Lock` layout.

- *API budget:* a long-only book does not read `/v6/short_positions` (only with `ALLOW_SHORTS`), so each order costs 5 calls. Cycles alternate between about 5 orders and none, at most 40 orders per rebalance; a rebalance is abandoned after 30 cycles. A 29-order switchover takes about 11 minutes.
- *Binance outage at the rebalance:* if the signal fetch fails (both hosts down, or a gap in the history of BTC or a held coin), the engine logs an error, keeps the previous day's targets and retries every minute. Small-cap stops, the drawdown de-risk and the activity trade keep running. With no earlier targets (the first start on a new state file) it trades nothing until the fetch works.
- *Dust after a stop:* a small-cap residue worth less than the minimum order is treated as flat, so the stop cannot fire again on it and re-bar the coin.

## What is and isn't tested

A red-team review on 2026-10-10 checked this table and the code; findings and what was done about each are in [REDTEAM_2026-10-10.md](REDTEAM_2026-10-10.md).


| Component | Status |
|---|---|
| Large-cap signal (Model 3) | Researched 2022-2026 on Binance data; its out-of-sample test (2025-26) was negative but far better than buy and hold. A 2018-21 run is cited in SYSTEM_REPORT.md on a branch not fetched here |
| Mid-cap signal (blend_v10) | Researched Apr-Oct 2026, one holdout |
| BTC regime gate on mid caps | Supported by 2022-26 mid-cap research for 28-day momentum (`MIDCAP_RESEARCH_REPORT.md`); **not tested on blend_v10**, whose own research found a different gate (the sleeve-index crash gate) did not help. `sleeves_backtest.py` reports it with and without |
| Mid universe 22 vs the researched 17 | **Untested.** The 5 extra coins are outside Binance's top 60 by volume in Jul-Sep 2026, and the ranks 41-60 tier lost money on train in the 2022-26 mid-cap research; they were kept so current holdings are re-scored rather than dumped. `sleeves_backtest.py` reports the researched-17 variant |
| Small-cap sleeve, stop, spread guard | **Untested.** The nearest committed analogues (7-day momentum on mid caps, the thinnest liquidity tier) lost money on 2022-23 training data and were mixed later. Kept to 5% of equity (about 4% in practice) for that reason |
| Push multipliers | **Untested**, team choice |
| Staged drawdown de-risk (3%/6%) | Mid-cap engine replay, twelve 14-day windows Apr-Oct 2026: mean return 2.1% with it vs 3.3% without, worst drawdown -4.7% vs -6.9% ([LIVE_READINESS.md](LIVE_READINESS.md)). **Effect on the composite not measured.** Synthetic red-team runs (not market evidence) found the 3% stage fires in most 14-day windows and lowers the median composite; a single 6% stage kept most of the protection in sustained losses. Compare on real data with step 4 below |
| Code: signals, no look-ahead, live = backtest weights, engine behaviour (including the activity trade across UTC/HKT/AEDT days, the Binance-outage fallback, dust after a stop, switchover order throughput) | `tests/test_sleeves.py` (offline, synthetic data) |

Nothing here has been run on real October 2026 prices yet: the session that wrote it had no access to Binance or Roostoo. Run the steps below on the EC2 instance before going live.

## Before switching the live bot

```sh
# 1. What the old bot actually did, trade by trade. Its service ran in /home/ubuntu, so its files are
#    probably ~/data_store/trades_log.csv and ~/roostoo_bot/logs/bot.log (find ~ -name trades_log.csv)
python -m roostoo_bot.scripts.review_live_trades --trades ~/data_store/trades_log.csv \
    --log ~/roostoo_bot/logs/bot.log --out logs/trade_review.md
# 2. The new book on recent Binance history, against its parts and the previous mid-cap blend
python -m roostoo_bot.scripts.sleeves_backtest --start 2026-04-01 --out logs/sleeves_backtest.md
# 3. A paper start: computes today's targets from live data and prints them, no orders
timeout 120 python -m roostoo_bot.main --strategy sleeves 2>&1 | grep -E "rebalance|dropped|not tradable" 
# 4. The drawdown de-risk on the same history (step 2 used the current 3%/6% schedule): one 6% stage, and none
python -c "import sys; from roostoo_bot import config; config.MIDCAP_DRAWDOWN_SCHEDULE = ((0.06, 0.3),); sys.argv = ['x', '--start', '2026-04-01', '--out', 'logs/sleeves_dd_6.md']; from roostoo_bot.scripts import sleeves_backtest; sleeves_backtest.main()"
python -c "import sys; from roostoo_bot import config; config.MIDCAP_DRAWDOWN_SCHEDULE = None; sys.argv = ['x', '--start', '2026-04-01', '--out', 'logs/sleeves_dd_none.md']; from roostoo_bot.scripts import sleeves_backtest; sleeves_backtest.main()"
# 5. Read-only, competition keys in .env, no orders. Prints the raw balance and pending replies the live broker
#    parses (the old bot never called pending_count): with nothing pending the second must contain "TotalPending": 0,
#    and the wallet must be under SpotWallet or Wallet with Free and Frozen or Lock, or every live cycle stops.
#    Then computes today's targets with every held coin required, as the first live rebalance will: a gap in the
#    Binance history of BTC or a held coin raises here, and live the bot would trade nothing until it cleared.
python - <<'EOF'
import time
from roostoo_bot import config, sleeves_live
from roostoo_bot.execution import trader
from roostoo_bot.midcap_live import latest_rebalance_hour
reply = trader.get_balance()
print(reply)
print(trader.get_pending_count())
wallet = reply.get("SpotWallet") or reply.get("Wallet")
held = {coin + "/USD" for coin, v in wallet.items() if coin != "USD" and float(v.get("Free", 0)) > 0}
sleeves = sleeves_live.tradable_sleeves(trader.get_exchange_info()["TradePairs"])
hour = latest_rebalance_hour(int(time.time() * 1000), config.MIDCAP_REBALANCE_EVERY_HOURS, config.MIDCAP_REBALANCE_DELAY_MS)
print(sleeves_live.binance_sleeve_weights(sleeves)(hour, held))
EOF
```

**Reading the results.** From about $97.5k with 7 days left, the competition score's sign is almost always the sign of the final return (red-team scoring analysis, synthetic), so judge choices by the chance of finishing above $100k and by the median 14-day composite, not by the mean composite, which a few huge rallies dominate under compounded Calmar. If `mid sleeve, researched 17 coins only` beats the 22-coin mid sleeve on the 14-day blocks, drop CRV, PENDLE, CAKE, POL and CFX from `SLEEVE_PAIRS["mid"]` in its own commit (holdings are then sold as leftovers); decide the mid regime gate the same way from `mid sleeve, no BTC gate`.

Go live only if the backtest shows the book positive on the whole window and in most 14-day blocks, the paper targets look sensible, and step 5 prints targets without an error. If, for the `sleeves (recovery sizing` variant, the 14-day blocks in `logs/sleeves_dd_6.md` show a higher median composite and at least as many positive blocks as `logs/sleeves_backtest.md`, set `MIDCAP_DRAWDOWN_SCHEDULE = ((0.06, 0.3),)` in `config.py` in its own commit (the mid-cap runner shares it). Otherwise keep 3%/6%. Switchover steps are in [DEPLOYMENT.md](../DEPLOYMENT.md).

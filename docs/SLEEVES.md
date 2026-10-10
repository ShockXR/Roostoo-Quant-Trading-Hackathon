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
| Large | BTC ETH SOL BNB XRP ADA DOGE LINK AVAX DOT LTC SUI | Top 3 by 28-day return, positive only, equal weight. Held only while BTC is above its 100-day average; otherwise cash. | 0.8% daily-vol target, at most 15% per coin, 35% for the sleeve | Model 3, branch `live-readiness`: 2018-21 and 2022-26 out of sample, 25 of 25 nearby settings positive, Sharpe about 1.0 |
| Mid | 22 coins: the 11 the live bot traded (UNI AAVE CRV PENDLE CAKE ONDO ENA ARB POL SEI CFX) plus FET WLD TAO VIRTUAL HBAR ICP FIL APT ASTER XLM PUMP | `blend_v10`: average of top 5 by 24h return and top 3 by 168h return, positive only, inverse-volatility weights. Half size while BTC is below its 100-day average. | 0.8% daily-vol target, at most 8% per coin, 30% for the sleeve | [MIDCAP_RESEARCH.md](MIDCAP_RESEARCH.md): positive in research, validation and holdout, still positive at 2x costs |
| Small | PEPE SHIB BONK WIF FLOKI PENGU TRUMP 1000CHEEMS EIGEN BIO | Top 2 by 168h return, kept only if the 24h return is also positive. Only while BTC is above its 100-day average. | 0.25% daily-vol target, at most 2% per coin, 5% for the sleeve | Same momentum family; **sleeve itself untested** |

Current holdings that belong to a sleeve are re-scored by that sleeve's signal at the first rebalance: kept or topped up if they rank, sold if they don't. Coins held outside every sleeve are sold.

**Risk controls**
- *Volatility targets* size each sleeve from the covariance of the last 168 hourly returns, so a calm market gets more capital and a volatile one less. In research this changed drawdown in proportion to size but left the ratios unchanged, which is what a Sortino/Calmar-weighted score wants.
- *Staged drawdown de-risk* (unchanged): beyond 3% below the bot's equity high, positions are cut to half; beyond 6%, to 30%. Applied at once, restored at the next daily rebalance, never zero.
- *No stop-loss on large or mid caps.* Model 3 research tested one and it hurt; the old bot's stops are part of why it lost.
- *Small caps only:* a hard stop 10% below the average entry price (checked every minute), then a 72-hour lockout; no new entry when Roostoo's bid/ask spread is wider than 0.30%. Worst case on the full 5% sleeve is about 0.5% of equity per stop.
- *Recovery and push modes:* sizes above apply while equity is at or below $100k. Once equity is above $100k at a daily rebalance, large-cap sizes are multiplied by 1.5 and mid-cap by 1.25 (caps still apply); small caps never scale up. Decided once a day so it cannot flip back and forth intraday. **The multipliers are a team choice, not a research result.**
- *Activity:* if nothing has filled by 20:00 UTC, the largest drift from target is traded (0.1% deadband) so every day has a strategy trade. On a day when the book is exactly on target this logs a warning and does nothing.

**Execution** is the mid-cap engine's: the wallet is re-read from Roostoo every minute, a fill is never assumed, sells go before buys, quantities are floored to the pair's precision, an order the exchange refuses is skipped, and state survives restarts. The balance parser now accepts both the `SpotWallet`/`Frozen` layout the live account returned to the old bot and the documented `Wallet`/`Lock` layout.

## What is and isn't tested

| Component | Status |
|---|---|
| Large-cap signal (Model 3) | Researched 2018-2026 on Binance data, out of sample twice |
| Mid-cap signal (blend_v10) | Researched Apr-Oct 2026, one holdout |
| BTC regime gate on mid caps | Supported by 2022-26 mid-cap research for 28-day momentum (`MIDCAP_RESEARCH_REPORT.md`); **not tested on blend_v10**. `sleeves_backtest.py` reports it with and without |
| Small-cap sleeve, stop, spread guard | **Untested.** Kept to 5% of equity for that reason |
| Push multipliers | **Untested**, team choice |
| Code: signals, no look-ahead, live = backtest weights, engine behaviour | `tests/test_sleeves.py` (offline, synthetic data) |

Nothing here has been run on real October 2026 prices yet: the session that wrote it had no access to Binance or Roostoo. Run the two scripts below on the EC2 instance before going live.

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
```

Go live only if the backtest shows the book positive on the whole window and in most 14-day blocks, and the paper targets look sensible. Switchover steps are in [DEPLOYMENT.md](../DEPLOYMENT.md).

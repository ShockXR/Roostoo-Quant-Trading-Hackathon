# Live readiness (2026-10-05)

What stands between `main` and a bot that trades autonomously on the competition account. The deployed EC2 instance runs the `Samyak` branch, which cannot place orders (hard-coded `DRY_RUN = True`, no model loaded, capital fraction passed as an order quantity), so it must be redeployed from `main` once the items below are resolved.

## Done in phase 1 (branch `phase1-scoring`)

- `scoring.py`: daily composite score `0.4*Sortino + 0.3*Sharpe + 0.3*Calmar`. Unconfirmed conventions are settings (`ScoreConventions`); see [ORGANIZER_QUESTIONS.md](ORGANIZER_QUESTIONS.md). Undefined ratios are reported as `None` with a reason, not replaced with 0.
- The engine appends one equity mark per cycle to `logs/equity.jsonl`.
- `scripts/live_score.py`: running composite, its three parts, max drawdown, and fills per day with an active-day count against the 8-day rule. Compare its composite with the Roostoo app leaderboard each day; a gap points to a convention mismatch.

  ```sh
  python -m roostoo_bot.scripts.live_score --mode live
  ```

- Backtest summaries now include `daily_composite`, `daily_sortino`, `daily_sharpe`, `daily_calmar` and `n_days` next to the older bar-level metrics. Phase 3 should rank strategies on `daily_composite`; `scripts/sweep.py` still ranks on the bar-level `composite_score`.
- Live broker: an uncertain order (ambiguous response) no longer blocks the bot forever. The flag clears on the next cycle once the exchange reports no pending orders and no locked balances, because live holdings are re-read from the exchange every cycle. Before, clearing it needed a person, which the rules prohibit. Paper mode keeps the old blocking behavior.
- Live broker: tiny negative balances from sell rounding (FAQ Q24) worth under $1 are ignored instead of failing every cycle. Larger negative balances still stop the cycle.

## Done in phase 4 (branch `phase1-scoring`)

- `--strategy midcap` runs the mid-cap sleeve (`midcap_live.py`): the two-lookback momentum blend at a 1.5% daily volatility target, 20% per coin, 70% gross, rebalanced once a day after 00:00 UTC.
- Signals come from Binance's public hourly klines (`data/binance_live.py`, with `data-api.binance.vision` as a fallback host), so there is no warm-up after a restart. A test checks that live weights equal the backtest's weights for the same hour.
- The latched 12% halt is replaced for this sleeve by a staged de-risk from the equity high-water mark: half size beyond a 3% drawdown, 30% beyond 6%. Size is never cut to zero, so the bot keeps trading every day. Cuts trade at once; size comes back only at the next daily rebalance and only if drawdown has recovered.
- A restart restores holdings and rebalance state from `data_store/midcap_state.json` and catches up on a missed rebalance. A rebalance that cannot finish (for example repeated rejections) is abandoned after 15 cycles until the next day.

## Done in phase 5 (checks run on 2026-10-05)

- **History replayed through the live engine** (`scripts/shadow_replay.py`): one-minute Binance closes fed to `MidcapEngine` as if they were Roostoo quotes, compared with the backtest on the same hours.
  - Without the drawdown schedule the two agree almost exactly: +9.62% engine vs +9.68% backtest for Sep 1 - Oct 4, with 243 fills in both. Fill timing (2 minutes after the hour vs 5), the paper broker's fee and slippage, lot rounding and exposure checks make no material difference.
  - With the schedule they can diverge in a window where a de-risk fires, because the engine checks drawdown every minute and the backtest every hour, so cuts and re-entries land at different times. Across twelve 14-day windows (Apr 8 - Oct 5): mean gap -0.07%, worst +/-3.2%, in both directions. Mean 14-day return 2.1% (engine), 2.2% (backtest), 3.3% (no schedule); worst drawdown -4.7%, -4.8%, -6.9%. In other words the schedule costs roughly a third of the return for roughly two points less worst-case drawdown.
- **Live order path against a fake exchange** (`tests/test_midcap_exchange.py`), through the real trader wrappers and rate limiter:
  - A seven-coin rebalance finishes in about three minutes, never above 30 API calls in any minute; idle cycles use 4 calls.
  - An order that fills but whose response is lost is reconciled on the next cycle with no duplicate buy.
  - A definitive rejection (`Success: false`) used to block every coin after it in the list; it is now logged and skipped, retried once, then given up for that rebalance (`MIDCAP_MAX_REJECTIONS_PER_PAIR`), and the other coins still trade.
  - A drawdown cut sells proportionally on the exchange, leaving positions inside the 1% no-trade band alone.
- **Paper run on live feeds** (earlier, 5 minutes): seven buys matched targets, no errors.

Not covered by anything run so far: the real signed endpoints (no competition keys were used), the exact fee, Binance reachability from the Sydney instance, and a real 00:00 UTC rebalance on live data.

## Paper run on EC2 (before switching live on)

1. Merge `phase1-scoring` into `main`, push, then on the instance: `git pull`, `pip install -r requirements.txt`.
2. Check Binance reachability from the instance: `curl -s "https://api.binance.com/api/v3/klines?symbol=ENAUSDT&interval=1h&limit=2"` (and the `data-api.binance.vision` host). The signal needs one of them.
3. Start paper mode in tmux: `python -m roostoo_bot.main --strategy midcap`. It uses the public ticker only and needs no keys.
4. Leave it through one 00:00 UTC rebalance. Then check: `logs/events.jsonl` has one BUY per target coin with quantities matching `target_fraction`; `python -m roostoo_bot.scripts.live_score --mode paper` shows equity marks every minute; no `ERROR` lines in the log.
5. Restart it once (stop and start) mid-day and confirm it resumes from `data_store/midcap_state.json` without re-buying.
6. Only then change the two live switches (`DRY_RUN` in `main.py`, `LIVE_TRADING_ENABLED` in `config.py`) in their own commit, set `ROOSTOO_API_KEY` and `ROOSTOO_API_SECRET` to the competition keys, and start with `--state data_store/midcap_live_state.json`.
7. After the first live rebalance: compare each fill's `fee` with the 0.1% assumption, compare `live_score` equity with the Roostoo app, and confirm the app shows the orders.

## Blockers before going live

1. **Deployment.** The instance still runs the `Samyak` branch. `phase1-scoring` must be merged into `main` and pushed first.
2. **Paper run on EC2** through one 00:00 UTC rebalance (checklist above).
3. **Two live switches.** `DRY_RUN` in `main.py` and `LIVE_TRADING_ENABLED` in `config.py` must both change, in their own commit, after the paper run.

## Known limits to keep in mind

- **Pending orders stop the bot.** Any pending order or locked balance makes every cycle stop until it clears. The bot only sends MARKET orders, which the API documentation shows as filled immediately, but nothing cancels a stuck order automatically.
- **Assets outside the sleeve stop the bot.** A held coin that is not in `config.MIDCAP_PAIRS` makes the live snapshot fail. The competition account should hold only USD and mid-cap coins.
- **Binance access.** The signal needs Binance's public API (FAQ Q16 allows other data APIs). If both hosts fail, the bot holds its positions and retries every minute; it does not trade on stale data.
- **Ticker gaps.** On 2026-10-05, `TON/USD` and `OMNI/USD` were listed as tradable in `/v3/exchangeInfo` but absent from `/v3/ticker`. Neither is in the mid-cap list. A mid-cap pair missing from the ticker stops the cycle.
- **Fees are unconfirmed.** `config.py` assumes 0.1% taker; the API documentation example shows 0.012%. Check the `fee` on the first live fills in `logs/events.jsonl`.
- **Order throughput.** One ticker call per minute plus about 7 calls per order under the 30 per minute limit allows about 3 orders per minute, so a full rebalance takes a few minutes.
- **The majors runner (`--strategy model` and the rule strategies) is unchanged** and still uses the latched 12% halt and one-minute feature warm-up.

## Deploying

On the EC2 instance (Sydney, Session Manager), inside tmux, with the competition keys in `ROOSTOO_API_KEY` and `ROOSTOO_API_SECRET`:

```sh
git fetch origin && git checkout main && git pull
pip install -r requirements.txt
python -m roostoo_bot.main --strategy midcap --state data_store/midcap_live_state.json
```

Use a separate state file for live; the engine refuses to load a paper state file in live mode. Check progress with `python -m roostoo_bot.scripts.live_score --mode live`.

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

## Blockers before going live

1. **No strategy that can start.** `--strategy model` needs trained artifacts in `models/` and there are none. The rule strategies (`momentum`, `mean_reversion`, `cross_sectional`) refuse to start unless `COMPONENTS["cost_filter"]` is `False`. A fast-lane strategy has to be chosen and backtested (phase 3).
2. **The drawdown halt is permanent and too loose.** The team wants a tighter limit than 12% (see ROADMAP.md, Risk limits). `ExecutionState.mark` latches `halted` at a 12% drawdown and never resumes, even across restarts. A halted bot sells everything and stops trading, which loses the remaining active days needed for the 8-day rule and freezes the score. Phase 4 should replace it with an automatic de-risk that scales back up.
3. **Two live switches.** `DRY_RUN` in `main.py` and `LIVE_TRADING_ENABLED` in `config.py` must both change. Do it in its own commit, after the strategy and halt changes.
4. **Universe is majors only.** `config.UNIVERSE` is `UNIVERSE_GROUPS["majors"]`; the mid-cap list in `ROADMAP.md` is not wired in.

## Known limits to keep in mind

- **Warm-up after every restart.** Features need 61 contiguous one-minute ticks per pair from the bot's own collection, and any gap over 2 minutes resets that. Each restart costs about an hour without new trades.
- **Pending orders stop the bot.** Any pending order or locked balance makes every cycle stop until it clears. The bot only sends MARKET orders, which the API documentation shows as filled immediately, so this should not happen, but nothing cancels a stuck order automatically.
- **Assets outside the universe stop the bot.** A held coin that is not in `config.UNIVERSE` makes the live snapshot fail. Sell a coin before removing it from the universe.
- **Ticker gaps.** On 2026-10-05, `TON/USD` and `OMNI/USD` were listed as tradable in `/v3/exchangeInfo` but absent from `/v3/ticker`. The bot skips pairs without quotes, so TON (in the majors list) is silently never traded.
- **Fees are unconfirmed.** `config.py` assumes 0.1% taker; the API documentation example shows 0.012%. Check the `fee` on the first live fills in `logs/events.jsonl`.
- **Order throughput.** One ticker call per minute plus about 7 calls per order (balance, pending, shorts before and after, plus the order) under the 30 per minute limit allows about 3 orders per minute.

## Deploying

On the EC2 instance (Sydney, Session Manager), inside tmux, with the competition keys in `ROOSTOO_API_KEY` and `ROOSTOO_API_SECRET`:

```sh
git fetch origin && git checkout main && git pull
pip install -r requirements.txt
python -m roostoo_bot.main --strategy <chosen> --state data_store/live_state.json
```

Use a separate state file for live; the engine refuses to load a paper state file in live mode.

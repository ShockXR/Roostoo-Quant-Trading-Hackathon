# Mid-cap trading roadmap (draft, 2026-10-05)

Plan for the mid-cap crypto sleeve of the Roostoo bot. This is a plan, not a result: no strategy below has been backtested yet, and every number is a starting point to tune.

## Scoring and what it implies

Score = `0.4 * Sortino + 0.3 * Sharpe + 0.3 * Calmar`, Sharpe sampled daily. Starting capital: $100k mock. The live leaderboard is only visible in the Roostoo app (iPad/iPhone), so it cannot be read programmatically.

- **Downside dominates.** Sortino and Calmar are 70% of the score and both are driven by losing days and drawdown. Avoiding a deep drawdown is worth more than capturing extra upside.
- **Daily equity is what counts for Sharpe.** Trade on hour-to-day horizons. Round-trip cost is roughly 30bp (10bp taker fee and about 3bp slippage per side, plus spread), so fast trading cannot pay for itself.
- **Few data points.** A competition of a few weeks gives only a few dozen daily returns, so the ratios will be noisy. Prefer simple strategies that are stable across parameters over the best-looking backtest.
- **Mid-caps move together.** Ten positions can behave like one leveraged crypto-beta bet. Manage net market exposure directly. A market-neutral long/short book is the cleanest way to cut drawdown, if shorts are permitted on our keys.
- **No manual intervention.** Stops, overrides and discretionary trades through the API are banned (FAQ Q28). Every safeguard must be automated and every change committed. A drawdown halt must resume on its own, or the score stays at zero.
- **Our tooling does not score this way yet.** `config.py` annualizes on 60-second bars (`BARS_PER_YEAR`). The engine needs a scorer that resamples equity to daily and reports the composite.

## Competition facts (from the organizer FAQ, 27 Sep)

- Timeline (as supplied by the team, from the organizers):
  - Sep 29: team registration deadline.
  - Oct 1-3: preparation period (build and test deployment).
  - **Oct 4-17 (14 full days): live trading.** Strategies may be iterated and bots redeployed during this window.
  - **Each bot must have at least 8 active trading days, with enough trades from strategies each day.** What counts as "enough trades" is not yet known.
  - **Before Oct 14: submit the open-source repo link, with a README for judging.**
  - Oct 21: top 15 finalists announced (5 per region: HK, AU, IN).
  - Oct 27: finalist deck or markdown submission deadline.
  - Oct 29 - Nov 4 (excl. weekend): virtual finale (tentative). One day in Nov 4-10: in-person event in HK for selected HK participants.
- The FAQ's Sep 29-30 dates are the registration deadline, not trading rounds. The timeline above governs.
- The FAQ says missing early trading is not a disqualification, but prize consideration favors bots active for the full period.
- Long and short are both allowed. API only, 30 calls per minute (queries and orders combined).
- Repository must be open source. Running bot on the leaderboard is the proof of deployment.
- The system auto-liquidates at the end.
- Roostoo prices stream from Binance, so Binance Vision data is valid for backtests (map USD pairs to USDT).

## Proposed mid-cap universe (snapshot taken 2026-10-05, re-check before use)

Filter: 24h volume and bid/ask spread, since spread is the main hidden cost.

- **Core (about $10-55M 24h volume, spread under 5bp):** ENA, FET, WLD, UNI, TAO, ONDO, AAVE, VIRTUAL, HBAR, ARB, ICP, FIL, SEI, APT, ASTER, XLM, PUMP.
- **Exclude:** memes (17-25bp spreads), anything under about $1M volume or over about 8bp spread, PAXG, tokenized stocks.
- **Overlap to decide:** `config.py` "majors" already contains APT, ICP, HBAR, FIL, XLM, SUI, NEAR, TON. Assign each to one sleeve.
- **BTC and ETH are out of scope for this sleeve** (team decision, 2026-10-05): not traded and not used as signal inputs. Market context comes from an equal-weighted index of the mid-cap universe instead.
- **History:** PUMP and ASTER turned out to have the full Apr-Oct 2026 history on Binance. XPL and WLFI were not downloaded. The universe was chosen using today's volume, so backtests on it still carry some survivorship bias.

## Candidate trades (hypotheses to test, in order)

1. Cross-sectional momentum within the mid-cap bucket (4-24h ranking, volatility-scaled weights).
2. Market-adjusted reversion: fade extreme residual returns after removing beta times the return of an equal-weighted mid-cap index.
3. Volume-confirmed breakout above a 20-bar high.
4. Crash gate across all of them: cut risk when the equal-weighted mid-cap index drops 10-15% in 24h or cross-coin correlation spikes. Rationale from the professor's paper (Anoop et al., ESWA 2025): crashes are 15%+ drops in 24h and dependence between coins shifts around them. The paper's Gaussian-process potential-field model itself is not planned for implementation: it evaluates 24-day price forecasts, with no P&L or fees.

## Phases

Tentative, and expected to change as the work goes. The model and usage columns are my judgment, not a benchmark. We are assuming Opus 5.5 is the strongest model available to us, so it takes the work where a subtle mistake would be expensive and hard to notice (scoring, leakage, risk logic). Sonnet 5.5 does the building, and Haiku 4.5 does routine checks whose output is easy to verify.

Usage column: how much of the model budget the phase should take, relative to the other phases. Light means short, occasional sessions. Medium means a few focused sessions. Heavy means long sessions with large amounts of code or output in context. "High effort" means the model's extended-thinking / effort setting turned up for that session, which costs more per answer. We have not measured real consumption or checked the plan limits, so adjust after the first few days.

| Phase | Work | Model | Effort | Usage | Why |
|---|---|---|---|---|---|
| 0. Pin down scoring | Confirm day boundary, Sortino target, risk-free rate, annualization, drawdown basis, end date, shorts permission, capital config | Sonnet 5.5 | Standard | Light | Mostly reading docs and drafting precise questions. Answers come from the organizers, not the model. |
| 1. Scoring harness | Daily-resampled composite scorer; synthetic-curve tests; bot logs own daily equity from `/v3/balance` | Opus 5.5 to design and review the scorer, Sonnet 5.5 to implement and write tests | High for the design and review, standard for the implementation | Medium | A wrong convention here silently corrupts every later decision, so it is worth paying for the review. |
| 2. Data and universe | Download history for the core mid-cap universe (BTC and ETH excluded); re-check spreads and volume over several days | Haiku 4.5, or Sonnet 5.5 for the downloader changes | Standard | Light | Scripted and easy to verify with row counts and gap checks. |
| 3. Strategy research | Momentum, beta-adjusted reversion, breakout, crash gate on the existing engine; walk-forward, holdout, 1x and 2x costs | Opus 5.5 for strategy design and leakage review, Sonnet 5.5 for running sweeps and writing reports | High for the design and review, standard for the runs | Heavy | The largest phase. Leakage, multiple-testing and overfitting errors are subtle and look like good results. Keep Opus sessions short and targeted, and let Sonnet do the long runs. |
| 4. Portfolio and risk layer | Volatility-targeted sizing, volume and beta caps, auto-resuming drawdown de-risk well below the current 12% halt | Opus 5.5 | High | Medium | Bugs here lose money or lock the bot out. Reason through failure cases before coding. |
| 5. Paper / shadow run | 48-72h on live ticker, compare to simulator-style fills, check restart state and rate limits | Sonnet 5.5, Haiku 4.5 for log summaries | Standard | Medium | Verification and debugging against logs. |
| 6. Deploy | EC2 Sydney, tmux, competition keys, reduced size first, clear commit history | Sonnet 5.5, with Opus 5.5 reviewing the final diff | Standard to build, high for the review | Light | Procedural, but a bad deploy is costly, so the Opus review is a single short pass. |
| 7. Operate | Daily comparison of bot score vs app leaderboard; changes only via reviewed commits | Haiku 4.5 for daily summaries, Opus 5.5 when investigating a drawdown or changing logic | Standard for summaries, high for investigations | Light day to day, spikes after a bad day | Routine checks are cheap. Diagnosing a loss deserves the strongest model. |

Across all phases: Opus 5.5 should review any change that touches order placement, sizing or the scorer before it is deployed. If usage runs short, keep Opus for phases 1, 3 (design only) and 4, and move everything else to Sonnet 5.5.

## Phase 0 status (as of 2026-10-05)

Settled:
- Live trading window is Oct 4-17, with at least 8 active days.
- Score formula, daily Sharpe sampling, and $100k starting capital.
- The deployed bot is not trading.

Still open (none blocks phase 1; the scorer takes these as settings and starts with the defaults in brackets):
- Day boundary [UTC].
- Sortino target and risk-free rate [0 and 0].
- Annualization [365].
- Whether drawdown is measured on daily or intraday equity [daily].
- What counts as "enough trades" per active day. Affects strategy design in phase 3, not the scorer.
- Whether shorts work on our competition keys. Affects strategy design in phase 3.
- Capital split between the mid-cap and majors sleeves. Affects phase 4.

Defaults are placeholders. If the organizers' answers differ, change the settings and re-run; ranking of strategies is unlikely to flip on these choices, but this has not been checked.

## Risk limits (team decision, 2026-10-05)

The current 12% drawdown halt (`MAX_DRAWDOWN_KILL_SWITCH`) is too loose for a score built on small, stable, appropriately sized bets. Phase 4 replaces it with a tighter, staged de-risk that reduces size as drawdown grows and restores it on recovery, instead of a permanent halt. The thresholds are to be set from the phase 3 backtests. The current halt is left unchanged until then, because lowering a permanent halt now would only make it more likely to stop trading for the rest of the competition.

## Phase 1 status (as of 2026-10-05)

Done and committed on branch `phase1-scoring`: scorer, equity logging, live score report and two live-broker fixes. What remains before the bot can trade is in [docs/LIVE_READINESS.md](docs/LIVE_READINESS.md); open questions for the organizers are collected in [docs/ORGANIZER_QUESTIONS.md](docs/ORGANIZER_QUESTIONS.md).

## Phase 2 status (as of 2026-10-05)

Downloaded Binance spot 1m klines for the 17 core mid-caps (USDT pairs), 2026-04-01 to 2026-10-04 UTC, into `binance_data/` (gitignored, about 1.5 GB). Every symbol has 269,280 candles with no gaps; VIRTUAL had 54 exact duplicate candles inside Binance's July archive, which were dropped.

| Symbol | Median daily volume ($M) | Daily volatility (%) | Zero-volume 1m bars (%) |
|---|---|---|---|
| TAO | 23.5 | 4.5 | 0.7 |
| WLD | 19.7 | 6.7 | 0.2 |
| ENA | 15.3 | 6.1 | 2.6 |
| AAVE | 14.3 | 4.3 | 0.3 |
| UNI | 13.4 | 5.0 | 1.1 |
| XLM | 11.6 | 4.6 | 1.3 |
| ONDO | 11.0 | 5.2 | 0.8 |
| FET | 10.6 | 5.1 | 2.2 |
| PUMP | 9.1 | 6.3 | 2.7 |
| ASTER | 8.3 | 3.3 | 17.5 |
| HBAR | 7.0 | 3.5 | 1.3 |
| FIL | 6.6 | 4.7 | 6.4 |
| ARB | 5.7 | 5.4 | 5.5 |
| APT | 5.5 | 4.5 | 11.0 |
| ICP | 4.9 | 4.7 | 5.5 |
| VIRTUAL | 4.6 | 4.9 | 3.3 |
| SEI | 3.4 | 4.1 | 3.2 |

Daily volatility is the standard deviation of hourly returns scaled to a day. Many minutes with no trades (ASTER, APT) mean 1m bars are noisy for these coins; research should use 15m or 1h bars.

Still to do: the live spread and volume check over several days needs a long-running ticker logger, best run on the EC2 instance; it cannot run from a laptop session.

## Gates

Suggested starting thresholds, adjust as evidence comes in.

| Gate | Pass condition |
|---|---|
| Research to paper | Positive net composite in most walk-forward windows including the holdout; still positive at 2x costs; no single day or coin dominates |
| Paper to live | Paper matches simulator-style fills within a small tolerance; no logging or state errors for 48h |
| Scale up | Live daily results consistent with paper over several days |

## Timing

Live trading runs Oct 4-17, so the plan has to be compressed. The deadlines that matter:

- **8 active days out of 14.** Day 1 is Oct 4. If the bot only starts trading on Oct 10, every remaining day through Oct 17 must be active, with no slack. Starting by Oct 7-8 leaves a few days of slack for outages. The activity rule also constrains the strategy: the crash gate and the drawdown de-risk must reduce size, not stop all trading for a day.
- **Repo submission before Oct 14**, with a README that explains the strategy, entry and exit rules, risk management and setup (FAQ Q44). Judges read it, so this ROADMAP.md is a working document and the README should describe what was actually built.
- **Finalists announced Oct 21**, deck due Oct 27.

Two lanes. Fast lane (target: trading by about Oct 7-8): the scorer, the crash gate and one conservative strategy, deployed at reduced size so the bot is making autonomous trades every day. Research lane (through about Oct 13): add the other strategies as they pass the gates, deploying each as a separate commit. Shorten the paper run (phase 5) to a day or less for the fast lane, and say so in the commit message.

## Open items

- **Deployed bot state.** Confirmed by the team: the bot is not trading. The EC2 instance runs Samyak's code, which as committed cannot place orders: `main.py` hard-codes `DRY_RUN = True`, its entrypoint calls `run_forever()` without a trained model so every pair is skipped, and even with `DRY_RUN` flipped it would pass a capital fraction to `place_order` as if it were a quantity. Do not edit the instance by hand: all changes must come through commits.
- What counts as "enough trades" per active day.
- Scoring details from Phase 0.
- Whether shorts work on our competition keys (`ALLOW_SHORTS` is off by default).
- Capital split between the mid-cap sleeve and the majors sleeve.

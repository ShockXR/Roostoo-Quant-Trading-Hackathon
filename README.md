# Roostoo three-sleeve momentum bot

An autonomous, long-only trading bot for the Roostoo Quant Trading Hackathon. It trades large-cap, mid-cap and a small slice of small-cap cryptocurrencies on the Roostoo exchange API with daily-rebalanced momentum strategies, sized for steady, low-drawdown returns because the competition score rewards risk-adjusted performance.

**Status (2026-10-10, update before submission).** From Oct 6 to Oct 10 the competition account ran an earlier dual-sleeve bot from branch `main` (24h dip-buying on large caps, momentum with tight profit-takes on mid caps); it was about -2.5% at day 6. This branch replaces it with the three-sleeve book described below, run with `--strategy sleeves`. Why the old bot lost and what changed: [docs/SLEEVES.md](docs/SLEEVES.md). Switchover steps: [DEPLOYMENT.md](DEPLOYMENT.md) section 5a. Everything below about performance is historical simulation with assumed costs, not live results.

## Strategy overview

One book, three sleeves with separate universes and risk budgets, rebalanced once a day after 00:00 UTC (details and evidence in [docs/SLEEVES.md](docs/SLEEVES.md)):

| Sleeve | Signal | Budget |
|---|---|---|
| Large caps (BTC, ETH, SOL, BNB, XRP, ADA, DOGE, LINK, AVAX, DOT, LTC, SUI) | Top 3 by 14-day return, held only while BTC is above its 100-day average | 1.0% daily-vol target, up to 32% of equity |
| Mid caps (22 coins) | 24h/168h momentum blend described below; half size while BTC is below its 100-day average | 1.4% daily-vol target, up to 40% |
| Small caps (10 memecoins and small alts) | Top 3 by 7-day return, confirmed by a positive 24h return, only while BTC is above its 100-day average; hard stop 10% below entry | 0.7% daily-vol target, up to 12% |

Sizes were raised on Oct 12 to put idle cash to work (at most 90% invested), and the large-cap lookback was cut from 28 to 14 days on the strength of the team's 2022-26 research grid; see [docs/SLEEVES.md](docs/SLEEVES.md). The rest of this README describes the mid-cap sleeve's research and the shared engine, which the book runs on.

**Mid-cap idea.** Over horizons of one to seven days, mid-cap crypto assets that have recently outperformed tended to keep outperforming in our 2026 test data (cross-sectional momentum), while short-horizon reversal lost in every configuration we tried. The sleeve buys the strongest recent performers, weights them by inverse volatility, keeps risk to a daily-volatility target, and sits in cash when nothing is trending.

**Scoring context.** Competition score = `0.4 * Sortino + 0.3 * Sharpe + 0.3 * Calmar`, with Sharpe sampled daily. All three penalize losing days and drawdown, so the design favors small, diversified, volatility-targeted positions over maximum return. Details of how we compute the score locally are in [scoring.py](scoring.py).

**Key features**
- Daily rebalance at 00:00 UTC; positions are held between rebalances (no high-frequency trading).
- Long-only, no leverage, market orders only.
- Staged drawdown de-risk that cuts size automatically and never goes to zero, so the bot keeps trading every day.
- State, equity and every fill are logged; restarts resume from saved state.
- Signals come from Binance hourly candles (Roostoo prices stream from Binance); execution and balances come from the Roostoo API.

## Architecture

```
Binance public klines (1h)          Roostoo API (/v3/ticker, /v3/balance, /v3/place_order ...)
        |                                        |
 data/binance_live.py                    execution/trader.py  (signing, rate limit, no blind retries)
        |                                        |
 strategies/midcap.py  --targets-->  midcap_live.py (MidcapEngine) --orders--> execution/broker.py (LiveBroker)
 (signals, sizing, overlays)           |  daily rebalance, drawdown de-risk,      or PaperBroker (simulated fills)
                                       |  restart-safe state
                                       +--> logs/events.jsonl, logs/equity.jsonl, data_store/*.json
```

| Component | File |
|---|---|
| Three-sleeve book: sleeve signals and sizing | [strategies/sleeves.py](strategies/sleeves.py) |
| Three-sleeve live/paper runner: small-cap stops, activity trade, drawdown de-risk | [sleeves_live.py](sleeves_live.py) |
| Strategy: momentum signals, volatility target, caps, overlays | [strategies/midcap.py](strategies/midcap.py) |
| Live/paper runner: schedule, drawdown de-risk, state | [midcap_live.py](midcap_live.py) |
| Engine shared with the earlier runner: reconcile, order loop, persistence | [main.py](main.py) |
| Binance hourly data for the live signal | [data/binance_live.py](data/binance_live.py) |
| Roostoo API client (HMAC signing, 30 calls/min budget) | [execution/trader.py](execution/trader.py), [execution/auth.py](execution/auth.py) |
| Live broker (reconciles wallet each cycle) and paper broker | [execution/broker.py](execution/broker.py), [execution/portfolio.py](execution/portfolio.py) |
| Competition score (daily Sortino, Sharpe, Calmar) | [scoring.py](scoring.py), [scripts/live_score.py](scripts/live_score.py) |
| Multi-asset hourly backtester | [backtest/portfolio.py](backtest/portfolio.py) |
| Research grid and history replay through the live engine | [scripts/midcap_research.py](scripts/midcap_research.py), [scripts/shadow_replay.py](scripts/shadow_replay.py) |
| Binance history downloader | [binance_downloader.py](binance_downloader.py) |

Tech stack: Python 3.10+, pandas, numpy, requests; Binance public market data; designed to run on AWS EC2 (Sydney) under systemd. The repository also holds an earlier model-based pipeline for large-cap coins (`features/`, `model/`, `--strategy model`); the mid-cap strategy does not use it. It is described in [docs/OFFLINE_VALIDATION.md](docs/OFFLINE_VALIDATION.md).

## Mid-cap sleeve in detail (research configuration)

As researched and first deployed on its own. In the three-sleeve book the same signal runs on 22 coins with the smaller budget in the table above; the drawdown schedule and order safety below apply to the whole book.

**Universe (17 pairs).** ENA, FET, WLD, UNI, TAO, ONDO, AAVE, VIRTUAL, HBAR, ARB, ICP, FIL, SEI, APT, ASTER, XLM, PUMP (all `/USD` on Roostoo). Chosen from the Roostoo pairs by 24h volume and bid/ask spread on 2026-10-05: about $8-53M of 24h volume on Roostoo and spreads under 5bp. BTC, ETH, memecoins, thinly traded pairs and tokenized stocks are excluded. The list was picked with knowledge of today's volumes, which flatters historical tests slightly.

**Signal (computed once a day from the closed 00:00 UTC hourly candle).** Two momentum sleeves, averaged:
1. 24-hour lookback: the 5 coins with the highest 24h return.
2. 168-hour lookback: the 3 coins with the highest 7-day return.

A coin only qualifies if its return over the lookback is positive; if fewer than 2x the sleeve size have data, that sleeve is empty. Within each sleeve, qualifying coins are weighted by inverse volatility (standard deviation of hourly returns over the last 168 hours). If neither sleeve has a qualifying coin, the bot holds cash; if only one does, the volatility target below sets the size.

**Entry and exit.** The bot enters or adds to a coin when the new daily targets give it a larger weight, and exits or trims when the coin drops out of the top group or its lookback return turns non-positive. Trades happen only at the daily rebalance (and for drawdown cuts, below). Per-coin changes smaller than 1% of equity are skipped, except exits to zero.

**Position sizing.** The averaged weights are scaled so the predicted daily volatility of the portfolio (from the covariance of the last 168 hourly returns) is at most a target of 1.5%, with at most 20% of equity in any coin and at most 70% of equity invested in total. In quiet markets the exposure reaches the 70% cap; in volatile markets it shrinks. No leverage.

**Risk management**
- *Drawdown schedule* (measured from the equity high-water mark, checked every minute): beyond a 3% drawdown the bot cuts positions to half of target, beyond 6% to 30% of target. Cuts are traded immediately. Size returns to full only at the next daily rebalance after drawdown has recovered. The bot is never fully halted by this rule, so it stays active every day, as the competition requires.
- *Order safety:* orders use quantities rounded to each pair's lot size and respect the venue minimum order size; reductions execute before additions; wallet balances are re-read from the exchange every cycle, so the bot trusts the exchange state rather than its own bookkeeping.
- *Failure handling:* a timeout or partial fill is never assumed to be a fill (the next cycle reconciles against the wallet before trading again); an order the exchange rejects is skipped and the other coins still trade; stale or missing quotes stop the cycle; if the signal data is unavailable the bot holds its positions and retries.
- *API budget:* about 4 calls per idle minute and 7 per order, under the 30 calls/minute limit.

**Assumptions**
- Taker fee 0.10% and about 0.05% slippage plus spread per side in backtests (0.15% total), with a 2x cost stress test. The real fee has not yet been verified on the competition account.
- Fills at the price 2 to 5 minutes after the signal hour, never at the signal price.
- Shorting is not used (permission on our keys is unconfirmed, and long/short momentum tested worse).

## Results (historical simulation, not live)

Mid-cap sleeve on its own, at its research configuration. Results for the large-cap signal are in `MODEL_RESEARCH_REPORT.md` on branch `live-readiness`; `scripts/sleeves_backtest.py` scores the whole book.

Data: Binance spot 1-minute candles for the 17 coins, 2026-04-01 to 2026-10-04, resampled to hourly. Backtests start flat with $100k, use the costs above, and score fresh 14-day windows the length of the competition. A test confirms strategy weights at any hour are unchanged when later data is removed (no look-ahead). Full method and caveats: [docs/MIDCAP_RESEARCH.md](docs/MIDCAP_RESEARCH.md).

| Period | Market (equal-weight hold of the 17 coins) | Strategy, live configuration | Max drawdown over the period |
|---|---|---|---|
| Apr 8 - Aug 1 (selection) | -5.7% | +13.2% | -6.0% |
| August | +21.3% | +7.8% | -4.1% |
| Sep 1 - Oct 4 | +48.3% | +4.2% (+7.1% replayed through the live engine) | -5.0% |

Across twelve 14-day windows from April to October, the live engine's average return was 2.1% per window with a worst drawdown of -4.7%, versus 3.3% and -6.9% without the drawdown schedule. Return figures include costs.

Read these carefully:
- The strategy is built to be steady, so it lags badly in strong rallies and protects capital in falling markets.
- Forty-seven configurations were tried; the best selection-period numbers are inflated by that. The September-October period was used once to confirm a one-percent-volatility variant, so it is not a clean out-of-sample test for the final 1.5% configuration, which differs.
- Annualized ratios over 14 days are very sensitive to how the organizers annualize; see [docs/ORGANIZER_QUESTIONS.md](docs/ORGANIZER_QUESTIONS.md) for the open questions.

## Setup and run

Requires Python 3.10 or newer.

```sh
git clone https://github.com/ShockXR/Roostoo-Quant-Trading-Hackathon
cd Roostoo-Quant-Trading-Hackathon
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # installs the package in editable mode
cp .env.example .env                     # add ROOSTOO_API_KEY and ROOSTOO_API_SECRET (live only)
python -m unittest discover -s tests -t . -v      # offline tests, no network, no orders
python -m roostoo_bot.main --strategy sleeves     # paper mode (default): public ticker, simulated fills
```

Paper mode needs no keys and never sends orders. Live trading needs two deliberate code changes (`DRY_RUN = False` in `main.py`, `LIVE_TRADING_ENABLED = True` in `config.py`) plus the competition keys. Deployment on EC2, the pre-live checklist and daily operation are in [DEPLOYMENT.md](DEPLOYMENT.md).

**Monitor.** `python -m roostoo_bot.scripts.live_score --mode live` prints the running composite score, its parts, max drawdown and fills per day (the competition requires at least 8 active trading days). Logs: `logs/events.jsonl` (every fill with price, quantity, fee and order ID), `logs/equity.jsonl` (one equity mark per minute), `logs/bot.log`.

**Reproduce the research.**

```sh
python binance_downloader.py --symbols ENAUSDT FETUSDT WLDUSDT UNIUSDT TAOUSDT ONDOUSDT AAVEUSDT VIRTUALUSDT HBARUSDT ARBUSDT ICPUSDT FILUSDT SEIUSDT APTUSDT ASTERUSDT XLMUSDT PUMPUSDT --start 2026-04-01 --end 2026-10-04 --interval 1m
python -m roostoo_bot.scripts.midcap_research --validate 8     # strategy grid, research and validation periods
python -m roostoo_bot.scripts.shadow_replay                    # history replayed through the live engine vs the backtest
```

## Competition rules and how the bot complies

- **Autonomous:** all trades come from the bot's loop; changes go through commits, `git pull` and a service restart. No manual orders.
- **Not high-frequency:** one rebalance per day, market orders only, 30 calls/minute budget enforced in code.
- **Logging:** timestamp, symbol, side, price, quantity, fee and order ID are written for every fill, plus the equity after it.
- **Open source:** this repository; each strategy change is a separate commit.

## Documentation

- [docs/SLEEVES.md](docs/SLEEVES.md): the three-sleeve book, why the Oct 6-10 bot lost, what is and isn't tested
- [docs/REDTEAM_2026-10-10.md](docs/REDTEAM_2026-10-10.md): adversarial review of the book before deployment, findings and fixes
- [ROADMAP.md](ROADMAP.md): plan, phases and decisions
- [docs/MIDCAP_RESEARCH.md](docs/MIDCAP_RESEARCH.md): strategy research and results
- [docs/LIVE_READINESS.md](docs/LIVE_READINESS.md): what was verified and what remains before live trading
- [docs/ORGANIZER_QUESTIONS.md](docs/ORGANIZER_QUESTIONS.md): open questions and the assumptions used meanwhile
- [docs/OFFLINE_VALIDATION.md](docs/OFFLINE_VALIDATION.md): the earlier model-based pipeline and its regression fixes
- [docs/BINANCE_DOWNLOADER.md](docs/BINANCE_DOWNLOADER.md), [docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md), [docs/INTEGRATION.md](docs/INTEGRATION.md): downloader guide and earlier single-coin research by SamyakJain092006

## Team

Shreyan Thanki (ShockXR) and Samyak (SamyakJain092006). Built with assistance from Claude (Anthropic).

# Roostoo three-sleeve momentum bot

An autonomous, long-only crypto trading bot for the Roostoo Quant Trading Hackathon (live window Oct 4-17, 2026, $100k mock capital). It runs three momentum sleeves (large, mid and small caps), rebalances once a day, and sizes everything by volatility. The competition score is `0.4 * Sortino + 0.3 * Sharpe + 0.3 * Calmar`, so the design favors avoiding drawdown over chasing returns.

Performance numbers in this repo are historical simulations with assumed costs. Live standings are on the Roostoo leaderboard.

## Strategy

Daily rebalance after the 00:00 UTC hourly close. Signals come from Binance hourly candles (Roostoo prices stream from Binance); orders and balances go through the Roostoo API. Long only, no leverage, market orders, at most 90% of equity invested.

| Sleeve | Universe | Signal | Budget |
|---|---|---|---|
| **Large caps** | BTC ETH SOL BNB XRP ADA DOGE LINK AVAX DOT LTC SUI | Top 3 by 14-day return (positive only), equal weight. Held only while BTC is above its 100-day average; otherwise cash. | 1.0% daily-vol target, up to 32% of equity |
| **Mid caps** | 22 coins (ENA FET WLD UNI TAO ONDO AAVE VIRTUAL HBAR ARB ICP FIL SEI APT ASTER XLM PUMP + CRV PENDLE CAKE POL CFX) | Blend of the top 5 by 24h return and the top 3 by 7-day return (positive only), inverse-volatility weights. Half size while BTC is below its 100-day average. | 1.4% daily-vol target, up to 40% |
| **Small caps** | PEPE SHIB BONK WIF FLOKI PENGU TRUMP 1000CHEEMS EIGEN BIO | Top 3 by 7-day return, kept only if the 24h return is also positive. Only while BTC is above its 100-day average. | 0.7% daily-vol target, up to 12%; 4% per coin |

**Risk controls**
- Each sleeve is scaled so the predicted daily volatility of its holdings (covariance of the last 168 hourly returns) stays at its target, with per-coin and per-sleeve caps.
- Drawdown de-risk: beyond 6% below the equity high, positions are cut to 30% at once and restored at the next rebalance. Never zero, so the bot trades every day.
- Small caps only: 10% hard stop from the average entry, 72-hour lockout after a stop, no entry through a spread wider than 0.30%.
- No stop-losses on large or mid caps, and no averaging down (research showed stops hurt the momentum edge).
- A strategy trade is forced by 12:00 UTC if nothing has filled that day (the competition requires at least 8 active days).

## Philosophy

1. **Optimize the score, not the return.** Sortino and Calmar are 70% of the score and both are driven by losing days and drawdown, so we size by volatility and avoid deep drawdowns rather than maximize exposure.
2. **Evidence before intuition.** Choices come from backtests with walk-forward splits and untouched holdouts, from parameter plateaus (not the best single cell), and from stress tests at 2x costs. Untested parts are labeled untested in [docs/SLEEVES.md](docs/SLEEVES.md).
3. **Momentum, not dip-buying.** On 2022-26 history a 1.5-sigma dip-buyer lost 55%, while cross-sectional momentum behind a BTC regime filter made money and held up in the 2025-26 bear market. Mid caps trended rather than mean-reverted over hours to days.
4. **Costs are real.** At about 0.10% per side, fast trading cannot pay for itself, so there is one rebalance a day and a no-trade band.
5. **Cut risk, never go to zero.** Every safeguard reduces size and restores automatically, because the rules require autonomous, active trading.
6. **Trust the exchange, not our bookkeeping.** The wallet is re-read every minute, a fill is never assumed, rejected orders are skipped, and state survives restarts.
7. **Attack our own work.** Before the live switch a four-team red-team review (market, execution, scoring, evidence), each finding checked by a skeptic, found 28 issues; the confirmed ones were fixed with regression tests ([docs/REDTEAM_2026-10-10.md](docs/REDTEAM_2026-10-10.md)).

## How the two weeks went

- **Before the window:** an ML pipeline (`features/`, `model/`) and a dip-reversal rule on large caps, from early Binance research.
- **Oct 5:** mid-cap momentum research (47 configurations, volatility targeting, staged drawdown de-risk, a local competition scorer) and a hardened live engine ([docs/MIDCAP_RESEARCH.md](docs/MIDCAP_RESEARCH.md)).
- **Oct 6-10:** the live account ran a dual-sleeve bot on `main` (large-cap dip-buying, mid-cap momentum, tight trailing profit-takes, an averaging-down booster). It was down about 2.5% by day 6: small wins, large stops, positions that grew as they fell.
- **Oct 10:** the diagnosis and rebuild into the three-sleeve book above, using the team's 2022-26 large-cap research ([docs/SLEEVES.md](docs/SLEEVES.md)).
- **Oct 11-12:** the sleeve architecture was ported to the live bot; sizes were raised to put idle cash to work, the large-cap lookback moved from 28 to 14 days (28 days was the worst lookback in the 2025-26 out-of-sample test), and the 3% drawdown stage was dropped.

## Layout

| Path | What it is |
|---|---|
| [strategies/sleeves.py](strategies/sleeves.py) | Sleeve signals, volatility targeting, regime gate (shared by live and backtest) |
| [sleeves_live.py](sleeves_live.py) | Live/paper engine: daily rebalance, drawdown de-risk, small-cap stops, activity trade, restart-safe state |
| [strategies/midcap.py](strategies/midcap.py) | Momentum blend, volatility-target and drawdown-scale primitives |
| [execution/](execution/) | Roostoo client (signing, 30 calls/min budget), live and paper brokers, order planning |
| [data/binance_live.py](data/binance_live.py) | Binance hourly klines for the signals |
| [backtest/portfolio.py](backtest/portfolio.py), [scoring.py](scoring.py) | Hourly multi-asset backtester and the competition score |
| [scripts/sleeves_backtest.py](scripts/sleeves_backtest.py), [scripts/review_live_trades.py](scripts/review_live_trades.py) | Book vs its parts on Binance history; round-trip review of live fills |
| [tests/](tests/) | 156 offline tests (signals, no look-ahead, live = backtest weights, engine, failure modes) |

## Run

Python 3.10+.

```sh
git clone https://github.com/ShockXR/Roostoo-Quant-Trading-Hackathon && cd Roostoo-Quant-Trading-Hackathon
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                                  # ROOSTOO_API_KEY / ROOSTOO_API_SECRET (live only)
python -m unittest discover -s tests -t . -v          # offline, no orders
python -m roostoo_bot.main --strategy sleeves         # paper mode by default: public ticker, simulated fills
python -m roostoo_bot.scripts.live_score --mode live  # running competition score and fills per day
```

Branch `Shreyan` is paper mode. Branch `Shreyan-live` is `Shreyan` plus one commit that sets `DRY_RUN = False` and `LIVE_TRADING_ENABLED = True`. Deployment on EC2 (systemd, switchover from the older bot, rollback): [DEPLOYMENT.md](DEPLOYMENT.md).

## Competition rules

- **Autonomous:** every trade comes from the bot loop; changes go through commits, `git pull` and a restart.
- **Not high-frequency:** one rebalance a day, market orders, API budget enforced in code.
- **Logging:** `logs/events.jsonl` has every fill (time, pair, side, price, quantity, fee, order id); `logs/equity.jsonl` has one equity mark per minute.
- **Open source:** this repository; strategy changes are separate commits.

## Docs

[docs/SLEEVES.md](docs/SLEEVES.md) (strategy, evidence, what is untested) · [docs/REDTEAM_2026-10-10.md](docs/REDTEAM_2026-10-10.md) · [docs/MIDCAP_RESEARCH.md](docs/MIDCAP_RESEARCH.md) · [docs/LIVE_READINESS.md](docs/LIVE_READINESS.md) · [docs/ORGANIZER_QUESTIONS.md](docs/ORGANIZER_QUESTIONS.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [ROADMAP.md](ROADMAP.md)

## Team

Shreyan Thanki ([ShockXR](https://github.com/ShockXR)), Samyak ([SamyakJain092006](https://github.com/SamyakJain092006)) and a teammate who ran the live deployment on AWS. Built with assistance from Claude (Anthropic).

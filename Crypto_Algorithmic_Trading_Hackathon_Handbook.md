# Crypto Algorithmic Trading — Hackathon Handbook

*Beginner-to-intermediate briefing for research, backtesting, risk, execution, and bot engineering*

```text
MARKET DATA -> FEATURES -> SIGNAL -> POSITION -> EXECUTION -> P&L
^ |
|---------------------- EVALUATION + RISK <-----------------|
```

The central idea of the handbook: every profitable-looking idea must survive the complete trading pipeline.

**Competition scope: cryptocurrencies • long/short • approximately 1× leverage • systematic trading focus**

*Educational material only — not financial advice. No strategy is guaranteed to be profitable.*

> **Markdown note:** Mathematical expressions use standard LaTeX delimiters (`$...$` inline and `$$...$$` for display math). They render in Markdown viewers with math support, including many VS Code/Obsidian setups and GitHub where supported.

# CONTENTS

**1. What Algorithmic Trading Actually Is**

**2. Basic Market and Crypto Trading Concepts**

**3. Price Data and Market Data**

**4. Statistics Every Algorithmic Trader Should Know**

**5. Probability and Statistical Thinking**

**6. Time Series Basics**

**7. Features and Indicators**

**8. Signal Generation**

**9. Position Sizing**

**10. Long/Short Strategies**

**11. Backtesting**

**12. How to Accidentally Fake a Profitable Strategy**

**13. Training / Validation / Testing**

**14. Overfitting**

**15. Robustness Testing**

**16. Performance Metrics**

**17. Transaction Costs and Execution**

**18. Market Microstructure**

**19. Risk Management**

**20. Portfolio Construction**

**21. Strategy Ideas for a Beginner**

**22. Combining Strategies**

**23. Machine Learning**

**24. Reinforcement Learning**

**25. Bot Architecture**

**26. Live Trading vs Backtesting**

**27. Paper Trading / Dry Run**

**28. Logging and Debugging**

**29. Software Engineering Practices**

**30. Experimentation Workflow**

**31. How to Conduct a Good Trading Experiment**

**32. 20 Things That Will Probably Go Wrong in Your First Trading Bot**

**33. Hackathon Strategy**

**34. Final Cheat Sheet**

Final: What We Actually Need to Know for the Hackathon

One-page Pre-Hackathon Checklist

## How to use this handbook

Read Parts 1–12 before writing a serious backtest. Use Parts 13–20 while validating research, Parts 25–29 while building the bot, and Parts 30–34 as the team’s operating playbook. The fastest way to learn is to implement a simple strategy end-to-end while checking each section against your code.

> **KEY TAKEAWAY: The core philosophy**  
> A trading strategy is a hypothesis, not a guarantee. A backtest is an experiment, not proof. The objective is not the highest historical return; it is a strategy with a reasonable chance of surviving unseen data, costs, execution, and changing regimes.

# PART 1 — WHAT ALGORITHMIC TRADING ACTUALLY IS

*From an idea to a complete trading system*

Algorithmic trading means expressing a trading decision as explicit rules or models that a computer can evaluate repeatedly. The computer may decide whether to trade, how much to trade, how to submit the order, when to exit, and when risk limits override the strategy. What matters is not that the code is complicated; what matters is that the decision process is reproducible and testable.

### Discretionary vs systematic trading

**In simple language.** A discretionary trader decides case by case; a systematic trader writes the decision rule before seeing the next outcome.

**Technical view.** A systematic strategy maps observable information at time t into a desired exposure or order. The same information should produce the same decision unless the system intentionally includes randomness.

**Why it matters.** A fixed rule can be backtested, audited, and debugged. Human intuition that changes after every loss cannot be evaluated cleanly.

> **EXAMPLE: Discretionary vs systematic trading**  
> Rule: go long BTC when its 20-candle moving average rises above its 50-candle moving average; exit or reverse when the opposite happens.

> **COMMON MISTAKE: Discretionary vs systematic trading**  
> Writing a vague rule such as 'buy strong-looking breakouts.' If two team members implement different interpretations, you do not have one strategy.

> **BOT DESIGN IMPLICATION: Discretionary vs systematic trading**  
> Encode every decision as a function with explicit inputs, parameters, outputs, and timestamps. Avoid hidden manual overrides during evaluation.

## What a trading strategy consists of

- Signal generation: what do we believe about future returns or risk?

- Position sizing: how much exposure do we want given the signal and volatility?

- Execution: what order do we send, and what price/fill do we realistically expect?

- Risk management: what limits can reduce or veto the desired position?

- Accounting and evaluation: what happened to capital after fees, slippage, and mark-to-market P&L?

> **KEY TAKEAWAY: Prediction is not profit**  
> A model can correctly predict that BTC has a 52% chance of rising and still lose money if its winners are tiny, its losers are large, turnover is expensive, or the signal arrives too late. Trading profitability depends on the entire pipeline, not only prediction accuracy.

**Figure 1 — Complete algorithmic trading pipeline**

```text
[Market Data]
|
v
[Cleaning + Alignment] -> [Features] -> [Signal]
|
v
[Desired Position]
|
+-----------------+-----------------+
| |
v v
[Risk Manager] [Position Sizer]
| |
+-----------------+-----------------+
v
[Execution]
|
v
[Orders / Fills]
|
v
[Portfolio + P&L]
|
v
[Evaluation + Monitoring]
```

The strategy is only one layer. A robust bot separates research logic, risk, execution, and accounting.

## Strategy, trading system, and trading bot

A strategy is the decision logic that maps information to desired exposure. A trading system includes the strategy plus portfolio construction, risk rules, and execution assumptions. A trading bot is the software implementation that connects to data and an exchange, runs the trading system, places orders, records fills, and survives operational failures. Teams often confuse the three and spend too long polishing signals while leaving accounting or execution fragile.

Alpha is the part of expected return that comes from an exploitable edge rather than simply taking broad market risk. In a hackathon, think of alpha more modestly: a repeatable feature or rule that improves expected risk-adjusted performance after realistic costs. The safest mindset is to treat every proposed source of alpha as a hypothesis that must earn its way into the system.

# PART 2 — BASIC MARKET AND CRYPTO TRADING CONCEPTS

**Table 1 — Key algorithmic trading terminology**

| **Concept**      | **Meaning**                                                                 | **Why it matters**                                                                     |
|------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| Asset            | A tradable instrument such as BTC, ETH, or SOL.                             | Strategies operate on one or more assets and must track exposure per asset.            |
| Exchange         | Venue that provides market data and accepts orders.                         | APIs, fees, liquidity, symbol names, and failure modes are exchange-specific.          |
| Trading pair     | Two currencies quoted together, e.g. BTC/USDT.                              | The base asset is BTC; the quote asset is USDT.                                        |
| Spot market      | Immediate exchange of the underlying asset.                                 | Long exposure is straightforward; shorting depends on platform/competition rules.      |
| Perpetual future | Derivative with no expiry, typically kept near spot using funding payments. | Common in crypto long/short trading; funding can alter net P&L.                        |
| Long             | Exposure that profits when price rises.                                     | Positive price return generally produces positive P&L.                                 |
| Short            | Exposure that profits when price falls.                                     | P&L sign reverses; funding/borrow rules may matter.                                    |
| Bid / Ask        | Best current buy price / sell price in the order book.                      | Crossing the spread costs money.                                                       |
| Spread           | Ask minus bid.                                                              | A direct execution friction, especially for high-turnover systems.                     |
| Mid-price        | (Bid + Ask)/2.                                                              | Useful reference, but a market order rarely fills exactly at mid.                      |
| OHLCV            | Open, high, low, close, volume for a candle.                                | Core dataset for many hackathon strategies.                                            |
| Market order     | Order that prioritizes immediate execution.                                 | Fast but usually pays spread and may suffer slippage.                                  |
| Limit order      | Order at a specified price or better.                                       | Can reduce price cost but may never fill or may only partially fill.                   |
| Stop order       | Order triggered when price crosses a threshold.                             | Useful for strategy exits, but gaps/slippage can make actual fill differ from trigger. |
| Liquidity        | Ability to trade without moving price much.                                 | Low liquidity increases slippage and execution uncertainty.                            |
| Volatility       | Magnitude of price fluctuations.                                            | Drives risk and should influence position sizing.                                      |
| Funding rate     | Periodic transfer between long and short perpetual positions.               | Can help or hurt net returns even if price is unchanged.                               |
| Mark price       | Exchange reference used for P&L/liquidation on derivatives.                 | May differ from last traded price.                                                     |
| Index price      | Reference constructed from external/spot markets.                           | Often anchors mark price and reduces manipulation sensitivity.                         |

## Long and short P&L intuition

**Long P&L**

$${PnL}_{long} = q\left( P_{exit} - P_{entry} \right)$$

For a long position, a price increase produces positive P&L before costs.

> **EXAMPLE: Long P&L**  
> Buy 0.01 BTC at ₹50,00,000 and exit at ₹50,50,000. Gross P&L = 0.01 × ₹50,000 = ₹500.

**Short P&L**

$${PnL}_{short} = q\left( P_{entry} - P_{exit} \right)$$

For a short position, falling prices produce positive P&L before costs.

> **EXAMPLE: Short P&L**  
> Short 0.01 BTC at ₹50,00,000 and cover at ₹49,50,000. Gross P&L = 0.01 × ₹50,000 = ₹500.

> **KEY TAKEAWAY: Crypto-specific reality**  
> Crypto trades 24/7, can shift regimes quickly, and often shows large volatility and uneven liquidity across assets. Perpetual futures add funding and exchange-specific mechanics. For a hackathon, keep derivative theory light but model the actual instrument rules used by the platform.

# PART 3 — PRICE DATA AND MARKET DATA

Before the strategy sees a number, the data pipeline has already made decisions about timestamps, aggregation, missing values, duplicate records, and what each candle actually represents. Many apparent trading edges are nothing more than data-processing mistakes.

## How markets become data

- Tick data records individual market updates. It can be extremely detailed and noisy.

- Trade data records executed trades: timestamp, price, size, and sometimes aggressor side.

- OHLCV candles compress many trades into open, high, low, close, and volume for a fixed interval.

- A timeframe is the aggregation interval: 1 minute, 5 minutes, 1 hour, and so on. A feature that works on hourly bars may behave very differently on one-minute bars.

- Rolling windows calculate a statistic over the most recent N observations. They are convenient, but every window must use only information available at that timestamp.

**Simple return**

$$r_{t} = \frac{P_{t}}{P_{t - 1}} - 1$$

The simple return is the percentage change from the previous price to the current price.

> **EXAMPLE: Simple return**  
> BTC from ₹50,00,000 to ₹50,50,000: return = 50,50,000 / 50,00,000 - 1 = 1.0%.

**Log return**

$$g_{t} = \ln\left( \frac{P_{t}}{P_{t - 1}} \right)$$

Log returns add naturally across time. For small price moves they are close to simple returns.

**Cumulative simple return**

$$R = \frac{P_{end}}{P_{start}} - 1$$

This is the total percentage change between two prices when no intermediate cash flows matter.

**Rolling return**

$$R_{t}(N) = \frac{P_{t}}{P_{t - N}} - 1$$

This asks how much price changed over the most recent N candles and is a common momentum feature.

## Why returns are often more useful than raw prices

Raw BTC prices at ₹20 lakh and ₹60 lakh are on different absolute scales even if their market behavior is similar. Returns normalize changes into percentages, making periods and assets more comparable. Returns are also closer to the object that determines P&L. Price levels can still be useful—for example relative to a moving average—but strategy evaluation should normally focus on returns and exposure.

## Close-to-close, open-to-close, and high/low information

Close-to-close return measures the change between consecutive closing prices. Open-to-close isolates movement during a candle. High and low contain intraperiod range information and are useful for volatility estimates, breakouts, and stop logic. The crucial question is availability: if you trade at the open of a candle, you do not yet know that candle’s close, high, or low.

### Timestamp alignment and leakage

**In simple language.** Every value must be aligned to the moment it became knowable.

**Technical view.** If a candle labeled 10:00 covers trades from 10:00:00 through 10:04:59, its final close/high/low are not known at 10:00. A signal using those fields can only be acted on after the candle closes unless the backtest explicitly models intrabar information.

**Why it matters.** One-bar timestamp mistakes can turn an ordinary strategy into a spectacular fake strategy.

> **EXAMPLE: Timestamp alignment and leakage**  
> Compute an RSI using the 12:00 candle close, then assume an order filled at the 12:00 candle open. The strategy used information from the future within the same bar.

> **COMMON MISTAKE: Timestamp alignment and leakage**  
> Using pandas rolling features without shifting them to the first tradable timestamp, or forward-filling data that was not yet published.

> **BOT DESIGN IMPLICATION: Timestamp alignment and leakage**  
> Document each column as 'event time' and 'availability time'. In code, shift signals so execution occurs no earlier than the next realistically tradable instant.

## Data cleaning checklist

- Convert all timestamps to one timezone, ideally UTC internally.

- Sort strictly by timestamp; verify monotonicity.

- Remove exact duplicates and investigate conflicting duplicates.

- Identify missing candles instead of silently interpolating price jumps.

- Treat exchange outages and zero-volume candles separately from ordinary missing data.

- Flag extreme outliers and confirm whether they are real market events or bad ticks.

- Resample with explicit OHLCV aggregation rules rather than taking arbitrary rows.

- Store raw data separately from cleaned data so transformations are reproducible.

# PART 4 — STATISTICS EVERY ALGORITHMIC TRADER SHOULD KNOW

*Measure behavior before judging performance*

## Descriptive statistics

Mean and median summarize central tendency. The median is more resistant to extreme events, which is useful because crypto return distributions can contain large jumps. Variance and standard deviation measure dispersion; in trading, standard deviation of returns is commonly used as volatility. Percentiles answer questions such as 'what does a bad 5th-percentile return look like?' Skewness measures asymmetry, while kurtosis describes how much tail behavior differs from a normal distribution.

**Variance**

$$Var(X) = \mathbb{E}\left[ \left( X - \mathbb{E}[ X] \right)^{2} \right]$$

Variance is the average squared distance from the mean. Squaring makes large deviations matter much more than small ones.

**Standard deviation**

$$\sigma = \sqrt{Var(X)}$$

Standard deviation returns dispersion to the original units. For return series it is the foundation of conventional volatility measures.

## Trading-specific statistics

**Expected value / expectancy**

$$EV = p_{win}\,\overline{W} - p_{loss}\,\overline{L}$$

A strategy makes money on average only if the weighted gains exceed the weighted losses, before considering changing regimes and costs.

> **EXAMPLE: Expected value / expectancy**  
> Win rate 40%, average win ₹300, average loss ₹120: EV = 0.40×300 - 0.60×120 = ₹48 per trade before costs.

> **KEY TAKEAWAY: Why win rate alone is nearly useless**  
> A 90% win-rate strategy can lose money if the occasional loss is enormous. A 35% win-rate trend strategy can be profitable if winners are much larger than losers. Always combine win rate with average win, average loss, profit factor, drawdown, and trade count.

**Profit factor**

$$\text{Profit Factor} = \frac{\text{Gross Profit}}{\left\lvert \text{Gross Loss} \right\rvert}$$

Values above 1 mean the historical gross profits exceeded the historical gross losses. It says nothing by itself about sample size, stability, or drawdown.

**Maximum drawdown**

$$MDD = \max_{t}\left( \frac{{Peak}_{t} - {Equity}_{t}}{{Peak}_{t}} \right)$$

Maximum drawdown is the largest percentage fall from a previous equity peak to a later trough.

**Sharpe ratio**

$$Sharpe = \frac{mean\left( r - r_{f} \right)}{std(r)}\sqrt{N}$$

Sharpe compares average return to total return variability. Annualization only makes sense if the sampling frequency and return process are handled consistently.

**Sortino ratio**

$$Sortino = \frac{mean\left( r - r_{f} \right)}{\sigma_{downside}}\sqrt{N}$$

Sortino penalizes harmful downside variation rather than all volatility.

**Calmar ratio**

$$Calmar = \frac{\text{Annualized Return}}{\lvert MDD\rvert}$$

Calmar compares return to the worst historical drawdown, which is intuitive for trading systems.

**Table 3 — Performance metrics**

| **Metric**        | **Formula / intuitive definition**      | **Interpretation**                                               |
|-------------------|-----------------------------------------|------------------------------------------------------------------|
| Total return      | Ending equity / starting equity - 1     | Overall growth, but ignores path and risk.                       |
| Annualized return | Geometric scaling of total return       | Comparable across test lengths when annualization is sensible.   |
| Volatility        | Std. dev. of returns, often annualized  | Magnitude of return fluctuations.                                |
| Sharpe            | Mean excess return / volatility         | Return per unit of total variability.                            |
| Sortino           | Mean excess return / downside deviation | Return per unit of downside variability.                         |
| Max drawdown      | Worst peak-to-trough decline            | Pain and capital-loss severity.                                  |
| Calmar            | Annualized return / max drawdown        | Return relative to worst drawdown.                               |
| Win rate          | Winning trades / all closed trades      | Frequency of wins; not profitability.                            |
| Profit factor     | Gross profit / gross loss               | Aggregate gain/loss balance.                                     |
| Expectancy        | Average P&L expected per trade          | Combines win rate and payoff.                                    |
| Turnover          | Amount traded relative to capital       | Higher turnover usually means larger cost sensitivity.           |
| Exposure          | Fraction of capital/time at risk        | Distinguishes return from how aggressively capital was deployed. |

# PART 5 — PROBABILITY AND STATISTICAL THINKING

Trading research is an exercise in uncertainty. A backtest does not reveal a fixed truth; it gives one noisy sample of how a rule interacted with historical markets. Statistical thinking helps us distinguish a genuine edge from a pattern that appeared by luck.

## Core ideas

- A random variable is a numerical outcome whose value is uncertain in advance, such as the next 1-hour BTC return.

- Expected value is the long-run average under a probability model; it is not the outcome of the next trade.

- Conditional probability asks how probabilities change after observing information: P(up next candle \| volatility is high).

- Covariance and correlation describe how two variables move together; correlation is scaled to lie between -1 and 1.

- Regression estimates how an outcome changes with one or more predictors, but a fitted relationship can be unstable and does not automatically imply causation.

- A confidence interval describes a range of plausible parameter values under modeling assumptions. A p-value is not the probability that a trading strategy is real or profitable.

### Noise vs signal

**In simple language.** Signal is repeatable information; noise is random variation that can look meaningful in small samples.

**Technical view.** If observed returns are y_t = alpha_t + epsilon_t, the tradable part is the persistent component alpha_t; epsilon_t is unpredictable noise. Real markets usually have very low signal-to-noise ratios.

**Why it matters.** A tiny effect can be economically valuable if stable, while a huge in-sample effect may be fake if selected from many trials.

> **EXAMPLE: Noise vs signal**  
> A rule earns +18% in one three-month bull market but disappears in all other periods. That is evidence of regime exposure, not proof of a universal edge.

> **COMMON MISTAKE: Noise vs signal**  
> Treating a colorful chart or single high Sharpe as enough evidence.

> **BOT DESIGN IMPLICATION: Noise vs signal**  
> Require out-of-sample validation, parameter stability, cost stress tests, and sufficient trade count before promoting an idea.

## Multiple testing and false discoveries

Suppose you test 200 unrelated strategies that have no true edge. Even if each has only a 5% chance of appearing statistically significant by luck, you should expect roughly 10 false 'discoveries' before adjusting for multiple testing. Trading research makes this worse because researchers often try many assets, timeframes, indicator lengths, entry thresholds, exit rules, and cost assumptions.

> **COMMON MISTAKE: Selection bias**  
> If you try 500 parameter combinations and report only the best curve, the reported performance is conditioned on winning a competition against 499 alternatives. Its apparent Sharpe is biased upward even if every candidate was random.

- Keep an experiment log so you remember rejected ideas.

- Use a validation period for model/parameter choices and reserve the final test period for very few decisions.

- Prefer broad regions of good performance over one isolated optimum.

- Adjust your confidence downward when you have tried many hypotheses.

- Demand economic logic: why should the feature predict returns or improve execution?

# PART 6 — TIME SERIES BASICS

*Markets are ordered in time; the order matters*

A time series is a sequence indexed by time. Unlike ordinary tabular data, rows cannot usually be shuffled because yesterday precedes today and features often depend on past values. Trading models must respect that temporal structure.

### Stationarity

**In simple language.** A stationary series has broadly stable statistical behavior over time.

**Technical view.** Strict stationarity is a mathematical ideal; in practice traders ask whether mean, variance, and dependence structures are stable enough for the model being used.

**Why it matters.** Many statistical methods implicitly assume stability. Prices themselves often trend and change scale, while short-horizon returns can be more stable but still experience regime changes.

> **EXAMPLE: Stationarity**  
> BTC price rises from 20k to 60k USD; a rule based on an absolute $500 move changes meaning. A 1% return feature is more comparable across levels.

> **COMMON MISTAKE: Stationarity**  
> Assuming a relationship estimated in a low-volatility year will remain identical during a crash.

> **BOT DESIGN IMPLICATION: Stationarity**  
> Use normalized features, rolling estimation, regime diagnostics, and robustness tests across periods.

## Momentum, mean reversion, breakout, and trend following

**Strategy mechanism comparison**

| **Mechanism**   | **Core belief**                            | **Simple example**              | **Typical danger**                                   |
|-----------------|--------------------------------------------|---------------------------------|------------------------------------------------------|
| Momentum        | Recent relative strength persists          | Long if 20-candle return \> +2% | Buying exhausted moves; high turnover.               |
| Trend following | Sustained directional movement persists    | Fast MA above slow MA           | Whipsaws in sideways markets.                        |
| Breakout        | Price leaving a range can start a new move | Buy above 20-day high           | False breakouts and slippage.                        |
| Mean reversion  | Extreme deviations partially reverse       | Long when z-score \< -2         | Catching a falling knife during a real regime shift. |

Momentum and trend following overlap: both bet on persistence. Momentum often describes a return-based ranking or threshold, while trend following often refers to persistent directional filters such as moving-average rules. Breakouts are a specific way to enter when price exceeds a recent boundary. Mean reversion takes the opposite view and expects extreme deviations to normalize.

## Autocorrelation, regimes, and volatility clustering

Autocorrelation measures whether a series is correlated with its own lagged values. Raw short-horizon returns often have weak linear autocorrelation, but absolute or squared returns can show stronger persistence: volatile periods tend to be followed by volatile periods. This is volatility clustering. A regime is a period in which market behavior—trend strength, volatility, correlations, liquidity—differs meaningfully from another period. Robust systems should not assume one regime lasts forever.

> **KEY TAKEAWAY: Regime awareness**  
> A mean-reversion system that thrives in calm range-bound conditions can suffer badly during a persistent trend. A trend system can lose repeatedly in choppy markets. Combining complementary systems or changing exposure based on observable regime features may improve robustness, but the regime rule itself must be tested out-of-sample.

# PART 7 — FEATURES AND INDICATORS

A feature is a numerical representation of information available to the strategy. Indicators such as RSI or MACD are simply engineered features. Their value comes from the hypothesis they represent and whether that hypothesis survives robust testing—not from the indicator’s popularity.

## Price-based features

- 1-, 5-, 20-, or N-candle returns and rolling returns.

- Simple and exponential moving averages (SMA/EMA). EMA weights recent observations more heavily.

- Price relative to a moving average, often expressed as a percentage or z-score.

- Momentum and rate of change (ROC), which quantify recent directional movement.

## Volatility features

- Rolling standard deviation of returns.

- Average True Range (ATR), which incorporates gaps/ranges in a price-scaled way.

- Realized volatility from high-frequency or bar returns.

- Volatility percentile: current volatility relative to its own recent history.

## Volume features

- Percentage change in volume.

- Relative volume = current volume / rolling average volume.

- Volume moving averages and volume z-scores.

- Price-volume confirmation, e.g. breakouts accompanied by unusually high volume.

## Common technical indicators

**Indicators as features, not magic**

| **Indicator**         | **What it summarizes**                                        | **Reasonable hypothesis to test**                                                    |
|-----------------------|---------------------------------------------------------------|--------------------------------------------------------------------------------------|
| RSI                   | Magnitude of recent up vs down moves                          | Extreme short-term moves may mean-revert in some regimes.                            |
| MACD                  | Difference between fast and slow EMAs, often with signal line | Trend/momentum acceleration may persist.                                             |
| Bollinger Bands       | Moving average ± k standard deviations                        | Distance from a volatility-scaled reference may revert or break out.                 |
| Stochastic oscillator | Close relative to recent high-low range                       | Location near extremes may indicate momentum or exhaustion.                          |
| VWAP                  | Volume-weighted average traded price                          | Price relative to session volume center may matter for intraday execution/reversion. |

> **KEY TAKEAWAY: Correct research sequence**  
> Indicator → economic or behavioral hypothesis → precise signal rule → realistic backtest → out-of-sample validation. Never use the sequence 'indicator → guaranteed profit.'

> **BOT DESIGN IMPLICATION: Feature scaling**  
> When combining features, put them on comparable scales—for example z-scores, ranks, or clipped standardized values. Otherwise a numerically large feature can dominate a composite signal for no economic reason.

# PART 8 — SIGNAL GENERATION

A signal converts features into a trading belief. It might say 'bullish,' 'bearish,' or 'the expected opportunity is +0.4 on a standardized scale.' A signal is not yet a trade. The position layer decides how strongly to act on the signal after considering risk and constraints.

## Signal forms

- Binary: +1 long, 0 flat, -1 short.

- Continuous: any value, e.g. -1.0 to +1.0, preserving confidence/strength.

- Threshold-based: act only when a feature crosses a boundary.

- Ranking: compare assets and long the strongest / short the weakest.

- Composite: combine multiple standardized features.

- Confirmation: require an additional condition such as volume or regime filter.

**Figure 2 — Strategy → signal → position → execution → P&L**

```text
Hypothesis: strong trends persist
|
v
Feature: 20-bar return, MA slope, volatility
|
v
Signal: +0.75 bullish confidence
|
v
Position: +30% capital after volatility/risk limits
|
v
Execution: buy with chosen order type and fill model
|
v
P&L: price movement - fees - spread - slippage - funding
```

Signal strength expresses belief; position expresses actual exposure.

## Example signals

- Moving-average crossover: +1 when fast MA \> slow MA, -1 otherwise.

- RSI mean reversion: +1 when RSI \< 30, -1 when RSI \> 70, with a neutral zone in between.

- Momentum threshold: +1 if 20-candle return \> +2%; -1 if \< -2%.

- Breakout: enter long when price closes above the previous N-candle high; short below the N-candle low.

- Volatility breakout: trade directionally only when price movement exceeds a volatility-scaled threshold.

- Mean reversion around moving average: signal = negative standardized distance from price to moving average.

> **KEY TAKEAWAY: Signal ≠ position**  
> A +1 signal does not have to mean 100% long. The position may be +10% in a volatile asset, +40% in a calmer asset, or 0% if the portfolio risk limit is already reached.

# PART 9 — POSITION SIZING

Position sizing is the transformation from a view into money at risk. It is one of the strongest determinants of drawdown and survival. Even an excellent signal can produce a terrible system if size is too large, concentration is uncontrolled, or volatility changes without adjustment.

**Notional position**

$$\text{Position Notional} = \text{Equity} \times \text{Target Weight}$$

A 20% target weight on ₹1,00,000 equity means ₹20,000 notional exposure.

**Volatility-scaled weight**

$$w \propto \frac{s}{\widehat{\sigma}}$$

For equal signal strength, lower-volatility assets receive larger positions and high-volatility assets smaller ones. Caps are essential.

**Risk-per-stop sizing**

$$q = \frac{\text{Risk Budget}}{\left\lvert P_{entry} - P_{stop} \right\rvert}$$

If the strategy intentionally uses a stop and wants to lose at most a fixed amount when the stop is reached, quantity can be sized from stop distance. This does not guarantee the loss cap because gaps and slippage exist.

## Common sizing approaches

- Fixed sizing: same notional or weight on each trade. Simple but ignores changing volatility.

- Equal allocation: split capital equally across active assets or strategies.

- Volatility-based sizing: target similar risk contribution by scaling inversely with estimated volatility.

- Risk-based sizing: allocate from a defined risk budget and expected loss scale.

- Confidence-based sizing: stronger signals receive larger weights, usually after clipping and calibration.

- Maximum position limits: hard cap per asset to prevent one signal from dominating the portfolio.

> **KEY TAKEAWAY: Sizing and drawdown**  
> Doubling every position approximately doubles both expected P&L and the size of ordinary losses, and can more than double practical risk when liquidity, nonlinear limits, and correlation are involved. Always analyze strategy behavior at multiple exposure levels.

> **BOT DESIGN IMPLICATION: Bot design**  
> Make position sizing a separate module. The strategy should output a desired normalized signal; the sizing/risk layers should decide exposure based on equity, volatility, caps, and portfolio state.

# PART 10 — LONG/SHORT STRATEGIES

Long-only systems can profit only from upward moves. Long/short systems can express both positive and negative views, but they introduce asymmetric behavior: crashes, short squeezes, funding, and liquidity can affect the short side differently from the long side.

- Directional long/short: exposure varies from negative to positive based on market view.

- Market neutral: attempt to keep overall market exposure near zero, for example long strong assets and short weak assets.

- Long-only / short-only: intentionally restrict one side, useful if platform rules or evidence supports asymmetric treatment.

**Return-based P&L approximation**

$$PnL \approx \text{Position Notional} \times \text{Signed Return}$$

Use positive signed exposure for longs and negative signed exposure for shorts. Fees and slippage are deducted separately.

> **EXAMPLE: Return-based P&L approximation**  
> If notional is ₹20,000 and BTC moves +1%, a long earns about ₹200 gross; a short loses about ₹200 gross.

## Shorting in crypto/perpetual markets

On perpetual futures, short positions are straightforward operationally but may pay or receive funding depending on market conditions. Liquidation mechanics are not central at approximately 1× leverage, but exchange rules still matter. Shorts can also encounter sharp squeezes and worse slippage during panic reversals. Backtests should report long and short performance separately so a strong long side does not hide a weak short side.

> **CHECKPOINT: Asymmetry check**  
> Measure hit rate, average trade, drawdown, and turnover for longs and shorts separately. If the short side is consistently unprofitable after costs, forcing symmetry into the bot may reduce performance.

# PART 11 — BACKTESTING

*The main laboratory for strategy research*

A backtest simulates how a fully specified trading system would have behaved on historical data. It is not a chart of a signal and not a list of hypothetical entries. A credible backtest has explicit timing, positions, fills, fees, slippage, cash/equity accounting, and rules for every transition.

## Minimal backtest components

> **1.** Load and validate historical data.
>
> **2.** Compute features using only past and currently available information.
>
> **3.** Generate signals at well-defined timestamps.
>
> **4.** Convert signals to target positions using sizing and risk rules.
>
> **5.** Generate trades needed to move from current to target position.
>
> **6.** Simulate fills with realistic execution prices, fees, spread, and slippage.
>
> **7.** Update cash, positions, mark-to-market value, and equity.
>
> **8.** Store every event so results can be reproduced and audited.
>
> **9.** Compute return, risk, trade, and exposure metrics.

**Figure 3 — Conceptual backtesting architecture**

```text
Historical Market Data
|
v
Data Validator ---> Feature Engine ---> Signal Engine
|
v
Target Position
|
v
Risk Layer
|
v
Order / Fill Simulator
|
+-----------------+----------------+
| |
v v
Portfolio Accounting Trade Log
|
v
Equity Curve + Metrics
```

A backtest should resemble the live system closely enough that strategy logic is not rewritten at deployment time.

## Vectorized vs event-driven backtesting

**Backtesting approaches**

| **Approach** | **How it works**                                                                     | **Advantages**                                                                            | **Disadvantages**                                                                        |
|--------------|--------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| Vectorized   | Use arrays/dataframes to compute signals and returns across many timestamps at once. | Fast, concise, excellent for early research and simple close-to-close strategies.         | Easy to hide timing mistakes; awkward for partial fills, intrabar orders, complex state. |
| Event-driven | Process each market/event timestamp sequentially, updating state and orders.         | Closer to live trading; handles order state, fills, multiple assets, and path dependence. | More code, slower, and easier to introduce engineering bugs.                             |

A sensible hackathon workflow is to explore simple ideas vectorized, then validate finalists in a more explicit sequential simulation. If the competition platform itself is event-based, sharing the same strategy interface between the backtester and live runner reduces implementation drift.

## Portfolio accounting

At every timestamp, the backtester should know current cash, asset quantities, entry/reference values, unrealized P&L, realized P&L, fees, and total equity. Position changes must be applied exactly once. A common beginner bug is double-counting return when both position value and trade cash flow are updated inconsistently.

> **KEY TAKEAWAY: A backtest is an experiment**  
> Good historical performance raises a hypothesis from 'interesting' to 'worth further testing.' It does not prove future profitability. The next steps are out-of-sample testing, robustness analysis, cost stress, and paper trading.

# PART 12 — HOW TO ACCIDENTALLY FAKE A PROFITABLE STRATEGY

Most spectacular beginner backtests are wrong for boring reasons. The danger is that the output still looks professional: smooth equity curves, high Sharpe, and many winning trades. Treat the following failure modes as adversarial tests against your own research.

**Table 4 — Common backtesting biases**

| **Bias / mistake**          | **What it means**                                     | **Concrete example**                                                | **How to prevent it**                                             |
|-----------------------------|-------------------------------------------------------|---------------------------------------------------------------------|-------------------------------------------------------------------|
| Look-ahead bias             | Using information not yet known at the trade time.    | Use current candle close to buy at the same candle open.            | Shift features/signals to the first realistic execution time.     |
| Data leakage                | Future information enters a feature/model indirectly. | Normalize all data using mean/std computed over the entire dataset. | Fit transforms on training history only; update causally.         |
| Selection bias              | Only attractive assets/periods are included.          | Backtest coins chosen because they later became winners.            | Define universe using information available historically.         |
| Survivorship bias           | Failed/delisted assets disappear from the dataset.    | Use only today’s surviving top coins in a historical portfolio.     | Use point-in-time universe when relevant.                         |
| Overfitting                 | Rules adapt to historical noise.                      | 25 thresholds tuned until equity curve is perfect.                  | Reduce complexity; validate on unseen data.                       |
| Curve fitting               | Parameter choices target quirks of one sample.        | MA lengths 17 and 43 beat nearby combinations only.                 | Look for parameter plateaus, not spikes.                          |
| Timestamp error             | Data labels do not equal availability times.          | Hourly candle stamped 10:00 contains trades through 10:59.          | Document bar conventions and availability.                        |
| Unrealistic fills           | Assume every trade fills at ideal price.              | Buy at low of candle after seeing high/close.                       | Use next-bar/open or bid/ask + slippage model.                    |
| Ignoring fees               | Gross returns reported as net.                        | 0.05% edge per trade with 0.10% round-trip fees.                    | Deduct actual maker/taker fees on every fill.                     |
| Ignoring slippage/liquidity | Trade size has no price effect.                       | Assume large market order fills at mid-price.                       | Stress slippage and cap size to liquidity.                        |
| Repeated test-set use       | Test period becomes another validation set.           | Keep checking test result after every tweak.                        | Use test set rarely; create a new truly unseen holdout if needed. |
| Excessive tuning            | Too many choices relative to data.                    | Search 10,000 parameter combinations on 6 months of data.           | Limit degrees of freedom and record number of experiments.        |

## Three especially dangerous fake-edge patterns

### Using future candle information

**In simple language.** The bot cannot know how the candle ends before it ends.

**Technical view.** OHLC bars are summaries. If the signal depends on close/high/low of bar t, the earliest safe action is after those values become final, usually bar t+1 in a bar-close system.

**Why it matters.** This is the single most common source of impossible fills in simple research code.

> **EXAMPLE: Using future candle information**  
> Signal = close_t > moving_average_t, return credited from open_t to close_t. The close that creates the signal is also inside the return being earned.

> **COMMON MISTAKE: Using future candle information**  
> Using dataframe multiplication signal_t × return_t without checking which price interval each variable represents.

> **BOT DESIGN IMPLICATION: Using future candle information**  
> Write a timeline diagram for every strategy and assert in code that feature timestamp <= decision timestamp <= order timestamp <= fill timestamp.

### Ignoring trading frictions

**In simple language.** Small edges disappear when every entry and exit has a cost.

**Technical view.** Net return = gross strategy return - explicit fees - spread crossing - slippage - funding/borrow costs. Costs are typically proportional to turnover and can dominate high-frequency rules.

**Why it matters.** The ranking of strategies can reverse after costs.

> **EXAMPLE: Ignoring trading frictions**  
> A strategy makes +0.08% per trade before costs, trades twice per round trip, and pays 0.05% each side. It is already negative before slippage.

> **COMMON MISTAKE: Ignoring trading frictions**  
> Backtesting on close prices with zero fees because 'the competition is simulated.' Simulators still have rules, spreads, or scoring mechanics that must be modeled.

> **BOT DESIGN IMPLICATION: Ignoring trading frictions**  
> Make cost assumptions configurable. Report gross and net results side by side and stress costs at 1×, 1.5×, and 2× baseline.

### Repeatedly optimizing on the test set

**In simple language.** If you keep looking at the final answer while changing your strategy, it stops being a final answer.

**Technical view.** The test set estimates generalization only if model choices are independent of its outcomes. Once test results influence decisions, the test set becomes part of training/validation.

**Why it matters.** This produces hidden selection bias and overstates future performance.

> **EXAMPLE: Repeatedly optimizing on the test set**  
> Version 1 fails on test. You change RSI threshold after seeing the failure, test again, and repeat until it passes.

> **COMMON MISTAKE: Repeatedly optimizing on the test set**  
> Calling data 'out of sample' while using it for dozens of development decisions.

> **BOT DESIGN IMPLICATION: Repeatedly optimizing on the test set**  
> Lock test results behind a final evaluation step. Track decisions and, if necessary, obtain a new untouched holdout period.

# PART 13 — TRAINING / VALIDATION / TESTING

Trading data must usually be split in chronological order. The training period is where models are fitted and broad ideas are built. Validation is where hyperparameters and design alternatives are compared. Test is the final, minimally touched estimate of how the chosen process generalizes to unseen time.

**Figure 4 — Train → validation → test time-series split**

```text
TIME --------------------------------------------------------------->
|---------------- TRAIN ----------------|------ VALID ------|-- TEST --|
learn feature transforms / fit model choose params final check
Walk-forward example:
[Train 1][Val 1]
[Train 2][Val 2]
[Train 3][Val 3] ...
```

Time-order is preserved. Random shuffling would allow future regimes to inform models evaluated on the past.

## Why random train-test splits are usually inappropriate

Randomly shuffling candle rows breaks temporal dependence and can place observations from the same market episode into both train and test sets. A model may effectively learn from the future. Example: a volatility regime in March appears in both sets because individual rows were shuffled, even though a live system in February could not have seen March.

## Walk-forward, rolling, and expanding windows

- Walk-forward: repeatedly train on past data and evaluate on the next unseen block. This approximates how a strategy would be refreshed over time.

- Rolling window: keep a fixed-length training window that moves forward, allowing old regimes to drop out.

- Expanding window: training history grows over time, preserving all earlier data.

- Choose the scheme based on how you expect the live strategy to be maintained. Do not select the scheme solely because it gives the best backtest.

> **KEY TAKEAWAY: Model-selection discipline**  
> Training answers 'can we fit this?' Validation answers 'which reasonable version should we choose?' Test answers 'how did that final choice behave on data that did not influence it?'

# PART 14 — OVERFITTING

Overfitting occurs when a strategy learns historical details that do not repeat. The core trade-off is flexibility: more parameters can fit more patterns, including meaningless noise. A simple rule can overfit too if it was selected after enough experiments, while a complex model can generalize if data, validation, and regularization are strong. The point is not 'complexity is bad'; it is 'unjustified flexibility is dangerous.'

**Simple robustness comparison**

| **Property**      | **Strategy A**      | **Strategy B**        |
|-------------------|---------------------|-----------------------|
| Parameters        | 2                   | 25                    |
| In-sample result  | Modest              | Spectacular           |
| Nearby parameters | Similar performance | Performance collapses |
| Different periods | Mostly positive     | Highly unstable       |
| Out-of-sample     | Still acceptable    | Poor                  |
| Interpretation    | Promising candidate | Likely overfit        |

## How to identify an overfit strategy

- Performance is concentrated in a tiny parameter combination.

- Small changes in lookback or threshold destroy results.

- Most profit comes from one period, one asset, or a few trades.

- The strategy uses many filters that each remove a historical losing episode.

- In-sample Sharpe is excellent but validation/test performance falls sharply.

- Performance degrades far more than expected after modestly higher costs.

- A complex model has unstable feature importance or predictions across retraining windows.

> **COMMON MISTAKE: Degrees of freedom**  
> Count every meaningful research choice: assets, dates, timeframes, indicator families, thresholds, exits, sizing methods, and filters. Even if the final strategy has three parameters, it can embody hundreds of hidden choices made during exploration.

# PART 15 — ROBUSTNESS TESTING

*Try to break the strategy before the market does*

Robustness testing asks whether the core result survives reasonable perturbations. A strategy that requires one exact parameter, one exact cost assumption, and one exact market period is fragile even if its headline return is high.

- Parameter perturbation: test neighboring lookbacks and thresholds.

- Different periods: bull, bear, sideways, high-volatility, and low-volatility windows.

- Different assets: test whether the economic mechanism transfers when appropriate.

- Different timeframes: avoid assuming one bar size is uniquely magical.

- Increased transaction costs and slippage: 1.5×–2× stress is a useful starting point.

- Randomized execution: delay entries or perturb fill prices within plausible ranges.

- Monte Carlo: resample trades/returns to understand distribution of possible equity paths.

- Trade-order randomization: shows how path-dependent drawdown may vary for the same set of trade outcomes.

- Stress testing: inject outages, missed fills, volatile gaps, and exposure caps.

### Parameter stability

**In simple language.** Good strategies tend to live on a hill or plateau, not a needle.

**Technical view.** If performance f(θ) is strong only at θ=17 but weak at 16 and 18, the optimum is probably sampling noise. Stable neighborhoods suggest the mechanism is less dependent on exact calibration.

**Why it matters.** Future data will not reproduce historical noise exactly.

> **EXAMPLE: Parameter stability**  
> MA crossover with fast lengths 18–24 and slow lengths 45–60 all performs reasonably; that is healthier than only 21/53 working.

> **COMMON MISTAKE: Parameter stability**  
> Selecting the single top Sharpe from a heatmap and ignoring the surrounding landscape.

> **BOT DESIGN IMPLICATION: Parameter stability**  
> Choose parameters from stable regions, then lock them before the test period.

> **KEY TAKEAWAY: Robust does not mean universally profitable**  
> A robust strategy can still lose for long periods or fail when market structure changes. Robustness means the historical evidence is less dependent on fragile choices, not that risk has disappeared.

# PART 16 — PERFORMANCE METRICS

Never rank strategies by total return alone. A strategy that earns 40% with a 10% drawdown and diversified trade distribution is a different object from one that earns 40% with a 45% drawdown and one lucky trade. Evaluation should describe return, risk, consistency, activity, and capital usage.

## Return and risk metrics

- Total return: net growth over the entire test period.

- CAGR / annualized return: time-normalized compound return when the period is long enough to make annualization meaningful.

- Volatility: variability of strategy returns.

- Sharpe / Sortino: risk-adjusted return measures; useful but sensitive to assumptions and non-normal tails.

- Maximum and average drawdown: depth of losses from prior equity peaks.

- Drawdown duration: how long the strategy remains below a previous high-water mark.

- Calmar: annualized return relative to maximum drawdown.

## Trade and activity metrics

- Win rate, average win, average loss, payoff ratio, profit factor, and expectancy.

- Average trade: net P&L per trade. Tiny average trade is vulnerable to cost estimation error.

- Number of trades: confidence in trade-level statistics improves with more independent evidence, not just more correlated micro-trades.

- Turnover: how aggressively the portfolio trades; directly linked to friction sensitivity.

- Exposure: fraction of capital/time employed.

- Long vs short performance: identifies directional asymmetry.

**Figure 5 — Typical equity curve and drawdown**

```text
Equity
^ new peak
| ________/\_____
| _____/ \____
| ____/ \__
|____/
+--------------------------------------------------> time
peak
|\
| \_____ trough
| \
|<-- drawdown -->|
```

Drawdown is measured from a prior equity peak, not from the starting capital.

> **KEY TAKEAWAY: Equity curve shape matters**  
> Smoothness is not automatically good—some fake backtests look perfectly smooth because risk and costs were modeled incorrectly. But the path still matters: persistent gains across many trades/periods are generally more credible than a return dominated by one short episode.

# PART 17 — TRANSACTION COSTS AND EXECUTION

Gross P&L is what the strategy would earn if trading were free and fills were ideal. Net P&L is what remains after all trading frictions. For high-turnover systems, the difference is often the difference between success and failure.

- Maker fee: charged when an order adds liquidity and rests in the book, depending on venue rules.

- Taker fee: charged when an order removes liquidity, commonly by crossing the spread.

- Spread: immediate cost between best bid and ask.

- Slippage: difference between expected/reference price and actual fill price.

- Market impact: price movement caused by your own order, especially at larger sizes.

- Order-book depth: quantity available at progressively worse prices.

- Turnover: more trading generally means more cumulative cost.

**Net P&L**

$$Net\ PnL = Gross\ PnL - \text{Fees} - \text{Spread Cost} - \text{Slippage} - \text{Funding/Other Costs}$$

Backtests should expose each cost component rather than hiding everything in one adjustment.

> **EXAMPLE: Numerical cost example**  
> Suppose a strategy makes +0.12% gross per round trip. Entry and exit each incur a 0.04% taker fee, plus 0.015% slippage each side. Estimated round-trip cost = 2×(0.04%+0.015%) = 0.11%. Net edge is only about 0.01% before funding. A tiny modeling error can erase it.

> **KEY TAKEAWAY: Hackathon lesson**  
> Prefer strategies whose average expected move is meaningfully larger than plausible transaction friction. If the backtest requires perfect fills to stay positive, it is not robust enough for deployment.

# PART 18 — MARKET MICROSTRUCTURE

Market microstructure studies how orders become trades. You do not need deep theory for a beginner hackathon, but you do need enough to avoid impossible execution assumptions.

**Figure 6 — Order book / bid-ask spread**

```text
SELL LIMITS (asks)
Price Size
100.12 1.6 BTC
100.08 0.9 BTC
100.05 0.4 BTC <- best ask
-------------------- <- spread = 100.05 - 100.00 = 0.05
100.00 0.7 BTC <- best bid
99.97 1.2 BTC
99.94 2.0 BTC
BUY LIMITS (bids)
```

A market buy consumes asks; a market sell consumes bids. Large orders may walk through multiple levels.

Order-book imbalance compares displayed buy and sell depth and can be used as a short-horizon feature, but the book can change rapidly and displayed size can be canceled. Price impact is the adverse movement associated with consuming liquidity. A limit order may avoid crossing the spread, but its fill probability is uncertain and fills can be adverse: you may be filled exactly when informed traders want to trade against you.

> **BOT DESIGN IMPLICATION: Execution assumption**  
> If your strategy trades on candle close, the cleanest beginner assumption is often to execute on the next candle open or on a documented bid/ask/slippage model. Do not assume you can inspect a completed candle and still trade at its closing price unless the competition simulator explicitly makes that possible.

# PART 19 — RISK MANAGEMENT

*Survival is part of the strategy*

Risk management is not an emergency patch applied after the strategy is built. It is part of the system design. Risk rules control how much capital can be lost because of ordinary strategy variance, correlated positions, operational errors, and rare market events.

## Core controls

- Maximum position size per asset.

- Maximum gross portfolio exposure and, where relevant, maximum net directional exposure.

- Volatility targeting so position risk shrinks when the market becomes more volatile.

- Drawdown limits that reduce or stop new risk after large losses.

- Daily loss limits for operational containment.

- Concentration limits across highly correlated assets.

- Leverage caps and liquidation buffers where derivatives are used.

- Order-size and rate limits to prevent runaway execution bugs.

## Stops and take-profits

A stop loss exits after an adverse move; a take profit exits after a favorable move. Neither is universally beneficial. A stop can reduce tail losses but also crystallize noise and create repeated whipsaws. A take profit can lock gains but cut off the long right tail that makes trend-following systems profitable. Treat both as parameters of the trading hypothesis and test them with realistic intrabar execution.

### Risk of ruin

**In simple language.** Risk of ruin is the chance of losing so much capital that the strategy cannot realistically recover or continue.

**Technical view.** Even a positive-expectancy process can have a high ruin probability if position sizes are too large relative to equity and loss distribution. Compounding makes large drawdowns especially damaging: a 50% loss requires a 100% gain to recover.

**Why it matters.** Survival allows the edge to play out over many trades; oversized bets can destroy that opportunity.

> **EXAMPLE: Risk of ruin**  
> A strategy with +0.2% expected trade return but occasional -15% losses can fail if it puts most capital into each trade.

> **COMMON MISTAKE: Risk of ruin**  
> Using maximum historical loss as if it were the worst possible future loss.

> **BOT DESIGN IMPLICATION: Risk of ruin**  
> Set position and portfolio caps from stress scenarios, not just average volatility. Include kill switches for anomalous behavior.

> **KEY TAKEAWAY: Risk manager must be able to say no**  
> The risk layer should be allowed to reduce or reject a strategy’s desired position. Strategy logic proposes; risk constraints dispose.

# PART 20 — PORTFOLIO CONSTRUCTION

When multiple assets or strategies are active, risk is determined by how they move together—not by evaluating each in isolation. Ten altcoins can look diversified while behaving like one leveraged crypto-beta position during a market shock.

## Allocation approaches

- Equal weighting: simple and transparent, but ignores different volatilities.

- Volatility weighting: scale lower-volatility assets higher and higher-volatility assets lower.

- Risk-parity intuition: aim for more balanced contributions to total portfolio risk rather than equal dollars.

- Signal weighting: allocate more to stronger opportunities after risk adjustment.

- Caps: maximum allocation by asset, sector/category, or correlated cluster.

**Two-asset portfolio variance**

$$Var\left( R_{p} \right) = w_{1}^{2}\sigma_{1}^{2} + w_{2}^{2}\sigma_{2}^{2} + 2w_{1}w_{2}\rho_{12}\sigma_{1}\sigma_{2}$$

The correlation term explains why two risky assets can reduce or increase portfolio risk depending on how their returns co-move.

> **COMMON MISTAKE: Correlation spikes**  
> Diversification is often weakest when you need it most. During broad crypto selloffs, correlations can rise sharply. Stress-test the portfolio using higher correlations than those observed in calm periods.

> **KEY TAKEAWAY: Multiple profitable strategies**  
> Two strategies can each have attractive standalone Sharpe but create excessive drawdown if they lose in the same regime. Combining low-correlated return streams is generally more valuable than simply adding more strategies.

# PART 21 — STRATEGY IDEAS FOR A BEGINNER

*Good starting points for a hackathon*

**Table 2 — Strategy families**

| **Strategy**             | **Core idea**                                | **Example signal**              | **Strength**                           | **Weakness**                   | **Typical regime**      |
|--------------------------|----------------------------------------------|---------------------------------|----------------------------------------|--------------------------------|-------------------------|
| Trend following          | Ride persistent direction                    | MA slope/crossover              | Simple; captures large moves           | Whipsaws                       | Trending                |
| Moving-average crossover | Fast average crossing slow average           | fastMA \> slowMA                | Transparent                            | Lag; choppy losses             | Sustained trends        |
| Momentum                 | Recent winners continue                      | N-bar return rank/threshold     | Easy multi-asset extension             | Reversals/crowding             | Directional persistence |
| Mean reversion           | Extreme moves partially reverse              | z-score vs MA                   | Works in ranges                        | Regime breaks                  | Range-bound             |
| RSI mean reversion       | Momentum extreme reverses                    | RSI\<30 long; \>70 short        | Simple normalization                   | Threshold overfit              | Calm/choppy             |
| Bollinger reversion      | Deviation from vol-scaled band reverses      | buy below lower band            | Adapts to volatility                   | Band riding in trends          | Range-bound             |
| Breakout                 | Range escape starts move                     | close \> N-bar high             | Captures new trends                    | False breakouts                | Transition to trend     |
| Volatility breakout      | Large move relative to normal range persists | return \> k×vol                 | Scale-aware                            | Slippage after jumps           | Volatility expansion    |
| Volume-based             | Volume validates price move                  | breakout + high relative volume | Adds participation context             | Volume noisy/exchange-specific | High participation      |
| Cross-sectional momentum | Strong assets beat weak assets               | rank N-bar returns              | Can reduce market-direction dependence | Correlation/crash reversals    | Dispersion              |
| Multi-factor             | Combine independent signals                  | weighted z-scores               | Diversifies signal sources             | More degrees of freedom        | Mixed                   |

## How to research each family

For every candidate, write the hypothesis before optimizing parameters. Define required data, the exact signal, the time at which it is known, the desired position, execution assumption, fees, exits, and risk limits. Then test a small parameter grid that represents different reasonable timescales—not hundreds of arbitrary values.

### Trend following

- Core idea: Persistent directional moves continue long enough to overcome whipsaws.

- Required data: OHLCV; returns; moving averages.

- Example signal: Long when 20EMA \> 60EMA and slope positive; short on opposite.

- Strengths: Captures rare large directional moves.

- Weaknesses: Repeated small losses in sideways markets.

- Typical failure mode: Trading too fast; ignoring regime-dependent turnover.

- Parameters worth testing: Fast/slow lookbacks, rebalance frequency, volatility scaling.

- Usually strongest when: Strong trends, volatility expansion.

### Mean reversion

- Core idea: Short-term deviations from a reference sometimes normalize.

- Required data: OHLCV; moving average; rolling volatility.

- Example signal: Signal = -zscore(price - MA); trade only beyond threshold.

- Strengths: Can harvest oscillation in ranges.

- Weaknesses: Can suffer badly when a real trend starts.

- Typical failure mode: Assuming every extreme is temporary.

- Parameters worth testing: Lookback, z-threshold, holding period, regime filter.

- Usually strongest when: Range-bound or stable mean behavior.

### Breakout

- Core idea: Leaving a recent range can indicate new information and trend initiation.

- Required data: OHLCV; rolling highs/lows; ATR or volatility.

- Example signal: Long above prior 20-bar high, short below prior 20-bar low.

- Strengths: Simple and interpretable.

- Weaknesses: False breakouts and expensive entry after sudden jumps.

- Typical failure mode: Using current bar high as a prior high; unrealistic fills.

- Parameters worth testing: Range lookback, buffer, exit, volatility filter.

- Usually strongest when: Transitions into directional regimes.

### Cross-sectional momentum

- Core idea: Relative winners continue to outperform relative losers for some horizon.

- Required data: Same-time data for multiple assets.

- Example signal: Rank 24h or 7d returns; long top group, short bottom group.

- Strengths: Can be closer to market-neutral than directional momentum.

- Weaknesses: Needs point-in-time universe and careful normalization.

- Typical failure mode: Survivorship bias and illiquid tail assets.

- Parameters worth testing: Ranking horizon, number of assets, weighting, rebalance frequency.

- Usually strongest when: High cross-sectional dispersion.

> **KEY TAKEAWAY: Choose a few, not all**  
> For a short hackathon, 2–4 well-tested strategy families are more valuable than 20 shallow indicator combinations. Start simple enough that you can diagnose why the system wins or loses.

# PART 22 — COMBINING STRATEGIES

An ensemble combines multiple signals or strategies. The benefit comes when components have different strengths and failure modes. Combining five highly correlated momentum indicators is not meaningful diversification.

- Signal averaging: standardize each signal and take the mean.

- Weighted signals: weights reflect reliability, stability, or risk contribution; weights themselves must be validated.

- Voting: each strategy votes long/flat/short; position depends on consensus.

- Regime-based selection: emphasize trend rules in trending conditions and mean reversion in ranges, if the regime classifier is itself robust.

- Portfolio ensemble: run independent strategies and allocate risk among their P&L streams.

**Weighted composite signal**

$$S = \sum_{i}^{}w_{i}S_{i},\quad\quad\sum_{i}^{}\left\lvert w_{i} \right\rvert\ \text{normalized or capped}$$

Standardize component signals before combining them; otherwise scale differences create accidental weights.

> **KEY TAKEAWAY: Diversify behavior**  
> Prefer components whose errors are not synchronized. A trend follower plus a short-horizon mean-reversion strategy can be more complementary than three versions of the same moving-average crossover.

# PART 23 — MACHINE LEARNING

Machine learning is useful when there are many interacting features or nonlinear relationships that are difficult to express as simple rules. It is unnecessary when a straightforward rule already captures the hypothesis. In trading, ML usually fails because of leakage, unstable regimes, weak signals, and overfitting—not because the team chose the wrong neural-network architecture.

## Features, labels, and prediction targets

Features are the information available at prediction time. Labels are future outcomes used to train the model. A practical target is often more stable than exact price prediction: for example, predict the probability that the next N-candle return is positive or exceeds transaction costs.

**Binary label example**

$$y_{t} = \left\{ \begin{matrix}
1, & r_{t \rightarrow t + N} > \text{cost buffer} \\
0, & \text{otherwise}
\end{matrix} \right.\ $$

The model can estimate $P(y=1 \mid \text{features}_t)$. The trading layer decides whether that probability is strong enough to justify a position.

## Model families

- Logistic/linear regression: strong baselines, interpretable, useful for checking whether complex models really add value.

- Random forests: nonlinear tree ensembles; can capture interactions but may be unstable in time-series settings.

- Gradient boosting: often effective on structured tabular features; powerful enough to overfit if validation is weak.

- Neural networks: flexible and potentially useful for rich sequential inputs, but usually data-hungry and harder to debug.

## Time-series ML rules

- Fit scalers and feature transforms on past training data only.

- Use chronological/walk-forward validation.

- Ensure labels do not overlap in a way that leaks information across splits.

- Monitor feature importance and prediction distributions across time.

- Calibrate predicted probabilities if position sizing depends on them.

- Compare every complex model to a simple baseline after costs.

> **KEY TAKEAWAY: Complexity is not alpha**  
> If a boosted tree increases validation AUC from 0.503 to 0.515, the trading value depends on whether that improvement is stable, concentrated in tradable cases, and large enough after costs. High model complexity does not guarantee high economic value.

# PART 24 — REINFORCEMENT LEARNING

Reinforcement learning (RL) trains an agent to choose actions that maximize cumulative reward. A trading formulation might define state as recent market features and portfolio state, action as target position, reward as net P&L minus risk penalties, and policy as the mapping from state to action.

- State: market features, current position, unrealized P&L, volatility, time since trade.

- Action: long/flat/short or continuous target position.

- Reward: change in portfolio equity after costs, possibly with drawdown/turnover penalties.

- Policy: the learned rule selecting actions from states.

RL is generally much more complicated than necessary for a beginner crypto hackathon. Historical market environments provide only one realized path, simulated rewards are highly sensitive to execution assumptions, and agents can exploit unrealistic simulator quirks. A simple well-tested systematic strategy is usually a better use of limited time.

> **KEY TAKEAWAY: When to skip RL**  
> If the team does not yet have a trusted event-driven simulator, realistic cost model, and strong baselines, adding RL increases the number of ways to overfit without solving the foundations.

# PART 25 — BOT ARCHITECTURE

*Turn research into reliable software*

**Figure 7 — Trading bot software architecture**

```text
[Data Feed]
|
v
[Data Processing]
|
v
[Feature Calculation]
|
v
[Signal Engine]
|
v
[Risk Manager]
|
v
[Position Sizer]
|
v
[Execution Engine] ---> [Exchange API]
| |
+-----------<-------------+
|
v
[Portfolio / P&L Tracker]
|
v
[Logging + Monitoring + Alerts]
```

Keep strategy logic separable from the exchange and execution layer so the same strategy can run in backtest, paper, and live modes.

**Table 5 — Trading bot modules**

| **Module**         | **Responsibility**                                         | **Typical implementation**                  |
|--------------------|------------------------------------------------------------|---------------------------------------------|
| Data feed          | Receive candles/trades/order book                          | REST/WebSocket client; reconnect logic.     |
| Data processing    | Clean, align, resample, validate                           | pandas / custom stream buffers.             |
| Feature engine     | Compute causal features                                    | Pure functions or feature pipeline.         |
| Signal engine      | Convert features to normalized trading belief              | Strategy class/function.                    |
| Risk manager       | Apply exposure, drawdown, concentration, kill-switch rules | Central risk state + hard limits.           |
| Position sizer     | Convert signal and risk budget into target quantity        | Volatility / fixed / confidence sizing.     |
| Execution engine   | Translate target change into orders; handle order state    | Order manager with idempotency.             |
| Exchange API       | Authentication and venue communication                     | Official SDK or well-tested wrapper.        |
| Portfolio tracker  | Positions, cash, fees, realized/unrealized P&L             | Single source of truth reconciled to venue. |
| Logging/monitoring | Structured audit trail and health metrics                  | JSON logs, dashboards, alerts.              |

## Separate strategy from execution

The strategy should ideally accept a data snapshot and portfolio context, then output a target signal/position without knowing whether the system is in backtest, paper, or live mode. Execution decides how to reach the target. This separation allows you to test strategy logic independently, change order types without rewriting research code, and compare simulated vs actual fills.

> **BOT DESIGN IMPLICATION: Single source of truth**  
> The portfolio tracker must be authoritative. After API reconnects or failures, reconcile local state with exchange state before sending new orders. Never assume that an order failed merely because a request timed out.

# PART 26 — LIVE TRADING VS BACKTESTING

Live trading differs because the world can refuse to cooperate. Data can arrive late, the API can reject orders, a partial fill can leave unexpected exposure, and network failures can make the bot unsure whether an order exists. Defensive engineering is part of trading performance.

**Live failure modes and defenses**

| **Failure mode**  | **What can happen**                 | **Defensive design**                                                                |
|-------------------|-------------------------------------|-------------------------------------------------------------------------------------|
| Latency           | Signal acts on stale market state   | Timestamp data; measure end-to-end delay; avoid overly latency-sensitive ideas.     |
| Slippage          | Fill worse than backtest assumption | Track realized slippage and stress it in research.                                  |
| API failure       | Request times out or errors         | Retries with idempotent client order IDs; reconcile status before retrying blindly. |
| Missing candle    | Feature state becomes wrong         | Gap detection; pause trading until state is repaired.                               |
| Partial fill      | Position differs from target        | Track filled quantity and remaining order separately.                               |
| Order rejection   | Position remains unchanged          | Log reason; recompute target; do not assume fill.                                   |
| Exchange downtime | Cannot trade or query state         | Circuit breaker; reduce dependence on immediate exits.                              |
| Clock drift       | Events misordered                   | Synchronize system clock; rely on exchange timestamps when possible.                |
| Rate limits       | Requests throttled                  | Central rate limiter; batch/poll intelligently.                                     |
| Network failure   | State uncertain                     | Safe reconnect and state reconciliation workflow.                                   |

> **KEY TAKEAWAY: Fail safe, not fast**  
> When state is uncertain, the default should usually be to stop sending new risk until you know the current position and open orders. Duplicate orders are more dangerous than a missed trade.

# PART 27 — PAPER TRADING / DRY RUN

Paper trading runs the strategy on live data without risking capital. It catches a different class of errors than historical backtests: timing mismatches, API behavior, incomplete data, unrealistic fill assumptions, and differences between research and production code.

- Shadow mode: compute signals and intended orders but send nothing.

- Paper positions: maintain simulated positions from live prices.

- Simulated fills: apply a documented bid/ask/slippage rule.

- Logging: store every input, signal, target, intended order, and simulated fill.

- Expected vs actual comparison: if the platform supports a sandbox, compare your modeled execution with sandbox fills.

- Discrepancy monitoring: alert when live features differ from backtest-calculated values for the same data.

> **CHECKPOINT: Promotion rule**  
> Do not promote a strategy to competition/live mode merely because the backtest is strong. Require at least a short paper/shadow run that demonstrates the entire data → signal → order → accounting loop behaves as expected.

# PART 28 — LOGGING AND DEBUGGING

A trading bot without detailed logs is nearly impossible to debug. You need to reconstruct what the bot believed, what it tried to do, what the venue reported, and how the portfolio changed.

**Trade/event log fields**

| **Field**                  | **Why record it**                                                |
|----------------------------|------------------------------------------------------------------|
| Timestamp                  | Order events and features must be reconstructable in time.       |
| Asset                      | Necessary in multi-asset systems.                                |
| Signal                     | Shows the strategy’s belief at decision time.                    |
| Position before / after    | Detects accounting and duplicate-order bugs.                     |
| Target position            | Separates strategy intent from execution outcome.                |
| Intended order             | Side, type, quantity, limit/trigger price.                       |
| Actual order / ID          | Venue acknowledgement and traceability.                          |
| Execution price / quantity | Required for realized P&L and slippage.                          |
| Fees / funding             | Required for net P&L.                                            |
| Slippage                   | Measures execution quality vs benchmark.                         |
| P&L                        | Realized/unrealized impact.                                      |
| Reason for trade           | Signal trigger, rebalance, risk override, stop, recovery action. |

## Debugging workflow

> **1.** Reproduce the issue from logged input data.
>
> **2.** Confirm the feature values and signal are correct.
>
> **3.** Confirm the target position from signal + sizing + risk.
>
> **4.** Confirm the delta order from current to target position.
>
> **5.** Confirm the exchange response and fill accounting.
>
> **6.** Recompute P&L independently from fills.
>
> **7.** Add a regression test before changing the code.

> **BOT DESIGN IMPLICATION: Structured logs**  
> Prefer machine-readable logs (for example JSON lines) with stable field names. Console strings are useful for humans but difficult to aggregate when you need to ask, 'How many rejected orders occurred in the last six hours?'

# PART 29 — SOFTWARE ENGINEERING PRACTICES

Hackathons reward speed, but the fastest teams avoid preventable chaos. A small amount of structure makes strategy changes safer and lets two people work in parallel without overwriting each other.

## Recommended practices

- Git: commit small coherent changes with meaningful messages; use branches for risky experiments.

- Configuration files: keep parameters out of source code where possible.

- Environment variables / secret manager: never commit API keys.

- Modular code: data, features, strategy, risk, execution, accounting, and reporting should have explicit interfaces.

- Unit tests: test returns, position transitions, fee calculations, and edge cases.

- Reproducibility: save parameters, data version, code commit, and random seed with every result.

- Deterministic backtests: same inputs should produce the same outputs unless randomness is deliberate and seeded.

- Error handling: distinguish recoverable API failures from fatal state inconsistencies.

## Suggested Python project structure

**Recommended folder structure**

```text
trading_bot/
├── README.md
├── pyproject.toml / requirements.txt
├── .env.example # never real secrets
├── config/
│ ├── research.yaml
│ └── live.yaml
├── data/ # preferably git-ignored
├── src/
│ ├── data_feed.py
│ ├── features.py
│ ├── strategies/
│ ├── risk.py
│ ├── sizing.py
│ ├── execution.py
│ ├── portfolio.py
│ ├── backtest.py
│ └── monitoring.py
├── tests/
├── notebooks/ # exploration only; final logic moves to src/
├── experiments/
└── logs/ # git-ignored
```

Keep notebooks for exploration, but move finalized strategy logic into tested modules.

> **KEY TAKEAWAY: API-key security**  
> Use least-privilege keys. If the venue allows it, disable withdrawals for trading keys. Keep secrets out of Git, screenshots, logs, and shared notebooks.

# PART 30 — EXPERIMENTATION WORKFLOW

*A repeatable research process*

> **1.** Form a hypothesis in plain language.
>
> **2.** Obtain and validate clean data.
>
> **3.** Create features causally.
>
> **4.** Define the signal precisely.
>
> **5.** Define position sizing and risk constraints.
>
> **6.** Define execution assumptions before viewing results.
>
> **7.** Backtest with full accounting.
>
> **8.** Include fees, spread, slippage, and funding as relevant.
>
> **9.** Evaluate return, drawdown, turnover, exposure, and trade statistics.
>
> **10.** Validate out-of-sample.
>
> **11.** Run parameter, period, asset, and cost robustness tests.
>
> **12.** Paper/shadow trade.
>
> **13.** Deploy with defensive controls.
>
> **14.** Monitor performance, execution, and system health.

**Figure 8 — Strategy development lifecycle**

```text
Hypothesis
|
v
Clean Data -> Features -> Precise Rules -> Backtest
|
v
Costs + Risk Metrics
|
v
Validation / Test
|
v
Robustness Stress
|
v
Paper Trading
|
v
Deploy
|
v
Monitor
|
if evidence changes, revisit
```

Do not loop endlessly on the same test set until the equity curve looks good.

> **COMMON MISTAKE: The forbidden loop**  
> Backtest → dislike result → tweak parameters → inspect same data → repeat until attractive. This is a machine for manufacturing overfit strategies. Use hypothesis-driven changes and keep a record of every attempt.

# PART 31 — HOW TO CONDUCT A GOOD TRADING EXPERIMENT

A good trading experiment is pre-specified enough that another teammate can understand what was tested and why. The experiment log is your memory and your defense against unconscious cherry-picking.

**Experiment log template**

| **Field**     | **Example**                                                    |
|---------------|----------------------------------------------------------------|
| Experiment ID | 2026-10-04_MOM_014                                             |
| Hypothesis    | 24h momentum persists for the next 4h when volume is elevated. |
| Strategy      | Cross-sectional momentum with relative-volume filter.          |
| Parameters    | lookback=24h, hold=4h, top/bottom=20%, vol cap=...             |
| Data period   | 2025-01-01 to 2026-08-31                                       |
| Assets        | Defined point-in-time liquid universe                          |
| Costs         | 0.05% fee/side + 0.02% slippage/side                           |
| Results       | Net return, Sharpe, turnover, long/short split                 |
| Drawdown      | Max DD and duration                                            |
| Robustness    | Neighbor parameters, 1.5× costs, subperiods                    |
| Observations  | Where/why it worked or failed                                  |
| Decision      | Reject / revise hypothesis / validation candidate              |

The experiment log also lets you estimate how many ideas you have tried. If the team tested 100 related strategies, the best result should be viewed with more skepticism than if the hypothesis and parameter range were specified once and performed well.

> **KEY TAKEAWAY: Record failures**  
> Rejected experiments are valuable. They prevent the next teammate from repeating the same search and help reveal patterns such as 'mean-reversion ideas keep failing once fees are included.'

# PART 32 — 20 THINGS THAT WILL PROBABLY GO WRONG IN YOUR FIRST TRADING BOT

**1. Look-ahead bias:** Signal uses data that was not available yet.

**2. Overfitting:** Parameters explain history rather than future behavior.

**3. Ignoring fees:** Gross P&L mistaken for net P&L.

**4. Ignoring slippage:** Fills assumed at impossible reference prices.

**5. Trading too frequently:** Tiny edge is consumed by turnover costs.

**6. Excessive parameters:** The strategy becomes a historical patchwork.

**7. Too many indicators:** Correlated features create complexity without new information.

**8. Optimizing on the test set:** Out-of-sample data quietly becomes training data.

**9. Correlation ≠ causation:** A relationship disappears when the common driver changes.

**10. Ignoring short-side behavior:** Long profitability hides unstable shorts.

**11. No risk limits:** One bug or regime shift can dominate the whole result.

**12. Hardcoded parameters:** Research/live configurations drift and changes are hard to audit.

**13. Poor timestamp handling:** Candles, features, and fills are misaligned.

**14. API failures:** Timeout interpreted incorrectly; order state becomes uncertain.

**15. Missing data:** Indicators silently compute from incomplete history.

**16. Incorrect position accounting:** Cash, quantity, or P&L is double-counted.

**17. Duplicate orders:** Retries create extra exposure.

**18. Partial fills:** Bot assumes target was reached when only part filled.

**19. No monitoring:** System fails silently.

**20. Assuming the backtest is reality:** Historical simulation is treated as proof rather than evidence.

> **CHECKPOINT: Use this as a code-review checklist**  
> Before every competition run, two teammates should independently walk through these 20 failure modes and point to the code/log that addresses each one. Anything unverified becomes a known risk, not an invisible assumption.

# PART 33 — HACKATHON STRATEGY

*How a small team should divide the work*

In a small team, roles overlap, but ownership should still be clear. The fastest way to lose time is for everyone to tweak strategy notebooks while nobody owns execution or accounting.

**Suggested team responsibilities**

| **Role**           | **Primary responsibility**                                      | **Deliverables**                                                 |
|--------------------|-----------------------------------------------------------------|------------------------------------------------------------------|
| Quant / Research   | Hypotheses, features, strategy rules, statistical analysis      | Research notebook/script, parameter rationale, robustness notes. |
| Backtesting        | Historical engine, accounting, metrics, bias checks             | Trusted backtester, report generator, cost scenarios.            |
| Engineering        | Exchange API, execution, infrastructure, state recovery         | Live/paper runner, order manager, monitoring.                    |
| Risk / Integration | Position limits, exposure, drawdown controls, final integration | Risk config, pre-run checklist, reconciliation tests.            |

For a two-person team, one person can own research/backtesting and the other engineering/integration, while both review risk and final strategy choices. Each person should be able to run the complete system and understand the other side’s assumptions.

## First 24 hours

> **1.** Confirm competition instrument rules, data access, fees, order types, and API constraints.
>
> **2.** Build the simplest end-to-end data → signal → position → backtest pipeline.
>
> **3.** Implement fees/slippage from the beginning.
>
> **4.** Create 2–4 baseline strategies: simple trend, mean reversion, breakout, and/or momentum.
>
> **5.** Create a standard report with return, Sharpe/Sortino, max DD, turnover, trade count, exposure, and long/short split.
>
> **6.** Select only candidates that remain sensible after costs and obvious bias checks.
>
> **7.** Get paper/shadow plumbing working before sophisticated research.

## Next 24 hours

> **1.** Run chronological validation and robustness tests on finalists.
>
> **2.** Combine strategies only if they add genuinely different behavior.
>
> **3.** Lock a primary and fallback configuration.
>
> **4.** Stress fees, slippage, missing data, API failures, and position reconciliation.
>
> **5.** Paper trade the exact production build.
>
> **6.** Add alerts/log dashboards and a safe stop mechanism.
>
> **7.** Freeze major strategy changes near deployment; prioritize reliability and bug fixing.

> **KEY TAKEAWAY: Hackathon optimization target**  
> The goal is not the most complicated bot. It is the highest-quality decision process you can confidently deploy: clean data, understandable edge, realistic backtest, controlled risk, and reliable execution.

# PART 34 — FINAL CHEAT SHEET

## Essential formulas

**Core formula cheat sheet**

| **Concept**       | **Formula**                                                                              | **Plain-English meaning**                               |
|-------------------|------------------------------------------------------------------------------------------|---------------------------------------------------------|
| Simple return     | $r_{t} = \frac{P_{t}}{P_{t - 1}} - 1$                                                    | Percentage price change.                                |
| Log return        | $g_{t} = \ln\left( \frac{P_{t}}{P_{t - 1}} \right)$                                      | Additive return representation across time.             |
| Long P&L          | $q\left( P_{exit} - P_{entry} \right)$                                                   | Long profits when exit price is higher.                 |
| Short P&L         | $q\left( P_{entry} - P_{exit} \right)$                                                   | Short profits when exit price is lower.                 |
| Expected value    | $p_{W}\overline{W} - p_{L}\overline{L}$                                                  | Average trade outcome implied by win/loss distribution. |
| Variance          | $\mathbb{E}\left[ \left( X - \mathbb{E}[ X] \right)^{2} \right]$ | Dispersion around the mean.                             |
| Volatility        | $\sigma_{r} = std(r)$                                                                    | Magnitude of return variation.                          |
| Sharpe            | $\frac{mean\left( r - r_{f} \right)}{\sigma_{r}}$                                        | Return per unit of total volatility.                    |
| Sortino           | $\frac{mean\left( r - r_{f} \right)}{\sigma_{down}}$                                     | Return per unit of downside variation.                  |
| Max drawdown      | $\max_{t}\left( \frac{{Peak}_{t} - {Equity}_{t}}{{Peak}_{t}} \right)$                    | Worst historical loss from a previous equity high.      |
| Profit factor     | $\frac{\text{Gross Profit}}{\left\lvert \text{Gross Loss} \right\rvert}$                           | Aggregate gains relative to aggregate losses.           |
| Win rate          | $\frac{\text{Wins}}{\text{Trades}}$                                                      | Frequency of profitable closed trades.                  |
| Expectancy        | $\frac{\text{Net PnL}}{\text{Trades}}$                                                   | Economic value of the trade distribution.               |
| Vol-scaled sizing | $w \propto \frac{s}{\widehat{\sigma}}$                                                   | Reduce size as estimated risk rises.                    |

## Essential Python libraries

- pandas: tabular/time-series manipulation and resampling.

- numpy: vectorized numerical operations.

- matplotlib: diagnostic plots and equity/drawdown visualization.

- scipy: statistical/numerical utilities when needed.

- scikit-learn: conventional ML pipelines and metrics.

- statsmodels: statistical models and time-series diagnostics. Use only what the strategy actually needs.

## Ten questions before trusting a strategy

> **1.** Is there any look-ahead bias or timestamp leakage?
>
> **2.** Are fees included on every realistic fill?
>
> **3.** Is slippage/spread modeled?
>
> **4.** Is test data genuinely untouched?
>
> **5.** Is performance stable across subperiods and regimes?
>
> **6.** Does it depend on one exact parameter?
>
> **7.** Does it survive higher transaction costs?
>
> **8.** How large and how long is the drawdown?
>
> **9.** How many trades/independent opportunities generate the result?
>
> **10.** Can we explain why the strategy should have an edge and when it should fail?

# WHAT WE ACTUALLY NEED TO KNOW FOR THE HACKATHON

## MUST KNOW

- Exact competition instrument, long/short, leverage, order, fee, and API rules.

- How data timestamps map to what the bot could know and when it can trade.

- Returns, positions, P&L, fees, spread, slippage, equity, and drawdown.

- Difference between signal and position sizing.

- How to build a causal backtest with realistic timing and costs.

- Look-ahead bias, leakage, overfitting, and why test data must remain unseen.

- Core risk limits: position, exposure, drawdown, and operational kill switch.

- How the live bot handles API errors, missing data, partial fills, and state reconciliation.

- How to read the logs and stop the bot safely.

## SHOULD KNOW

- Momentum, trend, mean reversion, and breakout mechanics.

- Volatility-based sizing and portfolio correlation.

- Sharpe, Sortino, Calmar, profit factor, expectancy, turnover, and exposure.

- Walk-forward testing and parameter robustness.

- Basic order-book mechanics and why execution assumptions matter.

- How to combine complementary strategies without accidentally duplicating the same risk.

- Basic ML workflow and time-series validation if ML is used.

## NICE TO KNOW

- Advanced microstructure and queue-position modeling.

- Sophisticated portfolio optimization.

- Complex neural sequence models.

- Reinforcement learning.

- Advanced derivatives pricing and Greeks, which are not necessary for this competition scope.

> **KEY TAKEAWAY: Final team principle**  
> Simplicity is often more robust than unnecessary complexity. A simple strategy with clean timing, realistic costs, disciplined sizing, and reliable execution is a stronger competition system than a spectacular but untrustworthy backtest.

# ONE-PAGE PRE-HACKATHON CHECKLIST

**☐** Competition rules confirmed: instruments, long/short, leverage, fees, funding, order types, limits.

**☐** API credentials stored securely; permissions minimized; no secrets committed to Git.

**☐** System clock synchronized; exchange timestamps understood.

**☐** Historical data validated for missing/duplicate/outlier timestamps.

**☐** Signal timing diagram reviewed for look-ahead bias.

**☐** Backtest uses the same position convention as live trading.

**☐** Fees, spread, slippage, and funding assumptions are configurable and included.

**☐** Train/validation/test or walk-forward split is chronological.

**☐** Final test data was not used for iterative tuning.

**☐** Parameter sensitivity and subperiod/regime tests completed.

**☐** Higher-cost stress test completed.

**☐** Long and short performance reviewed separately.

**☐** Max position, portfolio exposure, and drawdown limits configured.

**☐** Order IDs are idempotent; duplicate orders cannot be created by naive retries.

**☐** Partial fills and rejected orders are handled.

**☐** On reconnect, positions and open orders are reconciled before new trading.

**☐** Paper/shadow run completed on the production build.

**☐** Every decision/order/fill is logged with timestamps and reasons.

**☐** Health monitoring and a safe kill switch are available.

**☐** Primary strategy config is frozen; fallback config exists; both are versioned.

**☐** At least two team members can start, inspect, and safely stop the bot.

**☐** We can explain the strategy’s edge, failure regime, and key risks in two minutes.

> **CHECKPOINT: Go / no-go question**  
> If the bot makes an unexpected trade in the first five minutes, can the team determine from logs exactly which data, feature, signal, sizing rule, risk decision, and order event caused it? If not, the system is not yet ready.

**A strategy is a hypothesis. A backtest is an experiment. Deployment is another experiment under stricter consequences.**

*Build for evidence, robustness, and survival—not for the prettiest historical curve.*

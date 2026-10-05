# Questions for the organizers

Running list, to be sent together. Each entry says what we assume until it is answered and where that assumption lives in the code.

## Scoring (score = 0.4 Sortino + 0.3 Sharpe + 0.3 Calmar, Sharpe sampled daily)

1. **Day boundary.** At what time and timezone does a scoring day end (UTC midnight, HKT midnight, or something else)? Assumed: UTC midnight. `ScoreConventions.day_offset_hours` in `scoring.py`.
2. **Sortino and Calmar sampling.** Are Sortino and Calmar also computed from daily returns, like Sharpe? Assumed: yes.
3. **Risk-free rate.** Is a risk-free rate subtracted in Sharpe/Sortino? Assumed: 0. `ScoreConventions.risk_free_annual`.
4. **Sortino target.** What target return defines downside deviation, and is the deviation averaged over all days or only losing days? Assumed: target 0, averaged over all days. `ScoreConventions.sortino_target_daily`.
5. **Annualization.** Are the ratios annualized, and with 365 or 252 periods per year? Assumed: 365. `ScoreConventions.periods_per_year`.
6. **Calmar return.** Is the Calmar numerator compounded annualized return, simple annualized mean return, or raw period return? Assumed: compounded. `ScoreConventions.annual_return`.
7. **Drawdown basis.** Is max drawdown measured on daily closing equity or on intraday marks? Assumed: daily. `ScoreConventions.drawdown_basis`.
8. **Undefined ratios.** How is a ratio scored when its denominator is zero (no losing days, no drawdown, or no trades)? Assumed: reported as undefined.
9. **Starting point.** Is day 1's return measured against the $100k starting capital, and do days before our first trade count as zero-return days? Assumed: yes to both.

## Activity rule (8 active trading days out of 14)

10. What counts as an "active trading day" and as "enough trades" per day: a minimum number of filled orders, a minimum notional, or something else?
11. Which timezone defines a trading day for this rule?

## Trading

12. Are short positions enabled on our competition API keys? Assumed: no until confirmed. `ALLOW_SHORTS` in `config.py`.
13. What are the actual spot taker and maker fees on the competition account? The API documentation example shows `CommissionPercent` of 0.00012, while `config.py` assumes 0.001 taker. Assumed: 0.001 until confirmed.
14. Does a MARKET order always come back `FILLED` in the same response, or can it be `PENDING` or partially filled?

## Finals

15. How are the top 15 finalists chosen: purely by composite score, or does judging of the repo and README also count?

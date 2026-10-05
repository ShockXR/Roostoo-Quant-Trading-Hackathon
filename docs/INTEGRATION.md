# Samyak branch integration

Integrated `origin/Samyak` through `87ad83f` into `main`, preserving its six
commits and the local repairs/fixtures. The Masterdoc remains present and
unchanged. The teammate's downloader README is retained in
`docs/BINANCE_DOWNLOADER.md`; the repository README explains the combined bot.

## Combined behavior

- The downloader, research-window sweep and historical findings are retained.
- Purging, scaled logistic regression, fixed input schemas and training-only
  conditional returns use the repaired implementation. Class 0 keeps its
  signed mean return. Probability calibration has its own chronological gap.
- Raw prices and unnormalized moving averages are excluded from both training
  and inference through one `model_inputs` helper.
- Training folds with inadequate class coverage for fitting or calibration
  stay flat with an explicit skipped status. Unknown models, invalid values,
  missing label provenance and leakage still raise errors.
- The teammate's experimental `SIZING_REFERENCE_VOL=0.0006` replaces the old
  0.01 constant in sizing. It was selected for 1m research and requires separate
  validation when sampling changes. Other live settings are not optimized
  from the historical report.
- Research candles expose closes only after the complete bar, use integer
  UTC milliseconds and reject interior gaps. The adapter uses installed
  `roostoo_bot` modules instead of registering a second package alias.
- Sweeps select baseline `9_full_sized`, report actual fill counts, reject
  fractional bar horizons and signal incomplete runs with a nonzero exit.

## Earlier research differences

The teammate's old backtest assumed bar-price fills, used a rolling maximum
to extend ML long positions for the forecast horizon, and offered optimistic
maker fees without a resting-order fill model. These results remain in
`docs/BACKTEST_RESULTS.md` as the teammate's historical report.

The combined engine keeps the shared live/paper close-first target policy,
one-observation execution delay, actual cash/units accounting and terminal
liquidation. The rolling hold experiment is not enabled; a holding-policy
experiment must be implemented consistently in both execution paths and
evaluated separately. Maker mode fails explicitly until fills can be modeled.
Reported winning bars are not presented as winning closed trades.

## Validation and limits

The full offline suite and refreshed audit cover the repaired bot and new
integration fixtures. New fixtures exercise downloader timestamp parsing,
completed-candle timing, coarse bars, gaps/duplicates, shared model columns,
single-class folds, the research CLI and sweep output. Compilation and a
wheel build validate syntax and packaging.

No Binance corpus was downloaded during integration; the earlier market
results have not been reproduced. Synthetic tests establish contracts, not
profitability or actual venue behavior. `DRY_RUN=True`,
`LIVE_TRADING_ENABLED=False` and `ALLOW_SHORTS=False` remain in force.

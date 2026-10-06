# Offline validation notes for the earlier ML and majors pipeline

> Moved from the repository README on 2026-10-05. It describes the model-based pipeline (`features/`, `model/`, `backtest/engine.py`, `execution/`, `--strategy model`) and its regression fixes. The mid-cap strategy that the bot is built around is described in the top-level [README](../README.md).

This checkout now contains regression fixes and deterministic fixtures for the original draft. `DRY_RUN=True`, `LIVE_TRADING_ENABLED=False`, and `ALLOW_SHORTS=False` remain the defaults. No historical profitability, organizer score parity, or actual exchange-fill behavior has been established.

## Install and verify

Python 3.10 or newer is required. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -t . -v
python analysis/audit_bot.py
```

`requirements.txt` installs the package in editable mode. The documented `python -m roostoo_bot.main` service command therefore resolves correctly. The fixture suite uses temporary storage and blocks network connections. It never sends real orders.

`analysis/audit_results_baseline.json` preserves the original 33-check audit at commit `3aa87f5`. `analysis/audit_results.json` records the current fixture results, file hashes, dependency versions and updated handbook coverage. Passing fixtures verifies these contracts; it does not measure strategy profitability.

## What changed

- Unknown classification outcomes stay missing. Training, validation and internal probability calibration have horizon-sized gaps, with actual label-availability timestamps checked at the boundaries.
- Training-only conditional returns translate class probabilities into signed expected returns. Class 0 retains its signed average return instead of being assumed to mean a loss. Return regression uses a regressor.
- Models carry their feature order, pair, training information cutoff, source/config fingerprint, runtime versions and a checksum manifest. Inference rejects missing/nonfinite features instead of dropping columns. Raw optional ticker fields and unnormalized moving-average levels are excluded from ML.
- The sequential backtest delays execution by one observation, uses the paper cash/units ledger and charges terminal liquidation costs. Starting equity is part of the drawdown peak. Sortino uses zero-target RMS downside over all observations; ratios are research conventions pending organizer verification. Positive bars are not mislabeled as winning closed trades.
- Ablations use identical out-of-sample timestamps and one classifier family. Regime features, expected-return filtering and sizing are added separately. Insufficient or noncontiguous evaluation data raises an error rather than reporting a misleading zero-trade result.
- The runner plans changes from reconciled holdings, converts capital fractions to rounded quantities, checks notional minimums, reserves cash for fees, caps gross exposure and prioritizes reductions. Direction flips close first, then consider a new position on a later quote.
- Drawdown halts, hourly order reservations and duplicate-quote suppression persist with paper cash/positions across restarts. API requests have a shared budget. Order POSTs are not retried after ambiguous failures. Pending/partial/uncertain fills stop further orders; the uncertainty marker is saved before submission.
- Shorts have separate collateral, entry and close accounting using the official `/v6` interfaces. They are disabled by default until this account's competition permission is verified. A spot SELL only reduces an existing long.
- Exact duplicate ticks are removed, conflicting duplicates rejected and parquet writes replaced atomically. Old cross-sectional quotes expire; rankings stay within the configured asset group. The runner rejects stale/future quotes and recent history gaps.
- `scripts/test_min_order.py` is now read-only. It cannot accidentally place a one-BTC market order.

## Train artifacts from historical data

Store cleaned ticker histories in `data_store/`. Required columns are UTC epoch-millisecond `timestamp`, `last_price`, `max_bid`, and `min_ask`. The current features and prediction horizon count observations, not guaranteed wall-clock minutes or OHLCV candles; clean and align the research sampling cadence before interpreting annualized metrics.

Choose and record a training cutoff strictly before the validation/final-test periods. Replace `TRAIN_END_MS` below with that integer timestamp:

```sh
python -m roostoo_bot.model.train --pair BTC/USD --train-end-ms TRAIN_END_MS --model logistic --output models/BTC_USD.joblib
```

The CLI reads only history at/before the cutoff and discards labels that would need later data. It saves a model and checksum manifest; it reports no out-of-sample score. Use only locally generated/trusted joblib artifacts, which use pickle serialization. Train a separate artifact for each desired asset; an asset without a model cannot open new risk. Changing features, their source, the universe or label settings requires retraining. Artifact loading checks Python/library versions before deserialization; train with the deployment runtime or install matching dependencies.

## Binance research tools

The `Samyak` branch through `87ad83f` is incorporated. The [downloader guide](BINANCE_DOWNLOADER.md) describes `binance_downloader.py`. Downloads are optional and access the public Binance archive; regression checks use local synthetic data instead.

```sh
python binance_downloader.py --start 2026-07-01 --end 2026-08-31 --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT
python scripts/backtest_sol.py --symbol SOLUSDT --bar 5m --horizon 12 --start 2026-07-01 --end 2026-09-01
python scripts/sweep.py --symbols SOLUSDT ETHUSDT BTCUSDT --jobs 4
```

The backtest loader labels candles by their closing availability time in UTC epoch milliseconds, drops incomplete boundary bars and rejects internal missing candles. Coarse bars update the research cadence and annualization settings. Train/validation sizes remain bar counts; short windows fail explicitly. Single-class or insufficient calibration folds are marked as skipped, remain flat and are counted in `n_skipped_folds`.

The sweep uses the current nine-stage comparison and actual fill counts. Failed cells make the sweep exit unsuccessfully, even if partial results are saved. `--maker` is rejected until a resting-order fill model exists. The inherited 0.0006 sizing reference is an experimental 1m setting, not a demonstrated parameter for other cadences.

[Earlier backtest findings](BACKTEST_RESULTS.md) are preserved as historical research, not reproduced results for this combined engine. See [integration decisions](INTEGRATION.md) for the differences, including the earlier horizon-hold experiment.

## Paper run

After historical model artifacts exist:

```sh
python -m roostoo_bot.main --model-dir models
```

This polls the public ticker and ExchangeInfo, simulates bid/ask plus configured slippage/fees, and persists `data_store/paper_state.json` and `logs/events.jsonl`. It does not place exchange orders. Without any models, startup fails before accessing the API. Rule-only paper research requires explicitly disabling the estimated-return cost filter because no trained rule-payoff model exists.

Keep paper and live state files separate. An uncertain-order marker is deliberately not auto-cleared: verify account/order state before resuming. Dust below venue minimums can remain, and an exhausted hourly order quota can defer liquidation. Drawdown halts prohibit new risk while exit orders wait for available quota/fresh quotes.

## Remaining gates before live implementation

1. Freeze a representative historical corpus, periods, parameters and final holdout. Run paired momentum/mean-reversion/cross-sectional baselines and parameter, asset, period, fee and latency stress tests.
2. Verify current competition permissions, rate limits, spot fees, short fees and scoring conventions. The code uses research defaults. Official API reference: https://github.com/roostoo/Roostoo-API-Documents .
3. Run a sustained paper/shadow comparison and reconcile signals, quantities, fills, fees and P&L against the actual simulator. Fixtures cannot establish real venue parity.
4. Review any intended live enablement separately. Both live guards are still disabled. The live adapter reads wallet/pending/short state and stops on an unresolved response; its behavior has been exercised only with mocked API responses.

The handbook recommends retaining a few validated strategy families. Missing breakout/volume strategies, portfolio concentration/daily loss policies, experiment statistics and a held-out market performance record remain research work; this repair does not turn those gaps into demonstrated coverage.

# Backtest results — Binance 1m history, Jul–Sep 2026

Data: Binance spot 1m klines (`binance_downloader.py`), SOL/ETH/BTC plus
BNB/XRP as cross-sectional context. Research window Jul 1 – Aug 31;
September held out and evaluated once. Fees: 0.10% taker + 0.03% slippage
per side unless noted. Positions are long-only, max 20% of capital.

## Bugs fixed before measuring

| Fix | Effect |
|---|---|
| Purge `TARGET_HORIZON` bars between walk-forward train and val | Last train labels were computed from val-block prices (leakage) |
| Cost filter uses `p·avg_win − (1−p)·avg_loss` from the train window | `p − 0.5` passed almost every p > 0.503 |
| Hold ML entries for `TARGET_HORIZON` bars | Bot paid a round trip to hold 1 bar of a 10-bar forecast |
| Raw price levels excluded from model inputs | Non-stationary across folds |
| Scaled logistic regression, `SIZING_REFERENCE_VOL` tuned for 1m | Never converged / vol shrink was a no-op |
| Skip folds whose train window has one class | Crashed on quiet BTC stretches |

SOL Jul–Sep, 1m bars, 10-bar horizon: full bot went from −6.65% to −0.43%
(max drawdown −6.65% → −0.43%).

## Sweep (Jul–Aug): bar size × horizon × symbol

`scripts/sweep.py`, full bot (baseline 8), mean across SOL/ETH/BTC:

| Bar | Horizon | Profitable symbols (taker) | Mean return (taker) | Mean return (maker) |
|---|---|---|---|---|
| 1m | 4h | **3/3** | **+0.74%** | +1.35% |
| 15m | 4h | 2/3 | +0.05% | +0.06% |
| 5m | 4h | 1/3 | +0.12% | +0.26% |
| 1m | 1h | 0/3 | −0.57% | −0.04% |
| 1m | 30m | 0/3 | −0.64% | −0.43% |

Horizons ≤ 1h lose after fees almost everywhere. 1h bars never trade: the
2000-bar train window exceeds the data. Maker results assume every limit
order fills, so treat them as an upper bound.

## September holdout: 1m bars, 4h horizon, taker

| Symbol | Bot | Buy & hold (20%) |
|---|---|---|
| SOL | +0.41% | +2.94% |
| ETH | −0.41% | +1.80% |
| BTC | −0.65% | +1.27% |

**The edge does not generalize.** 1/3 symbols profitable out of sample; the
research-window result was largely selection from 33 configs. The live
execution layer (model persistence, position management, order quantities)
was deliberately not built on top of this.

## Where to look next

- **Target definition.** The threshold target (10-bar return > 0.29%) is
  hit ~7.6% of the time on 1m SOL; at 4h it's a different problem. Try
  volatility-scaled thresholds or a regression target ranked across assets.
- **Cross-sectional allocation instead of per-asset timing.** The universe
  is 20+ majors; rank and rotate rather than time each coin alone.
- **Maker execution.** It's worth roughly +0.6pp over two months here, if
  limit fills on the mock exchange are reliable — measure that live first.

Reproduce:

```
.venv/bin/python scripts/sweep.py [--maker]
.venv/bin/python scripts/backtest_sol.py --symbol SOLUSDT --horizon 240 --start 2026-09-01
```

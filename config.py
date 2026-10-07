"""
Central configuration for the trading bot.
Every strategy component is a flag here so the ablation study can flip
pieces on/off without touching any other module.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env if present (local dev); no-op if absent (AWS sets real env vars directly)
except ImportError:
    pass

# --- API ---
# Secrets come from environment variables ONLY -- never hardcode real keys
# here, since this file is committed to a (possibly public) GitHub repo.
# Set these in your shell, a .env file (loaded via python-dotenv, gitignored),
# or your AWS instance's environment before running the bot.
BASE_URL = "https://mock-api.roostoo.com"
API_KEY = os.environ.get("ROOSTOO_API_KEY", "")
SECRET = os.environ.get("ROOSTOO_API_SECRET", "")

if not API_KEY or not SECRET:
    import warnings
    warnings.warn(
        "ROOSTOO_API_KEY / ROOSTOO_API_SECRET not set in environment. "
        "Signed endpoints will fail until these are configured."
    )

# --- Universe ---
# Confirmed pairs from competition, split into groups so cross-sectional
# ranking only compares assets that actually move for similar reasons
# (see conversation: tokenized stocks / meme coins behave very differently
# from large-cap crypto and would add noise if ranked together).
UNIVERSE_GROUPS = {
    "majors": [
        "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
        "DOGE/USD", "LINK/USD", "AVAX/USD", "DOT/USD", "LTC/USD", "TRX/USD",
        "TON/USD", "SUI/USD", "APT/USD", "NEAR/USD", "ICP/USD", "HBAR/USD",
        "FIL/USD", "XLM/USD",
    ],
    "defi_midcap": [
        "UNI/USD", "AAVE/USD", "CRV/USD", "PENDLE/USD", "CAKE/USD",
        "ONDO/USD", "ENA/USD", "ARB/USD", "POL/USD", "SEI/USD", "CFX/USD",
    ],
    "tokenized_equities": [
        "NVDAB/USD", "TSLAB/USD", "GOOGLB/USD", "MSFTB/USD", "AMDB/USD",
        "QCOMB/USD", "INTCB/USD", "SNDKB/USD", "CRCLB/USD", "MSTRB/USD",
        "SPCXB/USD", "LITEB/USD", "COINB/USD", "SKHYB/USD", "CBRSB/USD",
    ],
    "meme_high_vol": [
        "PEPE/USD", "SHIB/USD", "BONK/USD", "WIF/USD", "FLOKI/USD",
        "1000CHEEMS/USD", "TRUMP/USD", "PENGU/USD",
    ],
    # not yet verified for historical data availability -- check before trading
    "unverified": [
        "EDEN/USD", "MIRA/USD", "STO/USD", "OMNI/USD", "OPEN/USD", "HEMI/USD",
        "TUT/USD", "BMT/USD", "BIO/USD", "GLWB/USD", "WDCB/USD", "AVNT/USD",
        "XPL/USD", "LISTA/USD", "VIRTUAL/USD", "SOMI/USD", "FORM/USD",
        "LINEA/USD", "EIGEN/USD", "PLUME/USD", "NBISB/USD", "METAB/USD",
        "WLFI/USD", "ASTER/USD", "ZEN/USD", "MUB/USD", "WLD/USD", "ZEC/USD",
    ],
}

# Active trading universe -- start with majors only (best data, best
# liquidity, cleanest cross-sectional signal). Expand later by adding
# another group's list, e.g. UNIVERSE = UNIVERSE_GROUPS["majors"] + UNIVERSE_GROUPS["defi_midcap"]
UNIVERSE = UNIVERSE_GROUPS["majors"]
MARKET_PROXY = "BTC/USD"  # used for "return relative to market" features

# --- Data collection ---
# Competition explicitly forbids HFT / market-making / arbitrage and warns
# that excessive requests fail. Keep this slow and deliberate -- there is
# no benefit to polling faster than your actual decision horizon anyway.
POLL_INTERVAL_SECONDS = 60      # how often we hit /v3/ticker (1 min)
STORE_DIR = "data_store"        # local parquet files, one per pair

# --- Competition rules (confirmed) ---
INITIAL_CAPITAL = 100_000.0
COMPETITION_DURATION_DAYS = 14
NO_LEVERAGE = True          # spot only, 1x long/short -- our short sizing already honors this
NO_HFT = True                 # informs POLL_INTERVAL_SECONDS and TARGET_HORIZON below

# Bars per year at the current poll interval -- used to annualize Sharpe/
# Sortino/Calmar correctly. Recompute automatically if POLL_INTERVAL_SECONDS changes.
BARS_PER_YEAR = int((365 * 24 * 60 * 60) / POLL_INTERVAL_SECONDS)

# --- Feature engineering ---
MOMENTUM_HORIZONS = [1, 5, 10, 20, 30]      # in ticks/bars
MA_WINDOWS = {"fast": 5, "slow": 20}
MEAN_REV_WINDOW = 20                          # rolling window for z-score / RSI
BOLLINGER_WINDOW = 20
BOLLINGER_STD = 2
REGIME_VOL_WINDOW = 30
REGIME_TREND_WINDOW = 30

# --- Target construction ---
TARGET_HORIZON = 10          # bars ahead we're predicting (tune with POLL_INTERVAL)
# threshold should be > round-trip cost + margin (see COST section below)
TARGET_MODE = "threshold"    # "regression" | "direction" | "threshold"

# --- Transaction costs: CONFIRMED from hackathon rules ---
TAKER_FEE = 0.001              # 0.10% market order
MAKER_FEE = 0.0005              # 0.05% limit order
ROUND_TRIP_COST_ESTIMATE = TAKER_FEE * 2   # worst case, two taker fills
SLIPPAGE_ESTIMATE = 0.0003                   # placeholder, refine from real fills -- mock exchange may have low slippage
SAFETY_MARGIN = 0.0003                        # extra edge required beyond costs
TARGET_THRESHOLD = ROUND_TRIP_COST_ESTIMATE + SLIPPAGE_ESTIMATE + SAFETY_MARGIN
# Prefer LIMIT (maker) orders where the strategy allows resting an order --
# maker fee is half the taker fee, which matters a lot at this trade frequency.

# --- Position sizing / risk ---
# Scoring is 0.4*Sortino + 0.3*Sharpe + 0.3*Calmar -- ALL THREE penalize
# drawdown/downside volatility, not just raw return. A high-return,
# high-drawdown bot scores worse than a modest-return, low-drawdown one.
# Risk controls below are tuned toward that, not toward maximizing raw PnL.
MAX_POSITION_PER_ASSET = 0.20    # fraction of capital, trimmed from 0.25 for risk-adjusted scoring
MAX_PORTFOLIO_EXPOSURE = 0.7      # fraction of capital deployed at once
MAX_TRADES_PER_HOUR = 20
MAX_DRAWDOWN_KILL_SWITCH = 0.12   # Calmar = return / max drawdown, so capping drawdown directly protects this metric
MIN_CONFIDENCE_TO_TRADE = 0.55     # model probability threshold to act at all
# Per-bar realized vol (REGIME_VOL_WINDOW-bar std of 1m returns) above which
# position size is shrunk proportionally. ~median for SOL 1m, Jul-Sep 2026
# (median 0.00057, p75 0.00079); the old 0.01 was never reached on 1m bars,
# so the vol shrink was a no-op. Re-tune if POLL_INTERVAL_SECONDS changes.
SIZING_REFERENCE_VOL = 0.0006

# --- Component switches (used by the ablation harness) ---
COMPONENTS = {
    "momentum": True,
    "mean_reversion": True,
    "cross_sectional": True,
    "regime": True,
    "ml_model": True,
    "cost_filter": True,
    "position_sizing": True,
}

# --- Walk-forward validation ---
WALKFORWARD_TRAIN_BARS = 2000
WALKFORWARD_VAL_BARS = 500
WALKFORWARD_STEP_BARS = 500

RANDOM_SEED = 42

# --- Daily dip-reversal strategy (strategy.py "reversal") ---
# Buy MAX_POSITION_PER_ASSET when the last REVERSAL_LOOKBACK_BARS return is
# below -REVERSAL_K x its expected size (1-day std of 1m returns scaled by
# sqrt(lookback)), checked at the close of each REVERSAL_CHECK_EVERY-minute
# block; hold REVERSAL_HOLD_BARS after the last qualifying signal.
# Chosen as the centre of the plateau that was positive on Jul (train) and
# Aug (validation) 2026; see TRADING_BOT_OPTIMIZATION_REPORT.md. Lookbacks
# under ~1 day were NOT robust (negative on the Sep test period).
REVERSAL_LOOKBACK_BARS = 1440   # 1 day of 1m bars
REVERSAL_K = 1.5
REVERSAL_HOLD_BARS = 1440
REVERSAL_CHECK_EVERY = 60       # minutes

# --- Production model (model-research branch) ---
# Chosen on 2022-01 .. 2026-09 Binance history; evidence in
# experiments/long/RESEARCH_LOG.md and MODEL_RESEARCH_REPORT.md.
# Model 3: once a day at the 23:00 UTC bar close, hold the 3 assets with the
# highest positive 28-day return at 20% each, only while BTC closes above its
# 100-day SMA; otherwise cash. strategies/m3_momentum_regime.py.
PRODUCTION_MODEL = "m3_momentum_regime"
PRODUCTION_PARAMS: dict = {}          # frozen defaults live in the model module
# The tested universe: the 2022-01-01 top-10 large caps that still trade
# (LUNA was delisted in May 2022). Binance symbol -> Roostoo pair.
PRODUCTION_SYMBOLS = {
    "BTCUSDT": "BTC/USD", "ETHUSDT": "ETH/USD", "BNBUSDT": "BNB/USD", "SOLUSDT": "SOL/USD",
    "ADAUSDT": "ADA/USD", "XRPUSDT": "XRP/USD", "DOTUSDT": "DOT/USD", "AVAXUSDT": "AVAX/USD",
    "DOGEUSDT": "DOGE/USD",
}
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"   # public, no key
LIVE_HISTORY_DAYS = 130            # >= 100-day SMA + 28-day lookback, with margin
LIVE_STATE_FILE = "data_store/live_state.json"
LIVE_DECISION_LOG = "data_store/live_decisions.jsonl"   # one JSON line per hour; scripts/reconcile_live.py
LIVE_RECONCILE_TOLERANCE = 0.02    # on a fresh start, fix positions > 2% of equity off target
# Drawdown stop for the live loop (fraction from peak equity; None = off).
# Off by default: on Model 3's 2022-2026 history a 12% stop fired in 7.6% of
# 14-day windows, cut the mean 14-day return from +1.48% to +0.90% and did
# not improve the worst 5% of windows (-7.3% vs -7.0%).
LIVE_KILL_SWITCH_DRAWDOWN: float | None = None

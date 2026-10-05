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

# --- Competition assumptions; verify against current organizer rules ---
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

# --- Research fee assumptions; reconcile spot fees with account/rules ---
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

# Paper is the default. Changing DRY_RUN alone cannot enable orders.
LIVE_TRADING_ENABLED = False
ALLOW_SHORTS = False  # verify that this competition permits short positions
MAX_QUOTE_AGE_MS = 120_000
MAX_HISTORY_GAP_MS = 120_000
MIN_REBALANCE_FRACTION = 0.002
SHORT_FEE = 0.001  # official /v6 short fee, both market and limit
MAX_API_CALLS_PER_MINUTE = 30
CROSS_SECTIONAL_HORIZON = 5
MEAN_REVERSION_THRESHOLD = 1.5
CROSS_SECTIONAL_LONG_PERCENTILE = 0.7
CROSS_SECTIONAL_SHORT_PERCENTILE = 0.3
MODEL_DIR = "models"
PAPER_STATE_PATH = "data_store/paper_state.json"
EVENT_LOG_PATH = "logs/events.jsonl"

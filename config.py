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

# Sleeve 1: 12 Large-Cap Liquid Coins
LARGECAP_UNIVERSE = [
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
    "DOGE/USD", "LINK/USD", "AVAX/USD", "DOT/USD", "LTC/USD", "SUI/USD"
]

# Sleeve 2: 11 Mid-Cap Coins
MIDCAP_UNIVERSE = [
    "UNI/USD", "AAVE/USD", "CRV/USD", "PENDLE/USD", "CAKE/USD",
    "ONDO/USD", "ENA/USD", "ARB/USD", "POL/USD", "SEI/USD", "CFX/USD"
]

# Sleeve 3: 10 Small-Cap Meme Coins
SMALLCAP_UNIVERSE = [
    "PEPE/USD", "SHIB/USD", "BONK/USD", "WIF/USD", "FLOKI/USD",
    "PENGU/USD", "TRUMP/USD", "1000CHEEMS/USD", "EIGEN/USD", "BIO/USD"
]

# Total Combined Active Trading Universe (33 Coins across 3 Sleeves)
UNIVERSE = LARGECAP_UNIVERSE + MIDCAP_UNIVERSE + SMALLCAP_UNIVERSE
MARKET_PROXY = "BTC/USD"  # used for "return relative to market" features

# --- Data collection ---
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
TARGET_MODE = "threshold"    # "regression" | "direction" | "threshold"

# --- Transaction costs: CONFIRMED from hackathon rules ---
TAKER_FEE = 0.001              # 0.10% market order
MAKER_FEE = 0.0005              # 0.05% limit order
ROUND_TRIP_COST_ESTIMATE = TAKER_FEE * 2   # worst case, two taker fills
SLIPPAGE_ESTIMATE = 0.0003                   # placeholder, refine from real fills -- mock exchange may have low slippage
SAFETY_MARGIN = 0.0003                        # extra edge required beyond costs
TARGET_THRESHOLD = ROUND_TRIP_COST_ESTIMATE + SLIPPAGE_ESTIMATE + SAFETY_MARGIN

# --- Portfolio Fund Allocation ($100,000 USD Total Equity) ---
LARGECAP_MAX_EXPOSURE = 0.35     # 35% ($35,000 USD max deployment for Large-Caps)
MIDCAP_MAX_EXPOSURE = 0.30       # 30% ($30,000 USD dedicated deployment for Mid-Caps)
SMALLCAP_MAX_EXPOSURE = 0.05     # 5% ($5,000 USD dedicated deployment for Small-Caps)
CASH_RESERVE_BUFFER = 0.30       # 30% ($30,000 USD preserved cash buffer reserve)
MAX_PORTFOLIO_EXPOSURE = 0.70     # 70% total portfolio exposure cap (35% + 30% + 5%)
MIN_CONFIDENCE_TO_TRADE = 0.55     # model probability threshold

# Sleeve 1 (Large-Caps) Parameters (28-day Momentum + BTC 100d SMA Gate)
MAX_POSITION_PER_ASSET = 0.15    # 15% max per large-cap coin
LARGE_LOOKBACK_HOURS = 28 * 24   # 28 days for Large-Cap Momentum
LARGE_TOP_K = 3                  # Top 3 Large-Caps
REGIME_SMA_HOURS = 100 * 24      # 100-day BTC MA regime (only trade Large-Caps when BTC > 100d SMA)
STOP_LOSS_K = 3.0                # Exit if price drops by > 3.0 * Volatility distance (Rule 1)
TAKE_PROFIT_K = 1.5              # Exit if price gains > 1.5 * Volatility distance (Rule 2)

# Sleeve 2 (Mid-Caps) Parameters (Hourly Momentum + Trailing Ratchets)
MIDCAP_MAX_POSITION_PER_ASSET = 0.08 # 8% max per mid-cap coin ($8,000 USD)
MIDCAP_STOP_LOSS_K = 3.5             # Exit if mid-cap price drops by > 3.5 * Volatility (Rule 1)
MIDCAP_TAKE_PROFIT_K = 2.0           # Exit if mid-cap price gains > 2.0 * Volatility (Rule 2)

# Sleeve 3 (Small-Caps) Parameters (7-day Confirmed Momentum + 10% Hard Stop)
SMALLCAP_MAX_POSITION_PER_ASSET = 0.02 # 2% max per small-cap coin ($2,000 USD)
SMALL_LOOKBACK_HOURS = 168             # 7 days
SMALL_CONFIRM_HOURS = 24               # 24 hours
SMALL_TOP_K = 2                        # Top 2 meme coins
SMALL_STOP_LOSS = 0.10                 # 10% hard stop
SMALL_STOP_LOCKOUT_HOURS = 72          # 72 hours post-stop lockout
SMALL_MAX_ENTRY_SPREAD = 0.003         # Skip entry if spread > 0.30%

# Slower Consolidating Mid-Caps (ONDO, CFX, POL, SEI, ARB) -> 12 Hours Recovery Window
SLOW_CONSOLIDATING_MIDCAPS = [
    "ONDO/USD", "CFX/USD", "POL/USD", "SEI/USD", "ARB/USD"
]
SLOW_MIDCAP_EXPIRY_HOURS = 12        # 12 Hours recovery window for slower consolidating mid-caps
FAST_MIDCAP_EXPIRY_HOURS = 4         # 4 Hours recovery window for fast-recovering mid-caps
MIDCAP_RANK_UPDATE_INTERVAL_MINUTES = 60 # Hourly mid-cap momentum rank updates
POST_STOP_LOSS_COOLDOWN_HOURS = 2    # 2 Hours post-stop-loss cool-down lockout before re-entry

# --- Price-Separation Re-Entry Guards ---
LARGECAP_REENTRY_SEPARATION_PCT = 0.0100  # Requires 1.0% price distance from last exit price for Large-Caps
MIDCAP_REENTRY_SEPARATION_PCT = 0.0200    # Requires 2.0% price distance from last exit price for Mid-Caps

# --- Deep-Dip High-Alpha Booster Configuration (ONDO and SUI) ---
DEEP_DIP_BOOSTER_PAIRS = ["ONDO/USD", "SUI/USD"]
DEEP_DIP_BOOSTER_POSITION_SIZE = 0.080  # 8.0% of portfolio equity ($8,000 USD per coin)
DEEP_DIP_TRIGGER_THRESHOLD_PCT = -0.050 # Triggers booster when 24h drop <= -5.0%

# Binance Klines URLs for macro regime and momentum signals
BINANCE_KLINE_URLS = [
    "https://api.binance.com/api/v3/klines",
    "https://data-api.binance.vision/api/v3/klines",
]
# --- Separate Dynamic Trailing Profit Ratchets ---
# Large-Cap Ratchet (Precision Tighter Locking for Lower-Volatility Majors)
LARGECAP_TRAILING_RATCHETS = [
    (1.0040, 1.0025),  # Peak +0.40% -> Lock +0.25% floor (+0.05% Net Profit)
    (1.0080, 1.0055),  # Peak +0.80% -> Lock +0.55% floor (+0.35% Net Profit)
    (1.0150, 1.0110),  # Peak +1.50% -> Lock +1.10% floor (+0.90% Net Profit)
    (1.0250, 1.0190),  # Peak +2.50% -> Lock +1.90% floor (+1.70% Net Profit)
]

# Mid-Cap Ratchet (Wider High-Growth Locking for Higher-Volatility Mid-Caps)
MIDCAP_TRAILING_RATCHETS = [
    (1.0070, 1.0045),  # Peak +0.70% -> Lock +0.45% floor (+0.25% Net Profit)
    (1.0150, 1.0100),  # Peak +1.50% -> Lock +1.00% floor (+0.80% Net Profit)
    (1.0300, 1.0220),  # Peak +3.00% -> Lock +2.20% floor (+2.00% Net Profit)
    (1.0500, 1.0380),  # Peak +5.00% -> Lock +3.80% floor (+3.60% Net Profit)
]

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
REVERSAL_LOOKBACK_BARS = 1440   # 1 day of 1m bars (proven optimal in optimization report)
REVERSAL_K = 1.5
REVERSAL_HOLD_BARS = 1440       # 1 day hold (proven optimal in optimization report)
REVERSAL_CHECK_EVERY = 1        # minute-by-minute dip signal evaluation

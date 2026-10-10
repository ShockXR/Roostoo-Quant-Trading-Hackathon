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
EQUITY_LOG_PATH = "logs/equity.jsonl"
COMPETITION_START_MS = 1_791_072_000_000  # 2026-10-04 00:00 UTC; live trading Oct 4-17

# --- Mid-cap sleeve (docs/MIDCAP_RESEARCH.md; BTC and ETH excluded) ---
MIDCAP_PAIRS = [
    "ENA/USD", "FET/USD", "WLD/USD", "UNI/USD", "TAO/USD", "ONDO/USD", "AAVE/USD", "VIRTUAL/USD", "HBAR/USD",
    "ARB/USD", "ICP/USD", "FIL/USD", "SEI/USD", "APT/USD", "ASTER/USD", "XLM/USD", "PUMP/USD",
]
MIDCAP_TARGET_DAILY_VOL = 0.015   # team decision 2026-10-05
MIDCAP_REBALANCE_EVERY_HOURS = 24  # rebalance at 00:00 UTC
MIDCAP_REBALANCE_DELAY_MS = 120_000  # wait for the hourly kline to close on Binance
MIDCAP_REBALANCE_BAND = 0.01       # skip per-coin trades under 1% of equity (as in the backtest)
MIDCAP_MAX_TRADES_PER_HOUR = 40    # replay peak was 19 in one hour; the API budget (about 3 orders/minute) binds first
MAX_CLOCK_SKEW_MS = 10_000         # tolerated lead of the server clock over this machine's when judging quote age
MIDCAP_MAX_REJECTIONS_PER_PAIR = 2   # stop retrying a pair the exchange refuses this often per rebalance
MIDCAP_MAX_REBALANCE_CYCLES = 15   # give up on a rebalance that cannot finish (e.g. repeated rejections)
# Staged drawdown de-risk from the equity high-water mark: half size beyond 3%, 30% beyond 6%.
# Never zero, so the bot keeps trading; size returns at the next daily rebalance once drawdown recovers.
MIDCAP_DRAWDOWN_SCHEDULE = ((0.03, 0.5), (0.06, 0.3))
MIDCAP_HISTORY_HOURS = 400         # hourly closes fetched for signals (needs at least 169)
MIDCAP_STATE_PATH = "data_store/midcap_state.json"
MIDCAP_LIVE_STATE_PATH = "data_store/midcap_live_state.json"  # live and paper state never share a file
BINANCE_KLINE_URLS = ("https://api.binance.com/api/v3/klines", "https://data-api.binance.vision/api/v3/klines")

# --- Three-sleeve book (docs/SLEEVES.md; runs with --strategy sleeves) ---
# Disjoint universes. Large and mid include every coin the Oct 6-9 live bot traded, so
# today's holdings are re-scored by the new signals instead of being dumped unexamined.
SLEEVE_PAIRS = {
    "large": ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
              "DOGE/USD", "LINK/USD", "AVAX/USD", "DOT/USD", "LTC/USD", "SUI/USD"],
    "mid": ["UNI/USD", "AAVE/USD", "CRV/USD", "PENDLE/USD", "CAKE/USD", "ONDO/USD", "ENA/USD", "ARB/USD",
            "POL/USD", "SEI/USD", "CFX/USD", "FET/USD", "WLD/USD", "TAO/USD", "VIRTUAL/USD", "HBAR/USD",
            "ICP/USD", "FIL/USD", "APT/USD", "ASTER/USD", "XLM/USD", "PUMP/USD"],
    "small": ["PEPE/USD", "SHIB/USD", "BONK/USD", "WIF/USD", "FLOKI/USD", "PENGU/USD", "TRUMP/USD",
              "1000CHEEMS/USD", "EIGEN/USD", "BIO/USD"],
}
# Budgets in recovery mode (equity below PUSH_MODE_EQUITY). Sum of gross caps = 70%.
SLEEVE_PARAMS = {
    "large": {"target_daily_vol": 0.008, "max_gross": 0.35, "max_weight": 0.15, "regime_off_scale": 0.0},
    "mid": {"target_daily_vol": 0.008, "max_gross": 0.30, "max_weight": 0.08, "regime_off_scale": 0.5},
    "small": {"target_daily_vol": 0.0025, "max_gross": 0.05, "max_weight": 0.02, "regime_off_scale": 0.0},
}
LARGE_LOOKBACK_HOURS = 28 * 24   # Model 3: 28-day momentum
LARGE_TOP_K = 3
REGIME_SMA_HOURS = 100 * 24      # BTC above its 100-day average = risk on
SMALL_LOOKBACK_HOURS = 168
SMALL_CONFIRM_HOURS = 24
SMALL_TOP_K = 2
SLEEVE_HISTORY_HOURS = {"large": REGIME_SMA_HOURS + 100, "mid": 400, "small": 400}
# Push mode: once equity is back above the starting capital at a daily rebalance,
# large and mid exposure is multiplied (caps still apply). Decided once a day, so it
# does not flap around the threshold.
PUSH_MODE_EQUITY = 100_000.0
PUSH_MULTIPLIER = {"large": 1.5, "mid": 1.25, "small": 1.0}
# Small-cap guards: hard stop from the average entry price (checked every minute),
# a lockout after a stop, and no new small-cap entry through a wide Roostoo spread.
SMALL_STOP_LOSS = 0.10
SMALL_STOP_LOCKOUT_HOURS = 72
SMALL_MAX_ENTRY_SPREAD = 0.003
# Activity: if nothing has filled by this UTC hour, trade the largest drift back to target
# (deadband ACTIVITY_BAND) so every competition day has a strategy trade.
ACTIVITY_HOUR_UTC = 20
ACTIVITY_BAND = 0.001
SLEEVES_STATE_PATH = "data_store/sleeves_state.json"
SLEEVES_LIVE_STATE_PATH = "data_store/sleeves_live_state.json"

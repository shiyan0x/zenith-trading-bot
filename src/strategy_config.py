"""
strategy_config.py — Default parameters for both trading strategies.

Centralised here so strategy adapters and research scripts can import
constants from a single location.
"""

# ============================================================================
# TIMEFRAME
# ============================================================================
DEFAULT_TIMEFRAME = "15min"

# Minimum candle history required before firing any signal.
# Must be >= max(indicator lookback periods) to avoid NaN signals.
MIN_CANDLES_REQUIRED = 50

# ============================================================================
# STRATEGY 1: EMA + VWAP + RSI (15-Minute Trend Strategy)
# ============================================================================
EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
EMA_TREND_PERIOD = 200   # Long-term trend filter (optional)

RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
RSI_BULL_MIN = 50        # For long entries: RSI must be above this
RSI_BULL_MAX = 70        # For long entries: RSI must be below this
RSI_BEAR_MIN = 30        # For short entries: RSI must be above this
RSI_BEAR_MAX = 50        # For short entries: RSI must be below this

# ============================================================================
# STRATEGY 2: RSI + BOLLINGER BANDS MEAN REVERSION
# ============================================================================
BB_PERIOD = 20
BB_STD_DEV = 2.0

ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25  # ADX < this = ranging market (safe to trade)

# Mean reversion RSI thresholds
MR_RSI_PERIOD = 14
MR_RSI_OVERBOUGHT = 70
MR_RSI_OVERSOLD = 30

# Time stop: close trade if no reversion within N bars
MR_TIME_STOP_BARS = 15

# ============================================================================
# RISK MANAGEMENT
# ============================================================================
DEFAULT_ACCOUNT_BALANCE = 10000.0
MAX_RISK_PER_TRADE_PCT = 1.0    # Risk 1% of account per trade
DEFAULT_RR_RATIO = 2.0          # Default Risk:Reward = 1:2
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 2.0        # Stop-loss = 2x ATR from entry

# ============================================================================
# BACKTESTING
# ============================================================================
INITIAL_CAPITAL = 10000.0
COMMISSION_PCT = 0.05           # 0.05% per trade (round-trip = 0.1%)
SLIPPAGE_PCT = 0.01             # 0.01% slippage per trade

# ============================================================================
# SIGNAL LABELS
# ============================================================================
SIGNAL_BUY = "BUY"
SIGNAL_SELL = "SELL"
SIGNAL_HOLD = "HOLD"

# Signal strength levels
STRENGTH_STRONG = "STRONG"
STRENGTH_MODERATE = "MODERATE"
STRENGTH_WEAK = "WEAK"

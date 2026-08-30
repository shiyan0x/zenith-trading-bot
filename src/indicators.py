"""
Technical Indicator Library
============================
Pure pandas/numpy implementations — no TA-Lib dependency.

All functions accept pandas Series or DataFrame columns and return
pandas Series for easy integration with DataFrames.
"""

import pandas as pd
import numpy as np


# ============================================================================
# MOVING AVERAGES
# ============================================================================

def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average.

    Gives more weight to recent prices, making it more responsive
    to new information than SMA.

    Args:
        series: Price series (typically close prices)
        period: Lookback period

    Returns:
        EMA series
    """
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """
    Simple Moving Average.

    Args:
        series: Price series
        period: Lookback period

    Returns:
        SMA series
    """
    return series.rolling(window=period).mean()


# ============================================================================
# MOMENTUM INDICATORS
# ============================================================================

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (Wilder's smoothing method).

    Measures the speed and magnitude of price changes.
    - RSI > 70 = Overbought
    - RSI < 30 = Oversold

    Args:
        series: Price series (typically close)
        period: Lookback period (default: 14)

    Returns:
        RSI series (0-100)
    """
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Use Wilder's smoothing (equivalent to EMA with alpha = 1/period)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi_values = 100.0 - (100.0 / (1.0 + rs))

    return rsi_values


# ============================================================================
# VOLATILITY INDICATORS
# ============================================================================

def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0
) -> tuple:
    """
    Bollinger Bands.

    Price channels based on standard deviation around a moving average.
    - Upper Band = SMA + (std_dev × σ)
    - Lower Band = SMA - (std_dev × σ)

    Args:
        series: Price series (typically close)
        period: SMA lookback period (default: 20)
        std_dev: Number of standard deviations (default: 2.0)

    Returns:
        Tuple of (upper_band, middle_band, lower_band) as pd.Series
    """
    middle = sma(series, period)
    std = series.rolling(window=period).std()

    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)

    return upper, middle, lower


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14
) -> pd.Series:
    """
    Average True Range (Wilder's smoothing).

    Measures market volatility by decomposing the entire range
    of a bar including gaps.

    Args:
        high: High prices
        low: Low prices
        close: Close prices
        period: Lookback period (default: 14)

    Returns:
        ATR series
    """
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothing
    atr_values = true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return atr_values


# ============================================================================
# TREND INDICATORS
# ============================================================================

def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14
) -> pd.Series:
    """
    Average Directional Index.

    Measures trend strength (not direction):
    - ADX < 20  = Weak/No trend (ranging market)
    - ADX 20-25 = Emerging trend
    - ADX > 25  = Strong trend
    - ADX > 50  = Very strong trend

    Used as a filter in mean reversion strategies: only trade when ADX < 25.

    Args:
        high: High prices
        low: Low prices
        close: Close prices
        period: Lookback period (default: 14)

    Returns:
        ADX series
    """
    # Calculate +DM and -DM
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where(
        (up_move > down_move) & (up_move > 0), up_move, 0.0
    ), index=high.index)

    minus_dm = pd.Series(np.where(
        (down_move > up_move) & (down_move > 0), down_move, 0.0
    ), index=high.index)

    # Calculate ATR for normalization
    atr_values = atr(high, low, close, period)

    # Smooth +DM and -DM using Wilder's method
    smooth_plus_dm = plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smooth_minus_dm = minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # Calculate +DI and -DI
    plus_di = 100.0 * smooth_plus_dm / atr_values
    minus_di = 100.0 * smooth_minus_dm / atr_values

    # Calculate DX
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = 100.0 * di_diff / di_sum.replace(0, np.nan)

    # Smooth DX to get ADX
    adx_values = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return adx_values


# ============================================================================
# VOLUME INDICATORS
# ============================================================================

def vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series
) -> pd.Series:
    """
    Volume Weighted Average Price.

    The average price weighted by volume — shows where institutional
    money is positioned. Acts as dynamic support/resistance.

    - Price > VWAP = Bullish institutional bias
    - Price < VWAP = Bearish institutional bias

    Note: VWAP resets each trading session. This implementation uses
    a cumulative VWAP across the entire dataset. For session-based
    VWAP, group by trading day before calling this function.

    Args:
        high: High prices
        low: Low prices
        close: Close prices
        volume: Volume data

    Returns:
        VWAP series
    """
    typical_price = (high + low + close) / 3.0
    cumulative_tp_vol = (typical_price * volume).cumsum()
    cumulative_vol = volume.cumsum()

    vwap_values = cumulative_tp_vol / cumulative_vol.replace(0, np.nan)

    return vwap_values


def vwap_session(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series
) -> pd.Series:
    """
    Session-based VWAP — resets at the start of each trading day.

    More accurate than cumulative VWAP for intraday strategies.
    Requires a DatetimeIndex on the input Series.

    Args:
        high: High prices (DatetimeIndex)
        low: Low prices
        close: Close prices
        volume: Volume data

    Returns:
        Session VWAP series (resets daily)
    """
    typical_price = (high + low + close) / 3.0
    tp_vol = typical_price * volume

    # Group by date for session-based reset
    dates = high.index.date

    cum_tp_vol = tp_vol.groupby(dates).cumsum()
    cum_vol = volume.groupby(dates).cumsum()

    vwap_values = cum_tp_vol / cum_vol.replace(0, np.nan)

    return vwap_values


# ============================================================================
# CANDLESTICK PATTERN DETECTION
# ============================================================================

def detect_bullish_rejection(
    open_prices: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series
) -> pd.Series:
    """
    Detect bullish rejection candles (hammer / bullish pin bar).

    A bullish rejection has:
    - Long lower wick (>= 2x the body size)
    - Small body
    - Close >= Open (bullish candle preferred)

    Returns:
        Boolean series — True where bullish rejection detected
    """
    body = (close - open_prices).abs()
    lower_wick = pd.concat([open_prices, close], axis=1).min(axis=1) - low
    upper_wick = high - pd.concat([open_prices, close], axis=1).max(axis=1)

    is_bullish_body = close >= open_prices
    has_long_lower_wick = lower_wick >= (body * 2)
    has_small_upper_wick = upper_wick <= (body * 0.5 + 0.001)  # small tolerance

    return is_bullish_body & has_long_lower_wick & has_small_upper_wick


def detect_bearish_rejection(
    open_prices: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series
) -> pd.Series:
    """
    Detect bearish rejection candles (shooting star / bearish pin bar).

    A bearish rejection has:
    - Long upper wick (>= 2x the body size)
    - Small body
    - Close <= Open (bearish candle preferred)

    Returns:
        Boolean series — True where bearish rejection detected
    """
    body = (close - open_prices).abs()
    upper_wick = high - pd.concat([open_prices, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_prices, close], axis=1).min(axis=1) - low

    is_bearish_body = close <= open_prices
    has_long_upper_wick = upper_wick >= (body * 2)
    has_small_lower_wick = lower_wick <= (body * 0.5 + 0.001)

    return is_bearish_body & has_long_upper_wick & has_small_lower_wick


def detect_bullish_engulfing(
    open_prices: pd.Series,
    close: pd.Series
) -> pd.Series:
    """
    Detect bullish engulfing pattern.

    Current candle: bullish (close > open)
    Previous candle: bearish (close < open)
    Current body completely engulfs previous body.

    Returns:
        Boolean series
    """
    prev_bearish = close.shift(1) < open_prices.shift(1)
    curr_bullish = close > open_prices

    curr_engulfs = (
        (open_prices <= close.shift(1)) &
        (close >= open_prices.shift(1))
    )

    return prev_bearish & curr_bullish & curr_engulfs


def detect_bearish_engulfing(
    open_prices: pd.Series,
    close: pd.Series
) -> pd.Series:
    """
    Detect bearish engulfing pattern.

    Current candle: bearish (close < open)
    Previous candle: bullish (close > open)
    Current body completely engulfs previous body.

    Returns:
        Boolean series
    """
    prev_bullish = close.shift(1) > open_prices.shift(1)
    curr_bearish = close < open_prices

    curr_engulfs = (
        (open_prices >= close.shift(1)) &
        (close <= open_prices.shift(1))
    )

    return prev_bullish & curr_bearish & curr_engulfs


def detect_rsi_divergence(
    close: pd.Series,
    rsi_values: pd.Series,
    lookback: int = 10
) -> tuple:
    """
    Detect RSI divergence (bullish and bearish).

    Bullish divergence: Price makes lower low, RSI makes higher low
    Bearish divergence: Price makes higher high, RSI makes lower high

    Args:
        close: Close prices
        rsi_values: RSI series
        lookback: Number of bars to look back for comparison

    Returns:
        Tuple of (bullish_divergence, bearish_divergence) boolean Series
    """
    price_low = close.rolling(window=lookback).min()
    price_high = close.rolling(window=lookback).max()
    rsi_low = rsi_values.rolling(window=lookback).min()
    rsi_high = rsi_values.rolling(window=lookback).max()

    # Bullish: price at new low, RSI not at new low
    price_at_low = close <= price_low
    rsi_not_at_low = rsi_values > rsi_low.shift(lookback)
    bullish_div = price_at_low & rsi_not_at_low

    # Bearish: price at new high, RSI not at new high
    price_at_high = close >= price_high
    rsi_not_at_high = rsi_values < rsi_high.shift(lookback)
    bearish_div = price_at_high & rsi_not_at_high

    return bullish_div, bearish_div

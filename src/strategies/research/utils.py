"""
Utility functions: logging setup, data validation, and helpers.
"""

import logging
import sys
import pandas as pd
import numpy as np
from datetime import datetime


def setup_logger(name: str, level=logging.INFO) -> logging.Logger:
    """Create a configured logger with console output."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            "[%(asctime)s] %(name)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def validate_ohlcv(df: pd.DataFrame) -> bool:
    """
    Validate that a DataFrame has required OHLCV columns.
    Returns True if valid, raises ValueError if not.
    """
    required = {"open", "high", "low", "close", "volume"}
    columns = set(col.lower() for col in df.columns)
    missing = required - columns
    if missing:
        raise ValueError(f"Missing required columns: {missing}. "
                         f"DataFrame must have: {required}")
    if df.empty:
        raise ValueError("DataFrame is empty — no data to process.")
    return True


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to lowercase for consistent access."""
    df = df.copy()
    df.columns = [col.lower().strip() for col in df.columns]
    return df


def generate_sample_data(
    n_bars: int = 2000,
    start_price: float = 100.0,
    volatility: float = 0.015,
    trend: float = 0.0001,
    seed: int = 42
) -> pd.DataFrame:
    """
    Generate synthetic OHLCV data for backtesting.
    
    Creates realistic-looking price data with:
    - Trending and ranging periods
    - Variable volatility
    - Proper OHLC relationships (high >= open/close, low <= open/close)
    
    Args:
        n_bars: Number of bars to generate
        start_price: Starting price
        volatility: Base volatility (standard deviation of returns)
        trend: Slight directional bias
        seed: Random seed for reproducibility
    
    Returns:
        DataFrame with columns: datetime, open, high, low, close, volume
    """
    np.random.seed(seed)
    
    # Generate returns with regime changes (trending + ranging)
    returns = np.random.normal(trend, volatility, n_bars)
    
    # Add regime changes: every ~200 bars, flip trend direction
    regime_length = 200
    for i in range(0, n_bars, regime_length):
        regime_trend = np.random.choice([-1, 0, 0.5, 1]) * trend * 3
        end = min(i + regime_length, n_bars)
        returns[i:end] += regime_trend
    
    # Build close prices
    close_prices = np.zeros(n_bars)
    close_prices[0] = start_price
    for i in range(1, n_bars):
        close_prices[i] = close_prices[i - 1] * (1 + returns[i])
    
    # Build OHLC from close
    open_prices = np.zeros(n_bars)
    high_prices = np.zeros(n_bars)
    low_prices = np.zeros(n_bars)
    
    open_prices[0] = start_price
    for i in range(1, n_bars):
        open_prices[i] = close_prices[i - 1] * (1 + np.random.normal(0, volatility * 0.3))
    
    for i in range(n_bars):
        bar_range = abs(close_prices[i] - open_prices[i])
        wick = np.random.uniform(0, bar_range * 0.5 + volatility * close_prices[i])
        high_prices[i] = max(open_prices[i], close_prices[i]) + abs(wick)
        low_prices[i] = min(open_prices[i], close_prices[i]) - abs(np.random.uniform(0, wick))
    
    # Generate volume with spikes
    base_volume = np.random.lognormal(mean=10, sigma=0.5, size=n_bars)
    # Volume spikes near big price moves
    price_changes = np.abs(returns)
    volume = base_volume * (1 + price_changes * 20)
    volume = volume.astype(int)
    
    # Create datetime index (15-minute bars)
    dates = pd.date_range(
        start="2024-01-02 09:15",
        periods=n_bars,
        # pandas 3 rejects several legacy frequency strings; an explicit
        # offset remains compatible with pandas 2 and 3.
        freq=pd.offsets.Minute(15)
    )
    
    df = pd.DataFrame({
        "datetime": dates,
        "open": np.round(open_prices, 2),
        "high": np.round(high_prices, 2),
        "low": np.round(low_prices, 2),
        "close": np.round(close_prices, 2),
        "volume": volume,
    })
    
    df.set_index("datetime", inplace=True)
    return df


def format_pct(value: float) -> str:
    """Format a decimal as a percentage string."""
    return f"{value * 100:.2f}%"


def format_currency(value: float) -> str:
    """Format a number as currency."""
    return f"${value:,.2f}"


def print_header(title: str, width: int = 60):
    """Print a formatted section header."""
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)

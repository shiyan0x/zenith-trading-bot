"""
Strategy 2: RSI + Bollinger Bands Mean Reversion
==================================================

A high win-rate strategy that fades extremes in RANGING markets:
- Bollinger Bands identify price extremes
- RSI confirms oversold/overbought conditions
- ADX filters out trending markets (only trade when ADX < 25)
- Candlestick rejection patterns add confirmation

Target: Middle Bollinger Band (20 SMA)
Win Rate: ~55-70% in ranging conditions

Signal Rules:
    LONG:  ADX < 25, Price <= Lower BB, RSI < 30, rejection candle
    SHORT: ADX < 25, Price >= Upper BB, RSI > 70, rejection candle
"""

import pandas as pd
import numpy as np

from config import (
    BB_PERIOD, BB_STD_DEV,
    MR_RSI_PERIOD, MR_RSI_OVERBOUGHT, MR_RSI_OVERSOLD,
    ADX_PERIOD, ADX_TREND_THRESHOLD,
    ATR_PERIOD, ATR_SL_MULTIPLIER, MR_TIME_STOP_BARS,
    SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD,
    STRENGTH_STRONG, STRENGTH_MODERATE, STRENGTH_WEAK,
)
from src.indicators import (
    rsi, bollinger_bands, adx, atr,
    detect_bullish_rejection, detect_bearish_rejection,
    detect_bullish_engulfing, detect_bearish_engulfing,
    detect_rsi_divergence,
)
from utils import setup_logger, validate_ohlcv, normalize_columns

logger = setup_logger("MeanReversion")


class MeanReversionStrategy:
    """
    RSI + Bollinger Bands Mean Reversion Strategy.
    
    Designed for RANGING markets (ADX < 25). Enters when price
    reaches Bollinger Band extremes with RSI confirmation,
    targeting mean reversion to the middle band.
    
    Usage:
        strategy = MeanReversionStrategy()
        df = strategy.generate_signals(ohlcv_df)
        # df has 'signal', 'signal_strength', 'stop_loss', 'take_profit' columns
    """
    
    def __init__(
        self,
        bb_period: int = BB_PERIOD,
        bb_std: float = BB_STD_DEV,
        rsi_period: int = MR_RSI_PERIOD,
        rsi_ob: float = MR_RSI_OVERBOUGHT,
        rsi_os: float = MR_RSI_OVERSOLD,
        adx_period: int = ADX_PERIOD,
        adx_threshold: float = ADX_TREND_THRESHOLD,
        atr_period: int = ATR_PERIOD,
        atr_sl_mult: float = ATR_SL_MULTIPLIER,
        time_stop_bars: int = MR_TIME_STOP_BARS,
        require_candle_confirmation: bool = True,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_ob = rsi_ob
        self.rsi_os = rsi_os
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.atr_sl_mult = atr_sl_mult
        self.time_stop_bars = time_stop_bars
        self.require_candle = require_candle_confirmation
        
        self.name = "RSI+BB Mean Reversion"
        logger.info(f"Initialized: {self.name}")
        logger.info(f"  BB: {bb_period}/{bb_std}σ | RSI: {rsi_period} | ADX threshold: {adx_threshold}")
        logger.info(f"  Candle confirmation: {require_candle_confirmation}")
    
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all required indicators.
        
        Args:
            df: OHLCV DataFrame
        
        Returns:
            DataFrame with indicator columns
        """
        df = normalize_columns(df.copy())
        validate_ohlcv(df)
        
        # --- Bollinger Bands ---
        df["bb_upper"], df["bb_middle"], df["bb_lower"] = bollinger_bands(
            df["close"], self.bb_period, self.bb_std
        )
        
        # Band position (where price is relative to bands)
        bb_width = df["bb_upper"] - df["bb_lower"]
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / bb_width.replace(0, np.nan)
        
        # --- RSI ---
        df["rsi"] = rsi(df["close"], self.rsi_period)
        
        # --- ADX (Trend Filter) ---
        df["adx"] = adx(df["high"], df["low"], df["close"], self.adx_period)
        df["is_ranging"] = df["adx"] < self.adx_threshold
        
        # --- ATR ---
        df["atr"] = atr(df["high"], df["low"], df["close"], self.atr_period)
        
        # --- Candlestick Patterns ---
        df["bullish_rejection"] = detect_bullish_rejection(
            df["open"], df["high"], df["low"], df["close"]
        )
        df["bearish_rejection"] = detect_bearish_rejection(
            df["open"], df["high"], df["low"], df["close"]
        )
        df["bullish_engulfing"] = detect_bullish_engulfing(df["open"], df["close"])
        df["bearish_engulfing"] = detect_bearish_engulfing(df["open"], df["close"])
        
        # Combined candle signals
        df["bull_candle"] = df["bullish_rejection"] | df["bullish_engulfing"]
        df["bear_candle"] = df["bearish_rejection"] | df["bearish_engulfing"]
        
        # --- RSI Divergence (higher conviction signals) ---
        df["rsi_bull_div"], df["rsi_bear_div"] = detect_rsi_divergence(
            df["close"], df["rsi"], lookback=10
        )
        
        return df
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate mean reversion signals.
        
        Signal Logic:
            BUY:
                1. ADX < 25 (market is ranging)
                2. Price touches/closes below lower Bollinger Band
                3. RSI < 30 (oversold)
                4. Bullish rejection candle (optional but recommended)
            
            SELL:
                1. ADX < 25 (market is ranging)
                2. Price touches/closes above upper Bollinger Band
                3. RSI > 70 (overbought)
                4. Bearish rejection candle (optional but recommended)
        
        Exits:
            - Take Profit: Middle Bollinger Band (20 SMA)
            - Stop Loss: 2x ATR beyond signal bar extreme
            - Time Stop: Close after N bars if no reversion
        
        Args:
            df: OHLCV DataFrame
        
        Returns:
            DataFrame with signal columns
        """
        logger.info("Generating mean reversion signals...")
        
        df = self.compute_indicators(df)
        
        # ================================================================
        # CONDITION LAYERS
        # ================================================================
        
        # Layer 1: Market regime — MUST be ranging
        is_ranging = df["is_ranging"]
        
        # Layer 2: Bollinger Band extremes
        at_lower_bb = df["close"] <= df["bb_lower"]
        at_upper_bb = df["close"] >= df["bb_upper"]
        
        # Layer 3: RSI extremes
        rsi_oversold = df["rsi"] < self.rsi_os
        rsi_overbought = df["rsi"] > self.rsi_ob
        
        # Layer 4: Candlestick confirmation (optional)
        bull_candle = df["bull_candle"]
        bear_candle = df["bear_candle"]
        
        # Layer 5: RSI divergence (bonus conviction)
        rsi_bull_div = df["rsi_bull_div"]
        rsi_bear_div = df["rsi_bear_div"]
        
        # ================================================================
        # SIGNAL SCORING
        # ================================================================
        
        # Core conditions (must all be met for any signal)
        core_buy = is_ranging & at_lower_bb & rsi_oversold
        core_sell = is_ranging & at_upper_bb & rsi_overbought
        
        if self.require_candle:
            # With candle confirmation required
            buy_signal = core_buy & bull_candle
            sell_signal = core_sell & bear_candle
            
            # Allow signals without candle if RSI divergence present
            buy_signal = buy_signal | (core_buy & rsi_bull_div)
            sell_signal = sell_signal | (core_sell & rsi_bear_div)
        else:
            buy_signal = core_buy
            sell_signal = core_sell
        
        # ================================================================
        # SIGNAL ASSIGNMENT
        # ================================================================
        
        df["signal"] = SIGNAL_HOLD
        df.loc[buy_signal, "signal"] = SIGNAL_BUY
        df.loc[sell_signal, "signal"] = SIGNAL_SELL
        
        # ================================================================
        # SIGNAL STRENGTH
        # ================================================================
        
        df["signal_strength"] = STRENGTH_WEAK
        
        # Count conviction layers for buy signals
        buy_conviction = (
            is_ranging.astype(int) +
            at_lower_bb.astype(int) +
            rsi_oversold.astype(int) +
            bull_candle.astype(int) +
            rsi_bull_div.astype(int)
        )
        
        sell_conviction = (
            is_ranging.astype(int) +
            at_upper_bb.astype(int) +
            rsi_overbought.astype(int) +
            bear_candle.astype(int) +
            rsi_bear_div.astype(int)
        )
        
        # Moderate: core conditions met (3/5)
        df.loc[(buy_conviction >= 3) | (sell_conviction >= 3), "signal_strength"] = STRENGTH_MODERATE
        
        # Strong: core + candle + divergence (4-5/5)
        df.loc[(buy_conviction >= 4) | (sell_conviction >= 4), "signal_strength"] = STRENGTH_STRONG
        
        # Only apply strength to actual signals (not holds)
        df.loc[df["signal"] == SIGNAL_HOLD, "signal_strength"] = STRENGTH_WEAK
        
        # ================================================================
        # STOP-LOSS & TAKE-PROFIT
        # ================================================================
        
        df["stop_loss"] = np.nan
        df["take_profit"] = np.nan
        df["time_stop_bar"] = np.nan
        
        # BUY: SL = 2x ATR below entry, TP = middle BB
        buy_mask = df["signal"] == SIGNAL_BUY
        df.loc[buy_mask, "stop_loss"] = (
            df.loc[buy_mask, "close"] - df.loc[buy_mask, "atr"] * self.atr_sl_mult
        ).round(2)
        df.loc[buy_mask, "take_profit"] = df.loc[buy_mask, "bb_middle"].round(2)
        
        # SELL: SL = 2x ATR above entry, TP = middle BB
        sell_mask = df["signal"] == SIGNAL_SELL
        df.loc[sell_mask, "stop_loss"] = (
            df.loc[sell_mask, "close"] + df.loc[sell_mask, "atr"] * self.atr_sl_mult
        ).round(2)
        df.loc[sell_mask, "take_profit"] = df.loc[sell_mask, "bb_middle"].round(2)
        
        # Time stop: mark the bar index where trade should be force-closed
        signal_mask = buy_mask | sell_mask
        signal_indices = df.index[signal_mask]
        for idx in signal_indices:
            pos = df.index.get_loc(idx)
            time_stop_pos = min(pos + self.time_stop_bars, len(df) - 1)
            df.loc[idx, "time_stop_bar"] = time_stop_pos
        
        # ================================================================
        # LOGGING
        # ================================================================
        
        total = (df["signal"] != SIGNAL_HOLD).sum()
        buys = (df["signal"] == SIGNAL_BUY).sum()
        sells = (df["signal"] == SIGNAL_SELL).sum()
        strong = ((df["signal_strength"] == STRENGTH_STRONG) & (df["signal"] != SIGNAL_HOLD)).sum()
        ranging_pct = is_ranging.mean() * 100
        
        logger.info(f"Market was ranging {ranging_pct:.1f}% of the time (ADX < {self.adx_threshold})")
        logger.info(f"Signals: {total} total ({buys} BUY, {sells} SELL, {strong} STRONG)")
        
        return df
    
    def get_latest_signal(self, df: pd.DataFrame) -> dict:
        """
        Get the most recent signal.
        
        Returns:
            dict with signal details
        """
        df = self.generate_signals(df)
        latest = df.iloc[-1]
        
        return {
            "strategy": self.name,
            "datetime": str(latest.name),
            "signal": latest["signal"],
            "strength": latest["signal_strength"],
            "price": latest["close"],
            "bb_upper": round(latest["bb_upper"], 2),
            "bb_middle": round(latest["bb_middle"], 2),
            "bb_lower": round(latest["bb_lower"], 2),
            "bb_pct": round(latest["bb_pct"], 4),
            "rsi": round(latest["rsi"], 2),
            "adx": round(latest["adx"], 2),
            "is_ranging": bool(latest["is_ranging"]),
            "atr": round(latest["atr"], 2),
            "stop_loss": latest["stop_loss"],
            "take_profit": latest["take_profit"],
        }
    
    def __repr__(self):
        return (f"MeanReversionStrategy(bb={self.bb_period}/{self.bb_std}σ, "
                f"rsi={self.rsi_period}, adx_thresh={self.adx_threshold})")

"""
Strategy 1: EMA + VWAP + RSI (15-Minute Trend Strategy)
========================================================

A trend-following strategy that combines:
- EMA 9/21 crossover for trend direction
- VWAP for institutional bias filter  
- RSI for momentum confirmation

Best used during trending market hours (e.g., London-NY overlap).

Signal Rules:
    LONG:  9 EMA > 21 EMA, Price > VWAP, 50 < RSI < 70
    SHORT: 9 EMA < 21 EMA, Price < VWAP, 30 < RSI < 50
"""

import pandas as pd
import numpy as np

from config import (
    EMA_FAST_PERIOD, EMA_SLOW_PERIOD,
    RSI_PERIOD, RSI_BULL_MIN, RSI_BULL_MAX, RSI_BEAR_MIN, RSI_BEAR_MAX,
    ATR_PERIOD, ATR_SL_MULTIPLIER, DEFAULT_RR_RATIO,
    SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD,
    STRENGTH_STRONG, STRENGTH_MODERATE, STRENGTH_WEAK,
)
from src.indicators import ema, rsi, vwap_session, atr
from risk_manager import RiskManager
from utils import setup_logger, validate_ohlcv, normalize_columns

logger = setup_logger("EMA_VWAP_RSI")


class EmaVwapRsiStrategy:
    """
    15-Minute EMA + VWAP + RSI Trend Strategy.
    
    This strategy identifies trending moves using three confluent signals:
    1. EMA crossover (9/21) — trend direction
    2. VWAP — institutional bias
    3. RSI — momentum confirmation (not overbought/oversold)
    
    Usage:
        strategy = EmaVwapRsiStrategy()
        df = strategy.generate_signals(ohlcv_df)
        # df now has 'signal', 'signal_strength', 'stop_loss', 'take_profit' columns
    """
    
    def __init__(
        self,
        ema_fast: int = EMA_FAST_PERIOD,
        ema_slow: int = EMA_SLOW_PERIOD,
        rsi_period: int = RSI_PERIOD,
        rsi_bull_range: tuple = (RSI_BULL_MIN, RSI_BULL_MAX),
        rsi_bear_range: tuple = (RSI_BEAR_MIN, RSI_BEAR_MAX),
        atr_period: int = ATR_PERIOD,
        atr_sl_mult: float = ATR_SL_MULTIPLIER,
        rr_ratio: float = DEFAULT_RR_RATIO,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_bull_min, self.rsi_bull_max = rsi_bull_range
        self.rsi_bear_min, self.rsi_bear_max = rsi_bear_range
        self.atr_period = atr_period
        self.atr_sl_mult = atr_sl_mult
        self.rr_ratio = rr_ratio
        
        self.name = "EMA+VWAP+RSI (15min Trend)"
        logger.info(f"Initialized: {self.name}")
        logger.info(f"  EMA: {ema_fast}/{ema_slow} | RSI: {rsi_period} | R:R = 1:{rr_ratio}")
    
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all required indicators and add them as columns.
        
        Args:
            df: OHLCV DataFrame with DatetimeIndex
        
        Returns:
            DataFrame with indicator columns added
        """
        df = normalize_columns(df.copy())
        validate_ohlcv(df)
        
        # --- Moving Averages ---
        df["ema_fast"] = ema(df["close"], self.ema_fast)
        df["ema_slow"] = ema(df["close"], self.ema_slow)
        
        # --- EMA Crossover Detection ---
        df["ema_bullish"] = df["ema_fast"] > df["ema_slow"]
        df["ema_cross_up"] = df["ema_bullish"] & ~df["ema_bullish"].shift(1).fillna(False)
        df["ema_cross_down"] = ~df["ema_bullish"] & df["ema_bullish"].shift(1).fillna(True)
        
        # --- VWAP ---
        df["vwap"] = vwap_session(df["high"], df["low"], df["close"], df["volume"])
        df["above_vwap"] = df["close"] > df["vwap"]
        
        # --- RSI ---
        df["rsi"] = rsi(df["close"], self.rsi_period)
        
        # --- ATR (for stop-loss calculation) ---
        df["atr"] = atr(df["high"], df["low"], df["close"], self.atr_period)
        
        return df
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate trading signals based on EMA + VWAP + RSI confluence.
        
        Signal Logic:
            BUY:  EMA fast > EMA slow AND Price > VWAP AND 50 < RSI < 70
            SELL: EMA fast < EMA slow AND Price < VWAP AND 30 < RSI < 50
            HOLD: Otherwise
        
        Also calculates:
            - Signal strength (STRONG/MODERATE/WEAK)
            - Stop-loss (ATR-based)
            - Take-profit (R:R based)
        
        Args:
            df: OHLCV DataFrame
        
        Returns:
            DataFrame with signal, signal_strength, stop_loss, take_profit columns
        """
        logger.info("Generating signals...")
        
        df = self.compute_indicators(df)
        
        # --- Individual Conditions ---
        # EMA trend
        ema_bull = df["ema_fast"] > df["ema_slow"]
        ema_bear = df["ema_fast"] < df["ema_slow"]
        
        # VWAP filter
        vwap_bull = df["close"] > df["vwap"]
        vwap_bear = df["close"] < df["vwap"]
        
        # RSI momentum (not at extremes)
        rsi_bull = (df["rsi"] > self.rsi_bull_min) & (df["rsi"] < self.rsi_bull_max)
        rsi_bear = (df["rsi"] > self.rsi_bear_min) & (df["rsi"] < self.rsi_bear_max)
        
        # --- Confluence Scoring ---
        bull_score = ema_bull.astype(int) + vwap_bull.astype(int) + rsi_bull.astype(int)
        bear_score = ema_bear.astype(int) + vwap_bear.astype(int) + rsi_bear.astype(int)
        
        # --- Signal Generation ---
        # Full confluence (all 3 conditions) = STRONG signal
        buy_signal = bull_score == 3
        sell_signal = bear_score == 3
        
        df["signal"] = SIGNAL_HOLD
        df.loc[buy_signal, "signal"] = SIGNAL_BUY
        df.loc[sell_signal, "signal"] = SIGNAL_SELL
        
        # --- Signal Strength ---
        df["signal_strength"] = STRENGTH_WEAK
        
        # Moderate: 2 out of 3 conditions met
        moderate_buy = (bull_score == 2) & ~buy_signal
        moderate_sell = (bear_score == 2) & ~sell_signal
        df.loc[moderate_buy | moderate_sell, "signal_strength"] = STRENGTH_MODERATE
        
        # Strong: all 3 conditions met
        df.loc[buy_signal | sell_signal, "signal_strength"] = STRENGTH_STRONG
        
        # --- Stop-Loss & Take-Profit ---
        df["stop_loss"] = np.nan
        df["take_profit"] = np.nan
        
        # BUY stops/targets
        buy_mask = df["signal"] == SIGNAL_BUY
        df.loc[buy_mask, "stop_loss"] = (
            df.loc[buy_mask, "close"] - df.loc[buy_mask, "atr"] * self.atr_sl_mult
        ).round(2)
        df.loc[buy_mask, "take_profit"] = (
            df.loc[buy_mask, "close"] + 
            (df.loc[buy_mask, "close"] - df.loc[buy_mask, "stop_loss"]) * self.rr_ratio
        ).round(2)
        
        # SELL stops/targets
        sell_mask = df["signal"] == SIGNAL_SELL
        df.loc[sell_mask, "stop_loss"] = (
            df.loc[sell_mask, "close"] + df.loc[sell_mask, "atr"] * self.atr_sl_mult
        ).round(2)
        df.loc[sell_mask, "take_profit"] = (
            df.loc[sell_mask, "close"] - 
            (df.loc[sell_mask, "stop_loss"] - df.loc[sell_mask, "close"]) * self.rr_ratio
        ).round(2)
        
        # --- Summary ---
        total_signals = (df["signal"] != SIGNAL_HOLD).sum()
        buy_count = (df["signal"] == SIGNAL_BUY).sum()
        sell_count = (df["signal"] == SIGNAL_SELL).sum()
        strong_count = (df["signal_strength"] == STRENGTH_STRONG).sum()
        
        logger.info(f"Signals generated: {total_signals} total "
                     f"({buy_count} BUY, {sell_count} SELL, {strong_count} STRONG)")
        
        return df
    
    def get_latest_signal(self, df: pd.DataFrame) -> dict:
        """
        Get the most recent signal from processed data.
        
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
            "ema_fast": round(latest["ema_fast"], 2),
            "ema_slow": round(latest["ema_slow"], 2),
            "vwap": round(latest["vwap"], 2),
            "rsi": round(latest["rsi"], 2),
            "atr": round(latest["atr"], 2),
            "stop_loss": latest["stop_loss"],
            "take_profit": latest["take_profit"],
        }
    
    def __repr__(self):
        return (f"EmaVwapRsiStrategy(ema={self.ema_fast}/{self.ema_slow}, "
                f"rsi={self.rsi_period}, rr=1:{self.rr_ratio})")

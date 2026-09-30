"""
context_analyzer.py — Evaluates Market Context for Detected Chart Patterns.

Assesses market regime, support/resistance, volume dynamics, volatility, and
technical indicator alignment strictly using information available at detection time.
"""

from typing import List, Optional, Dict, Any
import numpy as np
import pandas as pd

from src.core.market_feed import Candle
from src import indicators as ind
from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternDirection,
    MarketContext,
)


class MarketContextAnalyzer:
    """
    Evaluates multi-factor market context at the moment a pattern is detected.
    Provides an objective confluence score and context breakdown.
    """

    def __init__(self, atr_period: int = 14, rsi_period: int = 14, adx_period: int = 14):
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.adx_period = adx_period

    def analyze(
        self,
        candles: List[Candle],
        idx: Optional[int] = None,
        higher_tf_candles: Optional[List[Candle]] = None,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
    ) -> MarketContext:
        """
        Analyze the market context at candle index `idx` (default is the last candle).
        Operates strictly with zero lookahead bias up to `idx`.
        """
        if idx is None:
            idx = len(candles) - 1

        sub_candles = candles[: idx + 1]
        n = len(sub_candles)
        c = sub_candles[-1]

        if n < 20:
            # Fallback for short histories
            return MarketContext(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=c.timestamp,
                current_trend="ranging",
                trend_strength=0.0,
            )

        closes = pd.Series([x.close for x in sub_candles], dtype=float)
        highs = pd.Series([x.high for x in sub_candles], dtype=float)
        lows = pd.Series([x.low for x in sub_candles], dtype=float)
        volumes = pd.Series([x.volume for x in sub_candles], dtype=float)

        # 1. Moving Averages & Trend
        ema9 = ind.ema(closes, 9).iloc[-1]
        ema21 = ind.ema(closes, 21).iloc[-1]

        if n >= 50:
            ema50 = ind.ema(closes, 50).iloc[-1]
            if ema9 > ema21 > ema50:
                current_trend = "uptrend"
            elif ema9 < ema21 < ema50:
                current_trend = "downtrend"
            else:
                current_trend = "ranging"
        else:
            if ema9 > ema21:
                current_trend = "uptrend"
            elif ema9 < ema21:
                current_trend = "downtrend"
            else:
                current_trend = "ranging"

        # 2. ADX & Trend Strength
        adx_series = ind.adx(highs, lows, closes, min(self.adx_period, n - 2))
        adx_val = float(adx_series.iloc[-1]) if not adx_series.empty and not np.isnan(adx_series.iloc[-1]) else 20.0
        trend_strength = min(1.0, adx_val / 50.0)

        # 3. Support & Resistance Levels (Recent 30 bars)
        lookback_window = min(40, n)
        recent_highs = highs.iloc[-lookback_window:-1]
        recent_lows = lows.iloc[-lookback_window:-1]
        nearby_resistance = float(recent_highs.max()) if not recent_highs.empty else c.high
        nearby_support = float(recent_lows.min()) if not recent_lows.empty else c.low

        # 4. Volume Dynamics
        vol_sma20 = volumes.rolling(20, min_periods=5).mean().iloc[-1]
        volume_ratio = float(c.volume / vol_sma20) if vol_sma20 > 0 else 1.0
        if volume_ratio > 1.5:
            volume_trend = "expanding"
        elif volume_ratio < 0.6:
            volume_trend = "drying_up"
        else:
            volume_trend = "normal"

        # 5. Volatility (ATR)
        atr_series = ind.atr(highs, lows, closes, min(self.atr_period, n - 2))
        atr_val = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else (c.high - c.low)
        atr_pct = float(atr_val / c.close * 100) if c.close > 0 else 0.0

        # 6. Technical Indicators: RSI & VWAP
        rsi_series = ind.rsi(closes, min(self.rsi_period, n - 2))
        rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty and not np.isnan(rsi_series.iloc[-1]) else 50.0

        vwap_series = ind.vwap(highs, lows, closes, volumes)
        vwap_val = float(vwap_series.iloc[-1]) if not vwap_series.empty and not np.isnan(vwap_series.iloc[-1]) else c.close
        vwap_position = "above_vwap" if c.close >= vwap_val else "below_vwap"

        # 7. Higher Timeframe Trend (if candles provided)
        higher_tf_trend = None
        if higher_tf_candles and len(higher_tf_candles) >= 15:
            htf_closes = pd.Series([x.close for x in higher_tf_candles], dtype=float)
            htf_ema9 = ind.ema(htf_closes, 9).iloc[-1]
            htf_ema21 = ind.ema(htf_closes, 21).iloc[-1]
            higher_tf_trend = "uptrend" if htf_ema9 > htf_ema21 else "downtrend"

        # 8. Regime Classification
        if adx_val >= 25 and current_trend == "uptrend":
            regime = "trending_bull"
        elif adx_val >= 25 and current_trend == "downtrend":
            regime = "trending_bear"
        elif atr_pct > 2.5:
            regime = "volatile"
        else:
            regime = "consolidating"

        # 9. Confluence Summary Score (-1.0 to +1.0)
        score = 0.0
        if current_trend == "uptrend":
            score += 0.35
        elif current_trend == "downtrend":
            score -= 0.35

        if vwap_position == "above_vwap":
            score += 0.20
        else:
            score -= 0.20

        if rsi_val > 55:
            score += 0.25
        elif rsi_val < 45:
            score -= 0.25

        if higher_tf_trend == "uptrend":
            score += 0.20
        elif higher_tf_trend == "downtrend":
            score -= 0.20

        summary_score = round(max(-1.0, min(1.0, score)), 2)

        return MarketContext(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=c.timestamp,
            current_trend=current_trend,
            trend_strength=round(trend_strength, 2),
            higher_tf_trend=higher_tf_trend,
            nearby_support=round(nearby_support, 2),
            nearby_resistance=round(nearby_resistance, 2),
            volume_ratio=round(volume_ratio, 2),
            volume_trend=volume_trend,
            atr=round(atr_val, 2),
            atr_pct=round(atr_pct, 2),
            rsi=round(rsi_val, 1),
            macd_histogram=None,
            vwap_position=vwap_position,
            regime=regime,
            summary_score=summary_score,
        )

    def evaluate_pattern_confluence(
        self, pattern: PatternMatch, context: MarketContext
    ) -> float:
        """
        Evaluate alignment between a pattern and its surrounding market context.
        Returns an adjusted confluence factor between 0.0 and 1.0.
        """
        multiplier = 1.0

        # Bullish pattern alignment
        if pattern.direction == PatternDirection.BULLISH:
            if context.current_trend == "uptrend":
                multiplier += 0.15
            elif context.current_trend == "downtrend" and pattern.pattern_type.value == "reversal":
                # Reversal pattern makes sense after downtrend!
                multiplier += 0.10
            elif context.current_trend == "downtrend" and pattern.pattern_type.value == "continuation":
                # Bullish flag in a downtrend is contradictory
                multiplier -= 0.25

            if context.volume_trend == "expanding":
                multiplier += 0.10
            if context.vwap_position == "above_vwap":
                multiplier += 0.10
            if context.rsi and context.rsi > 75:
                # Overbought caution
                multiplier -= 0.10

        # Bearish pattern alignment
        elif pattern.direction == PatternDirection.BEARISH:
            if context.current_trend == "downtrend":
                multiplier += 0.15
            elif context.current_trend == "uptrend" and pattern.pattern_type.value == "reversal":
                # Reversal pattern makes sense after uptrend!
                multiplier += 0.10
            elif context.current_trend == "uptrend" and pattern.pattern_type.value == "continuation":
                # Bearish flag in an uptrend is contradictory
                multiplier -= 0.25

            if context.volume_trend == "expanding":
                multiplier += 0.10
            if context.vwap_position == "below_vwap":
                multiplier += 0.10
            if context.rsi and context.rsi < 25:
                # Oversold caution
                multiplier -= 0.10

        return max(0.1, min(1.0, pattern.confidence_score * multiplier))

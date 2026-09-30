"""
candlestick_detector.py — Deterministic Candlestick Pattern Recognition.

Detects single and multi-candle formations with exact geometric criteria:
- Bullish Engulfing
- Bearish Engulfing
- Hammer
- Shooting Star
- Doji
- Morning Star
- Evening Star

Strict rules:
- Operates only on closed candles.
- Body, upper wick, lower wick, and range proportions are computed mathematically.
- Trend context (prior slope / moving average) is required for reversal formations.
- Zero lookahead bias.
"""

from typing import List, Optional
import numpy as np

from src.core.market_feed import Candle
from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternType,
    PatternStatus,
    PatternDirection,
    BULLISH_ENGULFING,
    BEARISH_ENGULFING,
    HAMMER,
    SHOOTING_STAR,
    DOJI,
    MORNING_STAR,
    EVENING_STAR,
)


class CandlestickDetector:
    """
    Scans a series of closed candles and identifies valid candlestick patterns at the latest bar.
    """

    def __init__(self, doji_threshold: float = 0.10, hammer_wick_ratio: float = 2.0):
        self.doji_threshold = doji_threshold
        self.hammer_wick_ratio = hammer_wick_ratio

    @staticmethod
    def _candle_metrics(c: Candle) -> dict:
        total_range = max(c.high - c.low, 1e-8)
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.close, c.open)
        lower_wick = min(c.close, c.open) - c.low
        is_bullish = c.close > c.open
        is_bearish = c.close < c.open
        return {
            'range': total_range,
            'body': body,
            'body_ratio': body / total_range,
            'upper_wick': upper_wick,
            'lower_wick': lower_wick,
            'upper_wick_ratio': upper_wick / total_range,
            'lower_wick_ratio': lower_wick / total_range,
            'is_bullish': is_bullish,
            'is_bearish': is_bearish,
        }

    @staticmethod
    def _is_downtrend(candles: List[Candle], end_idx: int, lookback: int = 5) -> bool:
        """Check if prior candles were sloping down."""
        if end_idx < lookback:
            return False
        slice_closes = [candles[i].close for i in range(end_idx - lookback, end_idx)]
        return slice_closes[-1] < slice_closes[0]

    @staticmethod
    def _is_uptrend(candles: List[Candle], end_idx: int, lookback: int = 5) -> bool:
        """Check if prior candles were sloping up."""
        if end_idx < lookback:
            return False
        slice_closes = [candles[i].close for i in range(end_idx - lookback, end_idx)]
        return slice_closes[-1] > slice_closes[0]

    def detect_at_index(
        self,
        candles: List[Candle],
        idx: int,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m"
    ) -> List[PatternMatch]:
        """
        Evaluate candle patterns ending precisely at candle `idx`.
        Returns all valid pattern matches found.
        """
        if idx < 2 or idx >= len(candles):
            return []

        matches: List[PatternMatch] = []
        c = candles[idx]
        m = self._candle_metrics(c)

        # 1. Doji
        if m['body_ratio'] <= self.doji_threshold:
            matches.append(PatternMatch(
                pattern_name=DOJI,
                pattern_type=PatternType.CANDLESTICK,
                direction=PatternDirection.NEUTRAL,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=c.timestamp,
                candle_range=(idx, idx),
                timestamps=(c.timestamp, c.timestamp),
                status=PatternStatus.CONFIRMED,
                key_levels={
                    'high': c.high,
                    'low': c.low,
                    'close': c.close,
                    'stop_loss': c.low - 0.5 * m['range'],
                    'target_price': c.high + m['range']
                },
                confidence_score=max(0.5, 1.0 - m['body_ratio'] * 5),
                supporting_evidence={
                    'body_ratio': round(m['body_ratio'], 4),
                    'total_range': round(m['range'], 2),
                },
                detection_method="rule_based_candlestick",
            ))

        # 2. Hammer (Bullish Reversal after Downtrend)
        if (
            m['lower_wick'] >= self.hammer_wick_ratio * max(m['body'], 1e-8)
            and m['upper_wick_ratio'] <= 0.15
            and m['body'] > 0
            and self._is_downtrend(candles, idx, lookback=4)
        ):
            stop_loss = c.low - 0.2 * m['range']
            target_price = c.high + 1.5 * m['range']
            matches.append(PatternMatch(
                pattern_name=HAMMER,
                pattern_type=PatternType.CANDLESTICK,
                direction=PatternDirection.BULLISH,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=c.timestamp,
                candle_range=(idx, idx),
                timestamps=(c.timestamp, c.timestamp),
                status=PatternStatus.CONFIRMED,
                key_levels={
                    'stop_loss': stop_loss,
                    'target_price': target_price,
                    'low': c.low,
                    'breakout_price': c.high
                },
                confidence_score=min(0.85, 0.5 + (m['lower_wick'] / m['range']) * 0.4),
                supporting_evidence={
                    'lower_wick_ratio': round(m['lower_wick_ratio'], 4),
                    'body_ratio': round(m['body_ratio'], 4),
                    'prior_trend': 'downtrend'
                },
                detection_method="rule_based_candlestick",
            ))

        # 3. Shooting Star (Bearish Reversal after Uptrend)
        if (
            m['upper_wick'] >= self.hammer_wick_ratio * max(m['body'], 1e-8)
            and m['lower_wick_ratio'] <= 0.15
            and m['body'] > 0
            and self._is_uptrend(candles, idx, lookback=4)
        ):
            stop_loss = c.high + 0.2 * m['range']
            target_price = c.low - 1.5 * m['range']
            matches.append(PatternMatch(
                pattern_name=SHOOTING_STAR,
                pattern_type=PatternType.CANDLESTICK,
                direction=PatternDirection.BEARISH,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=c.timestamp,
                candle_range=(idx, idx),
                timestamps=(c.timestamp, c.timestamp),
                status=PatternStatus.CONFIRMED,
                key_levels={
                    'stop_loss': stop_loss,
                    'target_price': target_price,
                    'high': c.high,
                    'breakout_price': c.low
                },
                confidence_score=min(0.85, 0.5 + (m['upper_wick'] / m['range']) * 0.4),
                supporting_evidence={
                    'upper_wick_ratio': round(m['upper_wick_ratio'], 4),
                    'body_ratio': round(m['body_ratio'], 4),
                    'prior_trend': 'uptrend'
                },
                detection_method="rule_based_candlestick",
            ))

        # 4. Bullish & Bearish Engulfing (2 candles: idx-1, idx)
        prev_c = candles[idx - 1]
        prev_m = self._candle_metrics(prev_c)

        if (
            prev_m['is_bearish']
            and m['is_bullish']
            and c.open <= prev_c.close * 1.002
            and c.close >= prev_c.open * 0.998
            and m['body'] > prev_m['body']
            and self._is_downtrend(candles, idx - 1, lookback=3)
        ):
            stop_loss = min(c.low, prev_c.low) - 0.2 * m['range']
            target_price = c.close + 1.5 * (c.close - stop_loss)
            matches.append(PatternMatch(
                pattern_name=BULLISH_ENGULFING,
                pattern_type=PatternType.CANDLESTICK,
                direction=PatternDirection.BULLISH,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=c.timestamp,
                candle_range=(idx - 1, idx),
                timestamps=(prev_c.timestamp, c.timestamp),
                status=PatternStatus.CONFIRMED,
                key_levels={
                    'stop_loss': stop_loss,
                    'target_price': target_price,
                    'breakout_price': c.close,
                    'pattern_low': min(c.low, prev_c.low)
                },
                confidence_score=0.75,
                supporting_evidence={
                    'engulf_ratio': round(m['body'] / max(prev_m['body'], 1e-8), 2),
                    'prior_trend': 'downtrend'
                },
                detection_method="rule_based_candlestick",
            ))

        elif (
            prev_m['is_bullish']
            and m['is_bearish']
            and c.open >= prev_c.close * 0.998
            and c.close <= prev_c.open * 1.002
            and m['body'] > prev_m['body']
            and self._is_uptrend(candles, idx - 1, lookback=3)
        ):
            stop_loss = max(c.high, prev_c.high) + 0.2 * m['range']
            target_price = c.close - 1.5 * (stop_loss - c.close)
            matches.append(PatternMatch(
                pattern_name=BEARISH_ENGULFING,
                pattern_type=PatternType.CANDLESTICK,
                direction=PatternDirection.BEARISH,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=c.timestamp,
                candle_range=(idx - 1, idx),
                timestamps=(prev_c.timestamp, c.timestamp),
                status=PatternStatus.CONFIRMED,
                key_levels={
                    'stop_loss': stop_loss,
                    'target_price': target_price,
                    'breakout_price': c.close,
                    'pattern_high': max(c.high, prev_c.high)
                },
                confidence_score=0.75,
                supporting_evidence={
                    'engulf_ratio': round(m['body'] / max(prev_m['body'], 1e-8), 2),
                    'prior_trend': 'uptrend'
                },
                detection_method="rule_based_candlestick",
            ))

        # 5. Morning Star & Evening Star (3 candles: idx-2, idx-1, idx)
        if idx >= 3:
            c1 = candles[idx - 2]
            c2 = candles[idx - 1]
            c3 = candles[idx]
            m1 = self._candle_metrics(c1)
            m2 = self._candle_metrics(c2)
            m3 = self._candle_metrics(c3)

            # Morning Star: Bearish C1, Small C2, Bullish C3 penetrating > 50% into C1 body
            if (
                m1['is_bearish'] and m1['body_ratio'] >= 0.45
                and m2['body_ratio'] <= 0.35
                and m3['is_bullish'] and m3['body_ratio'] >= 0.45
                and c3.close >= c1.open - (c1.open - c1.close) * 0.5
                and self._is_downtrend(candles, idx - 2, lookback=3)
            ):
                pattern_low = min(c1.low, c2.low, c3.low)
                stop_loss = pattern_low - 0.2 * m3['range']
                target_price = c3.close + 1.5 * (c3.close - stop_loss)
                matches.append(PatternMatch(
                    pattern_name=MORNING_STAR,
                    pattern_type=PatternType.CANDLESTICK,
                    direction=PatternDirection.BULLISH,
                    symbol=symbol,
                    timeframe=timeframe,
                    detection_timestamp=c3.timestamp,
                    candle_range=(idx - 2, idx),
                    timestamps=(c1.timestamp, c3.timestamp),
                    status=PatternStatus.CONFIRMED,
                    key_levels={
                        'stop_loss': stop_loss,
                        'target_price': target_price,
                        'breakout_price': c3.close,
                        'pattern_low': pattern_low
                    },
                    confidence_score=0.80,
                    supporting_evidence={
                        'star_body_ratio': round(m2['body_ratio'], 3),
                        'c3_penetration_pct': round((c3.close - c1.close) / max(m1['body'], 1e-8) * 100, 1),
                        'prior_trend': 'downtrend'
                    },
                    detection_method="rule_based_candlestick",
                ))

            # Evening Star: Bullish C1, Small C2, Bearish C3 penetrating > 50% into C1 body
            elif (
                m1['is_bullish'] and m1['body_ratio'] >= 0.45
                and m2['body_ratio'] <= 0.35
                and m3['is_bearish'] and m3['body_ratio'] >= 0.45
                and c3.close <= c1.close - (c1.close - c1.open) * 0.5
                and self._is_uptrend(candles, idx - 2, lookback=3)
            ):
                pattern_high = max(c1.high, c2.high, c3.high)
                stop_loss = pattern_high + 0.2 * m3['range']
                target_price = c3.close - 1.5 * (stop_loss - c3.close)
                matches.append(PatternMatch(
                    pattern_name=EVENING_STAR,
                    pattern_type=PatternType.CANDLESTICK,
                    direction=PatternDirection.BEARISH,
                    symbol=symbol,
                    timeframe=timeframe,
                    detection_timestamp=c3.timestamp,
                    candle_range=(idx - 2, idx),
                    timestamps=(c1.timestamp, c3.timestamp),
                    status=PatternStatus.CONFIRMED,
                    key_levels={
                        'stop_loss': stop_loss,
                        'target_price': target_price,
                        'breakout_price': c3.close,
                        'pattern_high': pattern_high
                    },
                    confidence_score=0.80,
                    supporting_evidence={
                        'star_body_ratio': round(m2['body_ratio'], 3),
                        'c3_penetration_pct': round((c1.close - c3.close) / max(m1['body'], 1e-8) * 100, 1),
                        'prior_trend': 'uptrend'
                    },
                    detection_method="rule_based_candlestick",
                ))

        return matches

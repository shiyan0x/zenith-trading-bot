"""
pattern_detector.py — Unified Chart Pattern Detection Coordinator.

Combines geometric detectors (reversals, continuations, triangles) and
candlestick detectors into a single deterministic, lookahead-free pipeline.
"""

from typing import List, Optional, Dict
from src.core.market_feed import Candle
from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternStatus,
    ALL_GEOMETRIC_PATTERNS,
    ALL_CANDLESTICK_PATTERNS,
)
from src.patterns.geometric_detector import GeometricPatternDetector
from src.patterns.candlestick_detector import CandlestickDetector
from src.patterns.swing_detector import SwingDetector


class PatternDetector:
    """
    Coordinates chart pattern recognition across both geometric formations
    and single/multi-bar candlestick structures.
    """

    def __init__(
        self,
        geometric_detector: Optional[GeometricPatternDetector] = None,
        candlestick_detector: Optional[CandlestickDetector] = None,
        min_confidence: float = 0.50,
        swing_left: int = 3,
        swing_right: int = 2,
    ):
        swing_detector = SwingDetector(left_bars=swing_left, right_bars=swing_right)
        self.geometric_detector = geometric_detector or GeometricPatternDetector(swing_detector=swing_detector)
        self.candlestick_detector = candlestick_detector or CandlestickDetector()
        self.min_confidence = min_confidence

    def detect_at_index(
        self,
        candles: List[Candle],
        idx: int,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
    ) -> List[PatternMatch]:
        """
        Run all detectors on closed candles strictly up to index `idx`.
        Zero lookahead bias guaranteed.
        """
        if idx < 3 or idx >= len(candles):
            return []

        # Only evaluate closed candles
        if not candles[idx].is_closed:
            return []

        results: List[PatternMatch] = []

        # 1. Candlestick patterns at current bar
        cs_matches = self.candlestick_detector.detect_at_index(
            candles, idx, symbol=symbol, timeframe=timeframe
        )
        results.extend(cs_matches)

        # 2. Geometric patterns spanning recent bars
        geo_matches = self.geometric_detector.detect_at_index(
            candles, idx, symbol=symbol, timeframe=timeframe
        )
        results.extend(geo_matches)

        # Filter by minimum confidence score
        filtered = [p for p in results if p.confidence_score >= self.min_confidence]

        # Prioritize confirmed patterns, then highest confidence
        filtered.sort(
            key=lambda p: (1 if p.status == PatternStatus.CONFIRMED else 0, p.confidence_score),
            reverse=True
        )
        return filtered

    def scan_historical(
        self,
        candles: List[Candle],
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        start_idx: int = 20,
    ) -> List[PatternMatch]:
        """
        Walks forward candle by candle and aggregates all pattern events.
        Simulates real-time arrival of bars without look-ahead.
        """
        all_matches: List[PatternMatch] = []
        for i in range(max(start_idx, 20), len(candles)):
            if not candles[i].is_closed:
                continue
            matches = self.detect_at_index(candles, i, symbol=symbol, timeframe=timeframe)
            all_matches.extend(matches)
        return all_matches

    def detect_all(
        self,
        candles: List[Candle],
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
    ) -> List[PatternMatch]:
        """Detect patterns present at the latest closed candle in the provided series."""
        if not candles:
            return []
        return self.detect_at_index(candles, len(candles) - 1, symbol=symbol, timeframe=timeframe)


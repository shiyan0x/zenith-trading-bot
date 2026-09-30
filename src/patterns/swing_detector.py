"""
swing_detector.py — Deterministic Swing High and Swing Low detection.

Identifies local price extrema (peaks and troughs) with zero lookahead bias.
A swing high at bar i is confirmed only after `right_bars` have closed after it:
    High[i] > max(High[i-left_bars : i]) and High[i] >= max(High[i+1 : i+right_bars+1])

A swing low at bar i is confirmed only after `right_bars` have closed after it:
    Low[i] < min(Low[i-left_bars : i]) and Low[i] <= min(Low[i+1 : i+right_bars+1])
"""

from typing import List, Optional
from src.core.market_feed import Candle
from src.patterns.pattern_definitions import SwingPoint


class SwingDetector:
    """
    Detects swing highs and swing lows in historical or streaming candles.
    """

    def __init__(self, left_bars: int = 4, right_bars: int = 3):
        self.left_bars = left_bars
        self.right_bars = right_bars

    def find_swings(self, candles: List[Candle], end_idx: Optional[int] = None) -> List[SwingPoint]:
        """
        Find all confirmed swing points up to end_idx.
        If end_idx is None, searches up to len(candles) - 1.

        Zero lookahead guarantee:
        Any swing point at index i will only be returned if i + self.right_bars <= end_idx.
        """
        if end_idx is None:
            end_idx = len(candles) - 1

        if len(candles) < self.left_bars + self.right_bars + 1:
            return []

        swings: List[SwingPoint] = []
        max_valid_pivot_idx = end_idx - self.right_bars

        for i in range(self.left_bars, max_valid_pivot_idx + 1):
            curr_high = candles[i].high
            curr_low = candles[i].low

            # Check for swing high
            is_swing_high = True
            for left in range(i - self.left_bars, i):
                if candles[left].high >= curr_high:
                    is_swing_high = False
                    break
            if is_swing_high:
                for right in range(i + 1, i + self.right_bars + 1):
                    if candles[right].high > curr_high:
                        is_swing_high = False
                        break

            # Check for swing low
            is_swing_low = True
            for left in range(i - self.left_bars, i):
                if candles[left].low <= curr_low:
                    is_swing_low = False
                    break
            if is_swing_low:
                for right in range(i + 1, i + self.right_bars + 1):
                    if candles[right].low < curr_low:
                        is_swing_low = False
                        break

            if is_swing_high:
                swings.append(SwingPoint(
                    index=i,
                    timestamp=candles[i].timestamp,
                    price=curr_high,
                    is_high=True,
                    bar_range=self.left_bars
                ))
            elif is_swing_low:
                swings.append(SwingPoint(
                    index=i,
                    timestamp=candles[i].timestamp,
                    price=curr_low,
                    is_high=False,
                    bar_range=self.left_bars
                ))

        return swings

    def get_peaks_and_troughs(
        self, candles: List[Candle], end_idx: Optional[int] = None
    ) -> tuple[List[SwingPoint], List[SwingPoint]]:
        """Convenience method returning separate lists of peaks (highs) and troughs (lows)."""
        swings = self.find_swings(candles, end_idx)
        peaks = [s for s in swings if s.is_high]
        troughs = [s for s in swings if not s.is_high]
        return peaks, troughs

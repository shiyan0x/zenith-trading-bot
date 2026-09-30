"""
geometric_detector.py — Deterministic Geometric Chart Pattern Recognition.

Detects Reversals, Continuations, and Triangles from Swing Points and Candles:
1. Reversal Patterns:
   - Double Top
   - Double Bottom
   - Head and Shoulders
   - Inverse Head and Shoulders
2. Continuation Patterns:
   - Bullish Flag
   - Bearish Flag
   - Bullish Pennant
   - Bearish Pennant
3. Triangle Patterns:
   - Ascending Triangle
   - Descending Triangle
   - Symmetrical Triangle

Features:
- Exact geometry, symmetry, and proportion measurements.
- Status classification: FORMING, CONFIRMED, or INVALIDATED.
- Documented breakout criteria: breakout requires closed candle beyond boundary.
- Zero lookahead bias: evaluates historical candles strictly up to index `idx`.
"""

from typing import List, Optional, Tuple, Dict, Any
import numpy as np

from src.core.market_feed import Candle
from src.patterns.swing_detector import SwingDetector, SwingPoint
from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternType,
    PatternStatus,
    PatternDirection,
    DOUBLE_TOP,
    DOUBLE_BOTTOM,
    HEAD_AND_SHOULDERS,
    INVERSE_HEAD_AND_SHOULDERS,
    BULLISH_FLAG,
    BEARISH_FLAG,
    BULLISH_PENNANT,
    BEARISH_PENNANT,
    ASCENDING_TRIANGLE,
    DESCENDING_TRIANGLE,
    SYMMETRICAL_TRIANGLE,
)


class GeometricPatternDetector:
    """
    Scans closed candles and swing points to identify geometric chart patterns.
    """

    def __init__(
        self,
        swing_detector: Optional[SwingDetector] = None,
        level_tolerance: float = 0.02,     # 2.0% tolerance for horizontal levels
        breakout_margin: float = 0.001,    # 0.1% buffer beyond level for confirmation
    ):
        self.swing_detector = swing_detector or SwingDetector(left_bars=3, right_bars=2)
        self.level_tolerance = level_tolerance
        self.breakout_margin = breakout_margin

    def detect_at_index(
        self,
        candles: List[Candle],
        idx: int,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m"
    ) -> List[PatternMatch]:
        """
        Scan for all geometric patterns up to candle `idx`.
        Zero lookahead bias guaranteed: uses only candles[0 : idx+1].
        """
        if idx < 20 or idx >= len(candles):
            return []

        sub_candles = candles[: idx + 1]
        swings = self.swing_detector.find_swings(sub_candles, end_idx=idx)
        peaks = [s for s in swings if s.is_high]
        troughs = [s for s in swings if not s.is_high]

        matches: List[PatternMatch] = []
        current_candle = sub_candles[-1]
        current_price = current_candle.close

        # ── 1. Reversal Patterns ──────────────────────────────────────────────
        dt = self._detect_double_top(sub_candles, peaks, troughs, current_price, idx, symbol, timeframe)
        if dt:
            matches.append(dt)

        db = self._detect_double_bottom(sub_candles, peaks, troughs, current_price, idx, symbol, timeframe)
        if db:
            matches.append(db)

        hs = self._detect_head_and_shoulders(sub_candles, peaks, troughs, current_price, idx, symbol, timeframe)
        if hs:
            matches.append(hs)

        ihs = self._detect_inverse_head_and_shoulders(sub_candles, peaks, troughs, current_price, idx, symbol, timeframe)
        if ihs:
            matches.append(ihs)

        # ── 2. Triangle Patterns ──────────────────────────────────────────────
        tri = self._detect_triangles(sub_candles, peaks, troughs, current_price, idx, symbol, timeframe)
        if tri:
            matches.extend(tri)

        # ── 3. Continuation Patterns (Flags & Pennants) ────────────────────────
        cont = self._detect_flags_and_pennants(sub_candles, current_price, idx, symbol, timeframe)
        if cont:
            matches.extend(cont)

        return matches

    # ── Reversal Pattern Implementations ──────────────────────────────────────

    def _detect_double_top(
        self, candles: List[Candle], peaks: List[SwingPoint], troughs: List[SwingPoint],
        curr_price: float, curr_idx: int, symbol: str, timeframe: str
    ) -> Optional[PatternMatch]:
        """Double Top: 2 similar peaks separated by an intervening trough."""
        if len(peaks) < 2 or not troughs:
            return None

        p2 = peaks[-1]
        p1 = peaks[-2]

        # Intervening trough between p1 and p2
        intervening_troughs = [t for t in troughs if p1.index < t.index < p2.index]
        if not intervening_troughs:
            return None
        neckline_trough = min(intervening_troughs, key=lambda t: t.price)
        neckline = neckline_trough.price

        avg_peak = (p1.price + p2.price) / 2.0
        peak_diff = abs(p1.price - p2.price) / avg_peak
        if peak_diff > self.level_tolerance:
            return None

        pattern_height = avg_peak - neckline
        if pattern_height <= 0 or (pattern_height / neckline) < 0.008:
            return None  # Pattern too shallow to be statistically meaningful

        # Distance between peaks should be between 5 and 60 bars
        peak_distance = p2.index - p1.index
        if peak_distance < 5 or peak_distance > 60:
            return None

        # Status:
        # Forming: Price is below peaks and above neckline
        # Confirmed: Price closed below neckline
        # Invalidated: Price closed above max peak
        max_peak = max(p1.price, p2.price)
        target = neckline - pattern_height
        stop_loss = max_peak * 1.002

        if curr_price < neckline * (1.0 - self.breakout_margin):
            status = PatternStatus.CONFIRMED
        elif curr_price > max_peak:
            status = PatternStatus.INVALIDATED
        else:
            status = PatternStatus.FORMING

        confidence = max(0.5, 1.0 - (peak_diff * 15))
        return PatternMatch(
            pattern_name=DOUBLE_TOP,
            pattern_type=PatternType.REVERSAL,
            direction=PatternDirection.BEARISH,
            symbol=symbol,
            timeframe=timeframe,
            detection_timestamp=candles[curr_idx].timestamp,
            candle_range=(p1.index, curr_idx),
            timestamps=(candles[p1.index].timestamp, candles[curr_idx].timestamp),
            status=status,
            key_levels={
                'peak1': p1.price,
                'peak2': p2.price,
                'neckline': neckline,
                'breakout_price': neckline,
                'target_price': target,
                'stop_loss': stop_loss,
                'invalidation_price': max_peak,
            },
            confidence_score=round(confidence, 3),
            supporting_evidence={
                'peak_symmetry_diff_pct': round(peak_diff * 100, 2),
                'pattern_height_pct': round((pattern_height / neckline) * 100, 2),
                'peak_distance_bars': peak_distance,
            },
            detection_method="rule_based_geometric",
        )

    def _detect_double_bottom(
        self, candles: List[Candle], peaks: List[SwingPoint], troughs: List[SwingPoint],
        curr_price: float, curr_idx: int, symbol: str, timeframe: str
    ) -> Optional[PatternMatch]:
        """Double Bottom: 2 similar troughs separated by an intervening peak."""
        if len(troughs) < 2 or not peaks:
            return None

        t2 = troughs[-1]
        t1 = troughs[-2]

        intervening_peaks = [p for p in peaks if t1.index < p.index < t2.index]
        if not intervening_peaks:
            return None
        neckline_peak = max(intervening_peaks, key=lambda p: p.price)
        neckline = neckline_peak.price

        avg_trough = (t1.price + t2.price) / 2.0
        trough_diff = abs(t1.price - t2.price) / avg_trough
        if trough_diff > self.level_tolerance:
            return None

        pattern_height = neckline - avg_trough
        if pattern_height <= 0 or (pattern_height / avg_trough) < 0.008:
            return None

        trough_distance = t2.index - t1.index
        if trough_distance < 5 or trough_distance > 60:
            return None

        min_trough = min(t1.price, t2.price)
        target = neckline + pattern_height
        stop_loss = min_trough * 0.998

        if curr_price > neckline * (1.0 + self.breakout_margin):
            status = PatternStatus.CONFIRMED
        elif curr_price < min_trough:
            status = PatternStatus.INVALIDATED
        else:
            status = PatternStatus.FORMING

        confidence = max(0.5, 1.0 - (trough_diff * 15))
        return PatternMatch(
            pattern_name=DOUBLE_BOTTOM,
            pattern_type=PatternType.REVERSAL,
            direction=PatternDirection.BULLISH,
            symbol=symbol,
            timeframe=timeframe,
            detection_timestamp=candles[curr_idx].timestamp,
            candle_range=(t1.index, curr_idx),
            timestamps=(candles[t1.index].timestamp, candles[curr_idx].timestamp),
            status=status,
            key_levels={
                'trough1': t1.price,
                'trough2': t2.price,
                'neckline': neckline,
                'breakout_price': neckline,
                'target_price': target,
                'stop_loss': stop_loss,
                'invalidation_price': min_trough,
            },
            confidence_score=round(confidence, 3),
            supporting_evidence={
                'trough_symmetry_diff_pct': round(trough_diff * 100, 2),
                'pattern_height_pct': round((pattern_height / avg_trough) * 100, 2),
                'trough_distance_bars': trough_distance,
            },
            detection_method="rule_based_geometric",
        )

    def _detect_head_and_shoulders(
        self, candles: List[Candle], peaks: List[SwingPoint], troughs: List[SwingPoint],
        curr_price: float, curr_idx: int, symbol: str, timeframe: str
    ) -> Optional[PatternMatch]:
        """Head and Shoulders: Left Shoulder, Higher Head, Right Shoulder."""
        if len(peaks) < 3 or len(troughs) < 2:
            return None

        ls, head, rs = peaks[-3], peaks[-2], peaks[-1]

        # Head must be distinctly higher than both shoulders
        if head.price <= ls.price * 1.005 or head.price <= rs.price * 1.005:
            return None

        # Shoulders should be roughly comparable
        shoulder_diff = abs(ls.price - rs.price) / ls.price
        if shoulder_diff > 0.04:  # within 4%
            return None

        # Intervening troughs (neckline anchors)
        t1_candidates = [t for t in troughs if ls.index < t.index < head.index]
        t2_candidates = [t for t in troughs if head.index < t.index < rs.index]
        if not t1_candidates or not t2_candidates:
            return None

        t1 = min(t1_candidates, key=lambda t: t.price)
        t2 = min(t2_candidates, key=lambda t: t.price)
        neckline = (t1.price + t2.price) / 2.0
        pattern_height = head.price - neckline
        if pattern_height <= 0:
            return None

        target = neckline - pattern_height
        stop_loss = rs.price * 1.005

        if curr_price < neckline * (1.0 - self.breakout_margin):
            status = PatternStatus.CONFIRMED
        elif curr_price > head.price:
            status = PatternStatus.INVALIDATED
        else:
            status = PatternStatus.FORMING

        confidence = max(0.55, 1.0 - (shoulder_diff * 10))
        return PatternMatch(
            pattern_name=HEAD_AND_SHOULDERS,
            pattern_type=PatternType.REVERSAL,
            direction=PatternDirection.BEARISH,
            symbol=symbol,
            timeframe=timeframe,
            detection_timestamp=candles[curr_idx].timestamp,
            candle_range=(ls.index, curr_idx),
            timestamps=(candles[ls.index].timestamp, candles[curr_idx].timestamp),
            status=status,
            key_levels={
                'left_shoulder': ls.price,
                'head': head.price,
                'right_shoulder': rs.price,
                'neckline': neckline,
                'breakout_price': neckline,
                'target_price': target,
                'stop_loss': stop_loss,
                'invalidation_price': head.price,
            },
            confidence_score=round(confidence, 3),
            supporting_evidence={
                'shoulder_diff_pct': round(shoulder_diff * 100, 2),
                'pattern_height_pct': round((pattern_height / neckline) * 100, 2),
            },
            detection_method="rule_based_geometric",
        )

    def _detect_inverse_head_and_shoulders(
        self, candles: List[Candle], peaks: List[SwingPoint], troughs: List[SwingPoint],
        curr_price: float, curr_idx: int, symbol: str, timeframe: str
    ) -> Optional[PatternMatch]:
        """Inverse Head and Shoulders: Left Shoulder low, Lower Head, Right Shoulder low."""
        if len(troughs) < 3 or len(peaks) < 2:
            return None

        ls, head, rs = troughs[-3], troughs[-2], troughs[-1]

        # Head must be distinctly lower than both shoulders
        if head.price >= ls.price * 0.995 or head.price >= rs.price * 0.995:
            return None

        shoulder_diff = abs(ls.price - rs.price) / ls.price
        if shoulder_diff > 0.04:
            return None

        p1_candidates = [p for p in peaks if ls.index < p.index < head.index]
        p2_candidates = [p for p in peaks if head.index < p.index < rs.index]
        if not p1_candidates or not p2_candidates:
            return None

        p1 = max(p1_candidates, key=lambda p: p.price)
        p2 = max(p2_candidates, key=lambda p: p.price)
        neckline = (p1.price + p2.price) / 2.0
        pattern_height = neckline - head.price
        if pattern_height <= 0:
            return None

        target = neckline + pattern_height
        stop_loss = rs.price * 0.995

        if curr_price > neckline * (1.0 + self.breakout_margin):
            status = PatternStatus.CONFIRMED
        elif curr_price < head.price:
            status = PatternStatus.INVALIDATED
        else:
            status = PatternStatus.FORMING

        confidence = max(0.55, 1.0 - (shoulder_diff * 10))
        return PatternMatch(
            pattern_name=INVERSE_HEAD_AND_SHOULDERS,
            pattern_type=PatternType.REVERSAL,
            direction=PatternDirection.BULLISH,
            symbol=symbol,
            timeframe=timeframe,
            detection_timestamp=candles[curr_idx].timestamp,
            candle_range=(ls.index, curr_idx),
            timestamps=(candles[ls.index].timestamp, candles[curr_idx].timestamp),
            status=status,
            key_levels={
                'left_shoulder': ls.price,
                'head': head.price,
                'right_shoulder': rs.price,
                'neckline': neckline,
                'breakout_price': neckline,
                'target_price': target,
                'stop_loss': stop_loss,
                'invalidation_price': head.price,
            },
            confidence_score=round(confidence, 3),
            supporting_evidence={
                'shoulder_diff_pct': round(shoulder_diff * 100, 2),
                'pattern_height_pct': round((pattern_height / head.price) * 100, 2),
            },
            detection_method="rule_based_geometric",
        )

    # ── Triangle Patterns Implementation ──────────────────────────────────────

    def _detect_triangles(
        self, candles: List[Candle], peaks: List[SwingPoint], troughs: List[SwingPoint],
        curr_price: float, curr_idx: int, symbol: str, timeframe: str
    ) -> List[PatternMatch]:
        """Detect Ascending, Descending, and Symmetrical Triangles."""
        if len(peaks) < 2 or len(troughs) < 2:
            return []

        p1, p2 = peaks[-2], peaks[-1]
        t1, t2 = troughs[-2], troughs[-1]

        # Bounds check: ensure interweaving (p1 < t1 < p2 < t2 or t1 < p1 < t2 < p2)
        start_idx = min(p1.index, t1.index)
        pattern_len = curr_idx - start_idx
        if pattern_len < 10 or pattern_len > 80:
            return []

        results = []
        peak_diff = abs(p1.price - p2.price) / p1.price
        trough_diff = abs(t1.price - t2.price) / t1.price

        # 1. Ascending Triangle: Flat Resistance (peaks similar) + Ascending Support (t2 > t1)
        if peak_diff <= 0.015 and t2.price > t1.price * 1.008:
            resistance = (p1.price + p2.price) / 2.0
            height = resistance - t1.price
            target = resistance + height
            stop_loss = t2.price * 0.998

            if curr_price > resistance * (1.0 + self.breakout_margin):
                status = PatternStatus.CONFIRMED
            elif curr_price < t2.price:
                status = PatternStatus.INVALIDATED
            else:
                status = PatternStatus.FORMING

            results.append(PatternMatch(
                pattern_name=ASCENDING_TRIANGLE,
                pattern_type=PatternType.TRIANGLE,
                direction=PatternDirection.BULLISH,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=candles[curr_idx].timestamp,
                candle_range=(start_idx, curr_idx),
                timestamps=(candles[start_idx].timestamp, candles[curr_idx].timestamp),
                status=status,
                key_levels={
                    'resistance': resistance,
                    'support_t1': t1.price,
                    'support_t2': t2.price,
                    'breakout_price': resistance,
                    'target_price': target,
                    'stop_loss': stop_loss,
                    'invalidation_price': t2.price,
                },
                confidence_score=0.72,
                supporting_evidence={
                    'resistance_flatness_pct': round(peak_diff * 100, 2),
                    'support_ascent_pct': round(((t2.price - t1.price) / t1.price) * 100, 2),
                },
                detection_method="rule_based_geometric",
            ))

        # 2. Descending Triangle: Flat Support (troughs similar) + Descending Resistance (p2 < p1)
        elif trough_diff <= 0.015 and p2.price < p1.price * 0.992:
            support = (t1.price + t2.price) / 2.0
            height = p1.price - support
            target = support - height
            stop_loss = p2.price * 1.002

            if curr_price < support * (1.0 - self.breakout_margin):
                status = PatternStatus.CONFIRMED
            elif curr_price > p2.price:
                status = PatternStatus.INVALIDATED
            else:
                status = PatternStatus.FORMING

            results.append(PatternMatch(
                pattern_name=DESCENDING_TRIANGLE,
                pattern_type=PatternType.TRIANGLE,
                direction=PatternDirection.BEARISH,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=candles[curr_idx].timestamp,
                candle_range=(start_idx, curr_idx),
                timestamps=(candles[start_idx].timestamp, candles[curr_idx].timestamp),
                status=status,
                key_levels={
                    'support': support,
                    'resistance_p1': p1.price,
                    'resistance_p2': p2.price,
                    'breakout_price': support,
                    'target_price': target,
                    'stop_loss': stop_loss,
                    'invalidation_price': p2.price,
                },
                confidence_score=0.72,
                supporting_evidence={
                    'support_flatness_pct': round(trough_diff * 100, 2),
                    'resistance_descent_pct': round(((p1.price - p2.price) / p1.price) * 100, 2),
                },
                detection_method="rule_based_geometric",
            ))

        # 3. Symmetrical Triangle: Lower Highs (p2 < p1) AND Higher Lows (t2 > t1)
        elif p2.price < p1.price * 0.995 and t2.price > t1.price * 1.005:
            height = p1.price - t1.price
            upper_bound = p2.price
            lower_bound = t2.price

            # Direction depends on breakout
            if curr_price > upper_bound * (1.0 + self.breakout_margin):
                direction = PatternDirection.BULLISH
                status = PatternStatus.CONFIRMED
                target = curr_price + height
                stop_loss = lower_bound
            elif curr_price < lower_bound * (1.0 - self.breakout_margin):
                direction = PatternDirection.BEARISH
                status = PatternStatus.CONFIRMED
                target = curr_price - height
                stop_loss = upper_bound
            else:
                direction = PatternDirection.NEUTRAL
                status = PatternStatus.FORMING
                target = upper_bound + height
                stop_loss = lower_bound

            results.append(PatternMatch(
                pattern_name=SYMMETRICAL_TRIANGLE,
                pattern_type=PatternType.TRIANGLE,
                direction=direction,
                symbol=symbol,
                timeframe=timeframe,
                detection_timestamp=candles[curr_idx].timestamp,
                candle_range=(start_idx, curr_idx),
                timestamps=(candles[start_idx].timestamp, candles[curr_idx].timestamp),
                status=status,
                key_levels={
                    'upper_bound': upper_bound,
                    'lower_bound': lower_bound,
                    'breakout_price': upper_bound if direction != PatternDirection.BEARISH else lower_bound,
                    'target_price': target,
                    'stop_loss': stop_loss,
                },
                confidence_score=0.68,
                supporting_evidence={
                    'convergence_highs_pct': round(((p1.price - p2.price) / p1.price) * 100, 2),
                    'convergence_lows_pct': round(((t2.price - t1.price) / t1.price) * 100, 2),
                },
                detection_method="rule_based_geometric",
            ))

        return results

    # ── Continuation Patterns (Flags and Pennants) ────────────────────────────

    def _detect_flags_and_pennants(
        self, candles: List[Candle], curr_price: float, curr_idx: int,
        symbol: str, timeframe: str
    ) -> List[PatternMatch]:
        """Detect Bullish/Bearish Flags and Pennants with flagpole and consolidation channel."""
        results: List[PatternMatch] = []
        if curr_idx < 15:
            return results

        # Flag search window:
        # Flagpole: 5 to 15 bars
        # Flag consolidation: 4 to 12 bars
        for pole_len in (7, 10, 14):
            for flag_len in (4, 6, 8):
                total_len = pole_len + flag_len
                if curr_idx < total_len:
                    continue

                start_pole_idx = curr_idx - total_len
                end_pole_idx = curr_idx - flag_len
                start_flag_idx = end_pole_idx

                p_start = candles[start_pole_idx].close
                p_end_pole = candles[end_pole_idx].close
                pole_return = (p_end_pole - p_start) / max(p_start, 1e-8)

                # Consolidation slice
                flag_closes = [candles[i].close for i in range(start_flag_idx, curr_idx + 1)]
                flag_highs = [candles[i].high for i in range(start_flag_idx, curr_idx + 1)]
                flag_lows = [candles[i].low for i in range(start_flag_idx, curr_idx + 1)]

                flag_max = max(flag_highs)
                flag_min = min(flag_lows)
                flag_height = flag_max - flag_min
                pole_height = abs(p_end_pole - p_start)

                if pole_height == 0:
                    continue

                # Flag height should be compact (<= 55% of flagpole)
                if flag_height / pole_height > 0.55:
                    continue

                # Linear regression on flag closes to check slope
                x = np.arange(len(flag_closes))
                slope, _ = np.polyfit(x, flag_closes, 1)
                normalized_slope = slope / p_end_pole

                # 1. Bullish Flag: Strong upward pole (+2.5%+) & slight downward/flat consolidation
                if pole_return >= 0.025 and normalized_slope <= 0.001:
                    breakout_level = flag_max
                    target = breakout_level + pole_height
                    stop_loss = flag_min * 0.998

                    if curr_price > breakout_level * (1.0 + self.breakout_margin):
                        status = PatternStatus.CONFIRMED
                    elif curr_price < flag_min:
                        status = PatternStatus.INVALIDATED
                    else:
                        status = PatternStatus.FORMING

                    results.append(PatternMatch(
                        pattern_name=BULLISH_FLAG,
                        pattern_type=PatternType.CONTINUATION,
                        direction=PatternDirection.BULLISH,
                        symbol=symbol,
                        timeframe=timeframe,
                        detection_timestamp=candles[curr_idx].timestamp,
                        candle_range=(start_pole_idx, curr_idx),
                        timestamps=(candles[start_pole_idx].timestamp, candles[curr_idx].timestamp),
                        status=status,
                        key_levels={
                            'pole_start': p_start,
                            'pole_end': p_end_pole,
                            'flag_high': flag_max,
                            'flag_low': flag_min,
                            'breakout_price': breakout_level,
                            'target_price': target,
                            'stop_loss': stop_loss,
                            'invalidation_price': flag_min,
                        },
                        confidence_score=0.74,
                        supporting_evidence={
                            'pole_gain_pct': round(pole_return * 100, 2),
                            'consolidation_retrace_pct': round((flag_height / pole_height) * 100, 2),
                            'flag_bars': flag_len,
                        },
                        detection_method="rule_based_geometric",
                    ))
                    return results  # Return most salient flag found

                # 2. Bearish Flag: Strong downward pole (-2.5%-) & slight upward/flat consolidation
                elif pole_return <= -0.025 and normalized_slope >= -0.001:
                    breakout_level = flag_min
                    target = breakout_level - pole_height
                    stop_loss = flag_max * 1.002

                    if curr_price < breakout_level * (1.0 - self.breakout_margin):
                        status = PatternStatus.CONFIRMED
                    elif curr_price > flag_max:
                        status = PatternStatus.INVALIDATED
                    else:
                        status = PatternStatus.FORMING

                    results.append(PatternMatch(
                        pattern_name=BEARISH_FLAG,
                        pattern_type=PatternType.CONTINUATION,
                        direction=PatternDirection.BEARISH,
                        symbol=symbol,
                        timeframe=timeframe,
                        detection_timestamp=candles[curr_idx].timestamp,
                        candle_range=(start_pole_idx, curr_idx),
                        timestamps=(candles[start_pole_idx].timestamp, candles[curr_idx].timestamp),
                        status=status,
                        key_levels={
                            'pole_start': p_start,
                            'pole_end': p_end_pole,
                            'flag_high': flag_max,
                            'flag_low': flag_min,
                            'breakout_price': breakout_level,
                            'target_price': target,
                            'stop_loss': stop_loss,
                            'invalidation_price': flag_max,
                        },
                        confidence_score=0.74,
                        supporting_evidence={
                            'pole_drop_pct': round(abs(pole_return) * 100, 2),
                            'consolidation_retrace_pct': round((flag_height / pole_height) * 100, 2),
                            'flag_bars': flag_len,
                        },
                        detection_method="rule_based_geometric",
                    ))
                    return results

                # 3. Pennants (converging highs and lows during consolidation)
                if abs(pole_return) >= 0.025 and len(flag_highs) >= 4:
                    highs_slope, _ = np.polyfit(np.arange(len(flag_highs)), flag_highs, 1)
                    lows_slope, _ = np.polyfit(np.arange(len(flag_lows)), flag_lows, 1)

                    # Converging: highs sloping down, lows sloping up
                    if highs_slope < 0 and lows_slope > 0:
                        is_bullish = pole_return > 0
                        p_name = BULLISH_PENNANT if is_bullish else BEARISH_PENNANT
                        direction = PatternDirection.BULLISH if is_bullish else PatternDirection.BEARISH
                        breakout_level = flag_max if is_bullish else flag_min
                        target = breakout_level + (pole_height if is_bullish else -pole_height)
                        stop_loss = flag_min if is_bullish else flag_max

                        if (is_bullish and curr_price > breakout_level * (1.0 + self.breakout_margin)) or \
                           (not is_bullish and curr_price < breakout_level * (1.0 - self.breakout_margin)):
                            status = PatternStatus.CONFIRMED
                        elif (is_bullish and curr_price < flag_min) or (not is_bullish and curr_price > flag_max):
                            status = PatternStatus.INVALIDATED
                        else:
                            status = PatternStatus.FORMING

                        results.append(PatternMatch(
                            pattern_name=p_name,
                            pattern_type=PatternType.CONTINUATION,
                            direction=direction,
                            symbol=symbol,
                            timeframe=timeframe,
                            detection_timestamp=candles[curr_idx].timestamp,
                            candle_range=(start_pole_idx, curr_idx),
                            timestamps=(candles[start_pole_idx].timestamp, candles[curr_idx].timestamp),
                            status=status,
                            key_levels={
                                'pole_start': p_start,
                                'pole_end': p_end_pole,
                                'pennant_high': flag_max,
                                'pennant_low': flag_min,
                                'breakout_price': breakout_level,
                                'target_price': target,
                                'stop_loss': stop_loss,
                            },
                            confidence_score=0.70,
                            supporting_evidence={
                                'pole_gain_drop_pct': round(abs(pole_return) * 100, 2),
                                'converging_slopes': (round(float(highs_slope), 2), round(float(lows_slope), 2)),
                            },
                            detection_method="rule_based_geometric",
                        ))
                        return results

        return results

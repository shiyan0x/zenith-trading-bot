"""
pattern_strategy.py — BaseStrategy Implementation Driven by Chart Patterns.

Allows backtesting and live paper trading on:
1. Pure chart pattern signals (e.g. Double Bottom, Bullish Flag, Hammer).
2. Combined pattern + technical indicator confluence (e.g. Pattern + RSI oversold + VWAP alignment).
"""

from typing import Optional, List, Dict, Any
import logging

from src.strategies.base_strategy import BaseStrategy
from src.core.market_feed import Candle
from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternType,
    PatternStatus,
    PatternDirection,
    DOUBLE_BOTTOM,
    DOUBLE_TOP,
    BULLISH_FLAG,
    BEARISH_FLAG,
    HAMMER,
    SHOOTING_STAR,
    BULLISH_ENGULFING,
    BEARISH_ENGULFING,
)
from src.patterns.pattern_detector import PatternDetector
from src.patterns.context_analyzer import MarketContextAnalyzer

logger = logging.getLogger(__name__)


class PatternStrategy(BaseStrategy):
    """
    Rule-based strategy executing on verified chart patterns with optional market context filters.
    """

    def __init__(self, name: str = "PatternStrategy", params: Optional[dict] = None):
        super().__init__(name, params)
        self.params = params or {}
        
        # Strategy settings
        self.allowed_patterns: List[str] = self.params.get('allowed_patterns', [
            DOUBLE_BOTTOM, BULLISH_FLAG, HAMMER, BULLISH_ENGULFING
        ])
        self.require_confirmation: bool = self.params.get('require_confirmation', True)
        self.min_confidence: float = float(self.params.get('min_confidence', 0.60))
        self.use_context_filter: bool = self.params.get('use_context_filter', True)
        self.min_confluence_score: float = float(self.params.get('min_confluence_score', 0.0))
        self.time_stop_bars: int = int(self.params.get('time_stop_bars', 25))
        self.rr_ratio: float = float(self.params.get('rr_ratio', 2.0))

        # Detectors
        self.detector = PatternDetector(min_confidence=self.min_confidence)
        self.context_analyzer = MarketContextAnalyzer()

        # State tracking
        self._bars_in_trade: int = 0
        self._pending_plan: Optional[dict] = None
        self._last_detected_pattern: Optional[PatternMatch] = None
        self._last_context: Optional[dict] = None
        self._detected_history: List[dict] = []

    def reset(self):
        super().reset()
        self._bars_in_trade = 0
        self._pending_plan = None
        self._last_detected_pattern = None
        self._last_context = None
        self._detected_history = []

    def get_trade_plan(self) -> Optional[dict]:
        return self._pending_plan

    def discard_pending_trade(self):
        self._pending_plan = None

    def should_enter(self) -> Optional[str]:
        """
        Evaluate if a qualifying chart pattern has occurred on the latest closed candle.
        """
        if not self.has_enough_data(25):
            return None

        curr_idx = len(self._candle_history) - 1
        matches = self.detector.detect_at_index(self._candle_history, curr_idx)
        if not matches:
            return None

        # Filter by allowed patterns and status
        valid_matches = []
        for m in matches:
            if m.pattern_name in self.allowed_patterns:
                if self.require_confirmation and m.status != PatternStatus.CONFIRMED:
                    continue
                valid_matches.append(m)

        if not valid_matches:
            return None

        best_pattern = valid_matches[0]
        context = self.context_analyzer.analyze(self._candle_history, curr_idx)

        # Context alignment check
        if self.use_context_filter:
            confluence = self.context_analyzer.evaluate_pattern_confluence(best_pattern, context)
            if confluence < self.min_confidence:
                return None

        current_candle = self._candle_history[-1]
        current_price = current_candle.close

        # Determine signal side
        if best_pattern.direction == PatternDirection.BULLISH:
            signal = "long"
            stop_loss = best_pattern.key_levels.get('stop_loss', current_price * 0.985)
            # Ensure stop loss is below current price
            if stop_loss >= current_price:
                stop_loss = current_price * 0.985
            risk = current_price - stop_loss
            take_profit = best_pattern.key_levels.get('target_price', current_price + risk * self.rr_ratio)
        elif best_pattern.direction == PatternDirection.BEARISH:
            signal = "short"
            stop_loss = best_pattern.key_levels.get('stop_loss', current_price * 1.015)
            # Ensure stop loss is above current price
            if stop_loss <= current_price:
                stop_loss = current_price * 1.015
            risk = stop_loss - current_price
            take_profit = best_pattern.key_levels.get('target_price', current_price - risk * self.rr_ratio)
        else:
            return None

        self._pending_plan = {
            'stop_loss': round(stop_loss, 2),
            'take_profit': round(take_profit, 2),
            'pattern': best_pattern.to_dict(),
            'context': context.to_dict(),
            'signal_price': current_price,
            'signal_time': current_candle.timestamp,
        }
        self._last_detected_pattern = best_pattern
        self._last_context = context.to_dict()
        self._detected_history.append(best_pattern.to_dict())
        self._bars_in_trade = 0

        return signal

    def should_exit(self, position_side: str) -> bool:
        """
        Check exit conditions: time stop or opposing confirmed pattern.
        """
        self._bars_in_trade += 1

        # Time stop
        if self._bars_in_trade >= self.time_stop_bars:
            return True

        # Opposing pattern exit
        curr_idx = len(self._candle_history) - 1
        matches = self.detector.detect_at_index(self._candle_history, curr_idx)
        for m in matches:
            if m.status == PatternStatus.CONFIRMED:
                if position_side == 'long' and m.direction == PatternDirection.BEARISH:
                    return True
                if position_side == 'short' and m.direction == PatternDirection.BULLISH:
                    return True

        return False

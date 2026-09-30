"""
outcome_evaluator.py — Empirical Historical Outcome Analytics for Chart Patterns.

Evaluates post-detection and post-confirmation price behavior across forward horizons:
- Maximum Favorable Excursion (MFE)
- Maximum Adverse Excursion (MAE)
- Breakout direction verification
- Stop-Loss / Take-Profit simulation with realistic execution costs
- Forward returns at N bars (5, 10, 20, 50)
"""

from typing import List, Optional, Dict, Any
from src.core.market_feed import Candle
from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternOutcome,
    PatternDirection,
    PatternStatus,
)


class PatternOutcomeEvaluator:
    """
    Evaluates historical forward outcomes for detected patterns.
    Strictly isolates future evaluation bars from detection logic.
    """

    def __init__(self, fee_pct: float = 0.001, slippage_bps: float = 10.0):
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_bps / 10000.0  # 10 bps = 0.0010 = 0.1%

    def evaluate_outcome(
        self,
        pattern: PatternMatch,
        candles: List[Candle],
        horizon_bars: int = 15,
        target_rr: float = 2.0,
    ) -> Optional[PatternOutcome]:
        """
        Evaluate what happened over the subsequent `horizon_bars` following pattern completion.
        Only valid if there are at least `horizon_bars` available after pattern.candle_range[1].
        """
        end_idx = pattern.candle_range[1]
        available_future_bars = len(candles) - 1 - end_idx
        if available_future_bars < 1:
            return None

        eval_horizon = min(horizon_bars, available_future_bars)
        future_slice = candles[end_idx + 1 : end_idx + 1 + eval_horizon]
        if not future_slice:
            return None

        entry_price = candles[end_idx].close
        is_bullish = (pattern.direction == PatternDirection.BULLISH)
        is_bearish = (pattern.direction == PatternDirection.BEARISH)

        # Stop loss and target price defaults from pattern or 2x ATR default
        key_levels = pattern.key_levels or {}
        stop_loss = key_levels.get('stop_loss')
        target_price = key_levels.get('target_price')

        if stop_loss is None:
            stop_loss = entry_price * 0.98 if is_bullish else entry_price * 1.02
        if target_price is None:
            risk_dist = abs(entry_price - stop_loss)
            target_price = entry_price + target_rr * risk_dist if is_bullish else entry_price - target_rr * risk_dist

        highest_price = max(c.high for c in future_slice)
        lowest_price = min(c.low for c in future_slice)
        horizon_exit_price = future_slice[-1].close

        # Compute MFE and MAE
        if is_bullish:
            mfe_pct = (highest_price - entry_price) / entry_price * 100.0
            mae_pct = (lowest_price - entry_price) / entry_price * 100.0  # negative
            return_at_horizon = (horizon_exit_price - entry_price) / entry_price * 100.0
            breakout_occurred = highest_price > entry_price * 1.005
        elif is_bearish:
            mfe_pct = (entry_price - lowest_price) / entry_price * 100.0
            mae_pct = (entry_price - highest_price) / entry_price * 100.0  # negative
            return_at_horizon = (entry_price - horizon_exit_price) / entry_price * 100.0
            breakout_occurred = lowest_price < entry_price * 0.995
        else:
            mfe_pct = (highest_price - entry_price) / entry_price * 100.0
            mae_pct = (lowest_price - entry_price) / entry_price * 100.0
            return_at_horizon = (horizon_exit_price - entry_price) / entry_price * 100.0
            breakout_occurred = abs(horizon_exit_price - entry_price) / entry_price > 0.005

        # Check if stop or target were hit in sequence
        stop_hit = False
        target_hit = False
        for c in future_slice:
            if is_bullish:
                if c.low <= stop_loss:
                    stop_hit = True
                    break
                if c.high >= target_price:
                    target_hit = True
                    break
            elif is_bearish:
                if c.high >= stop_loss:
                    stop_hit = True
                    break
                if c.low <= target_price:
                    target_hit = True
                    break

        # Estimated net P&L after two-way friction (entry & exit fees + slippage)
        friction_pct = (self.fee_pct * 2 + self.slippage_pct * 2) * 100.0
        if target_hit:
            raw_pnl = abs(target_price - entry_price) / entry_price * 100.0
        elif stop_hit:
            raw_pnl = -abs(entry_price - stop_loss) / entry_price * 100.0
        else:
            raw_pnl = return_at_horizon

        net_pnl = raw_pnl - friction_pct

        return PatternOutcome(
            pattern_id=f"{pattern.pattern_name}_{int(pattern.detection_timestamp)}",
            pattern_name=pattern.pattern_name,
            symbol=pattern.symbol,
            timeframe=pattern.timeframe,
            direction=pattern.direction.value,
            detection_timestamp=pattern.detection_timestamp,
            confirmation_timestamp=pattern.detection_timestamp if pattern.status == PatternStatus.CONFIRMED else None,
            entry_price=round(entry_price, 2),
            horizon_bars=eval_horizon,
            return_at_horizon_pct=round(return_at_horizon, 2),
            max_favorable_excursion_pct=round(mfe_pct, 2),
            max_adverse_excursion_pct=round(mae_pct, 2),
            breakout_occurred=breakout_occurred,
            stop_hit=stop_hit,
            target_hit=target_hit,
            net_pnl_after_costs=round(net_pnl, 2),
        )

    def evaluate_batch(
        self,
        patterns: List[PatternMatch],
        candles: List[Candle],
        horizon_bars: int = 15,
    ) -> List[PatternOutcome]:
        """Evaluate outcomes for a batch of historical patterns."""
        outcomes: List[PatternOutcome] = []
        for p in patterns:
            outcome = self.evaluate_outcome(p, candles, horizon_bars=horizon_bars)
            if outcome:
                outcomes.append(outcome)
        return outcomes

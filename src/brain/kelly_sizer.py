"""
kelly_sizer.py — Fractional Kelly Criterion for position sizing.

Plain English: Kelly tells you how much of your money to bet on
each trade. Bet too much and one bad trade wipes you out.
Bet too little and you grow too slowly.

The formula (verified from Wikipedia + multiple trading sources):
  f* = W - (1 - W) / R

Where:
  W = win rate (e.g., 0.55 = 55% of trades win)
  R = risk-reward ratio (avg_win / avg_loss)
  f* = fraction of capital to risk

We use HALF-Kelly (0.5 × f*) because:
- Full Kelly is way too aggressive for real trading
- Half Kelly keeps ~75% of the growth rate with much less drawdown
- This is standard practice among professional traders

Source: https://en.wikipedia.org/wiki/Kelly_criterion
"""

import logging

logger = logging.getLogger(__name__)


class KellySizer:
    """
    Calculates position sizes using fractional Kelly criterion.

    Recalculates after every N trades based on recent performance.
    If the strategy has no edge (Kelly ≤ 0), size = 0 (stop trading).
    """

    def __init__(self, config: dict):
        kelly_cfg = config.get('kelly', {})
        risk_cfg = config.get('risk', {})

        self.fraction = kelly_cfg.get('fraction', 0.5)      # half-Kelly
        self.lookback = kelly_cfg.get('lookback_trades', 20)
        self.min_trades = kelly_cfg.get('min_trades_required', 10)
        self.max_risk_pct = risk_cfg.get('max_risk_per_trade_pct', 2)

        self._last_kelly = 0.0
        self._last_win_rate = 0.0
        self._last_rr_ratio = 0.0

    def calculate_kelly(self, trades: list[dict]) -> float:
        """
        Calculate the raw Kelly fraction from recent trades.

        f* = W - (1 - W) / R

        Returns f* (can be negative if strategy has no edge).
        """
        if len(trades) < self.min_trades:
            logger.info(
                f"[KELLY] Not enough trades ({len(trades)} < {self.min_trades}). "
                f"Using minimum size."
            )
            return 0.0

        # Use the most recent N trades
        recent = trades[-self.lookback:] if len(trades) > self.lookback else trades

        wins = [t for t in recent if t.get('net_pnl', 0) > 0]
        losses = [t for t in recent if t.get('net_pnl', 0) <= 0]

        win_rate = len(wins) / len(recent) if recent else 0

        avg_win = (sum(t['net_pnl'] for t in wins) / len(wins)) if wins else 0
        avg_loss = (abs(sum(t['net_pnl'] for t in losses) / len(losses))
                    if losses else 0.001)  # avoid division by zero

        rr_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # Kelly formula
        if rr_ratio == 0:
            kelly = 0.0
        else:
            kelly = win_rate - (1 - win_rate) / rr_ratio

        self._last_kelly = kelly
        self._last_win_rate = win_rate
        self._last_rr_ratio = rr_ratio

        logger.info(
            f"[KELLY] Win rate: {win_rate*100:.1f}% | "
            f"R:R ratio: {rr_ratio:.2f} | "
            f"Raw Kelly: {kelly*100:.2f}% | "
            f"Half Kelly: {kelly * self.fraction * 100:.2f}%"
        )

        return kelly

    def get_position_size_pct(self, trades: list[dict]) -> float:
        """
        Get the recommended position size as a percentage of equity.

        This is the number the bot actually uses to decide how much
        to trade. It's capped at max_risk_pct for safety.

        Returns:
            Percentage of equity to risk (e.g., 1.5 means risk 1.5%)
            Returns 0 if the strategy has no edge.
        """
        kelly = self.calculate_kelly(trades)

        if kelly <= 0:
            logger.warning(
                f"[KELLY] Kelly ≤ 0 ({kelly*100:.2f}%). "
                f"Strategy has NO edge. Position size = 0."
            )
            return 0.0

        # Apply fraction (half-Kelly)
        fractional = kelly * self.fraction * 100  # convert to percentage

        # Cap at max risk per trade
        capped = min(fractional, self.max_risk_pct)

        logger.info(f"[KELLY] Position size: {capped:.2f}% of equity")
        return capped

    def get_stats(self) -> dict:
        """Current Kelly stats for the dashboard."""
        return {
            'raw_kelly': self._last_kelly,
            'fractional_kelly': self._last_kelly * self.fraction,
            'win_rate': self._last_win_rate,
            'rr_ratio': self._last_rr_ratio,
            'fraction_used': self.fraction,
            'max_risk_pct': self.max_risk_pct,
        }

"""
sma_crossover.py — Simple Moving Average Crossover Strategy.

Plain English: we track two averages of the price — one short (recent)
and one long (older). When the short average crosses above the long one,
it suggests the price is trending up, so we buy. When it crosses below,
we sell.

Honest disclaimer: this is a textbook indicator. It works in trending
markets but gets chopped up in sideways markets. It has no proven
long-term edge.
"""

from typing import Optional

from src.strategies.base_strategy import BaseStrategy


class SMACrossover(BaseStrategy):
    """
    Buy when short SMA crosses above long SMA.
    Sell when short SMA crosses below long SMA.

    SMA = Simple Moving Average = average of the last N closing prices.

    Default: short=10, long=30 (from settings.json)
    """

    def __init__(self, params: dict = None):
        params = params or {'short_period': 10, 'long_period': 30}
        super().__init__(name='SMA Crossover', params=params)
        self.short_period = params.get('short_period', 10)
        self.long_period = params.get('long_period', 30)

    def _sma(self, prices: list[float], period: int) -> Optional[float]:
        """Calculate Simple Moving Average over the last `period` prices."""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    def should_enter(self) -> Optional[str]:
        """
        Enter long when short SMA crosses ABOVE long SMA.
        (We need at least long_period + 1 candles to detect a crossover.)
        """
        if not self.has_enough_data(self.long_period + 1):
            return None

        closes = self.closes

        # Current SMAs
        short_now = self._sma(closes, self.short_period)
        long_now = self._sma(closes, self.long_period)

        # Previous SMAs (one candle ago)
        short_prev = self._sma(closes[:-1], self.short_period)
        long_prev = self._sma(closes[:-1], self.long_period)

        if None in (short_now, long_now, short_prev, long_prev):
            return None

        # Crossover: short was below long, now short is above long
        if short_prev <= long_prev and short_now > long_now:
            return 'long'

        return None

    def should_exit(self, position_side: str) -> bool:
        """
        Exit long when short SMA crosses BELOW long SMA.
        """
        if not self.has_enough_data(self.long_period + 1):
            return False

        closes = self.closes

        short_now = self._sma(closes, self.short_period)
        long_now = self._sma(closes, self.long_period)

        short_prev = self._sma(closes[:-1], self.short_period)
        long_prev = self._sma(closes[:-1], self.long_period)

        if None in (short_now, long_now, short_prev, long_prev):
            return False

        if position_side == 'long':
            # Exit when short crosses below long
            return short_prev >= long_prev and short_now < long_now

        return False

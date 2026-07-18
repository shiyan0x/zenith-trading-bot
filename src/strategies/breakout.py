"""
breakout.py — Donchian Channel Breakout Strategy.

Plain English: we look at the highest high and lowest low of the
last N candles (the "channel"). When price breaks above the top
of the channel, we buy — the idea is that a new high means
momentum might continue.

Honest disclaimer: breakouts work in trending markets but produce
many false signals in choppy markets. Every breakout strategy
has a lot of losing trades — it relies on the few big winners
to cover the many small losses.
"""

from typing import Optional

from src.strategies.base_strategy import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    """
    Buy when price breaks above the N-period high (Donchian upper band).
    Sell when price breaks below the N-period low (Donchian lower band).

    Default: N=20 (from settings.json)
    """

    def __init__(self, params: dict = None):
        params = params or {'period': 20}
        super().__init__(name='Donchian Breakout', params=params)
        self.period = params.get('period', 20)

    def should_enter(self) -> Optional[str]:
        """
        Enter long when the latest close is above the
        previous N-period high (excluding current candle).
        """
        if not self.has_enough_data(self.period + 1):
            return None

        closes = self.closes
        highs = self.highs

        # Channel high = highest high of the previous N candles
        # (not including the current candle)
        channel_high = max(highs[-(self.period + 1):-1])

        # Current close breaks above channel
        if closes[-1] > channel_high:
            return 'long'

        return None

    def should_exit(self, position_side: str) -> bool:
        """
        Exit long when price breaks below the N-period low.
        """
        if not self.has_enough_data(self.period + 1):
            return False

        closes = self.closes
        lows = self.lows

        # Channel low = lowest low of the previous N candles
        channel_low = min(lows[-(self.period + 1):-1])

        if position_side == 'long':
            return closes[-1] < channel_low

        return False

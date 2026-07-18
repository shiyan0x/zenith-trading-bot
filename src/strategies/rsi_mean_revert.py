"""
rsi_mean_revert.py — RSI Mean Reversion Strategy.

Plain English: RSI (Relative Strength Index) measures how "overbought"
or "oversold" something is on a scale of 0-100.
- Below 30 = oversold (it dropped a lot, might bounce back)
- Above 70 = overbought (it rose a lot, might pull back)

We buy when RSI drops below 30, and sell when it rises above 70.

RSI formula (verified, standard):
  RSI = 100 - 100 / (1 + avg_gain / avg_loss)

Honest disclaimer: mean reversion works when the market bounces.
In a real crash, "oversold" can get more oversold. This is not
a guarantee of anything.
"""

from typing import Optional

from src.strategies.base_strategy import BaseStrategy


class RSIMeanRevert(BaseStrategy):
    """
    Buy when RSI < oversold threshold (default 30).
    Sell when RSI > overbought threshold (default 70).
    """

    def __init__(self, params: dict = None):
        params = params or {'period': 14, 'oversold': 30, 'overbought': 70}
        super().__init__(name='RSI Mean Revert', params=params)
        self.period = params.get('period', 14)
        self.oversold = params.get('oversold', 30)
        self.overbought = params.get('overbought', 70)

    def _calculate_rsi(self) -> Optional[float]:
        """
        Calculate RSI using the standard Wilder smoothing method.

        RSI = 100 - 100 / (1 + RS)
        RS = Average Gain / Average Loss over `period` candles
        """
        closes = self.closes
        if len(closes) < self.period + 1:
            return None

        # Calculate price changes
        changes = [closes[i] - closes[i - 1]
                    for i in range(1, len(closes))]

        # Need at least `period` changes
        if len(changes) < self.period:
            return None

        # First average: simple average of first `period` changes
        gains = [max(c, 0) for c in changes[:self.period]]
        losses = [abs(min(c, 0)) for c in changes[:self.period]]
        avg_gain = sum(gains) / self.period
        avg_loss = sum(losses) / self.period

        # Wilder smoothing for remaining changes
        for c in changes[self.period:]:
            gain = max(c, 0)
            loss = abs(min(c, 0))
            avg_gain = (avg_gain * (self.period - 1) + gain) / self.period
            avg_loss = (avg_loss * (self.period - 1) + loss) / self.period

        if avg_loss == 0:
            return 100.0  # no losses = maximum RSI

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def should_enter(self) -> Optional[str]:
        """
        Enter long when RSI drops below oversold level (30).
        """
        rsi = self._calculate_rsi()
        if rsi is None:
            return None

        if rsi < self.oversold:
            return 'long'

        return None

    def should_exit(self, position_side: str) -> bool:
        """
        Exit long when RSI rises above overbought level (70).
        """
        rsi = self._calculate_rsi()
        if rsi is None:
            return False

        if position_side == 'long':
            return rsi > self.overbought

        return False

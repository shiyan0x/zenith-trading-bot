"""
base_strategy.py — The interface every strategy must follow.

Plain English: a "strategy" is just a set of rules that say
"buy now" or "sell now" based on the price data.

Every strategy must answer two questions:
1. should_enter() — should I open a new trade?
2. should_exit() — should I close my current trade?
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.core.market_feed import Candle


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    To create a new strategy:
    1. Subclass this
    2. Implement should_enter() and should_exit()
    3. That's it — the engine handles everything else
    """

    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}
        self._candle_history: list[Candle] = []

    def update(self, candle: Candle):
        """Feed a new candle to the strategy. Only keeps closed candles."""
        if candle.is_closed:
            self._candle_history.append(candle)

    @property
    def closes(self) -> list[float]:
        """Get list of closing prices."""
        return [c.close for c in self._candle_history]

    @property
    def highs(self) -> list[float]:
        """Get list of high prices."""
        return [c.high for c in self._candle_history]

    @property
    def lows(self) -> list[float]:
        """Get list of low prices."""
        return [c.low for c in self._candle_history]

    @property
    def volumes(self) -> list[float]:
        """Get list of volumes."""
        return [c.volume for c in self._candle_history]

    def has_enough_data(self, min_candles: int) -> bool:
        """Check if we have enough price history to make a decision."""
        return len(self._candle_history) >= min_candles

    def reset(self):
        """Clear history — used when starting a new backtest run."""
        self._candle_history = []

    @abstractmethod
    def should_enter(self) -> Optional[str]:
        """
        Should we open a new trade?

        Returns:
            'long' — buy signal
            'short' — sell signal (not used in spot mode)
            None — no signal, stay out

        This is called on every new closed candle.
        """
        pass

    @abstractmethod
    def should_exit(self, position_side: str) -> bool:
        """
        Should we close our current trade?

        Args:
            position_side: 'long' or 'short' — what we're currently holding

        Returns:
            True — close the trade now
            False — keep holding

        This is called on every new closed candle while we have an open position.
        """
        pass

    def __repr__(self):
        return f"{self.name}({self.params})"

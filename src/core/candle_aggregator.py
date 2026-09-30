"""
candle_aggregator.py — Safe candle aggregation for non-native intervals.

Binance does not support all possible timeframes natively.
For example, 10m is not a valid kline interval.

This module aggregates smaller native-interval candles into larger
synthetic candles, with strict boundary alignment and integrity checks.

Supported aggregation rules (verified against Binance klines):
    10m  = 2 × 5m  (boundaries at minutes :00, :10, :20, :30, :40, :50)

Honesty rules:
- We ONLY aggregate from the smallest native interval that evenly divides
  the target. We never interpolate, extrapolate, or fabricate candle data.
- If the required source candles are incomplete, the aggregated candle is
  not emitted — we wait for the full set.
- Each aggregated candle carries the same OHLCV semantics as a native one:
    O = first candle's open
    H = max of all highs
    L = min of all lows
    C = last candle's close
    V = sum of all volumes
"""

import logging
from typing import Optional, Callable

from src.core.market_feed import Candle

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────

# Maps a non-native target interval to its (source_native_interval, ratio).
AGGREGATION_RULES: dict[str, tuple[str, int]] = {
    '10m': ('5m', 2),   # 2 × 5-minute candles = 10 minutes
}

# All intervals Binance supports natively via WebSocket and REST klines.
NATIVE_INTERVALS = {
    '1m', '3m', '5m', '15m', '30m',
    '1h', '2h', '4h', '6h', '8h', '12h',
    '1d', '3d', '1w', '1M',
}

# The combined set of intervals this bot can work with.
SUPPORTED_INTERVALS = NATIVE_INTERVALS | set(AGGREGATION_RULES.keys())

# Interval duration in seconds (used for boundary alignment).
INTERVAL_SECONDS: dict[str, int] = {
    '1m': 60, '3m': 180, '5m': 300, '10m': 600, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800,
    '12h': 43200, '1d': 86400,
}


def is_native(interval: str) -> bool:
    """Return True if Binance supports this interval directly."""
    return interval in NATIVE_INTERVALS


def needs_aggregation(interval: str) -> bool:
    """Return True if this interval must be constructed from smaller candles."""
    return interval in AGGREGATION_RULES


def get_source_interval(interval: str) -> Optional[str]:
    """Return the native source interval used for aggregation, or None."""
    rule = AGGREGATION_RULES.get(interval)
    return rule[0] if rule else None


def get_aggregation_ratio(interval: str) -> int:
    """Return how many source candles make one aggregated candle."""
    rule = AGGREGATION_RULES.get(interval)
    return rule[1] if rule else 1


class CandleAggregator:
    """
    Buffers source-interval candles and emits aggregated candles
    when a full set is collected.

    Usage (for 10m from 5m):
        aggregator = CandleAggregator('10m')
        # Feed every 5m candle:
        result = aggregator.push(candle_5m)
        if result is not None:
            # result is a complete 10m Candle
            strategy.update(result)

    Thread safety: not thread-safe. Use one aggregator per symbol per stream.
    """

    def __init__(self, target_interval: str):
        if target_interval not in AGGREGATION_RULES:
            raise ValueError(
                f"CandleAggregator only handles non-native intervals. "
                f"'{target_interval}' is native or unsupported."
            )
        rule = AGGREGATION_RULES[target_interval]
        self.target_interval = target_interval
        self.source_interval = rule[0]
        self.ratio = rule[1]
        self._buffer: list[Candle] = []
        self._target_seconds = INTERVAL_SECONDS[target_interval]

    def _is_boundary_start(self, candle: Candle) -> bool:
        """
        Check if this source candle starts at a boundary of the target interval.
        For 10m: timestamps at :00, :10, :20, :30, :40, :50.
        """
        ts_seconds = int(candle.timestamp)
        return (ts_seconds % self._target_seconds) == 0

    def push(self, candle: Candle) -> Optional[Candle]:
        """
        Feed a source-interval candle. Returns a completed aggregated
        Candle when the buffer is full, or None if still collecting.

        Only processes closed candles. Non-closed candles are ignored.
        """
        if not candle.is_closed:
            return None

        # If this candle is a boundary start, reset the buffer
        if self._is_boundary_start(candle):
            self._buffer = [candle]
        elif self._buffer:
            # Guard against stale or duplicate candles:
            if candle.timestamp <= self._buffer[-1].timestamp:
                logger.warning(
                    f"[AGGREGATOR] Ignoring stale/duplicate candle ts={candle.timestamp} "
                    f"(last ts={self._buffer[-1].timestamp})"
                )
                return None
            self._buffer.append(candle)
        else:
            # We haven't seen a boundary start yet — discard
            return None

        if len(self._buffer) < self.ratio:
            return None

        # We have a full set — construct the aggregated candle
        aggregated = self._merge()
        self._buffer = []
        return aggregated

    def _merge(self) -> Candle:
        """Merge buffered candles into one aggregated candle."""
        buf = self._buffer
        return Candle(
            timestamp=buf[0].timestamp,              # open time of first bar
            o=buf[0].open,                            # first open
            h=max(c.high for c in buf),               # highest high
            l=min(c.low for c in buf),                # lowest low
            c=buf[-1].close,                          # last close
            volume=sum(c.volume for c in buf),         # total volume
            is_closed=True,
        )

    def reset(self):
        """Clear the buffer (e.g. on timeframe change)."""
        self._buffer = []

    @property
    def pending_count(self) -> int:
        """How many source candles are buffered (waiting for more)."""
        return len(self._buffer)


def aggregate_historical_candles(
    source_candles: list[Candle],
    target_interval: str,
) -> list[Candle]:
    """
    Aggregate a list of historical source candles into target-interval candles.

    Used by the backtester when it needs to test on a non-native interval.
    Source candles must already be sorted by timestamp ascending.

    Args:
        source_candles: list of Candle from the native source interval
        target_interval: e.g. '10m'

    Returns:
        list of aggregated Candle objects
    """
    if target_interval not in AGGREGATION_RULES:
        raise ValueError(f"No aggregation rule for '{target_interval}'")

    aggregator = CandleAggregator(target_interval)
    result = []
    for candle in source_candles:
        merged = aggregator.push(candle)
        if merged is not None:
            result.append(merged)

    if aggregator.pending_count > 0:
        logger.debug(
            f"[AGGREGATOR] Discarded {aggregator.pending_count} trailing "
            f"{aggregator.source_interval} candles (incomplete {target_interval} bar)"
        )

    logger.info(
        f"[AGGREGATOR] Aggregated {len(source_candles)} {aggregator.source_interval} "
        f"candles → {len(result)} {target_interval} candles"
    )
    return result

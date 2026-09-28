"""
resilience.py — Production Resilience & Fault-Tolerance Controls.

Provides:
1. StaleDataDetector: Detects halted, delayed, or stale market feeds.
2. ReconnectHandler: Exponential backoff reconnection manager with state tracking.
3. DuplicateEventFilter: Sliding-window event deduplication preventing double executions.
4. StatePersistence: Atomic state snapshots and recovery across bot restarts.
"""

import os
import json
import time
import logging
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class StaleDataDetector:
    """
    Monitors market feed activity per symbol. Flags stale data and halts trading
    if market updates stop arriving.
    """

    def __init__(self, default_max_age_seconds: float = 120.0):
        self.default_max_age_seconds = default_max_age_seconds
        self._last_tick_timestamps: Dict[str, float] = {}

    def record_tick(self, symbol: str, timestamp: Optional[float] = None):
        """Record the arrival of fresh market data for a symbol."""
        self._last_tick_timestamps[symbol.upper()] = timestamp or time.time()

    def is_stale(self, symbol: str, max_age_seconds: Optional[float] = None,
                 current_time: Optional[float] = None) -> Tuple[bool, float]:
        """
        Check if data for a symbol is stale.
        Returns (is_stale, elapsed_seconds).
        """
        sym = symbol.upper()
        last_ts = self._last_tick_timestamps.get(sym)
        if last_ts is None:
            # Never seen any data yet
            return True, float('inf')

        now = current_time or time.time()
        elapsed = now - last_ts
        threshold = max_age_seconds or self.default_max_age_seconds
        return (elapsed > threshold), elapsed

    def is_any_stale(self, symbols: List[str],
                     max_age_seconds: Optional[float] = None) -> Tuple[bool, List[str]]:
        """Check if any of the required symbols are stale."""
        stale_symbols = []
        for sym in symbols:
            stale, _ = self.is_stale(sym, max_age_seconds)
            if stale:
                stale_symbols.append(sym)
        return (len(stale_symbols) > 0), stale_symbols


class ReconnectHandler:
    """
    Exponential backoff reconnection manager.
    Tracks connection states: CONNECTED, CONNECTING, RECONNECTING, DISCONNECTED, FAILED.
    """

    STATES = {'CONNECTED', 'CONNECTING', 'RECONNECTING', 'DISCONNECTED', 'FAILED'}

    def __init__(self, base_delay: float = 1.0, max_delay: float = 60.0,
                 backoff_factor: float = 2.0, max_attempts: int = 10):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.max_attempts = max_attempts

        self.current_state = 'DISCONNECTED'
        self.attempts = 0
        self.last_attempt_time: Optional[float] = None
        self.last_connected_time: Optional[float] = None

    def record_success(self):
        """Mark connection as successfully established."""
        self.current_state = 'CONNECTED'
        self.attempts = 0
        self.last_connected_time = time.time()
        logger.info("[RECONNECT] Connection established successfully.")

    def record_failure(self, reason: str = "") -> float:
        """
        Record a disconnection or failed connection attempt.
        Calculates and returns the delay in seconds before next retry.
        """
        self.attempts += 1
        self.last_attempt_time = time.time()

        if self.attempts >= self.max_attempts:
            self.current_state = 'FAILED'
            logger.critical(
                f"[RECONNECT] Connection failed after {self.attempts} attempts. "
                f"Reason: {reason}. Max attempts exceeded."
            )
            return self.max_delay

        self.current_state = 'RECONNECTING'
        # Exponential backoff formula
        delay = min(self.max_delay, self.base_delay * (self.backoff_factor ** (self.attempts - 1)))
        logger.warning(
            f"[RECONNECT] Connection lost ({reason}). "
            f"Attempt {self.attempts}/{self.max_attempts}. Retrying in {delay:.1f}s..."
        )
        return delay

    def is_failed(self) -> bool:
        return self.current_state == 'FAILED'


class DuplicateEventFilter:
    """
    Sliding-window deduplication cache.
    Prevents repeated processing of duplicate WebSocket candles or re-emitted signals.
    """

    def __init__(self, max_entries: int = 5000):
        self.max_entries = max_entries
        self._seen_keys: OrderedDict[str, float] = OrderedDict()

    def _check_and_add(self, key: str) -> bool:
        """Returns True if key was already seen (duplicate), False otherwise."""
        if key in self._seen_keys:
            return True

        self._seen_keys[key] = time.time()
        if len(self._seen_keys) > self.max_entries:
            # Evict oldest entry (FIFO)
            self._seen_keys.popitem(last=False)
        return False

    def is_duplicate_candle(self, symbol: str, timestamp: float,
                            is_closed: bool = True) -> bool:
        """Check if candle update has already been processed."""
        key = f"c:{symbol.upper()}:{int(timestamp)}:{is_closed}"
        return self._check_and_add(key)

    def is_duplicate_signal(self, strategy_name: str, symbol: str,
                            timestamp: float, side: str) -> bool:
        """Check if identical strategy signal was already emitted."""
        key = f"s:{strategy_name}:{symbol.upper()}:{int(timestamp)}:{side.lower()}"
        return self._check_and_add(key)

    def clear(self):
        self._seen_keys.clear()


class StatePersistence:
    """
    Persists and recovers PaperWallet state across bot restarts.
    Uses atomic file writing to prevent data corruption during crashes.
    """

    def __init__(self, filepath: Optional[str] = None):
        if filepath is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            filepath = os.path.join(base_dir, 'data', 'state', 'paper_wallet_state.json')
        self.filepath = filepath
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)

    def save_wallet_state(self, wallet: Any) -> bool:
        """Save wallet state to JSON atomically."""
        try:
            state = wallet.to_dict()
            temp_path = f"{self.filepath}.tmp_{os.getpid()}"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, default=str)
            # Atomic rename (safe on POSIX and Windows in modern Python)
            os.replace(temp_path, self.filepath)
            logger.debug(f"[STATE] Wallet state saved to {self.filepath}")
            return True
        except Exception as e:
            logger.error(f"[STATE] Failed to save wallet state: {e}")
            return False

    def load_wallet_state(self) -> Optional[Dict[str, Any]]:
        """Load saved state from JSON if it exists."""
        if not os.path.exists(self.filepath):
            return None
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[STATE] Failed to load wallet state from {self.filepath}: {e}")
            return None

    def restore_wallet(self, wallet: Any) -> bool:
        """Restore wallet from persistent snapshot."""
        state = self.load_wallet_state()
        if not state:
            return False
        try:
            if hasattr(wallet, 'restore_state'):
                wallet.restore_state(state)
                logger.info(f"[STATE] Successfully recovered wallet state from {self.filepath}")
                return True
        except Exception as e:
            logger.error(f"[STATE] Error restoring wallet state: {e}")
        return False

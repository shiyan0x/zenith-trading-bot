"""
notifications.py — Critical Event Notifications & Alerts.

Dispatches high-priority notifications for critical trading events:
- Circuit breaker triggers
- Global emergency stop activations
- Daily loss limit breaches
- Stale market data feeds
- Broker disconnections / reconnect failures
- Order rejections and reconciliation anomalies
"""

import time
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger(__name__)


class NotificationManager:
    """Central notification dispatcher for critical system events."""

    def __init__(self, max_buffer_size: int = 100):
        self._history = deque(maxlen=max_buffer_size)
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []

    def register_callback(self, cb: Callable[[Dict[str, Any]], None]):
        """Register custom alert callback (e.g. webhook or UI push)."""
        self._callbacks.append(cb)

    def dispatch(self, event_type: str, message: str,
                 severity: str = "INFO",
                 details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Dispatch a critical event notification."""
        now = time.time()
        notification = {
            'timestamp': now,
            'iso_time': datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            'event_type': event_type,
            'message': message,
            'severity': severity.upper(),
            'details': details or {},
        }
        self._history.append(notification)

        # Log according to severity
        log_msg = f"[NOTIFICATION] [{severity.upper()}] {event_type}: {message}"
        if severity.upper() in ('EMERGENCY', 'CRITICAL'):
            logger.critical(log_msg)
        elif severity.upper() == 'WARNING':
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # Trigger callbacks
        for cb in self._callbacks:
            try:
                cb(notification)
            except Exception as e:
                logger.error(f"[NOTIFICATION] Callback failed: {e}")

        return notification

    def get_recent_notifications(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(self._history)[-limit:]

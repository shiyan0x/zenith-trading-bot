"""
execution_tracker.py — Comprehensive Event & Execution Audit Trail.

Tracks and persists all:
1. Signals (strategy, symbol, side, accepted/rejected, reason)
2. Orders (order_id, symbol, side, qty, price, status)
3. Fills (fill_id, order_id, exec_price, exec_qty, fee, slippage)
4. Positions (symbol, side, qty, entry_price, pnl)
5. Fees (individual trade fees, cumulative totals)
6. Equity snapshots (cash, equity, drawdown over time)
7. Risk events (breaker tripped, limits breached, stale data, emergency stop)

Data is written to an SQLite database (data/execution.db) and cached in an in-memory
ring buffer for high-performance dashboard access.
"""

import os
import json
import time
import sqlite3
import logging
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL NOT NULL,
    iso_time    TEXT NOT NULL,
    event_type  TEXT NOT NULL, -- signal, order, fill, position, fee, equity, risk
    symbol      TEXT,
    side        TEXT,
    status      TEXT,
    details     TEXT NOT NULL -- JSON payload of the event
);

CREATE INDEX IF NOT EXISTS idx_exec_type ON execution_events(event_type);
CREATE INDEX IF NOT EXISTS idx_exec_sym ON execution_events(symbol);
CREATE INDEX IF NOT EXISTS idx_exec_time ON execution_events(timestamp);
"""


class ExecutionTracker:
    """Audit logger and live event tracker for all execution activities."""

    def __init__(self, db_path: Optional[str] = None, max_memory_events: int = 500):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(base_dir, 'data', 'execution.db')
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._memory_events = deque(maxlen=max_memory_events)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _record_event(self, event_type: str, symbol: Optional[str],
                      side: Optional[str], status: Optional[str],
                      details: Dict[str, Any], timestamp: Optional[float] = None) -> int:
        ts = timestamp or time.time()
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        payload_str = json.dumps(details, default=str)

        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO execution_events
                   (timestamp, iso_time, event_type, symbol, side, status, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts, iso, event_type, symbol, side, status, payload_str)
            )
            event_id = cur.lastrowid

        event_dict = {
            'id': event_id,
            'timestamp': ts,
            'iso_time': iso,
            'event_type': event_type,
            'symbol': symbol,
            'side': side,
            'status': status,
            'details': details,
        }
        self._memory_events.append(event_dict)
        return event_id

    # ── Specialized Record Methods ──────────────────────────────────────────

    def record_signal(self, strategy_name: str, symbol: str, side: str,
                      accepted: bool, reason: str = "",
                      timestamp: Optional[float] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> int:
        details = {
            'strategy_name': strategy_name,
            'accepted': accepted,
            'reason': reason,
            'metadata': metadata or {},
        }
        return self._record_event(
            event_type='signal',
            symbol=symbol,
            side=side,
            status='ACCEPTED' if accepted else 'REJECTED',
            details=details,
            timestamp=timestamp,
        )

    def record_order(self, order_id: str, symbol: str, side: str,
                     quantity: float, price: float, order_type: str = "MARKET",
                     status: str = "PENDING", reason: str = "",
                     timestamp: Optional[float] = None) -> int:
        details = {
            'order_id': order_id,
            'quantity': quantity,
            'price': price,
            'order_type': order_type,
            'reason': reason,
        }
        return self._record_event(
            event_type='order',
            symbol=symbol,
            side=side,
            status=status,
            details=details,
            timestamp=timestamp,
        )

    def record_fill(self, fill_id: str, order_id: str, symbol: str,
                    side: str, exec_price: float, exec_qty: float,
                    fee: float, slippage_bps: float = 0.0,
                    timestamp: Optional[float] = None) -> int:
        details = {
            'fill_id': fill_id,
            'order_id': order_id,
            'execution_price': exec_price,
            'executed_quantity': exec_qty,
            'fee': fee,
            'slippage_bps': slippage_bps,
        }
        return self._record_event(
            event_type='fill',
            symbol=symbol,
            side=side,
            status='FILLED',
            details=details,
            timestamp=timestamp,
        )

    def record_position(self, position_id: str, symbol: str, side: str,
                        quantity: float, entry_price: float, fee_paid: float,
                        action: str = "OPEN",
                        unrealized_pnl: float = 0.0,
                        timestamp: Optional[float] = None) -> int:
        details = {
            'position_id': position_id,
            'quantity': quantity,
            'entry_price': entry_price,
            'fee_paid': fee_paid,
            'unrealized_pnl': unrealized_pnl,
        }
        return self._record_event(
            event_type='position',
            symbol=symbol,
            side=side,
            status=action,
            details=details,
            timestamp=timestamp,
        )

    def record_fee(self, trade_id: str, symbol: str, fee_amount: float,
                   fee_type: str = "taker", cumulative_fees: float = 0.0,
                   timestamp: Optional[float] = None) -> int:
        details = {
            'trade_id': trade_id,
            'fee_amount': fee_amount,
            'fee_type': fee_type,
            'cumulative_fees': cumulative_fees,
        }
        return self._record_event(
            event_type='fee',
            symbol=symbol,
            side=None,
            status='CHARGED',
            details=details,
            timestamp=timestamp,
        )

    def record_equity(self, cash: float, total_equity: float,
                      drawdown_pct: float, positions_count: int = 0,
                      timestamp: Optional[float] = None) -> int:
        details = {
            'cash': cash,
            'equity': total_equity,
            'drawdown_pct': drawdown_pct,
            'positions_count': positions_count,
        }
        return self._record_event(
            event_type='equity',
            symbol=None,
            side=None,
            status='SNAPSHOT',
            details=details,
            timestamp=timestamp,
        )

    def record_risk_event(self, event_type: str, details: str,
                          severity: str = "WARNING",
                          symbol: Optional[str] = None,
                          timestamp: Optional[float] = None) -> int:
        payload = {
            'risk_event': event_type,
            'details': details,
            'severity': severity,
        }
        return self._record_event(
            event_type='risk',
            symbol=symbol,
            side=None,
            status=severity,
            details=payload,
            timestamp=timestamp,
        )

    # ── Query Methods ────────────────────────────────────────────────────────

    def get_recent_events(self, limit: int = 50,
                          event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve recent events from DB."""
        query = "SELECT * FROM execution_events "
        params: List[Any] = []
        if event_type:
            query += "WHERE event_type = ? "
            params.append(event_type)
        query += "ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d['details'] = json.loads(d['details'])
            except Exception:
                pass
            results.append(d)
        return results

    def get_recent_memory_events(self) -> List[Dict[str, Any]]:
        """Get fast in-memory events queue."""
        return list(self._memory_events)

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
    event_type  TEXT NOT NULL, -- signal, order, fill, position, fee, equity, risk, trade
    symbol      TEXT,
    side        TEXT,
    status      TEXT,
    timeframe   TEXT,
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
            # Safe migration: add timeframe column if upgrading existing DB
            cols = [r[1] for r in conn.execute("PRAGMA table_info(execution_events)").fetchall()]
            if 'timeframe' not in cols:
                conn.execute("ALTER TABLE execution_events ADD COLUMN timeframe TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_tf ON execution_events(timeframe)")

    def _record_event(self, event_type: str, symbol: Optional[str],
                      side: Optional[str], status: Optional[str],
                      details: Dict[str, Any], timestamp: Optional[float] = None,
                      timeframe: Optional[str] = None) -> int:
        ts = timestamp or time.time()
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        payload_str = json.dumps(details, default=str)

        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO execution_events
                   (timestamp, iso_time, event_type, symbol, side, status, timeframe, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, iso, event_type, symbol, side, status, timeframe, payload_str)
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
            'timeframe': timeframe,
            'details': details,
        }
        self._memory_events.append(event_dict)
        return event_id

    # ── Specialized Record Methods ──────────────────────────────────────────

    def record_signal(self, strategy_name: str, symbol: str, side: str,
                      accepted: bool, reason: str = "",
                      timestamp: Optional[float] = None,
                      metadata: Optional[Dict[str, Any]] = None,
                      timeframe: Optional[str] = None) -> int:
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
            timeframe=timeframe,
        )

    def record_order(self, order_id: str, symbol: str, side: str,
                     quantity: float, price: float, order_type: str = "MARKET",
                     status: str = "PENDING", reason: str = "",
                     timestamp: Optional[float] = None,
                     timeframe: Optional[str] = None) -> int:
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
            timeframe=timeframe,
        )

    def record_fill(self, fill_id: str, order_id: str, symbol: str,
                    side: str, exec_price: float, exec_qty: float,
                    fee: float, slippage_bps: float = 0.0,
                    timestamp: Optional[float] = None,
                    timeframe: Optional[str] = None) -> int:
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
            timeframe=timeframe,
        )

    def record_position(self, position_id: str, symbol: str, side: str,
                        quantity: float, entry_price: float, fee_paid: float,
                        action: str = "OPEN",
                        unrealized_pnl: float = 0.0,
                        timestamp: Optional[float] = None,
                        timeframe: Optional[str] = None) -> int:
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
            timeframe=timeframe,
        )

    def record_fee(self, trade_id: str, symbol: str, fee_amount: float,
                   fee_type: str = "taker", cumulative_fees: float = 0.0,
                   timestamp: Optional[float] = None,
                   timeframe: Optional[str] = None) -> int:
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
            timeframe=timeframe,
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
                          timestamp: Optional[float] = None,
                          timeframe: Optional[str] = None) -> int:
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
            timeframe=timeframe,
        )

    def record_trade(self, symbol: str, timeframe: str,
                     strategy_name: str, strategy_version: int = 1,
                     signal_time: Optional[float] = None,
                     entry_time: Optional[float] = None,
                     exit_time: Optional[float] = None,
                     entry_price: float = 0.0,
                     exit_price: float = 0.0,
                     quantity: float = 0.0,
                     gross_pnl: float = 0.0,
                     fees: float = 0.0,
                     slippage_bps: float = 0.0,
                     net_pnl: float = 0.0,
                     outcome: str = "flat",
                     exit_reason: str = "strategy_exit",
                     mode: str = "paper",
                     timestamp: Optional[float] = None) -> int:
        """
        Record a completed trade with full audit attributes including timeframe.
        Required by Multi-Timeframe Performance tracking (Step 4).
        """
        details = {
            'symbol': symbol,
            'timeframe': timeframe,
            'strategy_name': strategy_name,
            'strategy_version': strategy_version,
            'signal_time': signal_time,
            'entry_time': entry_time,
            'exit_time': exit_time,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'quantity': quantity,
            'gross_pnl': gross_pnl,
            'fees': fees,
            'slippage_bps': slippage_bps,
            'net_pnl': net_pnl,
            'outcome': outcome,
            'exit_reason': exit_reason,
            'mode': mode,
        }
        return self._record_event(
            event_type='trade',
            symbol=symbol,
            side=None,
            status=outcome.upper(),
            timeframe=timeframe,
            details=details,
            timestamp=timestamp or exit_time or time.time(),
        )

    # ── Query Methods ────────────────────────────────────────────────────────

    def get_recent_events(self, limit: int = 50,
                          event_type: Optional[str] = None,
                          timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve recent events from DB with optional event_type and timeframe filters."""
        query = "SELECT * FROM execution_events WHERE 1=1 "
        params: List[Any] = []
        if event_type:
            query += "AND event_type = ? "
            params.append(event_type)
        if timeframe:
            query += "AND timeframe = ? "
            params.append(timeframe)
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

    def get_trades_by_timeframe(self, timeframe: Optional[str] = None,
                                symbol: Optional[str] = None,
                                strategy: Optional[str] = None,
                                mode: Optional[str] = None,
                                start_time: Optional[float] = None,
                                end_time: Optional[float] = None) -> List[Dict[str, Any]]:
        """Retrieve recorded trades filtered by timeframe, symbol, strategy, date range, or mode."""
        query = "SELECT * FROM execution_events WHERE event_type = 'trade' "
        params: List[Any] = []

        if timeframe:
            query += "AND timeframe = ? "
            params.append(timeframe)
        if symbol:
            query += "AND symbol = ? "
            params.append(symbol)
        if start_time:
            query += "AND timestamp >= ? "
            params.append(start_time)
        if end_time:
            query += "AND timestamp <= ? "
            params.append(end_time)

        query += "ORDER BY timestamp ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        trades = []
        for r in rows:
            d = dict(r)
            try:
                details = json.loads(d['details'])
            except Exception:
                details = {}

            # Additional in-memory filtering for json details fields
            if strategy and details.get('strategy_name') != strategy:
                continue
            if mode and details.get('mode') != mode:
                continue

            item = {
                'id': d['id'],
                'timestamp': d['timestamp'],
                'iso_time': d['iso_time'],
                'symbol': d['symbol'],
                'timeframe': d['timeframe'] or details.get('timeframe', ''),
                **details
            }
            trades.append(item)
        return trades

    def get_timeframe_stats(self, timeframe: str,
                            symbol: Optional[str] = None,
                            strategy: Optional[str] = None,
                            mode: Optional[str] = None,
                            start_time: Optional[float] = None,
                            end_time: Optional[float] = None,
                            min_trades: int = 5) -> Dict[str, Any]:
        """
        Aggregate performance metrics for a specific timeframe according to Step 5 requirements:
        - total trades, winning/losing, win rate, gross pnl, net pnl, avg trade pnl, max drawdown, profit factor, data gaps/uptime.
        Returns 'status': 'insufficient_data' if total_trades < min_trades.
        """
        trades = self.get_trades_by_timeframe(
            timeframe=timeframe, symbol=symbol, strategy=strategy, mode=mode,
            start_time=start_time, end_time=end_time
        )

        # Count data gaps/errors for this timeframe
        error_query = """
            SELECT COUNT(*) as err_count FROM execution_events
            WHERE event_type = 'risk'
            AND (timeframe = ? OR timeframe IS NULL)
        """
        params: List[Any] = [timeframe]
        if start_time:
            error_query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            error_query += " AND timestamp <= ?"
            params.append(end_time)

        with self._connect() as conn:
            err_row = conn.execute(error_query, params).fetchone()
            error_count = err_row['err_count'] if err_row else 0

        total_trades = len(trades)
        date_range_str = "No trades recorded"
        if trades:
            first_ts = trades[0]['timestamp']
            last_ts = trades[-1]['timestamp']
            d1 = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime('%Y-%m-%d')
            d2 = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime('%Y-%m-%d')
            date_range_str = f"{d1} to {d2}" if d1 != d2 else d1

        if total_trades < min_trades:
            return {
                'timeframe': timeframe,
                'status': 'insufficient_data',
                'message': 'Not enough data',
                'total_trades': total_trades,
                'sample_size': total_trades,
                'min_required_trades': min_trades,
                'date_range': date_range_str,
                'data_gaps_and_errors': error_count,
                'uptime_pct': 100.0 if error_count == 0 else max(0.0, 100.0 - error_count * 2.0),
            }

        winning = [t for t in trades if t.get('net_pnl', 0.0) > 0]
        losing = [t for t in trades if t.get('net_pnl', 0.0) < 0]
        gross_pnl = sum(t.get('gross_pnl', 0.0) for t in trades)
        net_pnl = sum(t.get('net_pnl', 0.0) for t in trades)
        fees_paid = sum(t.get('fees', 0.0) for t in trades)

        gross_wins = sum(t.get('gross_pnl', 0.0) for t in winning)
        gross_losses = abs(sum(t.get('gross_pnl', 0.0) for t in losing))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (float('inf') if gross_wins > 0 else 0.0)

        # Calculate max drawdown along closed trade equity curve
        eq = 10000.0
        peak = eq
        max_dd = 0.0
        for t in trades:
            eq += t.get('net_pnl', 0.0)
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        return {
            'timeframe': timeframe,
            'status': 'completed',
            'sample_size': total_trades,
            'total_trades': total_trades,
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': round((len(winning) / total_trades) * 100.0, 2),
            'gross_pnl': round(gross_pnl, 2),
            'fees_paid': round(fees_paid, 2),
            'net_pnl': round(net_pnl, 2),
            'avg_net_pnl': round(net_pnl / total_trades, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 999.0,
            'date_range': date_range_str,
            'data_gaps_and_errors': error_count,
            'uptime_pct': 100.0 if error_count == 0 else max(0.0, 100.0 - error_count * 2.0),
        }

    def get_recent_memory_events(self) -> List[Dict[str, Any]]:
        """Get fast in-memory events queue."""
        return list(self._memory_events)

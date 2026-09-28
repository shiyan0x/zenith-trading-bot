"""
experiment_store.py — SQLite persistence for the AI Strategy Research Lab.

Stores strategies, experiments, backtest results, and research reports.
Uses Python's built-in sqlite3 — no extra dependency.

Database location: data/research.db (auto-created on first use).
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, 'data', 'research.db')


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strategies (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    strategy_type TEXT NOT NULL,       -- 'legacy' | 'generated'
    blueprint   TEXT NOT NULL,         -- JSON
    timeframe   TEXT,
    entry_rules_desc TEXT,
    exit_rules_desc  TEXT,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'candidate'
                CHECK (status IN ('legacy','candidate','promoted','retired',
                                  'pending_paper','paper_trading','pending_live','live')),
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS experiments (
    id           TEXT PRIMARY KEY,
    strategy_id  TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    data_start_ms INTEGER,
    data_end_ms   INTEGER,
    num_candles  INTEGER,
    config_snapshot TEXT NOT NULL,      -- JSON (full settings used)
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','running','completed','failed')),
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    completed_at TEXT,
    error_message TEXT,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

CREATE TABLE IF NOT EXISTS experiment_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id  TEXT NOT NULL,
    period         TEXT NOT NULL,       -- 'train' | 'test' | 'full'
    total_trades   INTEGER,
    winning_trades INTEGER,
    losing_trades  INTEGER,
    win_rate       REAL,
    total_return_pct REAL,
    max_drawdown_pct REAL,
    sharpe_ratio   REAL,
    profit_factor  REAL,
    avg_win        REAL,
    avg_loss       REAL,
    starting_balance REAL,
    ending_balance REAL,
    passed         INTEGER,            -- 0 or 1
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

CREATE TABLE IF NOT EXISTS research_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    report_type    TEXT NOT NULL,       -- 'analysis' | 'suggestion' | 'weakness'
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,       -- JSON structured body
    experiment_ids TEXT,                -- JSON list of related experiment IDs
    suggestions    TEXT                 -- JSON list of suggested actions
);
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid4().hex[:12]


# ── Store ─────────────────────────────────────────────────────────────────────

class ExperimentStore:
    """SQLite-backed storage for research experiments and results.

    Every write goes through this class to guarantee schema consistency
    and audit-trail integrity.  The database file is created automatically
    on first use.
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        logger.info(f"[STORE] Research database: {self.db_path}")

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    from contextlib import contextmanager

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── strategies ────────────────────────────────────────────────────────

    def save_strategy(self, *, name: str, version: int,
                      strategy_type: str, blueprint: dict,
                      timeframe: str = '', entry_desc: str = '',
                      exit_desc: str = '',
                      status: str = 'candidate') -> str:
        """Register a strategy and return its ID."""
        sid = _new_id()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO strategies
                   (id, name, version, strategy_type, blueprint,
                    timeframe, entry_rules_desc, exit_rules_desc,
                    created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, name, version, strategy_type,
                 json.dumps(blueprint, default=str),
                 timeframe, entry_desc, exit_desc,
                 _now_iso(), status),
            )
        logger.info(f"[STORE] Saved strategy {name} v{version} ({sid})")
        return sid

    def get_strategy(self, strategy_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_strategies(self, status: str = None) -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM strategies WHERE status = ? "
                    "ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM strategies ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def update_strategy_status(self, strategy_id: str, status: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE strategies SET status = ? WHERE id = ?",
                (status, strategy_id),
            )

    # ── experiments ───────────────────────────────────────────────────────

    def create_experiment(self, *, strategy_id: str, strategy_name: str,
                          symbol: str, timeframe: str,
                          config_snapshot: dict,
                          data_start_ms: int = 0,
                          data_end_ms: int = 0,
                          num_candles: int = 0) -> str:
        """Create a pending experiment and return its ID."""
        eid = _new_id()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO experiments
                   (id, strategy_id, strategy_name, symbol, timeframe,
                    data_start_ms, data_end_ms, num_candles,
                    config_snapshot, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (eid, strategy_id, strategy_name, symbol, timeframe,
                 data_start_ms, data_end_ms, num_candles,
                 json.dumps(config_snapshot, default=str),
                 _now_iso()),
            )
        return eid

    def start_experiment(self, experiment_id: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE experiments SET status='running', started_at=? "
                "WHERE id=?",
                (_now_iso(), experiment_id),
            )

    def complete_experiment(self, experiment_id: str,
                            num_candles: int = 0):
        with self._connect() as conn:
            updates = "status='completed', completed_at=?"
            params: list = [_now_iso()]
            if num_candles:
                updates += ", num_candles=?"
                params.append(num_candles)
            params.append(experiment_id)
            conn.execute(
                f"UPDATE experiments SET {updates} WHERE id=?", params,
            )

    def fail_experiment(self, experiment_id: str, error: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE experiments SET status='failed', completed_at=?, "
                "error_message=? WHERE id=?",
                (_now_iso(), error, experiment_id),
            )

    def get_experiment(self, experiment_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_experiments(self, status: str = None,
                         strategy_id: str = None,
                         limit: int = 100) -> list[dict]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM experiments {where} "
                "ORDER BY created_at DESC LIMIT ?", params,
            ).fetchall()
        return [dict(r) for r in rows]

    def count_experiments(self, status: str = None) -> int:
        with self._connect() as conn:
            if status:
                row = conn.execute(
                    "SELECT COUNT(*) FROM experiments WHERE status=?",
                    (status,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM experiments"
                ).fetchone()
        return row[0]

    # ── results ───────────────────────────────────────────────────────────

    def save_result(self, experiment_id: str, period: str,
                    metrics: dict):
        """Persist a single BacktestResult as experiment metrics."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO experiment_results
                   (experiment_id, period, total_trades, winning_trades,
                    losing_trades, win_rate, total_return_pct,
                    max_drawdown_pct, sharpe_ratio, profit_factor,
                    avg_win, avg_loss, starting_balance, ending_balance,
                    passed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (experiment_id, period,
                 metrics.get('total_trades', 0),
                 metrics.get('winning_trades', 0),
                 metrics.get('losing_trades', 0),
                 metrics.get('win_rate', 0),
                 metrics.get('total_return_pct', 0),
                 metrics.get('max_drawdown_pct', 0),
                 metrics.get('sharpe_ratio', 0),
                 metrics.get('profit_factor', 0),
                 metrics.get('avg_win', 0),
                 metrics.get('avg_loss', 0),
                 metrics.get('starting_balance', 10000),
                 metrics.get('ending_balance', 10000),
                 int(metrics.get('passed', False))),
            )

    def get_results(self, experiment_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM experiment_results WHERE experiment_id=?",
                (experiment_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_completed_results(self, limit: int = 500) -> list[dict]:
        """Fetch test-period results for all completed experiments."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT er.*, e.strategy_name, e.symbol, e.timeframe,
                          e.strategy_id
                   FROM experiment_results er
                   JOIN experiments e ON er.experiment_id = e.id
                   WHERE e.status = 'completed' AND er.period = 'test'
                   ORDER BY e.completed_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── research reports ──────────────────────────────────────────────────

    def save_report(self, *, report_type: str, title: str,
                    content: dict, experiment_ids: list[str] = None,
                    suggestions: list[dict] = None) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO research_reports
                   (created_at, report_type, title, content,
                    experiment_ids, suggestions)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_now_iso(), report_type, title,
                 json.dumps(content, default=str),
                 json.dumps(experiment_ids or []),
                 json.dumps(suggestions or [])),
            )
            return cursor.lastrowid

    def list_reports(self, report_type: str = None,
                     limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            if report_type:
                rows = conn.execute(
                    "SELECT * FROM research_reports WHERE report_type=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (report_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM research_reports "
                    "ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

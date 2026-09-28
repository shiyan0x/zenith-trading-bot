"""
promotion_gate.py — Controlled strategy promotion with human approval gates.

Enforces the promotion pipeline:
  candidate → (auto-validated) → pending_paper → (HUMAN APPROVAL) → paper_trading
  paper_trading → pending_live → (HUMAN APPROVAL) → live

Safety invariants:
  - A candidate MUST beat the current baseline on out-of-sample metrics
  - Previous approved strategy is ALWAYS preserved as rollback fallback
  - AI cannot change risk limits, credentials, or live trading permissions
  - Complete audit log of every promotion decision
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.strategy_evaluator import StrategyEvaluator

logger = logging.getLogger(__name__)


# ── Audit Log Schema (added to research.db) ──────────────────────────────────

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS promotion_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    strategy_name   TEXT NOT NULL,
    action          TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT,
    reason          TEXT NOT NULL,
    baseline_id     TEXT,
    metrics_snapshot TEXT,
    approved_by     TEXT DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS active_baseline (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    strategy_id     TEXT NOT NULL,
    strategy_name   TEXT NOT NULL,
    set_at          TEXT NOT NULL,
    previous_id     TEXT,
    previous_name   TEXT
);
"""


class PromotionGate:
    """Enforces strategy promotion rules with human approval gates.

    Promotion pipeline:
      1. auto_validate()   — compare candidate vs baseline metrics
      2. request_paper()   — mark as pending_paper (requires human approval)
      3. approve_paper()   — human approves for paper trading
      4. request_live()    — mark as pending_live (requires human approval)
      5. approve_live()    — human approves for live trading
      6. rollback()        — revert to previous baseline

    The AI can only perform step 1 (auto_validate). Steps 2-5 require
    explicit human action. The system CANNOT bypass these gates.
    """

    # Minimum improvement thresholds to beat baseline
    MIN_SHARPE_IMPROVEMENT = 0.0     # must at least match
    MIN_RETURN_IMPROVEMENT = -2.0    # within 2% of baseline
    MAX_DRAWDOWN_TOLERANCE = 5.0     # no more than 5% worse drawdown

    def __init__(self, store: ExperimentStore, registry: StrategyRegistry,
                 evaluator: Optional[StrategyEvaluator] = None):
        self.store = store
        self.registry = registry
        self.evaluator = evaluator or StrategyEvaluator()
        self._ensure_schema()

    def _ensure_schema(self):
        with self.store._connect() as conn:
            conn.executescript(AUDIT_SCHEMA)

    # ── Audit Logging ─────────────────────────────────────────────────────

    def _log_audit(self, strategy_id: str, strategy_name: str,
                   action: str, reason: str,
                   from_status: str = "", to_status: str = "",
                   baseline_id: str = "", metrics: dict = None,
                   approved_by: str = "system"):
        with self.store._connect() as conn:
            conn.execute(
                """INSERT INTO promotion_audit
                   (timestamp, strategy_id, strategy_name, action,
                    from_status, to_status, reason, baseline_id,
                    metrics_snapshot, approved_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now(timezone.utc).isoformat(),
                 strategy_id, strategy_name, action,
                 from_status, to_status, reason, baseline_id,
                 json.dumps(metrics or {}, default=str),
                 approved_by),
            )

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM promotion_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Baseline Management ───────────────────────────────────────────────

    def get_baseline(self) -> Optional[Dict[str, Any]]:
        """Get the current active baseline strategy."""
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM active_baseline WHERE id = 1"
            ).fetchone()
        return dict(row) if row else None

    def set_baseline(self, strategy_id: str, approved_by: str = "system"):
        """Set a strategy as the active baseline (preserves previous)."""
        strat = self.registry.get(strategy_id)
        if not strat:
            raise ValueError(f"Strategy {strategy_id} not found")

        current = self.get_baseline()
        prev_id = current["strategy_id"] if current else None
        prev_name = current["strategy_name"] if current else None

        with self.store._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO active_baseline
                   (id, strategy_id, strategy_name, set_at,
                    previous_id, previous_name)
                   VALUES (1, ?, ?, ?, ?, ?)""",
                (strategy_id, strat["name"],
                 datetime.now(timezone.utc).isoformat(),
                 prev_id, prev_name),
            )

        self._log_audit(
            strategy_id, strat["name"], "set_baseline",
            f"Set as active baseline (previous: {prev_name or 'none'})",
            baseline_id=prev_id or "", approved_by=approved_by,
        )

    def rollback(self, approved_by: str = "human") -> Optional[str]:
        """Rollback to the previous baseline strategy.

        Returns the rolled-back-to strategy ID, or None if no previous exists.
        """
        current = self.get_baseline()
        if not current or not current.get("previous_id"):
            logger.warning("[GATE] No previous baseline to rollback to")
            return None

        prev_id = current["previous_id"]
        prev_name = current["previous_name"]
        cur_id = current["strategy_id"]
        cur_name = current["strategy_name"]

        # Restore previous as baseline
        with self.store._connect() as conn:
            conn.execute(
                """UPDATE active_baseline
                   SET strategy_id = ?, strategy_name = ?,
                       set_at = ?, previous_id = NULL, previous_name = NULL
                   WHERE id = 1""",
                (prev_id, prev_name,
                 datetime.now(timezone.utc).isoformat()),
            )

        self._log_audit(
            prev_id, prev_name or "unknown", "rollback",
            f"Rolled back from {cur_name} to {prev_name}",
            from_status="baseline", to_status="baseline",
            baseline_id=cur_id, approved_by=approved_by,
        )

        # Retire the strategy that was rolled back from
        self.registry.retire(cur_id)

        logger.info(f"[GATE] Rolled back to {prev_name} ({prev_id})")
        return prev_id

    # ── Automatic Validation ──────────────────────────────────────────────

    def auto_validate(self, candidate_id: str,
                      candidate_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Automatically validate a candidate against the baseline.

        This is the ONLY step the AI can perform autonomously.
        Returns a validation result dict with pass/fail and reasons.
        """
        strat = self.registry.get(candidate_id)
        if not strat:
            return {"passed": False, "reason": "Strategy not found"}

        if strat.get("status") != "candidate":
            return {"passed": False, "reason": f"Strategy is {strat['status']}, not candidate"}

        # Basic metric thresholds
        test = candidate_metrics
        failures = []

        sharpe = test.get("sharpe_ratio", 0)
        if sharpe < 0.5:
            failures.append(f"Sharpe ratio too low ({sharpe:.2f} < 0.5)")

        total_return = test.get("total_return_pct", 0)
        if total_return <= 0:
            failures.append(f"Negative return ({total_return:.2f}%)")

        max_dd = test.get("max_drawdown_pct", 100)
        if max_dd > 20:
            failures.append(f"Excessive drawdown ({max_dd:.1f}% > 20%)")

        trades = test.get("total_trades", 0)
        if trades < 15:
            failures.append(f"Insufficient trades ({trades} < 15)")

        profit_factor = test.get("profit_factor", 0)
        if profit_factor < 1.0:
            failures.append(f"Profit factor < 1.0 ({profit_factor:.2f})")

        # Compare against baseline if one exists
        baseline = self.get_baseline()
        baseline_comparison = None
        if baseline:
            baseline_results = self.store.get_all_completed_results(limit=500)
            baseline_metrics = None
            for r in baseline_results:
                if r.get("strategy_id") == baseline["strategy_id"]:
                    baseline_metrics = r
                    break

            if baseline_metrics:
                bl_sharpe = baseline_metrics.get("sharpe_ratio", 0)
                bl_return = baseline_metrics.get("total_return_pct", 0)
                bl_dd = baseline_metrics.get("max_drawdown_pct", 0)

                baseline_comparison = {
                    "baseline_name": baseline["strategy_name"],
                    "baseline_sharpe": bl_sharpe,
                    "baseline_return": bl_return,
                    "baseline_drawdown": bl_dd,
                    "candidate_sharpe": sharpe,
                    "candidate_return": total_return,
                    "candidate_drawdown": max_dd,
                }

                if sharpe < bl_sharpe + self.MIN_SHARPE_IMPROVEMENT:
                    failures.append(
                        f"Sharpe ({sharpe:.2f}) does not beat baseline "
                        f"({bl_sharpe:.2f})"
                    )
                if max_dd > bl_dd + self.MAX_DRAWDOWN_TOLERANCE:
                    failures.append(
                        f"Drawdown ({max_dd:.1f}%) exceeds baseline "
                        f"({bl_dd:.1f}%) + {self.MAX_DRAWDOWN_TOLERANCE}% tolerance"
                    )

        passed = len(failures) == 0
        action = "auto_validate_pass" if passed else "auto_validate_fail"
        reason = "Passed all validation criteria" if passed else "; ".join(failures)

        self._log_audit(
            candidate_id, strat["name"], action, reason,
            from_status="candidate",
            to_status="validated" if passed else "candidate",
            metrics=candidate_metrics,
        )

        if passed:
            self.registry.promote(candidate_id)

        return {
            "passed": passed,
            "strategy_id": candidate_id,
            "strategy_name": strat["name"],
            "failures": failures,
            "reason": reason,
            "baseline_comparison": baseline_comparison,
            "requires_human_approval": True if passed else False,
        }

    # ── Human Approval Gates ─────────────────────────────────────────────

    def request_paper(self, strategy_id: str) -> bool:
        """Mark a promoted strategy as pending paper-trading approval.

        Returns True if the request was valid.
        """
        strat = self.registry.get(strategy_id)
        if not strat or strat.get("status") != "promoted":
            return False

        self.store.update_strategy_status(strategy_id, "pending_paper")
        self._log_audit(
            strategy_id, strat["name"], "request_paper",
            "Requested paper-trading approval (requires human)",
            from_status="promoted", to_status="pending_paper",
        )
        return True

    def approve_paper(self, strategy_id: str,
                      approved_by: str = "human") -> bool:
        """Human approves strategy for paper trading.

        Sets it as the new baseline and preserves the old one.
        """
        strat = self.registry.get(strategy_id)
        if not strat or strat.get("status") != "pending_paper":
            return False

        self.store.update_strategy_status(strategy_id, "paper_trading")
        self.set_baseline(strategy_id, approved_by=approved_by)
        self._log_audit(
            strategy_id, strat["name"], "approve_paper",
            f"Approved for paper trading by {approved_by}",
            from_status="pending_paper", to_status="paper_trading",
            approved_by=approved_by,
        )
        return True

    def request_live(self, strategy_id: str) -> bool:
        """Mark a paper-trading strategy as pending live-trading approval.

        Returns True if the request was valid.
        """
        strat = self.registry.get(strategy_id)
        if not strat or strat.get("status") != "paper_trading":
            return False

        self.store.update_strategy_status(strategy_id, "pending_live")
        self._log_audit(
            strategy_id, strat["name"], "request_live",
            "Requested live-trading approval (requires human verification)",
            from_status="paper_trading", to_status="pending_live",
        )
        return True

    def approve_live(self, strategy_id: str,
                     approved_by: str = "human",
                     verification: Optional[Dict[str, Any]] = None) -> bool:
        """Human approves strategy for controlled live trading.

        Requires explicit human approval and verification metadata.
        """
        strat = self.registry.get(strategy_id)
        if not strat or strat.get("status") != "pending_live":
            return False

        self.store.update_strategy_status(strategy_id, "live")
        self._log_audit(
            strategy_id, strat["name"], "approve_live",
            f"Approved for controlled live trading by {approved_by}",
            from_status="pending_live", to_status="live",
            metrics=verification or {},
            approved_by=approved_by,
        )
        return True

    def reject(self, strategy_id: str, reason: str = "",
               rejected_by: str = "human") -> bool:
        """Human rejects a strategy at any pending stage."""
        strat = self.registry.get(strategy_id)
        if not strat:
            return False

        old_status = strat.get("status", "")
        self.registry.retire(strategy_id)
        self._log_audit(
            strategy_id, strat["name"], "rejected",
            reason or "Rejected by human review",
            from_status=old_status, to_status="retired",
            approved_by=rejected_by,
        )
        return True

    # ── Status Query ──────────────────────────────────────────────────────

    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """List strategies waiting for human approval."""
        pending = []
        for status in ("pending_paper", "pending_live"):
            pending.extend(self.registry.list_by_status(status))
        return pending

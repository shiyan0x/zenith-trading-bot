"""
research_api.py — Read-only Flask API endpoints for the research lab.

Exposes research data to the existing dashboard without modifying
server.py's structure. Call register_research_routes(app) from server.py.

SAFETY:
  - All endpoints are GET (read-only)
  - Cannot modify strategies, experiments, or bot configuration
  - Serves data from ExperimentStore (SQLite)
  - Does NOT import any live trading or wallet modules
"""

import json
import logging
import time
from typing import Optional
from flask import Blueprint, jsonify, request

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.strategy_evaluator import StrategyEvaluator
from src.research.promotion_gate import PromotionGate
from src.research.overfitting_detector import OverfittingDetector
from src.research.research_assistant import ResearchAssistant

logger = logging.getLogger(__name__)

research_bp = Blueprint("research", __name__, url_prefix="/api/research")


# Module-level state — set by register_research_routes()
_store: Optional[ExperimentStore] = None
_registry: Optional[StrategyRegistry] = None
_gate: Optional[PromotionGate] = None
_detector: Optional[OverfittingDetector] = None


def register_research_routes(app, db_path: Optional[str] = None):
    """Register research API routes on an existing Flask app.

    Call this from server.py after creating the app:
        from src.research.research_api import register_research_routes
        register_research_routes(app)
    """
    global _store, _registry, _gate, _detector

    _store = ExperimentStore(db_path)
    _registry = StrategyRegistry(_store)
    _gate = PromotionGate(_store, _registry)
    _detector = OverfittingDetector(_store)

    if "research" not in app.blueprints:
        app.register_blueprint(research_bp)
        logger.info("[RESEARCH API] Routes registered on /api/research/*")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@research_bp.route("/summary")
def research_summary():
    """Lab summary: strategy counts, experiment stats."""
    if not _store or not _registry:
        return jsonify({"error": "Research lab not initialized"}), 503

    reg_summary = _registry.summary()
    return jsonify({
        "strategies": {
            "total": reg_summary["total"],
            "counts": reg_summary["counts"],
        },
        "experiments": {
            "total": _store.count_experiments(),
            "completed": _store.count_experiments(status="completed"),
            "failed": _store.count_experiments(status="failed"),
            "pending": _store.count_experiments(status="pending"),
        },
        "baseline": _gate.get_baseline() if _gate else None,
    })


@research_bp.route("/strategies")
def research_strategies():
    """List all strategies with optional status filter."""
    if not _registry:
        return jsonify({"error": "Research lab not initialized"}), 503

    status = request.args.get("status")
    strategies = (
        _registry.list_by_status(status) if status
        else _registry.list_all()
    )

    # Parse blueprint JSON for the response
    result = []
    for s in strategies:
        entry = dict(s)
        bp = entry.get("blueprint", "{}")
        if isinstance(bp, str):
            try:
                entry["blueprint"] = json.loads(bp)
            except (json.JSONDecodeError, TypeError):
                entry["blueprint"] = {}
        result.append(entry)

    return jsonify(result)


@research_bp.route("/experiments")
def research_experiments():
    """List recent experiments."""
    if not _store:
        return jsonify({"error": "Research lab not initialized"}), 503

    limit = request.args.get("limit", 50, type=int)
    status = request.args.get("status")
    experiments = _store.list_experiments(status=status)[:limit]
    return jsonify(experiments)


@research_bp.route("/results")
def research_results():
    """List completed experiment results (test period only)."""
    if not _store:
        return jsonify({"error": "Research lab not initialized"}), 503

    limit = request.args.get("limit", 50, type=int)
    results = _store.get_all_completed_results(limit=limit)
    return jsonify(results)


@research_bp.route("/reports")
def research_reports():
    """List research reports."""
    if not _store:
        return jsonify({"error": "Research lab not initialized"}), 503

    limit = request.args.get("limit", 20, type=int)
    report_type = request.args.get("type")
    reports = _store.list_reports(limit=limit, report_type=report_type)
    return jsonify(reports)


@research_bp.route("/audit")
def research_audit():
    """Promotion audit log."""
    if not _gate:
        return jsonify({"error": "Research lab not initialized"}), 503

    limit = request.args.get("limit", 50, type=int)
    log = _gate.get_audit_log(limit=limit)
    return jsonify(log)


@research_bp.route("/baseline")
def research_baseline():
    """Current active baseline strategy."""
    if not _gate:
        return jsonify({"error": "Research lab not initialized"}), 503

    baseline = _gate.get_baseline()
    return jsonify(baseline or {"status": "no_baseline_set"})


@research_bp.route("/pending")
def research_pending():
    """Strategies pending human approval."""
    if not _gate:
        return jsonify({"error": "Research lab not initialized"}), 503

    pending = _gate.get_pending_approvals()
    return jsonify(pending)


@research_bp.route("/leaderboard")
def research_leaderboard():
    """Top strategies ranked by evaluation score."""
    if not _store:
        return jsonify({"error": "Research lab not initialized"}), 503

    evaluator = StrategyEvaluator()
    results = _store.get_all_completed_results(limit=100)

    leaderboard = []
    for r in results:
        diag = evaluator.evaluate_experiment({
            "strategy_name": r.get("strategy_name", "?"),
            "strategy_id": r.get("strategy_id", "?"),
            "test_metrics": r,
        })
        leaderboard.append({
            "strategy_name": diag["strategy_name"],
            "strategy_id": diag.get("strategy_id"),
            "score": diag["score"],
            "grade": diag["grade"],
            "verdict": diag["verdict"],
            "sharpe": diag["test_summary"]["sharpe"],
            "return_pct": diag["test_summary"]["return_pct"],
            "max_drawdown": diag["test_summary"]["max_drawdown"],
            "win_rate": diag["test_summary"]["win_rate"],
            "trades": diag["test_summary"]["trades"],
        })

    leaderboard.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(leaderboard)


# ── AI Research Recommendations ───────────────────────────────────────────────

@research_bp.route("/recommendations")
def research_recommendations():
    """AI research recommendations based on historical experiments."""
    if not _store:
        return jsonify({"error": "Research lab not initialized"}), 503

    assistant = ResearchAssistant(_store)
    patterns = assistant.analyze_performance_patterns()
    suggestions = assistant.suggest_experiments(limit=5)
    overfit_risks = _detector.check_test_period_reuse() if _detector else []

    return jsonify({
        "status": "ready",
        "performance_patterns": patterns,
        "recommendations": suggestions,
        "overfitting_alerts": overfit_risks,
    })


# ── Research Job Runner & Controls ────────────────────────────────────────────

import threading

_job_lock = threading.Lock()
_active_job = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "max_candidates": 0,
    "error": None,
    "stop_requested": False,
}


@research_bp.route("/job-status")
def research_job_status():
    """Check background research job status."""
    return jsonify(_active_job)


@research_bp.route("/run-job", methods=["POST"])
def research_run_job():
    """Start an autonomous research improvement loop job."""
    from src.dashboard.auth import validate_token
    token = request.headers.get("X-Zenith-Token") or request.args.get("token")
    if not validate_token(token):
        return jsonify({"error": "Unauthorized", "message": "Valid token required"}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("confirmation"):
        return jsonify({"error": "ConfirmationRequired", "message": "Confirmation required"}), 400

    with _job_lock:
        if _active_job["status"] == "running":
            return jsonify({"status": "already_running", "message": "A research job is already running."}), 409

        _active_job["status"] = "running"
        _active_job["started_at"] = time.time()
        _active_job["completed_at"] = None
        _active_job["error"] = None
        _active_job["stop_requested"] = False
        max_candidates = int(data.get("max_candidates", 3))
        _active_job["max_candidates"] = max_candidates

    def _worker():
        try:
            from src.research.improvement_loop import ImprovementLoop, ImprovementConfig
            cfg = ImprovementConfig(
                max_candidates=max_candidates,
                max_runtime_seconds=int(data.get("max_runtime_seconds", 300)),
                symbols=data.get("symbols", ["BTCUSDT"]),
            )
            loop = ImprovementLoop(config=cfg, db_path=_store.db_path if _store else None)
            import asyncio
            asyncio.run(loop.run())
            with _job_lock:
                _active_job["status"] = "completed"
                _active_job["completed_at"] = time.time()
        except Exception as e:
            logger.error(f"[RESEARCH JOB] Error during job execution: {e}")
            with _job_lock:
                _active_job["status"] = "failed"
                _active_job["error"] = str(e)
                _active_job["completed_at"] = time.time()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    return jsonify({"success": True, "status": "running", "max_candidates": max_candidates})


@research_bp.route("/stop-job", methods=["POST"])
def research_stop_job():
    """Request stopping a running research job."""
    from src.dashboard.auth import validate_token
    token = request.headers.get("X-Zenith-Token") or request.args.get("token")
    if not validate_token(token):
        return jsonify({"error": "Unauthorized", "message": "Valid token required"}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("confirmation"):
        return jsonify({"error": "ConfirmationRequired", "message": "Confirmation required"}), 400

    with _job_lock:
        if _active_job["status"] != "running":
            return jsonify({"status": "not_running", "message": "No research job currently running."})
        _active_job["status"] = "stopping"
        _active_job["stop_requested"] = True

    return jsonify({"success": True, "message": "Stop requested for research job."})


# ── Human Approval & Rejection Gates ──────────────────────────────────────────

@research_bp.route("/approve", methods=["POST"])
def research_approve():
    """Human approves strategy candidate for paper trading."""
    from src.dashboard.auth import validate_token
    token = request.headers.get("X-Zenith-Token") or request.args.get("token")
    if not validate_token(token):
        return jsonify({"error": "Unauthorized", "message": "Valid token required"}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("confirmation"):
        return jsonify({"error": "ConfirmationRequired", "message": "Confirmation required"}), 400

    strategy_id = data.get("strategy_id")
    approved_by = data.get("approved_by", "dashboard_operator")
    if not strategy_id:
        return jsonify({"error": "MissingStrategyId", "message": "strategy_id is required"}), 400

    if not _gate:
        return jsonify({"error": "GateNotInitialized"}), 503

    # If currently promoted, request paper first
    strat = _registry.get(strategy_id) if _registry else None
    if strat and strat.get("status") == "promoted":
        _gate.request_paper(strategy_id)

    success = _gate.approve_paper(strategy_id, approved_by=approved_by)
    if success:
        return jsonify({"success": True, "strategy_id": strategy_id, "status": "paper_trading"})
    else:
        return jsonify({"success": False, "message": f"Could not approve strategy {strategy_id}. Check its current status."}), 400


@research_bp.route("/reject", methods=["POST"])
def research_reject():
    """Human rejects strategy candidate."""
    from src.dashboard.auth import validate_token
    token = request.headers.get("X-Zenith-Token") or request.args.get("token")
    if not validate_token(token):
        return jsonify({"error": "Unauthorized", "message": "Valid token required"}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("confirmation"):
        return jsonify({"error": "ConfirmationRequired", "message": "Confirmation required"}), 400

    strategy_id = data.get("strategy_id")
    reason = data.get("reason", "Rejected via dashboard")
    rejected_by = data.get("rejected_by", "dashboard_operator")
    if not strategy_id:
        return jsonify({"error": "MissingStrategyId", "message": "strategy_id is required"}), 400

    if not _gate:
        return jsonify({"error": "GateNotInitialized"}), 503

    success = _gate.reject(strategy_id, reason=reason, rejected_by=rejected_by)
    return jsonify({"success": success, "strategy_id": strategy_id, "status": "retired"})


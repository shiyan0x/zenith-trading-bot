"""
test_improvement_loop.py — Comprehensive tests for Stage 4: Self-Improvement Loop.

Tests cover:
  1. Promotion Gate — baseline comparison, human approval gates, rollback
  2. Overfitting Detector — train/test divergence, test-period reuse, saturation
  3. Improvement Loop — full cycle with synthetic data
  4. Failed candidate rejection — cannot replace baseline
  5. Audit trail completeness
  6. Live execution path isolation
  7. Dashboard research API endpoints (read-only)
"""

import os
import json
import tempfile
import pytest
from datetime import datetime, timezone

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.strategy_generator import StrategyGenerator
from src.research.strategy_evaluator import StrategyEvaluator
from src.research.promotion_gate import PromotionGate
from src.research.overfitting_detector import OverfittingDetector
from src.research.improvement_loop import ImprovementLoop, ImprovementConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_loop.db")
        store = ExperimentStore(db_path=db_path)
        yield store


@pytest.fixture
def registry(temp_store):
    return StrategyRegistry(temp_store)


@pytest.fixture
def gate(temp_store, registry):
    return PromotionGate(temp_store, registry)


@pytest.fixture
def detector(temp_store):
    return OverfittingDetector(temp_store)


def _create_test_strategy(temp_store, registry, name="TestStrat", status="candidate"):
    """Helper to register a test strategy and return its ID."""
    gen = StrategyGenerator(registry, seed=42)
    bp = gen.generate_trend_following()
    bp["name"] = name
    strat_id = gen.register_candidate(bp)
    if status != "candidate":
        temp_store.update_strategy_status(strat_id, status)
    return strat_id


def _good_metrics():
    """Metrics that should pass validation."""
    return {
        "total_trades": 30,
        "sharpe_ratio": 1.5,
        "total_return_pct": 15.0,
        "max_drawdown_pct": 8.0,
        "win_rate": 0.55,
        "profit_factor": 1.8,
        "avg_win": 120.0,
        "avg_loss": 70.0,
    }


def _bad_metrics():
    """Metrics that should fail validation."""
    return {
        "total_trades": 5,
        "sharpe_ratio": -0.5,
        "total_return_pct": -10.0,
        "max_drawdown_pct": 25.0,
        "win_rate": 0.2,
        "profit_factor": 0.4,
        "avg_win": 10.0,
        "avg_loss": 50.0,
    }


# ── 1. Promotion Gate — Baseline & Validation ────────────────────────────────

def test_gate_auto_validate_pass(temp_store, registry, gate):
    """Good candidate passes auto-validation and gets promoted."""
    strat_id = _create_test_strategy(temp_store, registry, "GoodStrat")
    result = gate.auto_validate(strat_id, _good_metrics())
    assert result["passed"] is True
    assert result["requires_human_approval"] is True
    # Strategy should now be promoted
    strat = registry.get(strat_id)
    assert strat["status"] == "promoted"


def test_gate_auto_validate_fail(temp_store, registry, gate):
    """Bad candidate fails auto-validation and stays candidate."""
    strat_id = _create_test_strategy(temp_store, registry, "BadStrat")
    result = gate.auto_validate(strat_id, _bad_metrics())
    assert result["passed"] is False
    assert len(result["failures"]) > 0
    # Strategy should remain candidate
    strat = registry.get(strat_id)
    assert strat["status"] == "candidate"


def test_gate_baseline_management(temp_store, registry, gate):
    """Setting and getting baseline strategy."""
    strat_id = _create_test_strategy(temp_store, registry, "Baseline1", "promoted")
    gate.set_baseline(strat_id, approved_by="test")

    baseline = gate.get_baseline()
    assert baseline is not None
    assert baseline["strategy_id"] == strat_id
    assert baseline["strategy_name"] == "Baseline1"


def test_gate_baseline_preserves_previous(temp_store, registry, gate):
    """Setting a new baseline preserves the old one for rollback."""
    id1 = _create_test_strategy(temp_store, registry, "BaseA", "promoted")
    gate.set_baseline(id1, approved_by="test")

    id2 = _create_test_strategy(temp_store, registry, "BaseB", "promoted")
    gate.set_baseline(id2, approved_by="test")

    baseline = gate.get_baseline()
    assert baseline["strategy_id"] == id2
    assert baseline["previous_id"] == id1
    assert baseline["previous_name"] == "BaseA"


def test_gate_rollback(temp_store, registry, gate):
    """Rollback reverts to previous baseline and retires current."""
    id1 = _create_test_strategy(temp_store, registry, "RollA", "promoted")
    gate.set_baseline(id1, approved_by="test")

    id2 = _create_test_strategy(temp_store, registry, "RollB", "promoted")
    gate.set_baseline(id2, approved_by="test")

    rolled_back_id = gate.rollback(approved_by="test_user")
    assert rolled_back_id == id1

    new_baseline = gate.get_baseline()
    assert new_baseline["strategy_id"] == id1

    # Old current should be retired
    retired = registry.get(id2)
    assert retired["status"] == "retired"


def test_gate_rollback_no_previous(temp_store, registry, gate):
    """Rollback returns None when there's no previous baseline."""
    id1 = _create_test_strategy(temp_store, registry, "Only1", "promoted")
    gate.set_baseline(id1, approved_by="test")

    result = gate.rollback()
    assert result is None


# ── 2. Human Approval Gates ──────────────────────────────────────────────────

def test_gate_paper_approval_pipeline(temp_store, registry, gate):
    """Full pipeline: candidate -> promoted -> pending_paper -> paper_trading."""
    strat_id = _create_test_strategy(temp_store, registry, "PaperTest")
    gate.auto_validate(strat_id, _good_metrics())
    assert registry.get(strat_id)["status"] == "promoted"

    assert gate.request_paper(strat_id) is True
    assert registry.get(strat_id)["status"] == "pending_paper"

    assert gate.approve_paper(strat_id, approved_by="trader_john") is True
    assert registry.get(strat_id)["status"] == "paper_trading"


def test_gate_cannot_approve_unpromoted(temp_store, registry, gate):
    """Cannot request paper trading for a non-promoted strategy."""
    strat_id = _create_test_strategy(temp_store, registry, "NotPromoted")
    assert gate.request_paper(strat_id) is False


def test_gate_rejection(temp_store, registry, gate):
    """Human can reject a strategy at any stage."""
    strat_id = _create_test_strategy(temp_store, registry, "Rejected")
    gate.auto_validate(strat_id, _good_metrics())
    gate.request_paper(strat_id)

    assert gate.reject(strat_id, reason="Too risky", rejected_by="trader_bob") is True
    assert registry.get(strat_id)["status"] == "retired"


def test_gate_pending_approvals(temp_store, registry, gate):
    """List strategies pending human approval."""
    id1 = _create_test_strategy(temp_store, registry, "Pending1")
    gate.auto_validate(id1, _good_metrics())
    gate.request_paper(id1)

    pending = gate.get_pending_approvals()
    assert len(pending) >= 1
    assert any(p["id"] == id1 for p in pending)


# ── 3. Audit Trail ───────────────────────────────────────────────────────────

def test_audit_log_records_all_actions(temp_store, registry, gate):
    """Audit log captures every promotion action."""
    strat_id = _create_test_strategy(temp_store, registry, "AuditTest")
    gate.auto_validate(strat_id, _good_metrics())
    gate.request_paper(strat_id)
    gate.approve_paper(strat_id, approved_by="auditor")

    log = gate.get_audit_log()
    actions = [entry["action"] for entry in log]
    # Should have: auto_validate_pass, set_baseline, request_paper, approve_paper
    assert "auto_validate_pass" in actions
    assert "request_paper" in actions
    assert "approve_paper" in actions
    assert "set_baseline" in actions


def test_audit_log_records_rejections(temp_store, registry, gate):
    """Audit log captures rejections with reasons."""
    strat_id = _create_test_strategy(temp_store, registry, "RejAudit")
    gate.auto_validate(strat_id, _bad_metrics())

    log = gate.get_audit_log()
    fail_entries = [e for e in log if e["action"] == "auto_validate_fail"]
    assert len(fail_entries) >= 1
    assert fail_entries[0]["strategy_name"] == "RejAudit"


# ── 4. Overfitting Detector ──────────────────────────────────────────────────

def test_overfit_severe_sharpe_collapse(detector):
    """Detect severe overfitting from Sharpe collapse."""
    result = detector.check_train_test_divergence(
        train_metrics={"sharpe_ratio": 3.0, "total_return_pct": 30, "max_drawdown_pct": 5},
        test_metrics={"sharpe_ratio": 0.5, "total_return_pct": 2, "max_drawdown_pct": 15},
    )
    assert result["overfit_detected"] is True
    assert result["severity"] == "severe"
    assert any(f["type"] == "sharpe_collapse" for f in result["flags"])


def test_overfit_moderate_degradation(detector):
    """Detect moderate overfitting from Sharpe degradation."""
    result = detector.check_train_test_divergence(
        train_metrics={"sharpe_ratio": 2.0, "total_return_pct": 20, "max_drawdown_pct": 8},
        test_metrics={"sharpe_ratio": 1.2, "total_return_pct": 12, "max_drawdown_pct": 10},
    )
    assert result["overfit_detected"] is True
    assert result["severity"] == "moderate"


def test_overfit_no_detection_good_strategy(detector):
    """No overfitting detected for a strategy with consistent performance."""
    result = detector.check_train_test_divergence(
        train_metrics={"sharpe_ratio": 1.5, "total_return_pct": 15, "max_drawdown_pct": 8},
        test_metrics={"sharpe_ratio": 1.3, "total_return_pct": 12, "max_drawdown_pct": 9},
    )
    assert result["overfit_detected"] is False
    assert result["severity"] == "none"


def test_overfit_return_sign_flip(detector):
    """Detect return sign flip (positive train, negative test)."""
    result = detector.check_train_test_divergence(
        train_metrics={"sharpe_ratio": 0.3, "total_return_pct": 10, "max_drawdown_pct": 5},
        test_metrics={"sharpe_ratio": -0.5, "total_return_pct": -8, "max_drawdown_pct": 15},
    )
    assert result["overfit_detected"] is True
    assert any(f["type"] == "return_sign_flip" for f in result["flags"])


def test_overfit_test_period_reuse(temp_store, detector):
    """Detect excessive reuse of the same test period."""
    # Create many experiments on the same symbol+timeframe
    registry = StrategyRegistry(temp_store)
    gen = StrategyGenerator(registry, seed=42)

    for i in range(8):
        bp = gen.generate_random_candidate()
        bp["name"] = f"Reuse_{i}"
        strat_id = gen.register_candidate(bp)
        exp_id = temp_store.create_experiment(
            strategy_id=strat_id,
            strategy_name=bp["name"],
            symbol="BTCUSDT",
            timeframe="1h",
            config_snapshot={},
        )
        temp_store.complete_experiment(exp_id)

    result = detector.check_test_period_reuse("BTCUSDT", "1h")
    assert result["reuse_detected"] is True
    assert result["test_count"] >= 8


def test_overfit_archetype_saturation(temp_store, detector):
    """Detect when too many variants of one archetype are created."""
    registry = StrategyRegistry(temp_store)
    gen = StrategyGenerator(registry, seed=42)

    for i in range(12):
        bp = gen.generate_trend_following()
        bp["name"] = f"TrendSat_{i}"
        gen.register_candidate(bp)

    result = detector.check_archetype_saturation()
    assert result["saturation_detected"] is True
    assert "trend_following" in result["saturated_archetypes"]


def test_overfit_full_diagnostic_saves_report(temp_store, detector):
    """Full diagnostic saves report to ExperimentStore."""
    result = detector.run_full_diagnostic(
        train_metrics={"sharpe_ratio": 3.0, "total_return_pct": 30, "max_drawdown_pct": 5},
        test_metrics={"sharpe_ratio": 0.3, "total_return_pct": -5, "max_drawdown_pct": 20},
    )
    assert result["overall_risk"] in ("low", "medium", "high")

    reports = temp_store.list_reports(report_type="overfitting_diagnostic")
    assert len(reports) >= 1


# ── 5. Failed Candidate Cannot Replace Baseline ─────────────────────────────

def test_failed_candidate_cannot_become_baseline(temp_store, registry, gate):
    """A candidate that fails validation CANNOT replace the baseline."""
    # Set a baseline
    good_id = _create_test_strategy(temp_store, registry, "GoodBaseline", "promoted")
    gate.set_baseline(good_id, approved_by="test")

    # Try to promote a bad candidate
    bad_id = _create_test_strategy(temp_store, registry, "BadCandidate")
    result = gate.auto_validate(bad_id, _bad_metrics())
    assert result["passed"] is False

    # Baseline should be unchanged
    baseline = gate.get_baseline()
    assert baseline["strategy_id"] == good_id
    assert baseline["strategy_name"] == "GoodBaseline"


def test_candidate_beaten_by_baseline(temp_store, registry, gate):
    """Candidate that doesn't beat baseline Sharpe is rejected."""
    # Set baseline with good metrics
    base_id = _create_test_strategy(temp_store, registry, "StrongBase", "promoted")
    gate.set_baseline(base_id, approved_by="test")

    # Create experiment result for baseline
    exp_id = temp_store.create_experiment(
        strategy_id=base_id,
        strategy_name="StrongBase",
        symbol="BTCUSDT",
        timeframe="1h",
        config_snapshot={},
    )
    temp_store.start_experiment(exp_id)
    temp_store.save_result(exp_id, "test", {
        "total_trades": 40,
        "sharpe_ratio": 2.5,
        "total_return_pct": 25.0,
        "max_drawdown_pct": 6.0,
        "win_rate": 0.6,
        "profit_factor": 2.0,
    })
    temp_store.complete_experiment(exp_id)

    # Candidate with decent but inferior metrics
    cand_id = _create_test_strategy(temp_store, registry, "WeakerCand")
    result = gate.auto_validate(cand_id, {
        "total_trades": 25,
        "sharpe_ratio": 1.8,
        "total_return_pct": 12.0,
        "max_drawdown_pct": 10.0,
        "win_rate": 0.5,
        "profit_factor": 1.4,
    })
    assert result["passed"] is False
    assert result["baseline_comparison"] is not None
    assert any("baseline" in f.lower() for f in result["failures"])


# ── 6. Live Execution Path Isolation ─────────────────────────────────────────

def test_stage4_modules_no_order_engine():
    """Stage 4 modules must not import order_engine or paper_wallet."""
    import importlib

    stage4_modules = [
        "src.research.promotion_gate",
        "src.research.overfitting_detector",
        "src.research.improvement_loop",
        "src.research.research_api",
    ]
    for modname in stage4_modules:
        mod = importlib.import_module(modname)
        source_file = mod.__file__
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        assert "order_engine" not in source, (
            f"{modname} must not import order_engine"
        )
        assert "paper_wallet" not in source, (
            f"{modname} must not import paper_wallet"
        )


def test_promotion_gate_cannot_modify_risk_limits(temp_store, registry, gate):
    """PromotionGate has no methods to change risk limits or credentials."""
    import inspect
    methods = [name for name, _ in inspect.getmembers(gate, predicate=inspect.ismethod)]
    forbidden = ["set_risk", "change_risk", "update_risk", "set_credentials", "set_api_key"]
    for method in methods:
        assert method not in forbidden, (
            f"PromotionGate has forbidden method: {method}"
        )


# ── 7. Dashboard Research API Isolation ──────────────────────────────────────

def test_research_api_is_read_only():
    """Research API only uses GET endpoints (read-only)."""
    from src.research.research_api import research_bp

    # All URL rules registered to the blueprint
    # Blueprint deferred_functions hasn't been applied yet, 
    # so check the source directly
    import inspect
    source = inspect.getsource(research_bp.__class__)
    # Check the module source instead
    with open(research_bp.import_name.replace(".", os.sep) + ".py", "r", encoding="utf-8") as f:
        pass  # file exists check

    # Better: check that research_api.py has no POST/PUT/DELETE decorators
    import src.research.research_api as api_mod
    api_source = inspect.getsource(api_mod)
    assert "methods=[" not in api_source or "'POST'" not in api_source
    assert "@research_bp.route" in api_source  # has routes
    assert "DELETE" not in api_source
    assert "PUT" not in api_source


# ── 8. Schema Integrity with New Statuses ────────────────────────────────────

def test_new_status_values_accepted(temp_store, registry):
    """New promotion statuses are accepted by the database schema."""
    gen = StrategyGenerator(registry, seed=42)
    bp = gen.generate_trend_following()
    bp["name"] = "StatusTest"
    strat_id = gen.register_candidate(bp)

    for status in ["candidate", "promoted", "pending_paper", "paper_trading", "retired"]:
        temp_store.update_strategy_status(strat_id, status)
        strat = registry.get(strat_id)
        assert strat["status"] == status


def test_invalid_status_rejected(temp_store, registry):
    """Invalid status values are rejected by the database constraint."""
    gen = StrategyGenerator(registry, seed=42)
    bp = gen.generate_mean_reversion()
    bp["name"] = "InvalidStatusTest"
    strat_id = gen.register_candidate(bp)

    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        temp_store.update_strategy_status(strat_id, "HACKED")

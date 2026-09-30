"""
test_research_lab.py — Comprehensive tests for the AI Strategy Research Lab.

Tests cover:
  1. Strategy creation and registry lifecycle
  2. Blueprint validation and parameter limits
  3. Generated strategy execution safety (zero eval/exec)
  4. Experiment reproducibility via seed propagation
  5. Experiment Manager budget enforcement and deduplication
  6. AI Research Assistant suggestions within approved boundaries
  7. Report persistence (SQLite + filesystem)
  8. Live trading isolation: research cannot place orders
"""

import os
import json
import tempfile
import pytest
from datetime import datetime, timezone

from src.core.market_feed import Candle
from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.strategy_generator import StrategyGenerator
from src.research.generated_strategy import (
    GeneratedStrategy,
    validate_blueprint,
    APPROVED_INDICATORS,
    APPROVED_OPS,
)
from src.research.strategy_evaluator import StrategyEvaluator
from src.research.experiment_manager import ExperimentManager, ExperimentBudget
from src.research.research_assistant import ResearchAssistant
from src.research.report_generator import ReportGenerator


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_research.db")
        store = ExperimentStore(db_path=db_path)
        yield store


@pytest.fixture
def registry(temp_store):
    return StrategyRegistry(temp_store)


@pytest.fixture
def generator(registry):
    return StrategyGenerator(registry, seed=42)


# ── 1. Strategy Registry & Lifecycle ─────────────────────────────────────────

def test_registry_legacy_bootstrap(temp_store):
    """Legacy strategies (EMA_VWAP_RSI, Mean_Reversion) are auto-registered."""
    registry = StrategyRegistry(temp_store)
    strategies = registry.list_all()
    names = {s["name"] for s in strategies}
    assert "EMA_VWAP_RSI" in names
    assert "Mean_Reversion" in names
    assert len(strategies) == 2


def test_registry_lifecycle(temp_store):
    """Strategy lifecycle: register -> candidate -> promote -> retire."""
    registry = StrategyRegistry(temp_store)
    gen = StrategyGenerator(registry, seed=99)
    bp = gen.generate_trend_following()
    strat_id = gen.register_candidate(bp)

    strat = registry.get(strat_id)
    assert strat is not None
    assert strat["status"] == "candidate"

    registry.promote(strat_id)
    assert registry.get(strat_id)["status"] == "promoted"

    registry.retire(strat_id)
    assert registry.get(strat_id)["status"] == "retired"


def test_registry_version_increment(temp_store):
    """Registering same strategy name increments version."""
    registry = StrategyRegistry(temp_store)
    gen = StrategyGenerator(registry, seed=55)

    bp1 = gen.generate_trend_following(name_prefix="TestStrat")
    bp1["name"] = "VersionTest"
    gen.register_candidate(bp1)
    assert registry.next_version("VersionTest") == 2

    bp2 = gen.generate_trend_following(name_prefix="TestStrat")
    bp2["name"] = "VersionTest"
    gen.register_candidate(bp2)
    assert registry.next_version("VersionTest") == 3


# ── 2. Blueprint Validation & Parameter Limits ──────────────────────────────

def test_blueprint_validation_valid():
    """A correctly generated blueprint passes validation."""
    gen = StrategyGenerator(seed=42)
    for fn in [gen.generate_trend_following, gen.generate_mean_reversion, gen.generate_momentum_breakout]:
        bp = fn()
        errors = validate_blueprint(bp)
        assert errors == [], f"Validation failed for {bp.get('name')}: {errors}"


def test_blueprint_validation_rejects_unapproved_indicator():
    """Blueprints with unapproved indicator types are rejected."""
    bad_bp = {
        "name": "Malicious_Strat",
        "indicators": {"hack": {"type": "arbitrary_eval_code"}},
        "entry_long": [{"left": "close", "op": "above", "right": 100}],
        "exit": {"stop_loss_atr_mult": 2.0},
    }
    errors = validate_blueprint(bad_bp)
    assert len(errors) > 0
    assert any("unapproved type" in e for e in errors)


def test_blueprint_validation_rejects_unapproved_operator():
    """Blueprints with unapproved condition operators are rejected."""
    bad_bp = {
        "name": "Bad_Op_Strat",
        "indicators": {"rsi": {"type": "rsi", "period": 14}},
        "entry_long": [{"left": "rsi", "op": "exec_code", "right": 50}],
        "exit": {"stop_loss_atr_mult": 2.0},
    }
    errors = validate_blueprint(bad_bp)
    assert any("unapproved op" in e for e in errors)


def test_blueprint_validation_rejects_missing_exit():
    """Blueprints without an exit section are rejected."""
    bad_bp = {
        "name": "No_Exit",
        "indicators": {"rsi": {"type": "rsi", "period": 14}},
        "entry_long": [{"left": "rsi", "op": "above", "right": 50}],
    }
    errors = validate_blueprint(bad_bp)
    assert any("exit" in e.lower() for e in errors)


def test_approved_indicators_whitelist():
    """Only known indicator types are in the whitelist."""
    expected = {"ema", "sma", "rsi", "bollinger_bands", "atr", "adx", "vwap", "macd", "obv", "pattern"}
    assert set(APPROVED_INDICATORS.keys()) == expected


def test_approved_ops_whitelist():
    """Only known condition operators are in the whitelist."""
    expected = {"above", "below", "between", "cross_above", "cross_below"}
    assert set(APPROVED_OPS.keys()) == expected


# ── 3. Generated Strategy Execution Safety ───────────────────────────────────

def test_generated_strategy_execution():
    """Generated strategy processes candles without errors."""
    gen = StrategyGenerator(seed=42)
    bp = gen.generate_trend_following()
    strategy = GeneratedStrategy(bp)

    base_price = 50000.0
    for i in range(70):
        c = Candle(
            timestamp=1700000000 + i * 3600,
            o=base_price + i * 10,
            h=base_price + i * 10 + 20,
            l=base_price + i * 10 - 20,
            c=base_price + i * 10 + 5,
            volume=100.0 + i,
            is_closed=True,
        )
        strategy.update(c)

    assert strategy.has_enough_data(strategy.min_candles)
    signal = strategy.should_enter()
    assert signal in (None, "long", "short")


def test_generated_strategy_rejects_invalid_blueprint():
    """Constructing a GeneratedStrategy with invalid blueprint raises ValueError."""
    bad_bp = {
        "name": "Invalid",
        "indicators": {"x": {"type": "not_real"}},
        "entry_long": [],
        "exit": {"stop_loss_atr_mult": 2.0},
    }
    with pytest.raises(ValueError, match="Invalid blueprint"):
        GeneratedStrategy(bad_bp)


def test_generated_strategy_no_eval_exec():
    """GeneratedStrategy source code contains no eval() or exec() calls."""
    import inspect
    source = inspect.getsource(GeneratedStrategy)
    # Allow the docstring mention of "eval()" but not actual calls
    lines = [
        line for line in source.split("\n")
        if not line.strip().startswith("#")
        and not line.strip().startswith("'")
        and not line.strip().startswith('"')
        and "SAFETY" not in line
        and "does NOT use" not in line
        and "No eval" not in line
        and "Zero eval" not in line
    ]
    code_only = "\n".join(lines)
    assert "eval(" not in code_only, "GeneratedStrategy must not use eval()"
    assert "exec(" not in code_only, "GeneratedStrategy must not use exec()"


# ── 4. Mutation ──────────────────────────────────────────────────────────────

def test_strategy_mutation():
    """Mutated blueprints remain valid and have different names."""
    gen = StrategyGenerator(seed=123)
    parent = gen.generate_mean_reversion()
    mutated = gen.mutate_blueprint(parent)
    assert validate_blueprint(mutated) == []
    assert mutated["name"] != parent["name"]


def test_mutation_preserves_archetype():
    """Mutated blueprints keep the same archetype."""
    gen = StrategyGenerator(seed=77)
    parent = gen.generate_momentum_breakout()
    mutated = gen.mutate_blueprint(parent)
    assert mutated.get("archetype") == parent.get("archetype")


# ── 5. Strategy Evaluator ───────────────────────────────────────────────────

def test_strategy_evaluator_good_strategy():
    """A good strategy gets a high grade."""
    evaluator = StrategyEvaluator()
    dummy_exp = {
        "strategy_name": "TestCandidate",
        "train_metrics": {"sharpe_ratio": 1.8, "total_return_pct": 25.0},
        "test_metrics": {
            "total_trades": 25,
            "sharpe_ratio": 1.4,
            "total_return_pct": 18.0,
            "max_drawdown_pct": 8.5,
            "win_rate": 0.55,
            "profit_factor": 1.7,
            "avg_win": 120.0,
            "avg_loss": 80.0,
        },
    }
    diag = evaluator.evaluate_experiment(dummy_exp)
    assert diag["grade"] in ("A", "B")
    assert diag["score"] >= 65.0
    assert len(diag["strengths"]) > 0


def test_strategy_evaluator_bad_strategy():
    """A losing strategy gets a low grade."""
    evaluator = StrategyEvaluator()
    dummy_exp = {
        "strategy_name": "LoserStrat",
        "train_metrics": {"sharpe_ratio": 0.3},
        "test_metrics": {
            "total_trades": 5,
            "sharpe_ratio": -1.5,
            "total_return_pct": -10.0,
            "max_drawdown_pct": 25.0,
            "win_rate": 0.2,
            "profit_factor": 0.3,
            "avg_win": 10.0,
            "avg_loss": 50.0,
        },
    }
    diag = evaluator.evaluate_experiment(dummy_exp)
    assert diag["grade"] in ("D", "F")
    assert len(diag["weaknesses"]) > 0


def test_strategy_evaluator_zero_trades():
    """Strategy with zero trades gets grade F and NO_TRADES verdict."""
    evaluator = StrategyEvaluator()
    diag = evaluator.evaluate_experiment({
        "strategy_name": "Ghost",
        "test_metrics": {"total_trades": 0},
    })
    assert diag["grade"] == "F"
    assert diag["verdict"] == "NO_TRADES"


# ── 6. Experiment Manager: Budget & Deduplication ────────────────────────────

def test_experiment_budget_enforcement():
    """Budget tracks experiments and enforces limits."""
    budget = ExperimentBudget(max_experiments=3, max_runtime_seconds=9999)
    budget.start()
    assert not budget.budget_exhausted

    budget.record_experiment(True)
    budget.record_experiment(True)
    assert not budget.budget_exhausted

    budget.record_experiment(True)
    assert budget.budget_exhausted
    assert budget.remaining_experiments == 0


def test_experiment_budget_failure_limit():
    """Budget exhausts on too many failures."""
    budget = ExperimentBudget(max_experiments=100, max_failed=2)
    budget.start()

    budget.record_experiment(False)
    assert not budget.budget_exhausted

    budget.record_experiment(False)
    assert budget.budget_exhausted


def test_experiment_manager_deduplication(temp_store):
    """ExperimentManager skips duplicate strategy+symbol+timeframe combos."""
    from src.research.backtest_harness import BacktestHarness
    harness = BacktestHarness(temp_store)
    evaluator = StrategyEvaluator()
    manager = ExperimentManager(temp_store, harness, evaluator, seed=42)

    strat_record = {"id": "test_123", "name": "TestStrat"}
    assert manager.queue_experiment(strat_record, "BTCUSDT", "1h") is True
    assert manager.queue_experiment(strat_record, "BTCUSDT", "1h") is False  # duplicate
    assert manager.queue_experiment(strat_record, "ETHUSDT", "1h") is True  # different symbol
    assert manager.queue_size == 2


def test_experiment_manager_seed_propagation(temp_store):
    """Experiments in the same manager get deterministic sub-seeds."""
    from src.research.backtest_harness import BacktestHarness
    harness = BacktestHarness(temp_store)
    evaluator = StrategyEvaluator()

    # Same master seed should produce same sub-seeds
    mgr1 = ExperimentManager(temp_store, harness, evaluator, seed=42)
    mgr2 = ExperimentManager(temp_store, harness, evaluator, seed=42)

    strat = {"id": "s1", "name": "Strat1"}
    mgr1.queue_experiment(strat, "BTCUSDT", "1h")
    mgr2.queue_experiment(strat, "BTCUSDT", "1h")

    # Inspect seeds
    q1 = mgr1.inspect_queue()
    q2 = mgr2.inspect_queue()
    assert q1[0]["seed"] == q2[0]["seed"]


# ── 7. AI Research Assistant ─────────────────────────────────────────────────

def test_research_assistant_cold_start(temp_store):
    """With no data, assistant suggests diverse initial exploration."""
    registry = StrategyRegistry(temp_store)
    generator = StrategyGenerator(registry, seed=42)
    assistant = ResearchAssistant(temp_store, registry, generator)

    result = assistant.suggest_next_experiments(max_suggestions=3)
    suggestions = result["suggestions"]
    assert len(suggestions) == 3

    # All suggestions should have valid blueprints
    for s in suggestions:
        bp = s["blueprint"]
        errors = validate_blueprint(bp)
        assert errors == [], f"Invalid suggestion blueprint: {bp.get('name')}: {errors}"
        assert "rationale" in s
        assert "priority" in s


def test_research_assistant_suggestions_are_not_code(temp_store):
    """AI suggestions are pure data (JSON-serialisable), never executable code."""
    registry = StrategyRegistry(temp_store)
    generator = StrategyGenerator(registry, seed=42)
    assistant = ResearchAssistant(temp_store, registry, generator)

    result = assistant.suggest_next_experiments(max_suggestions=3)
    # Verify everything is JSON-serialisable
    serialised = json.dumps(result, default=str)
    assert len(serialised) > 0

    # No suggestion contains executable Python
    for s in result["suggestions"]:
        bp_str = json.dumps(s["blueprint"])
        assert "eval(" not in bp_str
        assert "exec(" not in bp_str
        assert "import " not in bp_str
        assert "__" not in bp_str


def test_research_assistant_analysis_saves_report(temp_store):
    """Analysis report is persisted to SQLite."""
    registry = StrategyRegistry(temp_store)
    generator = StrategyGenerator(registry, seed=42)
    assistant = ResearchAssistant(temp_store, registry, generator)

    report_id = assistant.save_analysis_report()
    assert report_id is not None

    reports = temp_store.list_reports(report_type="analysis")
    assert len(reports) >= 1


# ── 8. Report Generation ────────────────────────────────────────────────────

def test_report_generation(temp_store):
    """Campaign report generates valid markdown and saves to DB."""
    reporter = ReportGenerator(temp_store)
    evals = [{
        "strategy_name": "Alpha_Trend_v1",
        "score": 88.5,
        "grade": "A",
        "verdict": "HIGHLY_RECOMMENDED",
        "strengths": ["Strong Sharpe ratio"],
        "weaknesses": [],
        "suggestions": ["Test on additional pairs"],
        "test_summary": {
            "sharpe": 1.65,
            "return_pct": 22.4,
            "max_drawdown": 6.2,
            "win_rate": 58.0,
            "profit_factor": 1.9,
            "trades": 35,
        },
    }]
    rep = reporter.generate_campaign_report("TestCampaign", evals)
    assert os.path.exists(rep["filepath"])
    assert rep["report_id"] is not None
    assert "leaderboard" in rep["markdown"].lower() or "Alpha_Trend" in rep["markdown"]


# ── 9. Live Trading Isolation ────────────────────────────────────────────────

def test_research_lab_cannot_import_order_engine():
    """Research modules should not import OrderEngine or PaperWallet."""
    import importlib

    research_modules = [
        "src.research.experiment_store",
        "src.research.strategy_registry",
        "src.research.strategy_generator",
        "src.research.strategy_evaluator",
        "src.research.experiment_manager",
        "src.research.research_assistant",
        "src.research.report_generator",
    ]
    for modname in research_modules:
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


def test_experiment_store_schema_integrity(temp_store):
    """Experiment store has the expected tables."""
    with temp_store._connect() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cursor.fetchall()}
    assert "strategies" in tables
    assert "experiments" in tables
    assert "experiment_results" in tables
    assert "research_reports" in tables

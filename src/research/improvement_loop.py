"""
improvement_loop.py — Automated self-improvement cycle for strategy research.

Implements the full cycle:
  1. Read historical results from ExperimentStore
  2. Analyze performance, drawdown, risk metrics, stability
  3. Generate new experiments within approved boundaries
  4. Backtest candidates using the upgraded engine
  5. Compare candidates against baseline strategy
  6. Reject candidates that fail validation or risk criteria
  7. Record all results with pass/fail reasoning
  8. Respect configurable runtime, data, and compute limits
  9. Generate periodic research reports
  10. Support rollback to earlier approved versions

SAFETY:
  - Cannot place orders, modify wallets, or change live config
  - Cannot bypass human approval gates
  - All strategies validated against approved indicator/op whitelist
  - Complete audit trail of every decision
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.strategy_generator import StrategyGenerator
from src.research.backtest_harness import BacktestHarness
from src.research.strategy_evaluator import StrategyEvaluator
from src.research.experiment_manager import ExperimentManager, ExperimentBudget
from src.research.research_assistant import ResearchAssistant
from src.research.promotion_gate import PromotionGate
from src.research.overfitting_detector import OverfittingDetector
from src.research.report_generator import ReportGenerator

logger = logging.getLogger(__name__)


class ImprovementConfig:
    """Configuration for a self-improvement cycle."""

    def __init__(
        self,
        max_candidates: int = 5,
        max_runtime_seconds: int = 300,
        max_failed_experiments: int = 3,
        symbols: Optional[List[str]] = None,
        timeframe: str = "1h",
        backtest_days: int = 60,
        min_grade_for_promotion: str = "B",
        use_ai_suggestions: bool = True,
        seed: Optional[int] = None,
    ):
        self.max_candidates = max_candidates
        self.max_runtime_seconds = max_runtime_seconds
        self.max_failed_experiments = max_failed_experiments
        self.symbols = symbols or ["BTCUSDT"]
        self.timeframe = timeframe
        self.backtest_days = backtest_days
        self.min_grade_for_promotion = min_grade_for_promotion
        self.use_ai_suggestions = use_ai_suggestions
        self.seed = seed

    def to_dict(self) -> dict:
        return {
            "max_candidates": self.max_candidates,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_failed_experiments": self.max_failed_experiments,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "backtest_days": self.backtest_days,
            "min_grade_for_promotion": self.min_grade_for_promotion,
            "use_ai_suggestions": self.use_ai_suggestions,
            "seed": self.seed,
        }


class ImprovementLoop:
    """Automated self-improvement cycle controller.

    Coordinates all research components to find better strategies
    while enforcing safety, budgets, and human approval gates.

    The loop operates in a single pass (not infinitely). Each call
    to run() executes one full improvement cycle and returns results.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        config: Optional[ImprovementConfig] = None,
    ):
        self.config = config or ImprovementConfig()
        self.store = ExperimentStore(db_path)
        self.registry = StrategyRegistry(self.store)
        self.generator = StrategyGenerator(self.registry, seed=self.config.seed)
        self.harness = BacktestHarness(self.store, self.registry)
        self.evaluator = StrategyEvaluator()
        self.gate = PromotionGate(self.store, self.registry, self.evaluator)
        self.detector = OverfittingDetector(self.store)
        self.assistant = ResearchAssistant(
            self.store, self.registry, self.generator, self.evaluator
        )
        self.reporter = ReportGenerator(self.store)

    async def run(self) -> Dict[str, Any]:
        """Execute one full self-improvement cycle.

        Steps:
          1. Analyze historical data
          2. Generate candidates (AI-guided or random)
          3. Backtest within budget limits
          4. Evaluate and run overfitting checks
          5. Compare against baseline
          6. Auto-validate passing candidates
          7. Generate report

        Returns a comprehensive cycle result dict.
        """
        cycle_start = time.monotonic()
        cycle_result = {
            "config": self.config.to_dict(),
            "phases": {},
            "candidates_generated": 0,
            "candidates_tested": 0,
            "candidates_passed": 0,
            "candidates_promoted": 0,
            "candidates_rejected": 0,
            "errors": [],
            "report": None,
        }

        logger.info("[LOOP] Starting self-improvement cycle")

        # ── Phase 1: Historical Analysis ──────────────────────────────────
        try:
            analysis = self.assistant.analyze_performance_patterns()
            cycle_result["phases"]["analysis"] = {
                "experiments_analyzed": analysis.get("experiments_analyzed", 0),
                "winners": analysis.get("winners", 0),
                "losers": analysis.get("losers", 0),
                "weakness_clusters": len(
                    analysis.get("weakness_clusters", [])
                ),
            }
        except Exception as e:
            logger.error(f"[LOOP] Analysis phase failed: {e}")
            cycle_result["errors"].append(f"Analysis: {e}")
            analysis = None

        # ── Phase 2: Generate Candidates ──────────────────────────────────
        blueprints = []
        try:
            if self.config.use_ai_suggestions and analysis:
                suggestion_data = self.assistant.suggest_next_experiments(
                    max_suggestions=self.config.max_candidates
                )
                blueprints = [
                    s["blueprint"]
                    for s in suggestion_data.get("suggestions", [])
                ]
            else:
                blueprints = self.generator.generate_batch(
                    self.config.max_candidates
                )

            cycle_result["candidates_generated"] = len(blueprints)
        except Exception as e:
            logger.error(f"[LOOP] Generation phase failed: {e}")
            cycle_result["errors"].append(f"Generation: {e}")

        if not blueprints:
            cycle_result["phases"]["generation"] = {"status": "no_candidates"}
            return cycle_result

        # Register candidates
        registered = []
        for bp in blueprints:
            try:
                strat_id = self.generator.register_candidate(
                    bp, timeframe=self.config.timeframe
                )
                strat_record = self.registry.get(strat_id)
                registered.append(strat_record)
            except Exception as e:
                logger.warning(f"[LOOP] Failed to register {bp.get('name')}: {e}")

        cycle_result["phases"]["generation"] = {
            "status": "ok",
            "candidates": len(registered),
        }

        # ── Phase 3: Backtest Within Budget ───────────────────────────────
        budget = ExperimentBudget(
            max_experiments=len(registered) * len(self.config.symbols) + 2,
            max_runtime_seconds=self.config.max_runtime_seconds,
            max_failed=self.config.max_failed_experiments,
        )
        manager = ExperimentManager(
            store=self.store,
            harness=self.harness,
            evaluator=self.evaluator,
            budget=budget,
            seed=self.config.seed,
        )

        queued = manager.queue_batch(
            strategy_records=registered,
            symbols=self.config.symbols,
            timeframe=self.config.timeframe,
            days=self.config.backtest_days,
        )

        try:
            await manager.run_queue()
        except Exception as e:
            logger.error(f"[LOOP] Backtest phase failed: {e}")
            cycle_result["errors"].append(f"Backtest: {e}")

        cycle_result["candidates_tested"] = budget.experiments_run
        cycle_result["phases"]["backtest"] = manager.summary()

        # ── Phase 4: Evaluate and Run Overfitting Checks ──────────────────
        evaluations = manager.get_evaluations()
        cycle_result["phases"]["evaluation"] = {
            "total_evaluated": len(evaluations),
            "grade_distribution": {},
        }

        grades = {}
        for ev in evaluations:
            g = ev.get("grade", "?")
            grades[g] = grades.get(g, 0) + 1
        cycle_result["phases"]["evaluation"]["grade_distribution"] = grades

        # Overfitting check for the entire batch
        for symbol in self.config.symbols:
            diagnostic = self.detector.run_full_diagnostic(
                symbol=symbol, timeframe=self.config.timeframe
            )
            if diagnostic.get("overall_risk") in ("medium", "high"):
                logger.warning(
                    f"[LOOP] Overfitting risk detected for {symbol}: "
                    f"{diagnostic['overall_risk']}"
                )

        # ── Phase 5: Compare Against Baseline and Auto-Validate ───────────
        acceptable_grades = {"A"} if self.config.min_grade_for_promotion == "A" else {"A", "B"}
        promoted_ids = []
        rejected_reasons = []

        for exp_result in manager.results:
            if exp_result.get("status") != "completed":
                continue
            if "evaluation" not in exp_result:
                continue

            ev = exp_result["evaluation"]
            strat_id = exp_result.get("strategy_id")
            strat_name = exp_result.get("strategy_name", "?")

            if ev.get("grade") not in acceptable_grades:
                rejected_reasons.append({
                    "strategy": strat_name,
                    "grade": ev.get("grade"),
                    "reason": f"Grade {ev.get('grade')} below minimum ({self.config.min_grade_for_promotion})",
                })
                cycle_result["candidates_rejected"] += 1
                continue

            # Run individual overfitting check
            train_m = exp_result.get("train_metrics", {})
            test_m = exp_result.get("test_metrics", {})
            overfit_check = self.detector.check_train_test_divergence(
                train_m, test_m
            )
            if overfit_check.get("severity") == "severe":
                rejected_reasons.append({
                    "strategy": strat_name,
                    "grade": ev.get("grade"),
                    "reason": "Severe overfitting detected (train/test divergence)",
                })
                cycle_result["candidates_rejected"] += 1
                continue

            # Auto-validate against baseline
            validation = self.gate.auto_validate(strat_id, test_m)
            if validation["passed"]:
                promoted_ids.append(strat_id)
                cycle_result["candidates_promoted"] += 1
                cycle_result["candidates_passed"] += 1
            else:
                rejected_reasons.append({
                    "strategy": strat_name,
                    "grade": ev.get("grade"),
                    "reason": validation["reason"],
                })
                cycle_result["candidates_rejected"] += 1

        cycle_result["phases"]["validation"] = {
            "promoted": len(promoted_ids),
            "rejected": rejected_reasons,
            "promoted_ids": promoted_ids,
        }

        # ── Phase 6: Generate Report ──────────────────────────────────────
        try:
            if evaluations:
                report = self.reporter.generate_campaign_report(
                    campaign_name="SelfImprovement_Cycle",
                    evaluations=evaluations,
                    symbol=", ".join(self.config.symbols),
                    timeframe=self.config.timeframe,
                )
                cycle_result["report"] = report

            # Also save AI analysis report
            self.assistant.save_analysis_report()
        except Exception as e:
            logger.error(f"[LOOP] Report phase failed: {e}")
            cycle_result["errors"].append(f"Report: {e}")

        elapsed = time.monotonic() - cycle_start
        cycle_result["elapsed_seconds"] = round(elapsed, 1)

        logger.info(
            f"[LOOP] Cycle complete: "
            f"{cycle_result['candidates_generated']} generated, "
            f"{cycle_result['candidates_tested']} tested, "
            f"{cycle_result['candidates_promoted']} promoted, "
            f"{cycle_result['candidates_rejected']} rejected "
            f"({elapsed:.1f}s)"
        )

        return cycle_result

    # ── Convenience Methods ───────────────────────────────────────────────

    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """List strategies pending human approval."""
        return self.gate.get_pending_approvals()

    def approve_for_paper(self, strategy_id: str,
                          approved_by: str = "human") -> bool:
        """Human approves a promoted strategy for paper trading."""
        if not self.gate.request_paper(strategy_id):
            return False
        return self.gate.approve_paper(strategy_id, approved_by=approved_by)

    def reject_candidate(self, strategy_id: str, reason: str = "",
                         rejected_by: str = "human") -> bool:
        """Human rejects a strategy."""
        return self.gate.reject(strategy_id, reason, rejected_by)

    def rollback(self, approved_by: str = "human") -> Optional[str]:
        """Rollback to the previous baseline."""
        return self.gate.rollback(approved_by=approved_by)

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get the promotion audit log."""
        return self.gate.get_audit_log(limit)

    def get_baseline(self) -> Optional[Dict[str, Any]]:
        """Get the current active baseline."""
        return self.gate.get_baseline()

"""
experiment_manager.py — Automatic experiment queuing with resource limits.

Manages:
  - Queue of pending experiments with configurable concurrency limits
  - Maximum runtime and experiment count budgets
  - Reproducibility through deterministic seed propagation
  - Experiment deduplication (same strategy + symbol + timeframe = skip)
  - Resource tracking and budget enforcement

SAFETY: This module only creates experiments and delegates to BacktestHarness.
It cannot place orders, modify wallets, or change live bot behaviour.
"""

import time
import random
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.backtest_harness import BacktestHarness
from src.research.strategy_evaluator import StrategyEvaluator

logger = logging.getLogger(__name__)


class ExperimentBudget:
    """Resource budget for a research session."""

    def __init__(
        self,
        max_experiments: int = 20,
        max_runtime_seconds: int = 600,
        max_failed: int = 5,
    ):
        self.max_experiments = max_experiments
        self.max_runtime_seconds = max_runtime_seconds
        self.max_failed = max_failed

        self.experiments_run = 0
        self.experiments_failed = 0
        self.start_time: Optional[float] = None

    def start(self):
        self.start_time = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        if self.start_time is None:
            return 0.0
        return time.monotonic() - self.start_time

    @property
    def budget_exhausted(self) -> bool:
        if self.experiments_run >= self.max_experiments:
            return True
        if self.elapsed_seconds >= self.max_runtime_seconds:
            return True
        if self.experiments_failed >= self.max_failed:
            return True
        return False

    @property
    def remaining_experiments(self) -> int:
        return max(0, self.max_experiments - self.experiments_run)

    def record_experiment(self, success: bool):
        self.experiments_run += 1
        if not success:
            self.experiments_failed += 1

    def summary(self) -> Dict[str, Any]:
        return {
            "experiments_run": self.experiments_run,
            "experiments_failed": self.experiments_failed,
            "remaining": self.remaining_experiments,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "budget_exhausted": self.budget_exhausted,
        }


class ExperimentManager:
    """Queues, deduplicates, and executes research experiments with budgets.

    Features:
      - Deterministic seed propagation for reproducibility
      - Experiment deduplication (same strategy+symbol+timeframe = skip)
      - Configurable resource limits (max experiments, runtime, failures)
      - Queue inspection and priority management
    """

    def __init__(
        self,
        store: ExperimentStore,
        harness: BacktestHarness,
        evaluator: StrategyEvaluator,
        budget: Optional[ExperimentBudget] = None,
        seed: Optional[int] = None,
    ):
        self.store = store
        self.harness = harness
        self.evaluator = evaluator
        self.budget = budget or ExperimentBudget()
        self.seed = seed
        self._rng = random.Random(seed)

        # Internal queue: list of (strategy_record, symbol, timeframe, days, seed)
        self._queue: List[Dict[str, Any]] = []
        self._completed_results: List[Dict[str, Any]] = []

    # ── Queue Management ──────────────────────────────────────────────────

    def queue_experiment(
        self,
        strategy_record: dict,
        symbol: str = "BTCUSDT",
        timeframe: str = "1h",
        days: int = 60,
    ) -> bool:
        """Add an experiment to the queue. Returns False if deduplicated."""
        # Deduplication: same strategy_id + symbol + timeframe
        strat_id = strategy_record.get("id", "")
        for queued in self._queue:
            if (queued["strategy_id"] == strat_id
                    and queued["symbol"] == symbol
                    and queued["timeframe"] == timeframe):
                logger.info(
                    f"[MANAGER] Skipping duplicate: {strategy_record.get('name')} "
                    f"on {symbol}/{timeframe}"
                )
                return False

        # Also check completed experiments in DB
        existing = self.store.list_experiments(strategy_id=strat_id)
        for exp in existing:
            if (exp.get("symbol") == symbol
                    and exp.get("timeframe") == timeframe
                    and exp.get("status") == "completed"):
                logger.info(
                    f"[MANAGER] Skipping already-completed: "
                    f"{strategy_record.get('name')} on {symbol}/{timeframe}"
                )
                return False

        # Assign a deterministic sub-seed for reproducibility
        exp_seed = self._rng.randint(0, 2**31)

        self._queue.append({
            "strategy_record": strategy_record,
            "strategy_id": strat_id,
            "strategy_name": strategy_record.get("name", "Unknown"),
            "symbol": symbol,
            "timeframe": timeframe,
            "days": days,
            "seed": exp_seed,
        })
        return True

    def queue_batch(
        self,
        strategy_records: List[dict],
        symbols: List[str],
        timeframe: str = "1h",
        days: int = 60,
    ) -> int:
        """Queue experiments for multiple strategies across multiple symbols.
        Returns count of experiments actually queued (after dedup)."""
        queued = 0
        for symbol in symbols:
            for strat in strategy_records:
                if self.queue_experiment(strat, symbol, timeframe, days):
                    queued += 1
        return queued

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    def inspect_queue(self) -> List[Dict[str, Any]]:
        """Return a read-only snapshot of the current queue."""
        return [
            {
                "strategy_name": q["strategy_name"],
                "symbol": q["symbol"],
                "timeframe": q["timeframe"],
                "days": q["days"],
                "seed": q["seed"],
            }
            for q in self._queue
        ]

    def clear_queue(self):
        self._queue.clear()

    # ── Execution ─────────────────────────────────────────────────────────

    async def run_queue(self) -> List[Dict[str, Any]]:
        """Execute all queued experiments within budget constraints.

        Returns list of experiment result dicts with evaluation diagnostics.
        """
        self.budget.start()
        self._completed_results = []

        while self._queue and not self.budget.budget_exhausted:
            item = self._queue.pop(0)

            # Set the seed for this experiment's reproducibility
            random.seed(item["seed"])

            logger.info(
                f"[MANAGER] Running experiment {self.budget.experiments_run + 1}: "
                f"{item['strategy_name']} on {item['symbol']} "
                f"(seed={item['seed']})"
            )

            result = await self.harness.run_experiment(
                strategy_record=item["strategy_record"],
                symbol=item["symbol"],
                timeframe=item["timeframe"],
                days=item["days"],
            )

            success = result.get("status") == "completed"
            self.budget.record_experiment(success)

            # Attach reproducibility metadata
            result["seed"] = item["seed"]
            result["queue_position"] = self.budget.experiments_run

            # Evaluate completed experiments
            if success:
                diag = self.evaluator.evaluate_experiment(result)
                result["evaluation"] = diag

            self._completed_results.append(result)

        if self.budget.budget_exhausted and self._queue:
            logger.warning(
                f"[MANAGER] Budget exhausted with {len(self._queue)} "
                f"experiments still in queue. {self.budget.summary()}"
            )

        return self._completed_results

    @property
    def results(self) -> List[Dict[str, Any]]:
        return self._completed_results

    def get_passing_results(self) -> List[Dict[str, Any]]:
        """Return only experiments that passed backtest validation."""
        return [
            r for r in self._completed_results
            if r.get("passed") is True
        ]

    def get_evaluations(self) -> List[Dict[str, Any]]:
        """Return evaluation diagnostics for all completed experiments."""
        return [
            r["evaluation"]
            for r in self._completed_results
            if "evaluation" in r
        ]

    def summary(self) -> Dict[str, Any]:
        """Full summary of manager state."""
        evals = self.get_evaluations()
        grades = {}
        for e in evals:
            g = e.get("grade", "?")
            grades[g] = grades.get(g, 0) + 1

        return {
            "budget": self.budget.summary(),
            "queue_remaining": self.queue_size,
            "completed": len(self._completed_results),
            "passed": len(self.get_passing_results()),
            "grade_distribution": grades,
        }

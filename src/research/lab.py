"""
lab.py — Central orchestrator and CLI entry point for the AI Strategy Research Lab.

Coordinates:
  - StrategyRegistry (catalog & lifecycle management)
  - StrategyGenerator (blueprint creation & mutation)
  - ExperimentManager (queuing, budgets, reproducibility)
  - BacktestHarness (walk-forward simulation with realistic fees)
  - StrategyEvaluator (overfitting diagnosis & quantitative grading)
  - ResearchAssistant (weakness analysis & experiment suggestions)
  - ReportGenerator (markdown & JSON report generation)

CLI Usage:
  python -m src.research.lab --campaign --count 5 --symbol BTCUSDT --timeframe 1h
  python -m src.research.lab --list
  python -m src.research.lab --summary
  python -m src.research.lab --analyze
  python -m src.research.lab --suggest --count 3
  python -m src.research.lab --promote
"""

import sys
import asyncio
import logging
import argparse
from typing import Optional, List, Dict, Any

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.strategy_generator import StrategyGenerator
from src.research.backtest_harness import BacktestHarness
from src.research.strategy_evaluator import StrategyEvaluator
from src.research.experiment_manager import ExperimentManager, ExperimentBudget
from src.research.research_assistant import ResearchAssistant
from src.research.report_generator import ReportGenerator

logger = logging.getLogger("research_lab")


class ResearchLab:
    """The central controller for the AI Strategy Research Lab.

    SAFETY GUARANTEES:
      - Cannot place real or paper orders
      - Cannot modify live wallet, risk limits, or trading config
      - Cannot execute arbitrary Python code (zero eval/exec)
      - All strategy blueprints validated against approved indicator/op whitelist
    """

    def __init__(self, db_path: Optional[str] = None, seed: Optional[int] = None):
        self.store = ExperimentStore(db_path)
        self.registry = StrategyRegistry(self.store)
        self.generator = StrategyGenerator(self.registry, seed=seed)
        self.harness = BacktestHarness(self.store, self.registry)
        self.evaluator = StrategyEvaluator()
        self.reporter = ReportGenerator(self.store)
        self.assistant = ResearchAssistant(
            self.store, self.registry, self.generator, self.evaluator
        )

    def list_strategies(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List strategies in registry, optionally filtered by status."""
        return self.registry.list_by_status(status) if status else self.registry.list_all()

    def get_summary(self) -> Dict[str, Any]:
        """Summary metrics for the research lab."""
        reg_summary = self.registry.summary()
        exp_count = self.store.count_experiments()
        completed_count = self.store.count_experiments(status="completed")
        failed_count = self.store.count_experiments(status="failed")
        reports = self.store.list_reports(limit=5)
        return {
            "strategies": reg_summary,
            "total_experiments": exp_count,
            "completed_experiments": completed_count,
            "failed_experiments": failed_count,
            "recent_reports": len(reports),
        }

    async def run_campaign(
        self,
        num_candidates: int = 5,
        symbol: str = "BTCUSDT",
        timeframe: str = "1h",
        days: int = 60,
        campaign_name: Optional[str] = None,
        max_runtime: int = 600,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate N candidate strategies, backtest them within budget, and produce report."""
        name = campaign_name or f"Discovery_{symbol}_{timeframe}"
        print(f"\n========================================================")
        print(f"[LAB] Starting AI Research Campaign: {name}")
        print(f"[LAB] Generating {num_candidates} candidate strategies...")
        print(f"========================================================\n")

        # 1. Generate & register candidate blueprints
        blueprints = self.generator.generate_batch(num_candidates)
        registered_strategies = []
        for bp in blueprints:
            strat_id = self.generator.register_candidate(bp, timeframe=timeframe)
            strat_record = self.registry.get(strat_id)
            registered_strategies.append(strat_record)
            print(f"  [+] Generated & Registered: {bp.get('name')} (ID: {strat_id})")

        # 2. Queue experiments via ExperimentManager with budget
        budget = ExperimentBudget(
            max_experiments=num_candidates * 2,
            max_runtime_seconds=max_runtime,
            max_failed=max(3, num_candidates),
        )
        manager = ExperimentManager(
            store=self.store,
            harness=self.harness,
            evaluator=self.evaluator,
            budget=budget,
            seed=seed,
        )
        queued = manager.queue_batch(
            strategy_records=registered_strategies,
            symbols=[symbol],
            timeframe=timeframe,
            days=days,
        )
        print(f"\n[*] Queued {queued} experiments (budget: {budget.max_experiments} max, "
              f"{budget.max_runtime_seconds}s timeout)")

        # 3. Execute queue
        print(f"[*] Running Walk-Forward Backtests on {symbol} ({days} days)...")
        await manager.run_queue()

        # 4. Evaluate and report
        print(f"\n[*] Diagnosing Weaknesses and Computing Performance Grades...")
        evaluations = manager.get_evaluations()
        for exp in manager.results:
            status = exp.get("status", "?")
            sname = exp.get("strategy_name", "?")
            if status == "completed" and "evaluation" in exp:
                diag = exp["evaluation"]
                grade_str = f"Grade {diag['grade']} (Score: {diag['score']})"
                print(f"  [>] {sname:<30} -> {grade_str:<18} | Verdict: {diag['verdict']}")
            else:
                print(f"  [-] {sname:<30} -> FAILED: {exp.get('error', 'unknown')}")

        # 5. Generate Research Report
        report_out = None
        if evaluations:
            report_out = self.reporter.generate_campaign_report(
                campaign_name=name,
                evaluations=evaluations,
                symbol=symbol,
                timeframe=timeframe,
            )
            print(f"\n[REPORT] Research Report Saved: {report_out['filepath']}")

        # 6. Budget summary
        bsummary = manager.summary()
        print(f"\n[BUDGET] {bsummary['budget']}")

        return {
            "campaign_name": name,
            "candidates_count": len(registered_strategies),
            "queued": queued,
            "evaluations": evaluations,
            "report": report_out,
            "budget_summary": bsummary,
        }

    async def run_ai_suggested_campaign(
        self,
        max_suggestions: int = 5,
        symbol: str = "BTCUSDT",
        timeframe: str = "1h",
        days: int = 60,
        max_runtime: int = 600,
    ) -> Dict[str, Any]:
        """Let the AI Research Assistant suggest and run experiments."""
        print(f"\n========================================================")
        print(f"[AI] AI Research Assistant: Analyzing past experiments...")
        print(f"========================================================\n")

        suggestion_data = self.assistant.suggest_next_experiments(max_suggestions)

        # Print rationale
        for line in suggestion_data.get("rationale_chain", []):
            print(f"  [AI] {line}")

        suggestions = suggestion_data.get("suggestions", [])
        if not suggestions:
            print("  [AI] No suggestions generated.")
            return {"suggestions": [], "evaluations": []}

        # Register and queue suggested strategies
        registered = []
        for s in suggestions:
            bp = s["blueprint"]
            priority = s["priority"]
            print(f"  [+] Suggested: {bp.get('name')} (priority: {priority})")
            print(f"      Rationale: {s['rationale']}")
            strat_id = self.generator.register_candidate(bp, timeframe=timeframe)
            strat_record = self.registry.get(strat_id)
            registered.append(strat_record)

        # Run via ExperimentManager
        budget = ExperimentBudget(
            max_experiments=len(registered) + 2,
            max_runtime_seconds=max_runtime,
        )
        manager = ExperimentManager(
            store=self.store,
            harness=self.harness,
            evaluator=self.evaluator,
            budget=budget,
        )
        manager.queue_batch(registered, [symbol], timeframe, days)

        print(f"\n[*] Running {manager.queue_size} AI-suggested experiments...")
        await manager.run_queue()

        evaluations = manager.get_evaluations()
        for diag in evaluations:
            grade_str = f"Grade {diag['grade']} (Score: {diag['score']})"
            print(f"  [>] {diag['strategy_name']:<30} -> {grade_str}")

        # Save AI analysis report
        report_id = self.assistant.save_analysis_report()
        print(f"\n[AI] Analysis report saved (ID: {report_id})")

        # Generate campaign report
        report_out = None
        if evaluations:
            report_out = self.reporter.generate_campaign_report(
                campaign_name="AI_Suggested",
                evaluations=evaluations,
                symbol=symbol,
                timeframe=timeframe,
            )
            print(f"[REPORT] Campaign report: {report_out['filepath']}")

        return {
            "suggestions": suggestions,
            "evaluations": evaluations,
            "report": report_out,
            "analysis_report_id": report_id,
        }

    def promote_passing_candidates(self, min_grade: str = "B") -> List[str]:
        """Automatically promote candidates with qualifying grades."""
        promoted = []
        acceptable_grades = {"A"} if min_grade == "A" else {"A", "B"}
        results = self.store.get_all_completed_results(limit=100)

        for r in results:
            strat_id = r.get("strategy_id")
            strat = self.registry.get(strat_id)
            if strat and strat.get("status") == "candidate":
                diag = self.evaluator.evaluate_experiment(
                    {"test_metrics": r, "strategy_name": r.get("strategy_name")}
                )
                if diag["grade"] in acceptable_grades:
                    self.registry.promote(strat_id)
                    promoted.append(
                        f"{r.get('strategy_name')} ({diag['grade']})"
                    )
                    print(
                        f"  [PROMOTED] Strategy: {r.get('strategy_name')} "
                        f"(Grade: {diag['grade']})"
                    )

        return promoted


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Zenith AI Strategy Research Lab",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m src.research.lab --campaign --count 5 --symbol BTCUSDT\n"
            "  python -m src.research.lab --ai-suggest --count 3\n"
            "  python -m src.research.lab --analyze\n"
            "  python -m src.research.lab --list\n"
            "  python -m src.research.lab --promote\n"
        ),
    )
    parser.add_argument("--campaign", action="store_true",
                        help="Run an automated candidate discovery campaign")
    parser.add_argument("--ai-suggest", action="store_true",
                        help="Let AI analyze history and suggest next experiments")
    parser.add_argument("--analyze", action="store_true",
                        help="Run AI analysis on past experiments and save report")
    parser.add_argument("--count", type=int, default=3,
                        help="Number of candidate strategies to generate")
    parser.add_argument("--symbol", type=str, default="BTCUSDT",
                        help="Trading pair symbol (e.g. BTCUSDT, ETHUSDT)")
    parser.add_argument("--timeframe", type=str, default="1h",
                        help="Candle interval (e.g. 15m, 1h, 4h)")
    parser.add_argument("--days", type=int, default=60,
                        help="Historical days for backtest")
    parser.add_argument("--max-runtime", type=int, default=600,
                        help="Maximum runtime in seconds for campaign")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--list", action="store_true",
                        help="List all strategies in the registry")
    parser.add_argument("--summary", action="store_true",
                        help="Show research lab summary metrics")
    parser.add_argument("--promote", action="store_true",
                        help="Promote high-performing candidate strategies")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    lab = ResearchLab(seed=args.seed)

    if args.list:
        strats = lab.list_strategies()
        print(f"\n{'='*80}")
        print(f"  {'ID':<14} {'NAME':<32} {'TYPE':<12} {'STATUS':<10} {'VER':<5}")
        print(f"{'='*80}")
        for s in strats:
            print(
                f"  {s['id']:<14} {s['name']:<32} "
                f"{s['strategy_type']:<12} {s['status']:<10} v{s['version']:<4}"
            )
        print(f"{'='*80}\n")

    elif args.summary:
        sum_data = lab.get_summary()
        counts = sum_data["strategies"]["counts"]
        print(f"\n{'='*50}")
        print(f"  [LAB] AI Strategy Research Lab Summary")
        print(f"{'='*50}")
        print(f"  Total Strategies:      {sum_data['strategies']['total']}")
        print(f"    - Legacy:            {counts.get('legacy', 0)}")
        print(f"    - Candidate:         {counts.get('candidate', 0)}")
        print(f"    - Promoted:          {counts.get('promoted', 0)}")
        print(f"    - Retired:           {counts.get('retired', 0)}")
        print(f"  Total Experiments:     {sum_data['total_experiments']}")
        print(f"  Completed Experiments: {sum_data['completed_experiments']}")
        print(f"  Failed Experiments:    {sum_data['failed_experiments']}")
        print(f"{'='*50}\n")

    elif args.analyze:
        print("\n[AI] Running AI Research Assistant analysis...")
        report_id = lab.assistant.save_analysis_report()
        analysis = lab.assistant.analyze_performance_patterns()

        print(f"\n{'='*60}")
        print(f"  [AI] Analysis Complete (Report #{report_id})")
        print(f"{'='*60}")
        print(f"  Experiments Analyzed: {analysis.get('experiments_analyzed', 0)}")
        print(f"  Winners:             {analysis.get('winners', 0)}")
        print(f"  Losers:              {analysis.get('losers', 0)}")

        arch_perf = analysis.get("archetype_performance", {})
        if arch_perf:
            print(f"\n  Archetype Performance:")
            for arch, stats in arch_perf.items():
                wr = stats['win_ratio']
                print(
                    f"    {arch:<25} "
                    f"Win: {stats['wins']}/{stats['total_experiments']} "
                    f"({wr:.0%}) | Avg Sharpe: {stats['avg_sharpe']}"
                )

        weaknesses = analysis.get("weakness_clusters", [])
        if weaknesses:
            print(f"\n  Weakness Clusters:")
            for wk in weaknesses:
                print(f"    - {wk['type']}: {wk['affected']} affected")
                print(f"      -> {wk['recommendation']}")

        print(f"{'='*60}\n")

    elif args.ai_suggest:
        asyncio.run(lab.run_ai_suggested_campaign(
            max_suggestions=args.count,
            symbol=args.symbol,
            timeframe=args.timeframe,
            days=args.days,
            max_runtime=args.max_runtime,
        ))

    elif args.promote:
        print("\n[*] Checking for candidate strategies ready for promotion...")
        promoted = lab.promote_passing_candidates()
        if not promoted:
            print("  No candidate strategies met the promotion threshold.\n")

    elif args.campaign:
        asyncio.run(lab.run_campaign(
            num_candidates=args.count,
            symbol=args.symbol,
            timeframe=args.timeframe,
            days=args.days,
            max_runtime=args.max_runtime,
            seed=args.seed,
        ))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

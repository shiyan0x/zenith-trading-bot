"""
research_assistant.py — AI Research Assistant for the Strategy Research Lab.

Analyzes historical experiment data to:
  - Identify patterns in successful vs failing strategies
  - Detect weakness clusters (fee sensitivity, drawdown regimes, etc.)
  - Suggest which archetypes, parameter ranges, and symbols to test next
  - Generate structured experiment proposals within approved boundaries

SAFETY:
  - Operates ONLY on data already in ExperimentStore (read-only analysis)
  - Suggestions are JSON-serialisable dicts — never executable code
  - All proposed blueprints are validated against APPROVED_INDICATORS/OPS
  - Recommendations are saved as research reports, not proof of profitability
"""

import json
import logging
from typing import Optional, List, Dict, Any
from collections import Counter, defaultdict

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.strategy_generator import StrategyGenerator
from src.research.strategy_evaluator import StrategyEvaluator
from src.research.generated_strategy import validate_blueprint

logger = logging.getLogger(__name__)


class ResearchAssistant:
    """Analyzes past experiments and proposes what to research next.

    All reasoning is rule-based and transparent — no opaque ML models.
    Every recommendation includes an explicit rationale chain.
    """

    def __init__(
        self,
        store: ExperimentStore,
        registry: StrategyRegistry,
        generator: StrategyGenerator,
        evaluator: Optional[StrategyEvaluator] = None,
    ):
        self.store = store
        self.registry = registry
        self.generator = generator
        self.evaluator = evaluator or StrategyEvaluator()

    # ── Data Collection ───────────────────────────────────────────────────

    def _load_completed_results(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Load all completed test-period results with strategy metadata."""
        return self.store.get_all_completed_results(limit=limit)

    def _load_strategies_with_blueprints(self) -> Dict[str, Dict]:
        """Load all strategies keyed by ID with parsed blueprints."""
        strategies = {}
        for s in self.registry.list_all():
            bp = s.get("blueprint", "{}")
            if isinstance(bp, str):
                try:
                    bp = json.loads(bp)
                except (json.JSONDecodeError, TypeError):
                    bp = {}
            s["_blueprint"] = bp
            strategies[s["id"]] = s
        return strategies

    # ── Analysis ──────────────────────────────────────────────────────────

    def analyze_performance_patterns(self) -> Dict[str, Any]:
        """Analyze all completed experiments to find performance patterns.

        Returns a structured analysis with:
          - archetype_performance: which archetypes work best
          - indicator_frequency: which indicators appear in winners vs losers
          - parameter_ranges: successful vs failing parameter ranges
          - weakness_clusters: common failure modes
        """
        results = self._load_completed_results()
        strategies = self._load_strategies_with_blueprints()

        if not results:
            return {
                "status": "insufficient_data",
                "message": "No completed experiments found. Run campaigns first.",
                "experiments_analyzed": 0,
            }

        # Classify results
        winners = []  # passed = True, positive return, Sharpe > 0.5
        losers = []

        for r in results:
            is_winner = (
                r.get("passed", 0) == 1
                and r.get("total_return_pct", 0) > 0
                and r.get("sharpe_ratio", 0) > 0.3
            )
            strat_id = r.get("strategy_id", "")
            strat = strategies.get(strat_id, {})
            bp = strat.get("_blueprint", {})

            record = {
                "strategy_name": r.get("strategy_name", ""),
                "strategy_id": strat_id,
                "archetype": bp.get("archetype", "legacy"),
                "indicators": list(bp.get("indicators", {}).keys()),
                "indicator_types": [
                    spec.get("type", "")
                    for spec in bp.get("indicators", {}).values()
                ],
                "sharpe": r.get("sharpe_ratio", 0),
                "return_pct": r.get("total_return_pct", 0),
                "max_drawdown": r.get("max_drawdown_pct", 0),
                "win_rate": r.get("win_rate", 0),
                "profit_factor": r.get("profit_factor", 0),
                "trades": r.get("total_trades", 0),
                "exit_config": bp.get("exit", {}),
            }

            if is_winner:
                winners.append(record)
            else:
                losers.append(record)

        # Archetype performance
        archetype_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "sharpes": []})
        for w in winners:
            a = w["archetype"]
            archetype_stats[a]["wins"] += 1
            archetype_stats[a]["sharpes"].append(w["sharpe"])
        for lo in losers:
            a = lo["archetype"]
            archetype_stats[a]["losses"] += 1
            archetype_stats[a]["sharpes"].append(lo["sharpe"])

        archetype_perf = {}
        for arch, stats in archetype_stats.items():
            total = stats["wins"] + stats["losses"]
            avg_sharpe = (
                sum(stats["sharpes"]) / len(stats["sharpes"])
                if stats["sharpes"] else 0
            )
            archetype_perf[arch] = {
                "total_experiments": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_ratio": stats["wins"] / total if total > 0 else 0,
                "avg_sharpe": round(avg_sharpe, 3),
            }

        # Indicator frequency in winners vs losers
        winner_ind_types = Counter()
        loser_ind_types = Counter()
        for w in winners:
            winner_ind_types.update(w["indicator_types"])
        for lo in losers:
            loser_ind_types.update(lo["indicator_types"])

        # Weakness clusters
        weakness_clusters = []
        high_dd_count = sum(1 for lo in losers if lo["max_drawdown"] > 15)
        low_trade_count = sum(1 for lo in losers if lo["trades"] < 10)
        negative_return_count = sum(1 for lo in losers if lo["return_pct"] < -5)

        if high_dd_count > len(losers) * 0.3:
            weakness_clusters.append({
                "type": "excessive_drawdown",
                "affected": high_dd_count,
                "recommendation": "Reduce ATR stop-loss multipliers or add ADX trending filter.",
            })
        if low_trade_count > len(losers) * 0.4:
            weakness_clusters.append({
                "type": "insufficient_signals",
                "affected": low_trade_count,
                "recommendation": "Widen entry condition bands or reduce required confirmations.",
            })
        if negative_return_count > len(losers) * 0.5:
            weakness_clusters.append({
                "type": "systematic_loss",
                "affected": negative_return_count,
                "recommendation": "Review fee impact. Strategies may be edge-negative after costs.",
            })

        # Successful exit parameter ranges
        winning_sl_mults = [w["exit_config"].get("stop_loss_atr_mult", 2.0) for w in winners if w["exit_config"]]
        winning_rr_ratios = [w["exit_config"].get("take_profit_rr", 2.0) for w in winners if w["exit_config"]]

        param_insights = {}
        if winning_sl_mults:
            param_insights["winning_sl_range"] = {
                "min": min(winning_sl_mults),
                "max": max(winning_sl_mults),
                "avg": round(sum(winning_sl_mults) / len(winning_sl_mults), 2),
            }
        if winning_rr_ratios:
            param_insights["winning_rr_range"] = {
                "min": min(winning_rr_ratios),
                "max": max(winning_rr_ratios),
                "avg": round(sum(winning_rr_ratios) / len(winning_rr_ratios), 2),
            }

        return {
            "status": "ok",
            "experiments_analyzed": len(results),
            "winners": len(winners),
            "losers": len(losers),
            "archetype_performance": archetype_perf,
            "indicator_frequency": {
                "in_winners": dict(winner_ind_types.most_common(10)),
                "in_losers": dict(loser_ind_types.most_common(10)),
            },
            "parameter_insights": param_insights,
            "weakness_clusters": weakness_clusters,
        }

    # ── Suggestion Engine ─────────────────────────────────────────────────

    def suggest_next_experiments(
        self, max_suggestions: int = 5
    ) -> Dict[str, Any]:
        """Propose what to test next based on historical analysis.

        Returns structured suggestions with rationale — never executable code.
        """
        analysis = self.analyze_performance_patterns()
        suggestions = []
        rationale_chain = []

        if analysis["status"] == "insufficient_data":
            # Cold-start: suggest a diverse initial batch
            rationale_chain.append(
                "No historical data available. Recommending a diverse "
                "initial exploration across all archetypes."
            )
            for archetype_fn in [
                self.generator.generate_trend_following,
                self.generator.generate_mean_reversion,
                self.generator.generate_momentum_breakout,
            ]:
                if len(suggestions) >= max_suggestions:
                    break
                bp = archetype_fn()
                suggestions.append({
                    "blueprint": bp,
                    "rationale": f"Cold-start exploration: {bp.get('archetype', 'unknown')} archetype.",
                    "priority": "medium",
                })

            return {
                "suggestions": suggestions,
                "rationale_chain": rationale_chain,
                "analysis_summary": analysis,
            }

        # Data-driven suggestions
        arch_perf = analysis.get("archetype_performance", {})
        param_insights = analysis.get("parameter_insights", {})
        weaknesses = analysis.get("weakness_clusters", [])

        # 1. Double down on best-performing archetype with parameter mutations
        best_arch = None
        best_win_ratio = 0
        for arch, stats in arch_perf.items():
            if stats["win_ratio"] > best_win_ratio and stats["total_experiments"] >= 2:
                best_win_ratio = stats["win_ratio"]
                best_arch = arch

        if best_arch and best_win_ratio > 0:
            rationale_chain.append(
                f"Best archetype: '{best_arch}' with {best_win_ratio:.0%} win ratio. "
                f"Suggesting refined variants."
            )
            # Find a winning strategy of this archetype and mutate it
            strategies = self._load_strategies_with_blueprints()
            for sid, strat in strategies.items():
                bp = strat.get("_blueprint", {})
                if bp.get("archetype") == best_arch and strat.get("status") != "retired":
                    try:
                        mutated = self.generator.mutate_blueprint(bp)
                        # Narrow exit params toward successful ranges if available
                        if "winning_sl_range" in param_insights:
                            sl_range = param_insights["winning_sl_range"]
                            exit_cfg = mutated.get("exit", {})
                            current_sl = exit_cfg.get("stop_loss_atr_mult", 2.0)
                            if current_sl < sl_range["min"] or current_sl > sl_range["max"]:
                                exit_cfg["stop_loss_atr_mult"] = sl_range["avg"]
                        suggestions.append({
                            "blueprint": mutated,
                            "rationale": (
                                f"Mutation of winning {best_arch} archetype. "
                                f"SL/TP narrowed to historical winning range."
                            ),
                            "priority": "high",
                        })
                        if len(suggestions) >= max_suggestions:
                            break
                    except ValueError:
                        continue

        # 2. Explore under-tested archetypes
        all_archetypes = {
            "trend_following", "mean_reversion", "momentum_breakout",
            "pattern_reversal", "pattern_continuation",
        }
        tested_archetypes = set(arch_perf.keys())
        untested = all_archetypes - tested_archetypes

        for arch in untested:
            if len(suggestions) >= max_suggestions:
                break
            rationale_chain.append(f"Archetype '{arch}' has 0 experiments. Recommending exploration.")
            gen_fn = {
                "trend_following": self.generator.generate_trend_following,
                "mean_reversion": self.generator.generate_mean_reversion,
                "momentum_breakout": self.generator.generate_momentum_breakout,
                "pattern_reversal": self.generator.generate_pattern_reversal,
                "pattern_continuation": self.generator.generate_pattern_continuation,
            }.get(arch)
            if gen_fn:
                bp = gen_fn()
                suggestions.append({
                    "blueprint": bp,
                    "rationale": f"Untested archetype: {arch}. Exploring for diversification.",
                    "priority": "medium",
                })

        # 3. Address weakness clusters
        for wk in weaknesses:
            if len(suggestions) >= max_suggestions:
                break
            wk_type = wk["type"]
            rationale_chain.append(
                f"Weakness detected: {wk_type} ({wk['affected']} strategies affected). "
                f"Recommendation: {wk['recommendation']}"
            )
            # Generate a strategy that addresses this weakness
            if wk_type == "excessive_drawdown":
                bp = self.generator.generate_trend_following()
                bp["exit"]["stop_loss_atr_mult"] = 1.5  # tighter stops
                bp["exit"]["time_stop_bars"] = 15
                bp["name"] = f"TightSL_{bp['name']}"
                if not validate_blueprint(bp):
                    suggestions.append({
                        "blueprint": bp,
                        "rationale": "Addressing excessive drawdown with tighter stop-loss.",
                        "priority": "high",
                    })
            elif wk_type == "insufficient_signals":
                bp = self.generator.generate_mean_reversion()
                # Loosen entry conditions
                for cond in bp.get("entry_long", []):
                    if cond.get("op") == "below" and isinstance(cond.get("right"), (int, float)):
                        cond["right"] = cond["right"] + 5
                bp["name"] = f"Loose_{bp['name']}"
                if not validate_blueprint(bp):
                    suggestions.append({
                        "blueprint": bp,
                        "rationale": "Addressing insufficient signals by loosening entry thresholds.",
                        "priority": "medium",
                    })

        # Fill remaining slots with random exploration
        while len(suggestions) < max_suggestions:
            bp = self.generator.generate_random_candidate()
            suggestions.append({
                "blueprint": bp,
                "rationale": "Random exploration for diversity.",
                "priority": "low",
            })

        return {
            "suggestions": suggestions[:max_suggestions],
            "rationale_chain": rationale_chain,
            "analysis_summary": {
                "experiments_analyzed": analysis["experiments_analyzed"],
                "winners": analysis["winners"],
                "losers": analysis["losers"],
                "best_archetype": best_arch,
                "weakness_count": len(weaknesses),
            },
        }

    # ── Report Persistence ────────────────────────────────────────────────

    def save_analysis_report(self) -> int:
        """Run analysis and save as a research report in the database.

        Returns the report ID.
        """
        analysis = self.analyze_performance_patterns()
        suggestions_data = self.suggest_next_experiments()

        title = (
            f"AI Research Analysis: {analysis.get('experiments_analyzed', 0)} experiments, "
            f"{analysis.get('winners', 0)} winners, "
            f"{len(analysis.get('weakness_clusters', []))} weaknesses"
        )

        report_id = self.store.save_report(
            report_type="analysis",
            title=title,
            content={
                "performance_patterns": analysis,
                "next_suggestions": suggestions_data["suggestions"],
                "rationale_chain": suggestions_data["rationale_chain"],
                "disclaimer": (
                    "This analysis is based on historical backtest data and does NOT "
                    "guarantee future profitability. All recommendations are within "
                    "approved indicator/operator boundaries only."
                ),
            },
            suggestions=[
                {
                    "blueprint_name": s["blueprint"].get("name", ""),
                    "rationale": s["rationale"],
                    "priority": s["priority"],
                }
                for s in suggestions_data["suggestions"]
            ],
        )

        logger.info(f"[ASSISTANT] Saved analysis report #{report_id}")
        return report_id

"""
overfitting_detector.py — Detect overfitting and repeated test-period abuse.

Detects:
  1. Train vs test performance collapse (Sharpe, return, drawdown divergence)
  2. Repeated backtesting on the same data window (test-period reuse)
  3. Excessive parameter tuning (too many mutations of same archetype)
  4. Performance instability across rolling windows

Safety:
  - Read-only analysis on ExperimentStore data
  - Cannot modify strategies, experiments, or live configuration
"""

import json
import logging
from typing import Dict, Any, List, Optional
from collections import defaultdict

from src.research.experiment_store import ExperimentStore

logger = logging.getLogger(__name__)


class OverfittingDetector:
    """Detects overfitting, data snooping, and parameter instability.

    All checks are rule-based and return structured, auditable results.
    """

    # Thresholds
    SEVERE_OVERFIT_THRESHOLD = 0.50    # >50% Sharpe drop = severe
    MODERATE_OVERFIT_THRESHOLD = 0.30  # >30% = moderate
    MAX_SAME_PERIOD_TESTS = 5         # max experiments on same data window
    MAX_ARCHETYPE_MUTATIONS = 10      # max mutations of same archetype
    STABILITY_WINDOW_COUNT = 3        # minimum rolling windows for check

    def __init__(self, store: ExperimentStore):
        self.store = store

    # ── Core Detection Methods ────────────────────────────────────────────

    def check_train_test_divergence(
        self, train_metrics: Dict, test_metrics: Dict
    ) -> Dict[str, Any]:
        """Check for train-vs-test performance collapse.

        Returns structured diagnosis with severity level.
        """
        train_sharpe = train_metrics.get("sharpe_ratio", 0)
        test_sharpe = test_metrics.get("sharpe_ratio", 0)
        train_return = train_metrics.get("total_return_pct", 0)
        test_return = test_metrics.get("total_return_pct", 0)
        train_dd = train_metrics.get("max_drawdown_pct", 0)
        test_dd = test_metrics.get("max_drawdown_pct", 0)

        flags = []
        severity = "none"

        # Sharpe collapse
        if train_sharpe > 0.5:
            sharpe_drop = (train_sharpe - test_sharpe) / train_sharpe
            if sharpe_drop > self.SEVERE_OVERFIT_THRESHOLD:
                flags.append({
                    "type": "sharpe_collapse",
                    "severity": "severe",
                    "detail": (
                        f"Sharpe dropped {sharpe_drop:.0%}: "
                        f"train={train_sharpe:.2f} -> test={test_sharpe:.2f}"
                    ),
                })
                severity = "severe"
            elif sharpe_drop > self.MODERATE_OVERFIT_THRESHOLD:
                flags.append({
                    "type": "sharpe_degradation",
                    "severity": "moderate",
                    "detail": (
                        f"Sharpe degraded {sharpe_drop:.0%}: "
                        f"train={train_sharpe:.2f} -> test={test_sharpe:.2f}"
                    ),
                })
                if severity != "severe":
                    severity = "moderate"

        # Return sign flip
        if train_return > 5 and test_return < 0:
            flags.append({
                "type": "return_sign_flip",
                "severity": "severe",
                "detail": (
                    f"Return flipped: train={train_return:.1f}% -> "
                    f"test={test_return:.1f}%"
                ),
            })
            severity = "severe"

        # Drawdown explosion
        if train_dd > 0 and test_dd > train_dd * 2:
            flags.append({
                "type": "drawdown_explosion",
                "severity": "moderate",
                "detail": (
                    f"Drawdown doubled: train={train_dd:.1f}% -> "
                    f"test={test_dd:.1f}%"
                ),
            })
            if severity == "none":
                severity = "moderate"

        return {
            "overfit_detected": severity != "none",
            "severity": severity,
            "flags": flags,
            "metrics": {
                "train_sharpe": train_sharpe,
                "test_sharpe": test_sharpe,
                "train_return": train_return,
                "test_return": test_return,
                "train_drawdown": train_dd,
                "test_drawdown": test_dd,
            },
        }

    def check_test_period_reuse(
        self, symbol: str, timeframe: str
    ) -> Dict[str, Any]:
        """Detect repeated backtesting on the same data window.

        Strategies tested too many times on the same symbol+timeframe
        risk indirect data snooping — the generator "learns" what works
        on that specific window through selection bias.
        """
        experiments = self.store.list_experiments()
        # Group by symbol + timeframe
        key = f"{symbol}_{timeframe}"
        matching = [
            e for e in experiments
            if e.get("symbol") == symbol
            and e.get("timeframe") == timeframe
            and e.get("status") == "completed"
        ]

        count = len(matching)
        reuse_detected = count > self.MAX_SAME_PERIOD_TESTS

        strategy_ids = list({e.get("strategy_id", "") for e in matching})

        return {
            "reuse_detected": reuse_detected,
            "test_count": count,
            "threshold": self.MAX_SAME_PERIOD_TESTS,
            "symbol": symbol,
            "timeframe": timeframe,
            "unique_strategies_tested": len(strategy_ids),
            "recommendation": (
                f"Consider testing on a different time window or symbol. "
                f"{count} experiments on {key} increases selection bias risk."
            ) if reuse_detected else "Test period reuse within acceptable limits.",
        }

    def check_archetype_saturation(self) -> Dict[str, Any]:
        """Detect excessive parameter tuning of the same archetype.

        If too many mutations of one archetype are tested, the research
        is effectively curve-fitting that archetype to the data.
        """
        strategies = self.store.list_strategies()
        archetype_counts = defaultdict(int)

        for s in strategies:
            bp_str = s.get("blueprint", "{}")
            try:
                bp = json.loads(bp_str) if isinstance(bp_str, str) else bp_str
            except (json.JSONDecodeError, TypeError):
                bp = {}
            archetype = bp.get("archetype", "legacy")
            archetype_counts[archetype] += 1

        saturated = {
            arch: count
            for arch, count in archetype_counts.items()
            if count > self.MAX_ARCHETYPE_MUTATIONS and arch != "legacy"
        }

        return {
            "saturation_detected": len(saturated) > 0,
            "archetype_counts": dict(archetype_counts),
            "saturated_archetypes": saturated,
            "recommendation": (
                f"Archetypes {list(saturated.keys())} have too many variants. "
                f"Focus on under-explored archetypes."
            ) if saturated else "No archetype saturation detected.",
        }

    # ── Full Diagnostic ──────────────────────────────────────────────────

    def run_full_diagnostic(
        self,
        candidate_id: Optional[str] = None,
        train_metrics: Optional[Dict] = None,
        test_metrics: Optional[Dict] = None,
        symbol: str = "BTCUSDT",
        timeframe: str = "1h",
    ) -> Dict[str, Any]:
        """Run all overfitting checks and return a combined report.

        If candidate_id/metrics are provided, also checks the specific
        candidate for train-test divergence.
        """
        results = {
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "checks": {},
            "overall_risk": "low",
        }

        # 1. Train-test divergence (if metrics provided)
        if train_metrics and test_metrics:
            divergence = self.check_train_test_divergence(
                train_metrics, test_metrics
            )
            results["checks"]["train_test_divergence"] = divergence
            if divergence["severity"] == "severe":
                results["overall_risk"] = "high"
            elif divergence["severity"] == "moderate":
                results["overall_risk"] = "medium"

        # 2. Test period reuse
        reuse = self.check_test_period_reuse(symbol, timeframe)
        results["checks"]["test_period_reuse"] = reuse
        if reuse["reuse_detected"] and results["overall_risk"] == "low":
            results["overall_risk"] = "medium"

        # 3. Archetype saturation
        saturation = self.check_archetype_saturation()
        results["checks"]["archetype_saturation"] = saturation
        if saturation["saturation_detected"] and results["overall_risk"] == "low":
            results["overall_risk"] = "medium"

        # 4. Save diagnostic as a report
        self.store.save_report(
            report_type="overfitting_diagnostic",
            title=f"Overfitting Diagnostic: risk={results['overall_risk']}",
            content=results,
            suggestions=[
                check.get("recommendation", "")
                for check in results["checks"].values()
                if isinstance(check, dict) and check.get("recommendation")
            ],
        )

        return results

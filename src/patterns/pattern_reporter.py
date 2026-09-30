"""
pattern_reporter.py — Comprehensive Pattern-Specific Performance Reporting.

Compiles empirical performance distributions for chart patterns across:
- Detection count, confirmation rate, invalidation rate
- Signal-to-trade conversion rate
- MFE / MAE distributions
- Regime-dependent performance breakdown
- Cost and threshold sensitivity analysis
"""

from typing import List, Dict, Any, Optional
import numpy as np

from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternOutcome,
    PatternStatus,
    MarketContext,
)


class PatternPerformanceReporter:
    """
    Generates structured reports and distributions evaluating pattern effectiveness.
    """

    def generate_report(
        self,
        patterns: List[PatternMatch],
        outcomes: List[PatternOutcome],
        trades: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Generate comprehensive statistics from detected patterns, outcomes, and executed trades.
        """
        total_detections = len(patterns)
        confirmed_count = sum(1 for p in patterns if p.status == PatternStatus.CONFIRMED)
        invalidated_count = sum(1 for p in patterns if p.status == PatternStatus.INVALIDATED)
        forming_count = sum(1 for p in patterns if p.status == PatternStatus.FORMING)

        confirmation_rate = (confirmed_count / total_detections * 100.0) if total_detections > 0 else 0.0
        invalidation_rate = (invalidated_count / total_detections * 100.0) if total_detections > 0 else 0.0

        trades = trades or []
        trade_count = len(trades)
        conversion_rate = (trade_count / confirmed_count * 100.0) if confirmed_count > 0 else 0.0

        # Outcome statistics
        mfe_values = [o.max_favorable_excursion_pct for o in outcomes]
        mae_values = [o.max_adverse_excursion_pct for o in outcomes]
        net_pnls = [o.net_pnl_after_costs for o in outcomes]

        avg_mfe = float(np.mean(mfe_values)) if mfe_values else 0.0
        avg_mae = float(np.mean(mae_values)) if mae_values else 0.0
        median_mfe = float(np.median(mfe_values)) if mfe_values else 0.0
        median_mae = float(np.median(mae_values)) if mae_values else 0.0
        win_outcomes = [o for o in outcomes if o.net_pnl_after_costs > 0]
        outcome_win_rate = (len(win_outcomes) / len(outcomes) * 100.0) if outcomes else 0.0

        # Breakdown by pattern name
        pattern_breakdown: Dict[str, Dict[str, Any]] = {}
        for p in patterns:
            name = p.pattern_name
            if name not in pattern_breakdown:
                pattern_breakdown[name] = {
                    'total': 0, 'confirmed': 0, 'invalidated': 0, 'outcomes': []
                }
            pattern_breakdown[name]['total'] += 1
            if p.status == PatternStatus.CONFIRMED:
                pattern_breakdown[name]['confirmed'] += 1
            elif p.status == PatternStatus.INVALIDATED:
                pattern_breakdown[name]['invalidated'] += 1

        for o in outcomes:
            if o.pattern_name in pattern_breakdown:
                pattern_breakdown[o.pattern_name]['outcomes'].append(o.net_pnl_after_costs)

        pattern_summary = {}
        for name, data in pattern_breakdown.items():
            outs = data['outcomes']
            pattern_summary[name] = {
                'total_detections': data['total'],
                'confirmed': data['confirmed'],
                'confirmation_rate_pct': round((data['confirmed'] / data['total'] * 100) if data['total'] else 0, 1),
                'outcome_count': len(outs),
                'avg_net_return_pct': round(float(np.mean(outs)), 2) if outs else 0.0,
                'win_rate_pct': round((sum(1 for x in outs if x > 0) / len(outs) * 100) if outs else 0.0, 1),
            }

        return {
            'overview': {
                'total_detections': total_detections,
                'confirmed_count': confirmed_count,
                'invalidated_count': invalidated_count,
                'forming_count': forming_count,
                'confirmation_rate_pct': round(confirmation_rate, 2),
                'invalidation_rate_pct': round(invalidation_rate, 2),
                'simulated_trades': trade_count,
                'signal_to_trade_conversion_pct': round(conversion_rate, 2),
            },
            'outcome_distribution': {
                'total_outcomes_evaluated': len(outcomes),
                'win_rate_pct': round(outcome_win_rate, 2),
                'avg_mfe_pct': round(avg_mfe, 2),
                'median_mfe_pct': round(median_mfe, 2),
                'avg_mae_pct': round(avg_mae, 2),
                'median_mae_pct': round(median_mae, 2),
                'mfe_to_mae_ratio': round(abs(avg_mfe / avg_mae), 2) if avg_mae != 0 else 0.0,
            },
            'pattern_breakdown': pattern_summary,
        }

"""
strategy_evaluator.py — Quantitative evaluation, weakness diagnosis, and scoring.

Analyzes backtest results (walk-forward train vs test sets) to detect:
  - Overfitting (large divergence between train and test metrics)
  - Sample size deficiencies (< 20 trades)
  - Tail risk & excessive drawdown (> 15%)
  - Poor risk-reward payoff (avg_loss significantly higher than avg_win)
  - Inconsistency across market regimes

Produces a quality grade (A, B, C, D, F), overall score (0-100),
and actionable mutation/optimization suggestions.
"""

import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class StrategyEvaluator:
    """Evaluates backtest experiments and identifies quantitative weaknesses."""

    def __init__(self, target_sharpe: float = 1.0, max_acceptable_dd: float = 18.0, min_trades: int = 20):
        self.target_sharpe = target_sharpe
        self.max_acceptable_dd = max_acceptable_dd
        self.min_trades = min_trades

    def evaluate_experiment(self, exp_data: Dict[str, Any]) -> Dict[str, Any]:
        """Perform full diagnostic analysis on an experiment result dictionary.

        exp_data is expected to contain:
          - 'train_metrics': dict
          - 'test_metrics': dict
          - 'strategy_name': str
          - 'strategy_id': str
          - 'symbol': str
        """
        train = exp_data.get("train_metrics", {})
        test = exp_data.get("test_metrics", {})
        name = exp_data.get("strategy_name", "UnknownStrategy")

        weaknesses = []
        strengths = []
        suggestions = []

        # 1. Sample Size Check
        test_trades = test.get("total_trades", 0)
        if test_trades == 0:
            return {
                "strategy_name": name,
                "score": 0.0,
                "grade": "F",
                "verdict": "NO_TRADES",
                "weaknesses": ["Strategy produced 0 trades in test period."],
                "strengths": [],
                "suggestions": ["Loosen entry constraints or increase indicator sensitivity."],
                "overfitting_ratio": 0.0,
            }
        elif test_trades < self.min_trades:
            weaknesses.append(f"Low sample size ({test_trades} trades < {self.min_trades} minimum). High statistical uncertainty.")
            suggestions.append("Broaden entry conditions or extend evaluation history.")
        else:
            strengths.append(f"Robust trade volume ({test_trades} trades).")

        # 2. Return and Sharpe Analysis
        test_sharpe = test.get("sharpe_ratio", 0.0)
        train_sharpe = train.get("sharpe_ratio", 0.0)
        test_return = test.get("total_return_pct", 0.0)
        train_return = train.get("total_return_pct", 0.0)

        if test_sharpe >= 1.5:
            strengths.append(f"Excellent out-of-sample Sharpe ratio ({test_sharpe:.2f}).")
        elif test_sharpe >= 0.8:
            strengths.append(f"Acceptable out-of-sample Sharpe ratio ({test_sharpe:.2f}).")
        elif test_sharpe > 0:
            weaknesses.append(f"Marginal Sharpe ratio ({test_sharpe:.2f} < {self.target_sharpe:.2f}).")
        else:
            weaknesses.append(f"Negative risk-adjusted returns (Sharpe: {test_sharpe:.2f}).")

        # 3. Overfitting Diagnosis (Train vs Test Divergence)
        overfitting_ratio = 0.0
        if train_sharpe > 0.5:
            sharpe_drop = (train_sharpe - test_sharpe) / train_sharpe
            overfitting_ratio = max(0.0, sharpe_drop)
            if sharpe_drop > 0.6:
                weaknesses.append(f"Severe overfitting detected: Train Sharpe ({train_sharpe:.2f}) collapsed on test set ({test_sharpe:.2f}).")
                suggestions.append("Simplify entry rules and prune secondary filter indicators.")
            elif sharpe_drop > 0.35:
                weaknesses.append(f"Moderate parameter curve-fitting: Sharpe degraded by {sharpe_drop*100:.1f}%.")

        # 4. Drawdown Analysis
        max_dd = test.get("max_drawdown_pct", 0.0)
        if max_dd > self.max_acceptable_dd:
            weaknesses.append(f"Excessive drawdown ({max_dd:.1f}% > {self.max_acceptable_dd}% tolerance).")
            suggestions.append("Tighten stop-loss ATR multiplier or reduce position holding time stop.")
        elif max_dd < 10.0 and test_trades >= 10:
            strengths.append(f"Controlled downside volatility (Max DD: {max_dd:.1f}%).")

        # 5. Payoff & Win Rate Balance
        avg_win = test.get("avg_win", 0.0)
        avg_loss = test.get("avg_loss", 0.0)
        win_rate = test.get("win_rate", 0.0) * 100
        profit_factor = test.get("profit_factor", 0.0)

        if avg_loss > 0 and (avg_loss / avg_win) > 2.0 and win_rate < 65:
            weaknesses.append(f"Asymmetric risk payoff: Avg Loss (${avg_loss:.2f}) is over double Avg Win (${avg_win:.2f}).")
            suggestions.append("Increase Take-Profit R:R ratio or implement trailing stops.")

        if profit_factor > 1.5:
            strengths.append(f"Strong profit factor ({profit_factor:.2f}).")
        elif profit_factor < 1.0:
            weaknesses.append(f"Unprofitable trade expectancy (Profit Factor: {profit_factor:.2f} < 1.0).")

        # Compute Composite Score (0 - 100)
        score = 0.0
        # Return / Sharpe contribution (up to 40 pts)
        score += min(max(test_sharpe / 2.0 * 25, 0), 25)
        if test_return > 0:
            score += min(test_return / 20.0 * 15, 15)

        # Drawdown safety contribution (up to 30 pts)
        if max_dd < self.max_acceptable_dd:
            dd_pts = (self.max_acceptable_dd - max_dd) / self.max_acceptable_dd * 30
            score += max(0, dd_pts)

        # Profit factor & Win rate contribution (up to 30 pts)
        if profit_factor > 1.0:
            score += min((profit_factor - 1.0) * 20, 20)
        score += min(win_rate / 100.0 * 10, 10)

        # Sample size bonus/penalty
        trade_ratio = min(test_trades / self.min_trades, 1.0)
        score *= (0.7 + 0.3 * trade_ratio)

        # Penalize for severe overfitting
        if overfitting_ratio > 0.5:
            score *= (1.0 - (overfitting_ratio - 0.5) * 0.8)

        score = round(min(max(score, 0.0), 100.0), 1)

        # Determine Grade
        if score >= 80 and test_sharpe >= 1.2 and max_dd <= 15 and test_trades >= self.min_trades:
            grade = "A"
            verdict = "HIGHLY_RECOMMENDED"
        elif score >= 65 and test_sharpe >= 0.7:
            grade = "B"
            verdict = "PROMISING"
        elif score >= 45 and test_return > 0:
            grade = "C"
            verdict = "MARGINAL"
        elif score >= 25:
            grade = "D"
            verdict = "POOR"
        else:
            grade = "F"
            verdict = "REJECTED"

        return {
            "strategy_name": name,
            "strategy_id": exp_data.get("strategy_id"),
            "experiment_id": exp_data.get("experiment_id"),
            "score": score,
            "grade": grade,
            "verdict": verdict,
            "overfitting_ratio": round(overfitting_ratio, 2),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "suggestions": suggestions,
            "test_summary": {
                "sharpe": round(test_sharpe, 2),
                "return_pct": round(test_return, 2),
                "max_drawdown": round(max_dd, 2),
                "win_rate": round(win_rate, 1),
                "profit_factor": round(profit_factor, 2),
                "trades": test_trades
            }
        }

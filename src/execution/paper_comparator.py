"""
paper_comparator.py — Compare Paper Trading Performance Against Backtest Expectations.

Continuously compares live forward paper-trading results against out-of-sample backtest
baselines. Identifies strategy decay, regime divergence, and slippage/fee impact.
"""

import math
import statistics
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class PaperVsBacktestComparator:
    """
    Evaluates live paper trading metrics against expected backtest metrics.
    """

    def __init__(self, min_eval_trades: int = 10):
        self.min_eval_trades = min_eval_trades

    def calculate_paper_metrics(self, closed_trades: List[Dict[str, Any]],
                                equity_curve: Optional[List[float]] = None) -> Dict[str, Any]:
        """Compute key quantitative metrics from closed paper trades."""
        if not closed_trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate_pct': 0.0,
                'total_pnl': 0.0,
                'profit_factor': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown_pct': 0.0,
                'avg_trade_pnl': 0.0,
            }

        pnls = [t.get('net_pnl', 0.0) for t in closed_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_trades = len(pnls)
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate_pct = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0

        total_win = sum(wins)
        total_loss = abs(sum(losses))
        profit_factor = (total_win / total_loss) if total_loss > 0 else (99.0 if total_win > 0 else 0.0)

        # Sharpe calculation from trade returns
        sharpe = 0.0
        if total_trades >= 3:
            mean_pnl = statistics.mean(pnls)
            std_pnl = statistics.stdev(pnls)
            if std_pnl > 0:
                # Annualized trade Sharpe approximation
                sharpe = (mean_pnl / std_pnl) * math.sqrt(min(total_trades, 252))

        # Max drawdown from equity curve
        max_dd = 0.0
        if equity_curve and len(equity_curve) > 1:
            peak = equity_curve[0]
            for eq in equity_curve:
                if eq > peak:
                    peak = eq
                if peak > 0:
                    dd = ((peak - eq) / peak) * 100.0
                    if dd > max_dd:
                        max_dd = dd

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate_pct': round(win_rate_pct, 2),
            'total_pnl': round(sum(pnls), 2),
            'profit_factor': round(profit_factor, 2),
            'sharpe_ratio': round(sharpe, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'avg_trade_pnl': round(statistics.mean(pnls), 2) if pnls else 0.0,
        }

    def compare(self, paper_metrics: Dict[str, Any],
                backtest_metrics: Dict[str, Any],
                strategy_name: str = "") -> Dict[str, Any]:
        """
        Compare live paper results against historical backtest metrics.
        Returns divergence status: NORMAL, WATCHLIST, CRITICAL, or INSUFFICIENT_DATA.
        """
        trade_count = paper_metrics.get('total_trades', 0)
        bt_trades = backtest_metrics.get('total_trades', 0)
        bt_sharpe = backtest_metrics.get('sharpe_ratio', 0.0)
        bt_win_rate = backtest_metrics.get('win_rate_pct', 0.0)
        bt_max_dd = backtest_metrics.get('max_drawdown_pct', 0.0)
        bt_pf = backtest_metrics.get('profit_factor', 1.0)

        paper_sharpe = paper_metrics.get('sharpe_ratio', 0.0)
        paper_win_rate = paper_metrics.get('win_rate_pct', 0.0)
        paper_max_dd = paper_metrics.get('max_drawdown_pct', 0.0)
        paper_pf = paper_metrics.get('profit_factor', 1.0)

        divergence_notes = []
        status = "NORMAL"

        if trade_count < self.min_eval_trades:
            return {
                'status': 'INSUFFICIENT_DATA',
                'strategy_name': strategy_name,
                'paper_trades': trade_count,
                'required_trades': self.min_eval_trades,
                'notes': [f"Collecting sample: {trade_count}/{self.min_eval_trades} trades executed."],
                'paper_metrics': paper_metrics,
                'backtest_metrics': backtest_metrics,
                'divergence': {},
            }

        # Check 1: Win rate decay
        wr_diff = paper_win_rate - bt_win_rate
        if wr_diff < -15.0:
            divergence_notes.append(
                f"Win rate degraded by {abs(wr_diff):.1f}% (Paper: {paper_win_rate:.1f}% vs BT: {bt_win_rate:.1f}%)"
            )
            status = "WATCHLIST"

        # Check 2: Sharpe decay
        sharpe_diff = paper_sharpe - bt_sharpe
        if paper_sharpe < 0:
            divergence_notes.append(f"Paper Sharpe is negative ({paper_sharpe:.2f} vs BT: {bt_sharpe:.2f})")
            status = "CRITICAL"
        elif bt_sharpe > 0 and (paper_sharpe / bt_sharpe) < 0.6:
            divergence_notes.append(
                f"Sharpe ratio dropped >40% (Paper: {paper_sharpe:.2f} vs BT: {bt_sharpe:.2f})"
            )
            status = "WATCHLIST" if status != "CRITICAL" else status

        # Check 3: Drawdown overrun
        if bt_max_dd > 0 and paper_max_dd > (bt_max_dd * 1.5):
            divergence_notes.append(
                f"Paper drawdown ({paper_max_dd:.1f}%) exceeded backtest max DD ({bt_max_dd:.1f}%) by >50%"
            )
            status = "CRITICAL"

        # Check 4: Profit factor collapse
        if paper_pf < 0.9 and bt_pf >= 1.2:
            divergence_notes.append(f"Profit factor sub-1.0 (Paper: {paper_pf:.2f} vs BT: {bt_pf:.2f})")
            if status != "CRITICAL":
                status = "WATCHLIST"

        if not divergence_notes:
            divergence_notes.append("Performance aligns with backtest statistical parameters.")

        return {
            'status': status,
            'strategy_name': strategy_name,
            'paper_trades': trade_count,
            'notes': divergence_notes,
            'paper_metrics': paper_metrics,
            'backtest_metrics': backtest_metrics,
            'divergence': {
                'sharpe_diff': round(sharpe_diff, 2),
                'win_rate_diff_pct': round(wr_diff, 2),
                'drawdown_ratio': round(paper_max_dd / max(bt_max_dd, 0.01), 2),
            }
        }

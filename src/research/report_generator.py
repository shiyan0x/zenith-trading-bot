"""
report_generator.py — Formats and exports research findings and campaign reports.

Generates human-readable Markdown digests and persists structured JSON
summaries into SQLite and the data/reports/ directory.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from src.research.experiment_store import ExperimentStore

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
REPORTS_DIR = os.path.join(_PROJECT_ROOT, "data", "reports")


class ReportGenerator:
    """Generates Markdown digests and saves reports in SQLite and filesystem."""

    def __init__(self, store: Optional[ExperimentStore] = None):
        self.store = store
        os.makedirs(REPORTS_DIR, exist_ok=True)

    def generate_campaign_report(
        self,
        campaign_name: str,
        evaluations: List[Dict[str, Any]],
        symbol: str = "BTCUSDT",
        timeframe: str = "1h"
    ) -> Dict[str, Any]:
        """Produce a comprehensive Markdown report from a list of strategy evaluations."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Sort by evaluation score descending
        sorted_evals = sorted(evaluations, key=lambda x: x.get("score", 0), reverse=True)

        lines = [
            f"# 🧪 AI Strategy Research Lab — Campaign Report",
            f"",
            f"> **Campaign**: `{campaign_name}`  ",
            f"> **Generated at**: `{now_str}`  ",
            f"> **Market Tested**: `{symbol}` (`{timeframe}`)  ",
            f"> **Candidates Evaluated**: `{len(sorted_evals)}`  ",
            f"",
            f"---",
            f"",
            f"## 🏆 Executive Summary & Leaderboard",
            f"",
            f"| Rank | Strategy Name | Grade | Score | Sharpe | Out-of-Sample Return | Max Drawdown | Trades | Verdict |",
            f"|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
        ]

        for rank, item in enumerate(sorted_evals, 1):
            ts = item.get("test_summary", {})
            grade_badge = f"**{item.get('grade')}**"
            verdict = item.get("verdict", "N/A")
            sharpe = ts.get("sharpe", 0.0)
            ret = ts.get("return_pct", 0.0)
            dd = ts.get("max_drawdown", 0.0)
            trades = ts.get("trades", 0)
            score = item.get("score", 0.0)
            name = item.get("strategy_name", "Unknown")

            lines.append(
                f"| {rank} | `{name}` | {grade_badge} | {score} | {sharpe:.2f} | {ret:+.2f}% | {dd:.2f}% | {trades} | `{verdict}` |"
            )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 🔬 Detailed Strategy Diagnostics",
            f""
        ])

        top_candidates = sorted_evals[:5]
        for item in top_candidates:
            name = item.get("strategy_name")
            grade = item.get("grade")
            score = item.get("score")
            ts = item.get("test_summary", {})
            strengths = item.get("strengths", [])
            weaknesses = item.get("weaknesses", [])
            suggestions = item.get("suggestions", [])

            lines.extend([
                f"### Strategy: `{name}` (Grade: **{grade}**, Score: **{score}/100**)",
                f"- **Out-of-Sample Sharpe**: `{ts.get('sharpe', 0.0)}` | **Profit Factor**: `{ts.get('profit_factor', 0.0)}`",
                f"- **Win Rate**: `{ts.get('win_rate', 0.0)}%` | **Total Trades**: `{ts.get('trades', 0)}`",
                f"- **Max Drawdown**: `{ts.get('max_drawdown', 0.0)}%` | **Net Return**: `{ts.get('return_pct', 0.0)}%`",
                f""
            ])

            if strengths:
                lines.append(f"**Strengths:**")
                for s in strengths:
                    lines.append(f"- ✅ {s}")
                lines.append("")

            if weaknesses:
                lines.append(f"**Identified Weaknesses:**")
                for w in weaknesses:
                    lines.append(f"- ⚠️ {w}")
                lines.append("")

            if suggestions:
                lines.append(f"**AI Optimization Recommendations:**")
                for sug in suggestions:
                    lines.append(f"- 💡 {sug}")
                lines.append("")

            lines.append("---")
            lines.append("")

        md_content = "\n".join(lines)

        # Save to disk
        filename = f"campaign_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
        filepath = os.path.join(REPORTS_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)

        # Save to SQLite if store provided
        report_id = None
        if self.store:
            exp_ids = [e.get("experiment_id") for e in sorted_evals if e.get("experiment_id")]
            all_sugs = []
            for e in sorted_evals:
                for s in e.get("suggestions", []):
                    all_sugs.append({"strategy": e.get("strategy_name"), "suggestion": s})

            report_id = self.store.save_report(
                report_type="campaign",
                title=f"Campaign: {campaign_name} ({len(sorted_evals)} strategies)",
                content={
                    "campaign_name": campaign_name,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "leaderboard": sorted_evals,
                    "markdown_file": filepath
                },
                experiment_ids=exp_ids,
                suggestions=all_sugs
            )

        return {
            "report_id": report_id,
            "filepath": filepath,
            "markdown": md_content,
            "top_candidates": top_candidates
        }

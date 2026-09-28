"""
AI Strategy Research Lab
=========================
Self-contained research system for strategy generation, backtesting
experiments, performance analysis, and self-improvement.

This package is ISOLATED from the live trading path.
It calls INTO the existing bot modules (Backtester, FeeModel, indicators)
but never modifies them.

It cannot:
  - Place real or paper orders directly
  - Modify the live wallet or risk limits
  - Execute arbitrary Python code (zero eval/exec)
  - Change existing strategy behaviour
  - Bypass human approval gates for paper/live trading
"""

from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.generated_strategy import GeneratedStrategy, validate_blueprint
from src.research.strategy_generator import StrategyGenerator
from src.research.backtest_harness import BacktestHarness
from src.research.strategy_evaluator import StrategyEvaluator
from src.research.experiment_manager import ExperimentManager, ExperimentBudget
from src.research.research_assistant import ResearchAssistant
from src.research.promotion_gate import PromotionGate
from src.research.overfitting_detector import OverfittingDetector
from src.research.improvement_loop import ImprovementLoop, ImprovementConfig
from src.research.report_generator import ReportGenerator

__all__ = [
    "ExperimentStore",
    "StrategyRegistry",
    "GeneratedStrategy",
    "validate_blueprint",
    "StrategyGenerator",
    "BacktestHarness",
    "StrategyEvaluator",
    "ExperimentManager",
    "ExperimentBudget",
    "ResearchAssistant",
    "PromotionGate",
    "OverfittingDetector",
    "ImprovementLoop",
    "ImprovementConfig",
    "ReportGenerator",
]

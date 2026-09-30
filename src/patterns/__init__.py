"""
src.patterns — Chart Pattern Recognition & Learning System.

Provides geometric (reversals, continuations, triangles) and candlestick
pattern detection, market context analysis, outcome evaluation, and
optional computer vision classification.
"""

from src.patterns.pattern_detector import PatternDetector
from src.patterns.context_analyzer import MarketContextAnalyzer
from src.patterns.outcome_evaluator import PatternOutcomeEvaluator
from src.patterns.pattern_reporter import PatternPerformanceReporter

__all__ = [
    'PatternDetector',
    'MarketContextAnalyzer',
    'PatternOutcomeEvaluator',
    'PatternPerformanceReporter',
]

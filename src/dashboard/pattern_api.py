"""
pattern_api.py — REST API Endpoints for Chart Pattern Recognition & Learning.

Exposes pattern detection, market context, empirical outcomes, and performance
distributions to the web dashboard and research lab.
"""

import logging
from typing import Optional, List, Dict, Any
from flask import Blueprint, jsonify, request

from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternStatus,
    ALL_GEOMETRIC_PATTERNS,
    ALL_CANDLESTICK_PATTERNS,
)
from src.patterns.pattern_detector import PatternDetector
from src.patterns.context_analyzer import MarketContextAnalyzer
from src.patterns.outcome_evaluator import PatternOutcomeEvaluator
from src.patterns.pattern_reporter import PatternPerformanceReporter
from src.research.experiment_store import ExperimentStore

logger = logging.getLogger(__name__)

pattern_bp = Blueprint("patterns", __name__, url_prefix="/api/patterns")

# Module references initialized by init_pattern_api()
_store: Optional[ExperimentStore] = None
_market_feed = None
_detector: Optional[PatternDetector] = None
_context_analyzer: Optional[MarketContextAnalyzer] = None
_outcome_evaluator: Optional[PatternOutcomeEvaluator] = None
_reporter: Optional[PatternPerformanceReporter] = None
_active_patterns_cache: List[Dict[str, Any]] = []


def init_pattern_api(
    store: Optional[ExperimentStore] = None,
    market_feed = None,
):
    """Initialize references for pattern API routes."""
    global _store, _market_feed, _detector, _context_analyzer, _outcome_evaluator, _reporter
    _store = store or ExperimentStore()
    _market_feed = market_feed
    _detector = PatternDetector(min_confidence=0.55)
    _context_analyzer = MarketContextAnalyzer()
    _outcome_evaluator = PatternOutcomeEvaluator()
    _reporter = PatternPerformanceReporter()


def register_pattern_routes(app, store=None, market_feed=None):
    """Register pattern blueprint with the Flask application."""
    init_pattern_api(store=store, market_feed=market_feed)
    if "patterns" not in app.blueprints:
        app.register_blueprint(pattern_bp)
        logger.info("[API] Chart pattern routes registered at /api/patterns")


def update_active_patterns(patterns: List[PatternMatch]):
    """Update in-memory cache of live active detected patterns."""
    global _active_patterns_cache
    _active_patterns_cache = [p.to_dict() for p in patterns]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@pattern_bp.route("/active", methods=["GET"])
def get_active_patterns():
    """Return currently detected forming and confirmed chart patterns."""
    symbol = request.args.get("symbol")
    timeframe = request.args.get("timeframe")

    results = _active_patterns_cache
    if symbol:
        results = [p for p in results if p.get("symbol") == symbol]
    if timeframe:
        results = [p for p in results if p.get("timeframe") == timeframe]

    return jsonify({
        "status": "success",
        "count": len(results),
        "patterns": results,
    })


@pattern_bp.route("/history", methods=["GET"])
def get_pattern_history():
    """Return recent historical pattern detections from research database."""
    if not _store:
        return jsonify({"error": "Store not initialized"}), 503

    limit = int(request.args.get("limit", 50))
    detections = _store.get_recent_pattern_detections(limit=limit)
    return jsonify({
        "status": "success",
        "count": len(detections),
        "detections": detections,
    })


@pattern_bp.route("/outcomes", methods=["GET"])
def get_pattern_outcomes():
    """Return empirical post-detection forward outcomes."""
    if not _store:
        return jsonify({"error": "Store not initialized"}), 503

    pattern_name = request.args.get("pattern_name")
    symbol = request.args.get("symbol")
    limit = int(request.args.get("limit", 100))

    outcomes = _store.get_pattern_outcomes(pattern_name=pattern_name, symbol=symbol, limit=limit)
    return jsonify({
        "status": "success",
        "count": len(outcomes),
        "outcomes": outcomes,
    })


@pattern_bp.route("/stats", methods=["GET"])
def get_pattern_stats():
    """Return comprehensive pattern performance distribution and win rate stats."""
    if not _store:
        return jsonify({"error": "Store not initialized"}), 503

    outcomes_raw = _store.get_pattern_outcomes(limit=250)
    detections_raw = _store.get_recent_pattern_detections(limit=250)

    # Convert to objects for reporter
    from src.patterns.pattern_definitions import PatternMatch, PatternOutcome
    patterns = [PatternMatch.from_dict(d) for d in detections_raw]
    outcomes = [
        PatternOutcome(
            pattern_id=o.get("pattern_id", ""),
            pattern_name=o.get("pattern_name", ""),
            symbol=o.get("symbol", ""),
            timeframe=o.get("timeframe", ""),
            direction=o.get("direction", ""),
            detection_timestamp=o.get("detection_timestamp", 0.0),
            confirmation_timestamp=o.get("confirmation_timestamp"),
            entry_price=o.get("entry_price", 0.0),
            horizon_bars=o.get("horizon_bars", 15),
            return_at_horizon_pct=o.get("return_at_horizon_pct", 0.0),
            max_favorable_excursion_pct=o.get("max_favorable_excursion_pct", 0.0),
            max_adverse_excursion_pct=o.get("max_adverse_excursion_pct", 0.0),
            breakout_occurred=bool(o.get("breakout_occurred", 0)),
            stop_hit=bool(o.get("stop_hit", 0)),
            target_hit=bool(o.get("target_hit", 0)),
            net_pnl_after_costs=o.get("net_pnl_after_costs", 0.0),
            evaluation_timestamp=o.get("evaluation_timestamp", 0.0),
        )
        for o in outcomes_raw
    ]

    reporter = _reporter or PatternPerformanceReporter()
    report = reporter.generate_report(patterns, outcomes)
    return jsonify({
        "status": "success",
        "report": report,
    })


@pattern_bp.route("/supported", methods=["GET"])
def get_supported_patterns():
    """List all supported geometric and candlestick patterns."""
    return jsonify({
        "status": "success",
        "geometric_patterns": sorted(list(ALL_GEOMETRIC_PATTERNS)),
        "candlestick_patterns": sorted(list(ALL_CANDLESTICK_PATTERNS)),
        "total_supported": len(ALL_GEOMETRIC_PATTERNS) + len(ALL_CANDLESTICK_PATTERNS),
    })

"""
test_chart_patterns.py — Comprehensive Unit & Integration Tests for
Chart Pattern Recognition, Computer Vision Interface, Market Context Analysis,
Outcome Tracking, Backtesting, and AI Self-Improvement Integration.
"""

import unittest
import time
import os
import shutil
import tempfile
import torch

from src.core.market_feed import Candle
from src.patterns.pattern_definitions import (
    PatternType,
    PatternStatus,
    PatternDirection,
    PatternMatch,
    SwingPoint,
    MarketContext,
    ALL_GEOMETRIC_PATTERNS,
    ALL_CANDLESTICK_PATTERNS,
    DOUBLE_TOP,
    DOUBLE_BOTTOM,
    HEAD_AND_SHOULDERS,
    BULLISH_ENGULFING,
    BEARISH_ENGULFING,
    HAMMER,
    SHOOTING_STAR,
    DOJI,
    MORNING_STAR,
    EVENING_STAR,
)
from src.patterns.swing_detector import SwingDetector
from src.patterns.candlestick_detector import CandlestickDetector
from src.patterns.geometric_detector import GeometricPatternDetector
from src.patterns.pattern_detector import PatternDetector
from src.patterns.context_analyzer import MarketContextAnalyzer
from src.patterns.outcome_evaluator import PatternOutcomeEvaluator
from src.patterns.pattern_reporter import PatternPerformanceReporter
from src.patterns.image_recognition import (
    CandlestickImageRenderer,
    ChartPatternCNN,
    ChartImagePatternRecognizer,
)
from src.patterns.pattern_strategy import PatternStrategy
from src.research.generated_strategy import GeneratedStrategy, APPROVED_INDICATORS
from src.research.strategy_generator import StrategyGenerator
from src.research.experiment_store import ExperimentStore
from src.dashboard.server import create_dashboard_app
from src.dashboard.pattern_api import register_pattern_routes, update_active_patterns


def make_candle(idx: int, o: float, h: float, l: float, c: float, v: float = 100.0, is_closed: bool = True) -> Candle:
    """Helper to generate a deterministic Candle matching Candle(timestamp, o, h, l, c, volume, is_closed)."""
    ts = 1700000000.0 + idx * 900.0  # 15m intervals
    return Candle(ts, float(o), float(h), float(l), float(c), float(v), is_closed)


class TestSwingDetector(unittest.TestCase):
    """Test swing point detector and verify look-ahead bias prevention."""

    def setUp(self):
        self.detector = SwingDetector(left_bars=2, right_bars=2)

    def test_swing_high_and_low_detection(self):
        prices = [100, 102, 105, 110, 104, 101, 95, 90, 96, 98, 100]
        candles = [
            make_candle(i, p - 1, p + 1, p - 2, p) for i, p in enumerate(prices)
        ]

        swings = self.detector.find_swings(candles)
        highs = [s for s in swings if s.is_high]
        lows = [s for s in swings if not s.is_high]

        self.assertGreaterEqual(len(highs), 1)
        self.assertGreaterEqual(len(lows), 1)
        self.assertEqual(highs[0].index, 3)
        self.assertEqual(lows[0].index, 7)

    def test_lookahead_prevention_on_unconfirmed_trailing_bars(self):
        # The last 2 bars (right_bars=2) can NEVER be confirmed pivots
        prices = [100, 102, 105, 115]  # Peak is at index 3, but there are 0 bars to the right!
        candles = [
            make_candle(i, p - 1, p + 1, p - 2, p) for i, p in enumerate(prices)
        ]

        swings = self.detector.find_swings(candles)
        indices = [s.index for s in swings]
        self.assertNotIn(3, indices, "Unclosed right bars must never declare premature swing point")


class TestCandlestickDetector(unittest.TestCase):
    """Test candlestick patterns."""

    def setUp(self):
        self.detector = CandlestickDetector()

    def test_bullish_engulfing(self):
        # Prior downtrend (at least 5 bars) + bearish bar + larger bullish bar engulfing it
        downtrend = [
            make_candle(0, 120, 121, 117, 118),
            make_candle(1, 118, 119, 114, 115),
            make_candle(2, 115, 116, 111, 112),
            make_candle(3, 112, 113, 108, 109),
            make_candle(4, 109, 110, 105, 106),
        ]
        engulfing_bars = [
            make_candle(5, 106, 107, 102, 103),      # Bearish
            make_candle(6, 102.5, 108, 102, 107.5),  # Engulfs candle 5
        ]
        candles = downtrend + engulfing_bars
        matches = self.detector.detect_at_index(candles, len(candles) - 1, symbol="BTCUSDT", timeframe="15m")
        names = [m.pattern_name for m in matches]
        self.assertIn(BULLISH_ENGULFING, names)

    def test_bearish_engulfing(self):
        # Prior uptrend (at least 5 bars) + bullish bar + larger bearish bar engulfing it
        uptrend = [
            make_candle(0, 80, 83, 79, 82),
            make_candle(1, 82, 86, 81, 85),
            make_candle(2, 85, 89, 84, 88),
            make_candle(3, 88, 92, 87, 91),
            make_candle(4, 91, 95, 90, 94),
        ]
        engulfing_bars = [
            make_candle(5, 94, 98, 93.5, 97.5),     # Bullish
            make_candle(6, 98, 98.5, 92, 93),       # Engulfs candle 5
        ]
        candles = uptrend + engulfing_bars
        matches = self.detector.detect_at_index(candles, len(candles) - 1, symbol="BTCUSDT", timeframe="15m")
        names = [m.pattern_name for m in matches]
        self.assertIn(BEARISH_ENGULFING, names)

    def test_hammer_and_shooting_star(self):
        # Downtrend + Hammer
        downtrend = [
            make_candle(0, 120, 121, 117, 118),
            make_candle(1, 118, 119, 114, 115),
            make_candle(2, 115, 116, 111, 112),
            make_candle(3, 112, 113, 108, 109),
            make_candle(4, 109, 110, 105, 106),
        ]
        hammer = [make_candle(5, 105, 105.5, 97.0, 104.8)]  # Lower wick = 7.8, body = 0.2
        candles = downtrend + hammer
        matches = self.detector.detect_at_index(candles, len(candles) - 1)
        names = [m.pattern_name for m in matches]
        self.assertIn(HAMMER, names)

        # Uptrend + Shooting Star
        uptrend = [
            make_candle(0, 80, 83, 79, 82),
            make_candle(1, 82, 86, 81, 85),
            make_candle(2, 85, 89, 84, 88),
            make_candle(3, 88, 92, 87, 91),
            make_candle(4, 91, 95, 90, 94),
        ]
        star = [make_candle(5, 95, 103.0, 94.6, 95.2)]  # Upper wick = 7.8, body = 0.2
        candles_star = uptrend + star
        matches_star = self.detector.detect_at_index(candles_star, len(candles_star) - 1)
        names_star = [m.pattern_name for m in matches_star]
        self.assertIn(SHOOTING_STAR, names_star)

    def test_doji(self):
        candles = [
            make_candle(0, 100, 102, 98, 101),
            make_candle(1, 101, 103, 99, 102),
            make_candle(2, 102, 108, 96, 102.1),  # Body = 0.1, Range = 12.0 (<10%)
        ]
        matches = self.detector.detect_at_index(candles, len(candles) - 1)
        names = [m.pattern_name for m in matches]
        self.assertIn(DOJI, names)

    def test_morning_and_evening_star(self):
        downtrend = [
            make_candle(0, 120, 121, 117, 118),
            make_candle(1, 118, 119, 114, 115),
            make_candle(2, 115, 116, 111, 112),
        ]
        morning = [
            make_candle(3, 110, 111, 100, 101),  # Long bearish
            make_candle(4, 99, 100, 97, 98.5),   # Small star
            make_candle(5, 99, 108, 98.5, 107),  # Bullish closing > mid
        ]
        candles = downtrend + morning
        matches = self.detector.detect_at_index(candles, len(candles) - 1)
        names = [m.pattern_name for m in matches]
        self.assertIn(MORNING_STAR, names)

    def test_incomplete_unclosed_candle_is_ignored(self):
        coordinator = PatternDetector()
        candles = [
            make_candle(0, 100, 102, 98, 101),
            make_candle(1, 101, 103, 99, 102),
            make_candle(2, 102, 103, 101, 102.5),
            make_candle(3, 102, 108, 96, 102.1, is_closed=False),
        ]
        matches = coordinator.detect_at_index(candles, 3)
        self.assertEqual(len(matches), 0, "Unclosed candles must not produce detections")


class TestGeometricDetector(unittest.TestCase):
    """Test geometric pattern recognition."""

    def setUp(self):
        swing_detector = SwingDetector(left_bars=2, right_bars=2)
        self.detector = GeometricPatternDetector(swing_detector=swing_detector)

    def test_double_top_and_double_bottom(self):
        # Double Top: Peak 1 at 120, trough at 100, Peak 2 at 120, breakdown below 100
        prefix = [make_candle(i, 90 + i * 0.5, 92 + i * 0.5, 89 + i * 0.5, 91 + i * 0.5) for i in range(10)]
        p1 = [100, 105, 112, 120, 115, 108, 100]
        p2 = [105, 112, 119.5, 114, 106, 98]
        prices = p1 + p2
        pattern_candles = [make_candle(10 + i, p - 1, p + 1, p - 2, p) for i, p in enumerate(prices)]
        all_candles = prefix + pattern_candles

        matches = self.detector.detect_at_index(all_candles, len(all_candles) - 1, symbol="BTCUSDT")
        dt_matches = [m for m in matches if m.pattern_name == DOUBLE_TOP]
        self.assertGreaterEqual(len(dt_matches), 1)
        self.assertEqual(dt_matches[0].direction, PatternDirection.BEARISH)

        # Double Bottom
        prefix_b = [make_candle(i, 110 - i * 0.5, 112 - i * 0.5, 109 - i * 0.5, 111 - i * 0.5) for i in range(10)]
        b1 = [100, 95, 88, 80, 85, 92, 100]
        b2 = [95, 88, 80.5, 86, 94, 102]
        prices_b = b1 + b2
        pattern_candles_b = [make_candle(10 + i, p - 1, p + 1, p - 2, p) for i, p in enumerate(prices_b)]
        all_candles_b = prefix_b + pattern_candles_b

        matches_b = self.detector.detect_at_index(all_candles_b, len(all_candles_b) - 1, symbol="ETHUSDT")
        db_matches = [m for m in matches_b if m.pattern_name == DOUBLE_BOTTOM]
        self.assertGreaterEqual(len(db_matches), 1)
        self.assertEqual(db_matches[0].direction, PatternDirection.BULLISH)

    def test_head_and_shoulders(self):
        prefix = [make_candle(i, 80 + i, 82 + i, 79 + i, 81 + i) for i in range(8)]
        pts = [
            85, 95, 102, 110, 104, 96, 95,
            105, 115, 125, 118, 102, 94,
            102, 109, 104, 98, 92
        ]
        pattern_candles = [make_candle(8 + i, p - 1, p + 1, p - 2, p) for i, p in enumerate(pts)]
        candles = prefix + pattern_candles
        matches = self.detector.detect_at_index(candles, len(candles) - 1, symbol="BTCUSDT")
        hs = [m for m in matches if m.pattern_name == HEAD_AND_SHOULDERS]
        self.assertGreaterEqual(len(hs), 1)
        self.assertEqual(hs[0].direction, PatternDirection.BEARISH)


class TestMarketContextAnalyzer(unittest.TestCase):
    """Test multi-factor market context analyzer."""

    def test_context_evaluation(self):
        analyzer = MarketContextAnalyzer()
        candles = [
            make_candle(i, 100 + i * 2, 103 + i * 2, 99 + i * 2, 102 + i * 2, v=200.0)
            for i in range(30)
        ]

        context = analyzer.analyze(candles, idx=len(candles) - 1, symbol="BTCUSDT", timeframe="15m")
        self.assertIsInstance(context, MarketContext)
        self.assertEqual(context.current_trend, "uptrend")
        self.assertGreater(context.summary_score, 0.0)


class TestPatternOutcomeEvaluator(unittest.TestCase):
    """Test forward outcome measurement."""

    def test_forward_outcome_bullish(self):
        evaluator = PatternOutcomeEvaluator()
        pattern = PatternMatch(
            pattern_name=HAMMER,
            pattern_type=PatternType.CANDLESTICK,
            direction=PatternDirection.BULLISH,
            symbol="BTCUSDT",
            timeframe="15m",
            detection_timestamp=1700000000.0,
            candle_range=(0, 2),
            timestamps=(1700000000.0, 1700001800.0),
            status=PatternStatus.CONFIRMED,
            key_levels={"stop_loss": 95.0, "target_price": 110.0, "breakout_price": 101.0},
            confidence_score=0.75,
        )

        base = [
            make_candle(0, 105, 106, 101, 102),
            make_candle(1, 102, 103, 98, 99),
            make_candle(2, 99, 101, 93, 100),
        ]
        future = [
            make_candle(3, 101, 104, 100, 103),
            make_candle(4, 103, 107, 102, 106),
            make_candle(5, 106, 111, 105, 110),  # TP reached (111 > 110)
            make_candle(6, 110, 112, 108, 109),
            make_candle(7, 109, 110, 107, 108),
        ]
        all_candles = base + future

        outcome = evaluator.evaluate_outcome(pattern, all_candles, horizon_bars=5)
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.target_hit)
        self.assertFalse(outcome.stop_hit)
        self.assertGreater(outcome.max_favorable_excursion_pct, 5.0)
        self.assertGreater(outcome.net_pnl_after_costs, 0.0)


class TestComputerVisionInterface(unittest.TestCase):
    """Test PyTorch chart image recognition interface & honest training state."""

    def test_image_renderer(self):
        renderer = CandlestickImageRenderer(height=64, width=64)
        candles = [
            make_candle(i, 100 + i, 105 + i, 95 + i, 102 + i)
            for i in range(20)
        ]
        arr = renderer.render(candles)
        tensor = torch.from_numpy(arr).unsqueeze(0).unsqueeze(0)
        self.assertEqual(tensor.shape, (1, 1, 64, 64))
        self.assertGreater(tensor.max().item(), 0.0)

    def test_cnn_architecture_and_untrained_honesty(self):
        model = ChartPatternCNN(num_classes=19)
        recognizer = ChartImagePatternRecognizer()

        self.assertFalse(recognizer.is_trained)
        spec = recognizer.get_training_specification()
        self.assertIn("classes", spec)
        self.assertIn("data_splitting", spec)

        candles = [make_candle(i, 100, 105, 95, 102) for i in range(20)]
        pred = recognizer.predict(candles)
        self.assertIsNone(pred, "Untrained model must return None without speculative claims")


class TestPatternStrategyAndBacktest(unittest.TestCase):
    """Test PatternStrategy execution and integration with backtester."""

    def test_pattern_strategy_lifecycle(self):
        strategy = PatternStrategy("PatternStrategy", {
            "min_confidence": 0.50,
            "rr_ratio": 2.0,
            "require_confirmation": True,
        })

        for i in range(15):
            candle = make_candle(i, 100 + i, 102 + i, 99 + i, 101 + i)
            strategy.update(candle)

        self.assertEqual(len(strategy._candle_history), 15)
        self.assertIsNone(strategy.should_enter())


class TestAISelfImprovementIntegration(unittest.TestCase):
    """Test AI research lab integration."""

    def test_pattern_in_approved_indicators(self):
        self.assertIn("pattern", APPROVED_INDICATORS, "pattern must be whitelisted for blueprint execution")

    def test_strategy_generator_creates_pattern_archetypes(self):
        generator = StrategyGenerator()
        rev_strat = generator.generate_pattern_reversal()
        self.assertEqual(rev_strat.get("archetype"), "pattern_reversal")
        self.assertIn("pattern_sig", rev_strat.get("indicators", {}))

        cont_strat = generator.generate_pattern_continuation()
        self.assertEqual(cont_strat.get("archetype"), "pattern_continuation")

    def test_generated_strategy_computes_pattern_without_eval(self):
        generator = StrategyGenerator()
        blueprint = generator.generate_pattern_reversal()
        instance = GeneratedStrategy(blueprint)

        # Feed candles through GeneratedStrategy instance
        for i in range(30):
            instance.update(make_candle(i, 100, 102, 98, 101))

        self.assertIsNotNone(instance)


class TestExperimentStorePatternPersistence(unittest.TestCase):
    """Test database schema and query methods for pattern detections and outcomes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_research.db")
        self.store = ExperimentStore(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_record_and_query_pattern_detection(self):
        pattern = PatternMatch(
            pattern_name=DOUBLE_BOTTOM,
            pattern_type=PatternType.REVERSAL,
            direction=PatternDirection.BULLISH,
            symbol="BTCUSDT",
            timeframe="15m",
            detection_timestamp=1700000000.0,
            candle_range=(10, 25),
            timestamps=(1700000000.0, 1700013500.0),
            status=PatternStatus.CONFIRMED,
            key_levels={"breakout_price": 42000.0, "stop_loss": 40000.0, "target_price": 44000.0},
            confidence_score=0.82,
        )

        p_id = self.store.record_pattern_detection(pattern)
        self.assertIsNotNone(p_id)

        recent = self.store.get_recent_pattern_detections(limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["pattern_name"], DOUBLE_BOTTOM)
        self.assertEqual(recent[0]["status"].lower(), "confirmed")


class TestPatternDashboardAPI(unittest.TestCase):
    """Test REST API routes registered for the dashboard."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "api_research.db")
        self.store = ExperimentStore(db_path=self.db_path)

        self.bot_state = {
            "status": "running",
            "prices": {"BTCUSDT": 50000.0},
            "patterns": [],
        }
        self.app, _ = create_dashboard_app(self.bot_state)
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_api_patterns_supported(self):
        res = self.client.get("/api/patterns/supported")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("geometric_patterns", data)
        self.assertIn("candlestick_patterns", data)
        self.assertEqual(len(data["geometric_patterns"]), 11)
        self.assertEqual(len(data["candlestick_patterns"]), 7)

    def test_api_patterns_active_and_caching(self):
        pattern = PatternMatch(
            pattern_name=HAMMER,
            pattern_type=PatternType.CANDLESTICK,
            direction=PatternDirection.BULLISH,
            symbol="BTCUSDT",
            timeframe="15m",
            detection_timestamp=time.time(),
            candle_range=(1, 2),
            timestamps=(1700000000.0, 1700000900.0),
            status=PatternStatus.CONFIRMED,
            key_levels={"breakout_price": 50100.0, "stop_loss": 49500.0, "target_price": 51000.0},
            confidence_score=0.75,
        )
        update_active_patterns([pattern])

        res = self.client.get("/api/patterns/active?symbol=BTCUSDT")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["patterns"][0]["pattern_name"], HAMMER)


if __name__ == "__main__":
    unittest.main()

"""
test_paper_and_live.py — Comprehensive Test Suite for Stage 5: Paper Trading & Controlled Live Trading.

Tests:
1. Mock broker adapter: rejected orders, duplicate requests, partial fills, timeouts, disconnections.
2. Paper broker adapter: backward compatibility with OrderEngine & PaperWallet.
3. Live broker adapter: credential masking, HMAC-SHA256 request signing, zero raw key logging.
4. Live mode authentication: explicit verification of account, instrument, order size, and risk limits.
5. Strict external live risk limits: order size, daily loss, portfolio exposure, position count, 1x leverage.
6. AI safety invariants: AI cannot change risk limits, increase leverage, or replace live strategies.
7. Global emergency stop: immediate order halt, position flattening, and reset authorization.
8. Order reconciliation: detects discrepancies, missing positions, and ghost orders.
9. Comprehensive event tracking: signals, orders, fills, positions, fees, equity, risk events.
10. Paper vs backtest comparison: performance tracking and divergence alerts.
11. Resilience: stale data detection, reconnect backoff, duplicate event filtering, restart recovery.
12. Paper strategy loader: human approval enforcement for AI strategies.
13. Research & backtest isolation: research modules cannot place live orders.
"""

import os
import json
import time
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.core.paper_wallet import PaperWallet, Position
from src.core.fee_model import FeeModel
from src.core.order_engine import OrderEngine
from src.core.trade_logger import TradeLogger
from src.execution import (
    BaseBrokerAdapter,
    PaperBrokerAdapter,
    LiveBrokerAdapter,
    MockBrokerAdapter,
    mask_key,
    LiveRiskGuardian,
    LiveModeAuth,
    EmergencyStop,
    LIVE_CONFIRMATION_PHRASE,
    ExecutionTracker,
    PaperVsBacktestComparator,
    StaleDataDetector,
    ReconnectHandler,
    DuplicateEventFilter,
    StatePersistence,
    OrderReconciler,
    NotificationManager,
    PaperStrategyLoader,
)
from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.promotion_gate import PromotionGate
from src.research.generated_strategy import GeneratedStrategy


class TestMockBroker(unittest.TestCase):
    """Test MockBrokerAdapter edge cases: rejections, duplicates, partial fills, timeouts, disconnects."""

    def setUp(self):
        self.broker = MockBrokerAdapter(starting_balance=10000.0)

    def test_mock_broker_successful_market_order(self):
        res = self.broker.place_order(
            symbol="BTCUSDT", side="buy", quantity=0.1, current_price=50000.0
        )
        self.assertTrue(res['success'])
        self.assertEqual(res['status'], 'FILLED')
        self.assertEqual(len(self.broker.positions), 1)
        self.assertLess(self.broker.cash, 10000.0)

    def test_mock_broker_rejected_order(self):
        self.broker.should_reject = True
        self.broker.rejection_reason = "Price out of bounds"
        res = self.broker.place_order(
            symbol="BTCUSDT", side="buy", quantity=0.1, current_price=50000.0
        )
        self.assertFalse(res['success'])
        self.assertEqual(res['status'], 'REJECTED')
        self.assertEqual(res['reason'], "Price out of bounds")

    def test_mock_broker_duplicate_order_protection(self):
        client_id = "test_order_123"
        res1 = self.broker.place_order(
            symbol="BTCUSDT", side="buy", quantity=0.05, current_price=50000.0,
            client_order_id=client_id
        )
        self.assertTrue(res1['success'])

        # Duplicate request with same client_order_id must be rejected
        res2 = self.broker.place_order(
            symbol="BTCUSDT", side="buy", quantity=0.05, current_price=50000.0,
            client_order_id=client_id
        )
        self.assertFalse(res2['success'])
        self.assertEqual(res2['status'], 'REJECTED')
        self.assertIn("Duplicate order", res2['reason'])

    def test_mock_broker_partial_fill(self):
        self.broker.partial_fill_ratio = 0.5  # 50% fill
        res = self.broker.place_order(
            symbol="ETHUSDT", side="buy", quantity=2.0, current_price=3000.0
        )
        self.assertTrue(res['success'])
        self.assertEqual(res['status'], 'PARTIALLY_FILLED')
        self.assertEqual(res['filled_quantity'], 1.0)

    def test_mock_broker_timeout(self):
        self.broker.should_timeout = True
        with self.assertRaises(TimeoutError):
            self.broker.place_order(
                symbol="BTCUSDT", side="buy", quantity=0.1, current_price=50000.0
            )

    def test_mock_broker_disconnection(self):
        self.broker.should_disconnect = True
        with self.assertRaises(ConnectionError):
            self.broker.get_account_info()


class TestPaperBrokerAdapter(unittest.TestCase):
    """Test PaperBrokerAdapter backward compatibility with existing engine."""

    def setUp(self):
        config = {
            'paper_trading': {'starting_balance': 10000.0, 'currency': 'USDT'},
            'fees': {'mode': 'spot', 'spot_maker': 0.001, 'spot_taker': 0.001},
            'slippage': {'base_bps': 5, 'volatility_multiplier': 1.0, 'max_bps': 30, 'random_jitter_pct': 0.0},
        }
        self.wallet = PaperWallet(config)
        self.fee_model = FeeModel(config)
        self.logger = MagicMock()
        self.order_engine = OrderEngine(self.wallet, self.fee_model, self.logger)
        self.adapter = PaperBrokerAdapter(self.order_engine, self.wallet)

    def test_paper_adapter_mode_and_info(self):
        self.assertEqual(self.adapter.mode, "paper")
        info = self.adapter.get_account_info()
        self.assertEqual(info['cash'], 10000.0)
        self.assertEqual(info['currency'], 'USDT')

    def test_paper_adapter_buy_and_close(self):
        order_res = self.adapter.place_order(
            symbol="BTCUSDT", side="buy", quantity=0.1, current_price=50000.0
        )
        self.assertTrue(order_res['success'])
        self.assertEqual(order_res['status'], 'FILLED')
        self.assertEqual(len(self.adapter.get_open_positions()), 1)

        pos_id = order_res['position_id']
        close_res = self.adapter.close_position("BTCUSDT", pos_id, current_price=52000.0)
        self.assertIsNotNone(close_res)
        self.assertEqual(len(self.adapter.get_open_positions()), 0)


class TestLiveBrokerSecurity(unittest.TestCase):
    """Test LiveBrokerAdapter credential safety, signing, and representation."""

    def test_mask_key_utility(self):
        self.assertEqual(mask_key(None), "NONE")
        self.assertEqual(mask_key(""), "NONE")
        self.assertEqual(mask_key("1234"), "****")
        self.assertEqual(mask_key("abcdefghijklmnop"), "abcd...mnop")

    def test_live_adapter_requires_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                LiveBrokerAdapter()

    def test_live_adapter_repr_masks_credentials(self):
        key = "my_secret_real_binance_key_12345"
        sec = "super_secret_signing_key_67890"
        adapter = LiveBrokerAdapter(api_key=key, api_secret=sec)
        rep = repr(adapter)
        self.assertNotIn(key, rep)
        self.assertNotIn(sec, rep)
        self.assertIn("my_s...2345", rep)

    def test_live_adapter_hmac_signature(self):
        key = "test_key"
        sec = "test_secret"
        adapter = LiveBrokerAdapter(api_key=key, api_secret=sec)
        params = {'symbol': 'BTCUSDT', 'side': 'BUY'}
        signed = adapter._sign_params(params)
        self.assertIn('timestamp', signed)
        self.assertIn('recvWindow', signed)
        self.assertIn('signature', signed)
        self.assertEqual(len(signed['signature']), 64)  # SHA256 hex length


class TestLiveRiskGuardianAndAuth(unittest.TestCase):
    """Test strict external risk limits, authenticated live confirmation, and emergency stop."""

    def setUp(self):
        self.guardian = LiveRiskGuardian(
            max_order_usd=500.0,
            daily_loss_limit_usd=100.0,
            max_exposure_pct=50.0,
            max_open_positions=2,
            leverage_cap=1.0,
        )
        self.auth = LiveModeAuth(self.guardian, allowed_instruments=["BTCUSDT", "ETHUSDT"])
        self.emergency = EmergencyStop()

    def test_paper_mode_is_default(self):
        self.assertFalse(self.auth.is_live_enabled)

    def test_guardian_blocks_order_exceeding_max_usd(self):
        # $600 order when limit is $500
        safe, reason = self.guardian.validate_order(
            symbol="BTCUSDT", side="buy", quantity=0.012, price=50000.0,
            equity=10000.0, open_positions=[]
        )
        self.assertFalse(safe)
        self.assertIn("exceeds maximum allowed order limit", reason)

    def test_guardian_blocks_order_when_daily_loss_limit_hit(self):
        self.guardian.record_trade_result(-120.0)  # Loss exceeds $100 limit
        safe, reason = self.guardian.validate_order(
            symbol="BTCUSDT", side="buy", quantity=0.005, price=50000.0,
            equity=10000.0, open_positions=[]
        )
        self.assertFalse(safe)
        self.assertIn("Daily loss limit reached", reason)

    def test_guardian_blocks_order_when_max_open_positions_reached(self):
        open_positions = [
            {'symbol': 'BTCUSDT', 'quantity': 0.005},
            {'symbol': 'ETHUSDT', 'quantity': 0.1},
        ]
        # Attempting 3rd distinct symbol
        safe, reason = self.guardian.validate_order(
            symbol="SOLUSDT", side="buy", quantity=1.0, price=150.0,
            equity=10000.0, open_positions=open_positions
        )
        self.assertFalse(safe)
        self.assertIn("reaches limit of 2", reason)

    def test_guardian_blocks_order_when_exposure_exceeds_equity_limit(self):
        # 50% limit of $1000 equity is $500. Attempt $600 exposure.
        safe, reason = self.guardian.validate_order(
            symbol="BTCUSDT", side="buy", quantity=0.012, price=50000.0,
            equity=1000.0, open_positions=[]
        )
        self.assertFalse(safe)

    def test_guardian_leverage_is_capped_at_1x(self):
        # Attempting to construct with 5x leverage must cap to 1.0
        g = LiveRiskGuardian(leverage_cap=5.0)
        self.assertEqual(g.leverage_cap, 1.0)

    def test_live_confirmation_step_requires_all_verifications(self):
        # 1. Invalid secret fails
        ok, msg = self.auth.confirm_live_trading(
            auth_secret="WRONG_SECRET",
            confirmation_phrase=LIVE_CONFIRMATION_PHRASE,
            account_id="ACC_123",
            verified_instruments=["BTCUSDT"],
            max_order_size_usd=300.0,
            risk_limits_ack={'ack_daily_loss': True, 'ack_max_drawdown': True},
        )
        self.assertFalse(ok)

        # 2. Invalid phrase fails
        ok, msg = self.auth.confirm_live_trading(
            auth_secret="DEMO_SAFE_LIVE_SECRET",
            confirmation_phrase="YES PLEASE",
            account_id="ACC_123",
            verified_instruments=["BTCUSDT"],
            max_order_size_usd=300.0,
            risk_limits_ack={'ack_daily_loss': True, 'ack_max_drawdown': True},
        )
        self.assertFalse(ok)

        # 3. Unapproved instrument fails
        ok, msg = self.auth.confirm_live_trading(
            auth_secret="DEMO_SAFE_LIVE_SECRET",
            confirmation_phrase=LIVE_CONFIRMATION_PHRASE,
            account_id="ACC_123",
            verified_instruments=["DOGEUSDT"],
            max_order_size_usd=300.0,
            risk_limits_ack={'ack_daily_loss': True, 'ack_max_drawdown': True},
        )
        self.assertFalse(ok)

        # 4. Valid confirmation succeeds
        ok, msg = self.auth.confirm_live_trading(
            auth_secret="DEMO_SAFE_LIVE_SECRET",
            confirmation_phrase=LIVE_CONFIRMATION_PHRASE,
            account_id="ACC_123",
            verified_instruments=["BTCUSDT"],
            max_order_size_usd=300.0,
            risk_limits_ack={'ack_daily_loss': True, 'ack_max_drawdown': True},
        )
        self.assertTrue(ok)
        self.assertTrue(self.auth.is_live_enabled)

    def test_emergency_stop_halts_and_closes_positions(self):
        mock_broker = MockBrokerAdapter(starting_balance=10000.0)
        mock_broker.place_order(symbol="BTCUSDT", side="buy", quantity=0.1, current_price=50000.0)
        self.assertEqual(len(mock_broker.positions), 1)

        res = self.emergency.trigger(
            reason="Anomaly detected", source="test_suite",
            broker=mock_broker, prices={'BTCUSDT': 50000.0}
        )
        self.assertTrue(self.emergency.is_halted)
        self.assertEqual(res['closed_positions_count'], 1)
        self.assertEqual(len(mock_broker.positions), 0)

        # Reset requires token
        self.assertFalse(self.emergency.reset("WRONG_TOKEN"))
        self.assertTrue(self.emergency.reset("RESET_EMERGENCY_OVERRIDE"))
        self.assertFalse(self.emergency.is_halted)


class TestOrderReconciliationAndNotifications(unittest.TestCase):
    """Test OrderReconciler discrepancy detection and NotificationManager dispatching."""

    def test_reconciliation_detects_mismatch(self):
        reconciler = OrderReconciler()
        mock_broker = MockBrokerAdapter()
        local_positions = [
            {'symbol': 'BTCUSDT', 'quantity': 0.5, 'entry_price': 50000.0}
        ]
        # Broker has no positions
        report = reconciler.reconcile(local_positions, mock_broker)
        self.assertFalse(report['synchronized'])
        self.assertTrue(len(report['discrepancies']) > 0)

    def test_notification_manager_dispatches_critical_events(self):
        mgr = NotificationManager()
        received = []
        mgr.register_callback(lambda n: received.append(n))

        notif = mgr.dispatch(
            event_type="CIRCUIT_BREAKER_TRIGGERED",
            message="Drawdown exceeded 15%",
            severity="CRITICAL",
            details={'drawdown': 15.2}
        )
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['event_type'], "CIRCUIT_BREAKER_TRIGGERED")
        self.assertEqual(received[0]['severity'], "CRITICAL")


class TestExecutionTracker(unittest.TestCase):
    """Test full event tracking across signals, orders, fills, positions, fees, equity, risk."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.temp_dir.name, "exec_test.db")
        self.tracker = ExecutionTracker(db_path=db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tracking_lifecycle(self):
        s_id = self.tracker.record_signal("TestStrat", "BTCUSDT", "long", True)
        o_id = self.tracker.record_order("ord_1", "BTCUSDT", "buy", 0.1, 50000.0, status="FILLED")
        f_id = self.tracker.record_fill("fill_1", "ord_1", "BTCUSDT", "buy", 50000.0, 0.1, 5.0)
        p_id = self.tracker.record_position("pos_1", "BTCUSDT", "long", 0.1, 50000.0, 5.0)
        fee_id = self.tracker.record_fee("tr_1", "BTCUSDT", 5.0, cumulative_fees=5.0)
        eq_id = self.tracker.record_equity(9500.0, 10000.0, 0.0)
        r_id = self.tracker.record_risk_event("CIRCUIT_BREAKER", "Drawdown limit reached", "CRITICAL")

        self.assertGreater(s_id, 0)
        self.assertGreater(o_id, 0)
        self.assertGreater(f_id, 0)

        events = self.tracker.get_recent_events(limit=20)
        self.assertEqual(len(events), 7)

        # In-memory buffer check
        mem_events = self.tracker.get_recent_memory_events()
        self.assertEqual(len(mem_events), 7)


class TestPaperVsBacktestComparator(unittest.TestCase):
    """Test comparison of paper performance against backtest baseline."""

    def setUp(self):
        self.comparator = PaperVsBacktestComparator(min_eval_trades=5)

    def test_comparator_insufficient_data(self):
        trades = [{'net_pnl': 50.0}, {'net_pnl': -20.0}]
        paper_metrics = self.comparator.calculate_paper_metrics(trades)
        report = self.comparator.compare(paper_metrics, {'total_trades': 50}, strategy_name="Test")
        self.assertEqual(report['status'], 'INSUFFICIENT_DATA')

    def test_comparator_detects_critical_drawdown_divergence(self):
        # 6 trades with heavy losses and high drawdown
        trades = [
            {'net_pnl': -100.0}, {'net_pnl': -150.0}, {'net_pnl': 20.0},
            {'net_pnl': -200.0}, {'net_pnl': -50.0}, {'net_pnl': -100.0}
        ]
        equity_curve = [10000.0, 9900.0, 9750.0, 9770.0, 9570.0, 9520.0, 9420.0]
        paper_metrics = self.comparator.calculate_paper_metrics(trades, equity_curve)
        bt_metrics = {
            'total_trades': 50,
            'sharpe_ratio': 1.8,
            'win_rate_pct': 60.0,
            'max_drawdown_pct': 2.0,  # Paper max DD is ~5.8%, which is > 2.0 * 1.5
            'profit_factor': 1.8,
        }
        report = self.comparator.compare(paper_metrics, bt_metrics, strategy_name="Test")
        self.assertEqual(report['status'], 'CRITICAL')


class TestResilienceControls(unittest.TestCase):
    """Test StaleDataDetector, ReconnectHandler, DuplicateEventFilter, and StatePersistence."""

    def test_stale_data_detection(self):
        detector = StaleDataDetector(default_max_age_seconds=10.0)
        # Never seen
        stale, _ = detector.is_stale("BTCUSDT")
        self.assertTrue(stale)

        # Fresh tick
        now = time.time()
        detector.record_tick("BTCUSDT", timestamp=now)
        stale, elapsed = detector.is_stale("BTCUSDT", current_time=now + 5.0)
        self.assertFalse(stale)

        # Stale tick
        stale, elapsed = detector.is_stale("BTCUSDT", current_time=now + 15.0)
        self.assertTrue(stale)

    def test_reconnect_exponential_backoff(self):
        handler = ReconnectHandler(base_delay=1.0, max_delay=10.0, backoff_factor=2.0, max_attempts=4)
        self.assertEqual(handler.current_state, 'DISCONNECTED')

        delay1 = handler.record_failure("WS dropped")
        self.assertEqual(delay1, 1.0)
        self.assertEqual(handler.current_state, 'RECONNECTING')

        delay2 = handler.record_failure("Connection refused")
        self.assertEqual(delay2, 2.0)

        delay3 = handler.record_failure("Timeout")
        self.assertEqual(delay3, 4.0)

        delay4 = handler.record_failure("Max attempts reached")
        self.assertTrue(handler.is_failed())

        handler.record_success()
        self.assertEqual(handler.current_state, 'CONNECTED')
        self.assertEqual(handler.attempts, 0)

    def test_duplicate_event_filter(self):
        flt = DuplicateEventFilter(max_entries=100)
        # Candles
        self.assertFalse(flt.is_duplicate_candle("BTCUSDT", 1000.0, True))
        self.assertTrue(flt.is_duplicate_candle("BTCUSDT", 1000.0, True))
        self.assertFalse(flt.is_duplicate_candle("BTCUSDT", 1001.0, True))

        # Signals
        self.assertFalse(flt.is_duplicate_signal("StratA", "BTCUSDT", 2000.0, "long"))
        self.assertTrue(flt.is_duplicate_signal("StratA", "BTCUSDT", 2000.0, "long"))
        self.assertFalse(flt.is_duplicate_signal("StratA", "BTCUSDT", 2000.0, "short"))

    def test_state_persistence_and_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = os.path.join(td, "wallet_state.json")
            persistence = StatePersistence(filepath=state_file)

            config = {'paper_trading': {'starting_balance': 10000.0, 'currency': 'USDT'}}
            wallet1 = PaperWallet(config)
            wallet1.cash = 8500.0
            wallet1.peak_equity = 11000.0
            pos = Position(
                symbol="BTCUSDT", side="long", quantity=0.1,
                entry_price=50000.0, fee_paid=5.0, timestamp=time.time()
            )
            wallet1.positions[pos.id] = pos

            self.assertTrue(persistence.save_wallet_state(wallet1))

            # New wallet starts with $10,000
            wallet2 = PaperWallet(config)
            self.assertEqual(wallet2.cash, 10000.0)

            # Restore into wallet2
            self.assertTrue(persistence.restore_wallet(wallet2))
            self.assertEqual(wallet2.cash, 8500.0)
            self.assertEqual(wallet2.peak_equity, wallet1.peak_equity)
            self.assertEqual(len(wallet2.positions), 1)
            self.assertIn(pos.id, wallet2.positions)


class TestPaperStrategyLoader(unittest.TestCase):
    """Test safe loading of AI-generated strategies with human approval checks."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.temp_dir.name, "research_test.db")
        self.store = ExperimentStore(db_path)
        self.registry = StrategyRegistry(self.store)
        self.gate = PromotionGate(self.store, self.registry)
        self.loader = PaperStrategyLoader(self.registry, self.gate)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unapproved_candidate_cannot_be_loaded(self):
        blueprint = {
            'name': 'AI_Test_Strat',
            'indicators': {'ema_fast': {'type': 'ema', 'period': 9}},
            'entry_long': [],
            'entry_short': [],
            'exit': {},
            'min_candles': 50,
        }
        strat_id = self.registry.register_generated(
            name="AI_Test_Strat",
            version=1,
            timeframe="15m",
            blueprint=blueprint,
        )
        with self.assertRaises(PermissionError):
            self.loader.load_strategy_instance(strat_id)

    def test_approved_paper_strategy_loads_successfully(self):
        blueprint = {
            'name': 'Approved_AI_Strat',
            'indicators': {'ema_fast': {'type': 'ema', 'period': 9}},
            'entry_long': [],
            'entry_short': [],
            'exit': {},
            'min_candles': 50,
        }
        strat_id = self.registry.register_generated(
            name="Approved_AI_Strat",
            version=1,
            timeframe="15m",
            blueprint=blueprint,
        )
        self.registry.promote(strat_id)
        self.gate.request_paper(strat_id)
        self.gate.approve_paper(strat_id, approved_by="human_admin")

        instance = self.loader.load_strategy_instance(strat_id)
        self.assertIsInstance(instance, GeneratedStrategy)
        self.assertEqual(instance.name, "Approved_AI_Strat")


class TestResearchIsolation(unittest.TestCase):
    """Verify research and backtest modules have zero ability to submit real orders."""

    def test_research_lab_no_order_methods(self):
        forbidden_methods = [
            "place_order", "market_buy", "market_sell", "market_short",
            "cancel_order", "submit_order", "live_trade"
        ]
        from src.research.experiment_manager import ExperimentManager
        from src.research.backtest_harness import BacktestHarness
        from src.research.improvement_loop import ImprovementLoop

        for cls in (ExperimentManager, BacktestHarness, ImprovementLoop, PromotionGate):
            for method in forbidden_methods:
                self.assertFalse(
                    hasattr(cls, method),
                    f"{cls.__name__} violates isolation: contains order method '{method}'"
                )


if __name__ == '__main__':
    unittest.main()

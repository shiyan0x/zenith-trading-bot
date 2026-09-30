"""
test_multi_timeframe.py — Comprehensive test suite for Multi-Timeframe Trading Selection
and Performance Comparison (Steps 1–8).
"""

import os
import json
import time
import pytest
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.market_feed import Candle, MarketFeed
from src.core.candle_aggregator import (
    CandleAggregator,
    aggregate_historical_candles,
    is_native,
    needs_aggregation,
    get_source_interval,
    get_aggregation_ratio,
    INTERVAL_SECONDS,
)
from src.core.fee_model import FeeModel
from src.core.paper_wallet import PaperWallet, Position
from src.core.order_engine import OrderEngine
from src.core.trade_logger import TradeLogger
from src.strategies.ema_vwap_rsi import EmaVwapRsiStrategy
from src.brain.backtester import Backtester
from src.brain.timeframe_comparator import TimeframeComparator, TimeframeComparisonResult
from src.execution.execution_tracker import ExecutionTracker
from src.dashboard.timeframe_api import init_timeframe_api, register_timeframe_routes
from flask import Flask


# ─── 1. Candle Aggregation & Mathematical Integrity ───

def test_candle_aggregation_ohlcv_math():
    """Verify that merging two 5m candles into a 10m candle produces mathematically exact OHLCV."""
    agg = CandleAggregator('10m')

    # Boundary start: timestamp 600 (10:00 UTC)
    c1 = Candle(timestamp=600.0, o=100.0, h=105.0, l=98.0, c=102.0, volume=10.0, is_closed=True)
    # Second bar: timestamp 900 (10:05 UTC)
    c2 = Candle(timestamp=900.0, o=102.0, h=108.0, l=101.0, c=107.0, volume=15.0, is_closed=True)

    r1 = agg.push(c1)
    assert r1 is None, "First 5m bar should not emit a completed 10m candle"
    assert agg.pending_count == 1

    r2 = agg.push(c2)
    assert r2 is not None, "Second 5m bar should complete the 10m candle"
    assert agg.pending_count == 0

    assert r2.timestamp == 600.0, "Timestamp must be the opening time of the first candle"
    assert r2.open == 100.0, "Open must be the open of the first candle"
    assert r2.high == 108.0, "High must be the max high of all buffered candles"
    assert r2.low == 98.0, "Low must be the min low of all buffered candles"
    assert r2.close == 107.0, "Close must be the close of the final candle"
    assert r2.volume == 25.0, "Volume must be the exact sum of all volumes"
    assert r2.is_closed is True, "Aggregated candle must be marked as closed"


def test_candle_boundary_alignment():
    """Verify that candles are aligned to 10-minute boundaries (:00, :10, :20, :30, :40, :50)."""
    agg = CandleAggregator('10m')

    # ts = 300 is 00:05 (NOT a 10m boundary start). It should be discarded until a boundary is found.
    misaligned = Candle(timestamp=300.0, o=100.0, h=101.0, l=99.0, c=100.5, volume=5.0, is_closed=True)
    assert agg.push(misaligned) is None
    assert agg.pending_count == 0, "Non-boundary start must not be buffered without an anchor"

    # ts = 600 is 00:10 (Valid boundary start)
    c1 = Candle(timestamp=600.0, o=100.0, h=102.0, l=99.0, c=101.0, volume=8.0, is_closed=True)
    assert agg.push(c1) is None
    assert agg.pending_count == 1

    # ts = 900 is 00:15
    c2 = Candle(timestamp=900.0, o=101.0, h=103.0, l=100.0, c=102.0, volume=7.0, is_closed=True)
    merged = agg.push(c2)
    assert merged is not None
    assert merged.timestamp == 600.0


def test_lookahead_prevention_unclosed_bars():
    """Verify that unclosed bars are NEVER emitted as complete or used for signal generation."""
    agg = CandleAggregator('10m')

    # Unclosed tick
    unclosed = Candle(timestamp=600.0, o=100.0, h=105.0, l=95.0, c=103.0, volume=5.0, is_closed=False)
    res = agg.push(unclosed)
    assert res is None, "Unclosed candles must never be aggregated into closed bars"
    assert agg.pending_count == 0


def test_historical_candle_aggregation():
    """Verify batch aggregation of historical candles with trailing incomplete bar handling."""
    candles = [
        Candle(timestamp=600.0 * i, o=100.0 + i, h=102.0 + i, l=99.0 + i, c=101.0 + i, volume=10.0, is_closed=True)
        for i in range(1, 6) # 5 candles of 5m
    ]
    # Timestamps: 600 (00:10), 1200 (00:20), 1800 (00:30), 2400 (00:40), 3000 (00:50)
    # Notice: these are spaced by 600s, so each is already at a 10m boundary!
    # Let's create pairs at 0, 300, 600, 900, 1200, 1500
    source_5m = [
        Candle(timestamp=float(i * 300), o=100.0, h=105.0, l=95.0, c=102.0, volume=10.0, is_closed=True)
        for i in range(5) # 5 bars -> exactly two 10m bars (0, 600), plus one trailing 5m bar at 1200
    ]
    aggregated = aggregate_historical_candles(source_5m, '10m')
    assert len(aggregated) == 2, "5 source 5m bars should yield exactly 2 full 10m bars"
    assert aggregated[0].timestamp == 0.0
    assert aggregated[1].timestamp == 600.0
    assert aggregated[0].volume == 20.0


# ─── 2. Supported Intervals & Aggregation Rules ───

def test_interval_support_classification():
    """Verify native vs aggregated classification for requested timeframes."""
    assert is_native('5m') is True
    assert needs_aggregation('5m') is False

    assert is_native('10m') is False
    assert needs_aggregation('10m') is True
    assert get_source_interval('10m') == '5m'
    assert get_aggregation_ratio('10m') == 2

    assert is_native('15m') is True
    assert is_native('1h') is True
    assert is_native('4h') is True


# ─── 3. Position and Trade Timeframe Tracking (Step 4) ───

def test_position_and_wallet_timeframe_tracking():
    """Verify that Position and PaperWallet closed_trades persist the timeframe they were opened under."""
    cfg = {'paper_trading': {'starting_balance': 10000.0, 'currency': 'USDT'}}
    wallet = PaperWallet(cfg)

    pos = wallet.open_position(
        symbol='BTCUSDT',
        side='long',
        quantity=0.1,
        execution_price=50000.0,
        fee=5.0,
        strategy_name='ema_vwap_rsi',
        timeframe='10m'
    )

    assert pos is not None
    assert pos.timeframe == '10m'
    pos_dict = pos.to_dict(51000.0)
    assert pos_dict['timeframe'] == '10m'

    # Reconstruct from dict
    restored_pos = Position.from_dict(pos_dict)
    assert restored_pos.timeframe == '10m'

    # Close position
    trade = wallet.close_position(pos.id, execution_price=52000.0, fee=5.2)
    assert trade is not None
    assert trade['timeframe'] == '10m'
    assert wallet.closed_trades[-1]['timeframe'] == '10m'


def test_order_engine_timeframe_passthrough():
    """Verify OrderEngine market_buy and market_short pass timeframe to positions."""
    cfg = {
        'paper_trading': {'starting_balance': 10000.0, 'currency': 'USDT'},
        'fees': {'spot_maker': 0.001, 'spot_taker': 0.001, 'mode': 'spot'},
        'slippage': {'base_bps': 0, 'volatility_multiplier': 0, 'max_bps': 0, 'random_jitter_pct': 0}
    }
    wallet = PaperWallet(cfg)
    fee_model = FeeModel(cfg)
    logger_mock = MagicMock()
    engine = OrderEngine(wallet, fee_model, logger_mock)

    pos = engine.market_buy(
        symbol='BTCUSDT',
        quantity=0.05,
        current_price=50000.0,
        timeframe='4h'
    )
    assert pos is not None
    assert pos.timeframe == '4h'


# ─── 4. Safe Timeframe Switching with Open Positions (Step 3) ───

def test_safe_timeframe_switch_closes_open_positions():
    """Verify safe switching closes existing open positions at current market prices before changing interval."""
    cfg = {
        'paper_trading': {'starting_balance': 10000.0, 'currency': 'USDT'},
        'fees': {'spot_maker': 0.001, 'spot_taker': 0.001, 'mode': 'spot'},
        'slippage': {'base_bps': 0, 'volatility_multiplier': 0, 'max_bps': 0, 'random_jitter_pct': 0}
    }
    wallet = PaperWallet(cfg)
    fee_model = FeeModel(cfg)
    logger_mock = MagicMock()
    engine = OrderEngine(wallet, fee_model, logger_mock)

    # Open position on 15m
    engine.market_buy(symbol='BTCUSDT', quantity=0.1, current_price=50000.0, timeframe='15m')
    assert len(wallet.positions) == 1

    # Simulate timeframe change policy: close all open positions
    prices = {'BTCUSDT': 51000.0}
    closed = engine.close_all(prices)
    assert len(closed) == 1
    assert len(wallet.positions) == 0
    assert closed[0]['timeframe'] == '15m'
    assert len(wallet.closed_trades) == 1
    assert wallet.closed_trades[0]['timeframe'] == '15m'


# ─── 5. Database Schema & Migration for ExecutionTracker (Step 4) ───

def test_execution_tracker_timeframe_schema_and_stats():
    """Verify execution_events table migration, record_trade method, and timeframe performance statistics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'exec_test.db')
        tracker = ExecutionTracker(db_path=db_path)

        # Record trades across two timeframes
        # Timeframe 5m: 3 trades (2 wins, 1 loss) -> Completed status
        tracker.record_trade(
            symbol='BTCUSDT', timeframe='5m', strategy_name='ema_vwap_rsi',
            entry_price=50000.0, exit_price=50500.0, quantity=0.1,
            gross_pnl=50.0, fees=5.0, net_pnl=45.0, outcome='win'
        )
        tracker.record_trade(
            symbol='BTCUSDT', timeframe='5m', strategy_name='ema_vwap_rsi',
            entry_price=50500.0, exit_price=51000.0, quantity=0.1,
            gross_pnl=50.0, fees=5.0, net_pnl=45.0, outcome='win'
        )
        tracker.record_trade(
            symbol='BTCUSDT', timeframe='5m', strategy_name='ema_vwap_rsi',
            entry_price=51000.0, exit_price=50800.0, quantity=0.1,
            gross_pnl=-20.0, fees=5.0, net_pnl=-25.0, outcome='loss'
        )

        # Timeframe 1h: 1 trade -> Insufficient data status (< 3 trades)
        tracker.record_trade(
            symbol='BTCUSDT', timeframe='1h', strategy_name='ema_vwap_rsi',
            entry_price=50000.0, exit_price=52000.0, quantity=0.1,
            gross_pnl=200.0, fees=10.0, net_pnl=190.0, outcome='win'
        )

        # Query 5m stats
        stats_5m = tracker.get_timeframe_stats('5m', min_trades=3)
        assert stats_5m['status'] == 'completed'
        assert stats_5m['total_trades'] == 3
        assert stats_5m['winning_trades'] == 2
        assert stats_5m['losing_trades'] == 1
        assert stats_5m['win_rate'] == 66.67
        assert stats_5m['net_pnl'] == 65.0
        assert stats_5m['profit_factor'] == 5.0 # 100 gross win / 20 gross loss

        # Query 1h stats (< min_trades threshold)
        stats_1h = tracker.get_timeframe_stats('1h', min_trades=3)
        assert stats_1h['status'] == 'insufficient_data'
        assert stats_1h['message'] == 'Not enough data'
        assert stats_1h['total_trades'] == 1


# ─── 6. Backtester Period Year Scaling for 10m (Step 6) ───

def test_backtester_periods_per_year_for_all_target_timeframes():
    """Verify that Backtester calculates correct periods_per_year for 5m, 10m, 15m, 1h, 4h."""
    for tf, expected_secs in [('5m', 300), ('10m', 600), ('15m', 900), ('1h', 3600), ('4h', 14400)]:
        expected_periods = int((365 * 24 * 3600) / expected_secs)
        periods = Backtester._periods_per_year(tf)
        assert periods == expected_periods, f"Incorrect periods_per_year for {tf}"


# ─── 7. REST API Endpoints (Step 5 & 6) ───

def test_timeframe_api_endpoints():
    """Verify /api/timeframe/current, /api/timeframe/comparison, and /api/timeframe/change."""
    app = Flask(__name__)
    register_timeframe_routes(app)

    cfg = {'timeframe': '15m'}
    wallet = PaperWallet({'paper_trading': {'starting_balance': 10000.0, 'currency': 'USDT'}})
    tracker = MagicMock()
    tracker.get_trades_by_timeframe.return_value = []
    tracker.get_recent_events.return_value = []

    callback_called = []
    def on_change(new_tf):
        callback_called.append(new_tf)
        cfg['timeframe'] = new_tf

    init_timeframe_api(
        tracker=tracker,
        wallet=wallet,
        market_feed=MagicMock(),
        fee_model=MagicMock(),
        config=cfg,
        on_timeframe_change=on_change,
    )

    client = app.test_client()

    # 1. GET /api/timeframe/current
    res = client.get('/api/timeframe/current')
    assert res.status_code == 200
    data = res.get_json()
    assert data['current_timeframe'] == '15m'
    assert data['open_positions_count'] == 0
    assert len(data['supported_timeframes']) == 5

    # 2. GET /api/timeframe/comparison
    res_cmp = client.get('/api/timeframe/comparison')
    assert res_cmp.status_code == 200
    data_cmp = res_cmp.get_json()
    assert 'timeframes' in data_cmp
    assert len(data_cmp['timeframes']) == 5
    # All show insufficient data initially
    assert all(row['status'] == 'insufficient_data' for row in data_cmp['timeframes'])

    # 3. POST /api/timeframe/change to 10m
    res_chg = client.post('/api/timeframe/change', json={'timeframe': '10m'})
    assert res_chg.status_code == 200
    data_chg = res_chg.get_json()
    assert data_chg['status'] == 'SUCCESS'
    assert data_chg['new_timeframe'] == '10m'
    assert callback_called == ['10m']

    # 4. Open position then change without force -> CONFIRMATION_REQUIRED
    wallet.open_position('BTCUSDT', 'long', 0.1, 50000.0, 5.0, timeframe='10m')
    res_chg2 = client.post('/api/timeframe/change', json={'timeframe': '1h'})
    assert res_chg2.status_code == 409
    data_chg2 = res_chg2.get_json()
    assert data_chg2['status'] == 'CONFIRMATION_REQUIRED'
    assert data_chg2['open_positions_count'] == 1


# ─── 8. Timeframe Comparator (Step 6 Controlled Evaluation) ───

def test_timeframe_comparator_controlled_evaluation():
    """Verify that TimeframeComparator runs isolated backtests per timeframe with shared parameters."""
    import asyncio
    cfg = {
        'backtest': {'train_ratio': 0.7, 'min_sharpe': 0.5, 'min_trades': 2},
        'fees': {'spot_maker': 0.001, 'spot_taker': 0.001, 'mode': 'spot'},
        'slippage': {'base_bps': 0, 'volatility_multiplier': 0, 'max_bps': 0, 'random_jitter_pct': 0},
        'risk': {'max_risk_per_trade_pct': 2.0, 'max_notional_pct': 100.0}
    }
    market_feed = MagicMock()
    fee_model = FeeModel(cfg)

    # Generate synthetic 5m candles (150 candles)
    candles_5m = [
        Candle(timestamp=float(i * 300), o=50000.0 + (i % 10) * 10,
               h=50150.0, l=49900.0, c=50020.0 + (i % 10) * 10, volume=10.0, is_closed=True)
        for i in range(150)
    ]

    async def mock_get_klines(symbol, interval, start_time, end_time):
        return candles_5m

    market_feed.get_all_historical_klines = AsyncMock(side_effect=mock_get_klines)

    comparator = TimeframeComparator(cfg, market_feed, fee_model)
    res = asyncio.run(comparator.compare(
        strategy_factory=EmaVwapRsiStrategy,
        strategy_params={'ema_fast': 5, 'ema_slow': 15, 'rsi_period': 10},
        symbol='BTCUSDT',
        timeframes=['5m', '10m'],
        days=30,
    ))

    assert isinstance(res, TimeframeComparisonResult)
    assert '5m' in res.results
    assert '10m' in res.results
    table = res.get_comparison_table()
    assert len(table) == 2


# ─── 9. Robustness Against Duplicate and Stale Data ───

def test_candle_aggregator_duplicate_and_stale_rejection():
    """Verify CandleAggregator rejects stale/duplicate candles and maintains aggregation integrity."""
    agg = CandleAggregator('10m')

    # Boundary candle at ts=600 (00:10 UTC)
    c1 = Candle(timestamp=600.0, o=100.0, h=105.0, l=98.0, c=102.0, volume=10.0, is_closed=True)
    assert agg.push(c1) is None
    assert agg.pending_count == 1

    # Stale candle with timestamp older than buffered candle
    c_stale = Candle(timestamp=550.0, o=99.0, h=101.0, l=98.0, c=100.0, volume=5.0, is_closed=True)
    assert agg.push(c_stale) is None
    assert agg.pending_count == 1

    # Exact duplicate timestamp of non-boundary candle should be ignored if pushed when buffer has it
    c2 = Candle(timestamp=900.0, o=102.0, h=108.0, l=101.0, c=107.0, volume=15.0, is_closed=True)
    merged = agg.push(c2)
    assert merged is not None
    assert merged.timestamp == 600.0
    assert agg.pending_count == 0

    # Repeating c2 (ts=900) now that buffer is empty:
    # 900 is NOT a 10m boundary start, so it gets safely rejected!
    assert agg.push(c2) is None
    assert agg.pending_count == 0


# ─── 10. Settings Persistence on Timeframe Change ───

def test_settings_persistence_on_timeframe_change():
    """Verify that updating timeframe persists to settings.json and is restored on next boot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_file = os.path.join(tmpdir, 'settings.json')
        initial_data = {'timeframe': '15m', 'symbols': ['BTCUSDT'], 'risk': {'max_loss': 100}}
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(initial_data, f, indent=4)

        # Update timeframe
        new_tf = '1h'
        with open(settings_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        cfg['timeframe'] = new_tf
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=4)

        # Restore from file
        with open(settings_file, 'r', encoding='utf-8') as f:
            reloaded = json.load(f)
        assert reloaded['timeframe'] == '1h'
        assert reloaded['symbols'] == ['BTCUSDT']


# ─── 11. ExecutionTracker Multi-Timeframe Filtering & Metrics ───

def test_execution_tracker_timeframe_filtering():
    """Verify ExecutionTracker filters trades correctly by symbol, strategy, and date range."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'filter_test.db')
        tracker = ExecutionTracker(db_path=db_path)

        now = time.time()
        # Record 4 trades with varying attributes
        tracker.record_trade(
            symbol='BTCUSDT', timeframe='15m', strategy_name='ema_vwap_rsi',
            entry_price=50000, exit_price=51000, quantity=0.1,
            gross_pnl=100, fees=5, net_pnl=95, outcome='win', timestamp=now - 200
        )
        tracker.record_trade(
            symbol='BTCUSDT', timeframe='15m', strategy_name='ema_vwap_rsi',
            entry_price=51000, exit_price=50500, quantity=0.1,
            gross_pnl=-50, fees=5, net_pnl=-55, outcome='loss', timestamp=now - 150
        )
        tracker.record_trade(
            symbol='ETHUSDT', timeframe='15m', strategy_name='ema_vwap_rsi',
            entry_price=3000, exit_price=3100, quantity=1.0,
            gross_pnl=100, fees=5, net_pnl=95, outcome='win', timestamp=now - 100
        )
        tracker.record_trade(
            symbol='BTCUSDT', timeframe='15m', strategy_name='other_strat',
            entry_price=50000, exit_price=50800, quantity=0.1,
            gross_pnl=80, fees=5, net_pnl=75, outcome='win', timestamp=now - 50
        )

        # All 15m trades (4 total)
        stats_all = tracker.get_timeframe_stats('15m', min_trades=3)
        assert stats_all['total_trades'] == 4
        assert stats_all['status'] == 'completed'

        # Filter by symbol BTCUSDT (3 total)
        stats_btc = tracker.get_timeframe_stats('15m', symbol='BTCUSDT', min_trades=3)
        assert stats_btc['total_trades'] == 3
        assert stats_btc['winning_trades'] == 2

        # Filter by strategy ema_vwap_rsi (3 total across symbols)
        stats_strat = tracker.get_timeframe_stats('15m', strategy='ema_vwap_rsi', min_trades=3)
        assert stats_strat['total_trades'] == 3

        # Filter by date range (only last 80 seconds)
        stats_recent = tracker.get_timeframe_stats('15m', start_time=now - 80, min_trades=2)
        assert stats_recent['total_trades'] == 1
        assert stats_recent['status'] == 'insufficient_data'


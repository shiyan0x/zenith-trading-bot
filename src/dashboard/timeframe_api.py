"""
timeframe_api.py — REST API endpoints for Multi-Timeframe Trading & Performance Comparison.

Endpoints:
- GET  /api/timeframe/comparison — Multi-timeframe metrics table and charts
- POST /api/timeframe/evaluate   — Controlled multi-timeframe backtest evaluation
- GET  /api/timeframe/current    — Current active timeframe & open position status
- POST /api/timeframe/change     — Safe timeframe switching with confirmation policy
"""

import os
import json
import time
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from flask import Blueprint, jsonify, request

from src.core.candle_aggregator import is_native, needs_aggregation, get_source_interval
from src.brain.timeframe_comparator import TimeframeComparator
from src.strategies.ema_vwap_rsi import EmaVwapRsiStrategy
from src.strategies.mean_reversion import MeanReversionStrategy

logger = logging.getLogger(__name__)

timeframe_bp = Blueprint('timeframe_api', __name__, url_prefix='/api/timeframe')

# Global references configured by init_timeframe_api
_execution_tracker = None
_wallet = None
_market_feed = None
_fee_model = None
_config = None
_on_timeframe_change_cb = None

TARGET_TIMEFRAMES = ['5m', '10m', '15m', '1h', '4h']


def init_timeframe_api(tracker=None, wallet=None, market_feed=None,
                       fee_model=None, config=None, on_timeframe_change=None):
    """Configure timeframe API dependencies."""
    global _execution_tracker, _wallet, _market_feed, _fee_model, _config, _on_timeframe_change_cb
    _execution_tracker = tracker
    _wallet = wallet
    _market_feed = market_feed
    _fee_model = fee_model
    _config = config
    _on_timeframe_change_cb = on_timeframe_change


def register_timeframe_routes(app):
    """Register the timeframe Blueprint with the dashboard app."""
    app.register_blueprint(timeframe_bp)
    logger.info("[API] Timeframe comparison routes registered at /api/timeframe")


@timeframe_bp.route('/current', methods=['GET'])
def get_current_timeframe():
    """Return active paper-trading timeframe and open positions."""
    current_tf = '15m'
    if _config:
        current_tf = _config.get('timeframe', '15m')

    open_pos = []
    if _wallet and _wallet.positions:
        open_pos = [p.to_dict() for p in _wallet.positions.values()]

    return jsonify({
        'current_timeframe': current_tf,
        'is_native': is_native(current_tf),
        'needs_aggregation': needs_aggregation(current_tf),
        'source_interval': get_source_interval(current_tf),
        'open_positions_count': len(open_pos),
        'open_positions': open_pos,
        'supported_timeframes': [
            {
                'timeframe': tf,
                'is_native': is_native(tf),
                'method': 'Direct WebSocket & REST' if is_native(tf) else f"Aggregated (2 × {get_source_interval(tf)})",
                'is_active': tf == current_tf
            }
            for tf in TARGET_TIMEFRAMES
        ]
    })


@timeframe_bp.route('/change', methods=['POST'])
def change_timeframe():
    """
    Switch the active trading timeframe safely.
    If positions are open, requires confirmation or closes them cleanly.
    """
    data = request.get_json(silent=True) or {}
    new_tf = data.get('timeframe')
    force = data.get('force', False)

    if not new_tf or new_tf not in TARGET_TIMEFRAMES:
        return jsonify({
            'status': 'ERROR',
            'message': f"Invalid timeframe '{new_tf}'. Supported: {', '.join(TARGET_TIMEFRAMES)}"
        }), 400

    current_tf = _config.get('timeframe', '15m') if _config else '15m'
    if new_tf == current_tf:
        return jsonify({'status': 'NO_CHANGE', 'timeframe': current_tf})

    open_positions = list(_wallet.positions.values()) if _wallet else []
    if open_positions and not force:
        return jsonify({
            'status': 'CONFIRMATION_REQUIRED',
            'message': (
                f"Switching from {current_tf} to {new_tf} will close {len(open_positions)} open position(s) "
                f"at current market prices to avoid cross-timeframe signal contamination. Trade history will be preserved."
            ),
            'open_positions_count': len(open_positions),
            'open_positions': [p.to_dict() for p in open_positions],
        }), 409

    if _on_timeframe_change_cb:
        try:
            _on_timeframe_change_cb(new_tf)
        except Exception as e:
            logger.error(f"[API] Error executing timeframe change callback: {e}")
            return jsonify({'status': 'ERROR', 'message': str(e)}), 500

    return jsonify({
        'status': 'SUCCESS',
        'previous_timeframe': current_tf,
        'new_timeframe': new_tf,
        'message': f"Timeframe switched to {new_tf}. Bot reconnected and strategies re-evaluated."
    })


@timeframe_bp.route('/comparison', methods=['GET'])
def get_timeframe_comparison():
    """
    Build multi-timeframe comparison table and charts for the 5 target timeframes:
    5m, 10m, 15m, 1h, 4h.
    Accepts filter query parameters: symbol, strategy, mode, start_date, end_date.
    """
    symbol_filter = request.args.get('symbol')
    strategy_filter = request.args.get('strategy')
    mode_filter = request.args.get('mode')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    start_ts = None
    end_ts = None
    if start_date_str:
        try:
            start_ts = datetime.fromisoformat(start_date_str).timestamp()
        except Exception:
            pass
    if end_date_str:
        try:
            end_ts = datetime.fromisoformat(end_date_str).timestamp()
        except Exception:
            pass

    rows = []
    # Collect all closed trades from wallet and/or tracker
    all_trades: List[Dict[str, Any]] = []
    if _wallet and hasattr(_wallet, 'closed_trades'):
        all_trades.extend(_wallet.closed_trades)

    if _execution_tracker:
        db_trades = _execution_tracker.get_trades_by_timeframe(
            symbol=symbol_filter,
            strategy=strategy_filter,
            mode=mode_filter,
            start_time=start_ts,
            end_time=end_ts
        )
        # Add any db trades not already in all_trades (by id)
        existing_ids = {t.get('id') for t in all_trades}
        for dt in db_trades:
            if dt.get('id') not in existing_ids:
                all_trades.append(dt)

    for tf in TARGET_TIMEFRAMES:
        # Filter trades for this specific timeframe
        tf_trades = [
            t for t in all_trades
            if t.get('timeframe') == tf
        ]

        if symbol_filter:
            tf_trades = [t for t in tf_trades if t.get('symbol') == symbol_filter]
        if strategy_filter:
            tf_trades = [t for t in tf_trades if t.get('strategy_name') == strategy_filter]
        if mode_filter and mode_filter != 'all':
            tf_trades = [t for t in tf_trades if t.get('mode', 'paper') == mode_filter]
        if start_ts:
            tf_trades = [t for t in tf_trades if t.get('timestamp', t.get('exit_time', 0)) >= start_ts]
        if end_ts:
            tf_trades = [t for t in tf_trades if t.get('timestamp', t.get('exit_time', 0)) <= end_ts]

        total_trades = len(tf_trades)
        date_range_str = "No trades recorded"
        if tf_trades:
            # Sort chronologically
            sorted_trades = sorted(tf_trades, key=lambda x: str(x.get('entry_time', '')))
            d1 = str(sorted_trades[0].get('entry_time', ''))[:10]
            d2 = str(sorted_trades[-1].get('exit_time', ''))[:10]
            date_range_str = f"{d1} to {d2}" if d1 != d2 else d1

        # Count risk events/gaps
        error_count = 0
        if _execution_tracker:
            recent_errs = _execution_tracker.get_recent_events(limit=100, event_type='risk', timeframe=tf)
            error_count = len(recent_errs)

        uptime_pct = 100.0 if error_count == 0 else max(0.0, 100.0 - error_count * 2.0)

        # Minimum trades required to display valid performance metrics (avoid small-sample delusion)
        min_required = 3
        if total_trades < min_required:
            rows.append({
                'timeframe': tf,
                'type': 'native' if is_native(tf) else 'aggregated',
                'aggregation_detail': None if is_native(tf) else f"2 × {get_source_interval(tf)}",
                'status': 'insufficient_data',
                'message': 'Not enough data',
                'sample_size': total_trades,
                'min_required_trades': min_required,
                'total_trades': total_trades,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'gross_pnl': 0.0,
                'fees_paid': 0.0,
                'net_pnl': 0.0,
                'avg_net_pnl': 0.0,
                'max_drawdown_pct': 0.0,
                'profit_factor': 0.0,
                'date_range': date_range_str,
                'data_gaps_and_errors': error_count,
                'uptime_pct': uptime_pct,
            })
            continue

        winning = [t for t in tf_trades if float(t.get('net_pnl', 0.0)) > 0]
        losing = [t for t in tf_trades if float(t.get('net_pnl', 0.0)) < 0]
        gross_pnl = sum(float(t.get('gross_pnl', t.get('net_pnl', 0.0))) for t in tf_trades)
        net_pnl = sum(float(t.get('net_pnl', 0.0)) for t in tf_trades)
        fees_paid = sum(float(t.get('total_fees', t.get('fees', 0.0))) for t in tf_trades)

        gross_wins = sum(float(t.get('gross_pnl', t.get('net_pnl', 0.0))) for t in winning)
        gross_losses = abs(sum(float(t.get('gross_pnl', t.get('net_pnl', 0.0))) for t in losing))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (float('inf') if gross_wins > 0 else 0.0)

        # Max Drawdown across this timeframe's trade curve
        eq = 10000.0
        peak = eq
        max_dd = 0.0
        for t in tf_trades:
            eq += float(t.get('net_pnl', 0.0))
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        rows.append({
            'timeframe': tf,
            'type': 'native' if is_native(tf) else 'aggregated',
            'aggregation_detail': None if is_native(tf) else f"2 × {get_source_interval(tf)}",
            'status': 'completed',
            'sample_size': total_trades,
            'total_trades': total_trades,
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': round((len(winning) / total_trades) * 100.0, 1),
            'gross_pnl': round(gross_pnl, 2),
            'fees_paid': round(fees_paid, 2),
            'net_pnl': round(net_pnl, 2),
            'avg_net_pnl': round(net_pnl / total_trades, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 999.0,
            'date_range': date_range_str,
            'data_gaps_and_errors': error_count,
            'uptime_pct': uptime_pct,
        })

    return jsonify({
        'timeframes': rows,
        'filters': {
            'symbol': symbol_filter or 'All',
            'strategy': strategy_filter or 'All',
            'mode': mode_filter or 'All',
            'start_date': start_date_str,
            'end_date': end_date_str,
        },
        'comparability_notice': (
            "Notice: Metrics are only directly comparable when evaluated across identical date ranges, "
            "symbols, and risk settings. Insufficient sample sizes are marked 'Not enough data' without extrapolation."
        )
    })


@timeframe_bp.route('/evaluate', methods=['POST'])
def run_timeframe_evaluation():
    """
    Run an isolated, controlled backtest evaluation comparing 5m, 10m, 15m, 1h, and 4h.
    Ensures identical symbol, date range, strategy parameters, and fees.
    """
    data = request.get_json(silent=True) or {}
    symbol = data.get('symbol', 'BTCUSDT').upper()
    strategy_key = data.get('strategy', 'ema_vwap_rsi')
    days = int(data.get('days', 30))
    timeframes = data.get('timeframes', TARGET_TIMEFRAMES)

    if not _config or not _market_feed or not _fee_model:
        return jsonify({
            'status': 'ERROR',
            'message': 'Trading bot components not initialized for backtesting'
        }), 503

    strategy_factories = {
        'ema_vwap_rsi': EmaVwapRsiStrategy,
        'mean_reversion': MeanReversionStrategy,
    }

    factory = strategy_factories.get(strategy_key)
    if not factory:
        return jsonify({
            'status': 'ERROR',
            'message': f"Unknown strategy '{strategy_key}'. Available: {list(strategy_factories.keys())}"
        }), 400

    strat_cfg = _config.get('strategies', {}).get(strategy_key, {})

    async def _do_compare():
        comparator = TimeframeComparator(_config, _market_feed, _fee_model)
        return await comparator.compare(
            strategy_factory=factory,
            strategy_params=strat_cfg,
            symbol=symbol,
            timeframes=timeframes,
            days=days,
        )

    try:
        # Run in event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        comparison = loop.run_until_complete(_do_compare())
        loop.close()

        result_dict = comparison.to_dict()
        result_dict['evaluation_parameters'] = {
            'symbol': symbol,
            'strategy': strategy_key,
            'days': days,
            'timeframes_tested': timeframes,
            'training_test_split': '70% Train / 30% Test (Walk-Forward)',
            'comparable': True,
        }
        return jsonify(result_dict)
    except Exception as e:
        logger.error(f"[API] Error running timeframe evaluation: {e}", exc_info=True)
        return jsonify({'status': 'ERROR', 'message': str(e)}), 500

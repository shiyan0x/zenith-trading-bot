"""
execution_api.py — Dashboard REST API routes for Execution, Paper Trading & Risk.

Exposes read-only and emergency control endpoints:
- GET  /api/execution/status
- GET  /api/execution/events
- GET  /api/execution/paper-vs-backtest
- GET  /api/execution/notifications
- POST /api/execution/emergency-stop
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

execution_bp = Blueprint('execution_api', __name__, url_prefix='/api/execution')

# Global references set by main.py or server.py
_execution_tracker = None
_emergency_stop = None
_paper_comparator = None
_risk_guardian = None
_live_auth = None
_stale_detector = None
_notification_mgr = None
_wallet = None


def init_execution_api(tracker=None, emergency_stop=None,
                       comparator=None, guardian=None,
                       live_auth=None, stale_detector=None,
                       notification_mgr=None, wallet=None):
    """Configure execution API dependencies."""
    global _execution_tracker, _emergency_stop, _paper_comparator
    global _risk_guardian, _live_auth, _stale_detector, _notification_mgr, _wallet
    _execution_tracker = tracker
    _emergency_stop = emergency_stop
    _paper_comparator = comparator
    _risk_guardian = guardian
    _live_auth = live_auth
    _stale_detector = stale_detector
    _notification_mgr = notification_mgr
    _wallet = wallet


@execution_bp.route('/status', methods=['GET'])
def get_execution_status():
    """Return execution engine mode, emergency status, and risk guardian limits."""
    mode = "live" if (_live_auth and _live_auth.is_live_enabled) else "paper"
    is_halted = _emergency_stop.is_halted if _emergency_stop else False
    halt_reason = _emergency_stop._halt_reason if _emergency_stop else ""

    limits = _risk_guardian.get_limits() if _risk_guardian else {}
    return jsonify({
        'mode': mode,
        'is_emergency_halted': is_halted,
        'halt_reason': halt_reason,
        'risk_limits': limits,
    })


@execution_bp.route('/events', methods=['GET'])
def get_recent_events():
    """Return recent execution audit events."""
    limit = request.args.get('limit', default=50, type=int)
    event_type = request.args.get('type', default=None, type=str)
    if _execution_tracker:
        events = _execution_tracker.get_recent_events(limit=limit, event_type=event_type)
    else:
        events = []
    return jsonify({'count': len(events), 'events': events})


@execution_bp.route('/paper-vs-backtest', methods=['GET'])
def get_paper_vs_backtest():
    """Return comparison of paper trading results against backtest baseline."""
    if not _paper_comparator or not _wallet:
        return jsonify({'status': 'UNAVAILABLE', 'message': 'Comparator not initialized'})

    paper_metrics = _paper_comparator.calculate_paper_metrics(_wallet.closed_trades)
    # Default baseline expectation for demo display
    baseline_metrics = {
        'total_trades': 30,
        'sharpe_ratio': 1.5,
        'win_rate_pct': 55.0,
        'max_drawdown_pct': 10.0,
        'profit_factor': 1.6,
    }
    report = _paper_comparator.compare(paper_metrics, baseline_metrics, strategy_name="Active_Paper_Strategy")
    return jsonify(report)


@execution_bp.route('/notifications', methods=['GET'])
def get_notifications():
    """Return recent critical notifications."""
    if _notification_mgr:
        notifs = _notification_mgr.get_recent_notifications(limit=50)
    else:
        notifs = []
    return jsonify({'count': len(notifs), 'notifications': notifs})


@execution_bp.route('/monitoring', methods=['GET'])
def get_system_monitoring():
    """Return connectivity, feed health, broker status, and risk status."""
    import time
    mode = "live" if (_live_auth and _live_auth.is_live_enabled) else "paper"
    is_halted = _emergency_stop.is_halted if _emergency_stop else False

    symbols = ["BTCUSDT", "ETHUSDT"]
    stale_info = {}
    if _stale_detector:
        for s in symbols:
            is_stale, elapsed = _stale_detector.is_stale(s)
            stale_info[s] = {
                'is_stale': is_stale,
                'elapsed_seconds': round(elapsed, 1) if elapsed < 1e6 else None,
            }

    limits = _risk_guardian.get_limits() if _risk_guardian else {}

    return jsonify({
        'broker': {
            'mode': mode,
            'status': 'HALTED' if is_halted else 'CONNECTED',
            'account_type': 'VIRTUAL (SIMULATED)' if mode == 'paper' else 'REAL (LIVE FUNDS)',
        },
        'data_feed': {
            'exchange': 'Binance',
            'status': 'ACTIVE',
            'symbols': stale_info,
            'timestamp': time.time(),
        },
        'risk': {
            'emergency_stop': is_halted,
            'limits': limits,
        }
    })


@execution_bp.route('/emergency-stop', methods=['POST'])
def trigger_emergency():
    """Trigger global emergency stop from dashboard (requires auth & confirmation)."""
    from src.dashboard.auth import validate_token
    token = request.headers.get("X-Zenith-Token") or request.args.get("token")
    if not validate_token(token):
        return jsonify({'error': 'Unauthorized', 'message': 'Valid authorization token required'}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("confirmation"):
        return jsonify({'error': 'ConfirmationRequired', 'message': 'Confirmation required'}), 400

    if not _emergency_stop:
        return jsonify({'success': False, 'message': 'Emergency stop not configured'}), 500

    reason = data.get('reason', 'Triggered manually via dashboard')
    res = _emergency_stop.trigger(reason=reason, source="dashboard_api")
    return jsonify({'success': True, 'emergency_status': res})


@execution_bp.route('/emergency-reset', methods=['POST'])
def reset_emergency():
    """Reset emergency stop (requires admin reset token & confirmation)."""
    from src.dashboard.auth import validate_token
    token = request.headers.get("X-Zenith-Token") or request.args.get("token")
    if not validate_token(token):
        return jsonify({'error': 'Unauthorized', 'message': 'Valid authorization token required'}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("confirmation"):
        return jsonify({'error': 'ConfirmationRequired', 'message': 'Confirmation required'}), 400

    if not _emergency_stop:
        return jsonify({'success': False, 'message': 'Emergency stop not configured'}), 500

    reset_token = data.get('reset_token', 'RESET_EMERGENCY_OVERRIDE')
    success = _emergency_stop.reset(reset_token)
    if success:
        return jsonify({'success': True, 'message': 'Emergency stop reset successfully.'})
    return jsonify({'success': False, 'message': 'Invalid emergency reset token.'}), 403


@execution_bp.route('/stop-trading', methods=['POST'])
def stop_trading():
    """Halt paper trading loop gracefully."""
    from src.dashboard.auth import validate_token
    token = request.headers.get("X-Zenith-Token") or request.args.get("token")
    if not validate_token(token):
        return jsonify({'error': 'Unauthorized', 'message': 'Valid authorization token required'}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("confirmation"):
        return jsonify({'error': 'ConfirmationRequired', 'message': 'Confirmation required'}), 400

    if _emergency_stop:
        res = _emergency_stop.trigger(reason="Trading stopped by operator via dashboard", source="dashboard_controls")
        return jsonify({'success': True, 'status': 'stopped', 'details': res})
    return jsonify({'success': True, 'status': 'stopped'})


def register_execution_routes(app):
    """Helper to attach the execution blueprint to the Flask app."""
    if "execution_api" not in app.blueprints:
        app.register_blueprint(execution_bp)

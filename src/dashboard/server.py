"""
server.py — Live dashboard web server.

Runs a Flask + SocketIO server on localhost:5000 that shows:
- Sidebar-based SPA with 8 views
- Live equity curve
- Open positions with unrealized PnL
- Trade history
- Strategy scoreboard
- Risk panel (drawdown, Kelly, circuit breaker status)
- Episodes (trading runs derived from trade data)
- Lessons (insights derived from trading performance)
- World (live market data and conditions)

Premium dark theme with sidebar navigation and warm gradient accents.
"""

import os
import json
import logging
import threading
from flask import Flask, render_template_string, jsonify, send_from_directory
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)


def create_dashboard_app(bot_state: dict) -> tuple:
    """
    Create and configure the dashboard Flask app.

    Args:
        bot_state: a shared dict that the trading engine updates
                   and the dashboard reads from.

    Returns:
        (app, socketio) tuple
    """
    # Determine template directory
    dashboard_dir = os.path.dirname(os.path.abspath(__file__))

    app = Flask(__name__,
                template_folder=dashboard_dir,
                static_folder=dashboard_dir)
    app.config['SECRET_KEY'] = 'honest-bot-dashboard'

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    # Callback for timeframe changes — set by main.py
    app._on_timeframe_change = None

    @app.route('/')
    def index():
        """Serve the main dashboard page."""
        html_path = os.path.join(dashboard_dir, 'index.html')
        with open(html_path, 'r', encoding='utf-8') as f:
            return f.read()

    @app.route('/dashboard.js')
    def dashboard_js():
        """Serve the dashboard JavaScript."""
        return send_from_directory(dashboard_dir, 'dashboard.js',
                                   mimetype='application/javascript')

    @app.route('/api/state')
    def get_state():
        """API endpoint — returns full bot state as JSON."""
        return jsonify(bot_state)

    @app.route('/api/episodes')
    def get_episodes():
        """API endpoint — returns derived episode/run data."""
        trades = bot_state.get('recent_trades', [])
        wallet = bot_state.get('wallet', {})
        stats = wallet.get('stats', {})
        start_bal = stats.get('starting_balance', 10000)

        episodes = []
        if not trades:
            equity = wallet.get('equity', start_bal)
            episodes.append({
                'number': 1,
                'status': 'running',
                'trades': 0,
                'pnl': equity - start_bal,
                'start_balance': start_bal,
                'end_balance': equity
            })
        else:
            current_ep = {
                'number': 1,
                'trades': [],
                'start_balance': start_bal,
                'pnl': 0
            }

            for i, trade in enumerate(trades):
                current_ep['trades'].append(trade)
                current_ep['pnl'] += trade.get('net_pnl', 0)

                end_bal = current_ep['start_balance'] + current_ep['pnl']
                is_blowup = end_bal <= current_ep['start_balance'] * 0.5
                is_goal = current_ep['pnl'] >= 500

                if is_blowup or is_goal or i == len(trades) - 1:
                    status = 'running'
                    if i < len(trades) - 1 or is_blowup or is_goal:
                        status = 'goal' if is_goal else 'blowup'
                    if i == len(trades) - 1 and not is_blowup and not is_goal:
                        status = 'running'

                    episodes.append({
                        'number': current_ep['number'],
                        'status': status,
                        'trades': len(current_ep['trades']),
                        'pnl': current_ep['pnl'],
                        'start_balance': current_ep['start_balance'],
                        'end_balance': end_bal
                    })

                    if i < len(trades) - 1:
                        current_ep = {
                            'number': current_ep['number'] + 1,
                            'trades': [],
                            'start_balance': end_bal,
                            'pnl': 0
                        }

        return jsonify(episodes)

    @app.route('/api/lessons')
    def get_lessons():
        """API endpoint — returns derived insights from trades."""
        trades = bot_state.get('recent_trades', [])
        wallet = bot_state.get('wallet', {})
        stats = wallet.get('stats', {})
        lessons = []

        if not trades:
            lessons.append({
                'icon': '🌱',
                'title': 'Just Getting Started',
                'body': 'No trades yet. The bot is analyzing market conditions.'
            })
        else:
            total_pnl = sum(t.get('net_pnl', 0) for t in trades)
            lessons.append({
                'icon': '💰' if total_pnl > 0 else '📉',
                'title': f'Net {"Positive" if total_pnl > 0 else "Negative"}',
                'body': f'Total PnL: ${abs(total_pnl):.2f} across {len(trades)} trades.'
            })

        return jsonify(lessons)

    @app.route('/api/news')
    def get_news():
        """API endpoint — returns news headlines with sentiment scores."""
        return jsonify({
            'items': bot_state.get('news', []),
            'sentiment': bot_state.get('sentiment', {}),
        })

    @socketio.on('connect')
    def handle_connect():
        logger.debug("[DASHBOARD] Client connected")
        socketio.emit('state_update', bot_state)

    @socketio.on('request_state')
    def handle_request():
        socketio.emit('state_update', bot_state)

    @socketio.on('change_timeframe')
    def handle_timeframe_change(data):
        """Handle timeframe change request from the dashboard."""
        new_tf = data.get('timeframe', '1m')
        valid_tfs = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '1d']
        if new_tf not in valid_tfs:
            logger.warning(f"[DASHBOARD] Invalid timeframe: {new_tf}")
            return

        old_tf = bot_state.get('timeframe', '1m')
        if new_tf == old_tf:
            return

        logger.info(f"[DASHBOARD] Timeframe change requested: {old_tf} → {new_tf}")
        bot_state['timeframe'] = new_tf

        # Notify all clients
        socketio.emit('timeframe_changed', {'timeframe': new_tf})

        # Trigger the bot to reconnect with the new timeframe
        if app._on_timeframe_change:
            try:
                app._on_timeframe_change(new_tf)
            except Exception as e:
                logger.error(f"[DASHBOARD] Timeframe change callback error: {e}")

    return app, socketio


def run_dashboard(bot_state: dict, host: str = '127.0.0.1', port: int = 5000):
    """
    Start the dashboard server in a background thread.

    Call this from main.py and the dashboard will be available
    at http://localhost:5000 while the bot runs.
    """
    app, socketio = create_dashboard_app(bot_state)

    def _run():
        logger.info(f"[DASHBOARD] Starting at http://{host}:{port}")
        socketio.run(app, host=host, port=port,
                     allow_unsafe_werkzeug=True, use_reloader=False)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return app, socketio

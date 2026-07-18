"""
main.py — Entry point for the Honest AI Trading Bot.

This is the master conductor. It:
1. Loads config
2. Initializes all components
3. Runs backtests to filter strategies
4. Starts the live dashboard
5. Connects to Binance WebSocket for live prices
6. Runs the trading loop (paper money, real prices)
7. Reports honestly

Run with: python src/main.py
Dashboard at: http://localhost:5000
"""

import os
import sys
import json
import time
import asyncio
import logging
import signal
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.core.market_feed import MarketFeed, Candle
from src.core.fee_model import FeeModel
from src.core.paper_wallet import PaperWallet
from src.core.order_engine import OrderEngine
from src.core.trade_logger import TradeLogger
from src.strategies.sma_crossover import SMACrossover
from src.strategies.rsi_mean_revert import RSIMeanRevert
from src.strategies.breakout import BreakoutStrategy
from src.brain.backtester import Backtester
from src.brain.kelly_sizer import KellySizer
from src.brain.risk_manager import RiskManager
from src.dashboard.server import run_dashboard

# ─── Ensure log directory exists before setting up logging ───
_log_dir = os.path.join(PROJECT_ROOT, 'data', 'logs')
os.makedirs(_log_dir, exist_ok=True)

# ─── Logging Setup ───
# Force UTF-8 encoding on Windows to avoid UnicodeEncodeError with emojis
import io
_stream_handler = logging.StreamHandler(
    stream=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
)
_file_handler = logging.FileHandler(
    os.path.join(_log_dir, 'bot.log'), mode='a', encoding='utf-8'
)
_formatter = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%H:%M:%S')
_stream_handler.setFormatter(_formatter)
_file_handler.setFormatter(_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_stream_handler, _file_handler],
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load configuration from settings.json."""
    config_path = os.path.join(PROJECT_ROOT, 'config', 'settings.json')
    with open(config_path, 'r') as f:
        config = json.load(f)
    logger.info(f"[INIT] Config loaded from {config_path}")
    return config


class TradingBot:
    """
    The main trading bot — ties everything together.

    This is a simulation. Real prices, fake money, honest results.
    """

    def __init__(self, config: dict):
        self.config = config
        self.running = False

        # ─── Core Components ───
        self.market_feed = MarketFeed(config)
        self.fee_model = FeeModel(config)
        self.wallet = PaperWallet(config)
        self.trade_logger = TradeLogger(
            os.path.join(PROJECT_ROOT, 'data', 'logs')
        )
        self.order_engine = OrderEngine(
            self.wallet, self.fee_model, self.trade_logger
        )

        # ─── Brain ───
        self.backtester = Backtester(config, self.market_feed, self.fee_model)
        self.kelly = KellySizer(config)
        self.risk_manager = RiskManager(config)

        # ─── Strategies ───
        strat_cfg = config.get('strategies', {})
        self.all_strategies = []
        self.active_strategies = []  # only strategies that passed backtest

        if strat_cfg.get('sma_crossover', {}).get('enabled', True):
            self.all_strategies.append(
                SMACrossover(strat_cfg.get('sma_crossover', {}))
            )
        if strat_cfg.get('rsi_mean_revert', {}).get('enabled', True):
            self.all_strategies.append(
                RSIMeanRevert(strat_cfg.get('rsi_mean_revert', {}))
            )
        if strat_cfg.get('breakout', {}).get('enabled', True):
            self.all_strategies.append(
                BreakoutStrategy(strat_cfg.get('breakout', {}))
            )

        # ─── Dashboard State (shared dict) ───
        self.bot_state = {
            'wallet': {},
            'risk': {},
            'kelly': {},
            'strategies': [],
            'recent_trades': [],
            'prices': {},
            'status': 'initializing',
            'timeframe': config.get('timeframe', '1m'),
        }

        # ─── Timeframe switching ───
        self._pending_timeframe = None

        # ─── Backtest Results ───
        self.backtest_results = []

        logger.info("[INIT] Trading Bot initialized")
        logger.info(f"[INIT] Starting balance: ${self.wallet.starting_balance:,.2f} (FAKE)")
        logger.info(f"[INIT] Symbols: {config['symbols']}")
        logger.info(f"[INIT] Strategies: {[s.name for s in self.all_strategies]}")

    def _update_dashboard_state(self, prices: dict = None):
        """Update the shared state dict that the dashboard reads."""
        prices = prices or self.bot_state.get('prices', {})
        self.bot_state.update({
            'wallet': self.wallet.to_dict(prices),
            'risk': self.risk_manager.get_status(),
            'kelly': self.kelly.get_stats(),
            'recent_trades': self.wallet.closed_trades[-50:],
            'prices': prices,
            'status': 'running' if self.running else 'stopped',
            'timeframe': self.config.get('timeframe', '1m'),
        })

    def _handle_timeframe_change(self, new_tf: str):
        """
        Called by the dashboard server when user selects a new timeframe.
        Stops the current WebSocket so the run loop can restart it.
        """
        old_tf = self.config.get('timeframe', '1m')
        logger.info(f"[BOT] Timeframe change: {old_tf} → {new_tf}")
        self.config['timeframe'] = new_tf
        self.bot_state['timeframe'] = new_tf
        self._pending_timeframe = new_tf

        # Reset strategy candle history since interval changed
        for strategy in self.active_strategies:
            strategy.reset()
        logger.info(f"[BOT] Strategies reset for {new_tf} candles")

        # Stop current WebSocket — the run loop will reconnect
        self.market_feed.stop()

    async def run_backtests(self):
        """
        Step 3: Run backtests on all strategies.
        Only strategies that pass on unseen test data get activated.

        If none pass, we say so honestly.
        """
        logger.info("\n" + "=" * 60)
        logger.info("  STEP 3: RUNNING BACKTESTS ON REAL HISTORICAL DATA")
        logger.info("=" * 60)

        symbols = self.config['symbols']
        bt_days = self.config.get('backtest', {}).get('history_days', 90)
        strategy_results = []

        for strategy in self.all_strategies:
            for symbol in symbols:
                try:
                    result = await self.backtester.run(
                        strategy=strategy,
                        symbol=symbol,
                        interval='1h',  # 1-hour candles for backtest
                        days=bt_days
                    )
                    if result:
                        strategy_results.append(result)
                        self.backtest_results.append(result)
                except Exception as e:
                    logger.error(f"[BACKTEST] Error testing {strategy.name} on {symbol}: {e}")

        # Filter: only keep strategies that passed on test data
        passed = [r for r in strategy_results if r['passed']]

        # Update dashboard with backtest results
        self.bot_state['strategies'] = [
            r['test'].to_dict() for r in strategy_results
        ]

        if passed:
            # Deduplicate: one strategy instance per name
            seen = set()
            for r in passed:
                sname = r['strategy']
                if sname not in seen:
                    seen.add(sname)
                    for s in self.all_strategies:
                        if s.name == sname:
                            self.active_strategies.append(s)
                            break

            logger.info(f"\n[BACKTEST] ✅ {len(self.active_strategies)} strategies PASSED:")
            for s in self.active_strategies:
                logger.info(f"  → {s.name}")
        else:
            logger.warning(
                "\n[BACKTEST] ❌ NO strategies passed the backtest.\n"
                "  This is honest: none of the tested strategies showed "
                "an edge on recent unseen data.\n"
                "  The bot will still run and show you live prices, "
                "but it won't take trades until a strategy passes.\n"
            )
            # Activate all anyway for demo/observation purposes
            # but with minimum position sizes
            self.active_strategies = list(self.all_strategies)
            logger.info(
                "  [NOTE] Activating all strategies in observation mode "
                "(small position sizes) so you can watch them work."
            )

        self._update_dashboard_state()

    def _on_candle(self, candle: Candle):
        """
        Called on every new candle from the WebSocket.
        This is the live trading loop heartbeat.
        """
        symbol = self.config['symbols'][0]  # primary symbol
        prices = {symbol: candle.close}
        self.bot_state['prices'] = prices

        # Only act on closed candles (complete data)
        if not candle.is_closed:
            self._update_dashboard_state(prices)
            return

        logger.debug(f"[CANDLE] {candle}")

        # ─── Risk Check ───
        if not self.risk_manager.can_trade():
            self._update_dashboard_state(prices)
            return

        current_drawdown = self.wallet.get_drawdown(prices)
        if self.risk_manager.check_drawdown(current_drawdown):
            # Circuit breaker triggered — close everything
            self.order_engine.close_all(prices)
            self._update_dashboard_state(prices)
            return

        # ─── Strategy Signals ───
        for strategy in self.active_strategies:
            strategy.update(candle)

            # Check for exit first (if we have a position)
            pos = self.wallet.get_position_for_symbol(symbol)
            if pos:
                if strategy.should_exit(pos.side):
                    self.order_engine.market_sell(
                        symbol=symbol,
                        position_id=pos.id,
                        current_price=candle.close
                    )
                continue  # don't enter and exit on same candle

            # Check for entry
            signal = strategy.should_enter()
            if signal == 'long' and not self.wallet.get_position_for_symbol(symbol):
                # Calculate position size using Kelly
                kelly_pct = self.kelly.get_position_size_pct(
                    self.wallet.closed_trades
                )
                if kelly_pct <= 0:
                    # Not enough data or no edge — use minimum size
                    kelly_pct = 1.0  # 1% of equity

                trade_amount = self.risk_manager.validate_trade_size(
                    kelly_pct,
                    self.wallet.total_equity(prices)
                )

                if trade_amount > 10:  # minimum $10
                    quantity = trade_amount / candle.close
                    self.order_engine.market_buy(
                        symbol=symbol,
                        quantity=quantity,
                        current_price=candle.close
                    )
                break  # only one entry per candle

        self._update_dashboard_state(prices)

    async def run(self):
        """
        Main run loop:
        1. Start dashboard
        2. Run backtests
        3. Stream live prices and trade
        """
        self.running = True

        # ─── Ensure data directories exist ───
        os.makedirs(os.path.join(PROJECT_ROOT, 'data', 'logs'), exist_ok=True)
        os.makedirs(os.path.join(PROJECT_ROOT, 'data', 'history'), exist_ok=True)

        # ─── Step 1: Start Dashboard ───
        dash_cfg = self.config.get('dashboard', {})
        app, socketio = run_dashboard(
            self.bot_state,
            host=dash_cfg.get('host', '127.0.0.1'),
            port=dash_cfg.get('port', 5000)
        )
        self.socketio = socketio

        # Register timeframe change callback
        app._on_timeframe_change = self._handle_timeframe_change

        logger.info("\n" + "=" * 60)
        logger.info("  🚀 HONEST AI TRADING BOT — STARTING")
        logger.info("  Real prices. Fake money. It never lies.")
        logger.info(f"  Dashboard: http://{dash_cfg.get('host', '127.0.0.1')}:{dash_cfg.get('port', 5000)}")
        logger.info("=" * 60 + "\n")

        # ─── Step 2: Verify connection with a live price ───
        logger.info("[STEP 2] Fetching live price to verify connection...")
        primary_symbol = self.config['symbols'][0]
        try:
            price = await self.market_feed.get_current_price(primary_symbol)
            logger.info(f"[STEP 2] ✅ Live {primary_symbol} price: ${price:,.2f}")
            self.bot_state['prices'] = {primary_symbol: price}
        except Exception as e:
            logger.error(f"[STEP 2] ❌ Could not fetch live price: {e}")
            logger.error("  Check your internet connection and try again.")
            return

        # ─── Step 3: Run Backtests ───
        await self.run_backtests()

        # ─── Step 4: Start Live Trading ───
        logger.info("\n" + "=" * 60)
        logger.info("  STEP 4: STARTING LIVE PAPER TRADING")
        logger.info(f"  Streaming {primary_symbol} via WebSocket...")
        logger.info(f"  Active strategies: {[s.name for s in self.active_strategies]}")
        logger.info("=" * 60 + "\n")

        # Push updates to dashboard periodically
        async def push_dashboard_updates():
            while self.running:
                try:
                    self.socketio.emit('state_update', self.bot_state)
                except Exception:
                    pass
                await asyncio.sleep(3)

        # Run WebSocket stream and dashboard updates concurrently
        update_task = asyncio.create_task(push_dashboard_updates())

        try:
            # Main stream loop — restarts when timeframe changes
            while self.running:
                self._pending_timeframe = None
                current_tf = self.config.get('timeframe', '1m')
                logger.info(f"[WS] Starting stream: {primary_symbol} @ {current_tf}")

                # Re-enable market feed for new stream
                self.market_feed._running = True

                await self.market_feed.stream_live(
                    symbol=primary_symbol,
                    interval=current_tf,
                    on_candle=self._on_candle
                )

                # If we get here, stream_live exited.
                # Check if it was a timeframe change or a real stop
                if self._pending_timeframe:
                    logger.info(f"[BOT] Reconnecting with {self._pending_timeframe} timeframe...")
                    await asyncio.sleep(1)  # brief pause before reconnect
                    continue
                else:
                    break  # real stop requested

        except KeyboardInterrupt:
            logger.info("\n[BOT] Shutting down gracefully...")
        finally:
            self.running = False
            update_task.cancel()
            self._print_final_report()

    def _print_final_report(self):
        """Print an honest final performance report."""
        stats = self.wallet.get_stats()
        prices = self.bot_state.get('prices', {})

        logger.info("\n" + "=" * 60)
        logger.info("  📊 FINAL HONEST REPORT")
        logger.info("=" * 60)
        logger.info(f"  Starting Balance:   ${stats['starting_balance']:,.2f} (FAKE)")
        logger.info(f"  Current Cash:       ${stats['current_cash']:,.2f}")
        logger.info(f"  Total Trades:       {stats['total_trades']}")
        logger.info(f"  Win Rate:           {stats['win_rate_pct']:.1f}%")
        logger.info(f"  Avg Win:            ${stats['avg_win']:.2f}")
        logger.info(f"  Avg Loss:           ${stats['avg_loss']:.2f}")
        logger.info(f"  Total Fees Paid:    ${stats['total_fees_paid']:.2f}")
        logger.info(f"  Net Return:         {stats['net_return_pct']:+.2f}%")
        logger.info(f"  Circuit Breakers:   {len(self.risk_manager.breaker_history)}")
        logger.info("=" * 60)
        logger.info("  Remember: this was fake money.")
        logger.info("  Real trading has more slippage, emotional pressure,")
        logger.info("  and exchange outages. Be careful.")
        logger.info("=" * 60 + "\n")


async def main():
    """Entry point."""
    config = load_config()
    bot = TradingBot(config)

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        logger.info("\n[BOT] Ctrl+C received — shutting down...")
        bot.running = False
        bot.market_feed.stop()

    signal.signal(signal.SIGINT, signal_handler)

    await bot.run()


if __name__ == '__main__':
    asyncio.run(main())

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
import statistics
from contextlib import suppress
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.core.market_feed import MarketFeed, Candle
from src.core.fee_model import FeeModel
from src.core.paper_wallet import PaperWallet
from src.core.order_engine import OrderEngine
from src.core.trade_logger import TradeLogger
from src.strategies.ema_vwap_rsi import EmaVwapRsiStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.brain.backtester import Backtester
from src.brain.kelly_sizer import KellySizer
from src.brain.risk_manager import RiskManager
from src.brain.sentiment import SentimentAnalyzer
from src.core.news_feed import NewsFeed
from src.dashboard.server import run_dashboard
from src.execution import (
    PaperBrokerAdapter,
    LiveRiskGuardian,
    LiveModeAuth,
    EmergencyStop,
    ExecutionTracker,
    PaperVsBacktestComparator,
    StaleDataDetector,
    ReconnectHandler,
    DuplicateEventFilter,
    StatePersistence,
    NotificationManager,
    init_execution_api,
)

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

        # ─── Execution & Resilience Components ───
        self.execution_tracker = ExecutionTracker()
        self.persistence = StatePersistence()
        self.persistence.restore_wallet(self.wallet)

        self.broker = PaperBrokerAdapter(self.order_engine, self.wallet)
        self.stale_detector = StaleDataDetector(
            default_max_age_seconds=config.get('stale_threshold_seconds', 120.0)
        )
        self.reconnect_handler = ReconnectHandler()
        self.duplicate_filter = DuplicateEventFilter()
        self.notification_mgr = NotificationManager()
        self.emergency_stop = EmergencyStop()

        risk_cfg = config.get('risk', {})
        self.live_guardian = LiveRiskGuardian(
            max_order_usd=risk_cfg.get('max_order_usd', 1000.0),
            daily_loss_limit_usd=risk_cfg.get('daily_loss_limit_usd', 300.0),
            max_exposure_pct=risk_cfg.get('max_exposure_pct', 50.0),
            max_open_positions=risk_cfg.get('max_open_positions', 2),
            leverage_cap=1.0,
        )
        self.live_auth = LiveModeAuth(self.live_guardian, config['symbols'])
        self.paper_comparator = PaperVsBacktestComparator()

        init_execution_api(
            tracker=self.execution_tracker,
            emergency_stop=self.emergency_stop,
            comparator=self.paper_comparator,
            guardian=self.live_guardian,
            live_auth=self.live_auth,
            stale_detector=self.stale_detector,
            notification_mgr=self.notification_mgr,
            wallet=self.wallet,
        )

        # ─── News & Sentiment ───
        self.news_feed = NewsFeed(config)
        self.sentiment = SentimentAnalyzer(config)

        # ─── Strategies ───
        strat_cfg = config.get('strategies', {})
        self.strategy_specs = []
        self.active_strategies = {symbol: [] for symbol in config['symbols']}
        self.allow_short = (
            config.get('trading', {}).get('allow_short', False)
            and self.fee_model.mode == 'futures'
        )

        if strat_cfg.get('ema_vwap_rsi', {}).get('enabled', True):
            self.strategy_specs.append(
                ('ema_vwap_rsi', EmaVwapRsiStrategy,
                 strat_cfg.get('ema_vwap_rsi', {}))
            )
        if strat_cfg.get('mean_reversion', {}).get('enabled', True):
            self.strategy_specs.append(
                ('mean_reversion', MeanReversionStrategy,
                 strat_cfg.get('mean_reversion', {}))
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
            'news': [],
            'sentiment': {},
        }

        # ─── Timeframe switching ───
        self._pending_timeframe = None
        self._pending_revalidation = False
        self._revalidation_task = None

        # ─── Backtest Results ───
        self.backtest_results = []

        logger.info("[INIT] Trading Bot initialized")
        logger.info(f"[INIT] Starting balance: ${self.wallet.starting_balance:,.2f} (FAKE)")
        logger.info(f"[INIT] Symbols: {config['symbols']}")
        logger.info(f"[INIT] Strategies: {[key for key, _, _ in self.strategy_specs]}")
        if not self.allow_short:
            logger.info("[INIT] Spot mode: short signals are ignored.")

    def _update_dashboard_state(self, prices: dict = None):
        """Update the shared state dict that the dashboard reads."""
        prices = prices or self.bot_state.get('prices', {})
        # Preserve explicit phase statuses ('backtesting', 'initializing')
        # Only update status when the bot is in live-trading mode
        if self.running and self.bot_state.get('status') not in ('backtesting', 'initializing'):
            current_status = 'running'
        elif not self.running:
            current_status = 'stopped'
        else:
            current_status = self.bot_state.get('status', 'initializing')
        self.bot_state.update({
            'wallet': self.wallet.to_dict(prices),
            'risk': self.risk_manager.get_status(),
            'kelly': self.kelly.get_stats(),
            'recent_trades': self.wallet.closed_trades[-100:],
            'prices': prices,
            'status': current_status,
            'timeframe': self.config.get('timeframe', '1m'),
            'news': self.news_feed.get_news_dicts(),
            'sentiment': self.sentiment.get_summary(),
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
        self._pending_revalidation = True

        # A stop/target calculated for one interval is not valid on another.
        # Close first, then clear signal state before reconnecting.
        prices = self.bot_state.get('prices', {})
        if self.wallet.positions:
            self.order_engine.close_all(prices)
            logger.warning("[BOT] Closed open positions before timeframe change")

        # Reset strategy candle history since interval changed.
        for strategies in self.active_strategies.values():
            for strategy in strategies:
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

        # Signal dashboard that we are in the backtest phase
        self.bot_state['status'] = 'backtesting'

        symbols = self.config['symbols']
        bt_days = self.config.get('backtest', {}).get('history_days', 90)
        strategy_results = []
        active = {symbol: [] for symbol in symbols}

        for _, strategy_factory, params in self.strategy_specs:
            for symbol in symbols:
                strategy = strategy_factory(dict(params))
                try:
                    result = await self.backtester.run(
                        strategy=strategy,
                        symbol=symbol,
                        interval=self.config.get('timeframe', '15m'),
                        days=bt_days
                    )
                    if result:
                        strategy_results.append(result)
                        if result['passed']:
                            # Live instances must not reuse a backtest's
                            # candle/position state, and must remain isolated
                            # per symbol.
                            active[symbol].append(strategy_factory(dict(params)))
                except Exception as e:
                    logger.error(f"[BACKTEST] Error testing {strategy.name} on {symbol}: {e}")

        self.backtest_results = strategy_results
        self.active_strategies = active

        # Load approved AI strategies into paper trading
        try:
            from src.research.experiment_store import ExperimentStore
            from src.research.strategy_registry import StrategyRegistry
            from src.research.promotion_gate import PromotionGate
            from src.execution.paper_strategy_loader import PaperStrategyLoader

            db_path = os.path.join(PROJECT_ROOT, 'data', 'research.db')
            if os.path.exists(db_path):
                store = ExperimentStore(db_path)
                registry = StrategyRegistry(store)
                gate = PromotionGate(store, registry)
                loader = PaperStrategyLoader(registry, gate)
                for strat_info in loader.get_active_paper_strategies():
                    try:
                        for symbol in symbols:
                            active[symbol].append(loader.load_strategy_instance(strat_info['id']))
                        logger.info(f"[BACKTEST] Loaded approved AI paper strategy: {strat_info.get('name')}")
                    except Exception as e:
                        logger.warning(f"[BACKTEST] AI strategy {strat_info.get('name')} not loaded: {e}")
        except Exception as e:
            logger.debug(f"[BACKTEST] Research store not loaded: {e}")
        passed = [r for r in strategy_results if r['passed']]

        # Update dashboard with backtest results
        self.bot_state['strategies'] = [
            r['test'].to_dict(self.backtester.min_sharpe, self.backtester.min_trades)
            for r in strategy_results
        ]

        if passed:
            active_count = sum(len(items) for items in active.values())
            logger.info(f"\n[BACKTEST] ✅ {active_count} strategy/symbol pairs PASSED:")
            for symbol, strategies in active.items():
                for strategy in strategies:
                    logger.info(f"  → {strategy.name} on {symbol}")
        else:
            logger.warning(
                "\n[BACKTEST] ❌ NO strategies passed the backtest.\n"
                "  This is honest: none of the tested strategies showed "
                "an edge on recent unseen data.\n"
                "  The bot will still run and show you live prices, "
                "but it won't take trades until a strategy passes.\n"
            )

        # Switch to 'running' now that backtests are done
        self.bot_state['status'] = 'running'
        self._update_dashboard_state()

    @staticmethod
    def _estimate_volatility(strategy) -> float:
        """Estimate recent close-to-close volatility for the slippage model."""
        closes = strategy.closes[-21:]
        if len(closes) < 3:
            return 0.0
        returns = [
            (current / previous) - 1
            for previous, current in zip(closes, closes[1:])
            if previous > 0
        ]
        return statistics.pstdev(returns) if len(returns) > 1 else 0.0

    def _strategy_trades(self, symbol: str, strategy_name: str) -> list[dict]:
        return [
            trade for trade in self.wallet.closed_trades
            if trade.get('symbol') == symbol
            and trade.get('strategy_name') == strategy_name
        ]

    def _discard_trade_plans(self, symbol: str = None):
        groups = (
            [self.active_strategies.get(symbol, [])]
            if symbol else self.active_strategies.values()
        )
        for strategies in groups:
            for strategy in strategies:
                strategy.discard_pending_trade()

    async def _revalidate_after_breaker(self):
        """Require a fresh out-of-sample pass before reopening the breaker."""
        try:
            await self.run_backtests()
            passed = any(self.active_strategies.values())
            self.risk_manager.complete_revalidation(passed)
        except Exception as exc:
            logger.exception("[RISK] Revalidation failed unexpectedly: %s", exc)
            self.risk_manager.complete_revalidation(False)
        finally:
            self._revalidation_task = None
            self._update_dashboard_state()

    def _on_candle(self, symbol: str, candle: Candle):
        """
        Called on every new candle from the WebSocket.
        This is the live trading loop heartbeat.
        """
        prices = dict(self.bot_state.get('prices', {}))
        prices[symbol] = candle.close
        self.bot_state['prices'] = prices

        # Emergency Stop Check
        if self.emergency_stop.is_halted:
            logger.debug("[BOT] Emergency stop active; halting candle processing.")
            self._update_dashboard_state(prices)
            return

        # Duplicate Candle Event Filter
        if self.duplicate_filter.is_duplicate_candle(symbol, candle.timestamp, candle.is_closed):
            return

        # Record Tick for Stale Data Detection
        self.stale_detector.record_tick(symbol, candle.timestamp)

        # Only act on closed candles (complete data)
        if not candle.is_closed:
            self._update_dashboard_state(prices)
            return

        logger.debug(f"[CANDLE] {candle}")

        strategies = self.active_strategies.get(symbol, [])
        for strategy in strategies:
            strategy.update(candle)

        # ─── Risk Check ───
        if not self.risk_manager.can_trade():
            if (self.risk_manager.needs_revalidation()
                    and self._revalidation_task is None):
                self._revalidation_task = asyncio.create_task(
                    self._revalidate_after_breaker()
                )
            self._update_dashboard_state(prices)
            return

        current_drawdown = self.wallet.get_drawdown(prices)
        if self.risk_manager.check_drawdown(current_drawdown):
            # Circuit breaker triggered — close everything
            self.order_engine.close_all(prices)
            self._discard_trade_plans()
            self._update_dashboard_state(prices)
            return

        # ─── Position Exit ───
        pos = self.wallet.get_position_for_symbol(symbol)
        if pos:
            owner = next(
                (strategy for strategy in strategies
                 if strategy.name == pos.strategy_name),
                None,
            )
            if owner is None:
                logger.error(
                    f"[BOT] Position {pos.id} has no matching strategy owner; "
                    "leaving it open for manual review."
                )
            elif owner.should_exit(pos.side):
                volatility = self._estimate_volatility(owner)
                order_size_ratio = pos.quantity / max(candle.volume, 1e-12)
                trade = self.order_engine.close_position(
                        symbol=symbol,
                        position_id=pos.id,
                        current_price=candle.close,
                        volatility=volatility,
                        order_size_ratio=order_size_ratio,
                    )
                if trade:
                    self.live_guardian.record_trade_result(trade.get('net_pnl', 0.0))
                    self.execution_tracker.record_fill(
                        fill_id=f"exit_{trade['id']}",
                        order_id=trade['id'],
                        symbol=symbol,
                        side='sell' if pos.side == 'long' else 'buy',
                        exec_price=trade.get('exit_price', candle.close),
                        exec_qty=trade.get('quantity', pos.quantity),
                        fee=trade.get('total_fees', 0.0),
                    )
                    self.persistence.save_wallet_state(self.wallet)
                owner.discard_pending_trade()
            self._update_dashboard_state(prices)
            return  # do not enter and exit on the same candle

        # ─── Strategy Entry ───
        for strategy in strategies:
            signal = strategy.should_enter()
            if signal is None:
                continue
            if signal == 'short' and not self.allow_short:
                logger.info(f"[BOT] Ignoring short signal from {strategy.name}: spot mode")
                strategy.discard_pending_trade()
                continue
            if signal not in {'long', 'short'}:
                strategy.discard_pending_trade()
                continue

            if self.duplicate_filter.is_duplicate_signal(strategy.name, symbol, candle.timestamp, signal):
                strategy.discard_pending_trade()
                continue

            if self.sentiment.should_block_trade():
                logger.info(
                    f"[BOT] Entry BLOCKED by sentiment filter "
                    f"({self.sentiment._overall_label}: "
                    f"{self.sentiment._overall_score:+.3f})"
                )
                strategy.discard_pending_trade()
                break

            plan = strategy.get_trade_plan()
            if not plan or plan.get('stop_loss') is None:
                logger.warning(f"[BOT] {strategy.name} emitted a signal without a stop; rejected")
                strategy.discard_pending_trade()
                continue

            kelly_pct = self.kelly.get_position_size_pct(
                self._strategy_trades(symbol, strategy.name)
            )
            if kelly_pct <= 0:
                logger.warning(f"[BOT] {strategy.name} has no positive Kelly edge; entry skipped")
                strategy.discard_pending_trade()
                continue

            volatility = self._estimate_volatility(strategy)
            entry_side = 'buy' if signal == 'long' else 'sell'
            quote = self.fee_model.total_cost(
                candle.close, 1.0, entry_side, volatility,
                use_jitter=False,
            )
            sizing = self.risk_manager.calculate_position_quantity(
                kelly_pct,
                self.wallet.total_equity(prices),
                quote['execution_price'],
                plan['stop_loss'],
                self.config.get('risk', {}).get('max_notional_pct', 100),
            )
            quantity = sizing['quantity']
            quantity = min(
                quantity,
                self.wallet.cash / (
                    quote['execution_price'] * (1 + self.fee_model.taker_fee)
                ),
            )
            order_size_ratio = quantity / max(candle.volume, 1e-12)

            if quantity * quote['execution_price'] <= 10:
                logger.info(f"[BOT] {strategy.name} position below $10 minimum; skipped")
                strategy.discard_pending_trade()
                continue

            # External Live Risk Guardian Check
            is_safe, reason = self.live_guardian.validate_order(
                symbol=symbol,
                side=signal,
                quantity=quantity,
                price=quote['execution_price'],
                equity=self.wallet.total_equity(prices),
                open_positions=list(self.wallet.positions.values()),
            )
            if not is_safe:
                logger.warning(f"[BOT] Order blocked by Risk Guardian: {reason}")
                self.execution_tracker.record_signal(
                    strategy_name=strategy.name,
                    symbol=symbol,
                    side=signal,
                    accepted=False,
                    reason=reason,
                )
                strategy.discard_pending_trade()
                continue

            order_kwargs = {
                'symbol': symbol,
                'quantity': quantity,
                'current_price': candle.close,
                'volatility': volatility,
                'order_size_ratio': order_size_ratio,
                'strategy_name': strategy.name,
                'stop_loss': plan['stop_loss'],
                'take_profit': plan.get('take_profit'),
            }
            if signal == 'long':
                position = self.order_engine.market_buy(**order_kwargs)
            else:
                position = self.order_engine.market_short(**order_kwargs)
            if position is None:
                strategy.discard_pending_trade()
            else:
                self.execution_tracker.record_signal(
                    strategy_name=strategy.name,
                    symbol=symbol,
                    side=signal,
                    accepted=True,
                )
                self.execution_tracker.record_order(
                    order_id=position.id,
                    symbol=symbol,
                    side=signal,
                    quantity=position.quantity,
                    price=position.entry_price,
                    status='FILLED',
                )
                self.execution_tracker.record_fill(
                    fill_id=f"fill_{position.id}",
                    order_id=position.id,
                    symbol=symbol,
                    side=signal,
                    exec_price=position.entry_price,
                    exec_qty=position.quantity,
                    fee=position.fee_paid,
                )
                self.persistence.save_wallet_state(self.wallet)
            break  # one position per symbol

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

        # ─── Step 2: Verify every configured market ───
        logger.info("[STEP 2] Fetching live prices to verify connection...")
        symbols = self.config['symbols']
        try:
            values = await asyncio.gather(
                *(self.market_feed.get_current_price(symbol) for symbol in symbols)
            )
            self.bot_state['prices'] = dict(zip(symbols, values))
            for symbol, price in self.bot_state['prices'].items():
                logger.info(f"[STEP 2] ✅ Live {symbol} price: ${price:,.2f}")
        except Exception as e:
            logger.error(f"[STEP 2] ❌ Could not fetch live price: {e}")
            logger.error("  Check your internet connection and try again.")
            return

        # ─── Step 3: Run Backtests ───
        await self.run_backtests()

        # ─── Step 4: Start Live Trading ───
        logger.info("\n" + "=" * 60)
        logger.info("  STEP 4: STARTING LIVE PAPER TRADING")
        logger.info(f"  Streaming {', '.join(symbols)} via WebSocket...")
        logger.info(
            "  Active strategies: " + str({
                symbol: [strategy.name for strategy in strategies]
                for symbol, strategies in self.active_strategies.items()
            })
        )
        logger.info("=" * 60 + "\n")

        # ─── Step 3.5: Initial News Fetch ───
        logger.info("[STEP 3.5] Fetching initial news...")
        try:
            news_items = await self.news_feed.fetch_news()
            self.sentiment.analyze_news(news_items)
            logger.info(f"[STEP 3.5] ✅ {len(news_items)} headlines loaded")
        except Exception as e:
            logger.warning(f"[STEP 3.5] News fetch failed (non-fatal): {e}")
        self._update_dashboard_state()

        # Push updates to dashboard periodically
        async def push_dashboard_updates():
            while self.running:
                try:
                    self.socketio.emit('state_update', self.bot_state)
                except Exception:
                    pass
                await asyncio.sleep(3)

        # Fetch news periodically in the background
        async def fetch_news_periodically():
            while self.running:
                await asyncio.sleep(self.news_feed.fetch_interval)
                try:
                    news_items = await self.news_feed.fetch_news()
                    self.sentiment.analyze_news(news_items)
                    self._update_dashboard_state()
                except Exception as e:
                    logger.warning(f"[NEWS] Background fetch error: {e}")

        # Run WebSocket stream, dashboard updates, and news concurrently
        update_task = asyncio.create_task(push_dashboard_updates())
        news_task = asyncio.create_task(fetch_news_periodically())

        try:
            # Main stream loop — one live stream per configured symbol, all
            # restarted together when the selected timeframe changes.
            while self.running:
                self._pending_timeframe = None
                current_tf = self.config.get('timeframe', '1m')
                logger.info(f"[WS] Starting streams: {symbols} @ {current_tf}")

                # Re-enable market feed for new stream
                self.market_feed._running = True

                await asyncio.gather(*(
                    self.market_feed.stream_live(
                        symbol=symbol,
                        interval=current_tf,
                        on_candle=lambda candle, current_symbol=symbol:
                            self._on_candle(current_symbol, candle),
                    )
                    for symbol in symbols
                )
                )

                # If we get here, stream_live exited.
                # Check if it was a timeframe change or a real stop
                if self._pending_timeframe:
                    logger.info(f"[BOT] Reconnecting with {self._pending_timeframe} timeframe...")
                    if self._pending_revalidation:
                        self._pending_revalidation = False
                        logger.info("[BOT] Revalidating strategies for the new timeframe...")
                        await self.run_backtests()
                    await asyncio.sleep(1)  # brief pause before reconnect
                    continue
                else:
                    break  # real stop requested

        except KeyboardInterrupt:
            logger.info("\n[BOT] Shutting down gracefully...")
        finally:
            self.running = False
            update_task.cancel()
            news_task.cancel()
            if self._revalidation_task:
                self._revalidation_task.cancel()
            with suppress(asyncio.CancelledError):
                await update_task
            with suppress(asyncio.CancelledError):
                await news_task
            self.persistence.save_wallet_state(self.wallet)
            self._print_final_report()

    def _print_final_report(self):
        """Print an honest final performance report."""
        prices = self.bot_state.get('prices', {})
        stats = self.wallet.get_stats(prices)

        logger.info("\n" + "=" * 60)
        logger.info("  📊 FINAL HONEST REPORT")
        logger.info("=" * 60)
        logger.info(f"  Starting Balance:   ${stats['starting_balance']:,.2f} (FAKE)")
        logger.info(f"  Current Cash:       ${stats['current_cash']:,.2f}")
        logger.info(f"  Current Equity:     ${stats['current_equity']:,.2f}")
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

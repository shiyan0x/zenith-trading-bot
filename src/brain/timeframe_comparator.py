"""
timeframe_comparator.py — Multi-timeframe performance comparison engine.

Runs isolated backtests on each requested timeframe and produces a
side-by-side comparison report with key metrics.

Honesty rules:
- Each timeframe is tested independently with the same strategy config.
- Results are never cherry-picked. If a timeframe fails, it shows as failed.
- Non-native intervals (e.g. 10m) use the CandleAggregator for safe data.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.core.market_feed import MarketFeed, Candle
from src.core.fee_model import FeeModel
from src.core.candle_aggregator import (
    needs_aggregation, get_source_interval,
    aggregate_historical_candles, INTERVAL_SECONDS,
)
from src.brain.backtester import Backtester, BacktestResult
from src.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class TimeframeComparisonResult:
    """Holds comparison results across multiple timeframes for a strategy/symbol."""

    def __init__(self, strategy_name: str, symbol: str):
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.results: dict[str, dict] = {}  # timeframe -> backtest result dict

    def add_result(self, timeframe: str, bt_result: dict):
        """Add a single timeframe's backtest result."""
        self.results[timeframe] = bt_result

    def add_error(self, timeframe: str, error: str):
        """Record an error for a timeframe that failed."""
        self.results[timeframe] = {
            'strategy': self.strategy_name,
            'symbol': self.symbol,
            'timeframe': timeframe,
            'error': error,
            'passed': False,
        }

    def get_comparison_table(self) -> list[dict]:
        """
        Build a comparison table for the dashboard.
        Each row = one timeframe with all its metrics.
        """
        rows = []
        for tf, result in self.results.items():
            if 'error' in result:
                rows.append({
                    'timeframe': tf,
                    'status': 'error',
                    'error': result['error'],
                    'total_trades': 0,
                    'win_rate': 0,
                    'total_return_pct': 0,
                    'max_drawdown_pct': 0,
                    'sharpe_ratio': 0,
                    'profit_factor': 0,
                    'passed': False,
                })
                continue

            test = result.get('test')
            if test is None:
                continue

            if isinstance(test, BacktestResult):
                rows.append({
                    'timeframe': tf,
                    'status': 'completed',
                    'total_trades': test.total_trades,
                    'win_rate': round(test.win_rate * 100, 1),
                    'total_return_pct': round(test.total_return_pct, 2),
                    'max_drawdown_pct': round(test.max_drawdown_pct, 2),
                    'sharpe_ratio': round(test.sharpe_ratio, 2),
                    'profit_factor': round(test.profit_factor, 2),
                    'avg_win': round(test.avg_win, 2),
                    'avg_loss': round(test.avg_loss, 2),
                    'passed': test.passed(),
                })
            elif isinstance(test, dict):
                rows.append({
                    'timeframe': tf,
                    'status': 'completed',
                    **test,
                })

        # Sort: passed first, then by Sharpe descending
        rows.sort(key=lambda r: (
            not r.get('passed', False),
            -(r.get('sharpe_ratio', 0) or 0),
        ))
        return rows

    def best_timeframe(self) -> Optional[str]:
        """Return the timeframe with the highest test Sharpe, if any passed."""
        best_tf = None
        best_sharpe = -float('inf')
        for tf, result in self.results.items():
            if 'error' in result:
                continue
            test = result.get('test')
            if test is None:
                continue
            sharpe = test.sharpe_ratio if isinstance(test, BacktestResult) else test.get('sharpe_ratio', 0)
            passed = test.passed() if isinstance(test, BacktestResult) else test.get('passed', False)
            if passed and sharpe > best_sharpe:
                best_sharpe = sharpe
                best_tf = tf
        return best_tf

    def to_dict(self) -> dict:
        return {
            'strategy': self.strategy_name,
            'symbol': self.symbol,
            'timeframes': self.get_comparison_table(),
            'best_timeframe': self.best_timeframe(),
        }


class TimeframeComparator:
    """
    Runs a strategy through multiple timeframes and compares performance.

    Usage:
        comparator = TimeframeComparator(config, market_feed, fee_model)
        result = await comparator.compare(
            strategy_factory=EmaVwapRsiStrategy,
            strategy_params={...},
            symbol='BTCUSDT',
            timeframes=['5m', '10m', '15m', '1h', '4h'],
            days=90,
        )
    """

    REQUESTED_TIMEFRAMES = ['5m', '10m', '15m', '1h', '4h']

    def __init__(self, config: dict, market_feed: MarketFeed, fee_model: FeeModel):
        self.config = config
        self.market_feed = market_feed
        self.fee_model = fee_model

    async def _fetch_candles_for_timeframe(
        self, symbol: str, timeframe: str, days: int
    ) -> list[Candle]:
        """
        Fetch candles for a given timeframe. For non-native intervals,
        fetches the source interval and aggregates.
        """
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = int(
            (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
        )

        if needs_aggregation(timeframe):
            source_interval = get_source_interval(timeframe)
            logger.info(
                f"[TF-COMPARE] Fetching {source_interval} candles for "
                f"{timeframe} aggregation ({symbol}, {days}d)"
            )
            source_candles = await self.market_feed.get_all_historical_klines(
                symbol=symbol,
                interval=source_interval,
                start_time=start_time,
                end_time=end_time,
            )
            return aggregate_historical_candles(source_candles, timeframe)
        else:
            return await self.market_feed.get_all_historical_klines(
                symbol=symbol,
                interval=timeframe,
                start_time=start_time,
                end_time=end_time,
            )

    async def compare(
        self,
        strategy_factory,
        strategy_params: dict,
        symbol: str,
        timeframes: list[str] = None,
        days: int = 90,
    ) -> TimeframeComparisonResult:
        """
        Run the same strategy across multiple timeframes and compare.

        Args:
            strategy_factory: Strategy class (e.g. EmaVwapRsiStrategy)
            strategy_params: Strategy config dict
            symbol: Trading pair (e.g. 'BTCUSDT')
            timeframes: List of intervals to test (default: REQUESTED_TIMEFRAMES)
            days: Historical data lookback

        Returns:
            TimeframeComparisonResult with side-by-side metrics
        """
        timeframes = timeframes or self.REQUESTED_TIMEFRAMES
        strategy = strategy_factory(dict(strategy_params))
        comparison = TimeframeComparisonResult(strategy.name, symbol)

        backtester = Backtester(self.config, self.market_feed, self.fee_model)

        for tf in timeframes:
            logger.info(f"[TF-COMPARE] Testing {strategy.name} on {symbol} @ {tf}...")
            try:
                candles = await self._fetch_candles_for_timeframe(symbol, tf, days)

                if len(candles) < 100:
                    comparison.add_error(
                        tf, f"Insufficient data: only {len(candles)} candles"
                    )
                    continue

                # Calculate periods_per_year for Sharpe
                tf_seconds = INTERVAL_SECONDS.get(tf, 900)
                periods_per_year = int((365 * 24 * 3600) / tf_seconds)

                # Walk-forward split
                train_ratio = self.config.get('backtest', {}).get('train_ratio', 0.7)
                split_idx = int(len(candles) * train_ratio)
                train_candles = candles[:split_idx]
                test_candles = candles[split_idx:]

                # Fresh strategy instance per timeframe
                strat_instance = strategy_factory(dict(strategy_params))

                train_result = backtester._run_on_candles(
                    strat_instance, train_candles, 10000.0,
                    f"Train ({tf})", periods_per_year,
                )

                strat_instance_test = strategy_factory(dict(strategy_params))
                test_result = backtester._run_on_candles(
                    strat_instance_test, test_candles, 10000.0,
                    f"Test ({tf})", periods_per_year,
                )

                comparison.add_result(tf, {
                    'strategy': strat_instance.name,
                    'symbol': symbol,
                    'timeframe': tf,
                    'train': train_result,
                    'test': test_result,
                    'passed': test_result.passed(
                        backtester.min_sharpe, backtester.min_trades
                    ),
                })

                logger.info(
                    f"[TF-COMPARE] {tf}: "
                    f"Sharpe={test_result.sharpe_ratio:.2f}, "
                    f"Return={test_result.total_return_pct:+.2f}%, "
                    f"Trades={test_result.total_trades}, "
                    f"{'PASSED' if test_result.passed() else 'FAILED'}"
                )

            except Exception as e:
                logger.error(f"[TF-COMPARE] Error on {tf}: {e}")
                comparison.add_error(tf, str(e))

        best = comparison.best_timeframe()
        if best:
            logger.info(f"[TF-COMPARE] Best timeframe for {strategy.name} on {symbol}: {best}")
        else:
            logger.info(f"[TF-COMPARE] No timeframe passed for {strategy.name} on {symbol}")

        return comparison

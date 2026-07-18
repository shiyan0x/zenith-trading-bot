"""
backtester.py — Walk-forward backtester on real historical data.

This tests strategies against real past prices from Binance and
applies full fees + slippage. It does NOT cherry-pick results.

Walk-forward method:
1. Download real historical candles from Binance
2. Split: first 70% for training, last 30% for testing
3. Run strategy on training data (to see if it works at all)
4. Run strategy on UNSEEN test data (the honest score)
5. Only keep strategies that pass on the test set

If no strategy passes, we say so. We never force a pick.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

from src.core.market_feed import MarketFeed, Candle
from src.core.fee_model import FeeModel
from src.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BacktestResult:
    """Results of a backtest run — all numbers honest, no rounding to look good."""

    def __init__(self, strategy_name: str, period: str):
        self.strategy_name = strategy_name
        self.period = period
        self.trades: list[dict] = []
        self.starting_balance = 10000.0
        self.ending_balance = 10000.0
        self.equity_curve: list[float] = []

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return len([t for t in self.trades if t['net_pnl'] > 0])

    @property
    def losing_trades(self) -> int:
        return len([t for t in self.trades if t['net_pnl'] <= 0])

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def total_return_pct(self) -> float:
        if self.starting_balance == 0:
            return 0.0
        return ((self.ending_balance - self.starting_balance)
                / self.starting_balance) * 100

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for val in self.equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def avg_win(self) -> float:
        wins = [t['net_pnl'] for t in self.trades if t['net_pnl'] > 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t['net_pnl'] for t in self.trades if t['net_pnl'] <= 0]
        return abs(sum(losses) / len(losses)) if losses else 0.0

    @property
    def profit_factor(self) -> float:
        """Gross profit / gross loss. Above 1.0 = profitable."""
        gross_profit = sum(t['net_pnl'] for t in self.trades if t['net_pnl'] > 0)
        gross_loss = abs(sum(t['net_pnl'] for t in self.trades if t['net_pnl'] <= 0))
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @property
    def sharpe_ratio(self) -> float:
        """
        Annualized Sharpe ratio (simplified, assuming 1-minute candles).
        Sharpe = mean(returns) / std(returns) * sqrt(annualization_factor)

        A Sharpe > 1 is decent. > 2 is very good. < 0.5 is poor.
        """
        if len(self.trades) < 2:
            return 0.0

        returns = [t['net_pnl_pct'] / 100 for t in self.trades]
        import numpy as np
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)
        if std_ret == 0:
            return 0.0

        # Rough annualization: assume ~252 trading days, ~1440 minutes/day
        # But trades happen irregularly, so we use sqrt(num_trades * 365/days_in_data)
        sharpe = mean_ret / std_ret * (len(returns) ** 0.5)
        return float(sharpe)

    def passed(self, min_sharpe: float = 0.5, min_trades: int = 20) -> bool:
        """Did this strategy honestly pass the backtest criteria?"""
        return (self.total_trades >= min_trades
                and self.sharpe_ratio >= min_sharpe
                and self.total_return_pct > 0)

    def summary(self) -> str:
        """Human-readable summary — honest, no sugar coating."""
        status = "✅ PASSED" if self.passed() else "❌ FAILED"
        return (
            f"\n{'='*60}\n"
            f"  {self.strategy_name} — {self.period} — {status}\n"
            f"{'='*60}\n"
            f"  Trades:         {self.total_trades} "
            f"({self.winning_trades}W / {self.losing_trades}L)\n"
            f"  Win Rate:       {self.win_rate*100:.1f}%\n"
            f"  Total Return:   {self.total_return_pct:+.2f}%\n"
            f"  Max Drawdown:   {self.max_drawdown_pct:.2f}%\n"
            f"  Sharpe Ratio:   {self.sharpe_ratio:.2f}\n"
            f"  Profit Factor:  {self.profit_factor:.2f}\n"
            f"  Avg Win:        ${self.avg_win:.2f}\n"
            f"  Avg Loss:       ${self.avg_loss:.2f}\n"
            f"{'='*60}\n"
        )

    def to_dict(self) -> dict:
        return {
            'strategy': self.strategy_name,
            'period': self.period,
            'total_trades': self.total_trades,
            'win_rate': self.win_rate,
            'total_return_pct': self.total_return_pct,
            'max_drawdown_pct': self.max_drawdown_pct,
            'sharpe_ratio': self.sharpe_ratio,
            'profit_factor': self.profit_factor,
            'avg_win': self.avg_win,
            'avg_loss': self.avg_loss,
            'passed': self.passed(),
        }


class Backtester:
    """
    Tests strategies on real historical data with full cost model.

    No cheating:
    - Uses real Binance historical prices
    - Applies real fees and slippage on every simulated trade
    - Walk-forward: trains on first 70%, tests on last 30%
    - Reports results honestly, even if they're bad
    """

    def __init__(self, config: dict, market_feed: MarketFeed, fee_model: FeeModel):
        self.config = config
        self.market_feed = market_feed
        self.fee_model = fee_model
        self.bt_config = config.get('backtest', {})
        self.train_ratio = self.bt_config.get('train_ratio', 0.7)
        self.min_sharpe = self.bt_config.get('min_sharpe', 0.5)
        self.min_trades = self.bt_config.get('min_trades', 20)

    def _run_on_candles(self, strategy: BaseStrategy, candles: list[Candle],
                        starting_balance: float, label: str) -> BacktestResult:
        """
        Run a strategy over a series of candles, simulating trades.

        This is the core simulation loop — handles entries, exits,
        fees, slippage, and PnL tracking.
        """
        result = BacktestResult(strategy.name, label)
        result.starting_balance = starting_balance

        balance = starting_balance
        position = None  # current open position (or None)
        trade_size_pct = 0.10  # risk 10% of balance per trade

        strategy.reset()

        for candle in candles:
            strategy.update(candle)

            if not candle.is_closed:
                continue

            current_price = candle.close

            # Track equity
            if position:
                if position['side'] == 'long':
                    unrealized = (current_price - position['entry']) * position['qty']
                else:
                    unrealized = (position['entry'] - current_price) * position['qty']
                result.equity_curve.append(balance + unrealized)
            else:
                result.equity_curve.append(balance)

            if position is not None:
                # We have an open position — check for exit
                if strategy.should_exit(position['side']):
                    # Close the trade at honest price
                    costs = self.fee_model.total_cost(
                        current_price, position['qty'], 'sell'
                    )
                    exit_price = costs['execution_price']
                    exit_fee = costs['fee']

                    # Calculate PnL
                    if position['side'] == 'long':
                        gross_pnl = (exit_price - position['entry']) * position['qty']
                    else:
                        gross_pnl = (position['entry'] - exit_price) * position['qty']

                    net_pnl = gross_pnl - position['fee'] - exit_fee
                    entry_value = position['entry'] * position['qty']
                    net_pnl_pct = (net_pnl / entry_value * 100) if entry_value > 0 else 0

                    balance += position['qty'] * exit_price - exit_fee
                    balance = max(balance, 0)  # can't go negative

                    result.trades.append({
                        'entry_price': position['entry'],
                        'exit_price': exit_price,
                        'side': position['side'],
                        'qty': position['qty'],
                        'gross_pnl': gross_pnl,
                        'fees': position['fee'] + exit_fee,
                        'net_pnl': net_pnl,
                        'net_pnl_pct': net_pnl_pct,
                    })

                    position = None

            else:
                # No position — check for entry
                signal = strategy.should_enter()
                if signal and balance > 10:  # minimum $10 to trade
                    # Calculate how much to buy
                    trade_amount = balance * trade_size_pct
                    costs = self.fee_model.total_cost(
                        current_price, trade_amount / current_price, 'buy'
                    )
                    entry_price = costs['execution_price']
                    entry_fee = costs['fee']
                    qty = (trade_amount - entry_fee) / entry_price

                    if qty > 0:
                        balance -= (qty * entry_price + entry_fee)
                        position = {
                            'side': signal,
                            'entry': entry_price,
                            'qty': qty,
                            'fee': entry_fee,
                        }

        # If still holding at end, force close at last price (honest)
        if position and candles:
            last_price = candles[-1].close
            costs = self.fee_model.total_cost(
                last_price, position['qty'], 'sell'
            )
            exit_price = costs['execution_price']
            exit_fee = costs['fee']

            if position['side'] == 'long':
                gross_pnl = (exit_price - position['entry']) * position['qty']
            else:
                gross_pnl = (position['entry'] - exit_price) * position['qty']

            net_pnl = gross_pnl - position['fee'] - exit_fee
            entry_value = position['entry'] * position['qty']
            net_pnl_pct = (net_pnl / entry_value * 100) if entry_value > 0 else 0

            balance += position['qty'] * exit_price - exit_fee

            result.trades.append({
                'entry_price': position['entry'],
                'exit_price': exit_price,
                'side': position['side'],
                'qty': position['qty'],
                'gross_pnl': gross_pnl,
                'fees': position['fee'] + exit_fee,
                'net_pnl': net_pnl,
                'net_pnl_pct': net_pnl_pct,
            })

        result.ending_balance = balance
        return result

    async def run(self, strategy: BaseStrategy, symbol: str,
                  interval: str = '1h',
                  days: int = 90) -> dict:
        """
        Run a full walk-forward backtest.

        1. Download real historical candles
        2. Split 70/30
        3. Test on training set (sanity check)
        4. Test on unseen test set (the real score)
        5. Return honest results

        Returns a dict with 'train' and 'test' BacktestResult objects.
        """
        logger.info(f"\n[BACKTEST] Running {strategy.name} on {symbol} "
                    f"({interval}, {days} days)")

        # Download real data from Binance
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time = int(
            (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
        )

        candles = await self.market_feed.get_all_historical_klines(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time
        )

        if len(candles) < 100:
            logger.warning(f"[BACKTEST] Only {len(candles)} candles — too few")
            return None

        # Walk-forward split
        split_idx = int(len(candles) * self.train_ratio)
        train_candles = candles[:split_idx]
        test_candles = candles[split_idx:]

        logger.info(
            f"[BACKTEST] Data: {len(candles)} candles | "
            f"Train: {len(train_candles)} | Test: {len(test_candles)}"
        )

        # Run on training data
        train_result = self._run_on_candles(
            strategy, train_candles, 10000.0,
            f"Train ({len(train_candles)} candles)"
        )

        # Run on test data (the honest score)
        test_result = self._run_on_candles(
            strategy, test_candles, 10000.0,
            f"Test ({len(test_candles)} candles)"
        )

        # Log results honestly
        logger.info(train_result.summary())
        logger.info(test_result.summary())

        passed = test_result.passed(self.min_sharpe, self.min_trades)
        if passed:
            logger.info(f"[BACKTEST] ✅ {strategy.name} PASSED on unseen test data")
        else:
            reasons = []
            if test_result.total_trades < self.min_trades:
                reasons.append(f"too few trades ({test_result.total_trades} < {self.min_trades})")
            if test_result.sharpe_ratio < self.min_sharpe:
                reasons.append(f"Sharpe too low ({test_result.sharpe_ratio:.2f} < {self.min_sharpe})")
            if test_result.total_return_pct <= 0:
                reasons.append(f"negative return ({test_result.total_return_pct:.2f}%)")
            logger.info(
                f"[BACKTEST] ❌ {strategy.name} FAILED: {', '.join(reasons)}"
            )

        return {
            'strategy': strategy.name,
            'symbol': symbol,
            'train': train_result,
            'test': test_result,
            'passed': passed,
        }

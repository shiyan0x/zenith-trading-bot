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

    def __init__(self, strategy_name: str, period: str,
                 periods_per_year: int = 1):
        self.strategy_name = strategy_name
        self.period = period
        self.trades: list[dict] = []
        self.starting_balance = 10000.0
        self.ending_balance = 10000.0
        self.equity_curve: list[float] = []
        self.periods_per_year = periods_per_year

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
        Annualized Sharpe ratio from per-candle equity returns.

        A Sharpe > 1 is decent. > 2 is very good. < 0.5 is poor.
        """
        if len(self.equity_curve) < 3:
            return 0.0

        import numpy as np
        equity = np.asarray(self.equity_curve, dtype=float)
        prior = equity[:-1]
        returns = np.diff(equity) / np.where(prior == 0, np.nan, prior)
        returns = returns[np.isfinite(returns)]
        if len(returns) < 2:
            return 0.0
        mean_ret = np.mean(returns)
        std_ret = np.std(returns, ddof=1)
        if std_ret == 0:
            return 0.0

        sharpe = mean_ret / std_ret * (self.periods_per_year ** 0.5)
        return float(sharpe)

    def passed(self, min_sharpe: float = 0.5, min_trades: int = 20) -> bool:
        """Did this strategy honestly pass the backtest criteria?"""
        return (self.total_trades >= min_trades
                and self.sharpe_ratio >= min_sharpe
                and self.total_return_pct > 0)

    def summary(self, min_sharpe: float = 0.5, min_trades: int = 20) -> str:
        """Human-readable summary — honest, no sugar coating."""
        status = "✅ PASSED" if self.passed(min_sharpe, min_trades) else "❌ FAILED"
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

    def to_dict(self, min_sharpe: float = 0.5, min_trades: int = 20) -> dict:
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
            'passed': self.passed(min_sharpe, min_trades),
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
                        starting_balance: float, label: str,
                        periods_per_year: int = 1) -> BacktestResult:
        """
        Run a strategy over a series of candles, simulating trades.

        This is the core simulation loop — handles entries, exits,
        fees, slippage, and PnL tracking.
        """
        result = BacktestResult(strategy.name, label, periods_per_year)
        result.starting_balance = starting_balance

        cash = starting_balance
        position = None
        risk_pct = self.bt_config.get(
            'risk_per_trade_pct',
            self.config.get('kelly', {}).get('bootstrap_risk_pct', 1.0),
        )
        max_notional_pct = self.config.get('risk', {}).get('max_notional_pct', 100)
        allow_short = (
            self.config.get('trading', {}).get('allow_short', False)
            and self.fee_model.mode == 'futures'
        )

        def equity(mark_price: float) -> float:
            if position is None:
                return cash
            if position['side'] == 'long':
                return cash + position['qty'] * mark_price
            return cash + position['collateral'] + (
                position['entry'] - mark_price
            ) * position['qty']

        def close_position(mark_price: float, reason: str):
            nonlocal cash, position
            exit_side = 'sell' if position['side'] == 'long' else 'buy'
            costs = self.fee_model.total_cost(
                mark_price, position['qty'], exit_side, use_jitter=False
            )
            exit_price = costs['execution_price']
            exit_fee = costs['fee']
            gross_pnl = (
                (exit_price - position['entry']) * position['qty']
                if position['side'] == 'long'
                else (position['entry'] - exit_price) * position['qty']
            )
            net_pnl = gross_pnl - position['fee'] - exit_fee
            entry_value = position['entry'] * position['qty']
            if position['side'] == 'long':
                cash += position['qty'] * exit_price - exit_fee
            else:
                cash += position['collateral'] + (
                    position['entry'] - exit_price
                ) * position['qty'] - exit_fee
            result.trades.append({
                'entry_price': position['entry'],
                'exit_price': exit_price,
                'side': position['side'],
                'qty': position['qty'],
                'gross_pnl': gross_pnl,
                'fees': position['fee'] + exit_fee,
                'net_pnl': net_pnl,
                'net_pnl_pct': (net_pnl / entry_value * 100) if entry_value else 0.0,
                'exit_reason': reason,
            })
            position = None

        strategy.reset()
        for candle in candles:
            if not candle.is_closed:
                continue
            strategy.update(candle)
            current_price = candle.close

            if position is not None:
                if strategy.should_exit(position['side']):
                    close_position(current_price, 'strategy_exit')
                result.equity_curve.append(equity(current_price))
                continue

            signal = strategy.should_enter()
            if signal not in {'long', 'short'} or (signal == 'short' and not allow_short):
                strategy.discard_pending_trade()
                result.equity_curve.append(cash)
                continue

            plan = strategy.get_trade_plan()
            stop_loss = plan.get('stop_loss') if plan else None
            if stop_loss is None:
                strategy.discard_pending_trade()
                result.equity_curve.append(cash)
                continue

            entry_side = 'buy' if signal == 'long' else 'sell'
            quote = self.fee_model.total_cost(
                current_price, 1.0, entry_side, use_jitter=False
            )
            unit_risk = abs(quote['execution_price'] - stop_loss)
            if unit_risk <= 0:
                strategy.discard_pending_trade()
                result.equity_curve.append(cash)
                continue

            current_equity = equity(current_price)
            risk_amount = current_equity * max(risk_pct, 0) / 100
            qty = risk_amount / unit_risk
            max_notional = current_equity * max(max_notional_pct, 0) / 100
            qty = min(qty, max_notional / quote['execution_price'])
            # A 1x position must be fully collateralized including entry fees.
            qty = min(
                qty,
                cash / (quote['execution_price'] * (1 + self.fee_model.taker_fee)),
            )
            if qty * quote['execution_price'] <= 10:
                strategy.discard_pending_trade()
                result.equity_curve.append(cash)
                continue

            costs = self.fee_model.total_cost(
                current_price, qty, entry_side, use_jitter=False
            )
            entry_price = costs['execution_price']
            entry_fee = costs['fee']
            collateral = entry_price * qty if signal == 'short' else 0.0
            if signal == 'long':
                cash -= entry_price * qty + entry_fee
            else:
                cash -= collateral + entry_fee
            position = {
                'side': signal,
                'entry': entry_price,
                'qty': qty,
                'fee': entry_fee,
                'collateral': collateral,
            }
            result.equity_curve.append(equity(current_price))

        if position and candles:
            close_position(candles[-1].close, 'end_of_data')
            result.equity_curve.append(cash)

        result.ending_balance = cash
        return result

    @staticmethod
    def _periods_per_year(interval: str) -> int:
        """Approximate number of crypto bars per calendar year."""
        seconds_by_interval = {
            '1m': 60, '3m': 180, '5m': 300, '10m': 600, '15m': 900, '30m': 1800,
            '1h': 3600, '2h': 7200, '4h': 14400, '1d': 86400,
        }
        seconds = seconds_by_interval.get(interval)
        if seconds is None:
            raise ValueError(f"Unsupported backtest interval: {interval}")
        return int((365 * 24 * 60 * 60) / seconds)

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
        periods_per_year = self._periods_per_year(interval)

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
            f"Train ({len(train_candles)} candles)", periods_per_year
        )

        # Run on test data (the honest score)
        test_result = self._run_on_candles(
            strategy, test_candles, 10000.0,
            f"Test ({len(test_candles)} candles)", periods_per_year
        )

        # Log results honestly
        logger.info(train_result.summary(self.min_sharpe, self.min_trades))
        logger.info(test_result.summary(self.min_sharpe, self.min_trades))

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

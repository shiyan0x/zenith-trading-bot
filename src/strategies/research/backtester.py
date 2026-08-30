"""
Backtesting Engine
===================
Simulates trades from strategy signals and calculates performance metrics.

Supports:
- Long and short trades
- ATR-based stop-loss / take-profit
- Commission and slippage costs
- Time-stop (for mean reversion)
- Per-trade logging
- Portfolio equity curve
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional

from config import (
    INITIAL_CAPITAL, COMMISSION_PCT, SLIPPAGE_PCT,
    SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD,
)
from utils import setup_logger, format_pct, format_currency

logger = setup_logger("Backtester")


@dataclass
class Trade:
    """Represents a single completed trade."""
    entry_time: str
    exit_time: str
    direction: str        # BUY or SELL
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    position_size: int
    pnl: float
    pnl_pct: float
    exit_reason: str      # TP_HIT, SL_HIT, SIGNAL_EXIT, TIME_STOP
    bars_held: int


@dataclass
class BacktestResult:
    """Complete backtest results with metrics and trade log."""
    strategy_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_return_pct: float
    profit_factor: float
    max_drawdown_pct: float
    avg_win: float
    avg_loss: float
    avg_rr_achieved: float
    sharpe_ratio: float
    avg_bars_held: float
    initial_capital: float
    final_capital: float
    equity_curve: List[float] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    
    def summary(self) -> str:
        """Human-readable summary of backtest results."""
        lines = [
            f"\n{'=' * 60}",
            f"  BACKTEST RESULTS: {self.strategy_name}",
            f"{'=' * 60}",
            f"  Capital:        {format_currency(self.initial_capital)} → {format_currency(self.final_capital)}",
            f"  Total Return:   {format_pct(self.total_return_pct)}",
            f"  Total PnL:      {format_currency(self.total_pnl)}",
            f"{'─' * 60}",
            f"  Total Trades:   {self.total_trades}",
            f"  Winners:        {self.winning_trades}",
            f"  Losers:         {self.losing_trades}",
            f"  Win Rate:       {format_pct(self.win_rate)}",
            f"{'─' * 60}",
            f"  Avg Win:        {format_currency(self.avg_win)}",
            f"  Avg Loss:       {format_currency(self.avg_loss)}",
            f"  Avg R:R:        1:{self.avg_rr_achieved:.2f}",
            f"  Profit Factor:  {self.profit_factor:.2f}",
            f"{'─' * 60}",
            f"  Max Drawdown:   {format_pct(self.max_drawdown_pct)}",
            f"  Sharpe Ratio:   {self.sharpe_ratio:.2f}",
            f"  Avg Bars Held:  {self.avg_bars_held:.1f}",
            f"{'=' * 60}",
        ]
        return "\n".join(lines)


class Backtester:
    """
    Event-driven backtesting engine.
    
    Walks through each bar chronologically, manages open positions,
    and tracks performance metrics.
    
    Usage:
        bt = Backtester(initial_capital=10000)
        result = bt.run(strategy_df, strategy_name="EMA+VWAP+RSI")
        print(result.summary())
    """
    
    def __init__(
        self,
        initial_capital: float = INITIAL_CAPITAL,
        commission_pct: float = COMMISSION_PCT,
        slippage_pct: float = SLIPPAGE_PCT,
        risk_per_trade_pct: float = 1.0,
    ):
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct / 100.0
        self.slippage_pct = slippage_pct / 100.0
        self.risk_per_trade_pct = risk_per_trade_pct / 100.0
    
    def run(
        self,
        df: pd.DataFrame,
        strategy_name: str = "Strategy",
        use_time_stop: bool = False,
    ) -> BacktestResult:
        """
        Run backtest on a DataFrame that already has signals.
        
        Expected columns: close, signal, stop_loss, take_profit
        Optional: time_stop_bar, high, low
        
        Args:
            df: DataFrame with signals from a strategy
            strategy_name: Name for logging
            use_time_stop: Whether to use time-stop exits
        
        Returns:
            BacktestResult with all metrics
        """
        logger.info(f"Running backtest: {strategy_name}")
        logger.info(f"  Capital: {format_currency(self.initial_capital)} | "
                     f"Commission: {self.commission_pct * 100:.2f}% | "
                     f"Slippage: {self.slippage_pct * 100:.2f}%")
        
        capital = self.initial_capital
        equity_curve = [capital]
        trades: List[Trade] = []
        
        # Position state
        in_position = False
        entry_price = 0.0
        entry_time = ""
        direction = ""
        stop_loss = 0.0
        take_profit = 0.0
        position_size = 0
        entry_bar_idx = 0
        time_stop_idx = None
        
        has_hl = "high" in df.columns and "low" in df.columns
        
        for i in range(len(df)):
            row = df.iloc[i]
            bar_time = str(df.index[i])
            close = row["close"]
            high = row.get("high", close)
            low = row.get("low", close)
            
            if in_position:
                # ===== CHECK EXIT CONDITIONS =====
                exit_price = None
                exit_reason = None
                
                if direction == SIGNAL_BUY:
                    # Check stop-loss (did price go below SL?)
                    if has_hl and low <= stop_loss:
                        exit_price = stop_loss
                        exit_reason = "SL_HIT"
                    # Check take-profit (did price reach TP?)
                    elif has_hl and high >= take_profit:
                        exit_price = take_profit
                        exit_reason = "TP_HIT"
                    # Check opposite signal
                    elif row["signal"] == SIGNAL_SELL:
                        exit_price = close
                        exit_reason = "SIGNAL_EXIT"
                        
                elif direction == SIGNAL_SELL:
                    if has_hl and high >= stop_loss:
                        exit_price = stop_loss
                        exit_reason = "SL_HIT"
                    elif has_hl and low <= take_profit:
                        exit_price = take_profit
                        exit_reason = "TP_HIT"
                    elif row["signal"] == SIGNAL_BUY:
                        exit_price = close
                        exit_reason = "SIGNAL_EXIT"
                
                # Time stop check
                if use_time_stop and exit_price is None and time_stop_idx is not None:
                    if i >= time_stop_idx:
                        exit_price = close
                        exit_reason = "TIME_STOP"
                
                # ===== EXECUTE EXIT =====
                if exit_price is not None:
                    # Apply slippage (unfavorable)
                    if direction == SIGNAL_BUY:
                        exit_price *= (1 - self.slippage_pct)
                    else:
                        exit_price *= (1 + self.slippage_pct)
                    
                    # Calculate PnL
                    if direction == SIGNAL_BUY:
                        raw_pnl = (exit_price - entry_price) * position_size
                    else:
                        raw_pnl = (entry_price - exit_price) * position_size
                    
                    # Deduct commission (exit side)
                    commission = exit_price * position_size * self.commission_pct
                    net_pnl = raw_pnl - commission
                    pnl_pct = net_pnl / (entry_price * position_size) if position_size > 0 else 0
                    
                    bars_held = i - entry_bar_idx
                    
                    trade = Trade(
                        entry_time=entry_time,
                        exit_time=bar_time,
                        direction=direction,
                        entry_price=round(entry_price, 2),
                        exit_price=round(exit_price, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        position_size=position_size,
                        pnl=round(net_pnl, 2),
                        pnl_pct=round(pnl_pct, 4),
                        exit_reason=exit_reason,
                        bars_held=bars_held,
                    )
                    trades.append(trade)
                    
                    capital += net_pnl
                    in_position = False
                    
                    # Don't enter a new trade on the same bar as exit
                    equity_curve.append(capital)
                    continue
            
            if not in_position:
                # ===== CHECK ENTRY CONDITIONS =====
                signal = row["signal"]
                
                if signal in (SIGNAL_BUY, SIGNAL_SELL):
                    sl = row.get("stop_loss", np.nan)
                    tp = row.get("take_profit", np.nan)
                    
                    if pd.isna(sl) or pd.isna(tp):
                        equity_curve.append(capital)
                        continue
                    
                    # Apply slippage to entry (unfavorable)
                    if signal == SIGNAL_BUY:
                        entry_price = close * (1 + self.slippage_pct)
                    else:
                        entry_price = close * (1 - self.slippage_pct)
                    
                    # Position sizing based on risk
                    risk_amount = capital * self.risk_per_trade_pct
                    price_risk = abs(entry_price - sl)
                    
                    if price_risk <= 0:
                        equity_curve.append(capital)
                        continue
                    
                    position_size = int(risk_amount / price_risk)
                    
                    if position_size <= 0:
                        equity_curve.append(capital)
                        continue
                    
                    # Deduct entry commission
                    commission = entry_price * position_size * self.commission_pct
                    capital -= commission
                    
                    # Set position state
                    in_position = True
                    direction = signal
                    stop_loss = sl
                    take_profit = tp
                    entry_time = bar_time
                    entry_bar_idx = i
                    
                    # Time stop
                    if use_time_stop and "time_stop_bar" in df.columns:
                        tsb = row.get("time_stop_bar", np.nan)
                        time_stop_idx = int(tsb) if not pd.isna(tsb) else None
                    else:
                        time_stop_idx = None
            
            equity_curve.append(capital)
        
        # Close any remaining open position at last close
        if in_position:
            last_close = df.iloc[-1]["close"]
            if direction == SIGNAL_BUY:
                raw_pnl = (last_close - entry_price) * position_size
            else:
                raw_pnl = (entry_price - last_close) * position_size
            commission = last_close * position_size * self.commission_pct
            net_pnl = raw_pnl - commission
            
            trade = Trade(
                entry_time=entry_time,
                exit_time=str(df.index[-1]),
                direction=direction,
                entry_price=round(entry_price, 2),
                exit_price=round(last_close, 2),
                stop_loss=round(stop_loss, 2),
                take_profit=round(take_profit, 2),
                position_size=position_size,
                pnl=round(net_pnl, 2),
                pnl_pct=round(net_pnl / (entry_price * position_size) if position_size > 0 else 0, 4),
                exit_reason="END_OF_DATA",
                bars_held=len(df) - 1 - entry_bar_idx,
            )
            trades.append(trade)
            capital += net_pnl
        
        # ================================================================
        # CALCULATE METRICS
        # ================================================================
        
        result = self._calculate_metrics(
            strategy_name=strategy_name,
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            final_capital=capital,
        )
        
        logger.info(f"Backtest complete: {result.total_trades} trades, "
                     f"Win Rate: {format_pct(result.win_rate)}, "
                     f"PnL: {format_currency(result.total_pnl)}")
        
        return result
    
    def _calculate_metrics(
        self,
        strategy_name: str,
        trades: List[Trade],
        equity_curve: List[float],
        initial_capital: float,
        final_capital: float,
    ) -> BacktestResult:
        """Calculate performance metrics from trade log."""
        
        if not trades:
            return BacktestResult(
                strategy_name=strategy_name,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_pnl=0.0,
                total_return_pct=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                avg_rr_achieved=0.0,
                sharpe_ratio=0.0,
                avg_bars_held=0.0,
                initial_capital=initial_capital,
                final_capital=final_capital,
                equity_curve=equity_curve,
                trades=trades,
            )
        
        pnls = [t.pnl for t in trades]
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl <= 0]
        bars = [t.bars_held for t in trades]
        
        total_trades = len(trades)
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        
        total_pnl = sum(pnls)
        total_return = total_pnl / initial_capital
        
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0001  # Avoid div by zero
        profit_factor = gross_profit / gross_loss
        
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        
        avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
        
        # Max drawdown
        equity = np.array(equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        max_dd = abs(drawdown.min()) if len(drawdown) > 0 else 0.0
        
        # Sharpe ratio (annualized, assuming 15-min bars)
        returns = np.diff(equity) / equity[:-1]
        if len(returns) > 1 and np.std(returns) > 0:
            # ~26 bars per day × 252 trading days
            annualization = np.sqrt(26 * 252)
            sharpe = (np.mean(returns) / np.std(returns)) * annualization
        else:
            sharpe = 0.0
        
        avg_bars = np.mean(bars) if bars else 0.0
        
        return BacktestResult(
            strategy_name=strategy_name,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=round(total_pnl, 2),
            total_return_pct=round(total_return, 4),
            profit_factor=round(profit_factor, 2),
            max_drawdown_pct=round(max_dd, 4),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            avg_rr_achieved=round(avg_rr, 2),
            sharpe_ratio=round(sharpe, 2),
            avg_bars_held=round(avg_bars, 1),
            initial_capital=initial_capital,
            final_capital=round(final_capital, 2),
            equity_curve=equity_curve,
            trades=trades,
        )

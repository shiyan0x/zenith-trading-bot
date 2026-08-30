"""
Trading Strategy Backtest Runner
==================================
Runs both strategies on sample data and compares performance.

Usage:
    python run_backtest.py
    python run_backtest.py --data path/to/your_data.csv
    python run_backtest.py --bars 3000 --capital 50000
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np

# Windows terminals often default to cp1252, while this CLI intentionally
# prints symbols and emoji in its reports. Avoid crashing a completed
# backtest merely while rendering its output.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Ensure the module directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import INITIAL_CAPITAL
from strategy_ema_vwap_rsi import EmaVwapRsiStrategy
from strategy_mean_reversion import MeanReversionStrategy
from backtester import Backtester
from utils import generate_sample_data, print_header, format_pct, format_currency, setup_logger

logger = setup_logger("BacktestRunner")


def load_data(filepath: str = None, n_bars: int = 2000) -> pd.DataFrame:
    """
    Load OHLCV data from CSV or generate synthetic data.
    
    CSV format expected:
        datetime, open, high, low, close, volume
    
    Args:
        filepath: Path to CSV file (optional)
        n_bars: Number of bars for synthetic data
    
    Returns:
        OHLCV DataFrame with DatetimeIndex
    """
    if filepath and os.path.exists(filepath):
        logger.info(f"Loading data from: {filepath}")
        df = pd.read_csv(filepath)
        
        # Try to find datetime column
        date_cols = [c for c in df.columns if "date" in c.lower() or "time" in c.lower()]
        if date_cols:
            df[date_cols[0]] = pd.to_datetime(df[date_cols[0]])
            df.set_index(date_cols[0], inplace=True)
        
        df.columns = [c.lower().strip() for c in df.columns]
        logger.info(f"Loaded {len(df)} bars from {filepath}")
        return df
    else:
        logger.info(f"Generating {n_bars} bars of synthetic 15-min data...")
        df = generate_sample_data(n_bars=n_bars)
        logger.info(f"Generated {len(df)} bars (price range: "
                     f"{df['close'].min():.2f} - {df['close'].max():.2f})")
        return df


def print_comparison(result1, result2):
    """Print side-by-side comparison of two strategy results."""
    print_header("STRATEGY COMPARISON")
    
    metrics = [
        ("Total Trades", result1.total_trades, result2.total_trades),
        ("Win Rate", format_pct(result1.win_rate), format_pct(result2.win_rate)),
        ("Total PnL", format_currency(result1.total_pnl), format_currency(result2.total_pnl)),
        ("Total Return", format_pct(result1.total_return_pct), format_pct(result2.total_return_pct)),
        ("Profit Factor", f"{result1.profit_factor:.2f}", f"{result2.profit_factor:.2f}"),
        ("Max Drawdown", format_pct(result1.max_drawdown_pct), format_pct(result2.max_drawdown_pct)),
        ("Sharpe Ratio", f"{result1.sharpe_ratio:.2f}", f"{result2.sharpe_ratio:.2f}"),
        ("Avg Win", format_currency(result1.avg_win), format_currency(result2.avg_win)),
        ("Avg Loss", format_currency(result1.avg_loss), format_currency(result2.avg_loss)),
        ("Avg R:R", f"1:{result1.avg_rr_achieved:.2f}", f"1:{result2.avg_rr_achieved:.2f}"),
        ("Avg Bars Held", f"{result1.avg_bars_held:.1f}", f"{result2.avg_bars_held:.1f}"),
        ("Final Capital", format_currency(result1.final_capital), format_currency(result2.final_capital)),
    ]
    
    # Calculate column widths
    col1_name = result1.strategy_name[:25]
    col2_name = result2.strategy_name[:25]
    
    header = f"  {'Metric':<18} │ {col1_name:>25} │ {col2_name:>25}"
    separator = f"  {'─' * 18}─┼─{'─' * 25}─┼─{'─' * 25}"
    
    print(header)
    print(separator)
    
    for name, v1, v2 in metrics:
        print(f"  {name:<18} │ {str(v1):>25} │ {str(v2):>25}")
    
    print(f"\n{'=' * 75}")
    
    # Winner declaration
    print("\n  📊 VERDICT:")
    
    if result1.win_rate > result2.win_rate:
        print(f"    🏆 Higher Win Rate:    {result1.strategy_name} ({format_pct(result1.win_rate)})")
    else:
        print(f"    🏆 Higher Win Rate:    {result2.strategy_name} ({format_pct(result2.win_rate)})")
    
    if result1.profit_factor > result2.profit_factor:
        print(f"    🏆 Higher Profit Factor: {result1.strategy_name} ({result1.profit_factor:.2f})")
    else:
        print(f"    🏆 Higher Profit Factor: {result2.strategy_name} ({result2.profit_factor:.2f})")
    
    if result1.total_pnl > result2.total_pnl:
        print(f"    🏆 More Profitable:    {result1.strategy_name} ({format_currency(result1.total_pnl)})")
    else:
        print(f"    🏆 More Profitable:    {result2.strategy_name} ({format_currency(result2.total_pnl)})")
    
    if result1.max_drawdown_pct < result2.max_drawdown_pct:
        print(f"    🏆 Lower Drawdown:     {result1.strategy_name} ({format_pct(result1.max_drawdown_pct)})")
    else:
        print(f"    🏆 Lower Drawdown:     {result2.strategy_name} ({format_pct(result2.max_drawdown_pct)})")
    
    print()


def print_trade_log(result, max_trades: int = 10):
    """Print the last N trades from a backtest result."""
    if not result.trades:
        print(f"  No trades for {result.strategy_name}")
        return
    
    print_header(f"RECENT TRADES: {result.strategy_name}")
    
    trades = result.trades[-max_trades:]
    
    header = f"  {'#':>3} │ {'Direction':>5} │ {'Entry':>10} │ {'Exit':>10} │ {'PnL':>12} │ {'Bars':>4} │ {'Exit Reason':<12}"
    print(header)
    print(f"  {'─' * 3}─┼─{'─' * 5}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 12}─┼─{'─' * 4}─┼─{'─' * 12}")
    
    start_idx = len(result.trades) - len(trades)
    for i, t in enumerate(trades, start=start_idx + 1):
        pnl_str = format_currency(t.pnl)
        dir_icon = "🟢" if t.direction == "BUY" else "🔴"
        pnl_icon = "✅" if t.pnl > 0 else "❌"
        print(f"  {i:>3} │ {dir_icon} {t.direction:>3} │ {t.entry_price:>10.2f} │ {t.exit_price:>10.2f} │ {pnl_icon} {pnl_str:>9} │ {t.bars_held:>4} │ {t.exit_reason:<12}")
    
    print()


def save_results(result, filepath: str):
    """Save trade log to CSV."""
    if not result.trades:
        return
    
    rows = []
    for t in result.trades:
        rows.append({
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_loss": t.stop_loss,
            "take_profit": t.take_profit,
            "position_size": t.position_size,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "exit_reason": t.exit_reason,
            "bars_held": t.bars_held,
        })
    
    df = pd.DataFrame(rows)
    df.to_csv(filepath, index=False)
    logger.info(f"Trade log saved to: {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description="Run backtests on both trading strategies"
    )
    parser.add_argument(
        "--data", type=str, default=None,
        help="Path to OHLCV CSV file (optional, generates synthetic data if not provided)"
    )
    parser.add_argument(
        "--bars", type=int, default=2000,
        help="Number of bars for synthetic data (default: 2000)"
    )
    parser.add_argument(
        "--capital", type=float, default=INITIAL_CAPITAL,
        help=f"Initial capital (default: {INITIAL_CAPITAL})"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save trade logs to CSV files"
    )
    
    args = parser.parse_args()
    
    # ================================================================
    # LOAD DATA
    # ================================================================
    print_header("TRADING STRATEGY BACKTESTER")
    print(f"  Initial Capital: {format_currency(args.capital)}")
    print(f"  Data Source: {'CSV file' if args.data else f'Synthetic ({args.bars} bars)'}")
    
    data = load_data(args.data, args.bars)
    print(f"  Bars Loaded: {len(data)}")
    print(f"  Date Range: {data.index[0]} → {data.index[-1]}")
    
    # ================================================================
    # STRATEGY 1: EMA + VWAP + RSI
    # ================================================================
    print_header("STRATEGY 1: EMA + VWAP + RSI (15-min Trend)")
    
    strategy1 = EmaVwapRsiStrategy()
    df1 = strategy1.generate_signals(data.copy())
    
    bt1 = Backtester(
        initial_capital=args.capital,
        risk_per_trade_pct=1.0,
    )
    result1 = bt1.run(df1, strategy_name=strategy1.name)
    print(result1.summary())
    print_trade_log(result1)
    
    # ================================================================
    # STRATEGY 2: RSI + BOLLINGER BANDS MEAN REVERSION
    # ================================================================
    print_header("STRATEGY 2: RSI + BB Mean Reversion")
    
    strategy2 = MeanReversionStrategy(require_candle_confirmation=False)
    df2 = strategy2.generate_signals(data.copy())
    
    bt2 = Backtester(
        initial_capital=args.capital,
        risk_per_trade_pct=1.0,
    )
    result2 = bt2.run(df2, strategy_name=strategy2.name, use_time_stop=True)
    print(result2.summary())
    print_trade_log(result2)
    
    # ================================================================
    # COMPARISON
    # ================================================================
    print_comparison(result1, result2)
    
    # ================================================================
    # SAVE RESULTS
    # ================================================================
    if args.save:
        save_dir = os.path.dirname(os.path.abspath(__file__))
        save_results(result1, os.path.join(save_dir, "trades_ema_vwap_rsi.csv"))
        save_results(result2, os.path.join(save_dir, "trades_mean_reversion.csv"))
        print(f"  📁 Trade logs saved to {save_dir}")
    
    print("\n  ✅ Backtest complete!\n")


if __name__ == "__main__":
    main()

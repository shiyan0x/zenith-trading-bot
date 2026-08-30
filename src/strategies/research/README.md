# Research / Strategy Development

This folder contains **standalone research files** used to develop and backtest
the two trading strategies before integrating them into the live bot.

## Files

| File | Purpose |
|------|---------|
| `strategy_ema_vwap_rsi.py` | Original DataFrame-based EMA+VWAP+RSI strategy (research version) |
| `strategy_mean_reversion.py` | Original DataFrame-based RSI+BB Mean Reversion strategy (research version) |
| `backtester.py` | Offline backtesting engine — simulates trades from signal DataFrames |
| `risk_manager.py` | Position sizing and risk validation utilities |
| `run_backtest.py` | CLI runner — compare both strategies on real or synthetic data |
| `utils.py` | Helper functions: logging, data generation, formatting |
| `trades_ema_vwap_rsi.csv` | Sample backtest output (EMA+VWAP+RSI) |
| `trades_mean_reversion.csv` | Sample backtest output (Mean Reversion) |

## How to run a backtest

```bash
# From the project root — generates synthetic data
python src/strategies/research/run_backtest.py

# With your own OHLCV CSV
python src/strategies/research/run_backtest.py --data path/to/data.csv

# Custom capital and save results
python src/strategies/research/run_backtest.py --capital 50000 --save
```

## Relationship to the live bot

The **live bot** uses adapter classes in `src/strategies/`:
- `ema_vwap_rsi.py` — wraps the research strategy into the `BaseStrategy` interface
- `mean_reversion.py` — wraps the research strategy into the `BaseStrategy` interface

The live adapters share the same indicator library and reproduce these signal
rules through the live `BaseStrategy` interface. The research runner is useful
for exploring signals, but the production walk-forward backtester is the sole
gate for live paper-trading eligibility.

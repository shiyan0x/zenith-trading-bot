"""
config.py — Research folder config shim.
Re-exports all constants from src/strategy_config.py so the standalone
research scripts (run_backtest.py, backtester.py, etc.) work unchanged.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from src.strategy_config import *

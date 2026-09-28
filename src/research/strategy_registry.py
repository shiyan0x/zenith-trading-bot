"""
strategy_registry.py — Central registry for existing and generated strategies.

Every strategy the bot has ever known is registered here with full
metadata: name, version, parameters, timeframe, entry/exit rules,
creation date, and status.

Status lifecycle:
  legacy    — original hand-coded strategies (EMA/VWAP/RSI, Mean Reversion)
  candidate — AI-generated, awaiting backtest validation
  promoted  — passed validation, eligible for paper trading
  retired   — superseded or consistently failing
"""

import json
import logging
from typing import Optional

from src.research.experiment_store import ExperimentStore

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Central catalog of all known trading strategies.

    Wraps ExperimentStore's strategy table with higher-level methods
    for registration, lookup, and lifecycle management.
    """

    def __init__(self, store: ExperimentStore):
        self.store = store
        self._ensure_legacy_strategies()

    # ── legacy bootstrap ──────────────────────────────────────────────────

    def _ensure_legacy_strategies(self):
        """Register the two original strategies if not already present."""
        existing = {s['name'] for s in self.store.list_strategies()}

        if 'EMA_VWAP_RSI' not in existing:
            self.register_legacy(
                name='EMA_VWAP_RSI',
                blueprint={
                    'strategy_class': 'src.strategies.ema_vwap_rsi.EmaVwapRsiStrategy',
                    'default_params': {
                        'ema_fast': 9, 'ema_slow': 21,
                        'rsi_period': 14, 'rsi_bull_min': 50,
                        'rsi_bull_max': 70, 'rsi_bear_min': 30,
                        'rsi_bear_max': 50, 'atr_period': 14,
                        'atr_sl_mult': 2.0, 'rr_ratio': 2.0,
                        'min_candles': 50,
                    },
                },
                entry_desc=(
                    'Long: EMA(9) > EMA(21), close > VWAP, '
                    '50 < RSI(14) < 70, bullish candle pattern'
                ),
                exit_desc=(
                    'ATR(14)*2 stop-loss, 2:1 R:R take-profit, '
                    'trend-flip exit, 20-bar time stop'
                ),
            )
            logger.info("[REGISTRY] Registered legacy: EMA_VWAP_RSI")

        if 'Mean_Reversion' not in existing:
            self.register_legacy(
                name='Mean_Reversion',
                blueprint={
                    'strategy_class': 'src.strategies.mean_reversion.MeanReversionStrategy',
                    'default_params': {
                        'bb_period': 20, 'bb_std': 2.0,
                        'rsi_period': 14, 'rsi_ob': 70, 'rsi_os': 30,
                        'adx_period': 14, 'adx_threshold': 25,
                        'atr_period': 14, 'atr_sl_mult': 2.0,
                        'time_stop_bars': 15, 'min_candles': 50,
                    },
                },
                entry_desc=(
                    'Long: close < BB_lower, RSI < 30, ADX < 25, '
                    'bullish rejection candle'
                ),
                exit_desc=(
                    'ATR(14)*2 stop-loss, BB_middle take-profit, '
                    '15-bar time stop, RSI > 70 exit'
                ),
            )
            logger.info("[REGISTRY] Registered legacy: Mean_Reversion")

    def register_legacy(self, *, name: str, blueprint: dict,
                        entry_desc: str = '', exit_desc: str = '') -> str:
        return self.store.save_strategy(
            name=name, version=1,
            strategy_type='legacy', blueprint=blueprint,
            entry_desc=entry_desc, exit_desc=exit_desc,
            status='legacy',
        )

    # ── generated strategies ──────────────────────────────────────────────

    def register_generated(self, *, name: str, version: int,
                           blueprint: dict, timeframe: str = '',
                           entry_desc: str = '',
                           exit_desc: str = '') -> str:
        """Register an AI-generated candidate strategy."""
        return self.store.save_strategy(
            name=name, version=version,
            strategy_type='generated', blueprint=blueprint,
            timeframe=timeframe,
            entry_desc=entry_desc, exit_desc=exit_desc,
            status='candidate',
        )

    # ── queries ───────────────────────────────────────────────────────────

    def get(self, strategy_id: str) -> Optional[dict]:
        return self.store.get_strategy(strategy_id)

    def list_all(self) -> list[dict]:
        return self.store.list_strategies()

    def list_by_status(self, status: str) -> list[dict]:
        return self.store.list_strategies(status=status)

    def list_candidates(self) -> list[dict]:
        return self.list_by_status('candidate')

    def list_legacy(self) -> list[dict]:
        return self.list_by_status('legacy')

    # ── lifecycle ─────────────────────────────────────────────────────────

    def promote(self, strategy_id: str):
        """Move a candidate → promoted (passed backtest validation)."""
        self.store.update_strategy_status(strategy_id, 'promoted')
        logger.info(f"[REGISTRY] Strategy {strategy_id} promoted")

    def retire(self, strategy_id: str):
        """Move a strategy → retired (no longer used)."""
        self.store.update_strategy_status(strategy_id, 'retired')
        logger.info(f"[REGISTRY] Strategy {strategy_id} retired")

    def next_version(self, name: str) -> int:
        """Return the next available version number for a strategy name."""
        strategies = self.store.list_strategies()
        versions = [s['version'] for s in strategies if s['name'] == name]
        return max(versions, default=0) + 1

    # ── summary ───────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Counts by status — for the dashboard."""
        all_strats = self.list_all()
        counts = {}
        for s in all_strats:
            status = s.get('status', 'unknown')
            counts[status] = counts.get(status, 0) + 1
        return {
            'total': len(all_strats),
            'counts': counts,
            'strategies': all_strats,
        }

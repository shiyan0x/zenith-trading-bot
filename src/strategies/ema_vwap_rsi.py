"""
ema_vwap_rsi.py — EMA + VWAP + RSI Trend Strategy adapter.

Wraps the research-grade EmaVwapRsiStrategy (DataFrame-based) into
the BaseStrategy interface so the live bot engine can use it directly
via the standard should_enter() / should_exit() API.

Strategy logic (15-minute trend-following):
    LONG:  EMA 9 > EMA 21, Price > VWAP, 50 < RSI < 70
    SHORT: EMA 9 < EMA 21, Price < VWAP, 30 < RSI < 50

Exits:
    - Stop-loss:   2x ATR below/above entry
    - Take-profit: 2:1 R:R above/below entry
"""

import logging
from typing import Optional
from datetime import datetime, timezone

import pandas as pd
import numpy as np

from src.strategies.base_strategy import BaseStrategy
from src.core.market_feed import Candle
from src.strategy_config import (
    EMA_FAST_PERIOD, EMA_SLOW_PERIOD,
    RSI_PERIOD, RSI_BULL_MIN, RSI_BULL_MAX, RSI_BEAR_MIN, RSI_BEAR_MAX,
    ATR_PERIOD, ATR_SL_MULTIPLIER, DEFAULT_RR_RATIO,
    SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD,
    MIN_CANDLES_REQUIRED,
)
from src.indicators import ema, rsi, vwap_session, atr

logger = logging.getLogger(__name__)


class EmaVwapRsiStrategy(BaseStrategy):
    """
    15-Minute EMA + VWAP + RSI Trend Strategy.

    Identifies trending moves using three confluent signals:
    1. EMA crossover (9/21) — trend direction
    2. VWAP — institutional bias filter
    3. RSI — momentum confirmation (not overbought/oversold)

    Supports both long and short entries.
    """

    def __init__(self, params: dict = None):
        params = params or {}
        super().__init__(name='EMA+VWAP+RSI Trend', params=params)

        self.ema_fast = params.get('ema_fast', EMA_FAST_PERIOD)
        self.ema_slow = params.get('ema_slow', EMA_SLOW_PERIOD)
        self.rsi_period = params.get('rsi_period', RSI_PERIOD)
        self.rsi_bull_min = params.get('rsi_bull_min', RSI_BULL_MIN)
        self.rsi_bull_max = params.get('rsi_bull_max', RSI_BULL_MAX)
        self.rsi_bear_min = params.get('rsi_bear_min', RSI_BEAR_MIN)
        self.rsi_bear_max = params.get('rsi_bear_max', RSI_BEAR_MAX)
        self.atr_period = params.get('atr_period', ATR_PERIOD)
        self.atr_sl_mult = params.get('atr_sl_mult', ATR_SL_MULTIPLIER)
        self.rr_ratio = params.get('rr_ratio', DEFAULT_RR_RATIO)
        self.min_candles = params.get('min_candles', MIN_CANDLES_REQUIRED)

        # Active trade tracking
        self._position_side: Optional[str] = None   # 'long' or 'short'
        self._stop_loss: Optional[float] = None
        self._take_profit: Optional[float] = None

        logger.info(
            f"[{self.name}] Initialized — "
            f"EMA {self.ema_fast}/{self.ema_slow} | RSI {self.rsi_period} | "
            f"R:R 1:{self.rr_ratio} | min_candles={self.min_candles}"
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _to_dataframe(self) -> pd.DataFrame:
        """Convert accumulated Candle history to a pandas OHLCV DataFrame."""
        records = [
            {
                'open': c.open,
                'high': c.high,
                'low': c.low,
                'close': c.close,
                'volume': c.volume,
                'timestamp': c.timestamp,
            }
            for c in self._candle_history
        ]
        df = pd.DataFrame(records)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
        df.set_index('datetime', inplace=True)
        return df

    def _compute_latest_signal(self) -> dict:
        """
        Run the indicator stack on the current candle history and return
        a dict with signal details for the latest bar.
        """
        df = self._to_dataframe()

        # Moving averages
        df['ema_fast'] = ema(df['close'], self.ema_fast)
        df['ema_slow'] = ema(df['close'], self.ema_slow)

        # VWAP
        df['vwap'] = vwap_session(df['high'], df['low'], df['close'], df['volume'])

        # RSI
        df['rsi'] = rsi(df['close'], self.rsi_period)

        # ATR
        df['atr'] = atr(df['high'], df['low'], df['close'], self.atr_period)

        latest = df.iloc[-1]

        # Trend conditions
        ema_bull = latest['ema_fast'] > latest['ema_slow']
        ema_bear = latest['ema_fast'] < latest['ema_slow']
        vwap_bull = latest['close'] > latest['vwap']
        vwap_bear = latest['close'] < latest['vwap']
        rsi_val = latest['rsi']
        rsi_bull = self.rsi_bull_min < rsi_val < self.rsi_bull_max
        rsi_bear = self.rsi_bear_min < rsi_val < self.rsi_bear_max

        # Full confluence = signal
        if ema_bull and vwap_bull and rsi_bull:
            signal = SIGNAL_BUY
        elif ema_bear and vwap_bear and rsi_bear:
            signal = SIGNAL_SELL
        else:
            signal = SIGNAL_HOLD

        return {
            'signal': signal,
            'close': latest['close'],
            'atr': latest['atr'],
        }

    # ------------------------------------------------------------------ #
    # BaseStrategy interface
    # ------------------------------------------------------------------ #

    def should_enter(self) -> Optional[str]:
        """
        Return 'long', 'short', or None based on EMA+VWAP+RSI confluence.
        Requires at least min_candles of history.
        """
        if not self.has_enough_data(self.min_candles):
            return None

        try:
            result = self._compute_latest_signal()
        except Exception as e:
            logger.warning(f"[{self.name}] Signal computation error: {e}")
            return None

        signal = result['signal']
        close = result['close']
        atr_val = result['atr']

        if signal == SIGNAL_BUY:
            # Pre-compute stop-loss / take-profit for the pending entry
            sl = close - atr_val * self.atr_sl_mult
            tp = close + (close - sl) * self.rr_ratio
            self._stop_loss = round(sl, 6)
            self._take_profit = round(tp, 6)
            self._position_side = 'long'
            logger.info(
                f"[{self.name}] LONG signal | price={close:.4f} "
                f"SL={self._stop_loss:.4f} TP={self._take_profit:.4f}"
            )
            return 'long'

        elif signal == SIGNAL_SELL:
            sl = close + atr_val * self.atr_sl_mult
            tp = close - (sl - close) * self.rr_ratio
            self._stop_loss = round(sl, 6)
            self._take_profit = round(tp, 6)
            self._position_side = 'short'
            logger.info(
                f"[{self.name}] SHORT signal | price={close:.4f} "
                f"SL={self._stop_loss:.4f} TP={self._take_profit:.4f}"
            )
            return 'short'

        return None

    def should_exit(self, position_side: str) -> bool:
        """
        Exit when the latest close hits the stop-loss or take-profit level.
        Also exits if the trend flips against the position.
        """
        if not self.has_enough_data(self.min_candles):
            return False

        if not self._candle_history:
            return False

        current_price = self._candle_history[-1].close

        # Stop-loss / take-profit check
        if position_side == 'long':
            if self._stop_loss is not None and current_price <= self._stop_loss:
                logger.info(
                    f"[{self.name}] LONG exit — SL hit @ {current_price:.4f} "
                    f"(SL={self._stop_loss:.4f})"
                )
                self._reset_trade()
                return True
            if self._take_profit is not None and current_price >= self._take_profit:
                logger.info(
                    f"[{self.name}] LONG exit — TP hit @ {current_price:.4f} "
                    f"(TP={self._take_profit:.4f})"
                )
                self._reset_trade()
                return True

        elif position_side == 'short':
            if self._stop_loss is not None and current_price >= self._stop_loss:
                logger.info(
                    f"[{self.name}] SHORT exit — SL hit @ {current_price:.4f} "
                    f"(SL={self._stop_loss:.4f})"
                )
                self._reset_trade()
                return True
            if self._take_profit is not None and current_price <= self._take_profit:
                logger.info(
                    f"[{self.name}] SHORT exit — TP hit @ {current_price:.4f} "
                    f"(TP={self._take_profit:.4f})"
                )
                self._reset_trade()
                return True

        # Trend-flip exit (EMA crossover reversal)
        try:
            result = self._compute_latest_signal()
            if position_side == 'long' and result['signal'] == SIGNAL_SELL:
                logger.info(f"[{self.name}] LONG exit — trend flipped to SELL")
                self._reset_trade()
                return True
            if position_side == 'short' and result['signal'] == SIGNAL_BUY:
                logger.info(f"[{self.name}] SHORT exit — trend flipped to BUY")
                self._reset_trade()
                return True
        except Exception:
            pass

        return False

    def _reset_trade(self):
        """Clear active trade state after exit."""
        self._position_side = None
        self._stop_loss = None
        self._take_profit = None

    def get_trade_plan(self) -> Optional[dict]:
        if self._position_side is None or self._stop_loss is None:
            return None
        return {
            'side': self._position_side,
            'stop_loss': self._stop_loss,
            'take_profit': self._take_profit,
        }

    def discard_pending_trade(self):
        self._reset_trade()

    def reset(self):
        """Full reset — called on timeframe change or new backtest run."""
        super().reset()
        self._reset_trade()

"""
mean_reversion.py — RSI + Bollinger Bands Mean Reversion Strategy adapter.

Wraps the research-grade MeanReversionStrategy (DataFrame-based) into
the BaseStrategy interface so the live bot engine can use it via the
standard should_enter() / should_exit() API.

Strategy logic (ranging market fade):
    LONG:  ADX < 25 (ranging), Price <= Lower BB, RSI < 30, bullish candle
    SHORT: ADX < 25 (ranging), Price >= Upper BB, RSI > 70, bearish candle

Exits:
    - Take-profit: Middle Bollinger Band (20 SMA)
    - Stop-loss:   2x ATR beyond entry bar extreme
    - Time stop:   Close after N bars if no reversion
"""

import logging
from typing import Optional

import pandas as pd
import numpy as np

from src.strategies.base_strategy import BaseStrategy
from src.core.market_feed import Candle
from src.strategy_config import (
    BB_PERIOD, BB_STD_DEV,
    MR_RSI_PERIOD, MR_RSI_OVERBOUGHT, MR_RSI_OVERSOLD,
    ADX_PERIOD, ADX_TREND_THRESHOLD,
    ATR_PERIOD, ATR_SL_MULTIPLIER, MR_TIME_STOP_BARS,
    SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD,
    MIN_CANDLES_REQUIRED,
)
from src.indicators import (
    rsi, bollinger_bands, adx, atr,
    detect_bullish_rejection, detect_bearish_rejection,
    detect_bullish_engulfing, detect_bearish_engulfing,
    detect_rsi_divergence,
)

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """
    RSI + Bollinger Bands Mean Reversion Strategy.

    Designed for RANGING markets (ADX < 25). Enters when price
    reaches Bollinger Band extremes with RSI confirmation,
    targeting mean reversion to the middle band.

    Supports both long and short entries.
    """

    def __init__(self, params: dict = None):
        params = params or {}
        super().__init__(name='RSI+BB Mean Reversion', params=params)

        self.bb_period = params.get('bb_period', BB_PERIOD)
        self.bb_std = params.get('bb_std', BB_STD_DEV)
        self.rsi_period = params.get('rsi_period', MR_RSI_PERIOD)
        self.rsi_ob = params.get('rsi_ob', MR_RSI_OVERBOUGHT)
        self.rsi_os = params.get('rsi_os', MR_RSI_OVERSOLD)
        self.adx_period = params.get('adx_period', ADX_PERIOD)
        self.adx_threshold = params.get('adx_threshold', ADX_TREND_THRESHOLD)
        self.atr_period = params.get('atr_period', ATR_PERIOD)
        self.atr_sl_mult = params.get('atr_sl_mult', ATR_SL_MULTIPLIER)
        self.time_stop_bars = params.get('time_stop_bars', MR_TIME_STOP_BARS)
        self.require_candle = params.get('require_candle_confirmation', True)
        self.min_candles = params.get('min_candles', MIN_CANDLES_REQUIRED)

        # Active trade tracking
        self._position_side: Optional[str] = None   # 'long' or 'short'
        self._stop_loss: Optional[float] = None
        self._take_profit: Optional[float] = None
        self._bars_in_trade: int = 0

        logger.info(
            f"[{self.name}] Initialized — "
            f"BB {self.bb_period}/{self.bb_std}σ | RSI {self.rsi_period} | "
            f"ADX threshold {self.adx_threshold} | "
            f"time stop {self.time_stop_bars} bars | "
            f"min_candles={self.min_candles}"
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
        Run the full indicator stack and return a dict with signal details
        for the latest bar.
        """
        df = self._to_dataframe()

        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = bollinger_bands(
            df['close'], self.bb_period, self.bb_std
        )
        df['bb_upper'] = bb_upper
        df['bb_middle'] = bb_middle
        df['bb_lower'] = bb_lower

        # RSI
        df['rsi'] = rsi(df['close'], self.rsi_period)

        # ADX — trend strength filter
        df['adx'] = adx(df['high'], df['low'], df['close'], self.adx_period)
        df['is_ranging'] = df['adx'] < self.adx_threshold

        # ATR — for stop-loss sizing
        df['atr'] = atr(df['high'], df['low'], df['close'], self.atr_period)

        # Candlestick patterns
        df['bull_candle'] = (
            detect_bullish_rejection(df['open'], df['high'], df['low'], df['close']) |
            detect_bullish_engulfing(df['open'], df['close'])
        )
        df['bear_candle'] = (
            detect_bearish_rejection(df['open'], df['high'], df['low'], df['close']) |
            detect_bearish_engulfing(df['open'], df['close'])
        )

        # RSI divergence for bonus conviction
        rsi_bull_div, rsi_bear_div = detect_rsi_divergence(
            df['close'], df['rsi'], lookback=10
        )
        df['rsi_bull_div'] = rsi_bull_div
        df['rsi_bear_div'] = rsi_bear_div

        latest = df.iloc[-1]

        # Core conditions
        is_ranging = bool(latest['is_ranging'])
        at_lower_bb = latest['close'] <= latest['bb_lower']
        at_upper_bb = latest['close'] >= latest['bb_upper']
        rsi_oversold = latest['rsi'] < self.rsi_os
        rsi_overbought = latest['rsi'] > self.rsi_ob
        bull_candle = bool(latest['bull_candle'])
        bear_candle = bool(latest['bear_candle'])
        rsi_bull_div_val = bool(latest['rsi_bull_div'])
        rsi_bear_div_val = bool(latest['rsi_bear_div'])

        core_buy = is_ranging and at_lower_bb and rsi_oversold
        core_sell = is_ranging and at_upper_bb and rsi_overbought

        if self.require_candle:
            buy_signal = (core_buy and bull_candle) or (core_buy and rsi_bull_div_val)
            sell_signal = (core_sell and bear_candle) or (core_sell and rsi_bear_div_val)
        else:
            buy_signal = core_buy
            sell_signal = core_sell

        if buy_signal:
            signal = SIGNAL_BUY
        elif sell_signal:
            signal = SIGNAL_SELL
        else:
            signal = SIGNAL_HOLD

        return {
            'signal': signal,
            'close': latest['close'],
            'bb_middle': latest['bb_middle'],
            'atr': latest['atr'],
            'is_ranging': is_ranging,
            'adx': latest['adx'],
        }

    # ------------------------------------------------------------------ #
    # BaseStrategy interface
    # ------------------------------------------------------------------ #

    def update(self, candle: Candle):
        """Override to count bars in trade for time-stop logic."""
        super().update(candle)
        if self._position_side is not None:
            self._bars_in_trade += 1

    def should_enter(self) -> Optional[str]:
        """
        Return 'long', 'short', or None based on RSI+BB mean reversion confluence.
        Only trades when ADX confirms a ranging market.
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
        bb_mid = result['bb_middle']
        atr_val = result['atr']

        if signal == SIGNAL_BUY:
            sl = close - atr_val * self.atr_sl_mult
            tp = bb_mid   # target = mean (middle BB)
            self._stop_loss = round(sl, 6)
            self._take_profit = round(tp, 6)
            self._position_side = 'long'
            self._bars_in_trade = 0
            logger.info(
                f"[{self.name}] LONG signal | price={close:.4f} "
                f"ADX={result['adx']:.1f} (ranging) "
                f"SL={self._stop_loss:.4f} TP={self._take_profit:.4f}"
            )
            return 'long'

        elif signal == SIGNAL_SELL:
            sl = close + atr_val * self.atr_sl_mult
            tp = bb_mid   # target = mean (middle BB)
            self._stop_loss = round(sl, 6)
            self._take_profit = round(tp, 6)
            self._position_side = 'short'
            self._bars_in_trade = 0
            logger.info(
                f"[{self.name}] SHORT signal | price={close:.4f} "
                f"ADX={result['adx']:.1f} (ranging) "
                f"SL={self._stop_loss:.4f} TP={self._take_profit:.4f}"
            )
            return 'short'

        return None

    def should_exit(self, position_side: str) -> bool:
        """
        Exit when:
        - Price hits stop-loss or take-profit
        - Time stop: no reversion within N bars
        - Market regime change: ADX goes trending (ADX >= threshold)
        """
        if not self._candle_history:
            return False

        current_price = self._candle_history[-1].close

        # Time stop — force exit if no reversion within allowed window
        if self._bars_in_trade >= self.time_stop_bars:
            logger.info(
                f"[{self.name}] {position_side.upper()} exit — "
                f"time stop ({self._bars_in_trade} bars) @ {current_price:.4f}"
            )
            self._reset_trade()
            return True

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

        # Regime change exit — market turned trending, abort the mean-reversion trade
        if self.has_enough_data(self.min_candles):
            try:
                result = self._compute_latest_signal()
                if not result['is_ranging']:
                    logger.info(
                        f"[{self.name}] {position_side.upper()} exit — "
                        f"market turned trending (ADX={result['adx']:.1f})"
                    )
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
        self._bars_in_trade = 0

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

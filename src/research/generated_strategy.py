"""
generated_strategy.py — Config-driven strategy that extends BaseStrategy.

SAFETY: This module does NOT use eval(), exec(), or any dynamic code
execution.  Strategy behaviour is determined entirely by a JSON-
serialisable *blueprint* dict that selects from a fixed set of
approved indicators and condition operators.

Blueprint format
-----------------
{
  "name": "EMA_RSI_Trend_v3",
  "indicators": {
      "ema_fast": {"type": "ema", "period": 9},
      "ema_slow": {"type": "ema", "period": 21},
      "rsi":      {"type": "rsi", "period": 14},
      "atr":      {"type": "atr", "period": 14},
      "bb":       {"type": "bollinger_bands", "period": 20, "std_dev": 2.0},
      "macd":     {"type": "macd", "fast": 12, "slow": 26, "signal": 9},
      "adx":      {"type": "adx", "period": 14},
      "vwap":     {"type": "vwap"}
  },
  "entry_long": [
      {"left": "ema_fast",  "op": "above", "right": "ema_slow"},
      {"left": "rsi",       "op": "between", "min": 40, "max": 70},
      {"left": "close",     "op": "above",   "right": "vwap"}
  ],
  "entry_short": [],
  "exit": {
      "stop_loss_atr_mult": 2.0,
      "take_profit_rr":     2.0,
      "time_stop_bars":     20
  },
  "min_candles": 50
}
"""

import logging
from typing import Optional

import pandas as pd
import numpy as np

from src.strategies.base_strategy import BaseStrategy
from src.core.market_feed import Candle
from src import indicators as ind

logger = logging.getLogger(__name__)


# ── Approved indicator types ──────────────────────────────────────────────────
# Every type maps to a callable that receives (df, params) and returns a dict
# of column_name → pd.Series.  No indicator outside this dict can be used.

def _compute_ema(df: pd.DataFrame, params: dict) -> dict:
    period = int(params.get('period', 20))
    return {'value': ind.ema(df['close'], period)}


def _compute_sma(df: pd.DataFrame, params: dict) -> dict:
    period = int(params.get('period', 20))
    return {'value': ind.sma(df['close'], period)}


def _compute_rsi(df: pd.DataFrame, params: dict) -> dict:
    period = int(params.get('period', 14))
    return {'value': ind.rsi(df['close'], period)}


def _compute_bollinger(df: pd.DataFrame, params: dict) -> dict:
    period = int(params.get('period', 20))
    std_dev = float(params.get('std_dev', 2.0))
    upper, middle, lower = ind.bollinger_bands(df['close'], period, std_dev)
    return {'upper': upper, 'middle': middle, 'lower': lower}


def _compute_atr(df: pd.DataFrame, params: dict) -> dict:
    period = int(params.get('period', 14))
    return {'value': ind.atr(df['high'], df['low'], df['close'], period)}


def _compute_adx(df: pd.DataFrame, params: dict) -> dict:
    period = int(params.get('period', 14))
    return {'value': ind.adx(df['high'], df['low'], df['close'], period)}


def _compute_vwap(df: pd.DataFrame, params: dict) -> dict:
    return {'value': ind.vwap(df['high'], df['low'], df['close'], df['volume'])}


def _compute_macd(df: pd.DataFrame, params: dict) -> dict:
    """MACD computed from the existing ema() function — no new dependency."""
    fast = int(params.get('fast', 12))
    slow = int(params.get('slow', 26))
    signal_period = int(params.get('signal', 9))
    ema_fast = ind.ema(df['close'], fast)
    ema_slow = ind.ema(df['close'], slow)
    macd_line = ema_fast - ema_slow
    signal_line = ind.ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return {'line': macd_line, 'signal': signal_line, 'hist': histogram}


def _compute_obv(df: pd.DataFrame, params: dict) -> dict:
    """On-Balance Volume — measures buying/selling pressure."""
    direction = np.sign(df['close'].diff())
    obv = (direction * df['volume']).fillna(0).cumsum()
    return {'value': obv}


def _compute_pattern(df: pd.DataFrame, params: dict) -> dict:
    """Computes chart pattern indicator signals (-1.0 bear, 0.0 none, +1.0 bull)."""
    from src.patterns.pattern_detector import PatternDetector
    from src.patterns.pattern_definitions import PatternDirection, PatternStatus

    min_conf = float(params.get('min_confidence', 0.55))
    target_pattern = params.get('pattern_name')
    require_confirmed = bool(params.get('require_confirmed', True))
    detector = PatternDetector(min_confidence=min_conf)

    n = len(df)
    signal_series = pd.Series(0.0, index=df.index, dtype=float)
    conf_series = pd.Series(0.0, index=df.index, dtype=float)

    eval_window = min(60, n)
    sub_df = df.iloc[-eval_window:]
    candles = [
        Candle(
            timestamp=float(row.name if isinstance(row.name, (int, float)) else idx),
            o=float(row['open']), h=float(row['high']), l=float(row['low']),
            c=float(row['close']), volume=float(row['volume']), is_closed=True
        )
        for idx, row in sub_df.iterrows()
    ]

    for offset, loc in [(-2, n - 2), (-1, n - 1)]:
        if len(candles) + offset < 20 or loc < 0:
            continue
        eval_idx = len(candles) + offset
        matches = detector.detect_at_index(candles, eval_idx)
        for m in matches:
            if target_pattern and m.pattern_name != target_pattern:
                continue
            if require_confirmed and m.status != PatternStatus.CONFIRMED:
                continue
            sig = 1.0 if m.direction == PatternDirection.BULLISH else (-1.0 if m.direction == PatternDirection.BEARISH else 0.0)
            signal_series.iloc[loc] = sig
            conf_series.iloc[loc] = m.confidence_score
            break

    return {'signal': signal_series, 'confidence': conf_series}


APPROVED_INDICATORS: dict[str, callable] = {
    'ema':             _compute_ema,
    'sma':             _compute_sma,
    'rsi':             _compute_rsi,
    'bollinger_bands': _compute_bollinger,
    'atr':             _compute_atr,
    'adx':             _compute_adx,
    'vwap':            _compute_vwap,
    'macd':            _compute_macd,
    'obv':             _compute_obv,
    'pattern':         _compute_pattern,
}


# ── Approved condition operators ──────────────────────────────────────────────
# Each takes (current_values_dict, previous_values_dict, condition_dict)
# and returns bool.

def _op_above(curr: dict, prev: dict, cond: dict) -> bool:
    left = curr.get(cond['left'])
    right = cond.get('right')
    if isinstance(right, str):
        right = curr.get(right)
    if left is None or right is None:
        return False
    return float(left) > float(right)


def _op_below(curr: dict, prev: dict, cond: dict) -> bool:
    left = curr.get(cond['left'])
    right = cond.get('right')
    if isinstance(right, str):
        right = curr.get(right)
    if left is None or right is None:
        return False
    return float(left) < float(right)


def _op_between(curr: dict, prev: dict, cond: dict) -> bool:
    left = curr.get(cond['left'])
    if left is None:
        return False
    return float(cond['min']) < float(left) < float(cond['max'])


def _op_cross_above(curr: dict, prev: dict, cond: dict) -> bool:
    left_now = curr.get(cond['left'])
    right_now = cond.get('right')
    if isinstance(right_now, str):
        right_now = curr.get(right_now)
    left_prev = prev.get(cond['left'])
    right_prev = cond.get('right')
    if isinstance(right_prev, str):
        right_prev = prev.get(right_prev)
    if any(v is None for v in (left_now, right_now, left_prev, right_prev)):
        return False
    return (float(left_now) > float(right_now)
            and float(left_prev) <= float(right_prev))


def _op_cross_below(curr: dict, prev: dict, cond: dict) -> bool:
    left_now = curr.get(cond['left'])
    right_now = cond.get('right')
    if isinstance(right_now, str):
        right_now = curr.get(right_now)
    left_prev = prev.get(cond['left'])
    right_prev = cond.get('right')
    if isinstance(right_prev, str):
        right_prev = prev.get(right_prev)
    if any(v is None for v in (left_now, right_now, left_prev, right_prev)):
        return False
    return (float(left_now) < float(right_now)
            and float(left_prev) >= float(right_prev))


APPROVED_OPS: dict[str, callable] = {
    'above':       _op_above,
    'below':       _op_below,
    'between':     _op_between,
    'cross_above': _op_cross_above,
    'cross_below': _op_cross_below,
}


# ── Blueprint validation ─────────────────────────────────────────────────────

def validate_blueprint(bp: dict) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors = []
    if not isinstance(bp, dict):
        return ["Blueprint must be a dict"]
    if 'indicators' not in bp or not bp['indicators']:
        errors.append("Missing 'indicators' section")
    else:
        for name, spec in bp['indicators'].items():
            itype = spec.get('type', '')
            if itype not in APPROVED_INDICATORS:
                errors.append(
                    f"Indicator '{name}' uses unapproved type '{itype}'. "
                    f"Allowed: {list(APPROVED_INDICATORS.keys())}"
                )
    for section in ('entry_long', 'entry_short'):
        for cond in bp.get(section, []):
            op = cond.get('op', '')
            if op not in APPROVED_OPS:
                errors.append(
                    f"Condition in '{section}' uses unapproved op '{op}'. "
                    f"Allowed: {list(APPROVED_OPS.keys())}"
                )
    if 'exit' not in bp:
        errors.append("Missing 'exit' section")
    return errors


# ── The strategy ──────────────────────────────────────────────────────────────

class GeneratedStrategy(BaseStrategy):
    """A trading strategy configured entirely by a blueprint dict.

    - No eval() / exec()
    - Only uses indicators from APPROVED_INDICATORS
    - Only evaluates conditions from APPROVED_OPS
    - Blueprint is JSON-serialisable and fully reproducible
    """

    def __init__(self, blueprint: dict, config: dict = None):
        errors = validate_blueprint(blueprint)
        if errors:
            raise ValueError(
                f"Invalid blueprint: {'; '.join(errors)}"
            )

        name = blueprint.get('name', 'GeneratedStrategy')
        super().__init__(name, config or {})

        self.blueprint = blueprint
        self.min_candles = int(blueprint.get('min_candles', 50))

        # Exit config
        exit_cfg = blueprint.get('exit', {})
        self.sl_atr_mult = float(exit_cfg.get('stop_loss_atr_mult', 2.0))
        self.tp_rr = float(exit_cfg.get('take_profit_rr', 2.0))
        self.time_stop_bars = int(exit_cfg.get('time_stop_bars', 20))

        # Internal state for pending trades
        self._pending_plan: Optional[dict] = None
        self._bars_in_trade = 0

    # ── indicator computation ─────────────────────────────────────────────

    def _compute_indicators(self) -> tuple[dict, dict]:
        """Compute all blueprint indicators and return (current, previous) value dicts.

        Returns flat dicts like:
          {"ema_fast": 50100.5, "rsi": 55.2, "bb_upper": 51000, "close": 50050, ...}
        """
        if len(self._candle_history) < 2:
            return {}, {}

        df = pd.DataFrame({
            'open':   [c.open for c in self._candle_history],
            'high':   [c.high for c in self._candle_history],
            'low':    [c.low for c in self._candle_history],
            'close':  [c.close for c in self._candle_history],
            'volume': [c.volume for c in self._candle_history],
        })

        current: dict[str, float] = {}
        previous: dict[str, float] = {}

        # Price data accessible in conditions
        for col in ('open', 'high', 'low', 'close', 'volume'):
            current[col] = float(df[col].iloc[-1])
            previous[col] = float(df[col].iloc[-2])

        # Compute each indicator
        for ind_name, spec in self.blueprint.get('indicators', {}).items():
            itype = spec.get('type', '')
            compute_fn = APPROVED_INDICATORS.get(itype)
            if compute_fn is None:
                continue
            try:
                series_dict = compute_fn(df, spec)
                for suffix, series in series_dict.items():
                    key = ind_name if suffix == 'value' else f"{ind_name}_{suffix}"
                    val_curr = series.iloc[-1]
                    val_prev = series.iloc[-2]
                    current[key] = float(val_curr) if pd.notna(val_curr) else None
                    previous[key] = float(val_prev) if pd.notna(val_prev) else None
            except Exception:
                # Indicator failed (not enough data, division by zero, etc.)
                pass

        return current, previous

    # ── condition evaluation ──────────────────────────────────────────────

    def _evaluate_conditions(self, conditions: list[dict],
                             curr: dict, prev: dict) -> bool:
        """All conditions must be True (AND logic)."""
        if not conditions:
            return False
        for cond in conditions:
            op_fn = APPROVED_OPS.get(cond.get('op', ''))
            if op_fn is None:
                return False
            if not op_fn(curr, prev, cond):
                return False
        return True

    # ── BaseStrategy interface ────────────────────────────────────────────

    def should_enter(self) -> Optional[str]:
        if not self.has_enough_data(self.min_candles):
            return None

        curr, prev = self._compute_indicators()
        if not curr:
            return None

        # Check long entry
        long_conditions = self.blueprint.get('entry_long', [])
        if long_conditions and self._evaluate_conditions(long_conditions, curr, prev):
            self._prepare_trade_plan(curr, 'long')
            return 'long'

        # Check short entry
        short_conditions = self.blueprint.get('entry_short', [])
        if short_conditions and self._evaluate_conditions(short_conditions, curr, prev):
            self._prepare_trade_plan(curr, 'short')
            return 'short'

        return None

    def _prepare_trade_plan(self, curr: dict, side: str):
        """Compute SL/TP from the exit config and current indicator values."""
        price = curr.get('close', 0)
        if price <= 0:
            self._pending_plan = None
            return

        # Find ATR value for stop-loss computation
        atr_val = None
        for ind_name, spec in self.blueprint.get('indicators', {}).items():
            if spec.get('type') == 'atr':
                atr_val = curr.get(ind_name)
                break
        if atr_val is None or atr_val <= 0:
            # Fallback: 2% of price
            atr_val = price * 0.02

        sl_dist = atr_val * self.sl_atr_mult
        tp_dist = sl_dist * self.tp_rr

        if side == 'long':
            stop_loss = price - sl_dist
            take_profit = price + tp_dist
        else:
            stop_loss = price + sl_dist
            take_profit = price - tp_dist

        self._pending_plan = {
            'side': side,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
        }
        self._bars_in_trade = 0

    def get_trade_plan(self) -> Optional[dict]:
        return self._pending_plan

    def discard_pending_trade(self):
        self._pending_plan = None

    def should_exit(self, position_side: str) -> bool:
        self._bars_in_trade += 1

        if not self.closes:
            return False

        price = self.closes[-1]
        plan = self._pending_plan

        # Time stop
        if self._bars_in_trade >= self.time_stop_bars:
            return True

        # SL / TP check
        if plan:
            sl = plan.get('stop_loss')
            tp = plan.get('take_profit')
            if position_side == 'long':
                if sl and price <= sl:
                    return True
                if tp and price >= tp:
                    return True
            else:
                if sl and price >= sl:
                    return True
                if tp and price <= tp:
                    return True

        return False

    def reset(self):
        super().reset()
        self._pending_plan = None
        self._bars_in_trade = 0

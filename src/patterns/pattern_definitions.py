"""
pattern_definitions.py — Core data structures, enums, and models for chart pattern recognition.

Defines standardized data classes for:
- Pattern types and status (Forming, Confirmed, Invalidated)
- PatternMatch representation with price levels and confidence
- MarketContext representation (regimes, support/resistance, volume, indicators)
- PatternOutcome representation (forward returns, MFE, MAE, execution metrics)
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
import time


class PatternType(str, Enum):
    REVERSAL = "reversal"
    CONTINUATION = "continuation"
    TRIANGLE = "triangle"
    CANDLESTICK = "candlestick"


class PatternStatus(str, Enum):
    FORMING = "forming"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"


class PatternDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# Standardized Pattern Name Constants
DOUBLE_TOP = "Double Top"
DOUBLE_BOTTOM = "Double Bottom"
HEAD_AND_SHOULDERS = "Head and Shoulders"
INVERSE_HEAD_AND_SHOULDERS = "Inverse Head and Shoulders"

BULLISH_FLAG = "Bullish Flag"
BEARISH_FLAG = "Bearish Flag"
BULLISH_PENNANT = "Bullish Pennant"
BEARISH_PENNANT = "Bearish Pennant"

ASCENDING_TRIANGLE = "Ascending Triangle"
DESCENDING_TRIANGLE = "Descending Triangle"
SYMMETRICAL_TRIANGLE = "Symmetrical Triangle"

BULLISH_ENGULFING = "Bullish Engulfing"
BEARISH_ENGULFING = "Bearish Engulfing"
HAMMER = "Hammer"
SHOOTING_STAR = "Shooting Star"
DOJI = "Doji"
MORNING_STAR = "Morning Star"
EVENING_STAR = "Evening Star"

ALL_GEOMETRIC_PATTERNS = {
    DOUBLE_TOP, DOUBLE_BOTTOM, HEAD_AND_SHOULDERS, INVERSE_HEAD_AND_SHOULDERS,
    BULLISH_FLAG, BEARISH_FLAG, BULLISH_PENNANT, BEARISH_PENNANT,
    ASCENDING_TRIANGLE, DESCENDING_TRIANGLE, SYMMETRICAL_TRIANGLE
}

ALL_CANDLESTICK_PATTERNS = {
    BULLISH_ENGULFING, BEARISH_ENGULFING, HAMMER, SHOOTING_STAR, DOJI,
    MORNING_STAR, EVENING_STAR
}


@dataclass
class SwingPoint:
    """Represents a local swing high or swing low in price action."""
    index: int
    timestamp: float
    price: float
    is_high: bool  # True if swing high, False if swing low
    bar_range: int = 5  # Left and right bar lookback used to identify peak


@dataclass
class PatternMatch:
    """
    Standardized result for any detected chart pattern.
    Contains full geometry, confirmation status, price levels, and audit trail.
    """
    pattern_name: str
    pattern_type: PatternType
    direction: PatternDirection
    symbol: str
    timeframe: str
    detection_timestamp: float
    candle_range: Tuple[int, int]           # (start_index, end_index)
    timestamps: Tuple[float, float]         # (start_timestamp, end_timestamp)
    status: PatternStatus                   # FORMING, CONFIRMED, INVALIDATED
    
    # Critical price boundaries and levels
    key_levels: Dict[str, float] = field(default_factory=dict)
    # Expected key_levels:
    # - 'neckline' or 'boundary'
    # - 'breakout_price'
    # - 'stop_loss'
    # - 'target_price'
    # - 'invalidation_price'

    confidence_score: float = 0.5           # 0.0 to 1.0 calibrated confidence
    supporting_evidence: Dict[str, Any] = field(default_factory=dict)
    
    # Meta / Audit attributes
    detection_method: str = "rule_based_geometric"
    version: str = "1.0.0"

    @property
    def is_confirmed(self) -> bool:
        return self.status == PatternStatus.CONFIRMED

    @property
    def is_forming(self) -> bool:
        return self.status == PatternStatus.FORMING

    @property
    def is_invalidated(self) -> bool:
        return self.status == PatternStatus.INVALIDATED

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-friendly dictionary."""
        d = asdict(self)
        d['pattern_type'] = self.pattern_type.value if isinstance(self.pattern_type, PatternType) else str(self.pattern_type)
        d['direction'] = self.direction.value if isinstance(self.direction, PatternDirection) else str(self.direction)
        d['status'] = self.status.value if isinstance(self.status, PatternStatus) else str(self.status)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PatternMatch":
        """Deserialize from dictionary."""
        return cls(
            pattern_name=data['pattern_name'],
            pattern_type=PatternType(data['pattern_type']),
            direction=PatternDirection(data['direction']),
            symbol=data['symbol'],
            timeframe=data['timeframe'],
            detection_timestamp=float(data['detection_timestamp']),
            candle_range=tuple(data['candle_range']),
            timestamps=tuple(data['timestamps']),
            status=PatternStatus(data['status']),
            key_levels=dict(data.get('key_levels', {})),
            confidence_score=float(data.get('confidence_score', 0.5)),
            supporting_evidence=dict(data.get('supporting_evidence', {})),
            detection_method=data.get('detection_method', 'rule_based_geometric'),
            version=data.get('version', '1.0.0'),
        )


@dataclass
class MarketContext:
    """
    Contextual market environment evaluated at the moment of pattern detection.
    Prevents trading blindly on geometry without market regime awareness.
    """
    symbol: str
    timeframe: str
    timestamp: float
    current_trend: str                      # 'uptrend', 'downtrend', 'ranging'
    trend_strength: float                   # 0.0 to 1.0 (e.g. ADX normalized)
    higher_tf_trend: Optional[str] = None    # Trend on higher TF if available
    nearby_support: Optional[float] = None
    nearby_resistance: Optional[float] = None
    volume_ratio: float = 1.0               # Current volume / 20-period volume SMA
    volume_trend: str = "normal"            # 'expanding', 'drying_up', 'normal'
    atr: float = 0.0
    atr_pct: float = 0.0                    # ATR as percentage of price
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    vwap_position: Optional[str] = None     # 'above_vwap', 'below_vwap'
    regime: str = "neutral"                 # 'trending_bull', 'trending_bear', 'consolidating', 'volatile'
    summary_score: float = 0.0              # Confluence score (-1.0 extreme bear to +1.0 extreme bull)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatternOutcome:
    """
    Evaluates what happened to price after a pattern was detected / confirmed.
    Keeps historical empirical evaluation separate from live predictions.
    """
    pattern_id: str
    pattern_name: str
    symbol: str
    timeframe: str
    direction: str
    detection_timestamp: float
    confirmation_timestamp: Optional[float]
    entry_price: float
    horizon_bars: int                        # e.g. 5, 10, 20, 50 bars evaluated
    return_at_horizon_pct: float             # PnL at the end of horizon
    max_favorable_excursion_pct: float       # MFE (% best price movement in trade direction)
    max_adverse_excursion_pct: float         # MAE (% worst price movement against trade)
    breakout_occurred: bool                  # Did price break in the expected direction?
    stop_hit: bool                           # Would a 1.5x/2x ATR stop have been triggered?
    target_hit: bool                         # Would a 2:1 RR target have been reached?
    net_pnl_after_costs: float               # Estimated return after 0.1% fees + slippage
    evaluation_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

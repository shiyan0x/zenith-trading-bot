"""
strategy_generator.py — Controlled strategy generator for AI research.

Generates candidate blueprints using approved indicators and condition operators.
SAFETY: Zero eval/exec. All generated blueprints strictly validate against
generated_strategy.validate_blueprint().

Supported Archetypes:
  - Trend Following (EMA cross, VWAP confluence, ADX filter)
  - Mean Reversion (Bollinger Bands, RSI oversold/overbought)
  - Momentum / Breakout (MACD cross, RSI momentum, OBV trend)
  - Volatility Expansion (ATR breakout, Bollinger squeeze)
"""

import random
import logging
from typing import Optional

from src.research.generated_strategy import (
    APPROVED_INDICATORS,
    APPROVED_OPS,
    validate_blueprint,
)
from src.research.strategy_registry import StrategyRegistry

logger = logging.getLogger(__name__)


class StrategyGenerator:
    """Generates and mutates structured trading blueprints."""

    def __init__(self, registry: Optional[StrategyRegistry] = None, seed: Optional[int] = None):
        self.registry = registry
        if seed is not None:
            random.seed(seed)

    # ── Archetype Generators ───────────────────────────────────────────────

    def generate_trend_following(self, name_prefix: str = "Trend_Follower") -> dict:
        """Trend following with fast/slow EMA, RSI confirmation, and ATR exits."""
        fast_ema = random.choice([8, 9, 12, 15, 20])
        slow_ema = random.choice([21, 26, 50, 100])
        if fast_ema >= slow_ema:
            slow_ema = fast_ema + 20

        rsi_period = random.choice([10, 14, 21])
        rsi_min = random.choice([45, 50, 55])
        rsi_max = random.choice([65, 70, 75])

        adx_period = random.choice([10, 14, 20])
        adx_min = random.choice([20, 25])

        atr_period = 14
        sl_mult = round(random.uniform(1.5, 3.0), 1)
        rr_ratio = round(random.uniform(1.5, 3.0), 1)
        time_stop = random.choice([15, 20, 30, 40])

        indicators = {
            "ema_fast": {"type": "ema", "period": fast_ema},
            "ema_slow": {"type": "ema", "period": slow_ema},
            "rsi": {"type": "rsi", "period": rsi_period},
            "adx": {"type": "adx", "period": adx_period},
            "atr": {"type": "atr", "period": atr_period},
            "vwap": {"type": "vwap"}
        }

        entry_long = [
            {"left": "ema_fast", "op": "above", "right": "ema_slow"},
            {"left": "rsi", "op": "between", "min": rsi_min, "max": rsi_max},
            {"left": "adx", "op": "above", "right": adx_min},
            {"left": "close", "op": "above", "right": "vwap"}
        ]

        entry_short = [
            {"left": "ema_fast", "op": "below", "right": "ema_slow"},
            {"left": "rsi", "op": "between", "min": 100 - rsi_max, "max": 100 - rsi_min},
            {"left": "adx", "op": "above", "right": adx_min},
            {"left": "close", "op": "below", "right": "vwap"}
        ]

        name = f"{name_prefix}_E{fast_ema}_{slow_ema}_R{rsi_period}"
        blueprint = {
            "name": name,
            "archetype": "trend_following",
            "indicators": indicators,
            "entry_long": entry_long,
            "entry_short": entry_short,
            "exit": {
                "stop_loss_atr_mult": sl_mult,
                "take_profit_rr": rr_ratio,
                "time_stop_bars": time_stop
            },
            "min_candles": max(slow_ema + 10, 50)
        }

        errors = validate_blueprint(blueprint)
        if errors:
            raise ValueError(f"Generated invalid trend blueprint: {errors}")
        return blueprint

    def generate_mean_reversion(self, name_prefix: str = "Mean_Revert") -> dict:
        """Mean reversion with Bollinger Bands and RSI overbought/oversold."""
        bb_period = random.choice([15, 20, 25, 30])
        bb_std = round(random.choice([1.8, 2.0, 2.2, 2.5]), 1)
        rsi_period = random.choice([7, 10, 14])
        rsi_os = random.choice([25, 30, 35])
        rsi_ob = 100 - rsi_os

        adx_period = 14
        adx_max = random.choice([25, 30])  # ensure ranging market

        atr_period = 14
        sl_mult = round(random.uniform(1.5, 2.5), 1)
        rr_ratio = round(random.uniform(1.5, 2.5), 1)
        time_stop = random.choice([10, 15, 20])

        indicators = {
            "bb": {"type": "bollinger_bands", "period": bb_period, "std_dev": bb_std},
            "rsi": {"type": "rsi", "period": rsi_period},
            "adx": {"type": "adx", "period": adx_period},
            "atr": {"type": "atr", "period": atr_period}
        }

        entry_long = [
            {"left": "close", "op": "below", "right": "bb_lower"},
            {"left": "rsi", "op": "below", "right": rsi_os},
            {"left": "adx", "op": "below", "right": adx_max}
        ]

        entry_short = [
            {"left": "close", "op": "above", "right": "bb_upper"},
            {"left": "rsi", "op": "above", "right": rsi_ob},
            {"left": "adx", "op": "below", "right": adx_max}
        ]

        name = f"{name_prefix}_BB{bb_period}_{bb_std}_R{rsi_period}"
        blueprint = {
            "name": name,
            "archetype": "mean_reversion",
            "indicators": indicators,
            "entry_long": entry_long,
            "entry_short": entry_short,
            "exit": {
                "stop_loss_atr_mult": sl_mult,
                "take_profit_rr": rr_ratio,
                "time_stop_bars": time_stop
            },
            "min_candles": max(bb_period + 10, 50)
        }

        errors = validate_blueprint(blueprint)
        if errors:
            raise ValueError(f"Generated invalid mean reversion blueprint: {errors}")
        return blueprint

    def generate_momentum_breakout(self, name_prefix: str = "MACD_Momentum") -> dict:
        """Momentum breakout using MACD histogram/line cross and RSI confirmation."""
        fast = random.choice([8, 12, 15])
        slow = random.choice([21, 26, 30])
        signal = random.choice([7, 9])
        rsi_period = random.choice([14, 21])
        rsi_min = random.choice([50, 55])

        atr_period = 14
        sl_mult = round(random.uniform(1.8, 3.0), 1)
        rr_ratio = round(random.uniform(2.0, 3.5), 1)
        time_stop = random.choice([20, 30, 45])

        indicators = {
            "macd": {"type": "macd", "fast": fast, "slow": slow, "signal": signal},
            "rsi": {"type": "rsi", "period": rsi_period},
            "atr": {"type": "atr", "period": atr_period},
            "vwap": {"type": "vwap"}
        }

        entry_long = [
            {"left": "macd_line", "op": "cross_above", "right": "macd_signal"},
            {"left": "rsi", "op": "above", "right": rsi_min},
            {"left": "close", "op": "above", "right": "vwap"}
        ]

        entry_short = [
            {"left": "macd_line", "op": "cross_below", "right": "macd_signal"},
            {"left": "rsi", "op": "below", "right": 100 - rsi_min},
            {"left": "close", "op": "below", "right": "vwap"}
        ]

        name = f"{name_prefix}_M{fast}_{slow}_R{rsi_period}"
        blueprint = {
            "name": name,
            "archetype": "momentum_breakout",
            "indicators": indicators,
            "entry_long": entry_long,
            "entry_short": entry_short,
            "exit": {
                "stop_loss_atr_mult": sl_mult,
                "take_profit_rr": rr_ratio,
                "time_stop_bars": time_stop
            },
            "min_candles": max(slow + signal + 10, 50)
        }

        errors = validate_blueprint(blueprint)
        if errors:
            raise ValueError(f"Generated invalid momentum blueprint: {errors}")
        return blueprint

    def generate_pattern_reversal(self, name_prefix: str = "Pattern_Reversal") -> dict:
        """Pattern reversal candidate combining geometric/candlestick reversals with RSI filter."""
        pat = random.choice(["Double Bottom", "Hammer", "Bullish Engulfing", "Morning Star"])
        rsi_period = random.choice([10, 14, 21])
        sl_mult = round(random.uniform(1.5, 2.5), 1)
        rr_ratio = round(random.uniform(1.8, 3.0), 1)
        time_stop = random.choice([15, 20, 25])

        blueprint = {
            "name": f"{name_prefix}_{pat.replace(' ', '')}_R{rsi_period}",
            "archetype": "pattern_reversal",
            "indicators": {
                "pattern_sig": {"type": "pattern", "pattern_name": pat, "require_confirmed": True},
                "rsi": {"type": "rsi", "period": rsi_period},
                "atr": {"type": "atr", "period": 14},
            },
            "entry_long": [
                {"left": "pattern_sig_signal", "op": "above", "right": 0},
                {"left": "rsi", "op": "between", "min": 30, "max": 65},
            ],
            "entry_short": [],
            "exit": {
                "stop_loss_atr_mult": sl_mult,
                "take_profit_rr": rr_ratio,
                "time_stop_bars": time_stop,
            },
            "min_candles": 50,
        }
        errors = validate_blueprint(blueprint)
        if errors:
            raise ValueError(f"Generated invalid pattern reversal blueprint: {errors}")
        return blueprint

    def generate_pattern_continuation(self, name_prefix: str = "Pattern_Continuation") -> dict:
        """Pattern continuation candidate combining flags/triangles with trend moving average."""
        pat = random.choice(["Bullish Flag", "Ascending Triangle", "Bullish Pennant"])
        ema_period = random.choice([15, 20, 30])
        sl_mult = round(random.uniform(1.5, 2.5), 1)
        rr_ratio = round(random.uniform(1.8, 3.0), 1)
        time_stop = random.choice([15, 20, 25])

        blueprint = {
            "name": f"{name_prefix}_{pat.replace(' ', '')}_E{ema_period}",
            "archetype": "pattern_continuation",
            "indicators": {
                "pattern_sig": {"type": "pattern", "pattern_name": pat, "require_confirmed": True},
                "ema_trend": {"type": "ema", "period": ema_period},
                "atr": {"type": "atr", "period": 14},
            },
            "entry_long": [
                {"left": "pattern_sig_signal", "op": "above", "right": 0},
                {"left": "close", "op": "above", "right": "ema_trend"},
            ],
            "entry_short": [],
            "exit": {
                "stop_loss_atr_mult": sl_mult,
                "take_profit_rr": rr_ratio,
                "time_stop_bars": time_stop,
            },
            "min_candles": 50,
        }
        errors = validate_blueprint(blueprint)
        if errors:
            raise ValueError(f"Generated invalid pattern continuation blueprint: {errors}")
        return blueprint

    # ── Random Candidate Dispatcher ───────────────────────────────────────

    def generate_random_candidate(self) -> dict:
        """Select a random archetype and create a valid blueprint."""
        archetypes = [
            self.generate_trend_following,
            self.generate_mean_reversion,
            self.generate_momentum_breakout,
            self.generate_pattern_reversal,
            self.generate_pattern_continuation,
        ]
        chosen = random.choice(archetypes)
        return chosen()

    def generate_batch(self, count: int = 5) -> list[dict]:
        """Generate multiple distinct candidate blueprints."""
        candidates = []
        names_seen = set()
        for _ in range(count * 3):
            if len(candidates) >= count:
                break
            candidate = self.generate_random_candidate()
            if candidate['name'] not in names_seen:
                names_seen.add(candidate['name'])
                candidates.append(candidate)
        return candidates

    # ── Mutation & Optimization ───────────────────────────────────────────

    def mutate_blueprint(self, blueprint: dict, mutation_rate: float = 0.3) -> dict:
        """Create a mutated offspring of an existing blueprint."""
        import copy
        child = copy.deepcopy(blueprint)

        # Mutate indicator periods slightly
        for ind_name, spec in child.get("indicators", {}).items():
            if random.random() < mutation_rate:
                if "period" in spec:
                    delta = random.choice([-3, -2, -1, 1, 2, 3])
                    spec["period"] = max(5, spec["period"] + delta)
                if "std_dev" in spec:
                    delta = random.choice([-0.2, 0.2])
                    spec["std_dev"] = max(1.0, round(spec["std_dev"] + delta, 1))

        # Mutate exit parameters
        exit_cfg = child.get("exit", {})
        if random.random() < mutation_rate:
            delta_sl = random.choice([-0.3, -0.1, 0.1, 0.3])
            exit_cfg["stop_loss_atr_mult"] = max(1.0, round(exit_cfg.get("stop_loss_atr_mult", 2.0) + delta_sl, 1))
        if random.random() < mutation_rate:
            delta_rr = random.choice([-0.3, -0.1, 0.1, 0.3])
            exit_cfg["take_profit_rr"] = max(1.2, round(exit_cfg.get("take_profit_rr", 2.0) + delta_rr, 1))
        if random.random() < mutation_rate:
            delta_ts = random.choice([-5, 5])
            exit_cfg["time_stop_bars"] = max(5, exit_cfg.get("time_stop_bars", 20) + delta_ts)

        base_name = child.get("name", "Strat").split("_mut")[0]
        child["name"] = f"{base_name}_mut{random.randint(100, 999)}"

        errors = validate_blueprint(child)
        if errors:
            raise ValueError(f"Mutated blueprint invalid: {errors}")
        return child

    # ── Registry Helper ───────────────────────────────────────────────────

    def register_candidate(self, blueprint: dict, timeframe: str = "1h") -> str:
        """Register a generated blueprint in StrategyRegistry."""
        if not self.registry:
            raise ValueError("Registry not configured on StrategyGenerator")

        name = blueprint.get("name", "Generated")
        version = self.registry.next_version(name)

        entry_desc = f"Long: {len(blueprint.get('entry_long', []))} rules | Short: {len(blueprint.get('entry_short', []))} rules"
        exit_cfg = blueprint.get("exit", {})
        exit_desc = f"SL: ATR*{exit_cfg.get('stop_loss_atr_mult', 2)} | TP: {exit_cfg.get('take_profit_rr', 2)}R | TimeStop: {exit_cfg.get('time_stop_bars', 20)}b"

        strat_id = self.registry.register_generated(
            name=name,
            version=version,
            blueprint=blueprint,
            timeframe=timeframe,
            entry_desc=entry_desc,
            exit_desc=exit_desc
        )
        return strat_id

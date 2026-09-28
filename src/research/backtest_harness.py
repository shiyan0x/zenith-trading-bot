"""
backtest_harness.py — Execution harness for AI strategy backtesting experiments.

Runs walk-forward backtests on generated and legacy strategies, applying
honest fees, realistic slippage, and walk-forward train/test split.
Records results directly into ExperimentStore (SQLite).
"""

import asyncio
import logging
from typing import Optional, Union, List

from src.core.market_feed import MarketFeed, Candle
from src.core.fee_model import FeeModel
from src.strategies.base_strategy import BaseStrategy
from src.strategies.ema_vwap_rsi import EmaVwapRsiStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.brain.backtester import Backtester, BacktestResult
from src.research.generated_strategy import GeneratedStrategy
from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry

logger = logging.getLogger(__name__)


class BacktestHarness:
    """Orchestrates research experiments and backtest evaluation."""

    def __init__(
        self,
        store: ExperimentStore,
        registry: Optional[StrategyRegistry] = None,
        market_feed: Optional[MarketFeed] = None,
        fee_model: Optional[FeeModel] = None,
        config: Optional[dict] = None
    ):
        self.store = store
        self.registry = registry or StrategyRegistry(store)
        self.config = config or {
            "exchange": {
                "name": "binance",
                "ws_url": "wss://stream.binance.com:9443/ws",
                "rest_url": "https://api.binance.com",
                "klines_endpoint": "/api/v3/klines",
                "ticker_endpoint": "/api/v3/ticker/price"
            },
            "backtest": {
                "train_ratio": 0.7,
                "min_sharpe": 0.5,
                "min_trades": 15,
                "risk_per_trade_pct": 1.5
            },
            "trading": {"allow_short": True},
            "risk": {"max_notional_pct": 100},
            "fees": {
                "spot_maker": 0.001,
                "spot_taker": 0.001,
                "futures_maker": 0.0002,
                "futures_taker": 0.0005,
                "mode": "futures"
            },
            "slippage": {
                "base_bps": 5,
                "volatility_multiplier": 2.0,
                "max_bps": 30,
                "random_jitter_pct": 0.0
            }
        }
        self.market_feed = market_feed or MarketFeed(self.config)
        self.fee_model = fee_model or FeeModel(self.config)
        self.backtester = Backtester(self.config, self.market_feed, self.fee_model)

    def instantiate_strategy(self, strategy_record: dict) -> BaseStrategy:
        """Instantiate a strategy object from a registry record or blueprint."""
        stype = strategy_record.get("strategy_type", "")
        bp = strategy_record.get("blueprint", {})
        if isinstance(bp, str):
            import json
            bp = json.loads(bp)

        name = strategy_record.get("name", "Strategy")

        if stype == "legacy":
            strat_cls = bp.get("strategy_class", "")
            params = bp.get("default_params", {})
            if "EmaVwapRsiStrategy" in strat_cls:
                return EmaVwapRsiStrategy(name=name, config=params)
            elif "MeanReversionStrategy" in strat_cls:
                return MeanReversionStrategy(name=name, config=params)
            else:
                raise ValueError(f"Unknown legacy strategy class: {strat_cls}")

        # Generated config-driven strategy
        return GeneratedStrategy(blueprint=bp, config=self.config)

    async def run_experiment(
        self,
        strategy_record: dict,
        symbol: str = "BTCUSDT",
        timeframe: str = "1h",
        days: int = 60
    ) -> dict:
        """Run a single walk-forward experiment and persist metrics."""
        strat_id = strategy_record.get("id", "temp_id")
        strat_name = strategy_record.get("name", "GeneratedStrategy")

        # 1. Create DB experiment
        exp_id = self.store.create_experiment(
            strategy_id=strat_id,
            strategy_name=strat_name,
            symbol=symbol,
            timeframe=timeframe,
            config_snapshot=self.config
        )
        self.store.start_experiment(exp_id)

        try:
            strategy_instance = self.instantiate_strategy(strategy_record)
            bt_out = await self.backtester.run(
                strategy=strategy_instance,
                symbol=symbol,
                interval=timeframe,
                days=days
            )

            if not bt_out:
                self.store.fail_experiment(exp_id, "Insufficient candle data or market feed error")
                return {"experiment_id": exp_id, "status": "failed", "error": "Insufficient candle data"}

            train_res: BacktestResult = bt_out["train"]
            test_res: BacktestResult = bt_out["test"]

            # Save train metrics
            self.store.save_result(exp_id, "train", train_res.to_dict(
                min_sharpe=self.config.get("backtest", {}).get("min_sharpe", 0.5),
                min_trades=self.config.get("backtest", {}).get("min_trades", 15)
            ))

            # Save test metrics
            test_dict = test_res.to_dict(
                min_sharpe=self.config.get("backtest", {}).get("min_sharpe", 0.5),
                min_trades=self.config.get("backtest", {}).get("min_trades", 15)
            )
            self.store.save_result(exp_id, "test", test_dict)

            num_candles = len(train_res.equity_curve) + len(test_res.equity_curve)
            self.store.complete_experiment(exp_id, num_candles=num_candles)

            return {
                "experiment_id": exp_id,
                "strategy_id": strat_id,
                "strategy_name": strat_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "status": "completed",
                "train_metrics": train_res.to_dict(),
                "test_metrics": test_dict,
                "passed": bt_out.get("passed", False)
            }

        except Exception as e:
            logger.exception(f"[HARNESS] Experiment {exp_id} failed: {e}")
            self.store.fail_experiment(exp_id, str(e))
            return {
                "experiment_id": exp_id,
                "strategy_id": strat_id,
                "strategy_name": strat_name,
                "status": "failed",
                "error": str(e)
            }

    async def run_batch(
        self,
        strategy_records: List[dict],
        symbols: List[str] = ["BTCUSDT"],
        timeframe: str = "1h",
        days: int = 60
    ) -> List[dict]:
        """Run experiments for a batch of strategies across multiple symbols."""
        results = []
        for symbol in symbols:
            for strat in strategy_records:
                logger.info(f"[HARNESS] Running {strat.get('name')} on {symbol}...")
                res = await self.run_experiment(
                    strategy_record=strat,
                    symbol=symbol,
                    timeframe=timeframe,
                    days=days
                )
                results.append(res)
        return results

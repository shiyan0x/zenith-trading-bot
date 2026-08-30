import unittest

from src.brain.backtester import Backtester
from src.core.fee_model import FeeModel
from src.core.market_feed import Candle
from src.strategies.base_strategy import BaseStrategy


def config(mode='spot', allow_short=False):
    return {
        'fees': {
            'mode': mode,
            'spot_maker': 0.0,
            'spot_taker': 0.0,
            'futures_maker': 0.0,
            'futures_taker': 0.0,
        },
        'slippage': {
            'base_bps': 1,
            'volatility_multiplier': 0.0,
            'max_bps': 1,
            'random_jitter_pct': 0.0,
        },
        'kelly': {'bootstrap_risk_pct': 1.0},
        'risk': {
            'max_risk_per_trade_pct': 2,
            'max_notional_pct': 100,
        },
        'trading': {'allow_short': allow_short},
        'backtest': {
            'risk_per_trade_pct': 1.0,
            'train_ratio': 0.7,
            'min_sharpe': 0.5,
            'min_trades': 1,
        },
    }


class TwoBarStrategy(BaseStrategy):
    def __init__(self, side):
        super().__init__('two-bar', {})
        self.side = side
        self.entered = False

    def should_enter(self):
        if not self.entered and len(self.closes) == 1:
            self.entered = True
            return self.side
        return None

    def should_exit(self, position_side):
        return len(self.closes) >= 2

    def get_trade_plan(self):
        return {
            'side': self.side,
            'stop_loss': 90 if self.side == 'long' else 110,
            'take_profit': 120 if self.side == 'long' else 80,
        }

    def reset(self):
        super().reset()
        self.entered = False


class BacktesterAccountingTests(unittest.TestCase):
    def candles(self, first, second):
        return [
            Candle(1, first, first, first, first, 1_000),
            Candle(2, second, second, second, second, 1_000),
        ]

    def test_long_exit_increases_balance_when_price_rises(self):
        cfg = config()
        result = Backtester(cfg, None, FeeModel(cfg))._run_on_candles(
            TwoBarStrategy('long'), self.candles(100, 110), 10_000, 'test'
        )

        self.assertEqual(result.total_trades, 1)
        self.assertGreater(result.ending_balance, 10_000)
        self.assertGreater(result.trades[0]['net_pnl'], 0)

    def test_short_exit_increases_balance_when_price_falls(self):
        cfg = config(mode='futures', allow_short=True)
        result = Backtester(cfg, None, FeeModel(cfg))._run_on_candles(
            TwoBarStrategy('short'), self.candles(100, 90), 10_000, 'test'
        )

        self.assertEqual(result.total_trades, 1)
        self.assertGreater(result.ending_balance, 10_000)
        self.assertGreater(result.trades[0]['net_pnl'], 0)

    def test_spot_backtest_rejects_short_entries(self):
        cfg = config()
        result = Backtester(cfg, None, FeeModel(cfg))._run_on_candles(
            TwoBarStrategy('short'), self.candles(100, 90), 10_000, 'test'
        )

        self.assertEqual(result.total_trades, 0)
        self.assertEqual(result.ending_balance, 10_000)


if __name__ == '__main__':
    unittest.main()

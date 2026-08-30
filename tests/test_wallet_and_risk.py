import unittest

from src.brain.kelly_sizer import KellySizer
from src.brain.risk_manager import RiskManager
from src.core.paper_wallet import PaperWallet


def config(starting_balance=10_000):
    return {
        'paper_trading': {
            'starting_balance': starting_balance,
            'currency': 'USDT',
        },
        'kelly': {
            'fraction': 0.5,
            'lookback_trades': 20,
            'min_trades_required': 10,
            'bootstrap_risk_pct': 1.0,
        },
        'risk': {
            'max_risk_per_trade_pct': 2,
            'max_drawdown_pct': 15,
            'circuit_breaker_cooldown_minutes': 0,
        },
    }


class PaperWalletTests(unittest.TestCase):
    def test_short_equity_and_realized_pnl_are_correct(self):
        wallet = PaperWallet(config())
        position = wallet.open_position('BTCUSDT', 'short', 1, 100, 1)

        self.assertIsNotNone(position)
        self.assertEqual(wallet.cash, 9_899)
        self.assertEqual(wallet.total_equity({'BTCUSDT': 100}), 9_999)
        self.assertEqual(wallet.total_equity({'BTCUSDT': 90}), 10_009)

        trade = wallet.close_position(position.id, 90, 1)
        self.assertEqual(trade['net_pnl'], 8)
        self.assertEqual(wallet.cash, 10_008)
        self.assertEqual(wallet.total_equity({}), 10_008)

    def test_short_collateral_cannot_be_reused_as_buying_power(self):
        wallet = PaperWallet(config(starting_balance=100))
        first = wallet.open_position('BTCUSDT', 'short', 1, 100, 0)
        second = wallet.open_position('ETHUSDT', 'short', 1, 100, 0)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(wallet.cash, 0)


class KellyAndRiskTests(unittest.TestCase):
    def test_kelly_bootstraps_only_without_history_then_stops_on_no_edge(self):
        sizer = KellySizer(config())
        self.assertEqual(sizer.get_position_size_pct([]), 1.0)

        losing_trades = [{'net_pnl': -1.0} for _ in range(10)]
        self.assertEqual(sizer.get_position_size_pct(losing_trades), 0.0)
        self.assertEqual(sizer.get_stats()['status'], 'no_edge')

    def test_stop_distance_sizing_respects_risk_cap(self):
        manager = RiskManager(config())
        sizing = manager.calculate_position_quantity(
            risk_pct=5, equity=10_000, entry_price=100, stop_loss=90
        )

        self.assertEqual(sizing['risk_amount'], 200)
        self.assertEqual(sizing['quantity'], 20)
        self.assertEqual(sizing['notional'], 2_000)

    def test_breaker_requires_revalidation_after_cooldown(self):
        manager = RiskManager(config())
        self.assertTrue(manager.check_drawdown(15))
        self.assertFalse(manager.can_trade())
        self.assertTrue(manager.needs_revalidation())

        manager.complete_revalidation(False)
        self.assertFalse(manager.can_trade())
        manager.complete_revalidation(True)
        self.assertTrue(manager.can_trade())


if __name__ == '__main__':
    unittest.main()

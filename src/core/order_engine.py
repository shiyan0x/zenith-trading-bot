"""
order_engine.py — Honest order execution.

This is where trades actually happen. Every order goes through
the full cost pipeline: slippage → execution → fees → wallet update.

Honesty rules:
1. Market orders only — we don't assume perfect limit fills.
2. Execution price = live price + slippage (always worse than what you see).
3. Fees are charged on every trade.
4. A losing trade closes at whatever the market says. No delay, no rounding.
"""

import time
import logging
from typing import Optional

from src.core.fee_model import FeeModel
from src.core.paper_wallet import PaperWallet, Position
from src.core.trade_logger import TradeLogger

logger = logging.getLogger(__name__)


class OrderEngine:
    """
    Executes orders honestly against real market prices with fake money.

    Think of it as the "cashier" — it processes your order, charges
    the real costs, and updates your wallet.
    """

    def __init__(self, wallet: PaperWallet, fee_model: FeeModel,
                 trade_logger: TradeLogger):
        self.wallet = wallet
        self.fee_model = fee_model
        self.logger = trade_logger

    def market_buy(self, symbol: str, quantity: float,
                   current_price: float,
                   volatility: float = 0.0) -> Optional[Position]:
        """
        Execute a market buy order.

        What happens (honest version):
        1. Slippage makes the price a bit higher than what you see
        2. You pay a trading fee on the total value
        3. Your cash goes down by (price × quantity + fee)
        4. You now hold a position

        Returns the Position if successful, None if you can't afford it.
        """
        # Step 1: Calculate real costs
        costs = self.fee_model.total_cost(
            price=current_price,
            quantity=quantity,
            side='buy',
            volatility=volatility
        )

        logger.info(
            f"[ORDER] BUY {quantity:.6f} {symbol} | "
            f"Market: ${current_price:.2f} → Exec: ${costs['execution_price']:.2f} | "
            f"Slippage: {costs['slippage_bps']:.1f} bps | "
            f"Fee: ${costs['fee']:.4f}"
        )

        # Step 2: Open position in wallet
        position = self.wallet.open_position(
            symbol=symbol,
            side='long',
            quantity=quantity,
            execution_price=costs['execution_price'],
            fee=costs['fee']
        )

        return position

    def market_sell(self, symbol: str, position_id: str,
                    current_price: float,
                    volatility: float = 0.0) -> Optional[dict]:
        """
        Close a position with a market sell.

        Honesty rule: the exit price is the real market price
        minus slippage minus fees. If it's a loss, it's a loss.
        We never round, delay, or hide it.

        Returns the trade summary dict if successful.
        """
        pos = self.wallet.positions.get(position_id)
        if pos is None:
            logger.warning(f"[ORDER] Position {position_id} not found")
            return None

        # Step 1: Calculate real exit costs
        costs = self.fee_model.total_cost(
            price=current_price,
            quantity=pos.quantity,
            side='sell',
            volatility=volatility
        )

        logger.info(
            f"[ORDER] SELL {pos.quantity:.6f} {pos.symbol} | "
            f"Market: ${current_price:.2f} → Exec: ${costs['execution_price']:.2f} | "
            f"Slippage: {costs['slippage_bps']:.1f} bps | "
            f"Fee: ${costs['fee']:.4f}"
        )

        # Step 2: Close position in wallet at the honest price
        trade = self.wallet.close_position(
            position_id=position_id,
            execution_price=costs['execution_price'],
            fee=costs['fee']
        )

        # Step 3: Log the trade
        if trade:
            self.logger.log_trade(trade)

        return trade

    def close_all(self, prices: dict[str, float],
                  volatility: float = 0.0) -> list[dict]:
        """
        Emergency close: shut down all open positions at current prices.
        Used by the risk manager when the drawdown breaker triggers.
        """
        trades = []
        position_ids = list(self.wallet.positions.keys())

        for pid in position_ids:
            pos = self.wallet.positions.get(pid)
            if pos:
                price = prices.get(pos.symbol, 0)
                if price > 0:
                    trade = self.market_sell(pos.symbol, pid, price, volatility)
                    if trade:
                        trades.append(trade)

        if trades:
            logger.warning(
                f"[ORDER] Emergency close: {len(trades)} positions closed"
            )
        return trades

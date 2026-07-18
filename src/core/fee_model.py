"""
fee_model.py — Real trading costs applied to every trade.

Nothing is free in real trading. This module ensures the paper bot
pays the same costs a real trader would:
1. Trading fees (maker/taker)
2. Slippage (the price moves against you when you hit the market)
3. Funding fees (for futures — disabled by default since we trade spot)

All numbers verified from official Binance fee schedule (July 2026):
- Spot: 0.1% maker, 0.1% taker (VIP 0)
- Futures: 0.02% maker, 0.05% taker (VIP 0)
- Source: https://www.binance.com/en/fee/trading
"""

import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class FeeModel:
    """
    Calculates real trading costs for every order.

    Honesty rule: every trade pays full fees and slippage.
    No trade ever gets a "free" fill.
    """

    def __init__(self, config: dict):
        fee_cfg = config['fees']
        slip_cfg = config['slippage']

        # Fee rates (verified from Binance)
        self.mode = fee_cfg.get('mode', 'spot')
        if self.mode == 'spot':
            self.maker_fee = fee_cfg['spot_maker']   # 0.001 = 0.1%
            self.taker_fee = fee_cfg['spot_taker']   # 0.001 = 0.1%
        else:
            self.maker_fee = fee_cfg['futures_maker']  # 0.0002 = 0.02%
            self.taker_fee = fee_cfg['futures_taker']  # 0.0005 = 0.05%

        # Slippage parameters
        self.base_bps = slip_cfg['base_bps']            # 5 basis points
        self.vol_multiplier = slip_cfg['volatility_multiplier']  # 2.0
        self.max_bps = slip_cfg['max_bps']              # 30 bps cap

    def calculate_fee(self, notional_value: float, is_maker: bool = False) -> float:
        """
        Calculate trading fee for an order.

        Args:
            notional_value: total value of the order in USDT
            is_maker: True for limit orders (lower fee), False for market orders

        Returns:
            Fee amount in USDT.

        We default to taker fee because paper trading uses market orders
        (honest — we don't assume perfect limit fills).
        """
        rate = self.maker_fee if is_maker else self.taker_fee
        fee = notional_value * rate
        logger.debug(f"[FEE] {rate*100:.3f}% on ${notional_value:.2f} = ${fee:.4f}")
        return fee

    def calculate_slippage(self, price: float, side: str,
                           volatility: float = 0.0,
                           order_size_ratio: float = 0.01) -> float:
        """
        Calculate slippage — the price impact of hitting the market.

        Plain English: when you place a market order to buy, you don't get
        the exact price you see. You get a slightly worse price because
        your order eats into the order book. This function models that.

        Args:
            price: current market price
            side: 'buy' or 'sell'
            volatility: recent price volatility (0-1 scale, e.g. 0.02 = 2%)
            order_size_ratio: your order size relative to typical volume (0-1)

        Returns:
            The execution price after slippage (always worse than market price).
        """
        # Base slippage in basis points (1 bp = 0.01%)
        base = self.base_bps

        # Add volatility component: more volatile = more slippage
        vol_component = volatility * self.vol_multiplier * 100  # convert to bps

        # Add size component: bigger orders move the market more
        size_component = order_size_ratio * 10  # rough scaling

        # Total slippage in basis points, capped
        total_bps = min(base + vol_component + size_component, self.max_bps)

        # Add a tiny random jitter (±20% of total) to be realistic
        # Real slippage isn't perfectly predictable
        jitter = random.uniform(-0.2, 0.2) * total_bps
        total_bps = max(1, total_bps + jitter)  # minimum 1 bp

        # Convert to price impact
        slippage_pct = total_bps / 10000.0  # bps to decimal

        if side == 'buy':
            # Buying: you pay MORE than the market price
            execution_price = price * (1 + slippage_pct)
        else:
            # Selling: you receive LESS than the market price
            execution_price = price * (1 - slippage_pct)

        logger.debug(
            f"[SLIPPAGE] {side} at ${price:.2f} → ${execution_price:.2f} "
            f"({total_bps:.1f} bps, vol={volatility:.4f})"
        )
        return execution_price

    def total_cost(self, price: float, quantity: float, side: str,
                   volatility: float = 0.0) -> dict:
        """
        Calculate the full honest cost of a trade.

        Returns a dict with:
        - execution_price: price after slippage
        - fee: trading fee in USDT
        - total_cost: total USDT paid/received including all costs
        - slippage_bps: how much slippage was applied
        """
        exec_price = self.calculate_slippage(price, side, volatility)
        notional = exec_price * quantity
        fee = self.calculate_fee(notional)

        slippage_bps = abs(exec_price - price) / price * 10000

        if side == 'buy':
            total = notional + fee  # you pay price + fee
        else:
            total = notional - fee  # you receive price - fee

        return {
            'execution_price': exec_price,
            'fee': fee,
            'notional': notional,
            'total_cost': total,
            'slippage_bps': slippage_bps,
        }

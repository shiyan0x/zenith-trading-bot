"""
Risk Management Module
=======================
Handles position sizing, stop-loss/take-profit calculation,
and trade risk validation.
"""

import pandas as pd
import numpy as np
from config import (
    DEFAULT_ACCOUNT_BALANCE,
    MAX_RISK_PER_TRADE_PCT,
    DEFAULT_RR_RATIO,
    ATR_SL_MULTIPLIER,
)
from src.indicators import atr
from utils import setup_logger

logger = setup_logger("RiskManager")


class RiskManager:
    """
    Manages trade risk: position sizing, stop-loss, take-profit.
    
    Usage:
        rm = RiskManager(account_balance=10000, risk_pct=1.0)
        size = rm.calculate_position_size(entry=100.0, stop_loss=98.0)
        tp = rm.calculate_take_profit(entry=100.0, stop_loss=98.0, rr_ratio=2.0)
    """
    
    def __init__(
        self,
        account_balance: float = DEFAULT_ACCOUNT_BALANCE,
        risk_pct: float = MAX_RISK_PER_TRADE_PCT,
        rr_ratio: float = DEFAULT_RR_RATIO,
        atr_multiplier: float = ATR_SL_MULTIPLIER,
    ):
        self.account_balance = account_balance
        self.risk_pct = risk_pct / 100.0  # Convert percentage to decimal
        self.rr_ratio = rr_ratio
        self.atr_multiplier = atr_multiplier
    
    def calculate_position_size(
        self,
        entry: float,
        stop_loss: float,
    ) -> int:
        """
        Calculate the number of shares/units to trade based on risk.
        
        Formula:
            Risk Amount = Account Balance × Risk %
            Position Size = Risk Amount / |Entry - Stop Loss|
        
        Args:
            entry: Entry price
            stop_loss: Stop-loss price
        
        Returns:
            Number of units to trade (integer, rounded down)
        """
        risk_amount = self.account_balance * self.risk_pct
        price_risk = abs(entry - stop_loss)
        
        if price_risk <= 0:
            logger.warning("Stop loss distance is zero — cannot calculate position size.")
            return 0
        
        position_size = risk_amount / price_risk
        return int(np.floor(position_size))
    
    def calculate_stop_loss(
        self,
        entry: float,
        atr_value: float,
        direction: str,
    ) -> float:
        """
        Calculate ATR-based stop loss.
        
        Args:
            entry: Entry price
            atr_value: Current ATR value
            direction: "BUY" for long, "SELL" for short
        
        Returns:
            Stop-loss price
        """
        sl_distance = atr_value * self.atr_multiplier
        
        if direction == "BUY":
            return round(entry - sl_distance, 2)
        elif direction == "SELL":
            return round(entry + sl_distance, 2)
        else:
            raise ValueError(f"Invalid direction: {direction}. Must be 'BUY' or 'SELL'.")
    
    def calculate_take_profit(
        self,
        entry: float,
        stop_loss: float,
        rr_ratio: float = None,
    ) -> float:
        """
        Calculate take-profit based on risk-reward ratio.
        
        Args:
            entry: Entry price
            stop_loss: Stop-loss price
            rr_ratio: Risk:Reward ratio (default from config)
        
        Returns:
            Take-profit price
        """
        rr = rr_ratio or self.rr_ratio
        risk = abs(entry - stop_loss)
        
        if entry > stop_loss:
            # Long trade: TP is above entry
            return round(entry + (risk * rr), 2)
        else:
            # Short trade: TP is below entry
            return round(entry - (risk * rr), 2)
    
    def calculate_risk_reward(
        self,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> float:
        """
        Calculate the actual risk-reward ratio for a trade.
        
        Returns:
            R:R ratio (e.g., 2.0 means reward is 2x the risk)
        """
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        
        if risk <= 0:
            return 0.0
        
        return round(reward / risk, 2)
    
    def validate_trade(
        self,
        entry: float,
        stop_loss: float,
        take_profit: float,
        min_rr: float = 1.5,
    ) -> dict:
        """
        Validate whether a trade meets risk management criteria.
        
        Args:
            entry: Entry price
            stop_loss: Stop-loss price
            take_profit: Take-profit price
            min_rr: Minimum acceptable R:R ratio
        
        Returns:
            dict with validation results
        """
        rr = self.calculate_risk_reward(entry, stop_loss, take_profit)
        position_size = self.calculate_position_size(entry, stop_loss)
        risk_amount = self.account_balance * self.risk_pct
        
        is_valid = rr >= min_rr and position_size > 0
        
        return {
            "valid": is_valid,
            "risk_reward": rr,
            "position_size": position_size,
            "risk_amount": round(risk_amount, 2),
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": "OK" if is_valid else f"R:R {rr} < minimum {min_rr}" if rr < min_rr else "Position size is 0",
        }
    
    def update_balance(self, pnl: float):
        """Update account balance after a trade."""
        self.account_balance += pnl
        logger.info(f"Balance updated: {self.account_balance:.2f} (PnL: {pnl:+.2f})")

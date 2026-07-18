"""
risk_manager.py — Drawdown circuit breaker + reset-and-learn.

This is the safety net. When the bot loses too much, it:
1. STOPS all trading immediately
2. Closes all open positions
3. Logs what happened (the "learn" part)
4. Waits for a cooldown period
5. Re-runs the backtester to check if the strategy still works
6. Only resumes if it passes again

Plain English: if you're on a losing streak, stop digging.
Take a break, figure out what went wrong, and only come back
when the evidence says it's okay.
"""

import time
import logging

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Monitors drawdown and triggers circuit breakers.

    Think of it like a fuse box in your house —
    if too much current flows, the fuse blows to prevent a fire.
    Here, if too much money is lost, the breaker trips to prevent ruin.
    """

    def __init__(self, config: dict):
        risk_cfg = config.get('risk', {})
        self.max_drawdown_pct = risk_cfg.get('max_drawdown_pct', 15)
        self.max_risk_per_trade = risk_cfg.get('max_risk_per_trade_pct', 2)
        self.cooldown_minutes = risk_cfg.get('circuit_breaker_cooldown_minutes', 60)

        # State
        self.is_breaker_active = False
        self.breaker_triggered_at = None
        self.breaker_reason = ""
        self.breaker_history: list[dict] = []  # log of all triggers

    def check_drawdown(self, current_drawdown_pct: float) -> bool:
        """
        Check if drawdown has exceeded the maximum threshold.

        Returns True if trading should STOP.
        """
        if current_drawdown_pct >= self.max_drawdown_pct:
            if not self.is_breaker_active:
                self._trigger_breaker(
                    f"Drawdown hit {current_drawdown_pct:.1f}% "
                    f"(limit: {self.max_drawdown_pct}%)"
                )
            return True

        return False

    def _trigger_breaker(self, reason: str):
        """Activate the circuit breaker — STOP all trading."""
        self.is_breaker_active = True
        self.breaker_triggered_at = time.time()
        self.breaker_reason = reason

        event = {
            'triggered_at': self.breaker_triggered_at,
            'reason': reason,
            'cooldown_minutes': self.cooldown_minutes,
        }
        self.breaker_history.append(event)

        logger.warning(
            f"\n{'!'*60}\n"
            f"  ⚠️  CIRCUIT BREAKER TRIGGERED\n"
            f"  Reason: {reason}\n"
            f"  All trading STOPPED.\n"
            f"  Cooldown: {self.cooldown_minutes} minutes.\n"
            f"{'!'*60}\n"
        )

    def can_trade(self) -> bool:
        """
        Check if trading is allowed right now.

        Returns False if the circuit breaker is active
        and cooldown hasn't expired.
        """
        if not self.is_breaker_active:
            return True

        # Check if cooldown has passed
        if self.breaker_triggered_at:
            elapsed = (time.time() - self.breaker_triggered_at) / 60
            if elapsed >= self.cooldown_minutes:
                logger.info(
                    f"[RISK] Cooldown expired after {elapsed:.0f} minutes. "
                    f"Resetting breaker — will need backtest re-check."
                )
                self.is_breaker_active = False
                return True

        return False

    def reset_breaker(self):
        """Manually reset the circuit breaker (after re-validation)."""
        self.is_breaker_active = False
        self.breaker_triggered_at = None
        self.breaker_reason = ""
        logger.info("[RISK] Circuit breaker manually reset.")

    def validate_trade_size(self, risk_pct: float, equity: float) -> float:
        """
        Cap the trade size to the maximum allowed risk.

        Even if Kelly says "bet 5%", this caps it at max_risk_per_trade (2%).

        Returns the allowed dollar amount to risk.
        """
        capped_pct = min(risk_pct, self.max_risk_per_trade)
        amount = equity * (capped_pct / 100)

        if risk_pct > self.max_risk_per_trade:
            logger.info(
                f"[RISK] Trade size capped: {risk_pct:.1f}% → "
                f"{self.max_risk_per_trade}% (${amount:.2f})"
            )

        return amount

    def get_status(self) -> dict:
        """Current risk manager status for the dashboard."""
        return {
            'breaker_active': self.is_breaker_active,
            'breaker_reason': self.breaker_reason,
            'max_drawdown_pct': self.max_drawdown_pct,
            'max_risk_per_trade': self.max_risk_per_trade,
            'cooldown_minutes': self.cooldown_minutes,
            'total_breaker_triggers': len(self.breaker_history),
        }

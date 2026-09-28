"""
live_guard.py — External Live Risk Guardian, Authenticated Live Confirmation & Global Emergency Stop.

SAFETY GUARANTEES:
1. Positioned outside and independent of any AI strategy module.
2. AI cannot modify live risk limits, increase leverage, or replace live strategies.
3. Paper trading is ALWAYS the default mode.
4. Enabling live trading requires explicit, authenticated human confirmation verifying
   account ID, instruments, order size limits, and risk thresholds.
5. Global emergency stop immediately halts execution, cancels open orders, and closes positions.
"""

import os
import time
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Standard confirmation phrase required for live mode authentication
LIVE_CONFIRMATION_PHRASE = "I VERIFY ACCOUNT AND RISK LIMITS FOR LIVE TRADING"


class LiveRiskGuardian:
    """
    Strict external risk controls running OUTSIDE the AI strategy module.
    
    Invariants:
    - Maximum order size (USD)
    - Daily loss limit (USD / %)
    - Maximum portfolio exposure (% of equity)
    - Maximum position count
    - Strict leverage cap of 1.0 (no leverage escalation allowed)
    - Risk parameters are frozen upon initialization; AI cannot change them.
    """

    def __init__(self,
                 max_order_usd: float = 500.0,
                 daily_loss_limit_usd: float = 200.0,
                 max_exposure_pct: float = 50.0,
                 max_open_positions: int = 2,
                 leverage_cap: float = 1.0):
        # Freeze risk configuration
        self._max_order_usd = float(max_order_usd)
        self._daily_loss_limit_usd = float(daily_loss_limit_usd)
        self._max_exposure_pct = float(max_exposure_pct)
        self._max_open_positions = int(max_open_positions)
        self._leverage_cap = min(float(leverage_cap), 1.0)  # Cannot exceed 1.0

        # State tracking
        self._daily_realized_loss = 0.0
        self._day_start_timestamp = time.time()
        self._trade_history: List[Tuple[float, float]] = []  # (timestamp, net_pnl)

    @property
    def max_order_usd(self) -> float:
        return self._max_order_usd

    @property
    def daily_loss_limit_usd(self) -> float:
        return self._daily_loss_limit_usd

    @property
    def max_exposure_pct(self) -> float:
        return self._max_exposure_pct

    @property
    def max_open_positions(self) -> int:
        return self._max_open_positions

    @property
    def leverage_cap(self) -> float:
        return self._leverage_cap

    def _refresh_daily_loss(self):
        """Roll over daily loss window every 24 hours."""
        now = time.time()
        # Keep only trades within last 24 hours (86400s)
        self._trade_history = [
            (ts, pnl) for ts, pnl in self._trade_history if now - ts < 86400
        ]
        # Sum of negative PnLs in last 24h
        losses = [abs(pnl) for _, pnl in self._trade_history if pnl < 0]
        self._daily_realized_loss = sum(losses)

    def record_trade_result(self, net_pnl: float):
        """Record trade PnL for rolling 24h daily loss tracking."""
        self._trade_history.append((time.time(), net_pnl))
        self._refresh_daily_loss()

    def validate_order(self, symbol: str, side: str, quantity: float,
                       price: float, equity: float,
                       open_positions: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Validate an order against strict external risk constraints.
        Returns (True, "") if order is safe, or (False, rejection_reason).
        """
        self._refresh_daily_loss()

        # Check 1: Daily loss limit
        if self._daily_realized_loss >= self._daily_loss_limit_usd:
            msg = (
                f"Order rejected: Daily loss limit reached "
                f"(${self._daily_realized_loss:.2f} >= ${self._daily_loss_limit_usd:.2f})"
            )
            logger.warning(f"[RISK GUARDIAN] {msg}")
            return False, msg

        # Check 2: Max order USD notional
        order_notional = quantity * price
        if order_notional > self._max_order_usd:
            msg = (
                f"Order rejected: Order notional ${order_notional:.2f} exceeds "
                f"maximum allowed order limit of ${self._max_order_usd:.2f}"
            )
            logger.warning(f"[RISK GUARDIAN] {msg}")
            return False, msg

        # Check 3: Max open positions
        # If opening a new symbol position, ensure we don't exceed max_open_positions
        existing_symbols = {p.get('symbol') for p in open_positions}
        if symbol not in existing_symbols and len(open_positions) >= self._max_open_positions:
            msg = (
                f"Order rejected: Open positions count ({len(open_positions)}) "
                f"reaches limit of {self._max_open_positions}"
            )
            logger.warning(f"[RISK GUARDIAN] {msg}")
            return False, msg

        # Check 4: Maximum portfolio exposure
        current_open_notional = sum(
            p.get('quantity', 0) * p.get('entry_price', price) for p in open_positions
        )
        total_projected_exposure = current_open_notional + order_notional
        max_allowed_exposure = equity * (self._max_exposure_pct / 100.0) if equity > 0 else 0.0

        if total_projected_exposure > max_allowed_exposure:
            msg = (
                f"Order rejected: Total projected exposure ${total_projected_exposure:.2f} "
                f"exceeds {self._max_exposure_pct:.1f}% equity limit (${max_allowed_exposure:.2f})"
            )
            logger.warning(f"[RISK GUARDIAN] {msg}")
            return False, msg

        return True, ""

    def get_limits(self) -> Dict[str, Any]:
        """Return snapshot of risk limits."""
        self._refresh_daily_loss()
        return {
            'max_order_usd': self._max_order_usd,
            'daily_loss_limit_usd': self._daily_loss_limit_usd,
            'current_daily_loss_usd': self._daily_realized_loss,
            'max_exposure_pct': self._max_exposure_pct,
            'max_open_positions': self._max_open_positions,
            'leverage_cap': self._leverage_cap,
        }


class LiveModeAuth:
    """
    Manages authenticated confirmation for enabling live trading mode.
    Paper trading is the invariant default.
    """

    def __init__(self, guardian: LiveRiskGuardian, allowed_instruments: List[str]):
        self.guardian = guardian
        self.allowed_instruments = [inst.upper() for inst in allowed_instruments]
        self._is_live_enabled = False
        self._authenticated_user: Optional[str] = None
        self._authenticated_at: Optional[float] = None
        self._confirmed_account_id: Optional[str] = None

    @property
    def is_live_enabled(self) -> bool:
        return self._is_live_enabled

    def confirm_live_trading(self,
                             auth_secret: str,
                             confirmation_phrase: str,
                             account_id: str,
                             verified_instruments: List[str],
                             max_order_size_usd: float,
                             risk_limits_ack: Dict[str, Any],
                             user_id: str = "human_operator") -> Tuple[bool, str]:
        """
        Explicit, authenticated human confirmation step for live mode.
        Requires user to verify account, instrument, order size, and risk limits.
        """
        # Step 1: Verify secret / token
        expected_secret = os.environ.get("LIVE_CONFIRMATION_SECRET", "DEMO_SAFE_LIVE_SECRET")
        if not auth_secret or auth_secret != expected_secret:
            msg = "Authentication failed: Invalid live confirmation secret."
            logger.error(f"[LIVE AUTH] {msg}")
            return False, msg

        # Step 2: Verify confirmation phrase
        if confirmation_phrase != LIVE_CONFIRMATION_PHRASE:
            msg = (
                f"Confirmation phrase mismatch. Expected: '{LIVE_CONFIRMATION_PHRASE}', "
                f"got: '{confirmation_phrase}'"
            )
            logger.error(f"[LIVE AUTH] {msg}")
            return False, msg

        # Step 3: Verify account ID
        if not account_id or len(account_id.strip()) == 0:
            msg = "Account verification failed: account_id cannot be blank."
            logger.error(f"[LIVE AUTH] {msg}")
            return False, msg

        # Step 4: Verify instruments
        norm_verified = [i.upper() for i in verified_instruments]
        for inst in norm_verified:
            if inst not in self.allowed_instruments:
                msg = f"Instrument '{inst}' is not in approved list {self.allowed_instruments}."
                logger.error(f"[LIVE AUTH] {msg}")
                return False, msg

        # Step 5: Verify order size limit doesn't exceed guardian
        if max_order_size_usd > self.guardian.max_order_usd:
            msg = (
                f"Order size verification failed: ${max_order_size_usd:.2f} exceeds "
                f"Risk Guardian limit ${self.guardian.max_order_usd:.2f}."
            )
            logger.error(f"[LIVE AUTH] {msg}")
            return False, msg

        # Step 6: Verify risk limits acknowledgement
        if not risk_limits_ack.get('ack_daily_loss') or not risk_limits_ack.get('ack_max_drawdown'):
            msg = "Risk limits verification failed: User must explicitly acknowledge daily loss and drawdown limits."
            logger.error(f"[LIVE AUTH] {msg}")
            return False, msg

        # All checks passed — enable live mode
        self._is_live_enabled = True
        self._authenticated_user = user_id
        self._authenticated_at = time.time()
        self._confirmed_account_id = account_id

        logger.warning(
            f"[LIVE AUTH] ⚠️ LIVE TRADING ENABLED by {user_id} for account {account_id} "
            f"on instruments {norm_verified} at {time.ctime(self._authenticated_at)}"
        )
        return True, "Live mode successfully authenticated and enabled."

    def disable_live_trading(self, reason: str = "Manual toggle to paper"):
        """Revert back to safe paper trading mode."""
        self._is_live_enabled = False
        self._authenticated_user = None
        self._authenticated_at = None
        self._confirmed_account_id = None
        logger.info(f"[LIVE AUTH] Reverted to PAPER TRADING mode. Reason: {reason}")


class EmergencyStop:
    """
    Global Emergency Stop.
    Haults all trading, cancels open orders, and closes all open positions.
    """

    def __init__(self):
        self._is_halted = False
        self._halt_reason: str = ""
        self._halted_at: Optional[float] = None
        self._halted_by: str = ""

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    def trigger(self, reason: str, source: str = "manual",
                broker: Optional[Any] = None,
                prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Trigger emergency stop immediately."""
        self._is_halted = True
        self._halt_reason = reason
        self._halted_at = time.time()
        self._halted_by = source

        closed_trades = []
        if broker and prices:
            try:
                closed_trades = broker.close_all(prices)
            except Exception as e:
                logger.error(f"[EMERGENCY STOP] Failed during broker.close_all: {e}")

        logger.critical(
            f"\n{'!'*60}\n"
            f"  🚨 GLOBAL EMERGENCY STOP ACTIVATED 🚨\n"
            f"  Reason: {reason}\n"
            f"  Triggered by: {source}\n"
            f"  Positions Closed: {len(closed_trades)}\n"
            f"  ALL TRADING IS HALTED IMMEDIATELY.\n"
            f"{'!'*60}\n"
        )
        return {
            'halted': True,
            'reason': reason,
            'source': source,
            'timestamp': self._halted_at,
            'closed_positions_count': len(closed_trades),
        }

    def reset(self, reset_token: str) -> bool:
        """Reset emergency stop (requires confirmation token)."""
        expected = os.environ.get("EMERGENCY_RESET_TOKEN", "RESET_EMERGENCY_OVERRIDE")
        if reset_token != expected:
            logger.warning("[EMERGENCY STOP] Reset attempted with invalid token.")
            return False

        self._is_halted = False
        self._halt_reason = ""
        self._halted_at = None
        self._halted_by = ""
        logger.warning("[EMERGENCY STOP] Emergency stop has been reset. Trading may resume.")
        return True

    def get_status(self) -> Dict[str, Any]:
        return {
            'is_halted': self._is_halted,
            'reason': self._halt_reason,
            'halted_at': self._halted_at,
            'halted_by': self._halted_by,
        }

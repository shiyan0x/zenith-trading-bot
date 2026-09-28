"""
paper_wallet.py — Fake money, real accounting.

This is the "bank" of the paper trading bot. It holds fake USDT,
tracks positions, and calculates equity truthfully.

Honesty rules:
- Losing trades close at the REAL market price, never rounded up.
- Balance can go to zero. We don't prevent it, we show it.
- Every position tracks its real entry price, fees paid, and PnL.
"""

import time
import logging
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class Position:
    """
    A single open position.

    Plain English: this tracks one bet — how much you bought,
    at what price, and how it's doing right now.
    """

    def __init__(self, symbol: str, side: str, quantity: float,
                 entry_price: float, fee_paid: float, timestamp: float,
                 strategy_name: str = '', stop_loss: float | None = None,
                 take_profit: float | None = None, collateral: float = 0.0):
        self.symbol = symbol
        self.side = side              # 'long' or 'short'
        self.quantity = quantity       # how much you hold
        self.entry_price = entry_price  # real price you got in at (after slippage)
        self.fee_paid = fee_paid      # total fees paid so far on this position
        self.timestamp = timestamp    # when you entered
        self.strategy_name = strategy_name
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        # A 1x paper-futures short reserves collateral equal to the entry
        # notional.  Keeping it separate from available cash makes both cash
        # and equity correct without pretending borrowed assets are owned.
        self.collateral = collateral
        self.id = f"{symbol}_{side}_{int(timestamp * 1000)}"

    def unrealized_pnl(self, current_price: float) -> float:
        """
        How much you'd make or lose if you closed right now.
        This is always based on the REAL current price.
        """
        if self.side == 'long':
            pnl = (current_price - self.entry_price) * self.quantity
        else:  # short
            pnl = (self.entry_price - current_price) * self.quantity
        return pnl

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """PnL as a percentage of the entry value."""
        entry_value = self.entry_price * self.quantity
        if entry_value == 0:
            return 0.0
        return (self.unrealized_pnl(current_price) / entry_value) * 100

    def to_dict(self, current_price: float = None) -> dict:
        result = {
            'id': self.id,
            'symbol': self.symbol,
            'side': self.side,
            'quantity': self.quantity,
            'entry_price': self.entry_price,
            'fee_paid': self.fee_paid,
            'strategy_name': self.strategy_name,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'collateral': self.collateral,
            'timestamp': self.timestamp,
            'entry_time': datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc
            ).isoformat(),
        }
        if current_price is not None:
            result['current_price'] = current_price
            result['unrealized_pnl'] = self.unrealized_pnl(current_price)
            result['unrealized_pnl_pct'] = self.unrealized_pnl_pct(current_price)
        return result

    @classmethod
    def from_dict(cls, d: dict) -> 'Position':
        """Construct a Position object from a dictionary snapshot."""
        pos = cls(
            symbol=d['symbol'],
            side=d['side'],
            quantity=float(d['quantity']),
            entry_price=float(d['entry_price']),
            fee_paid=float(d.get('fee_paid', 0.0)),
            timestamp=float(d.get('timestamp', time.time())),
            strategy_name=d.get('strategy_name', ''),
            stop_loss=float(d['stop_loss']) if d.get('stop_loss') is not None else None,
            take_profit=float(d['take_profit']) if d.get('take_profit') is not None else None,
            collateral=float(d.get('collateral', 0.0)),
        )
        if 'id' in d:
            pos.id = d['id']
        return pos


class PaperWallet:
    """
    Fake money wallet that tracks real positions and honest PnL.

    Think of it like a practice account at a casino —
    the chips are fake but the game is real.
    """

    def __init__(self, config: dict):
        pt_cfg = config['paper_trading']
        self.starting_balance = pt_cfg['starting_balance']
        self.currency = pt_cfg['currency']

        # Current state
        self.cash = float(self.starting_balance)  # available cash
        self.positions: dict[str, Position] = {}  # open positions by ID
        self.closed_trades: list[dict] = []       # history of all closed trades
        self.peak_equity = float(self.starting_balance)

        # Stats
        self.total_fees_paid = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0

        logger.info(
            f"[WALLET] Initialized with ${self.starting_balance:.2f} "
            f"fake {self.currency}"
        )

    @property
    def equity(self) -> float:
        """
        Total value: cash + value of all open positions.
        This is NOT the same as balance — open trades can be
        winning or losing.
        """
        return self.cash + sum(
            p.quantity * p.entry_price if p.side == 'long' else p.collateral
            for p in self.positions.values()
        )

    def total_equity(self, prices: dict[str, float]) -> float:
        """
        True equity using current market prices.
        This is the honest number — what you'd have if you
        closed everything right now.
        """
        total = self.cash
        for pos in self.positions.values():
            price = prices.get(pos.symbol, pos.entry_price)
            if pos.side == 'long':
                total += pos.quantity * price
            else:
                total += pos.collateral + pos.unrealized_pnl(price)
        return total

    def get_drawdown(self, prices: dict[str, float]) -> float:
        """
        Current drawdown from peak equity, as a percentage.
        0% = at peak. 15% = dropped 15% from best.
        """
        current = self.total_equity(prices)
        if current > self.peak_equity:
            self.peak_equity = current
        if self.peak_equity == 0:
            return 0.0
        return ((self.peak_equity - current) / self.peak_equity) * 100

    def can_afford(self, amount: float) -> bool:
        """Check if we have enough cash for a trade."""
        return self.cash >= amount

    def open_position(self, symbol: str, side: str, quantity: float,
                      execution_price: float, fee: float,
                      strategy_name: str = '', stop_loss: float | None = None,
                      take_profit: float | None = None) -> Optional[Position]:
        """
        Open a new position.

        Longs debit notional plus fees. A short uses a conservative 1x margin
        model: entry notional is reserved as collateral and released with its
        realized PnL when the short is covered.

        Returns the Position if successful, None if we can't afford it.
        """
        cost = execution_price * quantity
        if side not in {'long', 'short'}:
            raise ValueError(f"Unsupported position side: {side}")

        # Require sufficient capital to reserve a full 1x short or buy a
        # long.  This prevents implicit leverage in the paper account.
        required_cash = cost + fee

        if not self.can_afford(required_cash):
            logger.warning(
                f"[WALLET] Cannot afford {side} {quantity} {symbol} "
                f"at ${execution_price:.2f} (need ${required_cash:.2f}, "
                f"have ${self.cash:.2f})"
            )
            return None

        collateral = 0.0
        if side == 'long':
            self.cash -= required_cash
        else:
            # Reserve collateral so open shorts cannot create implicit
            # leverage or be reused as buying power.
            collateral = cost
            self.cash -= required_cash
        self.total_fees_paid += fee

        pos = Position(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=execution_price,
            fee_paid=fee,
            timestamp=time.time(),
            strategy_name=strategy_name,
            stop_loss=stop_loss,
            take_profit=take_profit,
            collateral=collateral,
        )
        self.positions[pos.id] = pos

        logger.info(
            f"[WALLET] OPENED {side} {quantity:.6f} {symbol} "
            f"at ${execution_price:.2f} | Fee: ${fee:.4f} | "
            f"Cash left: ${self.cash:.2f}"
        )
        return pos

    def close_position(self, position_id: str, execution_price: float,
                       fee: float) -> Optional[dict]:
        """
        Close a position at the real market price.

        Honesty rule: the exit price is whatever the market says.
        If it's a loss, it's a loss. We never round or hide it.

        Returns a trade summary dict.
        """
        pos = self.positions.get(position_id)
        if pos is None:
            logger.warning(f"[WALLET] Position {position_id} not found")
            return None

        notional = execution_price * pos.quantity
        if pos.side == 'long':
            # Sell the owned asset and receive the proceeds less fees.
            self.cash += notional - fee
        else:
            # Release collateral and settle the short's realized PnL.
            self.cash += pos.collateral + (
                pos.entry_price - execution_price
            ) * pos.quantity - fee

        # Calculate real PnL
        if pos.side == 'long':
            gross_pnl = (execution_price - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - execution_price) * pos.quantity

        total_fees = pos.fee_paid + fee
        net_pnl = gross_pnl - total_fees
        self.total_fees_paid += fee
        self.total_trades += 1

        if net_pnl >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        # Build trade record
        trade = {
            'id': pos.id,
            'symbol': pos.symbol,
            'side': pos.side,
            'strategy_name': pos.strategy_name,
            'quantity': pos.quantity,
            'entry_price': pos.entry_price,
            'exit_price': execution_price,
            'entry_time': pos.timestamp,
            'exit_time': time.time(),
            'gross_pnl': gross_pnl,
            'total_fees': total_fees,
            'net_pnl': net_pnl,
            'net_pnl_pct': (net_pnl / (pos.entry_price * pos.quantity)) * 100,
            'balance_after': self.cash,
            'duration_seconds': time.time() - pos.timestamp,
        }

        self.closed_trades.append(trade)
        del self.positions[position_id]

        # Log honestly — wins and losses both shown
        emoji = "✅" if net_pnl >= 0 else "❌"
        logger.info(
            f"[WALLET] {emoji} CLOSED {pos.side} {pos.quantity:.6f} {pos.symbol} | "
            f"Entry: ${pos.entry_price:.2f} → Exit: ${execution_price:.2f} | "
            f"PnL: ${net_pnl:.4f} ({trade['net_pnl_pct']:.2f}%) | "
            f"Fees: ${total_fees:.4f} | Balance: ${self.cash:.2f}"
        )
        return trade

    def get_position_for_symbol(self, symbol: str,
                                 side: Optional[str] = None) -> Optional[Position]:
        """Find an open position for a symbol (optionally filtered by side)."""
        for pos in self.positions.values():
            if pos.symbol == symbol:
                if side is None or pos.side == side:
                    return pos
        return None

    def get_stats(self, prices: Optional[dict[str, float]] = None) -> dict:
        """Get wallet performance stats — honest numbers only."""
        win_rate = 0.0
        if self.total_trades > 0:
            win_rate = (self.winning_trades / self.total_trades) * 100

        avg_win = 0.0
        avg_loss = 0.0
        wins = [t['net_pnl'] for t in self.closed_trades if t['net_pnl'] > 0]
        losses = [t['net_pnl'] for t in self.closed_trades if t['net_pnl'] < 0]
        if wins:
            avg_win = sum(wins) / len(wins)
        if losses:
            avg_loss = abs(sum(losses) / len(losses))

        current_equity = self.total_equity(prices or {})
        return {
            'starting_balance': self.starting_balance,
            'current_cash': self.cash,
            'current_equity': current_equity,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate_pct': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'total_fees_paid': self.total_fees_paid,
            'net_return_pct': ((current_equity - self.starting_balance)
                               / self.starting_balance) * 100,
        }

    def to_dict(self, prices: dict[str, float] = None) -> dict:
        """Full wallet state as a dict — for dashboard and logging."""
        prices = prices or {}
        return {
            'cash': self.cash,
            'equity': self.total_equity(prices),
            'positions': [
                p.to_dict(prices.get(p.symbol))
                for p in self.positions.values()
            ],
            'drawdown_pct': self.get_drawdown(prices),
            'stats': self.get_stats(prices),
            'peak_equity': self.peak_equity,
            'total_fees_paid': self.total_fees_paid,
            'closed_trades': self.closed_trades,
        }

    def restore_state(self, state: dict):
        """Restore wallet state from a saved dictionary snapshot."""
        if 'cash' in state:
            self.cash = float(state['cash'])
        if 'peak_equity' in state:
            self.peak_equity = float(state['peak_equity'])
        if 'total_fees_paid' in state:
            self.total_fees_paid = float(state['total_fees_paid'])
        if 'total_trades' in state:
            self.total_trades = int(state['total_trades'])
        elif 'stats' in state and 'total_trades' in state['stats']:
            self.total_trades = int(state['stats']['total_trades'])
        if 'winning_trades' in state:
            self.winning_trades = int(state['winning_trades'])
        elif 'stats' in state and 'winning_trades' in state['stats']:
            self.winning_trades = int(state['stats']['winning_trades'])
        if 'losing_trades' in state:
            self.losing_trades = int(state['losing_trades'])
        elif 'stats' in state and 'losing_trades' in state['stats']:
            self.losing_trades = int(state['stats']['losing_trades'])
        if 'closed_trades' in state:
            self.closed_trades = list(state['closed_trades'])
        if 'positions' in state:
            self.positions = {}
            for p_data in state['positions']:
                pos = Position.from_dict(p_data)
                self.positions[pos.id] = pos
        logger.info(
            f"[WALLET] State restored: cash=${self.cash:.2f}, "
            f"open_positions={len(self.positions)}, total_trades={self.total_trades}"
        )


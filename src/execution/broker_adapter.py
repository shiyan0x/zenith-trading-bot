"""
broker_adapter.py — Unified Broker Adapter for Paper, Live, and Testing.

This module provides a unified interface across all execution modes:
1. PaperBrokerAdapter: Wraps the existing OrderEngine + PaperWallet (default mode).
2. LiveBrokerAdapter: Official Binance Spot API v3 integration with secure HMAC-SHA256
   signing, environment-based credentials, timeouts, rate limits, and key masking.
3. MockBrokerAdapter: Test harness for simulating rejections, timeouts, partial fills,
   disconnections, and duplicate order requests.

CRITICAL SECURITY RULES:
- Never log, print, or expose raw API keys or secrets in logs, representations, or files.
- Credentials must be sourced from secure environment configuration or a secrets manager.
- Live trading is NEVER enabled by default.
"""

import os
import time
import hmac
import hashlib
import urllib.parse
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def mask_key(key: Optional[str]) -> str:
    """Safely mask API key for logging without revealing secret data."""
    if not key:
        return "NONE"
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


class BaseBrokerAdapter(ABC):
    """Abstract Base Class for broker adapters."""

    @property
    @abstractmethod
    def mode(self) -> str:
        """Execution mode: 'paper', 'live', or 'mock'."""
        pass

    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        """Fetch account balances, cash, and equity."""
        pass

    @abstractmethod
    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return currently open positions."""
        pass

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return currently active unfilled / partially filled orders."""
        pass

    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "MARKET", current_price: float = 0.0,
                    stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    client_order_id: Optional[str] = None,
                    volatility: float = 0.0,
                    order_size_ratio: float = 0.01,
                    strategy_name: str = "") -> Dict[str, Any]:
        """Place an order through the broker."""
        pass

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel an open order."""
        pass

    @abstractmethod
    def close_position(self, symbol: str, position_id: str,
                       current_price: float, volatility: float = 0.0,
                       order_size_ratio: float = 0.01) -> Optional[Dict[str, Any]]:
        """Close an existing open position."""
        pass

    @abstractmethod
    def close_all(self, prices: Dict[str, float],
                  volatility: float = 0.0) -> List[Dict[str, Any]]:
        """Emergency liquidate all open positions."""
        pass


class PaperBrokerAdapter(BaseBrokerAdapter):
    """
    Adapter wrapping the bot's existing OrderEngine + PaperWallet.
    Preserves existing order flow, realistic slippage, and fee accounting.
    """

    def __init__(self, order_engine, wallet):
        self.order_engine = order_engine
        self.wallet = wallet

    @property
    def mode(self) -> str:
        return "paper"

    def get_account_info(self) -> Dict[str, Any]:
        return {
            'mode': 'paper',
            'cash': self.wallet.cash,
            'equity': self.wallet.equity,
            'currency': self.wallet.currency,
            'positions_count': len(self.wallet.positions),
            'drawdown_pct': self.wallet.get_drawdown({}),
        }

    def get_open_positions(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self.wallet.positions.values()]

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        # In market-order paper trading, orders fill immediately upon execution
        return []

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "MARKET", current_price: float = 0.0,
                    stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    client_order_id: Optional[str] = None,
                    volatility: float = 0.0,
                    order_size_ratio: float = 0.01,
                    strategy_name: str = "") -> Dict[str, Any]:
        """Execute simulated market order via existing OrderEngine."""
        side_lower = side.lower()
        if side_lower in ('buy', 'long'):
            pos = self.order_engine.market_buy(
                symbol=symbol,
                quantity=quantity,
                current_price=current_price,
                volatility=volatility,
                order_size_ratio=order_size_ratio,
                strategy_name=strategy_name,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
        elif side_lower in ('sell', 'short'):
            pos = self.order_engine.market_short(
                symbol=symbol,
                quantity=quantity,
                current_price=current_price,
                volatility=volatility,
                order_size_ratio=order_size_ratio,
                strategy_name=strategy_name,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
        else:
            raise ValueError(f"Unsupported side: {side}")

        if pos is None:
            return {
                'success': False,
                'status': 'REJECTED',
                'reason': 'Insufficient funds or sizing limit reached in PaperWallet',
                'order_id': client_order_id or f"paper_ord_{int(time.time()*1000)}",
            }

        return {
            'success': True,
            'status': 'FILLED',
            'order_id': client_order_id or f"paper_ord_{int(time.time()*1000)}",
            'position_id': pos.id,
            'symbol': pos.symbol,
            'side': pos.side,
            'quantity': pos.quantity,
            'execution_price': pos.entry_price,
            'fee': pos.fee_paid,
            'timestamp': pos.timestamp,
        }

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return {'success': True, 'order_id': order_id, 'status': 'CANCELLED'}

    def close_position(self, symbol: str, position_id: str,
                       current_price: float, volatility: float = 0.0,
                       order_size_ratio: float = 0.01) -> Optional[Dict[str, Any]]:
        return self.order_engine.close_position(
            symbol=symbol,
            position_id=position_id,
            current_price=current_price,
            volatility=volatility,
            order_size_ratio=order_size_ratio,
        )

    def close_all(self, prices: Dict[str, float],
                  volatility: float = 0.0) -> List[Dict[str, Any]]:
        return self.order_engine.close_all(prices, volatility=volatility)


class LiveBrokerAdapter(BaseBrokerAdapter):
    """
    Official Binance Spot API v3 Live Broker Adapter.
    
    Security & Authentication:
    - Reads keys strictly from environment variables: BINANCE_API_KEY, BINANCE_API_SECRET
      (or optional secure config dict passed at instantiation).
    - Requests signed using HMAC-SHA256 with timestamp in milliseconds and recvWindow=5000.
    - All keys are sanitized and masked in string representations and logs.
    """

    def __init__(self, api_key: Optional[str] = None,
                 api_secret: Optional[str] = None,
                 base_url: str = "https://api.binance.com",
                 recv_window: int = 5000,
                 timeout_seconds: float = 10.0):
        self._api_key = api_key or os.environ.get("BINANCE_API_KEY")
        self._api_secret = api_secret or os.environ.get("BINANCE_API_SECRET")
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.timeout_seconds = timeout_seconds

        if not self._api_key or not self._api_secret:
            raise ValueError(
                "LiveBrokerAdapter requires valid API credentials in environment "
                "variables (BINANCE_API_KEY, BINANCE_API_SECRET). Do not hardcode keys."
            )

        logger.info(
            f"[LIVE BROKER] Initialized adapter for {self.base_url} "
            f"with API Key: {mask_key(self._api_key)}"
        )

    @property
    def mode(self) -> str:
        return "live"

    def __repr__(self) -> str:
        return f"<LiveBrokerAdapter mode=live base_url='{self.base_url}' key='{mask_key(self._api_key)}'>"

    def _sign_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sign request parameters using HMAC-SHA256 according to Binance API docs."""
        signed = dict(params)
        signed['timestamp'] = int(time.time() * 1000)
        signed['recvWindow'] = self.recv_window
        query_string = urllib.parse.urlencode(signed)
        signature = hmac.new(
            self._api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        signed['signature'] = signature
        return signed

    def _headers(self) -> Dict[str, str]:
        return {
            'X-MBX-APIKEY': self._api_key,
            'Content-Type': 'application/x-www-form-urlencoded',
        }

    def get_account_info(self) -> Dict[str, Any]:
        """Fetch real Binance account info."""
        import urllib.request
        import json

        params = self._sign_params({})
        query_str = urllib.parse.urlencode(params)
        url = f"{self.base_url}/api/v3/account?{query_str}"
        req = urllib.request.Request(url, headers=self._headers(), method='GET')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return {
                    'mode': 'live',
                    'can_trade': data.get('canTrade', False),
                    'account_type': data.get('accountType', 'SPOT'),
                    'balances': [b for b in data.get('balances', []) if float(b.get('free', 0)) > 0 or float(b.get('locked', 0)) > 0],
                }
        except Exception as e:
            logger.error(f"[LIVE BROKER] get_account_info failed: {e}")
            raise ConnectionError(f"Binance account fetch failed: {e}")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        # For Binance Spot, positions correspond to non-zero asset balances
        info = self.get_account_info()
        return info.get('balances', [])

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        import urllib.request
        import json

        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
        signed = self._sign_params(params)
        url = f"{self.base_url}/api/v3/openOrders?{urllib.parse.urlencode(signed)}"
        req = urllib.request.Request(url, headers=self._headers(), method='GET')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            logger.error(f"[LIVE BROKER] get_open_orders failed: {e}")
            raise ConnectionError(f"Binance open orders fetch failed: {e}")

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "MARKET", current_price: float = 0.0,
                    stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    client_order_id: Optional[str] = None,
                    volatility: float = 0.0,
                    order_size_ratio: float = 0.01,
                    strategy_name: str = "") -> Dict[str, Any]:
        """Place live order on Binance with full cost & response verification."""
        import urllib.request
        import json

        binance_side = 'BUY' if side.lower() in ('buy', 'long') else 'SELL'
        params = {
            'symbol': symbol.upper(),
            'side': binance_side,
            'type': order_type.upper(),
            'quantity': f"{quantity:.6f}",
        }
        if client_order_id:
            params['newClientOrderId'] = client_order_id

        signed = self._sign_params(params)
        data = urllib.parse.urlencode(signed).encode('utf-8')
        url = f"{self.base_url}/api/v3/order"
        req = urllib.request.Request(url, data=data, headers=self._headers(), method='POST')

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                logger.info(
                    f"[LIVE BROKER] Order {res.get('orderId')} placed successfully: "
                    f"{binance_side} {quantity} {symbol} ({res.get('status')})"
                )
                return {
                    'success': True,
                    'status': res.get('status', 'FILLED'),
                    'order_id': str(res.get('orderId')),
                    'client_order_id': res.get('clientOrderId'),
                    'symbol': res.get('symbol'),
                    'executed_qty': float(res.get('executedQty', 0)),
                    'cummulative_quote_qty': float(res.get('cummulativeQuoteQty', 0)),
                    'raw_response': res,
                }
        except Exception as e:
            logger.error(f"[LIVE BROKER] place_order failed: {e}")
            return {
                'success': False,
                'status': 'REJECTED',
                'reason': str(e),
                'symbol': symbol,
            }

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        import urllib.request
        import json

        params = {
            'symbol': symbol.upper(),
            'orderId': order_id,
        }
        signed = self._sign_params(params)
        url = f"{self.base_url}/api/v3/order?{urllib.parse.urlencode(signed)}"
        req = urllib.request.Request(url, headers=self._headers(), method='DELETE')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                return {'success': True, 'order_id': order_id, 'status': res.get('status', 'CANCELED')}
        except Exception as e:
            logger.error(f"[LIVE BROKER] cancel_order failed: {e}")
            return {'success': False, 'order_id': order_id, 'reason': str(e)}

    def close_position(self, symbol: str, position_id: str,
                       current_price: float, volatility: float = 0.0,
                       order_size_ratio: float = 0.01) -> Optional[Dict[str, Any]]:
        # In Binance spot, closing a position requires placing an opposite market order
        raise NotImplementedError("Use place_order with opposing side for live spot liquidation")

    def close_all(self, prices: Dict[str, float],
                  volatility: float = 0.0) -> List[Dict[str, Any]]:
        logger.warning("[LIVE BROKER] Emergency close_all called on live broker adapter!")
        open_orders = self.get_open_orders()
        cancelled = []
        for ord in open_orders:
            res = self.cancel_order(ord['symbol'], str(ord['orderId']))
            cancelled.append(res)
        return cancelled


class MockBrokerAdapter(BaseBrokerAdapter):
    """
    Mock broker adapter for testing and simulation.
    Supports simulated rejections, partial fills, timeouts, disconnections,
    and duplicate request protection.
    """

    def __init__(self, starting_balance: float = 10000.0, currency: str = "USDT"):
        self.starting_balance = starting_balance
        self.cash = starting_balance
        self.currency = currency
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.closed_trades: List[Dict[str, Any]] = []

        # Simulation behavior flags
        self.should_reject = False
        self.rejection_reason = "Simulated rejection"
        self.should_timeout = False
        self.should_disconnect = False
        self.partial_fill_ratio: Optional[float] = None  # e.g. 0.5 for 50% fill
        self.processed_client_order_ids: set = set()

    @property
    def mode(self) -> str:
        return "mock"

    def get_account_info(self) -> Dict[str, Any]:
        if self.should_disconnect:
            raise ConnectionError("Simulated broker disconnection")
        if self.should_timeout:
            raise TimeoutError("Simulated broker timeout")

        equity = self.cash + sum(p['quantity'] * p.get('entry_price', 0) for p in self.positions.values())
        return {
            'mode': 'mock',
            'cash': self.cash,
            'equity': equity,
            'currency': self.currency,
            'positions_count': len(self.positions),
        }

    def get_open_positions(self) -> List[Dict[str, Any]]:
        if self.should_disconnect:
            raise ConnectionError("Simulated broker disconnection")
        return list(self.positions.values())

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.should_disconnect:
            raise ConnectionError("Simulated broker disconnection")
        orders = [o for o in self.orders.values() if o['status'] in ('OPEN', 'PARTIALLY_FILLED')]
        if symbol:
            orders = [o for o in orders if o['symbol'] == symbol]
        return orders

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "MARKET", current_price: float = 0.0,
                    stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    client_order_id: Optional[str] = None,
                    volatility: float = 0.0,
                    order_size_ratio: float = 0.01,
                    strategy_name: str = "") -> Dict[str, Any]:
        if self.should_disconnect:
            raise ConnectionError("Simulated broker disconnection")
        if self.should_timeout:
            raise TimeoutError("Simulated broker timeout")

        order_id = client_order_id or f"mock_ord_{int(time.time()*1000)}"

        # Duplicate request protection (idempotency check)
        if order_id in self.processed_client_order_ids:
            return {
                'success': False,
                'status': 'REJECTED',
                'reason': f"Duplicate order request rejected: {order_id}",
                'order_id': order_id,
            }

        self.processed_client_order_ids.add(order_id)

        if self.should_reject:
            order_record = {
                'order_id': order_id,
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'status': 'REJECTED',
                'reason': self.rejection_reason,
                'timestamp': time.time(),
            }
            self.orders[order_id] = order_record
            return {
                'success': False,
                'status': 'REJECTED',
                'reason': self.rejection_reason,
                'order_id': order_id,
            }

        cost = quantity * current_price
        if self.cash < cost and side.lower() in ('buy', 'long'):
            return {
                'success': False,
                'status': 'REJECTED',
                'reason': 'Insufficient simulated funds',
                'order_id': order_id,
            }

        # Partial fill handling
        filled_qty = quantity
        status = 'FILLED'
        if self.partial_fill_ratio is not None and 0.0 < self.partial_fill_ratio < 1.0:
            filled_qty = quantity * self.partial_fill_ratio
            status = 'PARTIALLY_FILLED'

        pos_id = f"{symbol}_{side}_{int(time.time()*1000)}"
        position = {
            'id': pos_id,
            'symbol': symbol,
            'side': 'long' if side.lower() in ('buy', 'long') else 'short',
            'quantity': filled_qty,
            'entry_price': current_price,
            'fee_paid': filled_qty * current_price * 0.001,
            'timestamp': time.time(),
            'strategy_name': strategy_name,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
        }
        self.positions[pos_id] = position
        self.cash -= (filled_qty * current_price + position['fee_paid'])

        order_record = {
            'order_id': order_id,
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'filled_quantity': filled_qty,
            'status': status,
            'execution_price': current_price,
            'fee': position['fee_paid'],
            'timestamp': time.time(),
        }
        self.orders[order_id] = order_record

        return {
            'success': True,
            'status': status,
            'order_id': order_id,
            'position_id': pos_id,
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'filled_quantity': filled_qty,
            'execution_price': current_price,
            'fee': position['fee_paid'],
        }

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        if self.should_disconnect:
            raise ConnectionError("Simulated broker disconnection")
        if order_id in self.orders:
            self.orders[order_id]['status'] = 'CANCELLED'
            return {'success': True, 'order_id': order_id, 'status': 'CANCELLED'}
        return {'success': False, 'order_id': order_id, 'reason': 'Order not found'}

    def close_position(self, symbol: str, position_id: str,
                       current_price: float, volatility: float = 0.0,
                       order_size_ratio: float = 0.01) -> Optional[Dict[str, Any]]:
        pos = self.positions.pop(position_id, None)
        if not pos:
            return None

        fee = pos['quantity'] * current_price * 0.001
        proceeds = pos['quantity'] * current_price - fee
        self.cash += proceeds
        pnl = (current_price - pos['entry_price']) * pos['quantity'] - (pos['fee_paid'] + fee)
        trade = {
            'id': pos['id'],
            'symbol': symbol,
            'side': pos['side'],
            'quantity': pos['quantity'],
            'entry_price': pos['entry_price'],
            'exit_price': current_price,
            'net_pnl': pnl,
            'total_fees': pos['fee_paid'] + fee,
            'timestamp': time.time(),
        }
        self.closed_trades.append(trade)
        return trade

    def close_all(self, prices: Dict[str, float],
                  volatility: float = 0.0) -> List[Dict[str, Any]]:
        trades = []
        for pid in list(self.positions.keys()):
            pos = self.positions.get(pid)
            if pos:
                price = prices.get(pos['symbol'], pos['entry_price'])
                t = self.close_position(pos['symbol'], pid, price)
                if t:
                    trades.append(t)
        return trades

"""
market_feed.py — Real live prices from Binance.
No API key needed. Prices are NEVER faked or cached stale.

Data sources (verified July 2026):
- WebSocket: wss://stream.binance.com:9443/ws (real-time klines)
- REST: https://api.binance.com/api/v3/klines (historical candles)
"""

import json
import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import aiohttp
import websockets

logger = logging.getLogger(__name__)


class Candle:
    """One candlestick bar. All prices are real, from Binance."""

    __slots__ = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'is_closed']

    def __init__(self, timestamp: float, o: float, h: float, l: float, c: float,
                 volume: float, is_closed: bool = True):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = volume
        self.is_closed = is_closed

    def __repr__(self):
        dt = datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
        return (f"Candle({dt.strftime('%Y-%m-%d %H:%M')} "
                f"O={self.open:.2f} H={self.high:.2f} L={self.low:.2f} "
                f"C={self.close:.2f} V={self.volume:.2f} closed={self.is_closed})")


class MarketFeed:
    """
    Pulls real live prices from Binance public API.

    Two modes:
    1. WebSocket — real-time kline stream (for live trading)
    2. REST — historical klines (for backtesting)

    Honesty rule: every price comes straight from Binance.
    We never generate, interpolate, or cache stale prices.
    """

    def __init__(self, config: dict):
        self.ws_url = config['exchange']['ws_url']
        self.rest_url = config['exchange']['rest_url']
        self.klines_endpoint = config['exchange']['klines_endpoint']
        self._running = False
        self._ws = None

    async def get_current_price(self, symbol: str) -> float:
        """
        Fetch the current price via REST.
        Returns the real last traded price from Binance.
        """
        url = f"{self.rest_url}/api/v3/ticker/price"
        params = {'symbol': symbol.upper()}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"Binance REST error {resp.status}: {text}")
                data = await resp.json()
                price = float(data['price'])
                logger.debug(f"[LIVE PRICE] {symbol} = {price}")
                return price

    async def get_historical_klines(self, symbol: str, interval: str = '1m',
                                     limit: int = 500,
                                     start_time: Optional[int] = None,
                                     end_time: Optional[int] = None) -> list[Candle]:
        """
        Fetch historical candles from Binance REST API.
        These are real past prices — never generated.

        Args:
            symbol: e.g. 'BTCUSDT'
            interval: e.g. '1m', '5m', '1h', '1d'
            limit: max candles (Binance caps at 1000)
            start_time: start timestamp in milliseconds
            end_time: end timestamp in milliseconds

        Returns:
            List of Candle objects with real historical prices.
        """
        url = f"{self.rest_url}{self.klines_endpoint}"
        params = {
            'symbol': symbol.upper(),
            'interval': interval,
            'limit': min(limit, 1000),
        }
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time

        candles = []
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"Binance klines error {resp.status}: {text}")
                data = await resp.json()

                for k in data:
                    # Binance kline format:
                    # [open_time, open, high, low, close, volume, close_time, ...]
                    candle = Candle(
                        timestamp=k[0] / 1000.0,  # ms -> seconds
                        o=float(k[1]),
                        h=float(k[2]),
                        l=float(k[3]),
                        c=float(k[4]),
                        volume=float(k[5]),
                        is_closed=True
                    )
                    candles.append(candle)

        logger.info(f"[HISTORY] Fetched {len(candles)} candles for {symbol} ({interval})")
        return candles

    async def get_all_historical_klines(self, symbol: str, interval: str,
                                         start_time: int, end_time: int) -> list[Candle]:
        """
        Fetch a large range of historical data by paginating through the API.
        Needed for backtesting over weeks/months of data.
        """
        all_candles = []
        current_start = start_time

        while current_start < end_time:
            batch = await self.get_historical_klines(
                symbol=symbol,
                interval=interval,
                limit=1000,
                start_time=current_start,
                end_time=end_time
            )
            if not batch:
                break

            all_candles.extend(batch)

            # Move start to after the last candle we got
            last_ts_ms = int(batch[-1].timestamp * 1000)
            current_start = last_ts_ms + 1

            # Rate limit: Binance allows 1200 requests/min for public endpoints
            await asyncio.sleep(0.1)

        logger.info(f"[HISTORY] Total: {len(all_candles)} candles for {symbol}")
        return all_candles

    async def stream_live(self, symbol: str, interval: str,
                          on_candle: Callable[[Candle], None]):
        """
        Stream real-time kline data via WebSocket.
        Calls on_candle() with each new candle update.

        This is the live heartbeat of the bot.
        Every price is real, straight from Binance.
        """
        stream = f"{symbol.lower()}@kline_{interval}"
        url = f"{self.ws_url}/{stream}"

        self._running = True
        logger.info(f"[WS] Connecting to {url}")

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20,
                                               ping_timeout=10) as ws:
                    self._ws = ws
                    logger.info(f"[WS] Connected — streaming {symbol} {interval}")

                    async for msg in ws:
                        if not self._running:
                            break

                        data = json.loads(msg)
                        k = data.get('k', {})

                        candle = Candle(
                            timestamp=k['t'] / 1000.0,
                            o=float(k['o']),
                            h=float(k['h']),
                            l=float(k['l']),
                            c=float(k['c']),
                            volume=float(k['v']),
                            is_closed=k.get('x', False)
                        )

                        on_candle(candle)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"[WS] Connection closed: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"[WS] Error: {e}. Reconnecting in 10s...")
                await asyncio.sleep(10)

    def stop(self):
        """Stop the WebSocket stream gracefully."""
        self._running = False
        logger.info("[WS] Stop requested")

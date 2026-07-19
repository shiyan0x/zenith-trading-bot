"""
news_feed.py — Free crypto news from RSS feeds.

Fetches real headlines from CoinDesk, CoinTelegraph, and Decrypt
using their public RSS feeds. Zero API keys, zero cost.

Data sources (verified July 2026):
- CoinDesk:      https://www.coindesk.com/arc/outboundfeeds/rss/
- CoinTelegraph: https://cointelegraph.com/rss
- Decrypt:       https://decrypt.co/feed
"""

import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)


class NewsItem:
    """A single news headline with metadata."""

    __slots__ = ['title', 'source', 'url', 'published', 'published_ts',
                 'sentiment_score', 'sentiment_label']

    def __init__(self, title: str, source: str, url: str = '',
                 published: str = '', published_ts: float = 0.0):
        self.title = title
        self.source = source
        self.url = url
        self.published = published
        self.published_ts = published_ts or time.time()
        self.sentiment_score = 0.0    # set by SentimentAnalyzer
        self.sentiment_label = 'neutral'  # set by SentimentAnalyzer

    def to_dict(self) -> dict:
        return {
            'title': self.title,
            'source': self.source,
            'url': self.url,
            'published': self.published,
            'published_ts': self.published_ts,
            'sentiment_score': self.sentiment_score,
            'sentiment_label': self.sentiment_label,
            'age_minutes': max(0, (time.time() - self.published_ts) / 60),
        }

    def __repr__(self):
        return f"NewsItem({self.source}: {self.title[:50]}...)"


# ─── RSS Feed Sources ───
RSS_FEEDS = [
    {
        'name': 'CoinDesk',
        'url': 'https://www.coindesk.com/arc/outboundfeeds/rss/',
        'item_tag': 'item',
    },
    {
        'name': 'CoinTelegraph',
        'url': 'https://cointelegraph.com/rss',
        'item_tag': 'item',
    },
    {
        'name': 'Decrypt',
        'url': 'https://decrypt.co/feed',
        'item_tag': 'item',
    },
]


def _parse_pub_date(date_str: str) -> float:
    """
    Parse RSS pubDate to Unix timestamp.
    RSS dates are typically RFC 822: 'Mon, 14 Jul 2026 12:00:00 +0000'
    """
    if not date_str:
        return time.time()

    # Common RSS date formats
    formats = [
        '%a, %d %b %Y %H:%M:%S %z',      # RFC 822 with timezone
        '%a, %d %b %Y %H:%M:%S GMT',      # RFC 822 GMT
        '%Y-%m-%dT%H:%M:%S%z',            # ISO 8601
        '%Y-%m-%dT%H:%M:%SZ',             # ISO 8601 UTC
        '%a, %d %b %Y %H:%M:%S %Z',       # RFC 822 with TZ name
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue

    # Fallback: use current time
    return time.time()


class NewsFeed:
    """
    Fetches crypto news from free RSS feeds.

    No API keys. No cost. Just public RSS endpoints.
    Caches results to avoid hammering the servers.
    """

    def __init__(self, config: dict):
        news_cfg = config.get('news', {})
        self.enabled = news_cfg.get('enabled', True)
        self.fetch_interval = news_cfg.get('fetch_interval_minutes', 5) * 60
        self.max_headlines = news_cfg.get('max_headlines', 20)

        # Cache
        self._cached_items: list[NewsItem] = []
        self._last_fetch_time: float = 0
        self._fetching = False

        logger.info(
            f"[NEWS] Initialized — fetch every {self.fetch_interval // 60} min, "
            f"max {self.max_headlines} headlines"
        )

    async def _fetch_feed(self, feed: dict,
                          session: aiohttp.ClientSession) -> list[NewsItem]:
        """Fetch and parse a single RSS feed."""
        items = []
        try:
            async with session.get(
                feed['url'],
                timeout=aiohttp.ClientTimeout(total=15),
                headers={'User-Agent': 'ZenithTradingBot/1.0'}
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[NEWS] {feed['name']} returned {resp.status}"
                    )
                    return items

                text = await resp.text()
                root = ElementTree.fromstring(text)

                # Find all items — handle both RSS 2.0 and Atom
                channel = root.find('channel')
                if channel is not None:
                    elements = channel.findall(feed['item_tag'])
                else:
                    elements = root.findall(
                        f".//{feed['item_tag']}"
                    )

                for elem in elements[:15]:  # max 15 per source
                    title_el = elem.find('title')
                    link_el = elem.find('link')
                    pub_el = elem.find('pubDate')

                    title = title_el.text.strip() if (
                        title_el is not None and title_el.text
                    ) else ''
                    link = link_el.text.strip() if (
                        link_el is not None and link_el.text
                    ) else ''
                    pub_date = pub_el.text.strip() if (
                        pub_el is not None and pub_el.text
                    ) else ''

                    if not title:
                        continue

                    items.append(NewsItem(
                        title=title,
                        source=feed['name'],
                        url=link,
                        published=pub_date,
                        published_ts=_parse_pub_date(pub_date),
                    ))

                logger.debug(
                    f"[NEWS] {feed['name']}: fetched {len(items)} headlines"
                )

        except ElementTree.ParseError as e:
            logger.warning(f"[NEWS] {feed['name']} XML parse error: {e}")
        except asyncio.TimeoutError:
            logger.warning(f"[NEWS] {feed['name']} timed out")
        except Exception as e:
            logger.warning(f"[NEWS] {feed['name']} error: {e}")

        return items

    async def fetch_news(self) -> list[NewsItem]:
        """
        Fetch news from all RSS sources.

        Respects the cache interval — won't re-fetch if called
        too frequently. Returns cached items if still fresh.
        """
        if not self.enabled:
            return []

        # Check cache freshness
        now = time.time()
        if (now - self._last_fetch_time < self.fetch_interval
                and self._cached_items):
            return self._cached_items

        # Prevent concurrent fetches
        if self._fetching:
            return self._cached_items
        self._fetching = True

        all_items = []
        try:
            async with aiohttp.ClientSession() as session:
                tasks = [
                    self._fetch_feed(feed, session)
                    for feed in RSS_FEEDS
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, list):
                        all_items.extend(result)
                    elif isinstance(result, Exception):
                        logger.warning(f"[NEWS] Feed error: {result}")

            # Sort by publish time (newest first) and limit
            all_items.sort(key=lambda x: x.published_ts, reverse=True)
            all_items = all_items[:self.max_headlines]

            self._cached_items = all_items
            self._last_fetch_time = now

            logger.info(
                f"[NEWS] Fetched {len(all_items)} headlines from "
                f"{len(RSS_FEEDS)} sources"
            )

        except Exception as e:
            logger.error(f"[NEWS] Fetch error: {e}")
        finally:
            self._fetching = False

        return self._cached_items

    def get_cached_news(self) -> list[NewsItem]:
        """Return cached news without fetching. For dashboard reads."""
        return self._cached_items

    def get_news_dicts(self) -> list[dict]:
        """Return cached news as list of dicts — for JSON API."""
        return [item.to_dict() for item in self._cached_items]

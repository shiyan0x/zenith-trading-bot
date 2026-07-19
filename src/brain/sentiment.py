"""
sentiment.py — Keyword-based sentiment analysis for crypto news.

No LLM, no API keys, no cost. Just pattern matching against
curated lists of bullish and bearish keywords.

How it works:
1. Each headline is scored from -1.0 (very bearish) to +1.0 (very bullish)
2. Keywords are weighted: stronger signals get higher weights
3. Overall market sentiment = weighted average of recent headlines
4. Classification: bullish (> +0.2), bearish (< -0.2), neutral (in between)

Honest disclaimer: keyword sentiment is ~60-65% accurate.
It's good as a trade filter, not a standalone strategy.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Keyword Dictionaries ───
# Each entry: (keyword_or_phrase, weight)
# Weight ranges: 0.1 (weak signal) to 1.0 (very strong signal)

BULLISH_KEYWORDS = [
    # Strong bullish (0.7 - 1.0)
    ('etf approved', 1.0),
    ('etf approval', 1.0),
    ('record high', 0.9),
    ('all-time high', 0.9),
    ('all time high', 0.9),
    ('ath', 0.8),
    ('mass adoption', 0.8),
    ('institutional adoption', 0.8),
    ('bull run', 0.9),
    ('bull market', 0.8),

    # Medium bullish (0.4 - 0.6)
    ('surge', 0.6),
    ('surges', 0.6),
    ('soar', 0.6),
    ('soars', 0.6),
    ('rally', 0.6),
    ('rallies', 0.6),
    ('breakout', 0.5),
    ('breaks out', 0.5),
    ('bullish', 0.5),
    ('moon', 0.4),
    ('pump', 0.4),
    ('upgrade', 0.4),
    ('upgraded', 0.4),
    ('partnership', 0.4),
    ('adoption', 0.4),
    ('launch', 0.3),
    ('launches', 0.3),
    ('milestone', 0.4),
    ('profit', 0.3),
    ('gains', 0.3),
    ('rises', 0.4),
    ('jumps', 0.5),
    ('spikes', 0.5),
    ('approval', 0.5),
    ('approved', 0.5),
    ('growth', 0.3),
    ('inflow', 0.4),
    ('inflows', 0.4),
    ('accumulation', 0.4),
    ('buying', 0.3),
    ('recovery', 0.4),
    ('recovers', 0.4),
    ('rebounds', 0.5),
    ('rebound', 0.5),

    # Weak bullish (0.1 - 0.3)
    ('positive', 0.2),
    ('optimistic', 0.3),
    ('support', 0.2),
    ('uptrend', 0.3),
    ('bullish sentiment', 0.4),
    ('green', 0.1),
    ('investment', 0.2),
]

BEARISH_KEYWORDS = [
    # Strong bearish (0.7 - 1.0)
    ('hack', 0.9),
    ('hacked', 0.9),
    ('exploit', 0.8),
    ('exploited', 0.8),
    ('rug pull', 1.0),
    ('rugpull', 1.0),
    ('scam', 0.8),
    ('fraud', 0.9),
    ('ponzi', 0.9),
    ('sec lawsuit', 0.9),
    ('sec charges', 0.9),
    ('sec sues', 0.9),
    ('ban', 0.8),
    ('banned', 0.8),
    ('crackdown', 0.8),
    ('collapse', 0.9),
    ('collapses', 0.9),
    ('bankrupt', 0.9),
    ('bankruptcy', 0.9),
    ('insolvent', 0.9),

    # Medium bearish (0.4 - 0.6)
    ('crash', 0.7),
    ('crashes', 0.7),
    ('plunge', 0.6),
    ('plunges', 0.6),
    ('plummets', 0.6),
    ('dump', 0.5),
    ('dumps', 0.5),
    ('sell-off', 0.6),
    ('selloff', 0.6),
    ('bearish', 0.5),
    ('bear market', 0.7),
    ('liquidation', 0.6),
    ('liquidated', 0.6),
    ('investigation', 0.5),
    ('regulatory', 0.3),
    ('regulation', 0.3),
    ('fine', 0.4),
    ('fined', 0.4),
    ('penalty', 0.4),
    ('lawsuit', 0.5),
    ('sued', 0.5),
    ('outflow', 0.4),
    ('outflows', 0.4),
    ('decline', 0.4),
    ('declines', 0.4),
    ('drops', 0.4),
    ('falls', 0.4),
    ('tumbles', 0.5),
    ('sinks', 0.5),
    ('slumps', 0.5),
    ('warning', 0.3),
    ('fear', 0.4),
    ('panic', 0.6),
    ('concern', 0.2),
    ('vulnerability', 0.5),
    ('breach', 0.6),

    # Weak bearish (0.1 - 0.3)
    ('negative', 0.2),
    ('risk', 0.1),
    ('uncertainty', 0.2),
    ('volatile', 0.1),
    ('downtrend', 0.3),
    ('resistance', 0.1),
    ('correction', 0.3),
    ('dip', 0.2),
]

# Crypto-specific terms that amplify sentiment
CRYPTO_TERMS = [
    'bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'blockchain',
    'defi', 'nft', 'altcoin', 'stablecoin', 'binance', 'coinbase',
    'solana', 'sol', 'xrp', 'cardano', 'ada', 'polygon', 'matic',
    'token', 'web3', 'mining', 'halving',
]


class SentimentAnalyzer:
    """
    Analyzes news headlines for bullish/bearish sentiment.

    Keyword-based — fast, free, no external dependencies.
    Not as accurate as an LLM, but good enough as a trade filter.
    """

    def __init__(self, config: dict):
        news_cfg = config.get('news', {})
        self.bearish_threshold = news_cfg.get('bearish_threshold', -0.3)
        self.block_on_bearish = news_cfg.get('block_on_bearish', True)

        # Pre-compile keyword patterns for efficiency
        self._bullish = [
            (re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE), w)
            for kw, w in BULLISH_KEYWORDS
        ]
        self._bearish = [
            (re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE), w)
            for kw, w in BEARISH_KEYWORDS
        ]
        self._crypto_pattern = re.compile(
            r'\b(' + '|'.join(re.escape(t) for t in CRYPTO_TERMS) + r')\b',
            re.IGNORECASE
        )

        self._overall_score = 0.0
        self._overall_label = 'neutral'

        logger.info(
            f"[SENTIMENT] Initialized — bearish threshold: "
            f"{self.bearish_threshold}, block: {self.block_on_bearish}"
        )

    def score_headline(self, headline: str) -> tuple[float, str]:
        """
        Score a single headline.

        Returns:
            (score, label) where score is -1.0 to +1.0
            and label is 'bullish', 'bearish', or 'neutral'
        """
        if not headline:
            return 0.0, 'neutral'

        bull_score = 0.0
        bear_score = 0.0

        # Check bullish keywords
        for pattern, weight in self._bullish:
            if pattern.search(headline):
                bull_score += weight

        # Check bearish keywords
        for pattern, weight in self._bearish:
            if pattern.search(headline):
                bear_score += weight

        # Boost if headline mentions crypto specifically
        is_crypto = bool(self._crypto_pattern.search(headline))
        if is_crypto:
            bull_score *= 1.2
            bear_score *= 1.2

        # Calculate net score
        if bull_score == 0 and bear_score == 0:
            return 0.0, 'neutral'

        # Normalize to -1.0 to +1.0 range
        raw_score = bull_score - bear_score
        max_possible = max(bull_score + bear_score, 1.0)
        score = max(-1.0, min(1.0, raw_score / max_possible))

        # Classify
        if score > 0.2:
            label = 'bullish'
        elif score < -0.2:
            label = 'bearish'
        else:
            label = 'neutral'

        return round(score, 3), label

    def analyze_news(self, news_items: list) -> dict:
        """
        Analyze a list of NewsItem objects.

        Scores each headline and computes an overall market sentiment.
        More recent headlines get higher weight.

        Returns a dict with overall sentiment info.
        """
        if not news_items:
            self._overall_score = 0.0
            self._overall_label = 'neutral'
            return self.get_summary()

        import time as _time
        now = _time.time()
        weighted_scores = []
        total_weight = 0.0

        for item in news_items:
            score, label = self.score_headline(item.title)
            item.sentiment_score = score
            item.sentiment_label = label

            # Recency weight: newer = more important
            # Decays over 2 hours (7200 seconds)
            age_seconds = max(0, now - item.published_ts)
            recency_weight = max(0.1, 1.0 - (age_seconds / 7200))

            weighted_scores.append(score * recency_weight)
            total_weight += recency_weight

        # Weighted average
        if total_weight > 0:
            self._overall_score = round(
                sum(weighted_scores) / total_weight, 3
            )
        else:
            self._overall_score = 0.0

        # Classify overall
        if self._overall_score > 0.2:
            self._overall_label = 'bullish'
        elif self._overall_score < -0.2:
            self._overall_label = 'bearish'
        else:
            self._overall_label = 'neutral'

        # Count by sentiment
        bulls = sum(1 for i in news_items if i.sentiment_label == 'bullish')
        bears = sum(1 for i in news_items if i.sentiment_label == 'bearish')
        neutrals = sum(1 for i in news_items if i.sentiment_label == 'neutral')

        logger.info(
            f"[SENTIMENT] Overall: {self._overall_label} "
            f"({self._overall_score:+.3f}) | "
            f"🟢 {bulls} bullish, 🔴 {bears} bearish, "
            f"🟡 {neutrals} neutral"
        )

        return self.get_summary()

    def should_block_trade(self) -> bool:
        """
        Should the bot block new trade entries?

        Returns True if sentiment is strongly bearish.
        Exits are NEVER blocked — you should always be able to close.
        """
        if not self.block_on_bearish:
            return False

        blocked = self._overall_score < self.bearish_threshold
        if blocked:
            logger.warning(
                f"[SENTIMENT] ⛔ Trade BLOCKED — sentiment is "
                f"{self._overall_label} ({self._overall_score:+.3f}, "
                f"threshold: {self.bearish_threshold})"
            )
        return blocked

    def get_summary(self) -> dict:
        """Current sentiment state for the dashboard."""
        return {
            'overall_score': self._overall_score,
            'overall_label': self._overall_label,
            'bearish_threshold': self.bearish_threshold,
            'block_on_bearish': self.block_on_bearish,
            'is_blocking': self.should_block_trade() if self.block_on_bearish else False,
        }

"""
trade_logger.py — Logs every trade to CSV and JSON.

Nothing is hidden. Every entry, exit, fee, slippage, and PnL
is recorded so you can audit the bot's honesty.
"""

import os
import csv
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class TradeLogger:
    """
    Records every trade to disk for full transparency.

    Two formats:
    - CSV: easy to open in Excel or Google Sheets
    - JSON: easy to read programmatically
    """

    CSV_FIELDS = [
        'id', 'symbol', 'side', 'quantity',
        'entry_price', 'exit_price',
        'entry_time', 'exit_time',
        'gross_pnl', 'total_fees', 'net_pnl', 'net_pnl_pct',
        'balance_after', 'duration_seconds',
    ]

    def __init__(self, log_dir: str = 'data/logs'):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        self.csv_path = os.path.join(log_dir, 'trades.csv')
        self.json_path = os.path.join(log_dir, 'trades.json')

        # Initialize CSV with headers if it doesn't exist
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
                writer.writeheader()

        # Initialize JSON file
        if not os.path.exists(self.json_path):
            with open(self.json_path, 'w') as f:
                json.dump([], f)

        logger.info(f"[LOGGER] Trade logs: {self.csv_path}")

    def log_trade(self, trade: dict):
        """
        Log a completed trade to both CSV and JSON.

        The trade dict comes from PaperWallet.close_position().
        """
        # Add human-readable timestamps
        trade_record = dict(trade)
        trade_record['entry_time_str'] = datetime.fromtimestamp(
            trade['entry_time'], tz=timezone.utc
        ).strftime('%Y-%m-%d %H:%M:%S UTC')
        trade_record['exit_time_str'] = datetime.fromtimestamp(
            trade['exit_time'], tz=timezone.utc
        ).strftime('%Y-%m-%d %H:%M:%S UTC')

        # Append to CSV
        try:
            with open(self.csv_path, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS,
                                         extrasaction='ignore')
                writer.writerow(trade_record)
        except Exception as e:
            logger.error(f"[LOGGER] CSV write error: {e}")

        # Append to JSON
        try:
            with open(self.json_path, 'r') as f:
                trades = json.load(f)
            trades.append(trade_record)
            with open(self.json_path, 'w') as f:
                json.dump(trades, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"[LOGGER] JSON write error: {e}")

        logger.debug(f"[LOGGER] Logged trade {trade.get('id', 'unknown')}")

    def get_all_trades(self) -> list[dict]:
        """Read all logged trades from JSON."""
        try:
            with open(self.json_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def get_trade_count(self) -> int:
        """How many trades have been logged."""
        return len(self.get_all_trades())

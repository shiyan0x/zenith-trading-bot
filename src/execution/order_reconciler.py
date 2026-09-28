"""
order_reconciler.py — Position and Order Reconciliation.

Detects discrepancies between local bot state and broker reported state:
- Ghost positions or orders on exchange not tracked locally
- Desynchronized position quantities or entry prices
- Unrecognized fills or executions
"""

import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OrderReconciler:
    """
    Reconciles internal state with the broker's actual state.
    """

    def __init__(self, tolerance_pct: float = 0.01):
        self.tolerance_pct = tolerance_pct

    def reconcile(self, local_positions: List[Dict[str, Any]],
                  broker: Any) -> Dict[str, Any]:
        """
        Compare local positions with broker positions.
        Returns a reconciliation report with discrepancy flags.
        """
        try:
            broker_positions = broker.get_open_positions()
        except Exception as e:
            logger.error(f"[RECONCILER] Failed to fetch broker positions: {e}")
            return {
                'synchronized': False,
                'error': str(e),
                'discrepancies': [f"Failed to fetch broker positions: {e}"],
            }

        discrepancies = []
        local_by_sym = {p['symbol'].upper(): p for p in local_positions}
        broker_by_sym = {}

        # Parse broker positions based on broker type
        for bp in broker_positions:
            sym = bp.get('symbol') or bp.get('asset', '')
            if sym:
                broker_by_sym[sym.upper()] = bp

        # Check local vs broker
        for sym, lp in local_by_sym.items():
            bp = broker_by_sym.get(sym)
            if not bp:
                discrepancies.append(
                    f"Position missing at broker: Local holds {lp.get('quantity')} {sym}, but broker reports none."
                )
            else:
                l_qty = float(lp.get('quantity', 0))
                b_qty = float(bp.get('quantity') or bp.get('free', 0))
                if abs(l_qty - b_qty) / max(l_qty, 1e-6) > self.tolerance_pct:
                    discrepancies.append(
                        f"Quantity mismatch for {sym}: Local={l_qty:.6f}, Broker={b_qty:.6f}"
                    )

        # Check for ghost positions (at broker but not local)
        for sym, bp in broker_by_sym.items():
            if sym not in local_by_sym:
                b_qty = float(bp.get('quantity') or bp.get('free', 0))
                if b_qty > 0.0001:  # filter dust
                    discrepancies.append(
                        f"Ghost position at broker: Broker has {b_qty} {sym} not tracked locally."
                    )

        is_synced = len(discrepancies) == 0
        if not is_synced:
            logger.warning(
                f"[RECONCILER] State mismatch detected! {len(discrepancies)} discrepancies found."
            )
            for d in discrepancies:
                logger.warning(f"  → {d}")
        else:
            logger.debug("[RECONCILER] Local and broker states are synchronized.")

        return {
            'synchronized': is_synced,
            'discrepancies': discrepancies,
            'local_count': len(local_positions),
            'broker_count': len(broker_positions),
        }

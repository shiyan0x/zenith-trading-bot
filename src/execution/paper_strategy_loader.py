"""
paper_strategy_loader.py — Safe Strategy Loader for Paper Trading.

Integrates approved AI-generated strategies with the paper execution engine:
- Verifies that any strategy selected for paper trading has explicit human approval.
- Enforces status == 'paper_trading'.
- Instantiates GeneratedStrategy from the validated blueprint.
- Rejects unapproved or candidate strategies before they can enter the live market feed loop.
"""

import json
import logging
from typing import Optional, Dict, Any, List

from src.research.strategy_registry import StrategyRegistry
from src.research.generated_strategy import GeneratedStrategy
from src.research.promotion_gate import PromotionGate

logger = logging.getLogger(__name__)


class PaperStrategyLoader:
    """
    Safely loads and verifies AI-generated strategies for paper trading.
    """

    def __init__(self, registry: StrategyRegistry, gate: PromotionGate):
        self.registry = registry
        self.gate = gate

    def get_active_paper_strategies(self) -> List[Dict[str, Any]]:
        """Retrieve all strategies currently marked with 'paper_trading' status."""
        return self.registry.list_by_status('paper_trading')

    def load_strategy_instance(self, strategy_id: str) -> GeneratedStrategy:
        """
        Instantiate an approved AI strategy for the paper trading engine.
        
        Strictly requires:
        1. Strategy exists in StrategyRegistry
        2. Status is 'paper_trading'
        3. Human approval recorded in promotion audit log
        """
        strat_record = self.registry.get(strategy_id)
        if not strat_record:
            raise ValueError(f"Strategy '{strategy_id}' not found in registry.")

        status = strat_record.get('status')
        if status != 'paper_trading':
            raise PermissionError(
                f"Strategy '{strat_record.get('name')}' ({strategy_id}) cannot be "
                f"loaded for paper trading: status is '{status}'. "
                f"Only strategies with human 'paper_trading' approval are permitted."
            )

        # Verify audit trail for human approval
        audit_logs = self.gate.get_audit_log(limit=100)
        has_human_approval = any(
            log.get('strategy_id') == strategy_id
            and log.get('action') == 'approve_paper'
            for log in audit_logs
        )
        if not has_human_approval:
            raise PermissionError(
                f"Strategy '{strat_record.get('name')}' ({strategy_id}) lacks "
                f"verified human approval in the promotion audit trail."
            )

        # Parse blueprint and instantiate GeneratedStrategy
        blueprint = strat_record.get('blueprint') or strat_record.get('parameters', {})
        if isinstance(blueprint, str):
            try:
                blueprint = json.loads(blueprint)
            except Exception:
                blueprint = {}

        # Set strategy name from registry
        if 'name' not in blueprint:
            blueprint['name'] = strat_record.get('name', 'AI_Strategy')

        strategy_instance = GeneratedStrategy(blueprint)
        logger.info(
            f"[STRATEGY LOADER] Loaded approved AI strategy: "
            f"{strat_record.get('name')} (v{strat_record.get('version', 1)})"
        )
        return strategy_instance

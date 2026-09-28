"""
src.execution — Paper Trading and Controlled Live Trading Engine.
"""

from src.execution.broker_adapter import (
    BaseBrokerAdapter,
    PaperBrokerAdapter,
    LiveBrokerAdapter,
    MockBrokerAdapter,
    mask_key,
)
from src.execution.live_guard import (
    LiveRiskGuardian,
    LiveModeAuth,
    EmergencyStop,
    LIVE_CONFIRMATION_PHRASE,
)
from src.execution.execution_tracker import ExecutionTracker
from src.execution.paper_comparator import PaperVsBacktestComparator
from src.execution.resilience import (
    StaleDataDetector,
    ReconnectHandler,
    DuplicateEventFilter,
    StatePersistence,
)
from src.execution.order_reconciler import OrderReconciler
from src.execution.notifications import NotificationManager
from src.execution.paper_strategy_loader import PaperStrategyLoader
from src.execution.execution_api import (
    execution_bp,
    register_execution_routes,
    init_execution_api,
)

__all__ = [
    'BaseBrokerAdapter',
    'PaperBrokerAdapter',
    'LiveBrokerAdapter',
    'MockBrokerAdapter',
    'mask_key',
    'LiveRiskGuardian',
    'LiveModeAuth',
    'EmergencyStop',
    'LIVE_CONFIRMATION_PHRASE',
    'ExecutionTracker',
    'PaperVsBacktestComparator',
    'StaleDataDetector',
    'ReconnectHandler',
    'DuplicateEventFilter',
    'StatePersistence',
    'OrderReconciler',
    'NotificationManager',
    'PaperStrategyLoader',
    'execution_bp',
    'register_execution_routes',
    'init_execution_api',
]

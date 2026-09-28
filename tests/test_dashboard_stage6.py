"""
test_dashboard_stage6.py — Integration and Security Tests for Dashboard Upgrade (Stage 6).

Verifies:
1. All 6 new dashboard views (AI Research, Strategy Comparison, Backtesting Lab,
   Paper Trading, System Monitoring, Safety & Controls) are fully accessible.
2. Front-end assets and existing views (Overview, Positions, Episodes, Evolution,
   Strategies, World, Lessons, Trades) continue to function without regressions.
3. Authentication and access control: state-changing actions strictly require
   the session token and explicit confirmation: true. Unauthorized requests return 401.
4. Data provenance: all displayed metrics and states originate from active backend
   components (Store, Registry, Wallet, EmergencyStop, RiskGuardian), not fabricated mock figures.
5. Approval and rejection gates transition strategy states and record persistent audit trails.
6. Emergency stop triggers instant execution halt and reset requires authorized override token.
7. Zero exposure of API secrets in responses or front-end templates.
"""

import os
import json
import tempfile
import pytest
from flask import Flask

from src.dashboard.server import create_dashboard_app
from src.dashboard.auth import get_dashboard_token, validate_token
from src.research.experiment_store import ExperimentStore
from src.research.strategy_registry import StrategyRegistry
from src.research.promotion_gate import PromotionGate
from src.research.research_api import register_research_routes
from src.core.paper_wallet import PaperWallet
from src.core.fee_model import FeeModel
from src.core.order_engine import OrderEngine
from src.core.trade_logger import TradeLogger
from src.execution import (
    EmergencyStop,
    LiveRiskGuardian,
    LiveModeAuth,
    PaperBrokerAdapter,
    ExecutionTracker,
    PaperVsBacktestComparator,
    StaleDataDetector,
    NotificationManager,
    init_execution_api,
)


@pytest.fixture
def test_setup():
    """Build a comprehensive dashboard test harness with temporary DB and mock wallet."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_research.db")
    store = ExperimentStore(db_path)
    registry = StrategyRegistry(store)
    gate = PromotionGate(store, registry)

    # Set up Execution components
    config = {
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "paper_trading": {"starting_balance": 10000.0, "currency": "USDT"},
        "fees": {"mode": "spot", "spot_maker": 0.001, "spot_taker": 0.001},
        "slippage": {"base_bps": 5, "volatility_multiplier": 2.0, "max_bps": 30},
    }
    wallet = PaperWallet(config)
    fee_model = FeeModel(config)
    trade_logger = TradeLogger(temp_dir)
    order_engine = OrderEngine(wallet, fee_model, trade_logger)

    old_reset_token = os.environ.get("EMERGENCY_RESET_TOKEN")
    os.environ["EMERGENCY_RESET_TOKEN"] = "SECRET_OVERRIDE_TOKEN_999"
    emergency_stop = EmergencyStop()
    guardian = LiveRiskGuardian(max_order_usd=500.0, daily_loss_limit_usd=200.0, max_exposure_pct=50.0)
    live_auth = LiveModeAuth(guardian, config["symbols"])
    broker = PaperBrokerAdapter(order_engine, wallet)
    tracker = ExecutionTracker()
    comparator = PaperVsBacktestComparator()
    stale_detector = StaleDataDetector(default_max_age_seconds=120.0)
    stale_detector.record_tick("BTCUSDT", 1700000000.0)
    notification_mgr = NotificationManager()

    init_execution_api(
        tracker=tracker,
        emergency_stop=emergency_stop,
        comparator=comparator,
        guardian=guardian,
        live_auth=live_auth,
        stale_detector=stale_detector,
        notification_mgr=notification_mgr,
        wallet=wallet,
    )

    bot_state = {
        "wallet": wallet.to_dict({"BTCUSDT": 50000.0}),
        "risk": {"breaker_active": False, "max_drawdown_pct": 15.0},
        "kelly": {"raw_kelly": 0.12, "fractional_kelly": 0.06},
        "strategies": [],
        "recent_trades": [],
        "prices": {"BTCUSDT": 50000.0, "ETHUSDT": 3000.0},
        "status": "running",
        "timeframe": "15m",
        "news": [{"title": "Crypto Market Steady", "source": "Reuters", "age_minutes": 5, "sentiment_label": "bullish", "sentiment_score": 0.4}],
        "sentiment": {"overall_label": "bullish", "overall_score": 0.4, "is_blocking": False},
    }

    app, socketio = create_dashboard_app(bot_state)
    # Register research routes pointing to our temporary test store
    register_research_routes(app, db_path=db_path)

    client = app.test_client()

    try:
        yield {
            "app": app,
            "client": client,
            "store": store,
            "registry": registry,
            "gate": gate,
            "wallet": wallet,
            "emergency_stop": emergency_stop,
            "guardian": guardian,
            "token": get_dashboard_token(),
            "bot_state": bot_state,
        }
    finally:
        if old_reset_token is not None:
            os.environ["EMERGENCY_RESET_TOKEN"] = old_reset_token
        else:
            os.environ.pop("EMERGENCY_RESET_TOKEN", None)


# ─── 1. FRONTEND TEMPLATE & EXISTING VIEWS ────────────────────────────────────

def test_frontend_serves_all_stage6_views_and_controls(test_setup):
    """Verify that index.html contains all 6 Stage 6 sections, nav items, and safety controls."""
    client = test_setup["client"]
    res = client.get("/")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    # 6 Stage 6 Nav items
    assert 'data-view="research"' in html
    assert 'data-view="comparison"' in html
    assert 'data-view="backtesting"' in html
    assert 'data-view="papertrading"' in html
    assert 'data-view="monitoring"' in html
    assert 'data-view="controls"' in html

    # 6 Stage 6 View sections
    assert 'id="view-research"' in html
    assert 'id="view-comparison"' in html
    assert 'id="view-backtesting"' in html
    assert 'id="view-papertrading"' in html
    assert 'id="view-monitoring"' in html
    assert 'id="view-controls"' in html

    # Safety & Controls elements
    assert 'id="btn-trigger-emergency"' in html
    assert 'id="ctrl-reset-token-input"' in html
    assert 'id="btn-reset-emergency"' in html
    assert 'id="confirm-modal"' in html
    assert 'id="modal-confirm-btn"' in html
    assert 'id="modal-cancel-btn"' in html

    # Simulated Account clarity
    assert 'SIMULATED (VIRTUAL FUNDS)' in html
    assert 'SIMULATED ACCOUNT' in html


def test_existing_dashboard_routes_preserved(test_setup):
    """Verify existing API endpoints (/api/state, /api/episodes, /api/lessons, /api/news) continue to work."""
    client = test_setup["client"]

    res_state = client.get("/api/state")
    assert res_state.status_code == 200
    assert "wallet" in res_state.get_json()

    res_ep = client.get("/api/episodes")
    assert res_ep.status_code == 200
    assert isinstance(res_ep.get_json(), list)

    res_less = client.get("/api/lessons")
    assert res_less.status_code == 200
    assert isinstance(res_less.get_json(), list)

    res_news = client.get("/api/news")
    assert res_news.status_code == 200
    assert "items" in res_news.get_json()


def test_dashboard_js_serves_stage6_handlers(test_setup):
    """Verify dashboard.js contains Stage 6 handlers, modal listeners, and auth token fetching."""
    client = test_setup["client"]
    res = client.get("/dashboard.js")
    assert res.status_code == 200
    js = res.data.decode("utf-8")

    assert "renderResearch" in js
    assert "renderComparison" in js
    assert "renderBacktesting" in js
    assert "renderPaperTrading" in js
    assert "renderMonitoring" in js
    assert "renderControls" in js
    assert "fetchDashboardToken" in js
    assert "showConfirmModal" in js


# ─── 2. AUTHENTICATION & ACCESS CONTROL ───────────────────────────────────────

def test_auth_token_endpoint(test_setup):
    """Verify /api/auth/token supplies the valid session token."""
    client = test_setup["client"]
    res = client.get("/api/auth/token")
    assert res.status_code == 200
    data = res.get_json()
    assert "token" in data
    assert validate_token(data["token"]) is True


def test_unauthorized_state_changing_requests_rejected(test_setup):
    """Verify mutating requests without authorization token return 401."""
    client = test_setup["client"]

    endpoints = [
        ("/api/execution/emergency-stop", {"confirmation": True}),
        ("/api/execution/emergency-reset", {"confirmation": True, "reset_token": "abc"}),
        ("/api/execution/stop-trading", {"confirmation": True}),
        ("/api/research/run-job", {"confirmation": True}),
        ("/api/research/stop-job", {"confirmation": True}),
        ("/api/research/approve", {"confirmation": True, "strategy_id": "strat_1"}),
        ("/api/research/reject", {"confirmation": True, "strategy_id": "strat_1"}),
    ]

    for ep, payload in endpoints:
        # Request with no token header
        res_no_token = client.post(ep, json=payload)
        assert res_no_token.status_code == 401, f"{ep} did not reject missing token"

        # Request with invalid token
        res_bad_token = client.post(ep, json=payload, headers={"X-Zenith-Token": "invalid_fake_token"})
        assert res_bad_token.status_code == 401, f"{ep} did not reject invalid token"


def test_confirmation_required_on_mutating_requests(test_setup):
    """Verify mutating requests with valid token but without confirmation: true return 400."""
    client = test_setup["client"]
    token = test_setup["token"]
    headers = {"X-Zenith-Token": token}

    endpoints = [
        ("/api/execution/emergency-stop", {"reason": "Test without confirm"}),
        ("/api/execution/stop-trading", {}),
        ("/api/research/run-job", {"max_candidates": 2}),
        ("/api/research/stop-job", {}),
        ("/api/research/approve", {"strategy_id": "strat_1"}),
        ("/api/research/reject", {"strategy_id": "strat_1"}),
    ]

    for ep, payload in endpoints:
        res = client.post(ep, json=payload, headers=headers)
        assert res.status_code == 400, f"{ep} did not require confirmation: true (got {res.status_code})"
        assert "Confirmation" in res.get_json().get("error", "")


# ─── 3. DATA PROVENANCE & REAL APPLICATION STATE ─────────────────────────────

def test_research_api_serves_actual_database_records(test_setup):
    """Verify research endpoints serve true database records, not fabricated data."""
    client = test_setup["client"]
    registry = test_setup["registry"]
    store = test_setup["store"]

    # Register real strategy candidate in test DB
    strat_id = registry.register_generated(
        name="Real_Momentum_Strategy",
        version=1,
        blueprint={"parameters": {"rsi_period": 14, "ema_fast": 9}},
        timeframe="15m",
        entry_desc="close > ema_fast",
        exit_desc="close < ema_fast",
    )

    # Record a completed experiment result
    exp_id = store.create_experiment(
        strategy_id=strat_id,
        strategy_name="Real_Momentum_Strategy",
        symbol="BTCUSDT",
        timeframe="15m",
        config_snapshot={"symbol": "BTCUSDT"},
    )
    store.save_result(
        experiment_id=exp_id,
        period="test",
        metrics={
            "sharpe_ratio": 1.85,
            "total_return_pct": 14.5,
            "max_drawdown_pct": 6.2,
            "win_rate": 60.0,
            "total_trades": 25,
            "profit_factor": 1.9,
            "passed": True,
        },
    )
    store.complete_experiment(exp_id)

    # 1. Summary endpoint
    res_summary = client.get("/api/research/summary")
    assert res_summary.status_code == 200
    s_data = res_summary.get_json()
    assert s_data["strategies"]["total"] >= 1
    assert s_data["experiments"]["completed"] >= 1

    # 2. Strategies endpoint
    res_strats = client.get("/api/research/strategies")
    assert res_strats.status_code == 200
    strat_list = res_strats.get_json()
    names = [s["name"] for s in strat_list]
    assert "Real_Momentum_Strategy" in names

    # 3. Leaderboard endpoint
    res_lb = client.get("/api/research/leaderboard")
    assert res_lb.status_code == 200
    lb = res_lb.get_json()
    assert len(lb) >= 1
    assert lb[0]["strategy_name"] == "Real_Momentum_Strategy"
    assert lb[0]["sharpe"] == 1.85


def test_execution_monitoring_reflects_actual_engine_state(test_setup):
    """Verify /api/execution/monitoring reflects real broker, feed freshness, and risk limits."""
    client = test_setup["client"]
    res = client.get("/api/execution/monitoring")
    assert res.status_code == 200
    mon = res.get_json()

    assert mon["broker"]["mode"] == "paper"
    assert mon["broker"]["status"] == "CONNECTED"
    assert "VIRTUAL" in mon["broker"]["account_type"]
    assert mon["risk"]["emergency_stop"] is False
    assert mon["risk"]["limits"]["max_order_usd"] == 500.0
    assert mon["risk"]["limits"]["daily_loss_limit_usd"] == 200.0


# ─── 4. SAFETY & CONTROLS INTERFACES ─────────────────────────────────────────

def test_emergency_stop_trigger_and_reset(test_setup):
    """Verify emergency stop trigger halts the bot and reset restores execution readiness."""
    client = test_setup["client"]
    emergency_stop = test_setup["emergency_stop"]
    token = test_setup["token"]
    headers = {"X-Zenith-Token": token}

    assert emergency_stop.is_halted is False

    # Trigger emergency stop via dashboard API
    res_stop = client.post(
        "/api/execution/emergency-stop",
        json={"confirmation": True, "reason": "Operator Emergency Drill"},
        headers=headers,
    )
    assert res_stop.status_code == 200
    assert emergency_stop.is_halted is True

    # Status check confirms halted
    res_status = client.get("/api/execution/status")
    assert res_status.get_json()["is_emergency_halted"] is True

    # Attempt reset with wrong token
    res_bad_reset = client.post(
        "/api/execution/emergency-reset",
        json={"confirmation": True, "reset_token": "WRONG_TOKEN"},
        headers=headers,
    )
    assert res_bad_reset.status_code == 403
    assert emergency_stop.is_halted is True

    # Reset with valid reset token
    res_good_reset = client.post(
        "/api/execution/emergency-reset",
        json={"confirmation": True, "reset_token": "SECRET_OVERRIDE_TOKEN_999"},
        headers=headers,
    )
    assert res_good_reset.status_code == 200
    assert emergency_stop.is_halted is False


def test_strategy_approval_and_rejection_lifecycle(test_setup):
    """Verify human approval and rejection gates update strategy registry status and audit trail."""
    client = test_setup["client"]
    registry = test_setup["registry"]
    token = test_setup["token"]
    headers = {"X-Zenith-Token": token}

    strat_1 = registry.register_generated(name="Candidate_For_Paper", version=1, blueprint={})
    registry.promote(strat_1)

    strat_2 = registry.register_generated(name="Candidate_For_Rejection", version=1, blueprint={})

    # 1. Approve strat_1 for paper trading
    res_app = client.post(
        "/api/research/approve",
        json={"strategy_id": strat_1, "approved_by": "lead_trader", "confirmation": True},
        headers=headers,
    )
    assert res_app.status_code == 200
    strat_1_entry = registry.get(strat_1)
    assert strat_1_entry["status"] == "paper_trading"

    # 2. Reject strat_2
    res_rej = client.post(
        "/api/research/reject",
        json={"strategy_id": strat_2, "reason": "High correlation with baseline", "confirmation": True},
        headers=headers,
    )
    assert res_rej.status_code == 200
    strat_2_entry = registry.get(strat_2)
    assert strat_2_entry["status"] == "retired"

    # 3. Verify audit log captures both events
    res_audit = client.get("/api/research/audit")
    assert res_audit.status_code == 200
    log = res_audit.get_json()
    actions = [item["action"] for item in log]
    assert "approve_paper" in actions or "promote" in actions
    assert "rejected" in actions or "reject" in actions


# ─── 5. SECURITY & SECRETS ISOLATION ──────────────────────────────────────────

def test_no_api_secrets_exposed_in_dashboard(test_setup):
    """Verify that backend secrets and credentials are never exposed in any dashboard responses."""
    client = test_setup["client"]

    endpoints = [
        "/",
        "/api/state",
        "/api/execution/status",
        "/api/execution/monitoring",
        "/api/research/summary",
        "/api/research/strategies",
    ]

    forbidden_patterns = [
        "SECRET_OVERRIDE_TOKEN_999",
        "api_secret",
        "private_key",
        "secret_key",
    ]

    for ep in endpoints:
        res = client.get(ep)
        body = res.data.decode("utf-8")
        for secret in forbidden_patterns:
            assert secret not in body, f"Secret pattern '{secret}' leaked in endpoint {ep}"

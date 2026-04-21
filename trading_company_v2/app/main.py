from __future__ import annotations

import secrets
from base64 import b64decode
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.responses import PlainTextResponse

from app.config import settings
from app.agents.execution_agent import ExecutionAgent
from app.agents.us_stock_desk_agent import USStockDeskAgent
from app.core.models import CompanyState
from app.core.state_store import init_db, load_closed_positions, load_company_state, load_open_positions, load_performance_quick_stats
from app.notifier import notifier
from app.orchestrator import CompanyOrchestrator
from app.services.kis_broker import get_account_positions as get_kis_account_positions
from app.services.kis_broker import get_order as get_kis_order
from app.services.kis_broker import normalize_order_state as normalize_kis_order_state
from app.services.market_gateway import get_naver_daily_prices, get_upbit_15m_candles, get_us_daily_prices, get_us_data_status
from app.services.recommendation_engine import build_crypto_plan, build_korea_plan, build_us_plan
from app.services.upbit_broker import get_account_positions as get_upbit_account_positions
from app.services.upbit_broker import get_order as get_upbit_order
from app.services.upbit_broker import normalize_order_state as normalize_upbit_order_state
from app.service_manager import local_access_urls


app = FastAPI(title="Trading Company V2", version="0.1.0")
orchestrator = CompanyOrchestrator()


def _safe_parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _latest_live_order_for_mode(state: CompanyState, mode: str) -> dict | None:
    for item in list(state.execution_log or []):
        if item.get("source") != "live":
            continue
        if str(item.get("applied_mode") or "") != mode:
            continue
        return item
    return None


def _check_item(status: str, label: str, detail: str) -> dict:
    return {"status": status, "label": label, "detail": detail}


def _auth_enabled() -> bool:
    return bool(settings.app_username and settings.app_password)


def _unauthorized_response() -> PlainTextResponse:
    return PlainTextResponse(
        "Authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Trading Company"'},
    )


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    if request.url.path == "/health" or not _auth_enabled():
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return _unauthorized_response()

    try:
        encoded = header.split(" ", 1)[1].strip()
        decoded = b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return _unauthorized_response()

    if not (
        secrets.compare_digest(username, settings.app_username)
        and secrets.compare_digest(password, settings.app_password)
    ):
        return _unauthorized_response()

    return await call_next(request)


def _compute_insight_score(state: CompanyState) -> int:
    if not state.agent_runs:
        return 50
    score = sum(item.score for item in state.agent_runs) / len(state.agent_runs)
    return round(score * 100)


def _build_equity_curve(state: CompanyState) -> list[dict]:
    realized = float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0)
    unrealized = float(state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0)
    current_total = round(100.0 + realized + unrealized, 2)
    updated_at = state.updated_at[11:16] if len(state.updated_at) >= 16 else "Now"
    return [
        {"label": "Start", "equity": 100.0, "delta": 0.0},
        {"label": updated_at, "equity": current_total, "delta": round(realized + unrealized, 2)},
    ]


def _build_desk_status(state: CompanyState) -> dict:
    crypto_plan = state.strategy_book.get("crypto_plan", {}) if state.strategy_book else {}
    korea_plan = state.strategy_book.get("korea_plan", {}) if state.strategy_book else {}
    us_plan = state.strategy_book.get("us_plan", {}) if state.strategy_book else {}
    crypto_view = state.desk_views.get("crypto_desk", {}) if state.desk_views else {}
    korea_view = state.desk_views.get("korea_stock_desk", {}) if state.desk_views else {}
    us_view = state.desk_views.get("us_stock_desk", {}) if state.desk_views else {}
    execution_log = state.execution_log or []

    latest_crypto_order = next((item for item in execution_log if item.get("desk") == "crypto"), None)
    latest_korea_order = next((item for item in execution_log if item.get("desk") == "korea"), None)
    latest_us_order = next((item for item in execution_log if item.get("desk") == "us"), None)

    return {
        "crypto": {
            "title": "Crypto Desk",
            "bias": crypto_view.get("desk_bias", "n/a"),
            "action": crypto_plan.get("action", "n/a"),
            "focus": crypto_plan.get("focus", "No crypto plan"),
            "size": crypto_plan.get("size", "0.00x"),
            "leaders": (state.market_snapshot.get("crypto_leaders", []) if state.market_snapshot else [])[:3],
            "latest_order": latest_crypto_order,
        },
        "korea": {
            "title": "Korea Stock Desk",
            "bias": "active" if korea_view.get("active_gap_count", 0) else "watch",
            "action": korea_plan.get("action", "n/a"),
            "focus": korea_plan.get("focus", "No stock plan"),
            "size": korea_plan.get("size", "0.00x"),
            "leaders": ((state.market_snapshot.get("gap_candidates") or state.market_snapshot.get("stock_leaders") or []) if state.market_snapshot else [])[:3],
            "latest_order": latest_korea_order,
        },
        "us": {
            "title": "U.S. Stock Desk",
            "bias": us_view.get("desk_bias", "n/a"),
            "action": us_plan.get("action", "n/a"),
            "focus": us_plan.get("focus", "No U.S. plan"),
            "size": us_plan.get("size", "0.00x"),
            "leaders": (state.market_snapshot.get("us_leaders", []) if state.market_snapshot else [])[:3],
            "latest_order": latest_us_order,
        },
    }


def _build_performance_payload(state: CompanyState, closed_positions: list[dict]) -> dict:
    cumulative = 100.0
    trade_curve = [{"label": "Start", "equity": cumulative, "delta": 0.0}]
    for index, item in enumerate(reversed(closed_positions[-8:]), start=1):
        delta = round(float(item.get("pnl_pct", 0.0) or 0.0), 2)
        cumulative = round(cumulative + delta, 2)
        trade_curve.append(
            {
                "label": item.get("symbol") or f"T{index}",
                "equity": cumulative,
                "delta": delta,
            }
        )

    return {
        "realized_pnl_pct": float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0),
        "unrealized_pnl_pct": float(state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0),
        "win_rate": float(state.daily_summary.get("win_rate", 0.0) or 0.0),
        "wins": int(state.daily_summary.get("wins", 0) or 0),
        "losses": int(state.daily_summary.get("losses", 0) or 0),
        "expectancy_pct": float(state.daily_summary.get("expectancy_pct", 0.0) or 0.0),
        "realized_pnl_krw": int(state.daily_summary.get("realized_pnl_krw", 0) or 0),
        "unrealized_pnl_krw": int(state.daily_summary.get("unrealized_pnl_krw", 0) or 0),
        "expectancy_krw": int(state.daily_summary.get("expectancy_krw", 0) or 0),
        "close_reason_stats": state.daily_summary.get("close_reason_stats", {}) or {},
        "desk_close_reason_stats": state.daily_summary.get("desk_close_reason_stats", {}) or {},
        "symbol_performance_stats": state.daily_summary.get("symbol_performance_stats", []) or [],
        "closed_count": len(closed_positions),
        "trade_curve": trade_curve,
        "recent_closed": closed_positions[:6],
    }


def _build_capital_payload(state: CompanyState) -> dict:
    base = float(settings.paper_capital_krw)
    realized_pct = float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0)
    unrealized_pct = float(state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0)
    realized_krw = round(base * realized_pct / 100)
    unrealized_krw = round(base * unrealized_pct / 100)
    total_krw = round(base + realized_krw + unrealized_krw)
    return {
        "base_krw": int(base),
        "realized_krw": realized_krw,
        "unrealized_krw": unrealized_krw,
        "total_krw": total_krw,
    }


def _build_market_charts_payload(state: CompanyState) -> dict:
    def summarize(candles: list[dict]) -> dict:
        if not candles:
            return {
                "last_close": 0.0,
                "change_pct": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0.0,
            }
        first_open = float(candles[0].get("open") or candles[0].get("close") or 0.0)
        last_close = float(candles[-1].get("close") or 0.0)
        high = max(float(item.get("high") or item.get("close") or 0.0) for item in candles)
        low = min(float(item.get("low") or item.get("close") or 0.0) for item in candles)
        volume = sum(float(item.get("volume") or 0.0) for item in candles)
        change_pct = round(((last_close - first_open) / first_open) * 100, 2) if first_open else 0.0
        return {
            "last_close": last_close,
            "change_pct": change_pct,
            "high": high,
            "low": low,
            "volume": volume,
        }

    crypto_symbol = str(state.strategy_book.get("crypto_plan", {}).get("symbol") or "KRW-BTC")
    korea_symbol = str(state.strategy_book.get("korea_plan", {}).get("symbol") or "")
    us_symbol = str(state.strategy_book.get("us_plan", {}).get("symbol") or "")
    crypto_candles = get_upbit_15m_candles(crypto_symbol, count=24)
    stock_candles = get_naver_daily_prices(korea_symbol, count=20) if korea_symbol else []
    us_candles = get_us_daily_prices(us_symbol, count=30) if us_symbol else []
    return {
        "crypto": {"symbol": crypto_symbol, "candles": crypto_candles, "summary": summarize(crypto_candles)},
        "korea": {"symbol": korea_symbol, "candles": stock_candles, "summary": summarize(stock_candles)},
        "us": {"symbol": us_symbol, "candles": us_candles, "summary": summarize(us_candles)},
    }


def _build_execution_summary(state: CompanyState) -> dict:
    execution_log = list(state.execution_log or [])
    live_rows = [item for item in execution_log if item.get("source") == "live"]
    partial_rows = [
        item
        for item in live_rows
        if str(item.get("status") or "") == "partial"
        or str(item.get("effect_status") or "").startswith("partial")
    ]
    pending_rows = [
        item
        for item in live_rows
        if str(item.get("status") or "") in {"submitted", "partial"}
        or str(item.get("effect_status") or "") in {"pending", "awaiting_balance_sync", "partial_balance_sync"}
    ]
    now_utc = datetime.now(timezone.utc)
    stale_rows = []
    for item in pending_rows:
        created_at = _safe_parse_utc(str(item.get("created_at") or ""))
        if created_at is None:
            continue
        age_minutes = (now_utc - created_at).total_seconds() / 60
        if age_minutes >= 15:
            stale_rows.append({**item, "age_minutes": round(age_minutes, 1)})
    return {
        "live_count": len(live_rows),
        "partial_count": len(partial_rows),
        "pending_count": len(pending_rows),
        "stale_count": len(stale_rows),
        "latest_live": live_rows[0] if live_rows else None,
        "stale_live": stale_rows[:3],
    }


def _build_ops_flags(state: CompanyState) -> dict:
    daily = state.daily_summary or {}
    close_reason_stats = daily.get("close_reason_stats", {}) or {}
    desk_close_reason_stats = daily.get("desk_close_reason_stats", {}) or {}
    symbol_performance_stats = daily.get("symbol_performance_stats", []) or []
    stop_stats = close_reason_stats.get("stop_hit", {}) or {}
    flags: list[dict] = []

    def add_flag(level: str, code: str, message: str) -> None:
        flags.append({"level": level, "code": code, "message": message})

    gross = float(daily.get("gross_open_notional_pct", 0.0) or 0.0)
    expectancy = float(daily.get("expectancy_pct", 0.0) or 0.0)
    realized = float(daily.get("realized_pnl_pct", 0.0) or 0.0)
    win_rate = float(daily.get("win_rate", 0.0) or 0.0)
    allow_new_entries = bool(state.allow_new_entries)

    if not allow_new_entries:
        add_flag("critical", "entries_blocked", "신규 진입이 차단된 상태")
    if gross >= 0.9:
        add_flag("warning", "gross_exposure_high", f"총 노출이 높음: {gross:.2f}x")
    if expectancy < 0:
        add_flag("warning", "negative_expectancy", f"거래 기대값이 음수: {expectancy:.2f}%")
    if realized <= -1.0:
        add_flag("warning", "daily_drawdown", f"일일 실현 손익 악화: {realized:.2f}%")
    if int(stop_stats.get("count", 0) or 0) >= 3 and float(stop_stats.get("pnl_pct", 0.0) or 0.0) <= -3.0:
        add_flag("warning", "stop_pressure", f"stop_hit 압력 높음: {stop_stats.get('count', 0)}회 / {stop_stats.get('pnl_pct', 0.0)}%")
    if daily.get("closed_positions", 0) and win_rate < 40.0:
        add_flag("warning", "low_win_rate", f"승률 저하: {win_rate:.1f}%")

    for desk_name, label in (("crypto", "코인"), ("korea", "한국"), ("us", "미국")):
        desk_stop = ((desk_close_reason_stats.get(desk_name, {}) or {}).get("stop_hit", {}) or {})
        if int(desk_stop.get("count", 0) or 0) >= 2 and float(desk_stop.get("pnl_pct", 0.0) or 0.0) <= -1.5:
            add_flag("warning", f"{desk_name}_desk_stop", f"{label} 데스크 stop 압력: {desk_stop.get('count', 0)}회 / {desk_stop.get('pnl_pct', 0.0)}%")

    for item in symbol_performance_stats[:2]:
        if int(item.get("stop_like_count", 0) or 0) >= 2 or float(item.get("pnl_pct", 0.0) or 0.0) <= -2.0:
            add_flag(
                "warning",
                f"{item.get('desk', 'n/a')}_{item.get('symbol', 'n/a')}",
                f"{item.get('desk', 'n/a')} {item.get('symbol', 'n/a')} 손실 반복: {item.get('stop_like_count', 0)}회 / {item.get('pnl_pct', 0.0)}%",
            )

    strategy_book = state.strategy_book or {}
    for desk_name, label in (("crypto_plan", "코인"), ("korea_plan", "한국"), ("us_plan", "미국")):
        plan = strategy_book.get(desk_name, {}) or {}
        action = str(plan.get("action", "") or "")
        notes = [str(item) for item in (plan.get("notes", []) or [])]
        if action in {"stand_by", "watchlist_only"} and any("overheated" in note or "weakly confirmed" in note for note in notes):
            add_flag("info", f"{desk_name}_hold", f"{label} 데스크 보류: {plan.get('focus', 'n/a')}")

    execution_summary = _build_execution_summary(state)
    if int(execution_summary.get("partial_count", 0) or 0) > 0:
        add_flag("warning", "live_partial_fill", f"live 부분체결 {execution_summary.get('partial_count', 0)}건 확인 필요")
    elif int(execution_summary.get("pending_count", 0) or 0) > 0:
        add_flag("info", "live_pending", f"live 주문 대기 {execution_summary.get('pending_count', 0)}건")

    if not allow_new_entries and int(execution_summary.get("pending_count", 0) or 0) > 0:
        add_flag("warning", "live_conservative_mode", "live execution unresolved, conservative entry pause active")

    if int(execution_summary.get("stale_count", 0) or 0) > 0:
        add_flag("warning", "live_stale_pending", f"live stale pending {execution_summary.get('stale_count', 0)} order(s)")

    severity = "stable"
    if any(item["level"] == "critical" for item in flags):
        severity = "critical"
    elif any(item["level"] == "warning" for item in flags):
        severity = "warning"

    return {"severity": severity, "items": flags}


def _runtime_profile(state: CompanyState) -> dict:
    session = state.session_state or {}
    if session.get("korea_opening_window") or session.get("us_regular"):
        return {
            "mode": "active",
            "interval_seconds": settings.realtime_active_interval_seconds,
            "reason": "시장 활성 구간이라 초단기 재평가 중",
        }
    if session.get("korea_open") or session.get("us_premarket") or session.get("crypto_focus"):
        return {
            "mode": "watch",
            "interval_seconds": settings.realtime_watch_interval_seconds,
            "reason": "감시 구간이라 짧은 주기로 재평가 중",
        }
    return {
        "mode": "idle",
        "interval_seconds": settings.realtime_idle_interval_seconds,
        "reason": "비활성 구간이라 완만한 주기로 감시 중",
    }


def _simulate_decision_snapshot(state: CompanyState, simulated_session: dict) -> dict:
    crypto_payload = state.desk_views.get("crypto_desk", {}) if state.desk_views else {}
    korea_payload = state.desk_views.get("korea_stock_desk", {}) if state.desk_views else {}
    us_payload = state.desk_views.get("us_stock_desk", {}) if state.desk_views else {}

    simulated_strategy_book = {
        "crypto_plan": build_crypto_plan(state.stance, state.regime, crypto_payload),
        "korea_plan": build_korea_plan(state.stance, state.regime, korea_payload, simulated_session),
        "us_plan": build_us_plan(state.stance, state.regime, us_payload, simulated_session),
    }
    execution_agent = ExecutionAgent()
    execution_agent.configure(
        strategy_book=simulated_strategy_book,
        regime=state.regime,
        market_snapshot=state.market_snapshot,
        open_positions=state.open_positions,
        closed_positions=load_closed_positions(limit=12),
        allow_new_entries=state.allow_new_entries,
        risk_budget=state.risk_budget,
    )
    orders = execution_agent.run().payload.get("orders", [])
    return {
        "session": simulated_session,
        "runtime_profile": _runtime_profile(CompanyState(**{**state.model_dump(), "session_state": simulated_session})),
        "strategy_book": simulated_strategy_book,
        "orders": orders,
    }


def _build_dashboard_payload(state: CompanyState) -> dict:
    closed_positions = load_closed_positions(limit=8)
    equity_curve = _build_equity_curve(state)
    latest_equity = equity_curve[-1]["equity"] if equity_curve else 100.0
    return {
        "insight_score": _compute_insight_score(state),
        "equity_curve": equity_curve,
        "equity_summary": {
            "start": 100.0,
            "current": latest_equity,
            "change_pct": round(latest_equity - 100.0, 2),
        },
        "desk_status": _build_desk_status(state),
        "open_positions": state.open_positions,
        "closed_positions": closed_positions,
        "performance": _build_performance_payload(state, closed_positions),
        "capital": _build_capital_payload(state),
        "execution_summary": _build_execution_summary(state),
        "exposure": {
            "gross_open_notional_pct": float(state.daily_summary.get("gross_open_notional_pct", 0.0) or 0.0),
            "allow_new_entries": bool(state.allow_new_entries),
            "risk_budget": float(state.risk_budget),
        },
        "runtime_profile": _runtime_profile(state),
        "ops_flags": _build_ops_flags(state),
        "market_charts": _build_market_charts_payload(state),
    }


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "env": settings.app_env,
        "execution": {
            "requested_mode": settings.execution_mode,
            "live_capital_krw": settings.live_capital_krw,
            "upbit_allow_live": settings.upbit_allow_live,
            "upbit_credentials_present": bool(settings.upbit_access_key and settings.upbit_secret_key),
            "kis_allow_live": settings.kis_allow_live,
            "kis_credentials_present": bool(settings.kis_app_key and settings.kis_app_secret and settings.kis_account_no and settings.kis_product_code),
        },
        "telegram_enabled": notifier.enabled,
        "telegram_last_error": notifier.last_error or None,
        "us_data_status": get_us_data_status(),
        "access": local_access_urls(),
    }


@app.get("/state")
def state() -> dict:
    return load_company_state().model_dump()


@app.get("/dashboard-data")
def dashboard_data() -> dict:
    state = load_company_state()
    return {
        "company_name": settings.company_name,
        "operator_name": settings.operator_name,
        "state": state.model_dump(),
        "dashboard": _build_dashboard_payload(state),
        "broker_live_health": broker_live_health(),
        "live_readiness_checklist": live_readiness_checklist(),
    }


@app.get("/ops-summary")
def ops_summary() -> dict:
    state = load_company_state()
    dashboard = _build_dashboard_payload(state)
    return {
        "company_name": settings.company_name,
        "updated_at": state.updated_at,
        "session": state.session_state,
        "risk": {
            "stance": state.stance,
            "regime": state.regime,
            "risk_budget": state.risk_budget,
            "allow_new_entries": state.allow_new_entries,
            "gross_open_notional_pct": state.daily_summary.get("gross_open_notional_pct", 0.0),
        },
        "runtime_profile": dashboard.get("runtime_profile", {}),
        "ops_flags": dashboard.get("ops_flags", {}),
        "performance": {
            "win_rate": state.daily_summary.get("win_rate", 0.0),
            "wins": state.daily_summary.get("wins", 0),
            "losses": state.daily_summary.get("losses", 0),
            "expectancy_pct": state.daily_summary.get("expectancy_pct", 0.0),
            "realized_pnl_pct": state.daily_summary.get("realized_pnl_pct", 0.0),
            "unrealized_pnl_pct": state.daily_summary.get("unrealized_pnl_pct", 0.0),
            "realized_pnl_krw": state.daily_summary.get("realized_pnl_krw", 0),
            "unrealized_pnl_krw": state.daily_summary.get("unrealized_pnl_krw", 0),
            "expectancy_krw": state.daily_summary.get("expectancy_krw", 0),
            "open_positions": state.daily_summary.get("open_positions", 0),
            "current_cycle_planned_orders": state.daily_summary.get("current_cycle_planned_orders", 0),
            "close_reason_stats": state.daily_summary.get("close_reason_stats", {}),
            "desk_close_reason_stats": state.daily_summary.get("desk_close_reason_stats", {}),
            "symbol_performance_stats": state.daily_summary.get("symbol_performance_stats", []),
            "desk_stats": state.daily_summary.get("desk_stats", {}),
        },
        "desk_plans": {
            "crypto": state.strategy_book.get("crypto_plan", {}),
            "korea": state.strategy_book.get("korea_plan", {}),
            "us": state.strategy_book.get("us_plan", {}),
        },
        "market": {
            "crypto_leaders": (state.market_snapshot.get("crypto_leaders") or [])[:3],
            "korea_leaders": (state.market_snapshot.get("gap_candidates") or state.market_snapshot.get("stock_leaders") or [])[:3],
            "us_leaders": (state.market_snapshot.get("us_leaders") or [])[:3],
        },
        "positions": {
            "open": dashboard.get("open_positions", [])[:5],
            "closed": dashboard.get("closed_positions", [])[:5],
        },
        "execution": (state.execution_log or [])[:5],
    }


@app.get("/mobile-summary")
def mobile_summary() -> dict:
    state = load_company_state()
    daily = state.daily_summary
    return {
        "updated_at": state.updated_at,
        "phase": state.session_state.get("market_phase", "n/a"),
        "risk": {
            "stance": state.stance,
            "regime": state.regime,
            "risk_budget": state.risk_budget,
            "allow_new_entries": state.allow_new_entries,
            "gross_open_notional_pct": daily.get("gross_open_notional_pct", 0.0),
        },
        "runtime_profile": _runtime_profile(state),
        "ops_flags": _build_ops_flags(state),
        "execution_summary": _build_execution_summary(state),
        "live_readiness": live_readiness_checklist(),
        "headline": {
            "win_rate": daily.get("win_rate", 0.0),
            "expectancy_pct": daily.get("expectancy_pct", 0.0),
            "realized_pnl_pct": daily.get("realized_pnl_pct", 0.0),
            "unrealized_pnl_pct": daily.get("unrealized_pnl_pct", 0.0),
            "realized_pnl_krw": daily.get("realized_pnl_krw", 0),
            "unrealized_pnl_krw": daily.get("unrealized_pnl_krw", 0),
            "open_positions": daily.get("open_positions", 0),
            "gross_open_notional_pct": daily.get("gross_open_notional_pct", 0.0),
        },
        "problem_symbols": (daily.get("symbol_performance_stats", []) or [])[:3],
        "desks": {
            "crypto": {
                "plan": state.strategy_book.get("crypto_plan", {}),
                "stats": (daily.get("desk_stats", {}) or {}).get("crypto", {}),
            },
            "korea": {
                "plan": state.strategy_book.get("korea_plan", {}),
                "stats": (daily.get("desk_stats", {}) or {}).get("korea", {}),
            },
            "us": {
                "plan": state.strategy_book.get("us_plan", {}),
                "stats": (daily.get("desk_stats", {}) or {}).get("us", {}),
            },
        },
        "recent_execution": (state.execution_log or [])[:3],
        "open_positions": state.open_positions[:3],
    }


@app.get("/manifest.webmanifest")
def manifest() -> dict:
    return {
        "name": settings.company_name,
        "short_name": "TradingApp",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#09111f",
        "theme_color": "#09111f",
        "lang": "ko-KR",
        "icons": [
            {
                "src": "/app-icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any",
            }
        ],
    }


@app.get("/service-worker.js")
def service_worker() -> Response:
    body = """
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request).catch(() => caches.match('/')));
});
""".strip()
    return Response(content=body, media_type="application/javascript")


@app.get("/app-icon.svg")
def app_icon() -> Response:
    svg = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="28" fill="#09111f"/>
  <rect width="128" height="128" rx="28" fill="url(#gi)" opacity="0.6"/>
  <defs><linearGradient id="gi" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#6bc7ff" stop-opacity="0.3"/><stop offset="100%" stop-color="#6bc7ff" stop-opacity="0.05"/></linearGradient></defs>
  <path d="M26 84h76" stroke="#6bc7ff" stroke-width="6" stroke-linecap="round"/>
  <path d="M34 74l16-18 14 10 28-30" fill="none" stroke="#67e8a5" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="92" cy="36" r="7" fill="#67e8a5"/>
</svg>
""".strip()
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/diagnostics/us-session-check")
def us_session_check() -> dict:
    state = load_company_state()
    us_payload = USStockDeskAgent().run().payload
    simulated_session = {
        **(state.session_state or {}),
        "us_premarket": False,
        "us_regular": True,
        "market_phase": "us_regular",
    }
    us_plan = build_us_plan(state.stance, state.regime, us_payload, simulated_session)
    execution_agent = ExecutionAgent()
    execution_agent.configure(
        strategy_book={"crypto_plan": {}, "korea_plan": {}, "us_plan": us_plan},
        regime=state.regime,
        market_snapshot={"us_leaders": us_payload.get("leaders", [])},
        open_positions=[item for item in state.open_positions if item.get("desk") == "us"],
        closed_positions=load_closed_positions(limit=12),
        allow_new_entries=state.allow_new_entries,
        risk_budget=state.risk_budget,
    )
    execution = execution_agent.run().payload.get("orders", [])
    us_order = next((item for item in execution if item.get("desk") == "us"), {})
    return {
        "session": simulated_session,
        "us_payload": us_payload,
        "us_plan": us_plan,
        "us_order": us_order,
        "us_data_status": get_us_data_status(),
    }


@app.get("/diagnostics/korea-session-check")
def korea_session_check() -> dict:
    state = load_company_state()
    simulated_session = {
        **(state.session_state or {}),
        "korea_open": True,
        "korea_opening_window": True,
        "korea_mid_session": False,
        "crypto_focus": False,
        "market_phase": "korea_regular",
    }
    return _simulate_decision_snapshot(state, simulated_session)


@app.get("/diagnostics/live-decision")
def live_decision() -> dict:
    state = load_company_state()
    return _simulate_decision_snapshot(state, state.session_state or {})


@app.get("/diagnostics/live-execution-health")
def live_execution_health() -> dict:
    state = load_company_state()
    execution_summary = _build_execution_summary(state)
    ops_flags = _build_ops_flags(state)
    stale_live = list(execution_summary.get("stale_live") or [])[:3]
    latest_live = execution_summary.get("latest_live")
    return {
        "updated_at": state.updated_at,
        "execution_mode": state.execution_mode,
        "allow_new_entries": state.allow_new_entries,
        "risk_budget": state.risk_budget,
        "ops_severity": ops_flags.get("severity", "stable"),
        "execution_summary": execution_summary,
        "latest_live": latest_live,
        "stale_live": stale_live,
        "ops_items": (ops_flags.get("items") or [])[:5],
        "notes": [
            note
            for note in (state.notes or [])
            if "live" in str(note).lower() or "execution" in str(note).lower() or "broker" in str(note).lower()
        ][:8],
    }


@app.get("/diagnostics/broker-live-health")
def broker_live_health() -> dict:
    state = load_company_state()
    execution_summary = _build_execution_summary(state)
    upbit_latest = _latest_live_order_for_mode(state, "upbit_live")
    kis_latest = _latest_live_order_for_mode(state, "kis_live")

    def upbit_snapshot() -> dict:
        snapshot = {
            "enabled": bool(settings.upbit_allow_live and settings.upbit_access_key and settings.upbit_secret_key),
            "configured": bool(settings.upbit_access_key and settings.upbit_secret_key),
            "balances_ok": False,
            "balances_count": 0,
            "latest_order_check_ok": False,
            "latest_order_state": None,
            "latest_order_error": None,
        }
        if not snapshot["configured"]:
            snapshot["latest_order_error"] = "missing_credentials"
            return snapshot
        try:
            balances = get_upbit_account_positions()
            snapshot["balances_ok"] = True
            snapshot["balances_count"] = len(balances)
        except Exception as exc:
            snapshot["latest_order_error"] = f"balances_failed: {exc}"
        order_id = str((upbit_latest or {}).get("broker_order_id") or "")
        if order_id:
            try:
                payload = get_upbit_order(order_id)
                snapshot["latest_order_check_ok"] = True
                snapshot["latest_order_state"] = normalize_upbit_order_state(payload)
            except Exception as exc:
                snapshot["latest_order_error"] = f"order_failed: {exc}"
        return snapshot

    def kis_snapshot() -> dict:
        snapshot = {
            "enabled": bool(
                settings.kis_allow_live
                and settings.kis_app_key
                and settings.kis_app_secret
                and settings.kis_account_no
                and settings.kis_product_code
            ),
            "configured": bool(
                settings.kis_app_key
                and settings.kis_app_secret
                and settings.kis_account_no
                and settings.kis_product_code
            ),
            "balances_ok": False,
            "balances_count": 0,
            "latest_order_check_ok": False,
            "latest_order_state": None,
            "latest_order_error": None,
        }
        if not snapshot["configured"]:
            snapshot["latest_order_error"] = "missing_credentials"
            return snapshot
        try:
            balances = get_kis_account_positions()
            snapshot["balances_ok"] = True
            snapshot["balances_count"] = len(balances)
        except Exception as exc:
            snapshot["latest_order_error"] = f"balances_failed: {exc}"
        order_id = str((kis_latest or {}).get("broker_order_id") or "")
        symbol = str((kis_latest or {}).get("symbol") or "")
        side_hint = "sell" if str((kis_latest or {}).get("action") or "") in {"reduce_risk", "capital_preservation"} else "buy"
        if order_id:
            try:
                payload = get_kis_order(order_id, symbol=symbol, side_hint=side_hint)
                snapshot["latest_order_check_ok"] = True
                snapshot["latest_order_state"] = normalize_kis_order_state(payload)
            except Exception as exc:
                snapshot["latest_order_error"] = f"order_failed: {exc}"
        return snapshot

    return {
        "updated_at": state.updated_at,
        "execution_mode": state.execution_mode,
        "execution_summary": execution_summary,
        "upbit": upbit_snapshot(),
        "kis": kis_snapshot(),
        "latest_live_orders": {
            "upbit_live": upbit_latest,
            "kis_live": kis_latest,
        },
    }


@app.get("/diagnostics/live-readiness-checklist")
def live_readiness_checklist() -> dict:
    state = load_company_state()
    execution_summary = _build_execution_summary(state)
    broker_health = broker_live_health()
    checklist: list[dict] = []

    checklist.append(
        _check_item(
            "pass" if settings.live_capital_krw > 0 else "block",
            "Live Capital",
            f"LIVE_CAPITAL_KRW={settings.live_capital_krw}",
        )
    )
    checklist.append(
        _check_item(
            "pass" if bool(settings.app_username and settings.app_password) else "warn",
            "App Auth",
            "basic auth configured" if settings.app_username and settings.app_password else "dashboard basic auth not configured",
        )
    )
    checklist.append(
        _check_item(
            "pass" if notifier.enabled else "warn",
            "Telegram",
            "telegram enabled" if notifier.enabled else "telegram disabled",
        )
    )

    mode = str(state.execution_mode or "paper")
    if mode == "paper":
        checklist.append(_check_item("warn", "Execution Mode", "currently paper mode"))
    else:
        checklist.append(_check_item("pass", "Execution Mode", f"current mode {mode}"))

    for broker_name in ("upbit", "kis"):
        health = broker_health.get(broker_name, {}) or {}
        broker_enabled = bool(health.get("enabled"))
        configured = bool(health.get("configured"))
        balances_ok = bool(health.get("balances_ok"))
        latest_order_ok = bool(health.get("latest_order_check_ok"))
        latest_order_state = health.get("latest_order_state") or {}
        latest_error = str(health.get("latest_order_error") or "")

        checklist.append(
            _check_item(
                "pass" if configured else "warn",
                f"{broker_name.upper()} Credentials",
                "configured" if configured else latest_error or "missing credentials",
            )
        )
        checklist.append(
            _check_item(
                "pass" if broker_enabled else "warn",
                f"{broker_name.upper()} Live Switch",
                "live enabled" if broker_enabled else "live disabled",
            )
        )
        checklist.append(
            _check_item(
                "pass" if balances_ok else ("warn" if configured else "warn"),
                f"{broker_name.upper()} Balance Check",
                f"balances_ok={balances_ok} count={health.get('balances_count', 0)}",
            )
        )
        if latest_order_ok:
            checklist.append(
                _check_item(
                    "pass",
                    f"{broker_name.upper()} Order Lookup",
                    f"latest order status={latest_order_state.get('request_status', 'n/a')} / broker={latest_order_state.get('broker_state', 'n/a')}",
                )
            )
        else:
            checklist.append(
                _check_item(
                    "warn" if configured else "warn",
                    f"{broker_name.upper()} Order Lookup",
                    latest_error or "no recent live order to verify",
                )
            )

    pending_count = int(execution_summary.get("pending_count", 0) or 0)
    partial_count = int(execution_summary.get("partial_count", 0) or 0)
    stale_count = int(execution_summary.get("stale_count", 0) or 0)
    allow_new_entries = bool(state.allow_new_entries)

    checklist.append(
        _check_item(
            "pass" if pending_count == 0 else "warn",
            "Pending Live Orders",
            f"pending={pending_count} / partial={partial_count}",
        )
    )
    checklist.append(
        _check_item(
            "pass" if stale_count == 0 else "block",
            "Stale Live Orders",
            f"stale={stale_count}",
        )
    )
    checklist.append(
        _check_item(
            "pass" if allow_new_entries else "warn",
            "Entry Gate",
            "new entries allowed" if allow_new_entries else "new entries blocked by risk/conservative mode",
        )
    )

    block_count = sum(1 for item in checklist if item["status"] == "block")
    warn_count = sum(1 for item in checklist if item["status"] == "warn")
    overall = "blocked" if block_count > 0 else "caution" if warn_count > 0 else "ready"

    return {
        "updated_at": state.updated_at,
        "overall": overall,
        "block_count": block_count,
        "warn_count": warn_count,
        "execution_mode": mode,
        "execution_summary": execution_summary,
        "checklist": checklist,
        "notes": (state.notes or [])[-8:],
    }


@app.post("/cycle")
def cycle() -> dict:
    return orchestrator.run_cycle()


@app.post("/telegram-test")
def telegram_test() -> dict:
    success = notifier.send(f"[{settings.company_name}] telegram manual test")
    return {"ok": success, "error": notifier.last_error or None}


@app.get("/performance")
def performance() -> dict:
    return {
        "stats": load_performance_quick_stats(),
        "open_positions": [p.model_dump() for p in load_open_positions()],
        "closed_positions": load_closed_positions(limit=50),
    }


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#09111f">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="{settings.company_name}">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/app-icon.svg" type="image/svg+xml">
  <title>{settings.company_name}</title>
  <style>
    :root {{
      --bg:#09111f;--surface:rgba(10,19,35,.84);--surface2:rgba(19,34,57,.9);
      --border:rgba(141,177,199,.18);--text:#eef6ff;--muted:#97aabf;
      --green:#67e8a5;--red:#ff7c7c;--blue:#6bc7ff;--yellow:#ffd36e;--orange:#ff9a62;
      --font:'Aptos','Bahnschrift','Malgun Gothic',sans-serif;
      --mono:'IBM Plex Mono','D2Coding','Consolas',monospace;
      --radius:18px;--shadow:0 30px 80px rgba(0,0,0,.28);
    }}
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;line-height:1.5;-webkit-font-smoothing:antialiased}}
    .app{{position:relative;max-width:1480px;margin:0 auto;padding:20px 18px 60px}}
    .app-glow{{position:fixed;top:0;left:50%;transform:translateX(-50%);width:900px;height:400px;background:radial-gradient(ellipse at top,rgba(107,199,255,.08) 0%,transparent 70%);pointer-events:none;z-index:0}}
    .hero-shell{{position:relative;z-index:1;display:flex;justify-content:space-between;align-items:center;padding:18px 24px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);backdrop-filter:blur(20px);margin-bottom:16px;flex-wrap:wrap;gap:12px}}
    .hero-brand{{display:flex;align-items:center;gap:14px}}
    .brand-icon{{width:44px;height:44px;border-radius:12px;background:linear-gradient(145deg,rgba(107,199,255,.25),rgba(107,199,255,.08));border:1px solid rgba(107,199,255,.3);display:grid;place-items:center;font-size:.78rem;font-weight:700;color:var(--blue);letter-spacing:.04em;flex-shrink:0}}
    .brand-eyebrow{{font-size:.68rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}}
    .brand-name{{font-size:1.1rem;font-weight:700;color:var(--text)}}
    .status-pill{{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:.75rem;font-weight:600;background:rgba(103,232,165,.12);border:1px solid rgba(103,232,165,.3);color:var(--green)}}
    .status-pill.loading{{background:rgba(151,170,191,.12);border-color:rgba(151,170,191,.3);color:var(--muted)}}
    .status-pill.error{{background:rgba(255,124,124,.12);border-color:rgba(255,124,124,.3);color:var(--red)}}
    .status-pill::before{{content:'●';font-size:.6rem}}
    .hero-actions{{display:flex;align-items:center;gap:12px}}
    .update-time{{font-size:.75rem;color:var(--muted);font-family:var(--mono)}}
    .btn-cycle{{padding:8px 18px;border-radius:10px;border:1px solid rgba(107,199,255,.3);background:rgba(107,199,255,.1);color:var(--blue);font-size:.82rem;font-weight:600;cursor:pointer;transition:all .15s}}
    .btn-cycle:hover{{background:rgba(107,199,255,.2)}}
    .btn-cycle:disabled{{opacity:.5;cursor:not-allowed}}
    .hero-overview{{position:relative;z-index:1;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}}
    .overview-card{{padding:16px 18px;background:var(--surface);border:1px solid var(--border);border-radius:14px;backdrop-filter:blur(16px)}}
    .ov-label{{font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:6px}}
    .ov-value{{font-size:1.1rem;font-weight:700}}
    .ov-sub{{font-size:.7rem;color:var(--muted);margin-top:3px}}
    .overview-card.tone-ok{{border-color:rgba(103,232,165,.3)}}
    .overview-card.tone-warn{{border-color:rgba(255,211,110,.3)}}
    .overview-card.tone-risk{{border-color:rgba(255,154,98,.3)}}
    .dashboard{{position:relative;z-index:1;display:grid;grid-template-columns:1.15fr .85fr;gap:14px}}
    .col-left,.col-right{{display:flex;flex-direction:column;gap:14px}}
    .panel{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);backdrop-filter:blur(16px);overflow:hidden}}
    .panel-title{{padding:14px 18px 10px;font-size:.78rem;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}}
    .badge,.insight-badge{{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:22px;padding:0 6px;border-radius:6px;background:rgba(107,199,255,.12);color:var(--blue);font-size:.72rem;font-weight:700}}
    .area-cards{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}
    .stat-card{{padding:16px;background:var(--surface2);border:1px solid var(--border);border-radius:14px}}
    .sc-label{{font-size:.7rem;color:var(--muted);margin-bottom:4px}}
    .sc-value{{font-size:1.3rem;font-weight:700}}
    .sc-sub{{font-size:.72rem;color:var(--muted);margin-top:2px}}
    .execution-strip{{padding:4px 0 8px}}
    .desk-row{{display:flex;align-items:center;gap:8px;padding:10px 18px;border-bottom:1px solid var(--border)}}
    .desk-row:last-child{{border-bottom:none}}
    .desk-tag{{width:52px;font-size:.66rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);flex-shrink:0}}
    .desk-size{{font-size:.72rem;color:var(--blue);font-family:var(--mono);flex-shrink:0}}
    .desk-focus{{font-size:.72rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}}
    .action-pill{{display:inline-block;padding:2px 8px;border-radius:6px;font-size:.7rem;font-weight:600;white-space:nowrap;flex-shrink:0}}
    .action-pill.buy{{background:rgba(103,232,165,.15);color:var(--green)}}
    .action-pill.sell{{background:rgba(255,124,124,.15);color:var(--red)}}
    .action-pill.watch{{background:rgba(151,170,191,.12);color:var(--muted)}}
    .action-pill.probe{{background:rgba(107,199,255,.12);color:var(--blue)}}
    .action-pill.attack{{background:rgba(255,211,110,.12);color:var(--yellow)}}
    .pos-table{{width:100%;border-collapse:collapse;font-size:.78rem}}
    .pos-table th{{padding:8px 14px;text-align:left;font-size:.67rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);background:rgba(19,34,57,.5)}}
    .pos-table td{{padding:10px 14px;border-top:1px solid var(--border)}}
    .pos-table tr:hover td{{background:rgba(107,199,255,.04)}}
    .symbol-cell{{font-family:var(--mono);font-weight:600}}
    .desk-chip{{display:inline-block;padding:2px 6px;border-radius:4px;font-size:.66rem;font-weight:600;background:rgba(107,199,255,.1);color:var(--blue)}}
    .empty-msg{{padding:20px 18px;color:var(--muted);font-size:.8rem;text-align:center}}
    #equity-svg{{width:100%;height:140px;display:block}}
    .insights-list,.journal-list{{padding:4px 0}}
    .insight-row{{display:flex;align-items:center;gap:10px;padding:8px 18px;border-bottom:1px solid rgba(141,177,199,.08)}}
    .insight-row:last-child{{border-bottom:none}}
    .ins-name{{flex:1;font-size:.8rem}}
    .ins-score{{font-family:var(--mono);font-size:.8rem;font-weight:700;min-width:36px;text-align:right}}
    .ins-bar{{width:80px;height:4px;background:rgba(141,177,199,.15);border-radius:2px;overflow:hidden;flex-shrink:0}}
    .ins-bar-fill{{height:100%;border-radius:2px;transition:width .4s}}
    .journal-row{{padding:8px 18px;border-bottom:1px solid rgba(141,177,199,.08);font-size:.76rem;color:var(--muted)}}
    .journal-row:last-child{{border-bottom:none}}
    .journal-time{{font-family:var(--mono);color:var(--blue);margin-right:8px}}
    .pos{{color:var(--green)!important}}.neg{{color:var(--red)!important}}.neutral{{color:var(--text)!important}}
    .tone-ok{{color:var(--green)!important}}.tone-warn{{color:var(--yellow)!important}}.tone-risk{{color:var(--orange)!important}}.tone-danger{{color:var(--red)!important}}.tone-muted{{color:var(--muted)!important}}.tone-blue{{color:var(--blue)!important}}
    @media(max-width:960px){{.dashboard{{grid-template-columns:1fr}}.hero-overview{{grid-template-columns:repeat(2,1fr)}}}}
    @media(max-width:600px){{.area-cards{{grid-template-columns:repeat(2,1fr)}}.hero-shell{{flex-direction:column;align-items:flex-start}}}}
  </style>
</head>
<body>
<div class="app">
  <div class="app-glow"></div>
  <header class="hero-shell">
    <div class="hero-brand">
      <div class="brand-icon">TC</div>
      <div>
        <div class="brand-eyebrow">Trading Company</div>
        <div class="brand-name">{settings.company_name}</div>
      </div>
      <span class="status-pill loading" id="status-pill">연결 중...</span>
    </div>
    <div class="hero-actions">
      <span class="update-time" id="update-time">--:--</span>
      <button class="btn-cycle" id="cycle-btn" onclick="runCycle()">사이클 실행</button>
    </div>
  </header>
  <div class="hero-overview">
    <div class="overview-card" id="ov-stance">
      <div class="ov-label">Stance</div>
      <div class="ov-value" id="ov-stance-val">--</div>
    </div>
    <div class="overview-card" id="ov-regime">
      <div class="ov-label">Regime</div>
      <div class="ov-value" id="ov-regime-val">--</div>
    </div>
    <div class="overview-card" id="ov-exposure">
      <div class="ov-label">Exposure</div>
      <div class="ov-value" id="ov-exposure-val">--</div>
      <div class="ov-sub" id="ov-entries">--</div>
    </div>
    <div class="overview-card" id="ov-ops">
      <div class="ov-label">Ops</div>
      <div class="ov-value" id="ov-ops-val">--</div>
    </div>
  </div>
  <div class="dashboard">
    <div class="col-left">
      <div class="area-cards">
        <div class="stat-card">
          <div class="sc-label">실현 손익</div>
          <div class="sc-value" id="sc-realized">--</div>
          <div class="sc-sub" id="sc-realized-krw">--</div>
        </div>
        <div class="stat-card">
          <div class="sc-label">미실현 손익</div>
          <div class="sc-value" id="sc-unrealized">--</div>
          <div class="sc-sub" id="sc-unrealized-krw">--</div>
        </div>
        <div class="stat-card">
          <div class="sc-label">승률 / 기대값</div>
          <div class="sc-value" id="sc-winrate">--</div>
          <div class="sc-sub" id="sc-trades">--</div>
        </div>
        <div class="stat-card">
          <div class="sc-label">포트폴리오</div>
          <div class="sc-value" id="sc-capital">--</div>
          <div class="sc-sub" id="sc-capital-base">--</div>
        </div>
      </div>
      <div class="panel execution-strip">
        <div class="panel-title">데스크 현황</div>
        <div id="desk-rows"></div>
      </div>
      <div class="panel">
        <div class="panel-title">오픈 포지션 <span class="badge" id="pos-count">0</span></div>
        <div id="positions-body"><div class="empty-msg">포지션 없음</div></div>
      </div>
      <div class="panel">
        <div class="panel-title">최근 청산</div>
        <div id="trades-body"><div class="empty-msg">청산 내역 없음</div></div>
      </div>
    </div>
    <div class="col-right">
      <div class="panel">
        <div class="panel-title">에쿼티 커브</div>
        <svg id="equity-svg" viewBox="0 0 400 140" preserveAspectRatio="none"></svg>
      </div>
      <div class="panel">
        <div class="panel-title">에이전트 시그널 <span class="insight-badge" id="insight-score">--</span></div>
        <div id="insights-body" class="insights-list"></div>
      </div>
      <div class="panel">
        <div class="panel-title">사이클 저널</div>
        <div id="journal-body" class="journal-list"><div class="empty-msg">저널 없음</div></div>
      </div>
    </div>
  </div>
</div>
<script>
  function fmtPct(v) {{
    var n = parseFloat(v) || 0;
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }}
  function fmtKrw(v) {{
    var n = parseInt(v) || 0;
    return (n >= 0 ? '+' : '') + n.toLocaleString('ko-KR') + '원';
  }}
  function pctCls(v) {{
    var n = parseFloat(v) || 0;
    return n > 0 ? 'pos' : n < 0 ? 'neg' : 'neutral';
  }}
  function actionCls(a) {{
    if (!a) return 'watch';
    var s = a.toLowerCase();
    if (s.indexOf('attack') >= 0 || s.indexOf('probe_long') >= 0) return 'buy';
    if (s.indexOf('reduce') >= 0 || s.indexOf('preservation') >= 0) return 'sell';
    if (s.indexOf('probe') >= 0 || s.indexOf('selective') >= 0) return 'probe';
    return 'watch';
  }}
  function stanceTone(s) {{
    if (!s) return 'tone-muted';
    var v = s.toUpperCase();
    return v === 'OFFENSE' ? 'tone-ok' : v === 'DEFENSE' ? 'tone-risk' : 'tone-blue';
  }}
  function regimeTone(r) {{
    if (!r) return 'tone-muted';
    var v = r.toUpperCase();
    if (v === 'TRENDING') return 'tone-ok';
    if (v === 'STRESSED') return 'tone-danger';
    if (v === 'RANGING') return 'tone-warn';
    return 'tone-muted';
  }}
  function renderEquity(pts) {{
    var svg = document.getElementById('equity-svg');
    if (!pts || pts.length < 2) {{
      svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#97aabf" font-size="12">데이터 없음</text>';
      return;
    }}
    var W = 400, H = 140, PAD = 24;
    var vals = pts.map(function(p) {{ return p.equity; }});
    var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals), rng = mx - mn || 1;
    var toX = function(i) {{ return PAD + (i / (pts.length - 1)) * (W - PAD * 2); }};
    var toY = function(val) {{ return PAD + ((mx - val) / rng) * (H - PAD * 2); }};
    var coords = pts.map(function(p, i) {{ return toX(i).toFixed(1) + ',' + toY(p.equity).toFixed(1); }}).join(' ');
    var last = pts[pts.length - 1];
    var col = last.equity >= 100 ? '#67e8a5' : '#ff7c7c';
    var fillPts = toX(0).toFixed(1) + ',' + H + ' ' + coords + ' ' + toX(pts.length - 1).toFixed(1) + ',' + H;
    var dots = pts.map(function(p, i) {{
      return i === pts.length - 1
        ? '<circle cx="' + toX(i).toFixed(1) + '" cy="' + toY(p.equity).toFixed(1) + '" r="4" fill="' + col + '"/>'
        : '';
    }}).join('');
    var lbls = pts.map(function(p, i) {{
      return '<text x="' + toX(i).toFixed(1) + '" y="' + (H - 4) + '" text-anchor="middle" fill="#97aabf" font-size="9">' + p.label + '</text>';
    }}).join('');
    svg.innerHTML = '<defs><linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0%" stop-color="' + col + '" stop-opacity="0.25"/>'
      + '<stop offset="100%" stop-color="' + col + '" stop-opacity="0"/>'
      + '</linearGradient></defs>'
      + '<polygon points="' + fillPts + '" fill="url(#eg)"/>'
      + '<polyline points="' + coords + '" fill="none" stroke="' + col + '" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
      + dots + lbls;
  }}
  function renderDesks(desks) {{
    if (!desks) return;
    var el = document.getElementById('desk-rows');
    var items = [['CRYPTO','crypto'],['KR','korea'],['US','us']];
    var html = '';
    for (var i = 0; i < items.length; i++) {{
      var lbl = items[i][0], k = items[i][1];
      var d = desks[k] || {{}};
      html += '<div class="desk-row">'
        + '<span class="desk-tag">' + lbl + '</span>'
        + '<span class="action-pill ' + actionCls(d.action) + '">' + (d.action || 'n/a') + '</span>'
        + '<span class="desk-focus" title="' + (d.focus || '') + '">' + (d.focus || '') + '</span>'
        + '<span class="desk-size">' + (d.size || '0.00x') + '</span>'
        + '</div>';
    }}
    el.innerHTML = html;
  }}
  function renderPositions(pos) {{
    var el = document.getElementById('positions-body');
    var cnt = document.getElementById('pos-count');
    if (!pos || !pos.length) {{
      cnt.textContent = '0';
      el.innerHTML = '<div class="empty-msg">오픈 포지션 없음</div>';
      return;
    }}
    cnt.textContent = pos.length;
    var rows = '';
    for (var i = 0; i < pos.length; i++) {{
      var p = pos[i];
      var pnl = parseFloat(p.unrealized_pnl_pct) || 0;
      rows += '<tr>'
        + '<td><span class="symbol-cell">' + (p.symbol || '--') + '</span></td>'
        + '<td><span class="desk-chip">' + (p.desk || '--') + '</span></td>'
        + '<td style="font-family:var(--mono)">' + parseFloat(p.entry_price || 0).toLocaleString() + '</td>'
        + '<td class="' + pctCls(pnl) + '" style="font-family:var(--mono)">' + fmtPct(pnl) + '</td>'
        + '<td style="font-size:.7rem;color:var(--muted)">' + (p.opened_at || '').slice(11, 16) + '</td>'
        + '</tr>';
    }}
    el.innerHTML = '<table class="pos-table"><thead><tr>'
      + '<th>심볼</th><th>데스크</th><th>진입가</th><th>미실현</th><th>시간</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>';
  }}
  function renderTrades(closed) {{
    var el = document.getElementById('trades-body');
    if (!closed || !closed.length) {{
      el.innerHTML = '<div class="empty-msg">청산 내역 없음</div>';
      return;
    }}
    var rows = '';
    var items = closed.slice(0, 6);
    for (var i = 0; i < items.length; i++) {{
      var t = items[i];
      var pnl = parseFloat(t.pnl_pct) || 0;
      rows += '<tr>'
        + '<td><span class="symbol-cell">' + (t.symbol || '--') + '</span></td>'
        + '<td><span class="desk-chip">' + (t.desk || '--') + '</span></td>'
        + '<td class="' + pctCls(pnl) + '" style="font-family:var(--mono)">' + fmtPct(pnl) + '</td>'
        + '<td style="font-size:.7rem;color:var(--muted)">' + (t.closed_reason || '--') + '</td>'
        + '<td style="font-size:.7rem;color:var(--muted)">' + (t.closed_at || '').slice(11, 16) + '</td>'
        + '</tr>';
    }}
    el.innerHTML = '<table class="pos-table"><thead><tr>'
      + '<th>심볼</th><th>데스크</th><th>손익</th><th>사유</th><th>시간</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>';
  }}
  function renderInsights(runs) {{
    var el = document.getElementById('insights-body');
    if (!runs || !runs.length) {{
      el.innerHTML = '<div class="empty-msg">에이전트 데이터 없음</div>';
      return;
    }}
    var html = '';
    for (var i = 0; i < runs.length; i++) {{
      var a = runs[i];
      var sc = Math.round((parseFloat(a.score) || 0) * 100);
      var col = sc >= 75 ? 'var(--green)' : sc >= 55 ? 'var(--blue)' : 'var(--red)';
      var cls = sc >= 75 ? 'tone-ok' : sc >= 55 ? 'tone-blue' : sc < 35 ? 'tone-danger' : 'tone-muted';
      html += '<div class="insight-row">'
        + '<span class="ins-name">' + (a.agent_name || a.name || '--') + '</span>'
        + '<div class="ins-bar"><div class="ins-bar-fill" style="width:' + sc + '%;background:' + col + '"></div></div>'
        + '<span class="ins-score ' + cls + '">' + sc + '</span>'
        + '</div>';
    }}
    el.innerHTML = html;
  }}
  function renderJournal(notes) {{
    var el = document.getElementById('journal-body');
    if (!notes || !notes.length) {{
      el.innerHTML = '<div class="empty-msg">저널 없음</div>';
      return;
    }}
    var html = '';
    var items = notes.slice(0, 8);
    for (var i = 0; i < items.length; i++) {{
      var note = items[i];
      var txt = typeof note === 'string' ? note : JSON.stringify(note);
      var m = txt.match(/^(\\d\\d:\\d\\d)/);
      if (m) {{
        html += '<div class="journal-row"><span class="journal-time">' + m[1] + '</span>' + txt.slice(m[1].length).trim() + '</div>';
      }} else {{
        html += '<div class="journal-row">' + txt + '</div>';
      }}
    }}
    el.innerHTML = html;
  }}
  async function loadData() {{
    try {{
      var r1 = await fetch('/dashboard-data');
      var r2 = await fetch('/health');
      var data = await r1.json();
      var st = data.state || {{}};
      var dash = data.dashboard || {{}};
      var perf = dash.performance || {{}};
      var cap = dash.capital || {{}};
      var exp = dash.exposure || {{}};
      var ops = dash.ops_flags || {{}};
      var pill = document.getElementById('status-pill');
      pill.textContent = st.regime || 'LIVE';
      pill.className = 'status-pill';
      var t = (st.updated_at || '').slice(11, 16);
      document.getElementById('update-time').textContent = t || '--:--';
      var sv = document.getElementById('ov-stance-val');
      sv.textContent = st.stance || '--';
      sv.className = 'ov-value ' + stanceTone(st.stance);
      document.getElementById('ov-stance').className = 'overview-card'
        + (st.stance === 'OFFENSE' ? ' tone-ok' : st.stance === 'DEFENSE' ? ' tone-risk' : '');
      var rv = document.getElementById('ov-regime-val');
      rv.textContent = st.regime || '--';
      rv.className = 'ov-value ' + regimeTone(st.regime);
      document.getElementById('ov-regime').className = 'overview-card'
        + (st.regime === 'TRENDING' ? ' tone-ok' : st.regime === 'STRESSED' ? ' tone-risk' : st.regime === 'RANGING' ? ' tone-warn' : '');
      var gross = parseFloat(exp.gross_open_notional_pct || 0);
      var evEl = document.getElementById('ov-exposure-val');
      evEl.textContent = (gross * 100).toFixed(0) + '%';
      evEl.className = 'ov-value ' + (gross >= 0.8 ? 'tone-warn' : 'tone-ok');
      document.getElementById('ov-entries').textContent = exp.allow_new_entries ? '진입 허용' : '진입 차단';
      document.getElementById('ov-exposure').className = 'overview-card' + (gross >= 0.8 ? ' tone-warn' : '');
      var sev = ops.severity || 'stable';
      var ovEl = document.getElementById('ov-ops-val');
      ovEl.textContent = sev === 'stable' ? '정상' : sev === 'warning' ? '주의' : '경고';
      ovEl.className = 'ov-value ' + (sev === 'stable' ? 'tone-ok' : sev === 'warning' ? 'tone-warn' : 'tone-danger');
      document.getElementById('ov-ops').className = 'overview-card'
        + (sev === 'warning' ? ' tone-warn' : sev !== 'stable' ? ' tone-risk' : '');
      var rzEl = document.getElementById('sc-realized');
      rzEl.textContent = fmtPct(perf.realized_pnl_pct);
      rzEl.className = 'sc-value ' + pctCls(perf.realized_pnl_pct);
      document.getElementById('sc-realized-krw').textContent = fmtKrw(perf.realized_pnl_krw);
      var uzEl = document.getElementById('sc-unrealized');
      uzEl.textContent = fmtPct(perf.unrealized_pnl_pct);
      uzEl.className = 'sc-value ' + pctCls(perf.unrealized_pnl_pct);
      document.getElementById('sc-unrealized-krw').textContent = fmtKrw(perf.unrealized_pnl_krw);
      var wr = parseFloat(perf.win_rate || 0);
      var wrEl = document.getElementById('sc-winrate');
      wrEl.textContent = wr.toFixed(1) + '%';
      wrEl.className = 'sc-value ' + (wr >= 55 ? 'pos' : wr < 40 ? 'neg' : 'neutral');
      document.getElementById('sc-trades').textContent =
        (perf.wins || 0) + '승 ' + (perf.losses || 0) + '패 · 기대 ' + fmtPct(perf.expectancy_pct);
      document.getElementById('sc-capital').textContent =
        (parseInt(cap.total_krw) || 0).toLocaleString('ko-KR') + '원';
      document.getElementById('sc-capital-base').textContent =
        '기준 ' + (parseInt(cap.base_krw) || 0).toLocaleString('ko-KR') + '원';
      renderEquity(dash.equity_curve || []);
      renderDesks(dash.desk_status || {{}});
      renderPositions(dash.open_positions || []);
      renderTrades(dash.closed_positions || []);
      document.getElementById('insight-score').textContent = dash.insight_score || '--';
      renderInsights(st.agent_runs || []);
      renderJournal(st.notes || []);
    }} catch (err) {{
      var pill2 = document.getElementById('status-pill');
      pill2.textContent = '오류: ' + err.message;
      pill2.className = 'status-pill error';
    }}
  }}
  async function runCycle() {{
    var btn = document.getElementById('cycle-btn');
    btn.disabled = true;
    btn.textContent = '실행 중...';
    try {{
      await fetch('/cycle', {{ method: 'POST' }});
      await loadData();
    }} catch (e) {{
      console.error(e);
    }} finally {{
      btn.disabled = false;
      btn.textContent = '사이클 실행';
    }}
  }}
  setInterval(function() {{ loadData().catch(function() {{}}); }}, 20000);
  loadData().catch(function() {{
    document.getElementById('status-pill').textContent = '연결 실패';
    document.getElementById('status-pill').className = 'status-pill error';
  }});
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)

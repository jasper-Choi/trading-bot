from __future__ import annotations

import secrets
from base64 import b64decode

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
from app.services.market_gateway import get_naver_daily_prices, get_upbit_15m_candles, get_us_daily_prices, get_us_data_status
from app.services.recommendation_engine import build_crypto_plan, build_korea_plan, build_us_plan
from app.service_manager import local_access_urls


app = FastAPI(title="Trading Company V2", version="0.1.0")
orchestrator = CompanyOrchestrator()


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
        "background_color": "#f4f7fb",
        "theme_color": "#2563eb",
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
  <rect width="128" height="128" rx="28" fill="#2563eb"/>
  <path d="M26 84h76" stroke="#ffffff" stroke-width="8" stroke-linecap="round"/>
  <path d="M34 74l16-18 14 10 28-30" fill="none" stroke="#ffffff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="92" cy="36" r="8" fill="#93c5fd"/>
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
        "closed_positions": [p.model_dump() for p in load_closed_positions(limit=50)],
    }


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#2563eb">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <meta name="apple-mobile-web-app-title" content="{settings.company_name}">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/app-icon.svg" type="image/svg+xml">
  <title>{settings.company_name}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --surface: rgba(255, 255, 255, 0.92);
      --ink: #101828;
      --muted: #667085;
      --line: rgba(16, 24, 40, 0.08);
      --accent: #2563eb;
      --accent-strong: #1d4ed8;
      --success: #16a34a;
      --danger: #dc2626;
      --shadow: 0 18px 44px rgba(16, 24, 40, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Pretendard", "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(37,99,235,0.10), transparent 28%),
        radial-gradient(circle at top right, rgba(15,118,110,0.08), transparent 24%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      color: var(--ink);
      min-height: 100vh;
    }}
    .shell {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 24px 18px 48px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: center;
      margin-bottom: 18px;
    }}
    .brand-stack {{
      display: flex;
      gap: 14px;
      align-items: center;
    }}
    .brand-mark {{
      width: 48px;
      height: 48px;
      border-radius: 16px;
      background: linear-gradient(145deg, rgba(37,99,235,0.95), rgba(29,78,216,0.92));
      color: white;
      display: grid;
      place-items: center;
      font-size: 0.95rem;
      font-weight: 700;
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.72rem;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .headline {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
    }}
    .headline h1 {{
      margin: 0;
      font-size: clamp(1.8rem, 2vw, 2.4rem);
    }}
    .live-pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 7px 12px;
      background: rgba(255,255,255,0.75);
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.82rem;
    }}
    .live-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--success);
    }}
    .action-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
    }}
    .ghost-note {{
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .cta {{
      border: none;
      border-radius: 999px;
      background: linear-gradient(135deg, var(--ink), #2c3c35);
      color: white;
      padding: 12px 18px;
      font-size: 0.92rem;
      cursor: pointer;
      box-shadow: 0 14px 28px rgba(23,33,28,0.14);
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
      gap: 14px;
      margin-bottom: 18px;
    }}
    .panel, .section-card, .metric-card {{
      background: var(--surface);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(255,255,255,0.65);
      box-shadow: var(--shadow);
    }}
    .panel {{
      border-radius: 28px;
      padding: 24px;
    }}
    .section-card {{
      border-radius: 24px;
      padding: 20px;
    }}
    .metric-card {{
      border-radius: 20px;
      padding: 16px;
    }}
    .hero-title {{
      margin: 8px 0 10px;
      font-size: clamp(2rem, 4vw, 3rem);
      line-height: 0.98;
      letter-spacing: -0.04em;
    }}
    .hero-copy p {{
      margin: 0;
      max-width: 700px;
      font-size: 1rem;
      line-height: 1.65;
      color: var(--muted);
    }}
    .hero-tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}
    .tag {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255,255,255,0.84);
      border: 1px solid var(--line);
      color: var(--ink);
      font-size: 0.84rem;
    }}
    .score-panel {{
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .score-ring {{
      width: 168px;
      height: 168px;
      border-radius: 50%;
      margin: 0 auto 18px;
      background:
        radial-gradient(circle at center, rgba(255,255,255,0.96) 0 54%, transparent 55%),
        conic-gradient(from 210deg, #0f766e, var(--accent), var(--accent-strong), #0f766e);
      display: grid;
      place-items: center;
    }}
    .score-core {{
      text-align: center;
    }}
    .score-core strong {{
      display: block;
      font-size: 2.4rem;
      line-height: 1;
    }}
    .score-core span {{
      color: var(--muted);
      font-size: 0.8rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .score-meta {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .score-stat {{
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.88);
      border: 1px solid var(--line);
    }}
    .score-stat strong,
    .metric-card strong,
    .perf-kpi strong,
    .status-kpi strong {{
      display: block;
      font-size: 0.78rem;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .ops-banner {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 0 0 18px;
    }}
    .ops-flag {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      font-size: 0.86rem;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.9);
    }}
    .ops-flag.critical {{
      background: rgba(220, 38, 38, 0.12);
      border-color: rgba(220, 38, 38, 0.24);
      color: #991b1b;
    }}
    .ops-flag.warning {{
      background: rgba(245, 158, 11, 0.14);
      border-color: rgba(245, 158, 11, 0.26);
      color: #92400e;
    }}
    .ops-flag.info {{
      background: rgba(37, 99, 235, 0.12);
      border-color: rgba(37, 99, 235, 0.24);
      color: #1d4ed8;
    }}
    .realtime-panel {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      margin-bottom: 18px;
    }}
    .realtime-card {{
      border-radius: 18px;
      padding: 14px;
      background: rgba(255,255,255,0.88);
      border: 1px solid var(--line);
    }}
    .realtime-card strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .metrics-strip {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin-bottom: 18px;
    }}
    .metric-card span {{
      font-size: 1.15rem;
    }}
    .content-grid {{
      display: grid;
      gap: 18px;
      grid-template-columns: minmax(0, 1.28fr) minmax(330px, 0.72fr);
    }}
    .column {{
      display: grid;
      gap: 18px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: baseline;
      margin-bottom: 14px;
    }}
    .section-head h2 {{
      margin: 0;
      font-size: 1.06rem;
    }}
    .section-head span {{
      color: var(--muted);
      font-size: 0.84rem;
    }}
    .hero-chart-grid,
    .dashboard-grid,
    .market-grid,
    .two-up,
    .performance-grid {{
      display: grid;
      gap: 18px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .triple-grid {{
      display: grid;
      gap: 18px;
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    .equity-svg, .mini-chart {{
      width: 100%;
      display: block;
    }}
    .equity-svg {{
      height: 240px;
    }}
    .mini-chart {{
      height: 200px;
    }}
    .equity-grid-line {{
      stroke: rgba(91,98,87,0.12);
      stroke-width: 1;
    }}
    .equity-path {{
      fill: none;
      stroke: var(--accent-strong);
      stroke-width: 3;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .equity-area {{
      fill: rgba(37,99,235,0.10);
    }}
    .equity-dot {{
      fill: var(--accent-strong);
    }}
    .equity-labels {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-size: 0.78rem;
      color: var(--muted);
      margin-top: 8px;
    }}
    .chart-card {{
      border-radius: 18px;
      padding: 14px;
      background: rgba(248,250,252,0.96);
      border: 1px solid var(--line);
    }}
    .chart-top {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 10px;
    }}
    .chart-top strong {{
      font-size: 0.98rem;
    }}
    .chart-top span {{
      color: var(--muted);
      font-size: 0.82rem;
    }}
    .desk-chart {{
      margin-top: 14px;
    }}
    .chart-summary {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin-top: 12px;
    }}
    .chart-stat {{
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(255,255,255,0.86);
      border: 1px solid var(--line);
    }}
    .chart-stat strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 0.74rem;
      color: var(--muted);
    }}
    .chart-stat span {{
      display: block;
      font-size: 0.92rem;
      font-weight: 600;
    }}
    .status-card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.94), rgba(243,247,255,0.92));
    }}
    .status-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }}
    .status-pill {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(37,99,235,0.10);
      color: var(--accent-strong);
      font-size: 0.76rem;
      text-transform: uppercase;
    }}
    .status-subline {{
      margin: 12px 0 0;
      color: var(--muted);
      line-height: 1.45;
      min-height: 48px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      word-break: break-word;
    }}
    .status-kpis {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      margin-top: 12px;
    }}
    .status-kpi, .perf-kpi {{
      padding: 12px;
      border-radius: 14px;
      background: rgba(255,255,255,0.80);
      border: 1px solid var(--line);
      min-width: 0;
    }}
    .status-kpi span,
    .perf-kpi span {{
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .compact-list {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .compact-list li {{
      padding: 8px 0;
      border-top: 1px solid rgba(23, 33, 28, 0.08);
      margin-bottom: 0;
      line-height: 1.45;
    }}
    .compact-list li:first-child {{
      border-top: none;
      padding-top: 0;
    }}
    .list-table {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    .list-row {{
      display: grid;
      gap: 8px;
      padding: 14px 16px;
      border-radius: 16px;
      background: rgba(255,255,255,0.72);
      border: 1px solid var(--line);
    }}
    .row-top {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
    }}
    .row-title {{
      font-size: 0.96rem;
    }}
    .row-meta {{
      color: var(--muted);
      font-size: 0.82rem;
      white-space: nowrap;
    }}
    .row-foot {{
      color: var(--muted);
      font-size: 0.84rem;
      line-height: 1.45;
    }}
    .perf-kpis {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 14px;
    }}
    .trade-bars {{
      display: grid;
      gap: 10px;
    }}
    .trade-bar-row {{
      display: grid;
      gap: 6px;
    }}
    .trade-bar-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 0.84rem;
    }}
    .trade-bar-track {{
      position: relative;
      height: 12px;
      border-radius: 999px;
      background: rgba(16,24,40,0.06);
      overflow: hidden;
    }}
    .trade-bar-fill {{
      position: absolute;
      top: 0;
      height: 100%;
      border-radius: 999px;
    }}
    .trade-bar-fill.pos {{
      left: 50%;
      background: linear-gradient(90deg, #22c55e, #16a34a);
    }}
    .trade-bar-fill.neg {{
      right: 50%;
      background: linear-gradient(90deg, #f97316, #dc2626);
    }}
    .danger {{
      color: var(--danger);
    }}
    @media (max-width: 1180px) {{
      .hero,
      .content-grid,
      .hero-chart-grid,
      .triple-grid,
      .dashboard-grid,
      .market-grid,
      .two-up,
      .performance-grid {{
        grid-template-columns: 1fr;
      }}
      .metrics-strip {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .chart-summary {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 760px) {{
      .shell {{
        padding: 14px 12px 28px;
      }}
      .topbar {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .headline h1 {{
        font-size: 1.6rem;
      }}
      .metrics-strip,
      .status-kpis,
      .score-meta,
      .perf-kpis,
      .chart-summary {{
        grid-template-columns: 1fr;
      }}
      .score-ring {{
        width: 144px;
        height: 144px;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand-stack">
        <div class="brand-mark">BOT</div>
        <div>
          <div class="eyebrow">트레이딩 대시보드</div>
          <div class="headline">
            <h1>{settings.company_name}</h1>
            <div class="live-pill">
              <span class="live-dot"></span>
              <span id="updated-line">실시간 동기화 대기 중</span>
            </div>
          </div>
        </div>
      </div>
      <div class="action-row">
        <span class="ghost-note">운영자: {settings.operator_name}</span>
        <span class="ghost-note">모드: 모의매매</span>
        <button class="cta" onclick="runCycle()">사이클 1회 실행</button>
      </div>
    </header>

    <section class="hero">
      <article class="panel">
        <div class="eyebrow">운영 센터</div>
        <h2 class="hero-title">차트와 자금 흐름을 먼저 보고, 텍스트는 뒤로 둡니다.</h2>
        <div class="hero-copy">
          <p>이 화면은 수익성과 실행 품질을 한눈에 판단할 수 있도록 정리합니다. 자본, 데스크 차트, 열린 포지션, 청산 흐름을 먼저 보도록 구성합니다.</p>
        </div>
        <div class="hero-tags">
          <span class="tag">집중: <strong id="focus-metric">불러오는 중...</strong></span>
          <span class="tag">리스크 예산: <strong id="risk-metric">불러오는 중...</strong></span>
          <span class="tag">오늘 사이클: <strong id="cycles-metric">불러오는 중...</strong></span>
          <span class="tag">모의 기대값: <strong id="pnl-metric">불러오는 중...</strong></span>
        </div>
      </article>
      <aside class="panel score-panel">
        <div>
          <div class="eyebrow">시그널 품질</div>
          <div class="score-ring">
            <div class="score-core">
              <strong id="insight-score">--</strong>
              <span>인사이트 점수</span>
            </div>
          </div>
        </div>
        <div class="score-meta">
          <div class="score-stat">
            <strong>회사 상태</strong>
            <span id="state-line">불러오는 중...</span>
          </div>
          <div class="score-stat">
            <strong>자산 스냅샷</strong>
            <span id="equity-summary">불러오는 중...</span>
          </div>
        </div>
      </aside>
    </section>

    <section class="ops-banner" id="ops-banner"></section>

    <section class="realtime-panel">
      <article class="realtime-card"><strong>실시간 루프</strong><span id="runtime-live">불러오는 중..</span></article>
      <article class="realtime-card"><strong>즉시 판단</strong><span id="decision-live">불러오는 중..</span></article>
      <article class="realtime-card"><strong>다음 재평가</strong><span id="next-live">불러오는 중..</span></article>
    </section>

    <section class="metrics-strip">
      <article class="metric-card"><strong>장세</strong><span id="regime-metric">불러오는 중...</span></article>
      <article class="metric-card"><strong>승률</strong><span id="winrate-metric">불러오는 중...</span></article>
      <article class="metric-card"><strong>열린 포지션</strong><span id="open-positions-metric">불러오는 중...</span></article>
      <article class="metric-card"><strong>청산 거래</strong><span id="closed-positions-metric">불러오는 중...</span></article>
      <article class="metric-card"><strong>기준 자본</strong><span id="capital-base-metric">불러오는 중...</span></article>
      <article class="metric-card"><strong>평가 자산</strong><span id="capital-total-metric">불러오는 중...</span></article>
      <article class="metric-card"><strong>실현 손익 KRW</strong><span id="capital-realized-metric">불러오는 중...</span></article>
      <article class="metric-card"><strong>미실현 손익 KRW</strong><span id="capital-unrealized-metric">불러오는 중...</span></article>
    </section>

    <section class="content-grid">
      <div class="column">
        <article class="section-card">
          <div class="section-head">
            <h2>자산 곡선</h2>
            <span>전체 자산 흐름</span>
          </div>
          <svg class="equity-svg" viewBox="0 0 640 210" preserveAspectRatio="none">
            <line class="equity-grid-line" x1="0" y1="35" x2="640" y2="35"></line>
            <line class="equity-grid-line" x1="0" y1="105" x2="640" y2="105"></line>
            <line class="equity-grid-line" x1="0" y1="175" x2="640" y2="175"></line>
            <path id="equity-area" class="equity-area"></path>
            <path id="equity-path" class="equity-path"></path>
            <g id="equity-dots"></g>
          </svg>
          <div class="equity-labels" id="equity-labels"></div>
        </article>

        <section class="triple-grid">
          <article class="section-card status-card">
            <div class="status-head">
              <h2>코인 데스크</h2>
              <span class="status-pill" id="crypto-bias">Loading...</span>
            </div>
            <p class="status-subline" id="crypto-focus"></p>
            <div class="status-kpis">
              <div class="status-kpi"><strong>액션</strong><span id="crypto-action">-</span></div>
              <div class="status-kpi"><strong>비중</strong><span id="crypto-size">-</span></div>
              <div class="status-kpi"><strong>최근 주문</strong><span id="crypto-order">-</span></div>
            </div>
            <div class="chart-card desk-chart">
              <div class="chart-top">
                <strong id="crypto-chart-symbol">KRW-BTC</strong>
                <span id="crypto-chart-meta">Loading...</span>
              </div>
              <svg class="mini-chart" viewBox="0 0 640 200" preserveAspectRatio="none">
                <line class="equity-grid-line" x1="0" y1="40" x2="640" y2="40"></line>
                <line class="equity-grid-line" x1="0" y1="100" x2="640" y2="100"></line>
                <line class="equity-grid-line" x1="0" y1="160" x2="640" y2="160"></line>
                <g id="crypto-chart-candles"></g>
              </svg>
              <div class="chart-summary">
                <div class="chart-stat"><strong>현재가</strong><span id="crypto-last">-</span></div>
                <div class="chart-stat"><strong>변화율</strong><span id="crypto-change">-</span></div>
                <div class="chart-stat"><strong>범위</strong><span id="crypto-range">-</span></div>
                <div class="chart-stat"><strong>거래량</strong><span id="crypto-volume">-</span></div>
              </div>
            </div>
          </article>

          <article class="section-card status-card">
            <div class="status-head">
              <h2>국내주식 데스크</h2>
              <span class="status-pill" id="korea-bias">Loading...</span>
            </div>
            <p class="status-subline" id="korea-focus"></p>
            <div class="status-kpis">
              <div class="status-kpi"><strong>액션</strong><span id="korea-action">-</span></div>
              <div class="status-kpi"><strong>비중</strong><span id="korea-size">-</span></div>
              <div class="status-kpi"><strong>최근 주문</strong><span id="korea-order">-</span></div>
            </div>
            <div class="chart-card desk-chart">
              <div class="chart-top">
                <strong id="stock-chart-symbol">-</strong>
                <span id="stock-chart-meta">Loading...</span>
              </div>
              <svg class="mini-chart" viewBox="0 0 640 200" preserveAspectRatio="none">
                <line class="equity-grid-line" x1="0" y1="40" x2="640" y2="40"></line>
                <line class="equity-grid-line" x1="0" y1="100" x2="640" y2="100"></line>
                <line class="equity-grid-line" x1="0" y1="160" x2="640" y2="160"></line>
                <g id="stock-chart-candles"></g>
              </svg>
              <div class="chart-summary">
                <div class="chart-stat"><strong>현재가</strong><span id="stock-last">-</span></div>
                <div class="chart-stat"><strong>변화율</strong><span id="stock-change">-</span></div>
                <div class="chart-stat"><strong>범위</strong><span id="stock-range">-</span></div>
                <div class="chart-stat"><strong>거래량</strong><span id="stock-volume">-</span></div>
              </div>
            </div>
          </article>

          <article class="section-card status-card">
            <div class="status-head">
              <h2>미국주식 데스크</h2>
              <span class="status-pill" id="us-bias">Loading...</span>
            </div>
            <p class="status-subline" id="us-focus"></p>
            <div class="status-kpis">
              <div class="status-kpi"><strong>액션</strong><span id="us-action">-</span></div>
              <div class="status-kpi"><strong>비중</strong><span id="us-size">-</span></div>
              <div class="status-kpi"><strong>최근 주문</strong><span id="us-order">-</span></div>
            </div>
            <div class="chart-card desk-chart">
              <div class="chart-top">
                <strong id="us-chart-symbol">-</strong>
                <span id="us-chart-meta">Loading...</span>
              </div>
              <svg class="mini-chart" viewBox="0 0 640 200" preserveAspectRatio="none">
                <line class="equity-grid-line" x1="0" y1="40" x2="640" y2="40"></line>
                <line class="equity-grid-line" x1="0" y1="100" x2="640" y2="100"></line>
                <line class="equity-grid-line" x1="0" y1="160" x2="640" y2="160"></line>
                <g id="us-chart-candles"></g>
              </svg>
              <div class="chart-summary">
                <div class="chart-stat"><strong>현재가</strong><span id="us-last">-</span></div>
                <div class="chart-stat"><strong>변화율</strong><span id="us-change">-</span></div>
                <div class="chart-stat"><strong>범위</strong><span id="us-range">-</span></div>
                <div class="chart-stat"><strong>거래량</strong><span id="us-volume">-</span></div>
              </div>
            </div>
          </article>
        </section>

        <section class="performance-grid">
          <article class="section-card">
            <div class="section-head">
              <h2>거래 성과 곡선</h2>
              <span>청산 거래 누적 흐름</span>
            </div>
            <svg class="mini-chart" viewBox="0 0 640 200" preserveAspectRatio="none">
              <line class="equity-grid-line" x1="0" y1="40" x2="640" y2="40"></line>
              <line class="equity-grid-line" x1="0" y1="100" x2="640" y2="100"></line>
              <line class="equity-grid-line" x1="0" y1="160" x2="640" y2="160"></line>
              <path id="trade-curve-area" class="equity-area"></path>
              <path id="trade-curve-path" class="equity-path"></path>
              <g id="trade-curve-dots"></g>
            </svg>
            <div class="equity-labels" id="trade-curve-labels"></div>
            <div class="perf-kpis">
              <div class="perf-kpi"><strong>실현 손익</strong><span id="realized-metric">-</span></div>
              <div class="perf-kpi"><strong>미실현 손익</strong><span id="unrealized-metric">-</span></div>
              <div class="perf-kpi"><strong>승</strong><span id="wins-metric">-</span></div>
              <div class="perf-kpi"><strong>패</strong><span id="losses-metric">-</span></div>
            </div>
          </article>

          <article class="section-card">
            <div class="section-head">
              <h2>청산 거래 상세</h2>
              <span>거래별 손익 막대</span>
            </div>
            <div class="trade-bars" id="trade-bars"></div>
          </article>
        </section>

        <div class="two-up">
          <article class="section-card">
            <div class="section-head"><h2>열린 포지션</h2><span>현재 보유 위험</span></div>
            <ul class="list-table" id="open-positions"></ul>
          </article>
          <article class="section-card">
            <div class="section-head"><h2>청산 거래</h2><span>최근 종료 거래</span></div>
            <ul class="list-table" id="closed-positions"></ul>
          </article>
        </div>
      </div>

      <aside class="column">
        <article class="section-card">
          <div class="section-head"><h2>핵심 상태</h2><span>빠른 판단</span></div>
          <ul class="compact-list" id="trade-pulse"></ul>
        </article>
        <article class="section-card">
          <div class="section-head"><h2>데스크 계획</h2><span>현재 배치</span></div>
          <ul class="compact-list" id="desk-plans"></ul>
        </article>
        <article class="section-card">
          <div class="section-head"><h2>일일 요약</h2><span>오늘 집계</span></div>
          <ul class="compact-list" id="daily-summary"></ul>
        </article>
        <article class="section-card">
          <div class="section-head"><h2>문제 심볼</h2><span>손실 반복 감시</span></div>
          <ul class="compact-list" id="problem-symbols"></ul>
        </article>
        <article class="section-card">
          <div class="section-head"><h2>시그널</h2><span>현재 스택 출력</span></div>
          <ul class="compact-list" id="signals"></ul>
        </article>
        <article class="section-card">
          <div class="section-head"><h2>세션 상태</h2><span>시장 운영 구간</span></div>
          <ul class="compact-list" id="session-state"></ul>
        </article>
      </aside>
    </section>

    <section class="market-grid" style="margin-top:18px;">
      <article class="section-card">
        <div class="section-head"><h2>코인 리더보드</h2><span>관심도 높은 KRW 마켓</span></div>
        <ul class="list-table" id="crypto-leaders"></ul>
      </article>
      <article class="section-card">
        <div class="section-head"><h2>코스닥 리더보드</h2><span>갭과 유동성 상위</span></div>
        <ul class="list-table" id="stock-leaders"></ul>
      </article>
    </section>

    <section class="market-grid" style="margin-top:18px;">
      <article class="section-card">
        <div class="section-head"><h2>미국 리더보드</h2><span>핵심 ETF와 대형주 상위</span></div>
        <ul class="list-table" id="us-leaders"></ul>
      </article>
      <article class="section-card">
        <div class="section-head"><h2>시장 데이터 상태</h2><span>외부 피드 상태</span></div>
        <ul class="compact-list" id="market-data-status"></ul>
      </article>
    </section>

    <section class="market-grid" style="margin-top:18px;">
      <article class="section-card">
        <div class="section-head"><h2>실행 장부</h2><span>최근 모의 주문</span></div>
        <ul class="list-table" id="paper-blotter"></ul>
      </article>
      <article class="section-card">
        <div class="section-head"><h2>사이클 저널</h2><span>판단 스냅샷</span></div>
        <ul class="list-table" id="cycle-journal"></ul>
      </article>
    </section>

    <section class="dashboard-grid" style="margin-top:18px;">
      <article class="section-card">
        <div class="section-head"><h2>전략집</h2><span>회사 단위 플레이북</span></div>
        <ul class="compact-list" id="strategy-book"></ul>
      </article>
      <article class="section-card">
        <div class="section-head"><h2>트레이더 원칙</h2><span>내장 제약</span></div>
        <ul class="compact-list" id="principles"></ul>
      </article>
      <article class="section-card">
        <div class="section-head"><h2>데스크 뷰</h2><span>원시 데스크 payload</span></div>
        <ul class="compact-list" id="desks"></ul>
      </article>
      <article class="section-card">
        <div class="section-head"><h2>에이전트 뷰</h2><span>에이전트별 메모</span></div>
        <ul class="compact-list" id="agents"></ul>
      </article>
    </section>
  </div>

  <script>
    if ('serviceWorker' in navigator) {{
      window.addEventListener('load', () => {{
        navigator.serviceWorker.register('/service-worker.js').catch(() => null);
      }});
    }}

    function renderCurve(pathId, areaId, series, labelsTarget = null, dotsTarget = null) {{
      const safeSeries = series && series.length ? series : [100, 100];
      const values = safeSeries.map(item => typeof item === 'number' ? item : Number(item.equity || 100));
      const labels = safeSeries.map((item, idx) => typeof item === 'number' ? `${{idx + 1}}` : item.label);
      const min = Math.min(...values);
      const max = Math.max(...values);
      const range = Math.max(max - min, 1);
      const width = 640;
      const height = 200;
      const baseY = height - 16;
      const pts = values.map((value, index) => {{
        const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * (width - 24) + 12;
        const y = baseY - ((Number(value) - min) / range) * (height - 40);
        return {{ x, y, value, label: labels[index] }};
      }});
      const pathD = pts.map((pt, idx) => `${{idx === 0 ? 'M' : 'L'}}${{pt.x.toFixed(2)}},${{pt.y.toFixed(2)}}`).join(' ');
      const areaD = `${{pathD}} L ${{pts[pts.length - 1].x.toFixed(2)}},${{baseY}} L ${{pts[0].x.toFixed(2)}},${{baseY}} Z`;
      document.getElementById(pathId).setAttribute('d', pathD);
      document.getElementById(areaId).setAttribute('d', areaD);
      if (dotsTarget) {{
        document.getElementById(dotsTarget).innerHTML = pts.map(pt =>
          `<circle class="equity-dot" cx="${{pt.x.toFixed(2)}}" cy="${{pt.y.toFixed(2)}}" r="4"></circle>`
        ).join('');
      }}
      if (labelsTarget) {{
        document.getElementById(labelsTarget).innerHTML = pts.map(pt =>
          `<span>${{pt.label}}<br>${{Number(pt.value).toFixed(2)}}</span>`
        ).join('');
      }}
    }}

    function renderTradeBars(items) {{
      const safeItems = items && items.length ? items : [];
      document.getElementById('trade-bars').innerHTML = safeItems.map(item => {{
        const pnl = Number(item.pnl_pct || 0);
        const widthPct = Math.min(Math.abs(pnl) * 20, 50);
        const cls = pnl >= 0 ? 'pos' : 'neg';
        return `
          <div class="trade-bar-row">
            <div class="trade-bar-head">
              <strong>${{item.symbol}}</strong>
              <span>${{pnl}}%</span>
            </div>
            <div class="trade-bar-track">
              <div class="trade-bar-fill ${{cls}}" style="width:${{widthPct}}%;"></div>
            </div>
            <div class="row-foot">${{item.closed_reason}} / ${{item.closed_at || 'n/a'}}</div>
          </div>
        `;
      }}).join('') || '<div class="row-foot">Not enough closed trades yet.</div>';
    }}

    function renderCandles(targetId, candles) {{
      const target = document.getElementById(targetId);
      const safeCandles = candles && candles.length ? candles : [];
      if (!safeCandles.length) {{
        target.innerHTML = '';
        return;
      }}
      const width = 640;
      const height = 200;
      const topPad = 14;
      const bottomPad = 18;
      const highs = safeCandles.map(item => Number(item.high || item.close || 0));
      const lows = safeCandles.map(item => Number(item.low || item.close || 0));
      const max = Math.max(...highs);
      const min = Math.min(...lows);
      const range = Math.max(max - min, 1);
      const step = (width - 24) / safeCandles.length;
      const bodyWidth = Math.max(Math.min(step * 0.58, 16), 6);
      const toY = (value) => {{
        return topPad + ((max - Number(value)) / range) * (height - topPad - bottomPad);
      }};

      target.innerHTML = safeCandles.map((item, index) => {{
        const x = 12 + (index * step) + (step / 2);
        const open = Number(item.open || item.close || 0);
        const close = Number(item.close || 0);
        const high = Number(item.high || close || 0);
        const low = Number(item.low || close || 0);
        const openY = toY(open);
        const closeY = toY(close);
        const highY = toY(high);
        const lowY = toY(low);
        const bodyY = Math.min(openY, closeY);
        const bodyH = Math.max(Math.abs(closeY - openY), 1.8);
        const color = close >= open ? '#16a34a' : '#dc2626';
        return `
          <line x1="${{x.toFixed(2)}}" y1="${{highY.toFixed(2)}}" x2="${{x.toFixed(2)}}" y2="${{lowY.toFixed(2)}}" stroke="${{color}}" stroke-width="1.4" stroke-linecap="round"></line>
          <rect x="${{(x - bodyWidth / 2).toFixed(2)}}" y="${{bodyY.toFixed(2)}}" width="${{bodyWidth.toFixed(2)}}" height="${{bodyH.toFixed(2)}}" rx="2" fill="${{color}}" fill-opacity="0.92"></rect>
        `;
      }}).join('');
    }}

    function renderDeskStatus(prefix, desk) {{
      document.getElementById(`${{prefix}}-bias`).textContent = desk.bias || 'n/a';
      document.getElementById(`${{prefix}}-focus`).textContent = desk.focus || 'No focus';
      document.getElementById(`${{prefix}}-action`).textContent = desk.action || 'n/a';
      document.getElementById(`${{prefix}}-size`).textContent = desk.size || '0.00x';
      document.getElementById(`${{prefix}}-order`).textContent = desk.latest_order ? `${{desk.latest_order.action}} / ${{desk.latest_order.size}}` : 'No order';
    }}

    function setChartSummary(prefix, payload) {{
      const summary = payload?.summary || {{}};
      const unit = prefix === 'us' ? '$' : 'KRW ';
      document.getElementById(`${{prefix}}-last`).textContent = summary.last_close
        ? `${{unit}}${{Number(summary.last_close).toLocaleString()}}`
        : '-';
      document.getElementById(`${{prefix}}-change`).textContent = `${{Number(summary.change_pct || 0).toFixed(2)}}%`;
      document.getElementById(`${{prefix}}-range`).textContent = summary.high
        ? `H ${{Number(summary.high).toLocaleString()}} / L ${{Number(summary.low).toLocaleString()}}`
        : '-';
      document.getElementById(`${{prefix}}-volume`).textContent = summary.volume
        ? Number(summary.volume).toLocaleString()
        : '-';
    }}

    async function loadData() {{
      const res = await fetch('/dashboard-data', {{ cache: 'no-store' }});
      const data = await res.json();
      const liveRes = await fetch('/diagnostics/live-decision', {{ cache: 'no-store' }});
      const liveDecision = await liveRes.json();
      const state = data.state;
      const dashboard = data.dashboard || {{}};
      const performance = dashboard.performance || {{}};
      const capital = dashboard.capital || {{}};
      const opsFlags = dashboard.ops_flags || {{ severity: 'stable', items: [] }};
      const runtimeProfile = dashboard.runtime_profile || {{}};
      const marketCharts = dashboard.market_charts || {{}};
      const healthRes = await fetch('/health', {{ cache: 'no-store' }});
      const health = await healthRes.json();

      document.getElementById('focus-metric').textContent = state.strategy_book.company_focus || 'n/a';
      document.getElementById('risk-metric').textContent = state.risk_budget ?? 'n/a';
      document.getElementById('cycles-metric').textContent = state.daily_summary.cycles_run ?? 0;
      document.getElementById('pnl-metric').textContent = `${{state.daily_summary.estimated_pnl_pct ?? 0}}%`;
      document.getElementById('regime-metric').textContent = `${{state.stance}} / ${{state.regime}}`;
      document.getElementById('winrate-metric').textContent = `${{state.daily_summary.win_rate ?? 0}}%`;
      document.getElementById('open-positions-metric').textContent = state.daily_summary.open_positions ?? 0;
      document.getElementById('closed-positions-metric').textContent = state.daily_summary.closed_positions ?? 0;
      document.getElementById('capital-base-metric').textContent = `KRW ${{Number(capital.base_krw || 0).toLocaleString()}}`;
      document.getElementById('capital-total-metric').textContent = `KRW ${{Number(capital.total_krw || 0).toLocaleString()}}`;
      document.getElementById('capital-realized-metric').textContent = `KRW ${{Number(capital.realized_krw || 0).toLocaleString()}}`;
      document.getElementById('capital-unrealized-metric').textContent = `KRW ${{Number(capital.unrealized_krw || 0).toLocaleString()}}`;
      document.getElementById('insight-score').textContent = dashboard.insight_score ?? '--';
      document.getElementById('equity-summary').textContent = dashboard.equity_summary
        ? `Current ${{dashboard.equity_summary.current}} / Net ${{dashboard.equity_summary.change_pct}}%`
        : 'No equity data yet';
      document.getElementById('state-line').textContent =
        `${{state.stance}} stance / ${{state.regime}} regime / risk budget ${{state.risk_budget}} / new entries ${{state.allow_new_entries ? 'ON' : 'BLOCKED'}} / ops ${{opsFlags.severity || 'stable'}} / runtime ${{runtimeProfile.mode || 'n/a'}}`;
      document.getElementById('updated-line').textContent = `Updated ${{state.updated_at}}`;
      document.getElementById('runtime-live').textContent = `${{liveDecision.runtime_profile?.mode || runtimeProfile.mode || 'n/a'}} / ${{liveDecision.runtime_profile?.reason || runtimeProfile.reason || 'n/a'}}`;
      document.getElementById('decision-live').textContent = `crypto ${{(liveDecision.strategy_book?.crypto_plan || {{}}).action || 'n/a'}} / korea ${{(liveDecision.strategy_book?.korea_plan || {{}}).action || 'n/a'}} / us ${{(liveDecision.strategy_book?.us_plan || {{}}).action || 'n/a'}}`;
      document.getElementById('next-live').textContent = `${{liveDecision.runtime_profile?.interval_seconds || runtimeProfile.interval_seconds || '-'}}초 후 재평가`;
      document.getElementById('ops-banner').innerHTML = (opsFlags.items || []).length
        ? (opsFlags.items || []).slice(0, 5).map(item =>
            `<div class="ops-flag ${{item.level || 'info'}}"><strong>${{item.code || 'flag'}}</strong><span>${{item.message || ''}}</span></div>`
          ).join('')
        : '<div class="ops-flag info"><strong>stable</strong><span>현재 핵심 경고 없음</span></div>';

      renderCurve('equity-path', 'equity-area', dashboard.equity_curve || [], 'equity-labels', 'equity-dots');
      renderCurve('trade-curve-path', 'trade-curve-area', performance.trade_curve || [], 'trade-curve-labels', 'trade-curve-dots');
      renderCandles('crypto-chart-candles', marketCharts.crypto?.candles || []);
      renderCandles('stock-chart-candles', marketCharts.korea?.candles || []);
      renderCandles('us-chart-candles', marketCharts.us?.candles || []);
      renderTradeBars(performance.recent_closed || []);

      document.getElementById('crypto-chart-symbol').textContent = marketCharts.crypto?.symbol || 'KRW-BTC';
      document.getElementById('stock-chart-symbol').textContent = marketCharts.korea?.symbol || '-';
      document.getElementById('crypto-chart-meta').textContent = (marketCharts.crypto?.candles || []).length
        ? `${{marketCharts.crypto.candles.length}} candles / last KRW ${{Number((marketCharts.crypto.candles || []).slice(-1)[0]?.close || 0).toLocaleString()}}`
        : 'No chart data';
      document.getElementById('stock-chart-meta').textContent = (marketCharts.korea?.candles || []).length
        ? `${{marketCharts.korea.candles.length}} candles / last KRW ${{Number((marketCharts.korea.candles || []).slice(-1)[0]?.close || 0).toLocaleString()}}`
        : 'No chart data';
      document.getElementById('us-chart-symbol').textContent = marketCharts.us?.symbol || '-';
      document.getElementById('us-chart-meta').textContent = (marketCharts.us?.candles || []).length
        ? `${{marketCharts.us.candles.length}} candles / last $${{Number((marketCharts.us.candles || []).slice(-1)[0]?.close || 0).toLocaleString()}}`
        : 'No chart data';
      setChartSummary('crypto', marketCharts.crypto || {{}});
      setChartSummary('stock', marketCharts.korea || {{}});
      setChartSummary('us', marketCharts.us || {{}});
      document.getElementById('realized-metric').textContent = `${{performance.realized_pnl_pct ?? 0}}%`;
      document.getElementById('unrealized-metric').textContent = `${{performance.unrealized_pnl_pct ?? 0}}%`;
      document.getElementById('wins-metric').textContent = performance.wins ?? 0;
      document.getElementById('losses-metric').textContent = performance.losses ?? 0;

      document.getElementById('trade-pulse').innerHTML = [
        `<li><strong>Ops severity</strong>: ${{opsFlags.severity || 'stable'}}</li>`,
        `<li><strong>Runtime</strong>: ${{runtimeProfile.mode || 'n/a'}} / ${{runtimeProfile.interval_seconds || '-'}}s</li>`,
        `<li><strong>Portfolio</strong>: KRW ${{Number(capital.total_krw || 0).toLocaleString()}}</li>`,
        `<li><strong>Expectancy</strong>: ${{performance.expectancy_pct ?? 0}}% / KRW ${{Number(performance.expectancy_krw || 0).toLocaleString()}}</li>`,
        `<li><strong>Win rate</strong>: ${{state.daily_summary.win_rate ?? 0}}% / wins ${{state.daily_summary.wins ?? 0}} / losses ${{state.daily_summary.losses ?? 0}}</li>`,
        `<li><strong>Open positions</strong>: ${{state.daily_summary.open_positions ?? 0}} / gross ${{state.daily_summary.gross_open_notional_pct ?? 0}}x</li>`,
        `<li><strong>Crypto desk</strong>: ${{dashboard.desk_status?.crypto?.action || 'n/a'}} / ${{dashboard.desk_status?.crypto?.size || 'n/a'}}</li>`,
        `<li><strong>Korea desk</strong>: ${{dashboard.desk_status?.korea?.action || 'n/a'}} / ${{dashboard.desk_status?.korea?.size || 'n/a'}}</li>`,
        `<li><strong>U.S. desk</strong>: ${{dashboard.desk_status?.us?.action || 'n/a'}} / ${{dashboard.desk_status?.us?.size || 'n/a'}}</li>`
      ].join('');

      renderDeskStatus('crypto', dashboard.desk_status?.crypto || {{}});
      renderDeskStatus('korea', dashboard.desk_status?.korea || {{}});
      renderDeskStatus('us', dashboard.desk_status?.us || {{}});

      document.getElementById('signals').innerHTML = (state.latest_signals || []).map(item => `<li>${{item}}</li>`).join('') || '<li>No signals yet</li>';
      document.getElementById('principles').innerHTML = (state.trader_principles || []).map(item => `<li>${{item}}</li>`).join('');
      document.getElementById('desks').innerHTML = Object.entries(state.desk_views || {{}}).map(([name, payload]) => `<li><strong>${{name}}</strong>: ${{JSON.stringify(payload)}}</li>`).join('') || '<li>No desk output yet</li>';
      document.getElementById('session-state').innerHTML = Object.entries(state.session_state || {{}}).map(([name, value]) => `<li><strong>${{name}}</strong>: ${{Array.isArray(value) ? value.join(', ') : value}}</li>`).join('') || '<li>No session state yet</li>';
      document.getElementById('strategy-book').innerHTML = [
        `<li><strong>company_focus</strong>: ${{state.strategy_book.company_focus || 'n/a'}}</li>`,
        `<li><strong>desk_priorities</strong>: ${{(state.strategy_book.desk_priorities || []).join(' | ') || 'n/a'}}</li>`
      ].join('');
      document.getElementById('desk-plans').innerHTML = [
        `<li><strong>crypto</strong>: ${{state.strategy_book.crypto_plan ? state.strategy_book.crypto_plan.action + ' / ' + state.strategy_book.crypto_plan.size + ' / ' + state.strategy_book.crypto_plan.focus : 'n/a'}}</li>`,
        `<li><strong>korea</strong>: ${{state.strategy_book.korea_plan ? state.strategy_book.korea_plan.action + ' / ' + state.strategy_book.korea_plan.size + ' / ' + state.strategy_book.korea_plan.focus : 'n/a'}}</li>`,
        `<li><strong>us</strong>: ${{state.strategy_book.us_plan ? state.strategy_book.us_plan.action + ' / ' + state.strategy_book.us_plan.size + ' / ' + state.strategy_book.us_plan.focus : 'n/a'}}</li>`
      ].join('');
      document.getElementById('daily-summary').innerHTML = [
        `<li><strong>date</strong>: ${{state.daily_summary.date || 'n/a'}}</li>`,
        `<li><strong>cycles</strong>: ${{state.daily_summary.cycles_run || 0}}</li>`,
        `<li><strong>orders</strong>: ${{state.daily_summary.orders_logged || 0}}</li>`,
        `<li><strong>planned_orders</strong>: ${{state.daily_summary.planned_orders || 0}}</li>`,
        `<li><strong>expectancy</strong>: ${{state.daily_summary.expectancy_pct || 0}}% / KRW ${{Number(state.daily_summary.expectancy_krw || 0).toLocaleString()}}</li>`,
        `<li><strong>realized_pnl_pct</strong>: ${{state.daily_summary.realized_pnl_pct || 0}}%</li>`,
        `<li><strong>unrealized_pnl_pct</strong>: ${{state.daily_summary.unrealized_pnl_pct || 0}}%</li>`,
        `<li><strong>gross_open_notional</strong>: ${{state.daily_summary.gross_open_notional_pct || 0}}x</li>`,
        `<li><strong>active_desks</strong>: ${{(state.daily_summary.active_desks || []).join(', ') || 'n/a'}}</li>`
      ].join('');
      document.getElementById('problem-symbols').innerHTML = (performance.symbol_performance_stats || []).slice(0, 4).map(item =>
        `<li><strong>${{item.desk}} / ${{item.symbol}}</strong>: pnl ${{item.pnl_pct}}% / stop-like ${{item.stop_like_count}} / wins ${{item.wins}} / losses ${{item.losses}}</li>`
      ).join('') || '<li>문제 심볼 없음</li>';

      document.getElementById('crypto-leaders').innerHTML = (state.market_snapshot.crypto_leaders || []).slice(0, 6).map(item =>
        `<li class="list-row"><div class="row-top"><strong class="row-title">${{item.market}}</strong><span class="row-meta">${{item.change_rate}}%</span></div><div class="row-foot">KRW ${{Number(item.trade_price).toLocaleString()}} / 24h volume ${{Number(item.volume_24h_krw || 0).toLocaleString()}}</div></li>`
      ).join('') || '<li class="list-row">No crypto snapshot yet</li>';
      document.getElementById('stock-leaders').innerHTML = (state.market_snapshot.gap_candidates || state.market_snapshot.stock_leaders || []).slice(0, 6).map(item =>
        `<li class="list-row"><div class="row-top"><strong class="row-title">${{item.name || item.ticker}}</strong><span class="row-meta">gap ${{item.gap_pct}}%</span></div><div class="row-foot">ticker ${{item.ticker || 'n/a'}} / volume ${{Number(item.volume || 0).toLocaleString()}} / price ${{Number(item.current_price || 0).toLocaleString()}}</div></li>`
      ).join('') || '<li class="list-row">No KOSDAQ snapshot yet</li>';
      document.getElementById('us-leaders').innerHTML = (state.market_snapshot.us_leaders || []).slice(0, 6).map(item =>
        `<li class="list-row"><div class="row-top"><strong class="row-title">${{item.ticker}}</strong><span class="row-meta">${{item.change_pct}}%</span></div><div class="row-foot">$${{Number(item.current_price || 0).toLocaleString()}} / 20d momentum ${{item.momentum_20d_pct}}% / volume ${{Number(item.volume || 0).toLocaleString()}}</div></li>`
      ).join('') || '<li class="list-row">No U.S. snapshot yet</li>';
      document.getElementById('market-data-status').innerHTML = [
        `<li><strong>U.S. data</strong>: ${{health.us_data_status?.provider || 'n/a'}} / ${{health.us_data_status?.message || 'n/a'}}</li>`,
        `<li><strong>Telegram</strong>: ${{health.telegram_enabled ? 'enabled' : 'disabled'}}</li>`
      ].join('');
      document.getElementById('paper-blotter').innerHTML = (state.execution_log || []).slice(0, 8).map(item =>
        `<li class="list-row"><div class="row-top"><strong class="row-title">${{item.desk}} / ${{item.action}}</strong><span class="row-meta">${{item.status}}</span></div><div class="row-foot">${{item.size}} / pnl est ${{item.pnl_estimate_pct}}% / ${{item.created_at}}</div></li>`
      ).join('') || '<li class="list-row">No paper orders yet</li>';
      document.getElementById('open-positions').innerHTML = (dashboard.open_positions || []).map(item =>
        `<li class="list-row"><div class="row-top"><strong class="row-title">${{item.symbol}}</strong><span class="row-meta">${{item.pnl_pct}}%</span></div><div class="row-foot">${{item.desk}} / entry KRW ${{Number(item.entry_price || 0).toLocaleString()}} / current KRW ${{Number(item.current_price || 0).toLocaleString()}} / cycles ${{item.cycles_open}}</div></li>`
      ).join('') || '<li class="list-row">No open positions</li>';
      document.getElementById('closed-positions').innerHTML = (dashboard.closed_positions || []).map(item =>
        `<li class="list-row"><div class="row-top"><strong class="row-title">${{item.symbol}}</strong><span class="row-meta">${{item.pnl_pct}}%</span></div><div class="row-foot">${{item.desk}} / ${{item.closed_reason}} / ${{item.closed_at || 'n/a'}}</div></li>`
      ).join('') || '<li class="list-row">No closed trades yet</li>';
      document.getElementById('cycle-journal').innerHTML = (state.recent_journal || []).slice(0, 6).map(item =>
        `<li class="list-row"><div class="row-top"><strong class="row-title">${{item.company_focus}}</strong><span class="row-meta">${{item.stance}} / ${{item.regime}}</span></div><div class="row-foot">${{item.run_at}}</div></li>`
      ).join('') || '<li class="list-row">No journal yet</li>';
      document.getElementById('agents').innerHTML = (state.agent_runs || []).map(item => `<li><strong>${{item.name}}</strong> (${{item.score}}): ${{item.reason}}</li>`).join('') || '<li>No agent records yet</li>';
    }}

    async function runCycle() {{
      await fetch('/cycle', {{ method: 'POST' }});
      await loadData();
    }}

    setInterval(() => loadData().catch(() => null), 20000);
    loadData().catch(err => {{
      document.getElementById('state-line').textContent = `Dashboard unavailable: ${{err.message}}`;
      document.getElementById('state-line').className = 'danger';
    }});
  </script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)

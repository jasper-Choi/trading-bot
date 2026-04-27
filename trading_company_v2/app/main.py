from __future__ import annotations

import hashlib
import secrets
from base64 import b64decode
from datetime import datetime, timedelta, timezone

_SESSION_COOKIE = "tcsession"
_SESSION_DURATION = 86400  # 24h
_SESSION_SECRET = secrets.token_hex(16)  # rotates on restart — acceptable for a dashboard


def _session_token(username: str) -> str:
    return hashlib.sha256(f"{username}:{_SESSION_SECRET}".encode()).hexdigest()

_KST = timezone(timedelta(hours=9))


def _to_kst_hhmm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_KST).strftime("%H:%M")
    except Exception:
        return iso[11:16] if len(iso) >= 16 else iso

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.responses import PlainTextResponse

from app.config import settings
from app.agents.execution_agent import ExecutionAgent
from app.agents.us_stock_desk_agent import USStockDeskAgent
from app.core.models import CompanyState
from app.core.state_store import (
    init_db,
    load_closed_positions,
    load_company_state,
    load_open_positions,
    load_performance_quick_stats,
    load_recent_journal,
)
from app.notifier import notifier
from app.orchestrator import CompanyOrchestrator
from app.services.kis_broker import get_account_positions as get_kis_account_positions
from app.services.kis_broker import get_order as get_kis_order
from app.services.kis_broker import normalize_order_state as normalize_kis_order_state
from app.services.broker_router import normalize_execution_mode
from app.services.market_gateway import get_naver_daily_prices, get_upbit_15m_candles, get_us_daily_prices, get_us_data_status
from app.services.recommendation_engine import build_crypto_plan, build_korea_plan, build_us_plan
from app.services.upbit_broker import get_account_positions as get_upbit_account_positions
from app.services.upbit_broker import get_order as get_upbit_order
from app.services.upbit_broker import normalize_order_state as normalize_upbit_order_state
from app.service_manager import (
    LOOP_LOG_PATH,
    SERVER_LOG_PATH,
    local_access_urls,
    start_services,
    status as service_status,
    stop_services,
)


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

    # 1. Session cookie — set after first Basic Auth success, used by all subsequent
    #    JavaScript fetch() calls (mobile Safari / Chrome don't auto-forward Basic Auth to JS)
    cookie = request.cookies.get(_SESSION_COOKIE, "")
    if cookie and secrets.compare_digest(cookie, _session_token(settings.app_username)):
        return await call_next(request)

    # 2. Basic Auth header — browser sends this on first page load / explicit prompt
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

    response = await call_next(request)
    response.set_cookie(
        _SESSION_COOKIE,
        _session_token(username),
        max_age=_SESSION_DURATION,
        httponly=True,
        samesite="lax",
    )
    return response


def _compute_insight_score(state: CompanyState) -> int:
    if not state.agent_runs:
        return 50
    score = sum(item.score for item in state.agent_runs) / len(state.agent_runs)
    return round(score * 100)


def _build_equity_curve(state: CompanyState) -> list[dict]:
    realized = float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0)
    unrealized = float(state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0)
    current_total = round(100.0 + realized + unrealized, 2)
    updated_at = _to_kst_hhmm(state.updated_at) if state.updated_at else "Now"
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
            "title": "크립토 데스크",
            "bias": crypto_view.get("desk_bias", "n/a"),
            "action": crypto_plan.get("action", "n/a"),
            "focus": crypto_plan.get("focus", "크립토 플랜 없음"),
            "size": crypto_plan.get("size", "0.00x"),
            "leaders": (state.market_snapshot.get("crypto_leaders", []) if state.market_snapshot else [])[:3],
            "latest_order": latest_crypto_order,
        },
        "korea": {
            "title": "한국주식 데스크",
            "bias": "active" if korea_view.get("active_gap_count", 0) else "watch",
            "action": korea_plan.get("action", "n/a"),
            "focus": korea_plan.get("focus", "주식 플랜 없음"),
            "size": korea_plan.get("size", "0.00x"),
            "quality_score": float(korea_plan.get("quality_score", 0.0) or 0.0),
            "avg_signal": float(korea_plan.get("avg_signal", 0.0) or 0.0),
            "quality_threshold": float(korea_plan.get("quality_threshold", 0.58) or 0.58),
            "breakout_confirmed_count": int(korea_view.get("breakout_confirmed_count", 0) or 0),
            "breakout_partial_count": int(korea_view.get("breakout_partial_count", 0) or 0),
            "leaders": ((state.market_snapshot.get("gap_candidates") or state.market_snapshot.get("stock_leaders") or []) if state.market_snapshot else [])[:3],
            "latest_order": latest_korea_order,
        },
        "us": {
            "title": "미국주식 데스크",
            "bias": us_view.get("desk_bias", "n/a"),
            "action": us_plan.get("action", "n/a"),
            "focus": us_plan.get("focus", "미국 플랜 없음"),
            "size": us_plan.get("size", "0.00x"),
            "quality_score": float(us_plan.get("quality_score", 0.0) or 0.0),
            "avg_signal": float(us_plan.get("avg_signal", 0.0) or 0.0),
            "quality_threshold": float(us_plan.get("quality_threshold", 0.72) or 0.72),
            "leaders": (state.market_snapshot.get("us_leaders", []) if state.market_snapshot else [])[:3],
            "latest_order": latest_us_order,
        },
    }


def _leaders_to_symbols(leaders: list) -> list[str]:
    symbols: list[str] = []
    for item in leaders or []:
        symbol = ""
        if isinstance(item, str):
            symbol = item.strip()
        elif isinstance(item, dict):
            for key in ("symbol", "ticker", "market", "code"):
                value = str(item.get(key) or "").strip()
                if value:
                    symbol = value
                    break
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols[:5]


def _build_desk_drilldown_payload(state: CompanyState, closed_positions: list[dict]) -> dict:
    desk_status = _build_desk_status(state)
    execution_log = list(state.execution_log or [])
    open_positions = list(state.open_positions or [])
    desk_stats = state.daily_summary.get("desk_stats", {}) or {}
    desk_views = state.desk_views or {}
    strategy_book = state.strategy_book or {}
    config_map = {
        "crypto": ("crypto_plan", "crypto"),
        "korea": ("korea_plan", "korea"),
        "us": ("us_plan", "us"),
    }
    payload: dict[str, dict] = {}

    for desk, (plan_key, desk_stat_key) in config_map.items():
        status = desk_status.get(desk, {}) or {}
        plan = strategy_book.get(plan_key, {}) or {}
        stats = desk_stats.get(desk_stat_key, {}) or {}
        desk_view_key = {
            "crypto": "crypto_desk",
            "korea": "korea_stock_desk",
            "us": "us_stock_desk",
        }.get(desk, "")
        view = desk_views.get(desk_view_key, {}) or {}
        target_symbol = str(plan.get("symbol") or "") or str(status.get("focus") or "")
        watch_symbols = _leaders_to_symbols(status.get("leaders") or [])
        candidate_symbols = [str(item).strip() for item in (plan.get("candidate_symbols") or view.get("candidate_symbols") or []) if str(item).strip()]
        candidate_details: list[dict] = []

        if desk == "crypto":
            ranked_candidates = []
            backtest_weights = view.get("backtest_weights", {}) or {}
            lead_market = str(view.get("lead_market", "") or target_symbol).strip()
            # candidate_markets has per-symbol breakout data from CryptoDeskAgent
            cand_markets_map = {
                str(m.get("market", "")): m
                for m in (view.get("candidate_markets") or [])
            }
            for symbol in candidate_symbols[:5]:
                weight = float(backtest_weights.get(symbol, 0.0) or 0.0)
                leader_bonus = 0.03 if symbol == lead_market else 0.0
                mdata = cand_markets_map.get(symbol, {}) or {}
                ranked_candidates.append(
                    {
                        "symbol": symbol,
                        "label": symbol.replace("KRW-", ""),
                        "score": round(float(mdata.get("combined_score", view.get("signal_score", 0.0)) or 0.0) + (weight * 0.1) + leader_bonus, 2),
                        "bias": str(mdata.get("bias", view.get("desk_bias", "balanced")) or "balanced"),
                        "weight": round(weight, 3),
                        "recent_change_pct": float(mdata.get("recent_change_pct", view.get("recent_change_pct", 0.0)) or 0.0),
                        "pullback_gap_pct": float(mdata.get("pullback_gap_pct", view.get("pullback_gap_pct", 0.0)) or 0.0),
                        "range_4_pct": float(mdata.get("range_4_pct", view.get("range_4_pct", 0.0)) or 0.0),
                        "rsi": mdata.get("rsi") or view.get("rsi"),
                        "micro_score": float(mdata.get("micro_score", 0.0) or 0.0),
                        "micro_ready": bool(mdata.get("micro_ready", False)),
                        "micro_vol_ratio": float(mdata.get("micro_vol_ratio", 0.0) or 0.0),
                        "micro_move_3_pct": float(mdata.get("micro_move_3_pct", 0.0) or 0.0),
                        "micro_vwap_gap_pct": float(mdata.get("micro_vwap_gap_pct", 0.0) or 0.0),
                        "orderbook_score": float(mdata.get("orderbook_score", 0.0) or 0.0),
                        "orderbook_ready": bool(mdata.get("orderbook_ready", False)),
                        "orderbook_bid_ask_ratio": float(mdata.get("orderbook_bid_ask_ratio", 0.0) or 0.0),
                        "orderbook_spread_pct": float(mdata.get("orderbook_spread_pct", 0.0) or 0.0),
                        "breakout_count": int(mdata.get("breakout_count", 0) or 0),
                        "vol_ratio": float(mdata.get("vol_ratio", 0.0) or 0.0),
                        "breakout_confirmed": bool(mdata.get("breakout_confirmed", False)),
                        "rsi_quality_ok": bool(mdata.get("rsi_quality_ok", True)),
                        "rsi_reset_confirmed": bool(mdata.get("rsi_reset_confirmed", False)),
                        "rsi_bearish_divergence": bool(mdata.get("rsi_bearish_divergence", False)),
                        "rsi_extreme": bool(mdata.get("rsi_extreme", False)),
                        "is_primary": symbol == lead_market,
                    }
                )
            candidate_details = ranked_candidates
        elif desk == "korea":
            for item in (view.get("gap_candidates") or [])[:5]:
                symbol = str(item.get("ticker", "")).strip()
                candidate_details.append(
                    {
                        "symbol": symbol,
                        "label": str(item.get("name", "") or symbol),
                        "score": round(float(item.get("candidate_score", 0.0) or 0.0), 2),
                        "bias": str(item.get("signal_bias", "neutral") or "neutral"),
                        "gap_pct": float(item.get("gap_pct", 0.0) or 0.0),
                        "signal_score": float(item.get("signal_score", 0.0) or 0.0),
                        "burst_change_pct": float(item.get("burst_change_pct", 0.0) or 0.0),
                        "rsi": item.get("rsi"),
                        "is_breakout": bool(item.get("is_breakout", False)),
                        "breakout_count": int(item.get("breakout_count", 0) or 0),
                        "vol_ratio": float(item.get("vol_ratio", 0.0) or 0.0),
                        "is_primary": symbol == target_symbol,
                    }
                )
        elif desk == "us":
            for item in (view.get("leaders") or [])[:5]:
                symbol = str(item.get("ticker", "")).strip()
                candidate_details.append(
                    {
                        "symbol": symbol,
                        "label": symbol,
                        "score": round(float(item.get("candidate_score", 0.0) or 0.0), 2),
                        "bias": str(item.get("signal_bias", "neutral") or "neutral"),
                        "change_pct": float(item.get("change_pct", 0.0) or 0.0),
                        "signal_score": float(item.get("signal_score", 0.0) or 0.0),
                        "volume": float(item.get("volume", 0.0) or 0.0),
                        "is_primary": symbol == target_symbol,
                    }
                )

        desk_open_positions = [
            {
                "symbol": item.get("symbol"),
                "action": item.get("action"),
                "opened_at": item.get("opened_at"),
                "entry_price": item.get("entry_price"),
                "current_price": item.get("current_price"),
                "unrealized_pnl_pct": float(item.get("unrealized_pnl_pct", 0.0) or 0.0),
                "notional_pct": float(item.get("notional_pct", 0.0) or 0.0),
            }
            for item in open_positions
            if str(item.get("desk") or "") == desk
        ][:5]

        desk_recent_orders = [
            {
                "symbol": item.get("symbol") or item.get("focus"),
                "action": item.get("action"),
                "status": item.get("status"),
                "effect_status": item.get("effect_status"),
                "created_at": item.get("created_at"),
                "source": item.get("source"),
            }
            for item in execution_log
            if str(item.get("desk") or "") == desk
        ][:5]

        desk_closed = [
            {
                "symbol": item.get("symbol"),
                "closed_reason": item.get("closed_reason"),
                "closed_at": item.get("closed_at"),
                "pnl_pct": float(item.get("pnl_pct", item.get("realized_pnl_pct", 0.0)) or 0.0),
                "notional_pct": float(item.get("notional_pct", 0.0) or 0.0),
            }
            for item in closed_positions
            if str(item.get("desk") or "") == desk
        ][:5]

        payload[desk] = {
            "title": status.get("title") or desk,
            "action": status.get("action") or "n/a",
            "focus": status.get("focus") or "",
            "size": status.get("size") or "0.00x",
            "target_symbol": target_symbol,
            "watch_symbols": watch_symbols,
            "candidate_symbols": candidate_symbols or watch_symbols,
            "candidate_details": candidate_details,
            "latest_order": status.get("latest_order"),
            "open_positions": desk_open_positions,
            "recent_orders": desk_recent_orders,
            "recent_closed": desk_closed,
            "realized_pnl_pct": float(stats.get("realized_pnl_pct", 0.0) or 0.0),
            "win_rate": float(stats.get("win_rate", 0.0) or 0.0),
            "wins": int(stats.get("wins", 0) or 0),
            "losses": int(stats.get("losses", 0) or 0),
            "quality_score": float(plan.get("quality_score", view.get("quality_score", 0.0)) or 0.0),
            "avg_signal": float(plan.get("avg_signal", view.get("avg_signal_score_top3", view.get("signal_score", 0.0))) or 0.0),
            "quality_threshold": float(plan.get("quality_threshold", 0.0) or 0.0),
            "desk_bias": str(view.get("desk_bias", status.get("bias", "neutral")) or "neutral"),
            "active_count": int(
                view.get("active_gap_count", view.get("active_us_count", len(candidate_details))) or 0
            ),
            "breakout_confirmed_count": int(view.get("breakout_confirmed_count", 0) or 0),
            "breakout_partial_count": int(view.get("breakout_partial_count", 0) or 0),
        }

    return payload


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
        "capital_profile": state.strategy_book.get("capital_profile", {}) or {},
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

    from concurrent.futures import ThreadPoolExecutor

    crypto_symbol = str(state.strategy_book.get("crypto_plan", {}).get("symbol") or "KRW-BTC")
    korea_symbol = str(state.strategy_book.get("korea_plan", {}).get("symbol") or "")
    us_symbol = str(state.strategy_book.get("us_plan", {}).get("symbol") or "")

    with ThreadPoolExecutor(max_workers=3) as _ex:
        _fc = _ex.submit(get_upbit_15m_candles, crypto_symbol, 24)
        _fk = _ex.submit(get_naver_daily_prices, korea_symbol, 20) if korea_symbol else None
        _fu = _ex.submit(get_us_daily_prices, us_symbol, 30) if us_symbol else None

    try:
        crypto_candles = _fc.result(timeout=10)
    except Exception:
        crypto_candles = []
    try:
        stock_candles = _fk.result(timeout=10) if _fk else []
    except Exception:
        stock_candles = []
    try:
        us_candles = _fu.result(timeout=10) if _fu else []
    except Exception:
        us_candles = []

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


def _deployment_profile(state: CompanyState) -> dict:
    access = local_access_urls()
    execution_mode = str(state.execution_mode or settings.execution_mode or "paper")
    app_env = str(settings.app_env or "local")
    role = "local_dev"
    if app_env in {"prod", "production"} or execution_mode != "paper":
        role = "live_target"
    label_map = {
        "local_dev": "Local Development",
        "live_target": "Live Target",
    }
    return {
        "app_env": app_env,
        "role": role,
        "label": label_map.get(role, role),
        "execution_mode": execution_mode,
        "public_url": access.get("public_url", ""),
        "local_url": access.get("local_url", ""),
        "lan_url": access.get("lan_url", ""),
        "summary": (
            "paper-first local environment"
            if role == "local_dev"
            else "live-target deployment profile"
        ),
    }


def _symbol_edge_summary(state: CompanyState) -> list[dict]:
    closed_positions = load_closed_positions(limit=20)
    plan_symbols: list[tuple[str, str]] = []
    for desk, plan_key in (("crypto", "crypto_plan"), ("korea", "korea_plan"), ("us", "us_plan")):
        plan = state.strategy_book.get(plan_key, {}) or {}
        primary = str(plan.get("symbol", "") or "").strip()
        if primary:
            plan_symbols.append((desk, primary))
        for candidate in plan.get("candidate_symbols", []) or []:
            symbol = str(candidate or "").strip()
            if symbol and (desk, symbol) not in plan_symbols:
                plan_symbols.append((desk, symbol))

    rows: list[dict] = []
    for desk, symbol in plan_symbols[:8]:
        history = [item for item in closed_positions if item.get("desk") == desk and item.get("symbol") == symbol][:5]
        if not history:
            rows.append({"desk": desk, "symbol": symbol, "tone": "neutral", "score": 0.0, "detail": "fresh symbol"})
            continue
        weighted_pnl = 0.0
        wins = 0
        losses = 0
        stop_like = 0
        for idx, item in enumerate(history):
            weight = max(1.0 - (idx * 0.16), 0.4)
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            weighted_pnl += pnl * weight
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            if str(item.get("closed_reason", "") or "") in {"stop_hit", "early_failure"}:
                stop_like += 1
        score = round((wins * 0.55) - (losses * 0.7) + (weighted_pnl * 0.18) - (stop_like * 0.35), 2)
        tone = "hot" if score >= 0.7 else "cold" if score <= -0.9 or stop_like >= 2 else "cool" if score <= -0.35 else "neutral"
        rows.append(
            {
                "desk": desk,
                "symbol": symbol,
                "tone": tone,
                "score": score,
                "detail": f"wins {wins} / losses {losses} / weighted pnl {round(weighted_pnl, 2)}%",
            }
        )
    return sorted(rows, key=lambda item: (0 if item["tone"] == "hot" else 1 if item["tone"] == "neutral" else 2, -item["score"]), reverse=False)


def _build_desk_offense_payload(state: CompanyState) -> list[dict]:
    desk_stats = state.daily_summary.get("desk_stats", {}) or {}
    capital_profile = state.strategy_book.get("capital_profile", {}) or {}
    desk_multipliers = capital_profile.get("desk_multipliers", {}) or {}
    plan_map = {
        "crypto": state.strategy_book.get("crypto_plan", {}) or {},
        "korea": state.strategy_book.get("korea_plan", {}) or {},
        "us": state.strategy_book.get("us_plan", {}) or {},
    }
    action_bonus = {
        "attack_opening_drive": 12,
        "probe_longs": 9,
        "selective_probe": 5,
        "watchlist_only": -4,
        "stand_by": -7,
        "capital_preservation": -14,
        "pre_market_watch": -9,
    }
    offense_rows: list[dict] = []
    for desk in ("crypto", "korea", "us"):
        stats = desk_stats.get(desk, {}) or {}
        plan = plan_map.get(desk, {}) or {}
        realized = float(stats.get("realized_pnl_pct", 0.0) or 0.0)
        win_rate = float(stats.get("win_rate", 0.0) or 0.0)
        closed_positions = int(stats.get("closed_positions", 0) or 0)
        open_notional = float(stats.get("open_notional_pct", 0.0) or 0.0)
        desk_multiplier = float(desk_multipliers.get(desk, 1.0) or 1.0)
        action = str(plan.get("action", "stand_by") or "stand_by")

        score = 50.0
        score += max(min(realized * 7.5, 18.0), -22.0)
        score += max(min((win_rate - 50.0) * 0.35, 12.0), -14.0)
        score += min(closed_positions * 1.8, 8.0)
        score += action_bonus.get(action, 0)
        score += (desk_multiplier - 1.0) * 50.0
        score -= max(open_notional - 0.55, 0.0) * 18.0
        score = round(max(min(score, 100.0), 0.0), 1)

        if score >= 67:
            tone = "press"
        elif score >= 52:
            tone = "balanced"
        else:
            tone = "cooldown"

        offense_rows.append(
            {
                "desk": desk,
                "title": {"crypto": "크립토 데스크", "korea": "한국주식 데스크", "us": "미국주식 데스크"}.get(desk, desk),
                "score": score,
                "tone": tone,
                "action": action,
                "size": str(plan.get("size", "0.00x") or "0.00x"),
                "focus": str(plan.get("focus", "") or ""),
                "multiplier": round(desk_multiplier, 2),
                "realized_pnl_pct": realized,
                "win_rate": win_rate,
                "closed_positions": closed_positions,
            }
        )

    return sorted(offense_rows, key=lambda item: item["score"], reverse=True)


def _crypto_live_lane_snapshot(state: CompanyState) -> dict:
    crypto_plan = state.strategy_book.get("crypto_plan", {}) or {}
    crypto_view = state.desk_views.get("crypto_desk", {}) or {}
    signal_score = float(crypto_view.get("signal_score", 0.0) or 0.0)
    lead_market = str(crypto_plan.get("symbol") or crypto_view.get("lead_market") or "KRW-BTC")
    lead_weight = float((crypto_view.get("backtest_weights", {}) or {}).get(lead_market, 0.0) or 0.0)
    trigger_threshold = 0.56 if lead_weight >= 0.30 and float(crypto_view.get("recent_change_pct", 0.0) or 0.0) >= -0.4 else 0.58
    distance_to_trigger = round(max(trigger_threshold - signal_score, 0.0), 2)
    return {
        "action": str(crypto_plan.get("action", "watchlist_only") or "watchlist_only"),
        "size": str(crypto_plan.get("size", "0.00x") or "0.00x"),
        "focus": str(crypto_plan.get("focus", "") or ""),
        "symbol": lead_market,
        "desk_bias": str(crypto_view.get("desk_bias", "balanced") or "balanced"),
        "signal_score": round(signal_score, 2),
        "recent_change_pct": round(float(crypto_view.get("recent_change_pct", 0.0) or 0.0), 2),
        "ema_gap_pct": round(float(crypto_view.get("ema_gap_pct", 0.0) or 0.0), 2),
        "lead_weight": round(lead_weight, 2),
        "trigger_threshold": round(trigger_threshold, 2),
        "distance_to_trigger": distance_to_trigger,
        "trigger_state": "ready" if signal_score >= trigger_threshold else "arming" if distance_to_trigger <= 0.08 else "waiting",
        "notes": [str(item) for item in (crypto_plan.get("notes", []) or [])][:3],
    }


def _crypto_live_lane_history(state: CompanyState) -> list[dict]:
    rows: list[dict] = []
    for item in list(state.recent_journal or [])[:6]:
        summary = [str(entry) for entry in (item.get("summary", []) or [])]
        signal = None
        trigger = None
        action = ""
        for entry in summary:
            if entry.startswith("crypto_signal="):
                try:
                    signal = round(float(entry.split("=", 1)[1]), 2)
                except ValueError:
                    signal = None
            elif entry.startswith("crypto_trigger="):
                try:
                    trigger = round(float(entry.split("=", 1)[1]), 2)
                except ValueError:
                    trigger = None
            elif entry.startswith("crypto_action="):
                action = str(entry.split("=", 1)[1]).strip()
        if signal is None and trigger is None and not action:
            continue
        run_at = str(item.get("run_at", "") or "")
        rows.append(
            {
                "run_at": run_at,
                "time": run_at[11:16] if len(run_at) >= 16 else run_at,
                "signal_score": signal,
                "trigger_threshold": trigger,
                "distance_to_trigger": round(max((trigger or 0.0) - (signal or 0.0), 0.0), 2) if signal is not None and trigger is not None else None,
                "action": action or "watchlist_only",
            }
        )
    ordered = list(reversed(rows))
    if ordered:
        return ordered
    snapshot = _crypto_live_lane_snapshot(state)
    return [
        {
            "run_at": state.updated_at,
            "time": _to_kst_hhmm(state.updated_at),
            "signal_score": snapshot.get("signal_score"),
            "trigger_threshold": snapshot.get("trigger_threshold"),
            "distance_to_trigger": snapshot.get("distance_to_trigger"),
            "action": snapshot.get("action", "watchlist_only"),
        }
    ]


def _build_agent_performance_payload(state: CompanyState) -> list[dict]:
    desk_stats = state.daily_summary.get("desk_stats", {}) or {}
    desk_agent_map = {
        "crypto_desk_agent": "crypto",
        "korea_stock_desk_agent": "korea",
        "us_stock_desk_agent": "us",
    }
    title_map = {
        "market_data_agent": "시장 데이터",
        "macro_sentiment_agent": "매크로 심리",
        "trend_structure_agent": "추세 구조",
        "strategy_allocator_agent": "전략 배분",
        "crypto_desk_agent": "크립토 데스크 에이전트",
        "korea_stock_desk_agent": "한국주식 데스크 에이전트",
        "us_stock_desk_agent": "미국주식 데스크 에이전트",
        "cio_agent": "최고투자책임자",
        "risk_committee_agent": "위험위원회",
        "execution_agent": "실행 에이전트",
        "ops_agent": "운영 에이전트",
    }
    infra_agents = {"risk_committee_agent", "execution_agent", "ops_agent", "cio_agent"}
    context_agents = {"market_data_agent", "macro_sentiment_agent", "trend_structure_agent", "strategy_allocator_agent"}
    rows: list[dict] = []
    for item in state.agent_runs or []:
        name = str(item.name or "")
        score = float(item.score or 0.0)
        linked_desk = desk_agent_map.get(name)
        desk_realized = float((desk_stats.get(linked_desk, {}) or {}).get("realized_pnl_pct", 0.0) or 0.0) if linked_desk else None
        desk_win_rate = float((desk_stats.get(linked_desk, {}) or {}).get("win_rate", 0.0) or 0.0) if linked_desk else None
        effectiveness = round(score * 100.0, 1)
        if desk_realized is not None:
            effectiveness = round(max(min(effectiveness + (desk_realized * 6.0), 100.0), 0.0), 1)
        if desk_win_rate is not None and (desk_stats.get(linked_desk, {}) or {}).get("closed_positions", 0):
            effectiveness = round(max(min(effectiveness + ((desk_win_rate - 50.0) * 0.18), 100.0), 0.0), 1)
        if name in infra_agents:
            effectiveness = round(effectiveness * 0.82, 1)
        elif name in context_agents:
            effectiveness = round(effectiveness * 0.9, 1)

        if effectiveness >= 68:
            tone = "strong"
        elif effectiveness >= 48:
            tone = "mixed"
        else:
            tone = "weak"

        rows.append(
            {
                "name": name,
                "title": title_map.get(name, name.replace("_", " ").title()),
                "score": round(score * 100.0, 1),
                "effectiveness": effectiveness,
                "tone": tone,
                "category": "desk" if linked_desk else "system",
                "linked_desk": linked_desk,
                "desk_realized_pnl_pct": desk_realized,
                "desk_win_rate": desk_win_rate,
                "reason": str(item.reason or ""),
            }
        )

    return sorted(
        rows,
        key=lambda item: (0 if item["linked_desk"] else 1, -item["effectiveness"]),
    )


def _entry_block_summary(state: CompanyState) -> dict:
    daily = state.daily_summary or {}
    realized = float(daily.get("realized_pnl_pct", 0.0) or 0.0)
    gross = float(daily.get("gross_open_notional_pct", 0.0) or 0.0)
    summary = {
        "blocked": not bool(state.allow_new_entries),
        "headline": "new entries allowed",
        "detail": "risk gate open",
        "reason_code": "allowed",
    }
    if state.allow_new_entries:
        return summary

    notes = [str(item) for item in (state.notes or [])]
    if any("live conservative mode" in item.lower() for item in notes):
        return {
            "blocked": True,
            "headline": "new entries blocked",
            "detail": "live execution unresolved, conservative mode active",
            "reason_code": "live_conservative_mode",
        }
    if realized <= -1.5:
        return {
            "blocked": True,
            "headline": "new entries blocked",
            "detail": f"daily drawdown guard active ({realized:.2f}%)",
            "reason_code": "daily_drawdown",
        }
    if gross >= 1.05:
        return {
            "blocked": True,
            "headline": "new entries blocked",
            "detail": f"gross exposure cap breached ({gross:.2f}x)",
            "reason_code": "gross_exposure",
        }
    if state.regime == "STRESSED":
        return {
            "blocked": True,
            "headline": "new entries blocked",
            "detail": "market regime is stressed",
            "reason_code": "stressed_regime",
        }
    if any("drawdown or exposure breach" in item.lower() for item in notes):
        return {
            "blocked": True,
            "headline": "new entries blocked",
            "detail": "risk committee pause after drawdown or exposure breach",
            "reason_code": "risk_committee",
        }
    return {
        "blocked": True,
        "headline": "new entries blocked",
        "detail": "risk gate closed for this cycle",
        "reason_code": "risk_gate",
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

    crypto_lane = _crypto_live_lane_snapshot(state)
    crypto_lane_history = _crypto_live_lane_history(state)
    signal_ready = crypto_lane.get("action") in {"probe_longs", "selective_probe", "attack_opening_drive"}
    signal_status = (
        "signal_ready"
        if signal_ready
        else "signal_arming"
        if crypto_lane.get("trigger_state") == "arming"
        else "signal_waiting"
    )
    signal_headline = (
        "Crypto signal is ready for a tiny live pilot."
        if signal_ready
        else f"Crypto signal still waiting: {crypto_lane.get('signal_score', 0.0):.2f} / {crypto_lane.get('trigger_threshold', 0.0):.2f}."
    )
    if normalize_execution_mode(settings.execution_mode) == "upbit_live" and allow_new_entries and crypto_lane.get("action") in {"watchlist_only", "capital_preservation"}:
        add_flag(
            "info",
            "crypto_live_wait",
            f"crypto live waiting: signal {crypto_lane.get('signal_score', 0.0):.2f} / trigger {crypto_lane.get('trigger_threshold', 0.0):.2f} / {crypto_lane.get('desk_bias', 'balanced')}",
        )

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


def _build_agent_log(state: CompanyState) -> list[dict]:
    """Format recent_journal entries for the AI activity feed."""
    result = []
    for entry in (state.recent_journal or []):
        desks = []
        for order in (entry.get("orders") or []):
            rationale_raw = order.get("rationale") or []
            notes = [str(r) for r in rationale_raw if isinstance(r, str)][:2]
            desks.append({
                "desk": order.get("desk", ""),
                "action": order.get("action", ""),
                "symbol": order.get("symbol", ""),
                "size": order.get("size", ""),
                "focus": order.get("focus", ""),
                "status": order.get("status", "idle"),
                "notes": notes,
            })
        result.append({
            "run_at": entry.get("run_at", ""),
            "stance": entry.get("stance", ""),
            "regime": entry.get("regime", ""),
            "company_focus": entry.get("company_focus", ""),
            "signals": (entry.get("summary") or [])[:3],
            "desks": desks,
        })
    return result


def _build_dashboard_payload(state: CompanyState) -> dict:
    closed_positions = load_closed_positions(limit=20)
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
        "desk_drilldown": _build_desk_drilldown_payload(state, closed_positions),
        "open_positions": state.open_positions,
        "closed_positions": closed_positions,
        "performance": _build_performance_payload(state, closed_positions),
        "capital": _build_capital_payload(state),
        "execution_summary": _build_execution_summary(state),
        "crypto_live_lane": _crypto_live_lane_snapshot(state),
        "crypto_live_lane_history": _crypto_live_lane_history(state),
        "symbol_edge": _symbol_edge_summary(state),
        "desk_offense": _build_desk_offense_payload(state),
        "agent_performance": _build_agent_performance_payload(state),
        "exposure": {
            "gross_open_notional_pct": float(state.daily_summary.get("gross_open_notional_pct", 0.0) or 0.0),
            "allow_new_entries": bool(state.allow_new_entries),
            "risk_budget": float(state.risk_budget),
            "entry_block_summary": _entry_block_summary(state),
        },
        "runtime_profile": _runtime_profile(state),
        "ops_flags": _build_ops_flags(state),
        "market_charts": _build_market_charts_payload(state),
        "agent_log": _build_agent_log(state),
    }


def _tail_lines(path: Path, lines: int = 40) -> list[str]:
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return content[-max(lines, 1):]


def _api_status_payload() -> dict:
    svc = service_status()
    state = load_company_state()
    next_run = None
    loop_running = bool((svc.get("loop", {}) or {}).get("running"))
    updated_at = _safe_parse_utc(state.updated_at)
    if updated_at is not None:
        freshness_limit = max(int(settings.realtime_watch_interval_seconds) * 3, 180)
        loop_running = loop_running or (datetime.now(timezone.utc) - updated_at).total_seconds() <= freshness_limit
    recent = list(state.recent_journal or [])
    if recent:
        latest_run = _safe_parse_utc(str(recent[0].get("run_at") or ""))
        if latest_run is not None:
            next_run = (latest_run + timedelta(seconds=settings.realtime_watch_interval_seconds)).isoformat()
    return {
        "running": loop_running,
        "server": {**(svc.get("server", {}) or {}), "running": True},
        "loop": {**(svc.get("loop", {}) or {}), "running": loop_running},
        "updated_at": state.updated_at,
        "next_run": next_run,
        "execution_mode": normalize_execution_mode(settings.execution_mode),
        "access": local_access_urls(),
    }


def _api_crypto_positions_payload(limit: int = 20) -> list[dict]:
    rows = [item for item in load_open_positions() if item.desk == "crypto"][:limit]
    result: list[dict] = []
    for row in rows:
        capital = round(float(settings.paper_capital_krw) * float(row.notional_pct or 0.0))
        unrealized = round(capital * float(row.unrealized_pnl_pct or 0.0) / 100)
        result.append(
            {
                "coin": row.symbol,
                "entry_price": row.entry_price,
                "current_price": row.current_price,
                "stop_loss": row.entry_price * 0.985 if row.entry_price else 0.0,
                "trailing_stop": row.entry_price * 0.99 if row.entry_price else 0.0,
                "capital": capital,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": float(row.unrealized_pnl_pct or 0.0),
                "entry_date": row.opened_at,
                "pyramid_count": 0,
            }
        )
    return result


def _api_stock_positions_payload(limit: int = 20) -> list[dict]:
    rows = [item for item in load_open_positions() if item.desk == "korea"][:limit]
    result: list[dict] = []
    for row in rows:
        capital = round(float(settings.paper_capital_krw) * float(row.notional_pct or 0.0))
        result.append(
            {
                "ticker": row.symbol,
                "name": row.symbol,
                "entry_price": row.entry_price,
                "stop_loss": row.entry_price * 0.985 if row.entry_price else 0.0,
                "capital": capital,
                "reason": row.action or "watch",
                "entry_date": row.opened_at,
            }
        )
    return result


def _api_trades_payload(limit: int = 50, desk: str | None = None) -> list[dict]:
    rows = load_closed_positions(limit=max(limit, 1))
    if desk:
        rows = [item for item in rows if str(item.get("desk") or "") == desk]
    result: list[dict] = []
    for item in rows[:limit]:
        capital = round(float(settings.paper_capital_krw) * float(item.get("notional_pct", 0.0) or 0.0))
        pnl_pct = float(item.get("pnl_pct", item.get("realized_pnl_pct", 0.0)) or 0.0)
        pnl = round(capital * pnl_pct / 100)
        result.append(
            {
                "coin": item.get("symbol", ""),
                "symbol": item.get("symbol", ""),
                "entry_price": float(item.get("entry_price", 0.0) or 0.0),
                "exit_price": float(item.get("exit_price", 0.0) or 0.0),
                "entry_date": item.get("opened_at", ""),
                "exit_date": item.get("closed_at", ""),
                "exit_reason": item.get("closed_reason", ""),
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "desk": item.get("desk", ""),
            }
        )
    return result


def _api_stats_payload() -> dict:
    state = load_company_state()
    stats = load_performance_quick_stats()
    realized_krw = int(state.daily_summary.get("realized_pnl_krw", 0) or 0)
    unrealized_krw = int(state.daily_summary.get("unrealized_pnl_krw", 0) or 0)
    total_pnl = realized_krw + unrealized_krw
    peak = max(1.0, 100.0 + max(float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0), 0.0))
    current = 100.0 + float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0) + float(state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0)
    mdd = round(min((current - peak) / peak, 0.0), 4)
    return {
        "total_pnl": total_pnl,
        "win_rate": float(stats.get("win_rate", 0.0) or 0.0) / 100.0,
        "sharpe": round(float(state.daily_summary.get("expectancy_pct", 0.0) or 0.0) / 1.5, 2),
        "mdd": mdd,
        "total_trades": int(stats.get("closed_positions", 0) or 0),
        "wins": int(state.daily_summary.get("wins", 0) or 0),
        "losses": int(state.daily_summary.get("losses", 0) or 0),
    }


def _api_market_regime_payload() -> dict:
    state = load_company_state()
    regime_map = {
        "TRENDING": "BULL" if state.stance == "OFFENSE" else "BEAR" if state.stance == "DEFENSE" else "NEUTRAL",
        "RANGING": "NEUTRAL",
        "STRESSED": "VOLATILE",
    }
    return {
        "regime": regime_map.get(state.regime, "NEUTRAL"),
        "lastChanged": state.updated_at,
    }


def _api_insights_payload() -> dict:
    state = load_company_state()
    agents: dict[str, dict] = {}
    for item in state.agent_runs or []:
        agents[item.name] = {
            "score": float(item.score or 0.0),
            "reason": str(item.reason or ""),
        }
    return {
        "insight_score": round(_compute_insight_score(state) / 100, 2),
        "agents": agents,
        "timestamp": state.updated_at,
    }


def _api_agent_status_payload() -> dict:
    state = load_company_state()
    agents: dict[str, dict] = {}
    for item in state.agent_runs or []:
        agents[item.name] = {
            "status": "ready",
            "score": float(item.score or 0.0),
            "last_run_at": item.generated_at,
        }
    snapshot = state.market_snapshot or {}
    return {
        "agents": agents,
        "strategy": {
            "direction": state.stance,
            "regime": state.regime,
        },
        "risk": {
            "allow_new_entries": state.allow_new_entries,
            "risk_budget": state.risk_budget,
        },
        "artifacts": {
            "coin_cached_count": len(snapshot.get("crypto_leaders", []) or []),
            "coin_signal_count": len((state.desk_views.get("crypto_desk", {}) or {}).get("reasons", []) or []),
            "stock_universe_count": len(snapshot.get("stock_leaders", []) or []),
            "stock_signal_count": len(snapshot.get("gap_candidates", []) or []),
        },
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


@app.get("/diagnostics/access-map")
def access_map() -> dict:
    auth_enabled = _auth_enabled()
    access = local_access_urls()
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "host": settings.host,
        "port": settings.port,
        "auth_enabled": auth_enabled,
        "execution_mode": settings.execution_mode,
        "app_env": settings.app_env,
        "access": access,
        "notes": [
            "local_url is always the machine-local browser route",
            "lan_url is the private network route detected from the current host",
            "public_url appears only when PUBLIC_BASE_URL is configured",
        ],
    }


@app.get("/state")
def state() -> dict:
    return load_company_state().model_dump()


@app.get("/dashboard-data")
def dashboard_data() -> dict:
    state = load_company_state()
    broker_health = _broker_live_health(state)
    readiness = _live_readiness_checklist(state, broker_health)
    return {
        "company_name": settings.company_name,
        "operator_name": settings.operator_name,
        "access": local_access_urls(),
        "deployment_profile": _deployment_profile(state),
        "state": state.model_dump(),
        "dashboard": _build_dashboard_payload(state),
        "broker_live_health": broker_health,
        "live_readiness_checklist": readiness,
        "upbit_live_pilot": _upbit_live_pilot(state, broker_health, readiness),
        "kis_live_pilot": _kis_live_pilot(state, broker_health, readiness),
    }


@app.get("/api/status")
def api_status() -> dict:
    return _api_status_payload()


@app.get("/api/positions")
def api_positions() -> list[dict]:
    return _api_crypto_positions_payload()


@app.get("/api/trades")
def api_trades(limit: int = 50) -> list[dict]:
    return _api_trades_payload(limit=limit, desk="crypto")


@app.get("/api/stats")
def api_stats() -> dict:
    return _api_stats_payload()


@app.get("/api/logs")
def api_logs(lines: int = 40) -> dict:
    merged = _tail_lines(LOOP_LOG_PATH, lines=max(lines, 1)) + _tail_lines(SERVER_LOG_PATH, lines=max(lines, 1))
    return {"lines": merged[-max(lines, 1):]}


@app.post("/api/bot/start")
def api_bot_start() -> dict:
    return start_services()


@app.post("/api/bot/stop")
def api_bot_stop() -> dict:
    return stop_services()


@app.get("/api/bot/market-regime")
def api_market_regime() -> dict:
    return _api_market_regime_payload()


@app.get("/api/stock/positions")
def api_stock_positions() -> list[dict]:
    return _api_stock_positions_payload()


@app.get("/api/stock/history")
def api_stock_history(limit: int = 30) -> list[dict]:
    return _api_trades_payload(limit=limit, desk="korea")


@app.get("/api/insights/")
def api_insights() -> dict:
    return _api_insights_payload()


@app.get("/api/insights/agents/status")
def api_agents_status() -> dict:
    return _api_agent_status_payload()


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
            "capital_profile": state.strategy_book.get("capital_profile", {}) or {},
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
        "capital_profile": state.strategy_book.get("capital_profile", {}) or {},
        "desk_offense": dashboard.get("desk_offense", []),
        "agent_performance": dashboard.get("agent_performance", []),
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
        "access": local_access_urls(),
        "deployment_profile": _deployment_profile(state),
        "phase": state.session_state.get("market_phase", "n/a"),
        "risk": {
            "stance": state.stance,
            "regime": state.regime,
            "risk_budget": state.risk_budget,
            "allow_new_entries": state.allow_new_entries,
            "gross_open_notional_pct": daily.get("gross_open_notional_pct", 0.0),
            "capital_profile": state.strategy_book.get("capital_profile", {}) or {},
        },
        "runtime_profile": _runtime_profile(state),
        "ops_flags": _build_ops_flags(state),
        "execution_summary": _build_execution_summary(state),
        "desk_offense": _build_desk_offense_payload(state),
        "agent_performance": _build_agent_performance_payload(state),
        "live_readiness": live_readiness_checklist(),
        "upbit_live_pilot": upbit_live_pilot(),
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
        daily_summary=state.daily_summary,
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


def _broker_live_health(state: CompanyState) -> dict:
    execution_summary = _build_execution_summary(state)
    configured_mode = normalize_execution_mode(settings.execution_mode)

    # In paper mode skip all live broker API calls entirely
    if configured_mode == "paper":
        return {
            "updated_at": state.updated_at,
            "execution_mode": configured_mode,
            "execution_summary": execution_summary,
            "upbit": {"enabled": False, "configured": bool(settings.upbit_access_key and settings.upbit_secret_key), "balances_ok": False, "note": "paper_mode"},
            "kis": {"enabled": False, "configured": bool(settings.kis_app_key and settings.kis_app_secret), "balances_ok": False, "note": "paper_mode"},
            "latest_live_orders": {"upbit_live": None, "kis_live": None},
        }

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
        if not snapshot["enabled"]:
            snapshot["latest_order_error"] = "live_not_enabled"
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
        if not snapshot["enabled"]:
            snapshot["latest_order_error"] = "live_not_enabled"
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
        "execution_mode": configured_mode,
        "execution_summary": execution_summary,
        "upbit": upbit_snapshot(),
        "kis": kis_snapshot(),
        "latest_live_orders": {
            "upbit_live": upbit_latest,
            "kis_live": kis_latest,
        },
    }


@app.get("/diagnostics/broker-live-health")
def broker_live_health() -> dict:
    state = load_company_state()
    return _broker_live_health(state)


def _live_readiness_checklist(state: CompanyState, broker_health: dict | None = None) -> dict:
    execution_summary = _build_execution_summary(state)
    broker_health = broker_health or _broker_live_health(state)
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

    mode = normalize_execution_mode(settings.execution_mode)
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
    next_actions: list[str] = []
    if settings.live_capital_krw <= 0:
        next_actions.append("Set LIVE_CAPITAL_KRW to the real capital you want the bot to control.")
    if not any(bool((broker_health.get(name, {}) or {}).get("configured")) for name in ("upbit", "kis")):
        next_actions.append("Configure one live broker first: Upbit for crypto or KIS for Korea.")
    if not any(bool((broker_health.get(name, {}) or {}).get("enabled")) for name in ("upbit", "kis")):
        next_actions.append("Enable exactly one live broker switch after credentials and balances verify cleanly.")
    if mode == "paper":
        next_actions.append("Move EXECUTION_MODE from paper to the intended live mode only after the checks above pass.")
    if not allow_new_entries:
        next_actions.append("Clear the current entry gate block before go-live; today's risk state is still defensive.")
    if not next_actions:
        next_actions.append("Run one tiny-size live validation cycle before scaling beyond minimum size.")

    current_step = next_actions[0] if next_actions else "Run one tiny-size live validation cycle before scaling beyond minimum size."
    primary_blocker = next((item["detail"] for item in checklist if item["status"] == "block"), "")
    status_headline = (
        "Go-live blocked"
        if overall == "blocked"
        else "Go-live needs caution"
        if overall == "caution"
        else "Go-live ready for tiny-size validation"
    )

    return {
        "updated_at": state.updated_at,
        "overall": overall,
        "block_count": block_count,
        "warn_count": warn_count,
        "status_headline": status_headline,
        "primary_blocker": primary_blocker,
        "current_step": current_step,
        "execution_mode": mode,
        "execution_summary": execution_summary,
        "entry_block_summary": _entry_block_summary(state),
        "checklist": checklist,
        "next_actions": next_actions[:5],
        "notes": (state.notes or [])[-8:],
    }


@app.get("/diagnostics/live-readiness-checklist")
def live_readiness_checklist() -> dict:
    state = load_company_state()
    return _live_readiness_checklist(state)


def _upbit_live_pilot(state: CompanyState, broker_health: dict | None = None, readiness: dict | None = None) -> dict:
    broker_health = broker_health or _broker_live_health(state)
    readiness = readiness or _live_readiness_checklist(state, broker_health)
    upbit = broker_health.get("upbit", {}) or {}
    execution_summary = broker_health.get("execution_summary", {}) or {}
    configured_mode = normalize_execution_mode(settings.execution_mode)
    crypto_lane = _crypto_live_lane_snapshot(state)
    crypto_lane_history = _crypto_live_lane_history(state)

    blockers: list[str] = []
    cautions: list[str] = []

    if settings.live_capital_krw <= 0:
        blockers.append("LIVE_CAPITAL_KRW is not configured.")
    if not bool(upbit.get("configured")):
        blockers.append("Upbit API credentials are missing.")
    if not bool(settings.upbit_allow_live):
        blockers.append("UPBIT_ALLOW_LIVE is false.")
    if configured_mode != "upbit_live":
        cautions.append("EXECUTION_MODE is not set to upbit_live yet.")
    if not bool(upbit.get("balances_ok")):
        cautions.append("Upbit balance check has not passed yet.")
    if not bool(state.allow_new_entries):
        cautions.append("Entry gate is currently blocked by risk mode.")
    if int(execution_summary.get("pending_count", 0) or 0) > 0:
        cautions.append("There are pending live orders that should be clean before pilot.")
    if int(execution_summary.get("stale_count", 0) or 0) > 0:
        blockers.append("There are stale live orders that must be cleared first.")

    pilot_cap_krw = 0
    if settings.live_capital_krw > 0:
        pilot_cap_krw = int(max(min(round(settings.live_capital_krw * 0.03), 300000), 50000))

    go_live_ready = not blockers and bool(upbit.get("balances_ok")) and bool(state.allow_new_entries)
    pilot_status = (
        "ready_for_tiny_size"
        if go_live_ready
        else "blocked"
        if blockers
        else "needs_caution"
    )
    pilot_headline = (
        "Upbit live pilot ready for a tiny-size validation cycle."
        if go_live_ready
        else blockers[0]
        if blockers
        else cautions[0]
        if cautions
        else "Upbit pilot still needs review."
    )
    mode_step = (
        "Keep EXECUTION_MODE=upbit_live on the live host."
        if configured_mode == "upbit_live"
        else "Set EXECUTION_MODE=upbit_live only after readiness blockers clear."
    )
    signal_ready = crypto_lane.get("action") in {"probe_longs", "selective_probe", "attack_opening_drive"}
    signal_status = (
        "signal_ready"
        if signal_ready
        else "signal_arming"
        if crypto_lane.get("trigger_state") == "arming"
        else "signal_waiting"
    )
    signal_headline = (
        "Crypto signal is ready for a tiny live pilot."
        if signal_ready
        else f"Crypto signal still waiting: {crypto_lane.get('signal_score', 0.0):.2f} / {crypto_lane.get('trigger_threshold', 0.0):.2f}."
    )
    return {
        "updated_at": state.updated_at,
        "go_live_ready": go_live_ready,
        "pilot_status": pilot_status,
        "pilot_headline": pilot_headline,
        "signal_ready": signal_ready,
        "signal_status": signal_status,
        "signal_headline": signal_headline,
        "broker": "upbit",
        "execution_mode": configured_mode,
        "pilot_cap_krw": pilot_cap_krw,
        "pilot_guardrails": {
            "max_order_krw": settings.upbit_pilot_max_krw,
            "single_order_only": settings.upbit_pilot_single_order_only,
        },
        "suggested_sequence": [
            "Verify balances and credentials with Upbit health check.",
            mode_step,
            "Run one tiny-size crypto entry/exit cycle first.",
            "Confirm order lookup, fill state, and position sync before scaling.",
        ],
        "blockers": blockers,
        "cautions": cautions[:5],
        "upbit_health": upbit,
        "crypto_lane": crypto_lane,
        "crypto_lane_history": crypto_lane_history,
        "entry_block_summary": _entry_block_summary(state),
        "readiness_overall": readiness.get("overall", "blocked"),
    }


@app.get("/diagnostics/upbit-live-pilot")
def upbit_live_pilot() -> dict:
    state = load_company_state()
    return _upbit_live_pilot(state)


def _kis_live_pilot(state: CompanyState, broker_health: dict | None = None, readiness: dict | None = None) -> dict:
    broker_health = broker_health or _broker_live_health(state)
    readiness = readiness or _live_readiness_checklist(state, broker_health)
    kis = broker_health.get("kis", {}) or {}
    execution_summary = broker_health.get("execution_summary", {}) or {}
    configured_mode = normalize_execution_mode(settings.execution_mode)

    blockers: list[str] = []
    cautions: list[str] = []

    if settings.live_capital_krw <= 0:
        blockers.append("LIVE_CAPITAL_KRW is not configured.")
    if not bool(kis.get("configured")):
        blockers.append("KIS API credentials are missing (KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO / KIS_PRODUCT_CODE).")
    if not bool(settings.kis_allow_live):
        blockers.append("KIS_ALLOW_LIVE is false.")
    if configured_mode != "kis_live":
        cautions.append("EXECUTION_MODE is not set to kis_live yet.")
    if not bool(kis.get("balances_ok")):
        cautions.append("KIS balance check has not passed yet.")
    if not bool(state.allow_new_entries):
        cautions.append("Entry gate is currently blocked by risk mode.")
    if int(execution_summary.get("pending_count", 0) or 0) > 0:
        cautions.append("There are pending live orders that should be clean before pilot.")
    if int(execution_summary.get("stale_count", 0) or 0) > 0:
        blockers.append("There are stale live orders that must be cleared first.")

    go_live_ready = not blockers and bool(kis.get("balances_ok")) and bool(state.allow_new_entries)
    pilot_status = (
        "ready_for_tiny_size"
        if go_live_ready
        else "blocked"
        if blockers
        else "needs_caution"
    )
    pilot_headline = (
        "KIS live pilot ready for a tiny-size Korea stock validation cycle."
        if go_live_ready
        else blockers[0]
        if blockers
        else cautions[0]
        if cautions
        else "KIS pilot still needs review."
    )
    mode_step = (
        "Keep EXECUTION_MODE=kis_live on the live host."
        if configured_mode == "kis_live"
        else "Set EXECUTION_MODE=kis_live only after readiness blockers clear."
    )
    korea_plan = (state.strategy_book or {}).get("korea_plan", {}) or {}
    korea_signal_ready = korea_plan.get("action") in {"attack_opening_drive", "selective_probe", "probe_longs"}
    return {
        "updated_at": state.updated_at,
        "go_live_ready": go_live_ready,
        "pilot_status": pilot_status,
        "pilot_headline": pilot_headline,
        "broker": "kis",
        "execution_mode": configured_mode,
        "korea_signal_ready": korea_signal_ready,
        "korea_action": korea_plan.get("action", "n/a"),
        "korea_focus": korea_plan.get("focus", ""),
        "korea_quality_score": float(korea_plan.get("quality_score", 0.0) or 0.0),
        "korea_quality_threshold": float(korea_plan.get("quality_threshold", 0.58) or 0.58),
        "suggested_sequence": [
            "Register KIS API credentials on the live host .env (KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, KIS_PRODUCT_CODE).",
            "Set KIS_ALLOW_LIVE=true and restart the service.",
            "Verify KIS balance check passes via /diagnostics/broker-live-health.",
            mode_step,
            "Run one tiny-size Korea stock entry/exit cycle during market hours (09:00-15:30 KST).",
            "Confirm fill state and position sync before scaling.",
        ],
        "blockers": blockers,
        "cautions": cautions[:5],
        "kis_health": kis,
        "entry_block_summary": _entry_block_summary(state),
        "readiness_overall": readiness.get("overall", "blocked"),
    }


@app.get("/diagnostics/kis-live-pilot")
def kis_live_pilot() -> dict:
    state = load_company_state()
    return _kis_live_pilot(state)


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


def _embedded_dashboard_html() -> str:  # noqa: PLR0915
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0d1117">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Trading Co.">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/app-icon.svg" type="image/svg+xml">
  <title>Trading Co. V2</title>
  <style>
    :root{
      --bg:#0d1117;--surface:rgba(13,17,23,.95);--border:rgba(48,54,61,1);--border-subtle:rgba(48,54,61,.6);
      --text:#e6edf3;--muted:#7d8590;--green:#3fb950;--green-bg:rgba(63,185,80,.12);--green-border:rgba(63,185,80,.3);
      --red:#f85149;--red-bg:rgba(248,81,73,.12);--red-border:rgba(248,81,73,.3);
      --blue:#58a6ff;--blue-bg:rgba(88,166,255,.10);--yellow:#d29922;--yellow-bg:rgba(210,153,34,.12);
      --font:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;--mono:'D2Coding','Consolas','IBM Plex Mono',monospace;
    }
    *{box-sizing:border-box}html,body{margin:0;padding:0}
    body{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;line-height:1.5;-webkit-font-smoothing:antialiased}
    .app{max-width:1200px;margin:0 auto;padding:16px 16px 80px}
    /* ── 상단 헤더 ── */
    .topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border:1px solid var(--border);border-radius:12px;background:rgba(22,27,34,.9);margin-bottom:14px}
    .topbar-left{display:flex;align-items:center;gap:10px}
    .app-name{font-size:.9rem;font-weight:700;color:var(--text)}
    .status-dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);flex-shrink:0}
    .status-dot.warn{background:var(--yellow);box-shadow:0 0 8px var(--yellow)}
    .status-dot.err{background:var(--red);box-shadow:0 0 8px var(--red)}
    .mode-tag{font-size:.72rem;font-weight:600;padding:3px 8px;border-radius:6px;border:1px solid var(--border);color:var(--muted)}
    .mode-tag.live{color:var(--green);border-color:var(--green-border);background:var(--green-bg)}
    .topbar-right{display:flex;align-items:center;gap:8px}
    .time-stamp{font-family:var(--mono);font-size:.78rem;color:var(--muted)}
    .btn{appearance:none;border:1px solid var(--border);border-radius:8px;padding:7px 14px;font-size:.82rem;font-weight:600;cursor:pointer;min-height:36px;font-family:var(--font)}
    .btn-primary{background:var(--blue-bg);color:var(--blue);border-color:rgba(88,166,255,.3)}
    .btn-ghost{background:transparent;color:var(--muted)}
    .btn:disabled{opacity:.4;cursor:not-allowed}
    /* ── 경보 배너 (문제 있을 때만) ── */
    .alert-bar{padding:10px 14px;border-radius:10px;border:1px solid var(--red-border);background:var(--red-bg);color:var(--red);font-size:.84rem;font-weight:600;margin-bottom:14px;display:none}
    .alert-bar.visible{display:block}
    /* ── 손익 히어로 ── */
    .pnl-hero{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
    .pnl-card{padding:16px;border-radius:12px;border:1px solid var(--border);background:rgba(22,27,34,.8)}
    .pnl-label{font-size:.72rem;color:var(--muted);margin-bottom:6px}
    .pnl-value{font-size:1.5rem;font-weight:800;font-family:var(--mono);letter-spacing:-.02em}
    .pnl-sub{font-size:.76rem;color:var(--muted);margin-top:4px;font-family:var(--mono)}
    .pnl-value.pos{color:var(--green)}.pnl-value.neg{color:var(--red)}.pnl-value.neu{color:var(--text)}
    .pnl-card.hl-pos{border-color:var(--green-border);background:var(--green-bg)}
    .pnl-card.hl-neg{border-color:var(--red-border);background:var(--red-bg)}
    /* ── 시스템 상태 바 ── */
    .status-bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
    .s-pill{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:rgba(22,27,34,.7);font-size:.8rem;font-weight:600}
    .s-pill .lbl{color:var(--muted);font-weight:400;font-size:.73rem;margin-right:2px}
    .s-pill.ok{border-color:var(--green-border);background:var(--green-bg);color:var(--green)}
    .s-pill.warn{border-color:rgba(210,153,34,.3);background:var(--yellow-bg);color:var(--yellow)}
    .s-pill.bad{border-color:var(--red-border);background:var(--red-bg);color:var(--red)}
    .s-pill.neu{color:var(--text)}
    /* ── 코인 신호 게이지 ── */
    .signal-card{border:1px solid var(--border);border-radius:12px;background:rgba(22,27,34,.8);padding:16px;margin-bottom:14px}
    .signal-card.arming{border-color:rgba(210,153,34,.4);background:var(--yellow-bg)}
    .signal-card.ready{border-color:var(--green-border);background:var(--green-bg)}
    .signal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
    .signal-title{font-size:.8rem;font-weight:700;color:var(--muted)}
    .signal-badge{font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:6px;border:1px solid currentColor}
    .signal-badge.waiting{color:var(--muted);border-color:var(--border)}
    .signal-badge.arming{color:var(--yellow);border-color:rgba(210,153,34,.4);background:var(--yellow-bg)}
    .signal-badge.ready{color:var(--green);border-color:var(--green-border);background:var(--green-bg)}
    .signal-sym{font-size:1rem;font-weight:700;margin-bottom:8px}
    .gauge-wrap{margin:10px 0}
    .gauge-track{height:8px;border-radius:999px;background:rgba(48,54,61,.8);position:relative;overflow:visible}
    .gauge-fill{height:100%;border-radius:999px;background:var(--blue);transition:width .4s}
    .gauge-fill.arming{background:var(--yellow)}
    .gauge-fill.ready{background:var(--green)}
    .gauge-labels{display:flex;justify-content:space-between;margin-top:5px;font-family:var(--mono);font-size:.72rem;color:var(--muted)}
    .mini-gauge-wrap{margin:6px 0 4px}
    .mini-gauge-track{height:4px;border-radius:999px;background:rgba(48,54,61,.8);overflow:hidden}
    .mini-gauge-fill{height:100%;border-radius:999px;background:var(--muted);transition:width .4s}
    .mini-gauge-fill.arming{background:var(--yellow)}
    .mini-gauge-fill.ready{background:var(--green)}
    .mini-gauge-lbls{display:flex;justify-content:space-between;margin-top:3px;font-family:var(--mono);font-size:.66rem;color:var(--muted)}
    .signal-meta{font-size:.78rem;color:var(--muted);margin-top:6px}
    .trend-mini{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
    .trend-chip{font-family:var(--mono);font-size:.70rem;padding:3px 8px;border-radius:6px;background:rgba(48,54,61,.6);color:var(--muted);border:1px solid var(--border-subtle)}
    /* ── 데스크 카드 ── */
    .desk-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
    .desk-card{border:1px solid var(--border);border-radius:12px;background:rgba(22,27,34,.8);padding:14px}
    .desk-card{appearance:none;text-align:left;cursor:pointer;width:100%;color:var(--text)}
    .desk-card.active{box-shadow:0 0 0 1px rgba(88,166,255,.25) inset,0 12px 26px rgba(0,0,0,.18);transform:translateY(-1px)}
    .desk-card.buy{border-color:var(--green-border)}.desk-card.sell{border-color:var(--red-border)}.desk-card.probe{border-color:rgba(88,166,255,.3)}
    .desk-name{font-size:.7rem;color:var(--muted);margin-bottom:6px;font-weight:600;text-transform:uppercase;letter-spacing:.08em}
    .desk-action{font-size:.88rem;font-weight:700;margin-bottom:4px}
    .desk-action.buy{color:var(--green)}.desk-action.sell{color:var(--red)}.desk-action.probe{color:var(--blue)}.desk-action.watch{color:var(--muted)}
    .desk-focus{font-size:.74rem;color:var(--muted);line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
    .desk-size{font-family:var(--mono);font-size:.8rem;font-weight:700;margin-top:6px}
    .desk-size.active{color:var(--blue)}
    .desk-detail-panel{display:none;margin:-2px 0 14px;border:1px solid var(--border);border-radius:14px;background:rgba(13,18,24,.92);padding:14px 14px 12px}
    .desk-detail-panel.open{display:block}
    .desk-detail-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}
    .desk-detail-close{border:1px solid var(--border-subtle);background:rgba(255,255,255,.03);color:var(--text);border-radius:999px;padding:4px 10px;font-size:.78rem;cursor:pointer}
    .desk-detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
    .desk-detail-card{border:1px solid var(--border-subtle);border-radius:12px;background:rgba(255,255,255,.03);padding:12px}
    .desk-detail-card-wide{grid-column:1 / -1}
    .desk-detail-label{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
    .desk-detail-main{font-size:.98rem;font-weight:700}
    .desk-detail-sub{font-size:.78rem;color:var(--muted);line-height:1.45;margin-top:4px}
    .desk-chip-row{display:flex;flex-wrap:wrap;gap:6px}
    .desk-chip{display:inline-flex;align-items:center;padding:5px 8px;border-radius:999px;background:rgba(88,166,255,.12);border:1px solid rgba(88,166,255,.2);font-size:.76rem}
    .desk-list{display:grid;gap:8px}
    .desk-list-item{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--border-subtle);font-size:.78rem}
    .desk-list-item:first-child{border-top:none;padding-top:0}
    .desk-list-main{font-weight:600}
    .desk-list-sub{color:var(--muted);font-size:.74rem;line-height:1.4;margin-top:2px}
    .desk-list-side{font-family:var(--mono);text-align:right;white-space:nowrap}
    /* ── 주문 현황 배지 ── */
    .order-bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
    .o-badge{display:inline-flex;align-items:center;gap:5px;padding:6px 10px;border-radius:8px;border:1px solid var(--border);font-size:.78rem;font-weight:600}
    .o-badge .num{font-family:var(--mono);font-weight:800}
    .o-badge.warn{border-color:rgba(210,153,34,.3);background:var(--yellow-bg);color:var(--yellow)}
    .o-badge.bad{border-color:var(--red-border);background:var(--red-bg);color:var(--red)}
    .o-badge.ok{border-color:var(--green-border);background:var(--green-bg);color:var(--green)}
    .o-badge.muted{color:var(--muted)}
    /* ── 포지션 / 거래 내역 ── */
    .section-title{font-size:.8rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px}
    .pos-table{width:100%;border-collapse:collapse;font-size:.84rem}
    .pos-table th{text-align:left;padding:8px 10px;color:var(--muted);font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--border)}
    .pos-table td{padding:10px;border-bottom:1px solid var(--border-subtle)}
    .pos-table tr:last-child td{border-bottom:none}
    .pos-table tbody tr:hover{background:rgba(48,54,61,.3)}
    .sym{font-family:var(--mono);font-weight:700}
    .desk-tag{display:inline-flex;padding:2px 7px;border-radius:5px;background:var(--blue-bg);color:var(--blue);font-size:.70rem;font-weight:600}
    .pos{color:var(--green)!important}.neg{color:var(--red)!important}
    .table-section{margin-bottom:16px;border:1px solid var(--border);border-radius:12px;overflow:hidden}
    .table-head{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-bottom:1px solid var(--border);background:rgba(22,27,34,.6)}
    .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
    .empty-row{padding:16px;text-align:center;color:var(--muted);font-size:.84rem}
    .count-badge{font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:5px;background:var(--blue-bg);color:var(--blue);font-family:var(--mono)}
    /* ── 수익 곡선 ── */
    .chart-section{border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px}
    .chart-head{padding:12px 14px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
    #equity-svg{display:block;width:100%;height:140px}
    /* ── 준비도 / 브로커 (접힘) ── */
    .detail-section{border:1px solid var(--border);border-radius:12px;margin-bottom:12px;overflow:hidden}
    .detail-toggle{width:100%;display:flex;justify-content:space-between;align-items:center;padding:12px 14px;background:rgba(22,27,34,.6);border:none;cursor:pointer;color:var(--text);font-family:var(--font);font-size:.84rem;font-weight:600}
    .detail-toggle .arrow{font-size:.72rem;color:var(--muted);transition:transform .2s}
    .detail-toggle.open .arrow{transform:rotate(180deg)}
    .detail-body{display:none;padding:12px 14px}
    .detail-body.open{display:block}
    .check-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-subtle);font-size:.82rem}
    .check-row:last-child{border-bottom:none}
    .check-lbl{color:var(--text)}.check-det{font-size:.74rem;color:var(--muted);margin-top:2px}
    .status-tag{font-size:.70rem;font-weight:700;padding:3px 8px;border-radius:5px}
    .status-tag.pass{color:var(--green);background:var(--green-bg)}.status-tag.warn{color:var(--yellow);background:var(--yellow-bg)}.status-tag.block{color:var(--red);background:var(--red-bg)}
    .broker-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-subtle)}
    .broker-row:last-child{border-bottom:none}
    .broker-name{font-weight:700;font-size:.84rem}.broker-ok{color:var(--green);font-size:.78rem}.broker-warn{color:var(--yellow);font-size:.78rem}.broker-muted{color:var(--muted);font-size:.78rem}
    /* ── AI 판단 이력 ── */
    .agent-log{border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:14px}
    .agent-log-head{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-bottom:1px solid var(--border);background:rgba(22,27,34,.6)}
    .cycle-entry{padding:10px 14px;border-bottom:1px solid var(--border-subtle)}
    .cycle-entry:last-child{border-bottom:none}
    .cycle-entry.latest{background:rgba(88,166,255,.04);border-left:2px solid rgba(88,166,255,.4)}
    .cycle-header{display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap}
    .cycle-time{font-family:var(--mono);font-weight:700;color:var(--text);font-size:.8rem}
    .cycle-badge{font-size:.66rem;font-weight:700;padding:2px 7px;border-radius:4px}
    .cycle-badge.offense{color:var(--green);background:var(--green-bg);border:1px solid var(--green-border)}
    .cycle-badge.defense{color:var(--red);background:var(--red-bg);border:1px solid var(--red-border)}
    .cycle-badge.neutral{color:var(--muted);background:rgba(48,54,61,.4);border:1px solid var(--border)}
    .desk-rows{display:grid;gap:5px}
    .desk-row{display:flex;align-items:flex-start;gap:7px;font-size:.77rem}
    .desk-row-tag{font-size:.64rem;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(88,166,255,.12);color:var(--blue);white-space:nowrap;flex-shrink:0;margin-top:1px}
    .desk-row-body{flex:1;min-width:0}
    .desk-row-act{font-weight:700}
    .desk-row-act.buy{color:var(--green)}.desk-row-act.probe{color:var(--blue)}.desk-row-act.sell{color:var(--red)}.desk-row-act.watch{color:var(--muted)}.desk-row-act.wanted{color:var(--yellow)}
    .desk-row-note{font-size:.71rem;color:var(--muted);line-height:1.4;margin-top:1px}
    .cycle-signals{font-size:.71rem;color:var(--muted);margin-top:5px;padding-top:5px;border-top:1px solid var(--border-subtle);line-height:1.4}
    /* ── 브레이크아웃 뱃지 + 후보 종목 ── */
    .desk-bk-badge{font-size:.62rem;font-weight:700;padding:1px 5px;border-radius:4px;vertical-align:middle;margin-left:3px}
    .desk-bk-badge.full{background:rgba(63,185,80,.18);color:var(--green);border:1px solid rgba(63,185,80,.3)}
    .desk-bk-badge.partial{background:rgba(210,153,34,.15);color:var(--yellow);border:1px solid rgba(210,153,34,.25)}
    .bk-badge{font-size:.65rem;font-weight:700;padding:2px 6px;border-radius:4px;white-space:nowrap}
    .bk-badge.full{background:rgba(63,185,80,.18);color:var(--green);border:1px solid rgba(63,185,80,.3)}
    .bk-badge.partial{background:rgba(210,153,34,.15);color:var(--yellow);border:1px solid rgba(210,153,34,.25)}
    .bk-chip{display:inline-block;font-size:.65rem;padding:1px 5px;border-radius:4px;background:rgba(255,255,255,.06);margin-right:4px;color:var(--muted)}
    .cand-row{display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border-subtle);gap:8px}
    .cand-row:last-child{border-bottom:none}
    .cand-row.primary .cand-name{color:var(--blue);font-weight:700}
    .cand-left{flex:1;min-width:0}
    .cand-name{font-size:.82rem;font-weight:600;margin-bottom:2px}
    .cand-chips{display:flex;flex-wrap:wrap;gap:3px;margin-top:2px}
    .cand-right{display:flex;align-items:center;gap:6px;flex-shrink:0}
    .cand-score{font-family:var(--mono);font-size:.78rem;color:var(--muted)}
    /* ── 반응형 ── */
    @media(max-width:900px){.pnl-hero{grid-template-columns:repeat(2,1fr)}.desk-grid{grid-template-columns:repeat(3,1fr)}}
    @media(max-width:600px){.app{padding:12px 12px 72px}.topbar{padding:10px 12px}.pnl-hero{grid-template-columns:repeat(2,1fr)}.pnl-value{font-size:1.2rem}.desk-grid{grid-template-columns:repeat(3,1fr)}.desk-card{padding:10px}.desk-action{font-size:.8rem}.desk-focus{display:none}.status-bar{gap:6px}.s-pill{font-size:.75rem;padding:5px 10px}.btn{padding:6px 12px;font-size:.78rem}}
    @media(max-width:400px){.pnl-hero,.desk-grid{grid-template-columns:repeat(2,1fr)}.desk-grid .desk-card:last-child{grid-column:span 2}}
  </style>
</head>
<body>
<div class="app">

  <!-- 상단 헤더 -->
  <header class="topbar">
    <div class="topbar-left">
      <div class="status-dot" id="status-dot"></div>
      <span class="app-name">Trading Co. V2</span>
      <span class="mode-tag" id="mode-tag">모의투자</span>
    </div>
    <div class="topbar-right">
      <span class="time-stamp">업데이트 <span id="update-time">--:--</span></span>
      <button class="btn btn-ghost" onclick="loadData()">새로고침</button>
      <button class="btn btn-primary" id="cycle-btn" onclick="runCycle()">사이클 실행</button>
    </div>
  </header>

  <!-- 경보 배너 -->
  <div class="alert-bar" id="alert-bar"></div>

  <!-- 손익 히어로 -->
  <div class="pnl-hero">
    <div class="pnl-card" id="pnl-realized-card">
      <div class="pnl-label">오늘 실현손익</div>
      <div class="pnl-value neu" id="pnl-realized">--</div>
      <div class="pnl-sub" id="pnl-realized-krw">--</div>
    </div>
    <div class="pnl-card" id="pnl-unrealized-card">
      <div class="pnl-label">미실현손익</div>
      <div class="pnl-value neu" id="pnl-unrealized">--</div>
      <div class="pnl-sub" id="pnl-unrealized-krw">--</div>
    </div>
    <div class="pnl-card">
      <div class="pnl-label">승률 (오늘)</div>
      <div class="pnl-value neu" id="pnl-winrate">--%</div>
      <div class="pnl-sub" id="pnl-trades">-- 승 / -- 패</div>
    </div>
    <div class="pnl-card">
      <div class="pnl-label">실전자본</div>
      <div class="pnl-value neu" id="pnl-capital">--</div>
      <div class="pnl-sub" id="pnl-capital-base">기준 --</div>
    </div>
  </div>

  <!-- 시스템 상태 바 -->
  <div class="status-bar" id="status-bar">
    <div class="s-pill neu"><span class="lbl">스탠스</span><span id="st-stance">--</span></div>
    <div class="s-pill neu"><span class="lbl">국면</span><span id="st-regime">--</span></div>
    <div class="s-pill neu"><span class="lbl">진입</span><span id="st-entry">--</span></div>
    <div class="s-pill neu"><span class="lbl">준비도</span><span id="st-ready">--</span></div>
  </div>

  <!-- 코인 신호 게이지 -->
  <div class="signal-card" id="signal-card">
    <div class="signal-header">
      <div class="signal-title">코인 파일럿 시그널</div>
      <div class="signal-badge waiting" id="signal-badge">대기</div>
    </div>
    <div class="signal-sym" id="signal-sym">KRW-BTC</div>
    <div class="gauge-wrap">
      <div class="gauge-track">
        <div class="gauge-fill" id="gauge-fill" style="width:0%"></div>
      </div>
      <div class="gauge-labels">
        <span>0</span>
        <span id="gauge-cur">현재: --</span>
        <span id="gauge-trig">진입: --</span>
      </div>
    </div>
    <div class="signal-meta" id="signal-meta">시그널 데이터 로딩 중...</div>
    <div class="trend-mini" id="trend-mini"></div>
  </div>

  <!-- 데스크 카드 -->
  <div class="desk-grid" id="desk-grid">
    <button type="button" class="desk-card" data-desk="crypto" onclick="toggleDeskDetail('crypto')"><div class="desk-name">코인 <span class="desk-bk-badge" id="dk-crypto-bk" style="display:none"></span></div><div class="desk-action watch" id="dk-crypto-act">--</div><div class="desk-focus" id="dk-crypto-focus">--</div><div class="desk-size" id="dk-crypto-size">0.00x</div></button>
    <button type="button" class="desk-card" data-desk="korea" onclick="toggleDeskDetail('korea')"><div class="desk-name">한국주식 <span class="desk-bk-badge" id="dk-korea-bk" style="display:none"></span></div><div class="desk-action watch" id="dk-korea-act">--</div><div class="desk-focus" id="dk-korea-focus">--</div><div class="mini-gauge-wrap"><div class="mini-gauge-track"><div class="mini-gauge-fill" id="dk-korea-qfill" style="width:0%"></div></div><div class="mini-gauge-lbls"><span id="dk-korea-qval">품질 --</span><span id="dk-korea-qthr">기준 --</span></div></div><div class="desk-size" id="dk-korea-size">0.00x</div></button>
    <button type="button" class="desk-card" data-desk="us" onclick="toggleDeskDetail('us')"><div class="desk-name">미국주식 <span class="desk-bk-badge" id="dk-us-bk" style="display:none"></span></div><div class="desk-action watch" id="dk-us-act">--</div><div class="desk-focus" id="dk-us-focus">--</div><div class="mini-gauge-wrap"><div class="mini-gauge-track"><div class="mini-gauge-fill" id="dk-us-qfill" style="width:0%"></div></div><div class="mini-gauge-lbls"><span id="dk-us-qval">품질 --</span><span id="dk-us-qthr">기준 --</span></div></div><div class="desk-size" id="dk-us-size">0.00x</div></button>
  </div>

  <div class="desk-detail-panel" id="desk-detail-panel">
    <div class="desk-detail-head">
      <div>
        <div class="section-title" id="desk-detail-title">Desk detail</div>
        <div class="desk-detail-sub" id="desk-detail-sub">--</div>
      </div>
      <button type="button" class="desk-detail-close" onclick="toggleDeskDetail(window.__activeDeskDetail || '')">닫기</button>
    </div>
    <div class="desk-detail-grid">
      <div class="desk-detail-card">
        <div class="desk-detail-label">Target</div>
        <div class="desk-detail-main" id="desk-detail-target">--</div>
        <div class="desk-detail-sub" id="desk-detail-plan">--</div>
      </div>
      <div class="desk-detail-card">
        <div class="desk-detail-label">Performance</div>
        <div class="desk-detail-main" id="desk-detail-pnl">--</div>
        <div class="desk-detail-sub" id="desk-detail-winrate">--</div>
      </div>
      <div class="desk-detail-card desk-detail-card-wide">
        <div class="desk-detail-label">후보 종목 <span style="font-size:.7rem;color:var(--muted);font-weight:400">BK = 브레이크아웃 신호 수</span></div>
        <div id="desk-detail-candidates"></div>
      </div>
      <div class="desk-detail-card desk-detail-card-wide">
        <div class="desk-detail-label">Watchlist</div>
        <div class="desk-chip-row" id="desk-detail-watchlist"></div>
      </div>
      <div class="desk-detail-card desk-detail-card-wide">
        <div class="desk-detail-label">Open Positions</div>
        <div class="desk-list" id="desk-detail-open">--</div>
      </div>
      <div class="desk-detail-card desk-detail-card-wide">
        <div class="desk-detail-label">Recent Orders</div>
        <div class="desk-list" id="desk-detail-orders">--</div>
      </div>
      <div class="desk-detail-card desk-detail-card-wide">
        <div class="desk-detail-label">Recent Closed</div>
        <div class="desk-list" id="desk-detail-closed">--</div>
      </div>
    </div>
  </div>

  <!-- AI 에이전트 판단 이력 -->
  <div class="agent-log">
    <div class="agent-log-head">
      <span class="section-title">AI \uc5d0\uc774\uc804\ud2b8 \ud310\ub2e8 \uc774\ub825</span>
      <span class="count-badge" id="agent-log-count">0</span>
    </div>
    <div id="agent-log-body"><div class="empty-row">Loading...</div></div>
  </div>

  <!-- 주문 현황 (있을 때만 표시) -->
  <div class="order-bar" id="order-bar" style="display:none"></div>

  <!-- 보유 포지션 -->
  <div class="table-section">
    <div class="table-head">
      <span class="section-title">보유 포지션</span>
      <span class="count-badge" id="pos-count">0</span>
    </div>
    <div class="table-wrap">
      <div id="positions-body"><div class="empty-row">보유 포지션 없음</div></div>
    </div>
  </div>

  <!-- 수익 곡선 -->
  <div class="chart-section">
    <div class="chart-head">
      <span class="section-title">수익 곡선</span>
      <span style="font-family:var(--mono);font-size:.78rem;color:var(--muted)" id="equity-label">--</span>
    </div>
    <svg id="equity-svg" viewBox="0 0 400 140" preserveAspectRatio="none"></svg>
  </div>

  <!-- 최근 청산 -->
  <div class="table-section">
    <div class="table-head">
      <span class="section-title">최근 청산 내역</span>
      <span style="font-size:.74rem;color:var(--muted)" id="trades-count-label">\ucd5c\uadfc 15\uac74</span>
    </div>
    <div class="table-wrap">
      <div id="trades-body"><div class="empty-row">청산 내역 없음</div></div>
    </div>
  </div>

  <!-- 브로커 / 준비도 (접힘) -->
  <div class="detail-section">
    <button class="detail-toggle" onclick="toggleDetail('broker-body',this)">
      브로커 상태 &amp; 실전 준비도 <span class="arrow">▼</span>
    </button>
    <div class="detail-body" id="broker-body">
      <div id="broker-rows"></div>
      <div style="margin-top:12px;border-top:1px solid var(--border-subtle);padding-top:12px" id="readiness-rows"></div>
    </div>
  </div>

</div><!-- /app -->

<script>
  function toKST(iso){if(!iso||iso==='--')return '--:--';try{return new Date(iso).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false});}catch(e){return String(iso).slice(11,16)||'--:--';}}
  function toKSTFull(iso){if(!iso||iso==='--')return '--';try{var d=new Date(iso);var date=d.toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit'});var time=d.toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false});return date+' '+time;}catch(e){return String(iso).slice(0,16)||'--';}}
  function fmtPct(v){var n=parseFloat(v)||0;return(n>=0?'+':'')+n.toFixed(2)+'%';}
  function fmtKrw(v){var n=Math.abs(parseInt(v)||0);return n>=1000000?(n/1000000).toFixed(2)+'M':n>=1000?(n/1000).toFixed(0)+'K':String(n);}
  function fmtKrwFull(v,sign){var n=parseInt(v)||0;var prefix=sign?(n>=0?'+':''):'';return prefix+'\\u20a9'+Math.abs(n).toLocaleString('ko-KR');}
  function pctCls(v){var n=parseFloat(v)||0;return n>0?'pos':n<0?'neg':'';}
  function actionCls(a){var s=String(a||'').toLowerCase();if(s.indexOf('probe_long')>=0||s.indexOf('attack')>=0)return 'buy';if(s.indexOf('reduce')>=0||s.indexOf('preservation')>=0)return 'sell';if(s.indexOf('probe')>=0||s.indexOf('selective')>=0)return 'probe';return 'watch';}
  function actionKo(a){var s=String(a||'').toLowerCase();if(s==='probe_longs')return '롱 진입 탐색';if(s==='selective_probe')return '선택적 진입';if(s==='attack_opening_drive')return '공세 진입';if(s==='reduce_risk')return '리스크 축소';if(s==='capital_preservation')return '자본 보존';if(s==='watchlist_only')return '관찰 대기';if(s==='stand_by')return '대기';if(s==='pre_market_watch')return '장 외 대기';if(s==='n/a')return '--';return a||'대기';}
  window.__activeDeskDetail = null;
  window.__deskDrilldown = {};
  function toggleDetail(id,btn){var el=document.getElementById(id);el.classList.toggle('open');btn.classList.toggle('open');}
  function listHtml(items, emptyText, mapper){if(!items||!items.length)return '<div class="empty-row">'+emptyText+'</div>';return items.map(mapper).join('');}
  function renderDeskDetail(desk){
    var panel=document.getElementById('desk-detail-panel');
    var rows=document.querySelectorAll('#desk-grid .desk-card');
    rows.forEach(function(node){node.classList.toggle('active', node.getAttribute('data-desk')===desk);});
    if(!desk || window.__activeDeskDetail===desk){
      window.__activeDeskDetail=null;
      panel.classList.remove('open');
      return;
    }
    window.__activeDeskDetail=desk;
    var item=(window.__deskDrilldown||{})[desk]||{};
    document.getElementById('desk-detail-title').textContent=item.title||desk;
    document.getElementById('desk-detail-sub').textContent=item.focus||'현재 포커스 없음';
    document.getElementById('desk-detail-target').textContent=item.target_symbol||'--';
    document.getElementById('desk-detail-plan').textContent=actionKo(item.action)+' / '+String(item.size||'0.00x');
    document.getElementById('desk-detail-pnl').textContent=fmtPct(item.realized_pnl_pct||0);
    document.getElementById('desk-detail-pnl').className='desk-detail-main '+pctCls(item.realized_pnl_pct||0);
    document.getElementById('desk-detail-winrate').textContent='승률 '+Number(item.win_rate||0).toFixed(1)+'% / '+(item.wins||0)+'승 '+(item.losses||0)+'패';
    document.getElementById('desk-detail-watchlist').innerHTML=(item.watch_symbols||[]).length?(item.watch_symbols||[]).map(function(symbol){return '<span class="desk-chip">'+symbol+'</span>';}).join(''):'<span class="desk-detail-sub">감시 종목 없음</span>';
    // Candidate details with breakout badges
    var candEl=document.getElementById('desk-detail-candidates');
    if(candEl){
      var cands=item.candidate_details||[];
      if(!cands.length){candEl.innerHTML='<div class="empty-row">후보 종목 없음</div>';}
      else{
        candEl.innerHTML=cands.map(function(c){
          var isPrim=c.is_primary;var label=String(c.label||c.symbol||'--');
          var bkCount=parseInt(c.breakout_count||0);var isBreakout=c.is_breakout||bkCount>=3;
          var volRatio=parseFloat(c.vol_ratio||0);var score=parseFloat(c.score||0);
          var gapPct=parseFloat(c.gap_pct||0);var rsiVal=c.rsi!=null?parseFloat(c.rsi):null;
          var bkBadge=isBreakout?'<span class="bk-badge full">BK '+bkCount+'/4</span>':
                      bkCount>=2?'<span class="bk-badge partial">BK '+bkCount+'/4</span>':'';
          var gapStr=gapPct>0.2?'<span class="bk-chip">갭 +'+gapPct.toFixed(1)+'%</span>':'';
          var volStr=volRatio>=2?'<span class="bk-chip">거래량 '+volRatio.toFixed(1)+'x</span>':'';
          var rsiStr=rsiVal!=null?'<span class="bk-chip">RSI '+rsiVal.toFixed(0)+'</span>':'';
          return '<div class="cand-row'+(isPrim?' primary':'')+'">'+
            '<div class="cand-left"><div class="cand-name">'+label+'</div>'+
            '<div class="cand-chips">'+gapStr+volStr+rsiStr+'</div></div>'+
            '<div class="cand-right"><span class="cand-score">'+score.toFixed(2)+'</span>'+bkBadge+'</div></div>';
        }).join('');
      }
    }
    document.getElementById('desk-detail-open').innerHTML=listHtml(item.open_positions||[],'보유 포지션 없음',function(row){return '<div class="desk-list-item"><div><div class="desk-list-main">'+(row.symbol||'--')+'</div><div class="desk-list-sub">'+toKST(row.opened_at||'')+' / '+(row.action||'watch')+'</div></div><div class="desk-list-side '+pctCls(row.unrealized_pnl_pct||0)+'">'+fmtPct(row.unrealized_pnl_pct||0)+'</div></div>';});
    document.getElementById('desk-detail-orders').innerHTML=listHtml(item.recent_orders||[],'최근 주문 없음',function(row){return '<div class="desk-list-item"><div><div class="desk-list-main">'+(row.symbol||'--')+'</div><div class="desk-list-sub">'+(row.status||'n/a')+' / '+(row.effect_status||'n/a')+'</div></div><div class="desk-list-side">'+toKST(row.created_at||'')+'</div></div>';});
    document.getElementById('desk-detail-closed').innerHTML=listHtml(item.recent_closed||[],'최근 청산 없음',function(row){return '<div class="desk-list-item"><div><div class="desk-list-main">'+(row.symbol||'--')+'</div><div class="desk-list-sub">'+(row.closed_reason||'--')+'</div></div><div class="desk-list-side '+pctCls(row.pnl_pct||0)+'">'+fmtPct(row.pnl_pct||0)+'</div></div>';});
    panel.classList.add('open');
  }
  function toggleDeskDetail(desk){renderDeskDetail(desk);}
  function renderPnl(perf,cap){var rp=parseFloat(perf.realized_pnl_pct||0),up=parseFloat(perf.unrealized_pnl_pct||0);var rc=document.getElementById('pnl-realized-card'),uc=document.getElementById('pnl-unrealized-card');document.getElementById('pnl-realized').textContent=fmtPct(rp);document.getElementById('pnl-realized').className='pnl-value '+(rp>0?'pos':rp<0?'neg':'neu');document.getElementById('pnl-realized-krw').textContent=fmtKrwFull(perf.realized_pnl_krw,true);rc.className='pnl-card'+(rp>0?' hl-pos':rp<0?' hl-neg':'');document.getElementById('pnl-unrealized').textContent=fmtPct(up);document.getElementById('pnl-unrealized').className='pnl-value '+(up>0?'pos':up<0?'neg':'neu');document.getElementById('pnl-unrealized-krw').textContent=fmtKrwFull(perf.unrealized_pnl_krw,true);uc.className='pnl-card'+(up>0?' hl-pos':up<0?' hl-neg':'');var wr=parseFloat(perf.win_rate||0);document.getElementById('pnl-winrate').textContent=wr.toFixed(1)+'%';document.getElementById('pnl-winrate').className='pnl-value '+(wr>=55?'pos':wr<40?'neg':'neu');document.getElementById('pnl-trades').textContent=(perf.wins||0)+'승 / '+(perf.losses||0)+'패 / 기대값 '+fmtPct(perf.expectancy_pct);document.getElementById('pnl-capital').textContent='\\u20a9'+(parseInt(cap.total_krw||0)).toLocaleString('ko-KR');document.getElementById('pnl-capital-base').textContent='기준 \\u20a9'+(parseInt(cap.base_krw||0)).toLocaleString('ko-KR');}
  function renderStatusBar(state,readiness,blockSummary){var stance=String(state.stance||'--');var regime=String(state.regime||'--');var allow=!!((readiness.exposure||{}).allow_new_entries!=null?(readiness.exposure||{}).allow_new_entries:state.allow_new_entries);var overall=String(readiness.overall||'caution');var stanceCls=stance==='BULLISH'?'ok':stance==='DEFENSE'?'bad':'warn';var regimeCls=regime==='TRENDING'?'ok':regime==='STRESSED'?'bad':'warn';var bar=document.getElementById('status-bar');bar.innerHTML='<div class="s-pill '+stanceCls+'"><span class="lbl">스탠스</span>'+stance+'</div>'+'<div class="s-pill '+regimeCls+'"><span class="lbl">국면</span>'+regime+'</div>'+'<div class="s-pill '+(allow?'ok':'bad')+'"><span class="lbl">진입</span>'+(allow?'허용':'차단')+'</div>'+'<div class="s-pill '+(overall==='ready'?'ok':overall==='caution'?'warn':'bad')+'"><span class="lbl">준비도</span>'+overall.toUpperCase()+'</div>';}
  function renderSignal(lane,history){var ts=String((lane||{}).trigger_state||'waiting');var sig=parseFloat((lane||{}).signal_score||0);var trig=parseFloat((lane||{}).trigger_threshold||0.56);var dist=parseFloat((lane||{}).distance_to_trigger||0);var sym=String((lane||{}).symbol||'KRW-BTC');var act=String((lane||{}).action||'watchlist_only');var card=document.getElementById('signal-card');var badge=document.getElementById('signal-badge');card.className='signal-card '+(ts==='ready'?'ready':ts==='arming'?'arming':'');badge.className='signal-badge '+ts;badge.textContent=ts==='ready'?'진입 준비':ts==='arming'?'접근 중':'대기';document.getElementById('signal-sym').textContent=sym+' · '+actionKo(act);var pct=trig>0?Math.min(sig/trig*100,100):0;var fill=document.getElementById('gauge-fill');fill.style.width=pct.toFixed(1)+'%';fill.className='gauge-fill '+(ts==='ready'?'ready':ts==='arming'?'arming':'');document.getElementById('gauge-cur').textContent='현재 '+sig.toFixed(2);document.getElementById('gauge-trig').textContent='진입 '+trig.toFixed(2);document.getElementById('signal-meta').textContent=ts==='ready'?'진입 조건 충족 — 파일럿 주문 실행 중':ts==='arming'?'진입까지 거리 '+dist.toFixed(2)+' — 모니터링 강화':'진입까지 거리 '+dist.toFixed(2)+' (필요: '+trig.toFixed(2)+')';var chips=(history||[]).slice(-4).map(function(r){var rs=parseFloat(r.signal_score||0),rt=parseFloat(r.trigger_threshold||0);return '<span class="trend-chip">'+(r.time||'--:--')+' '+rs.toFixed(2)+'</span>';});document.getElementById('trend-mini').innerHTML=chips.join('');}
  function renderDesks(desks){var map=[['crypto','dk-crypto'],['korea','dk-korea'],['us','dk-us']];var dg=document.getElementById('desk-grid');var cards=dg.querySelectorAll('.desk-card');map.forEach(function(m,i){var key=m[0],pfx=m[1],item=(desks||{})[key]||{};var cls=actionCls(item.action);cards[i].className='desk-card '+(cls==='watch'?'':''+cls);document.getElementById(pfx+'-act').textContent=actionKo(item.action);document.getElementById(pfx+'-act').className='desk-action '+cls;document.getElementById(pfx+'-focus').textContent=item.focus||'신호 없음';var sizeEl=document.getElementById(pfx+'-size');sizeEl.textContent=item.size||'0.00x';sizeEl.className='desk-size'+(item.size&&item.size!=='0.00x'?' active':'');var qfill=document.getElementById(pfx+'-qfill');if(qfill){var qs=parseFloat(item.quality_score||0),qt=parseFloat(item.quality_threshold||0.58);var qpct=qt>0?Math.min(qs/qt*100,100):0;qfill.style.width=qpct.toFixed(1)+'%';qfill.className='mini-gauge-fill'+(qpct>=100?' ready':qpct>=70?' arming':'');var qvalEl=document.getElementById(pfx+'-qval');var qthrEl=document.getElementById(pfx+'-qthr');if(qvalEl)qvalEl.textContent='품질 '+qs.toFixed(2);if(qthrEl)qthrEl.textContent='기준 '+qt.toFixed(2);}
      // Breakout badge for Korea / Crypto desks
      var bkEl=document.getElementById(pfx+'-bk');if(bkEl){var bkC=parseInt(item.breakout_confirmed_count||0),bkP=parseInt(item.breakout_partial_count||0);if(bkC>0){bkEl.textContent='BK '+bkC;bkEl.className='desk-bk-badge full';bkEl.style.display='inline-block';}else if(bkP>0){bkEl.textContent='BK ~'+bkP;bkEl.className='desk-bk-badge partial';bkEl.style.display='inline-block';}else{bkEl.style.display='none';}}
    });}
  function renderOrderBar(exec){var pending=parseInt(exec.pending_count||0),partial=parseInt(exec.partial_count||0),stale=parseInt(exec.stale_count||0),live=parseInt(exec.live_count||0);var items=[];if(stale>0)items.push('<div class="o-badge bad"><span class="num">'+stale+'</span> 미처리 주문</div>');if(partial>0)items.push('<div class="o-badge warn"><span class="num">'+partial+'</span> 부분 체결</div>');if(pending>0)items.push('<div class="o-badge warn"><span class="num">'+pending+'</span> 대기 중</div>');if(live>0&&!pending&&!partial&&!stale)items.push('<div class="o-badge ok"><span class="num">'+live+'</span> 실전 주문 정상</div>');var bar=document.getElementById('order-bar');if(items.length){bar.innerHTML=items.join('');bar.style.display='flex';}else{bar.style.display='none';}}
  function renderPositions(pos){var cnt=document.getElementById('pos-count'),body=document.getElementById('positions-body');if(!pos||!pos.length){cnt.textContent='0';body.innerHTML='<div class="empty-row">보유 포지션 없음</div>';return;}cnt.textContent=String(pos.length);var rows=pos.map(function(p){var pnl=parseFloat(p.unrealized_pnl_pct||0);return '<tr><td class="sym">'+(p.symbol||'--')+'</td><td><span class="desk-tag">'+(p.desk||'--')+'</span></td><td>'+Number(p.entry_price||0).toLocaleString('ko-KR')+'</td><td class="'+(pnl>0?'pos':pnl<0?'neg':'')+'" style="font-weight:700">'+fmtPct(pnl)+'</td><td style="color:var(--muted);font-size:.78rem">'+toKST(p.opened_at||'')+'</td></tr>';}).join('');body.innerHTML='<table class="pos-table"><thead><tr><th>종목</th><th>데스크</th><th>진입가</th><th>미실현</th><th>시각</th></tr></thead><tbody>'+rows+'</tbody></table>';}
  function renderTrades(closed){var body=document.getElementById('trades-body');var items=(closed||[]).slice(0,15);if(!items.length){body.innerHTML='<div class="empty-row">청산 내역 없음</div>';return;}var rows=items.map(function(t){var pnl=parseFloat(t.pnl_pct||0);return '<tr><td class="sym">'+(t.symbol||'--')+'</td><td><span class="desk-tag">'+(t.desk||'--')+'</span></td><td class="'+(pnl>0?'pos':pnl<0?'neg':'')+'" style="font-weight:700">'+fmtPct(pnl)+'</td><td style="color:var(--muted);font-size:.78rem">'+(t.closed_reason||'--')+'</td><td style="color:var(--muted);font-size:.78rem">'+toKST(t.closed_at||'')+'</td></tr>';}).join('');body.innerHTML='<table class="pos-table"><thead><tr><th>종목</th><th>데스크</th><th>손익</th><th>사유</th><th>시각</th></tr></thead><tbody>'+rows+'</tbody></table>';}
  function renderEquity(points){var svg=document.getElementById('equity-svg');if(!points||points.length<2){svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" fill="#7d8590" font-size="12">수익 데이터 없음</text>';document.getElementById('equity-label').textContent='';return;}var w=400,h=140,pad=20,vals=points.map(function(p){return Number(p.equity||0);}),mn=Math.min.apply(null,vals),mx=Math.max.apply(null,vals),rng=mx-mn||1;var last=vals[vals.length-1],color=last>=100?'#3fb950':'#f85149';document.getElementById('equity-label').textContent=(last>=100?'+':'')+((last-100).toFixed(2))+'%';var xi=function(i){return pad+(i/(points.length-1))*(w-pad*2);};var yi=function(v){return pad+((mx-v)/rng)*(h-pad*2);};var pts=points.map(function(p,i){return xi(i).toFixed(1)+','+yi(Number(p.equity||0)).toFixed(1);}).join(' ');var fill=xi(0).toFixed(1)+','+h+' '+pts+' '+xi(points.length-1).toFixed(1)+','+h;svg.innerHTML='<defs><linearGradient id="eg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="'+color+'" stop-opacity=".25"/><stop offset="100%" stop-color="'+color+'" stop-opacity="0"/></linearGradient></defs><polygon points="'+fill+'" fill="url(#eg)"/><polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="'+xi(points.length-1).toFixed(1)+'" cy="'+yi(last).toFixed(1)+'" r="4" fill="'+color+'"/>';}
  function renderAgentLog(log){
    var body=document.getElementById('agent-log-body');
    var countEl=document.getElementById('agent-log-count');
    if(!log||!log.length){body.innerHTML='<div class="empty-row">\ud310\ub2e8 \uc774\ub825 \uc5c6\uc74c</div>';countEl.textContent='0';return;}
    countEl.textContent=log.length+'\uc0ac\uc774\ud074';
    var html='';
    log.forEach(function(cycle,idx){
      var isLatest=idx===0;
      var st=String(cycle.stance||'').toUpperCase();
      var stanceCls=st.indexOf('OFFENSE')>=0?'offense':st.indexOf('DEFENSE')>=0?'defense':'neutral';
      var deskHtml='';
      (cycle.desks||[]).forEach(function(d){
        var actCls=actionCls(d.action);
        var isActionable=['probe_longs','selective_probe','attack_opening_drive'].indexOf(d.action)>=0;
        var isIdle=d.status==='idle';
        if(isActionable&&isIdle)actCls='wanted';
        var sym=d.symbol?'\u00b7 '+String(d.symbol).replace('KRW-',''):'';
        var sz=(isActionable&&!isIdle&&d.size&&d.size!=='0.00x')?' '+d.size:'';
        var notes=(d.notes||[]).slice(0,2).join(' / ');
        var noteHtml=notes?'<div class="desk-row-note">'+notes+'</div>':'';
        var deskLabel=d.desk==='crypto'?'\ucf54\uc778':d.desk==='korea'?'\ud55c\uad6d':'\ubbf8\uad6d';
        deskHtml+='<div class="desk-row"><span class="desk-row-tag">'+deskLabel+'</span><div class="desk-row-body"><span class="desk-row-act '+actCls+'">'+actionKo(d.action)+(sym?' '+sym:'')+(sz?' '+sz:'')+'</span>'+noteHtml+'</div></div>';
      });
      var sigHtml='';
      if(cycle.signals&&cycle.signals.length){sigHtml='<div class="cycle-signals">'+cycle.signals.slice(0,3).join(' \u00b7 ')+'</div>';}
      html+='<div class="cycle-entry'+(isLatest?' latest':'')+'"><div class="cycle-header"><span class="cycle-time">'+toKSTFull(cycle.run_at)+'</span><span class="cycle-badge '+stanceCls+'">'+String(cycle.stance||'--')+'</span><span class="cycle-badge neutral">'+String(cycle.regime||'--')+'</span></div>'+(deskHtml?'<div class="desk-rows">'+deskHtml+'</div>':'')+sigHtml+'</div>';
    });
    body.innerHTML=html;
  }
  function renderBroker(health,readiness){var bhtml='';['upbit','kis'].forEach(function(key){var item=(health||{})[key]||{};var ok=item.balances_ok,cfg=item.configured!==false;bhtml+='<div class="broker-row"><div><div class="broker-name">'+key.toUpperCase()+'</div><div class="'+(cfg?ok?'broker-ok':'broker-warn':'broker-muted')+'">'+(cfg?ok?'잔고 확인':'잔고 오류':'미설정')+'</div></div><div class="status-tag '+(cfg?ok?'pass':'warn':'')+'">'+( cfg?ok?'연결':'점검':'미설정')+'</div></div>';});var rhtml='';((readiness||{}).checklist||[]).slice(0,6).forEach(function(item){var st=item.status||'warn';rhtml+='<div class="check-row"><div><div class="check-lbl">'+(item.label||'--')+'</div><div class="check-det">'+(item.detail||'')+'</div></div><div class="status-tag '+st+'">'+(st==='pass'?'통과':st==='block'?'차단':'주의')+'</div></div>';});document.getElementById('broker-rows').innerHTML=bhtml;document.getElementById('readiness-rows').innerHTML=rhtml||'<div style="color:var(--muted);font-size:.82rem;padding:8px 0">준비도 데이터 없음</div>';}
  async function loadData(){try{var dr=await fetch('/dashboard-data'),hr=await fetch('/health');var data=await dr.json(),health=await hr.json();var state=data.state||{},dash=data.dashboard||{},readiness=data.live_readiness_checklist||{},brokerH=data.broker_live_health||{},exec=(dash.execution_summary||{}),perf=(dash.performance||{}),cap=(dash.capital||{}),blockSummary=((dash.exposure||{}).entry_block_summary)||((readiness||{}).entry_block_summary)||{};var isLive=String(readiness.execution_mode||'').indexOf('live')>=0;var dot=document.getElementById('status-dot');dot.className='status-dot'+(blockSummary.blocked?' err':isLive?' ':'');var modeEl=document.getElementById('mode-tag');modeEl.textContent=String(readiness.execution_mode||'모의투자');modeEl.className='mode-tag'+(isLive?' live':'');document.getElementById('update-time').textContent=toKST(state.updated_at);if(blockSummary&&blockSummary.blocked){var ab=document.getElementById('alert-bar');ab.textContent='\\u26a0\\ufe0f '+String(blockSummary.detail||blockSummary.headline||'실행 차단');ab.className='alert-bar visible';}else{document.getElementById('alert-bar').className='alert-bar';}renderPnl(perf,cap);renderStatusBar(state,readiness,blockSummary);renderSignal(dash.crypto_live_lane||null,dash.crypto_live_lane_history||[]);window.__deskDrilldown=dash.desk_drilldown||{};renderDesks(dash.desk_status||{});renderOrderBar(exec);renderPositions(dash.open_positions||[]);renderTrades(dash.closed_positions||[]);renderEquity(dash.equity_curve||[]);renderBroker(brokerH,readiness);renderAgentLog(dash.agent_log||[]);}catch(e){var dot2=document.getElementById('status-dot');dot2.className='status-dot err';document.getElementById('alert-bar').textContent='\\u26a0\\ufe0f 데이터 로딩 실패: '+e.message;document.getElementById('alert-bar').className='alert-bar visible';}}
  async function runCycle(){var btn=document.getElementById('cycle-btn');btn.disabled=true;btn.textContent='실행 중...';try{await fetch('/cycle',{method:'POST'});await loadData();}catch(e){console.error(e);}finally{btn.disabled=false;btn.textContent='사이클 실행';}}
  setInterval(function(){loadData().catch(function(){});},20000);loadData().catch(function(){});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return _embedded_dashboard_html()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)

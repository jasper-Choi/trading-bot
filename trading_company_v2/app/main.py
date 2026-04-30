from __future__ import annotations

import hashlib
import secrets
import time
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
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
    load_paper_closed_positions,
    load_performance_analytics,
    load_performance_quick_stats,
    load_recent_journal,
)
from app.notifier import notifier
from app.orchestrator import CompanyOrchestrator
from app.services.kis_broker import get_account_positions as get_kis_account_positions
from app.services.kis_broker import get_order as get_kis_order
from app.services.kis_broker import normalize_order_state as normalize_kis_order_state
from app.services.broker_router import normalize_execution_mode
from app.services.market_gateway import get_naver_daily_prices, get_upbit_15m_candles, get_upbit_ticker_prices, get_us_daily_prices, get_us_data_status
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
_SCANNER_CHART_CACHE: dict[str, dict] = {}
_SCANNER_CHART_TTL_SECONDS = 75.0
_SCANNER_CHART_COUNT = 24


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
    active_desks = set((state.strategy_book or {}).get("active_desks") or settings.active_desk_set)
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
            "disabled": "crypto" not in active_desks,
            "bias": crypto_view.get("desk_bias", "n/a"),
            "action": crypto_plan.get("action", "n/a"),
            "focus": crypto_plan.get("focus", "크립토 플랜 없음"),
            "size": crypto_plan.get("size", "0.00x"),
            "leaders": (state.market_snapshot.get("crypto_leaders", []) if state.market_snapshot else [])[:3],
            "latest_order": latest_crypto_order,
        },
        "korea": {
            "title": "한국주식 데스크",
            "disabled": "korea" not in active_desks,
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
            "disabled": "us" not in active_desks,
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
                        "discovery_score": float(mdata.get("discovery_score", 0.0) or 0.0),
                        "change_rate": float(mdata.get("change_rate", 0.0) or 0.0),
                        "volume_24h_krw": int(float(mdata.get("volume_24h_krw", 0.0) or 0.0)),
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
        # All-time cumulative metrics
        "cumulative_realized_pnl_pct": float(state.daily_summary.get("cumulative_realized_pnl_pct", 0.0) or 0.0),
        "cumulative_closed_positions": int(state.daily_summary.get("cumulative_closed_positions", 0) or 0),
        "cumulative_wins": int(state.daily_summary.get("cumulative_wins", 0) or 0),
        "cumulative_losses": int(state.daily_summary.get("cumulative_losses", 0) or 0),
        "cumulative_win_rate": float(state.daily_summary.get("cumulative_win_rate", 0.0) or 0.0),
    }


def _build_capital_payload(state: CompanyState) -> dict:
    base = float(settings.paper_capital_krw)
    # Today's P&L (for daily display)
    realized_pct = float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0)
    unrealized_pct = float(state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0)
    # Cumulative (all-time) — compounding base
    cumulative_pct = float(state.daily_summary.get("cumulative_realized_pnl_pct", 0.0) or 0.0)
    effective_capital = round(base * (1 + cumulative_pct / 100))
    realized_krw = round(effective_capital * realized_pct / 100)
    unrealized_krw = round(effective_capital * unrealized_pct / 100)
    total_krw = round(effective_capital + unrealized_krw)
    return {
        "base_krw": int(base),
        "effective_capital_krw": int(effective_capital),
        "cumulative_realized_pnl_pct": round(cumulative_pct, 2),
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
        closed_positions=load_paper_closed_positions(limit=12),
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


def _action_label(action: str) -> str:
    labels = {
        "probe_longs": "진입 탐색",
        "selective_probe": "선별 진입",
        "attack_opening_drive": "공격 진입",
        "reduce_risk": "리스크 축소",
        "capital_preservation": "자본 보존",
        "watchlist_only": "관찰 대기",
        "stand_by": "대기",
        "pre_market_watch": "장외 대기",
    }
    return labels.get(str(action or ""), str(action or "대기"))


def _desk_label(desk: str) -> str:
    return {"crypto": "코인", "korea": "한국주식", "us": "미국주식"}.get(str(desk or ""), str(desk or "데스크"))


def _briefing_tone(value: float) -> str:
    if value > 0.25:
        return "positive"
    if value < -0.25:
        return "negative"
    return "neutral"


def _build_operator_briefing(state: CompanyState, closed_positions: list[dict]) -> dict:
    """Human-readable control-room summary for non-technical dashboard use."""
    daily = state.daily_summary or {}
    strategy_book = state.strategy_book or {}
    active_desks = set(strategy_book.get("active_desks") or settings.active_desk_set)
    debate = (strategy_book.get("decision_debate", {}) or {}).get("portfolio_manager", {}) or {}
    decisions = [item for item in list(debate.get("decisions") or []) if str(item.get("desk") or "") in active_desks]
    open_positions = [item for item in list(state.open_positions or []) if str(item.get("desk") or "") in active_desks]
    live_rows = [item for item in (state.execution_log or []) if item.get("source") == "live"]
    pending_live = [
        item for item in live_rows
        if str(item.get("status") or "") in {"submitted", "partial"}
        or str(item.get("effect_status") or "") in {"pending", "awaiting_balance_sync", "partial_balance_sync"}
    ]
    mode = str(state.execution_mode or settings.execution_mode or "paper")
    mode_label = "실거래" if "live" in mode else "모의운영"
    open_count = len(open_positions)
    current_cycle_planned = int(daily.get("current_cycle_planned_orders", 0) or 0)
    cumulative_pct = float(daily.get("cumulative_realized_pnl_pct", 0.0) or 0.0)
    unrealized_pct = float(daily.get("unrealized_pnl_pct", 0.0) or 0.0)
    wins = int(daily.get("cumulative_wins", daily.get("wins", 0)) or 0)
    losses = int(daily.get("cumulative_losses", daily.get("losses", 0)) or 0)
    win_rate = float(daily.get("cumulative_win_rate", daily.get("win_rate", 0.0)) or 0.0)
    gross = round(sum(float(item.get("notional_pct", 0.0) or 0.0) for item in open_positions), 2)

    if pending_live:
        headline = f"{mode_label}: 브로커 주문 {len(pending_live)}건 처리 대기 중"
        headline_tone = "warning"
    elif open_count:
        headline = f"{mode_label}: {open_count}개 포지션 보유, 신규 진입은 조건 충족 시에만 실행"
        headline_tone = "active"
    elif current_cycle_planned:
        headline = f"{mode_label}: 이번 사이클 신규 주문 {current_cycle_planned}건 계획"
        headline_tone = "active"
    else:
        headline = f"{mode_label}: 현재 신규 진입 없음, 조건 충족 대기"
        headline_tone = "waiting"

    pnl_tone = _briefing_tone(cumulative_pct + unrealized_pct)
    pnl_message = (
        f"누적 실현 {cumulative_pct:+.2f}%, 현재 미실현 {unrealized_pct:+.2f}% / "
        f"누적 승률 {win_rate:.1f}% ({wins}승 {losses}패)"
    )
    if losses > wins and losses >= 3:
        pnl_message += " - 손실 거래가 우세해서 사이징 축소와 선별 진입이 필요합니다."
    elif cumulative_pct > 0 and win_rate >= 50:
        pnl_message += " - 현재는 수익 우위가 있어 좋은 셋업에서만 증액 가능합니다."

    plan_map = {
        "crypto": strategy_book.get("crypto_plan", {}) or {},
        "korea": strategy_book.get("korea_plan", {}) or {},
        "us": strategy_book.get("us_plan", {}) or {},
    }
    decision_map = {str(item.get("desk") or ""): item for item in decisions}
    desk_messages: list[dict] = []
    for desk in ("crypto", "korea", "us"):
        if desk not in active_desks:
            continue
        plan = plan_map.get(desk, {}) or {}
        decision = decision_map.get(desk, {}) or {}
        action = str(decision.get("action") or plan.get("action") or "stand_by")
        size = str(decision.get("size") or plan.get("size") or "0.00x")
        symbol = str(plan.get("symbol") or "")
        focus = str(plan.get("focus") or "").strip()
        decision_name = str(decision.get("decision") or "review")
        bull_score = decision.get("bull_score")
        bear_score = decision.get("bear_score")
        if action in {"probe_longs", "selective_probe", "attack_opening_drive"} and size != "0.00x":
            state_text = f"{_action_label(action)} 준비"
            tone = "active"
        elif action in {"watchlist_only", "stand_by", "pre_market_watch"}:
            state_text = "보류"
            tone = "waiting"
        else:
            state_text = _action_label(action)
            tone = "warning" if action in {"reduce_risk", "capital_preservation"} else "neutral"
        reason_parts = []
        if focus:
            reason_parts.append(focus)
        if decision_name in {"press", "throttle", "cut", "block"}:
            reason_parts.append(f"PM 판단: {decision_name}")
        if bull_score is not None and bear_score is not None:
            reason_parts.append(f"찬성 {float(bull_score):.2f} / 반대 {float(bear_score):.2f}")
        desk_messages.append(
            {
                "desk": desk,
                "title": _desk_label(desk),
                "tone": tone,
                "state": state_text,
                "action": action,
                "size": size,
                "symbol": symbol,
                "message": f"{symbol + ' - ' if symbol else ''}{' / '.join(reason_parts) if reason_parts else '판단 근거 수집 중'}",
            }
        )

    close_reason_stats = daily.get("close_reason_stats", {}) or {}
    symbol_stats = list(daily.get("symbol_performance_stats", []) or [])
    main_causes: list[str] = []
    if losses and wins == 0:
        main_causes.append(f"현재 누적 {losses}패 0승으로 아직 승리 샘플이 없습니다.")
    if close_reason_stats:
        worst_reason = max(close_reason_stats.items(), key=lambda item: int((item[1] or {}).get("count", 0) or 0))
        main_causes.append(f"가장 많은 청산 사유는 {worst_reason[0]} {int((worst_reason[1] or {}).get('count', 0) or 0)}건입니다.")
    negative_symbols = [
        item for item in symbol_stats
        if float(item.get("pnl_pct", 0.0) or 0.0) < 0
    ][:3]
    if negative_symbols:
        names = ", ".join(f"{item.get('symbol')} {float(item.get('pnl_pct', 0.0) or 0.0):+.2f}%" for item in negative_symbols)
        main_causes.append(f"손실 기여 종목: {names}.")
    if not main_causes:
        main_causes.append("수익 악화 원인은 아직 통계 샘플이 부족합니다. 포지션별 결과를 더 쌓아야 합니다.")

    open_summary = [
        {
            "desk": item.get("desk"),
            "symbol": item.get("symbol"),
            "pnl_pct": float(item.get("unrealized_pnl_pct", item.get("pnl_pct", 0.0)) or 0.0),
            "notional_pct": float(item.get("notional_pct", 0.0) or 0.0),
        }
        for item in open_positions[:6]
    ]

    next_actions = []
    if pnl_tone == "negative":
        next_actions.append("손실 우위 구간이므로 신규 진입은 PM debate에서 찬성 우위가 큰 경우만 허용합니다.")
    if gross >= 0.9:
        next_actions.append(f"총 노출 {gross:.2f}x가 높아 추가 진입보다 기존 포지션 관리가 우선입니다.")
    if any(item.get("tone") == "active" for item in desk_messages):
        next_actions.append("활성 후보는 소액/선별 진입으로만 진행하고, 손실 종목 재진입은 cooldown을 유지합니다.")
    if not next_actions:
        next_actions.append("현재는 확실한 진입 조건 대기 상태입니다. 과열 추격보다 다음 좋은 셋업을 기다립니다.")

    return {
        "headline": headline,
        "headline_tone": headline_tone,
        "mode": mode,
        "mode_label": mode_label,
        "pnl_tone": pnl_tone,
        "pnl_message": pnl_message,
        "desk_messages": desk_messages,
        "loss_causes": main_causes[:4],
        "open_summary": open_summary,
        "next_actions": next_actions[:3],
        "gross_open_notional_pct": gross,
    }


def _build_dashboard_payload(state: CompanyState) -> dict:
    active_desks = set((state.strategy_book or {}).get("active_desks") or settings.active_desk_set)
    closed_positions = [
        item for item in load_paper_closed_positions(limit=40)
        if str(item.get("desk") or "") in active_desks
    ][:20]
    open_positions = [
        item for item in (state.open_positions or [])
        if str(item.get("desk") or "") in active_desks
    ]
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
        "open_positions": open_positions,
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
        "operator_briefing": _build_operator_briefing(state, closed_positions),
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
        closed_positions=load_paper_closed_positions(limit=12),
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


@app.get("/performance-data")
def performance_data() -> dict:
    try:
        state = load_company_state()
        bot_status = {
            "regime": state.regime or "UNKNOWN",
            "stance": state.stance or "NEUTRAL",
            "allow_new_entries": bool(state.allow_new_entries),
            "risk_budget": float(state.risk_budget or 0.0),
            "cycle": int(state.cycle or 0),
        }
    except Exception:
        bot_status = {
            "regime": "UNKNOWN",
            "stance": "NEUTRAL",
            "allow_new_entries": False,
            "risk_budget": 0.0,
            "cycle": 0,
        }
    try:
        analytics = load_performance_analytics()
    except Exception as exc:  # noqa: BLE001
        analytics = {"error": str(exc), "updated_at": None, "summary": {}}
    return {
        "quick_stats": load_performance_quick_stats(),
        "analytics": analytics,
        "bot_status": bot_status,
    }


@app.get("/performance")
def performance(request: Request):
    if request.query_params.get("format") == "json":
        return {
            "stats": load_performance_quick_stats(),
            "open_positions": [p.model_dump() for p in load_open_positions()],
            "closed_positions": load_closed_positions(limit=50),
            "analytics": load_performance_analytics(),
        }
    return HTMLResponse(_performance_html())


def _performance_html() -> str:  # noqa: PLR0915
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0b1020">
  <title>성과 분석</title>
  <style>
    :root{--bg:#08111f;--panel:#111b2d;--panel2:#0d1626;--line:#243047;--text:#e8eefc;--muted:#8d9bb4;--green:#39d98a;--red:#ff6b6b;--blue:#70b7ff;--yellow:#ffd166;--font:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;--mono:'D2Coding','Consolas',monospace}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top left,#183150 0,#08111f 42%,#050914 100%);color:var(--text);font-family:var(--font);line-height:1.5}
    .app{max-width:1200px;margin:0 auto;padding:18px 14px 60px}
    .top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
    .title h1{margin:0;font-size:1.35rem}.title p{margin:4px 0 0;color:var(--muted);font-size:.86rem}
    .nav{display:flex;gap:8px;flex-wrap:wrap}.btn{border:1px solid var(--line);border-radius:10px;padding:9px 12px;color:var(--text);background:rgba(17,27,45,.75);text-decoration:none;font-weight:700;font-size:.84rem}
    /* ── 봇 상태 바 ── */
    .bot-bar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;padding:12px 14px;border:1px solid var(--line);border-radius:14px;background:rgba(13,22,38,.8);align-items:center}
    .bot-bar .lbl{color:var(--muted);font-size:.74rem;margin-right:3px}
    .pill{display:inline-flex;align-items:center;gap:4px;padding:5px 11px;border-radius:8px;border:1px solid var(--line);font-size:.8rem;font-weight:700}
    .pill.ok{color:var(--green);border-color:rgba(57,217,138,.35);background:rgba(57,217,138,.08)}
    .pill.warn{color:var(--yellow);border-color:rgba(255,209,102,.35);background:rgba(255,209,102,.07)}
    .pill.bad{color:var(--red);border-color:rgba(255,107,107,.35);background:rgba(255,107,107,.08)}
    .pill.neu{color:var(--blue);border-color:rgba(112,183,255,.3);background:rgba(112,183,255,.07)}
    .dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0}
    /* ── 메트릭 그리드 ── */
    .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
    .card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(17,27,45,.94),rgba(9,17,31,.96));box-shadow:0 18px 45px rgba(0,0,0,.25);padding:16px}
    .metric-label{color:var(--muted);font-size:.78rem}.metric-value{font-family:var(--mono);font-size:1.6rem;font-weight:900;margin-top:6px}.metric-sub{color:var(--muted);font-size:.78rem;margin-top:4px}
    .green{color:var(--green)}.red{color:var(--red)}.blue{color:var(--blue)}.yellow{color:var(--yellow)}
    .section{margin-top:14px}.section h2{font-size:1rem;margin:0 0 10px}
    .wide{grid-column:span 2}.full{grid-column:1/-1}
    /* ── 에쿼티 커브 ── */
    .equity-wrap{width:100%;height:160px;position:relative}
    #equity-svg{width:100%;height:100%;display:block}
    .equity-zero{stroke:var(--line);stroke-dasharray:4 3}
    /* ── 히트맵 ── */
    .heat-scroll{overflow-x:auto;overflow-y:hidden;padding-bottom:8px}
    .heat{display:grid;grid-template-columns:repeat(24,minmax(82px,1fr));gap:7px;min-width:2050px}
    .heat-cell{border:1px solid var(--line);border-radius:11px;padding:8px 6px;min-height:72px;background:rgba(13,22,38,.82)}
    .heat-hour{font-family:var(--mono);font-size:.72rem;color:var(--muted)}.heat-pnl{font-family:var(--mono);font-weight:900;margin-top:5px}.heat-meta{font-size:.7rem;color:var(--muted)}
    /* ── 바 / 테이블 ── */
    .bars{display:flex;flex-direction:column;gap:9px}
    .bar-row{display:grid;grid-template-columns:100px 1fr 70px;gap:10px;align-items:center;font-size:.82rem}
    .bar-track{height:12px;border-radius:999px;background:#1c2940;overflow:hidden}
    .bar-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--blue),var(--green))}
    .table-wrap{overflow:auto}
    .tbl{width:100%;border-collapse:collapse;font-size:.82rem}
    .tbl th,.tbl td{border-bottom:1px solid rgba(36,48,71,.75);padding:9px 7px;text-align:left;white-space:nowrap}
    .tbl th{color:var(--muted);font-size:.74rem;font-weight:800;cursor:pointer;user-select:none}
    .tbl th:hover{color:var(--text)}.tbl th.sorted{color:var(--blue)}
    .empty{color:var(--muted);padding:16px;border:1px dashed var(--line);border-radius:14px}
    /* ── 스트릭 카드 ── */
    .streak-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
    .streak-card{border:1px solid var(--line);border-radius:14px;padding:14px;background:rgba(13,22,38,.8);text-align:center}
    .streak-num{font-family:var(--mono);font-size:1.8rem;font-weight:900;margin:6px 0 2px}
    .streak-lbl{color:var(--muted);font-size:.76rem}
    /* ── 코인별 테이블 ── */
    .symbol-bar{height:6px;border-radius:999px;display:inline-block;vertical-align:middle}
    /* ── 진입/청산 사유 ── */
    .reason-list{display:flex;flex-direction:column;gap:8px}
    .reason{display:grid;grid-template-columns:minmax(130px,1fr) 70px 70px 80px;gap:8px;align-items:center;border:1px solid rgba(36,48,71,.75);border-radius:12px;padding:10px;background:rgba(13,22,38,.75);font-size:.82rem}
    .reason b{overflow:hidden;text-overflow:ellipsis}
    .mobile-bottom-nav{display:none}
    .mobile-bottom-nav a{color:var(--muted);text-decoration:none;font-size:.74rem;font-weight:900;text-align:center;padding:9px 6px;border-radius:12px;border:1px solid transparent}
    .mobile-bottom-nav a.active{color:var(--blue);background:rgba(112,183,255,.10);border-color:rgba(112,183,255,.3)}
    @media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.wide{grid-column:1/-1}.streak-grid{grid-template-columns:repeat(2,1fr)}.top{align-items:flex-start;flex-direction:column}.nav{width:100%}.btn{flex:1;text-align:center}}
    @media(max-width:700px){.mobile-bottom-nav{position:fixed;left:10px;right:10px;bottom:10px;z-index:50;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:7px;border:1px solid var(--line);border-radius:18px;background:rgba(8,17,31,.96);box-shadow:0 14px 38px rgba(0,0,0,.45);backdrop-filter:blur(10px)}}
    @media(max-width:560px){.grid{grid-template-columns:1fr}.metric-value{font-size:1.35rem}.reason{grid-template-columns:1fr 54px 58px 64px}.bar-row{grid-template-columns:80px 1fr 56px}.app{padding:14px 10px 96px}.streak-grid{grid-template-columns:repeat(2,1fr)}}
  </style>
</head>
<body>
  <main class="app">
    <header class="top">
      <div class="title">
        <h1>성과 분석</h1>
        <p id="subtitle">거래 결과를 수익 극대화 관점으로 다시 읽는 페이지</p>
      </div>
      <nav class="nav">
        <a class="btn" href="/">대시보드</a>
        <a class="btn" href="/scanner">스캐너</a>
        <a class="btn" href="/performance?format=json">JSON</a>
      </nav>
    </header>

    <nav class="mobile-bottom-nav" aria-label="Mobile navigation">
      <a href="/">Dashboard</a>
      <a href="/scanner">Scanner</a>
      <a class="active" href="/performance">Performance</a>
    </nav>

    <!-- 봇 상태 바 -->
    <div class="bot-bar" id="bot-bar">
      <span style="color:var(--muted);font-size:.8rem;font-weight:700">봇 상태</span>
      <span class="pill neu" id="pill-regime"><span class="dot"></span> --</span>
      <span class="pill neu" id="pill-stance"><span class="dot"></span> --</span>
      <span class="pill neu" id="pill-entries"><span class="dot"></span> --</span>
      <span class="pill neu" id="pill-budget"><span class="dot"></span> --</span>
      <span style="margin-left:auto;color:var(--muted);font-family:var(--mono);font-size:.74rem" id="cycle-label">cycle --</span>
    </div>

    <!-- 핵심 메트릭 8종 -->
    <section class="grid" id="metrics"></section>

    <!-- 에쿼티 커브 + 스트릭 -->
    <section class="grid section">
      <div class="card" style="grid-column:span 3">
        <h2>에쿼티 커브 <span style="font-weight:400;color:var(--muted);font-size:.8rem">(일별 누적 PnL)</span></h2>
        <div class="equity-wrap"><svg id="equity-svg" preserveAspectRatio="none"></svg></div>
      </div>
      <div class="card" style="grid-column:span 1">
        <h2>연속 손익 스트릭</h2>
        <div class="streak-grid" id="streaks" style="grid-template-columns:1fr 1fr;margin-top:4px"></div>
      </div>
    </section>

    <!-- 시간대 히트맵 + 일별 성과 -->
    <section class="grid section">
      <div class="card full">
        <h2>시간대별 히트맵</h2>
        <div class="heat-scroll"><div class="heat" id="heatmap"></div></div>
        <h2 style="margin-top:18px">일자별 거래 종합 성과</h2>
        <div class="table-wrap" id="daily-table"></div>
      </div>
      <div class="card full"><h2>PnL 분포</h2><div class="bars" id="distribution"></div></div>
    </section>

    <!-- 코인별 성과 -->
    <section class="grid section">
      <div class="card full">
        <h2>코인별 성과 분석 <span style="font-weight:400;color:var(--muted);font-size:.8rem">(클릭으로 정렬)</span></h2>
        <div class="table-wrap" id="symbol-table"></div>
      </div>
    </section>

    <!-- 진입/청산 사유 -->
    <section class="grid section">
      <div class="card wide"><h2>진입 사유별 승률</h2><div class="reason-list" id="entry-reasons"></div></div>
      <div class="card wide"><h2>청산 사유별 손익</h2><div class="reason-list" id="exit-reasons"></div></div>
    </section>

    <!-- 포지션 테이블 -->
    <section class="grid section">
      <div class="card wide"><h2>오픈 포지션</h2><div class="table-wrap" id="open-table"></div></div>
      <div class="card wide"><h2>최근 청산 거래</h2><div class="table-wrap" id="closed-table"></div></div>
    </section>
  </main>
<script>
/* ── 유틸 ── */
function n(v,d){v=Number(v);return Number.isFinite(v)?v:(d??0)}
function cls(v){return n(v)>=0?'green':'red'}
function pct(v){return (n(v)>0?'+':'')+n(v).toFixed(2)+'%'}
function krw(v){var sign=n(v)>0?'+':'';return sign+Math.round(n(v)).toLocaleString('ko-KR')+'원'}
function kst(iso){try{return new Date(iso).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}catch(e){return iso||'--'}}
function metric(label,value,sub,color){return '<div class="card"><div class="metric-label">'+label+'</div><div class="metric-value '+(color||'')+'">'+value+'</div><div class="metric-sub">'+(sub||'')+'</div></div>'}
function table(headers,rows,sortFn){
  if(!rows.length)return '<div class="empty">표시할 데이터 없음</div>';
  return '<table class="tbl"><thead><tr>'+headers.map(function(h,i){return '<th onclick="'+( sortFn?'sortTable(this,'+i+')':'' )+'" '+(sortFn?'':'')+'>'+(typeof h==='object'?h.label:h)+'</th>'}).join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table>';
}

/* ── 봇 상태 바 ── */
function renderBotStatus(bs){
  if(!bs)return;
  var regime=bs.regime||'?';
  var stance=bs.stance||'?';
  var allow=bs.allow_new_entries;
  var budget=n(bs.risk_budget);
  var regimeCls=regime==='TRENDING'?'ok':regime==='RANGING'?'neu':regime==='STRESSED'?'bad':'warn';
  var stanceCls=stance==='OFFENSE'?'ok':stance==='DEFENSE'?'bad':'neu';
  var entryCls=allow?'ok':'bad';
  var budgetCls=budget>=0.4?'ok':budget>=0.25?'warn':'bad';
  document.getElementById('pill-regime').className='pill '+regimeCls;
  document.getElementById('pill-regime').innerHTML='<span class="dot"></span> 레짐 '+regime;
  document.getElementById('pill-stance').className='pill '+stanceCls;
  document.getElementById('pill-stance').innerHTML='<span class="dot"></span> 스탠스 '+stance;
  document.getElementById('pill-entries').className='pill '+entryCls;
  document.getElementById('pill-entries').innerHTML='<span class="dot"></span> '+(allow?'진입 허용':'진입 차단');
  document.getElementById('pill-budget').className='pill '+budgetCls;
  document.getElementById('pill-budget').innerHTML='<span class="dot"></span> 리스크 '+n(budget*100).toFixed(0)+'%';
  document.getElementById('cycle-label').textContent='cycle '+n(bs.cycle);
}

/* ── 핵심 메트릭 ── */
function renderMetrics(a){
  var s=a.summary||{},t=s.today||{};
  document.getElementById('metrics').innerHTML=[
    metric('누적 PnL',pct(s.total_pnl_pct),krw(s.total_pnl_krw)+' · '+s.trades+'건',cls(s.total_pnl_pct)),
    metric('승률',n(s.win_rate).toFixed(1)+'%',s.wins+'승 / '+s.losses+'패','blue'),
    metric('평균 기대값',pct(s.avg_pnl_pct),'평균 보유 '+n(s.avg_hold_min).toFixed(1)+'분',cls(s.avg_pnl_pct)),
    metric('오늘 PnL',pct(t.total_pnl_pct),krw(t.total_pnl_krw)+' · '+t.trades+'건',cls(t.total_pnl_pct)),
    metric('최대 낙폭',pct(s.max_drawdown_pct),'샘플 '+s.sample_size+'건','yellow'),
    metric('오픈 포지션',String(s.open_positions||0),'현재 보유 중','blue'),
    metric('최고 거래',pct(s.best_pnl_pct),'최근 표본 기준','green'),
    metric('최악 거래',pct(s.worst_pnl_pct),'손실 원인 점검 필요','red')
  ].join('');
}

/* ── 에쿼티 커브 SVG ── */
function renderEquity(a){
  var curve=a.equity_curve||[];
  var svg=document.getElementById('equity-svg');
  if(!curve.length){svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" fill="#8d9bb4" font-size="13">거래 데이터 없음</text>';return;}
  var vals=curve.map(function(x){return n(x.cumulative_pnl_pct)});
  var minV=Math.min(0,...vals),maxV=Math.max(0,...vals);
  var range=Math.max(maxV-minV,0.5);
  var W=800,H=150,pad=8;
  var xScale=function(i){return pad+(W-pad*2)*i/(Math.max(curve.length-1,1))};
  var yScale=function(v){return H-pad-(v-minV)/range*(H-pad*2)};
  var zero=yScale(0);
  // Build fill area
  var pts=curve.map(function(x,i){return xScale(i)+','+yScale(n(x.cumulative_pnl_pct))});
  var pathD='M'+pts.join(' L');
  var areaD=pathD+' L'+(W-pad)+','+zero+' L'+pad+','+zero+' Z';
  // Final value color
  var finalVal=vals[vals.length-1];
  var lineCol=finalVal>=0?'#39d98a':'#ff6b6b';
  var fillCol=finalVal>=0?'rgba(57,217,138,0.15)':'rgba(255,107,107,0.12)';
  // Tick labels (first, middle, last dates)
  var ticks='';
  if(curve.length>1){
    [[0,curve[0].date],[Math.floor((curve.length-1)/2),curve[Math.floor((curve.length-1)/2)].date],[curve.length-1,curve[curve.length-1].date]].forEach(function(tk){
      var xi=xScale(tk[0]);
      ticks+='<text x="'+xi+'" y="'+(H-1)+'" text-anchor="middle" font-size="9" fill="#8d9bb4">'+String(tk[1]).slice(5)+'</text>';
    });
  }
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.innerHTML='<defs><linearGradient id="eg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="'+lineCol+'" stop-opacity=".35"/><stop offset="100%" stop-color="'+lineCol+'" stop-opacity=".02"/></linearGradient></defs>'
    +'<line x1="'+pad+'" y1="'+zero+'" x2="'+(W-pad)+'" y2="'+zero+'" stroke="#243047" stroke-dasharray="4 3" stroke-width="1"/>'
    +'<path d="'+areaD+'" fill="url(#eg)"/>'
    +'<path d="'+pathD+'" fill="none" stroke="'+lineCol+'" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
    +'<circle cx="'+xScale(curve.length-1)+'" cy="'+yScale(finalVal)+'" r="4" fill="'+lineCol+'"/>'
    +'<text x="'+(xScale(curve.length-1)+7)+'" y="'+(yScale(finalVal)+4)+'" font-size="10" font-weight="700" fill="'+lineCol+'">'+(finalVal>=0?'+':'')+finalVal.toFixed(2)+'%</text>'
    +ticks;
}

/* ── 스트릭 ── */
function renderStreaks(a){
  var si=a.streak_info||{};
  var cur=n(si.current_streak),ct=si.current_type||'none';
  var isWin=ct==='win';
  var streakCls=ct==='none'?'':'streak-card '+(isWin?'':'');
  var items=[
    {lbl:'현재 스트릭',val:(ct==='none'?'--':(isWin?'🟢':'🔴')+' '+cur+'연속'),cls:ct==='none'?'':'metric-value '+(isWin?'green':'red')},
    {lbl:'최장 연승',val:n(si.longest_win_streak)+'연승',cls:'metric-value green'},
    {lbl:'최장 연패',val:n(si.longest_loss_streak)+'연패',cls:'metric-value red'},
    {lbl:'유형',val:ct==='win'?'연승 중':ct==='loss'?'연패 중':'없음',cls:'metric-value blue'},
  ];
  document.getElementById('streaks').innerHTML=items.map(function(it){
    return '<div class="streak-card"><div class="streak-lbl">'+it.lbl+'</div><div class="'+it.cls+'" style="font-family:var(--mono);font-size:1.3rem;font-weight:900;margin:6px 0 2px">'+it.val+'</div></div>';
  }).join('');
}

/* ── 히트맵 ── */
function renderHeat(a){
  document.getElementById('heatmap').innerHTML=(a.hourly_heatmap||[]).map(function(x){
    var p=n(x.total_pnl_pct),o=Math.min(.95,Math.max(.08,Math.abs(p)/4));
    var bg=p>=0?'rgba(57,217,138,'+o+')':'rgba(255,107,107,'+o+')';
    return '<div class="heat-cell" style="background:'+bg+'"><div class="heat-hour">'+x.label+'</div><div class="heat-pnl '+cls(p)+'">'+pct(p)+'</div><div class="heat-meta">'+x.trades+'건 · '+n(x.win_rate).toFixed(0)+'%</div></div>';
  }).join('');
}

/* ── 일별 테이블 ── */
function renderDaily(a){
  var rows=(a.daily_performance||[]).slice().sort(function(a,b){return String(b.label).localeCompare(String(a.label))}).slice(0,14);
  document.getElementById('daily-table').innerHTML=table(['일자','거래','승률','PnL','금액','평균','보유'],rows.map(function(x){
    return '<tr><td>'+x.label+'</td><td>'+x.trades+'건</td><td class="blue">'+n(x.win_rate).toFixed(1)+'%</td><td class="'+cls(x.total_pnl_pct)+'">'+pct(x.total_pnl_pct)+'</td><td class="'+cls(x.total_pnl_krw)+'">'+krw(x.total_pnl_krw)+'</td><td class="'+cls(x.avg_pnl_pct)+'">'+pct(x.avg_pnl_pct)+'</td><td>'+n(x.avg_hold_min).toFixed(1)+'분</td></tr>';
  }));
}

/* ── PnL 분포 ── */
function renderBars(a){
  var rows=a.pnl_distribution||[],max=Math.max(1,...rows.map(function(x){return n(x.trades)}));
  document.getElementById('distribution').innerHTML=rows.map(function(x){
    var w=Math.max(3,n(x.trades)/max*100);
    return '<div class="bar-row"><span>'+x.label+'</span><div class="bar-track"><div class="bar-fill" style="width:'+w+'%"></div></div><b>'+x.trades+'건</b></div>';
  }).join('')||'<div class="empty">분포 데이터 없음</div>';
}

/* ── 코인별 성과 테이블 (sortable) ── */
var _symData=[];
var _symSort={col:3,asc:false}; // default: sort by total_pnl_pct desc
function renderSymbolTable(a){
  _symData=(a.symbol_stats||[]);
  _drawSymbolTable();
}
function _drawSymbolTable(){
  if(!_symData.length){document.getElementById('symbol-table').innerHTML='<div class="empty">거래 데이터 없음</div>';return;}
  var maxAbs=Math.max(1,..._symData.map(function(x){return Math.abs(n(x.total_pnl_pct))}));
  var cols=['코인','거래','승률','누적 PnL','평균 PnL','평균 보유','최고','최악'];
  var sorted=_symData.slice().sort(function(a,b){
    var vals=[
      [a.label,b.label],[n(a.trades),n(b.trades)],[n(a.win_rate),n(b.win_rate)],
      [n(a.total_pnl_pct),n(b.total_pnl_pct)],[n(a.avg_pnl_pct),n(b.avg_pnl_pct)],
      [n(a.avg_hold_min),n(b.avg_hold_min)],[n(a.best_pnl_pct),n(b.best_pnl_pct)],[n(a.worst_pnl_pct),n(b.worst_pnl_pct)]
    ];
    var pair=vals[_symSort.col];
    var r=typeof pair[0]==='string'?pair[0].localeCompare(pair[1]):pair[0]-pair[1];
    return _symSort.asc?r:-r;
  });
  var rows=sorted.map(function(x){
    var barW=Math.min(60,Math.max(4,Math.abs(n(x.total_pnl_pct))/maxAbs*60));
    var barCol=n(x.total_pnl_pct)>=0?'#39d98a':'#ff6b6b';
    var coin=(x.label||'').replace('KRW-','');
    return '<tr>'
      +'<td><b>'+coin+'</b></td>'
      +'<td>'+n(x.trades)+'건</td>'
      +'<td class="blue">'+n(x.win_rate).toFixed(1)+'%</td>'
      +'<td class="'+cls(x.total_pnl_pct)+'"><span class="symbol-bar" style="width:'+barW+'px;background:'+barCol+';margin-right:6px"></span>'+pct(x.total_pnl_pct)+'</td>'
      +'<td class="'+cls(x.avg_pnl_pct)+'">'+pct(x.avg_pnl_pct)+'</td>'
      +'<td>'+n(x.avg_hold_min).toFixed(1)+'분</td>'
      +'<td class="green">'+pct(x.best_pnl_pct)+'</td>'
      +'<td class="red">'+pct(x.worst_pnl_pct)+'</td>'
      +'</tr>';
  });
  document.getElementById('symbol-table').innerHTML=table(cols,rows,true);
  // Mark sorted column header
  var ths=document.querySelectorAll('#symbol-table .tbl th');
  ths.forEach(function(th,i){th.classList.toggle('sorted',i===_symSort.col);th.textContent=cols[i]+(_symSort.col===i?(_symSort.asc?' ↑':' ↓'):'');});
}
function sortTable(th,colIdx){
  if(_symSort.col===colIdx){_symSort.asc=!_symSort.asc;}else{_symSort.col=colIdx;_symSort.asc=false;}
  _drawSymbolTable();
}

/* ── 사유별 리스트 ── */
function renderReasons(id,rows){
  document.getElementById(id).innerHTML=(rows||[]).slice(0,10).map(function(x){
    return '<div class="reason"><b title="'+x.label+'">'+x.label+'</b><span>'+x.trades+'건</span><span class="blue">'+n(x.win_rate).toFixed(1)+'%</span><span class="'+cls(x.total_pnl_pct)+'">'+pct(x.total_pnl_pct)+'</span></div>';
  }).join('')||'<div class="empty">집계 데이터 없음</div>';
}

/* ── 포지션 테이블 ── */
function renderTables(a){
  document.getElementById('open-table').innerHTML=table(['종목','진입','보유','PnL','Peak'],(a.open_positions||[]).map(function(x){
    return '<tr><td><b>'+(x.symbol||'').replace('KRW-','')+'</b></td><td>'+x.action+'</td><td>'+x.holding_minutes+'분</td><td class="'+cls(x.pnl_pct)+'">'+pct(x.pnl_pct)+'</td><td>'+pct(x.peak_pnl_pct)+'</td></tr>';
  }));
  document.getElementById('closed-table').innerHTML=table(['종목','청산','보유','PnL','금액','시간'],(a.recent_closed||[]).slice(0,20).map(function(x){
    return '<tr><td><b>'+(x.symbol||'').replace('KRW-','')+'</b></td><td>'+x.closed_reason+'</td><td>'+x.holding_minutes+'분</td><td class="'+cls(x.pnl_pct)+'">'+pct(x.pnl_pct)+'</td><td class="'+cls(x.pnl_krw)+'">'+krw(x.pnl_krw)+'</td><td>'+kst(x.closed_at)+'</td></tr>';
  }));
}

/* ── 메인 로드 ── */
async function load(){
  try{
    var res=await fetch('/performance-data');
    var data=await res.json();
    var a=data.analytics||{};
    document.getElementById('subtitle').textContent='업데이트 '+kst(a.updated_at)+' · '+(a.timezone||'Asia/Seoul');
    renderBotStatus(data.bot_status);
    renderMetrics(a);
    renderEquity(a);
    renderStreaks(a);
    renderHeat(a);
    renderDaily(a);
    renderBars(a);
    renderSymbolTable(a);
    renderReasons('entry-reasons',a.entry_reason_stats);
    renderReasons('exit-reasons',a.exit_reason_stats);
    renderTables(a);
  }catch(e){
    document.getElementById('metrics').innerHTML='<div class="card full red">성과 데이터 로딩 실패: '+e.message+'</div>';
  }
}
load();setInterval(load,20000);
</script>
</body>
</html>"""


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
    .briefing-panel{border:1px solid var(--border);border-radius:14px;background:linear-gradient(135deg,rgba(22,27,34,.94),rgba(13,17,23,.98));padding:14px;margin:0 0 14px;box-shadow:0 10px 28px rgba(0,0,0,.22)}
    .briefing-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}
    .briefing-title{font-size:.95rem;font-weight:800;line-height:1.35}
    .briefing-sub{font-size:.78rem;color:var(--muted);line-height:1.45;margin-top:3px}
    .briefing-badge{font-size:.72rem;font-weight:800;padding:4px 8px;border-radius:999px;white-space:nowrap;border:1px solid var(--border)}
    .briefing-badge.active{color:var(--green);background:var(--green-bg);border-color:var(--green-border)}
    .briefing-badge.waiting{color:var(--yellow);background:var(--yellow-bg);border-color:rgba(210,153,34,.3)}
    .briefing-badge.warning,.briefing-badge.negative{color:var(--red);background:var(--red-bg);border-color:var(--red-border)}
    .briefing-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:10px;margin-bottom:10px}
    .briefing-card{border:1px solid var(--border-subtle);border-radius:12px;background:rgba(255,255,255,.03);padding:11px}
    .briefing-card-title{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px}
    .briefing-card-main{font-size:.85rem;line-height:1.5;font-weight:600}
    .briefing-card-main.positive{color:var(--green)}.briefing-card-main.negative{color:var(--red)}.briefing-card-main.neutral{color:var(--text)}
    .briefing-desk-list{display:grid;gap:7px}
    .briefing-desk{display:flex;align-items:flex-start;gap:8px;font-size:.8rem;padding:7px 0;border-top:1px solid var(--border-subtle)}
    .briefing-desk:first-child{border-top:none;padding-top:0}
    .briefing-desk-tag{font-size:.68rem;font-weight:800;padding:3px 7px;border-radius:999px;background:var(--blue-bg);color:var(--blue);white-space:nowrap}
    .briefing-desk-state{font-weight:800}.briefing-desk-state.active{color:var(--green)}.briefing-desk-state.waiting{color:var(--yellow)}.briefing-desk-state.warning{color:var(--red)}
    .briefing-desk-msg{font-size:.74rem;color:var(--muted);line-height:1.42;margin-top:2px}
    .briefing-list{display:grid;gap:5px;margin:0;padding:0;list-style:none}
    .briefing-list li{font-size:.78rem;line-height:1.45;color:var(--muted)}
    .briefing-open{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
    .briefing-open-chip{font-size:.72rem;padding:4px 7px;border-radius:999px;background:rgba(255,255,255,.05);border:1px solid var(--border-subtle)}
    .briefing-open-chip.pos{color:var(--green);border-color:var(--green-border);background:var(--green-bg)}
    .briefing-open-chip.neg{color:var(--red);border-color:var(--red-border);background:var(--red-bg)}
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
    .mobile-bottom-nav{display:none}
    .mobile-bottom-nav a{color:var(--muted);text-decoration:none;font-size:.74rem;font-weight:800;text-align:center;padding:9px 6px;border-radius:12px;border:1px solid transparent}
    .mobile-bottom-nav a.active{color:var(--blue);background:var(--blue-bg);border-color:rgba(88,166,255,.25)}
    /* ── 반응형 ── */
    @media(max-width:900px){.pnl-hero{grid-template-columns:repeat(2,1fr)}.desk-grid{grid-template-columns:repeat(3,1fr)}.briefing-grid{grid-template-columns:1fr}}
    @media(max-width:700px){.mobile-bottom-nav{position:fixed;left:10px;right:10px;bottom:10px;z-index:50;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:7px;border:1px solid var(--border);border-radius:18px;background:rgba(13,17,23,.96);box-shadow:0 14px 38px rgba(0,0,0,.45);backdrop-filter:blur(10px)}}
    @media(max-width:600px){.app{padding:12px 12px 96px}.topbar{padding:10px 12px;align-items:stretch;flex-direction:column}.topbar-left,.topbar-right{width:100%}.topbar-right{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.topbar-right .time-stamp{grid-column:1/-1}.topbar-right .btn{text-align:center;justify-content:center}.pnl-hero{grid-template-columns:repeat(2,1fr)}.pnl-value{font-size:1.2rem}.desk-grid{grid-template-columns:repeat(3,1fr)}.desk-card{padding:10px}.desk-action{font-size:.8rem}.desk-focus{display:none}.status-bar{gap:6px}.s-pill{font-size:.75rem;padding:5px 10px}.btn{padding:6px 12px;font-size:.78rem}.briefing-head{display:block}.briefing-badge{display:inline-flex;margin-top:8px}.briefing-panel{padding:12px}}
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
      <a class="btn btn-ghost" href="/scanner" style="text-decoration:none">📡 스캐너</a>
      <a class="btn btn-ghost" href="/performance" style="text-decoration:none">📈 성과</a>
      <button class="btn btn-ghost" onclick="loadData()">새로고침</button>
      <button class="btn btn-primary" id="cycle-btn" onclick="runCycle()">사이클 실행</button>
    </div>
  </header>

  <nav class="mobile-bottom-nav" aria-label="Mobile navigation">
    <a class="active" href="/">Dashboard</a>
    <a href="/scanner">Scanner</a>
    <a href="/performance">Performance</a>
  </nav>

  <!-- 경보 배너 -->
  <div class="alert-bar" id="alert-bar"></div>

  <section class="briefing-panel" id="operator-briefing">
    <div class="briefing-head">
      <div>
        <div class="briefing-title" id="briefing-headline">운영 상태 로딩 중...</div>
        <div class="briefing-sub" id="briefing-pnl">손익 상태 확인 중</div>
      </div>
      <span class="briefing-badge waiting" id="briefing-mode">대기</span>
    </div>
    <div class="briefing-grid">
      <div class="briefing-card">
        <div class="briefing-card-title">지금 왜 거래/보류 중인가</div>
        <div class="briefing-desk-list" id="briefing-desks"></div>
      </div>
      <div class="briefing-card">
        <div class="briefing-card-title">수익 악화 원인</div>
        <ul class="briefing-list" id="briefing-losses"></ul>
      </div>
      <div class="briefing-card">
        <div class="briefing-card-title">현재 보유</div>
        <div class="briefing-card-main" id="briefing-open-text">--</div>
        <div class="briefing-open" id="briefing-open"></div>
      </div>
      <div class="briefing-card">
        <div class="briefing-card-title">다음 운영 방침</div>
        <ul class="briefing-list" id="briefing-actions"></ul>
      </div>
    </div>
  </section>

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
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
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
  function renderOperatorBriefing(b){
    b=b||{};
    var badge=document.getElementById('briefing-mode');
    document.getElementById('briefing-headline').textContent=b.headline||'운영 상태 정보 없음';
    document.getElementById('briefing-pnl').textContent=b.pnl_message||'손익 상태 정보 없음';
    document.getElementById('briefing-pnl').className='briefing-sub '+(b.pnl_tone||'neutral');
    badge.textContent=b.mode_label||b.mode||'대기';
    badge.className='briefing-badge '+(b.headline_tone||'waiting');
    var desks=(b.desk_messages||[]).map(function(d){
      return '<div class="briefing-desk"><span class="briefing-desk-tag">'+esc(d.title||d.desk||'데스크')+'</span><div><div><span class="briefing-desk-state '+esc(d.tone||'waiting')+'">'+esc(d.state||'대기')+'</span> <span style="color:var(--muted);font-size:.72rem">'+esc(d.size||'0.00x')+'</span></div><div class="briefing-desk-msg">'+esc(d.message||'판단 근거 수집 중')+'</div></div></div>';
    }).join('');
    document.getElementById('briefing-desks').innerHTML=desks||'<div class="empty-row">데스크 판단 없음</div>';
    document.getElementById('briefing-losses').innerHTML=(b.loss_causes||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('')||'<li>손익 원인 분석 데이터 없음</li>';
    document.getElementById('briefing-actions').innerHTML=(b.next_actions||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('')||'<li>다음 운영 방침 없음</li>';
    var opens=b.open_summary||[];
    document.getElementById('briefing-open-text').textContent=opens.length?opens.length+'개 포지션 보유 / 총 노출 '+Number(b.gross_open_notional_pct||0).toFixed(2)+'x':'보유 포지션 없음';
    document.getElementById('briefing-open').innerHTML=opens.map(function(p){var pnl=parseFloat(p.pnl_pct||0);return '<span class="briefing-open-chip '+(pnl>0?'pos':pnl<0?'neg':'')+'">'+esc(p.symbol||'--')+' '+fmtPct(pnl)+'</span>';}).join('');
  }
  function renderPnl(perf,cap){var rp=parseFloat(perf.realized_pnl_pct||0),up=parseFloat(perf.unrealized_pnl_pct||0),cp=parseFloat(cap.cumulative_realized_pnl_pct||0);var rc=document.getElementById('pnl-realized-card'),uc=document.getElementById('pnl-unrealized-card');document.getElementById('pnl-realized').textContent=fmtPct(rp);document.getElementById('pnl-realized').className='pnl-value '+(rp>0?'pos':rp<0?'neg':'neu');document.getElementById('pnl-realized-krw').textContent=fmtKrwFull(perf.realized_pnl_krw,true);rc.className='pnl-card'+(rp>0?' hl-pos':rp<0?' hl-neg':'');document.getElementById('pnl-unrealized').textContent=fmtPct(up);document.getElementById('pnl-unrealized').className='pnl-value '+(up>0?'pos':up<0?'neg':'neu');document.getElementById('pnl-unrealized-krw').textContent=fmtKrwFull(perf.unrealized_pnl_krw,true);uc.className='pnl-card'+(up>0?' hl-pos':up<0?' hl-neg':'');var wr=parseFloat(perf.cumulative_win_rate||perf.win_rate||0);document.getElementById('pnl-winrate').textContent=wr.toFixed(1)+'%';document.getElementById('pnl-winrate').className='pnl-value '+(wr>=55?'pos':wr<40?'neg':'neu');document.getElementById('pnl-trades').textContent=(perf.cumulative_wins||perf.wins||0)+'승 / '+(perf.cumulative_losses||perf.losses||0)+'패 (누적 '+fmtPct(cp)+')';document.getElementById('pnl-capital').textContent='₩'+(parseInt(cap.total_krw||0)).toLocaleString('ko-KR');document.getElementById('pnl-capital-base').textContent='복리자본 ₩'+(parseInt(cap.effective_capital_krw||cap.base_krw||0)).toLocaleString('ko-KR')+(cp!==0?' ('+fmtPct(cp)+')':'');}
  function renderStatusBar(state,readiness,blockSummary){var stance=String(state.stance||'--');var regime=String(state.regime||'--');var allow=!!((readiness.exposure||{}).allow_new_entries!=null?(readiness.exposure||{}).allow_new_entries:state.allow_new_entries);var overall=String(readiness.overall||'caution');var stanceCls=stance==='BULLISH'?'ok':stance==='DEFENSE'?'bad':'warn';var regimeCls=regime==='TRENDING'?'ok':regime==='STRESSED'?'bad':'warn';var bar=document.getElementById('status-bar');bar.innerHTML='<div class="s-pill '+stanceCls+'"><span class="lbl">스탠스</span>'+stance+'</div>'+'<div class="s-pill '+regimeCls+'"><span class="lbl">국면</span>'+regime+'</div>'+'<div class="s-pill '+(allow?'ok':'bad')+'"><span class="lbl">진입</span>'+(allow?'허용':'차단')+'</div>'+'<div class="s-pill '+(overall==='ready'?'ok':overall==='caution'?'warn':'bad')+'"><span class="lbl">준비도</span>'+overall.toUpperCase()+'</div>';}
  function renderSignal(lane,history){var ts=String((lane||{}).trigger_state||'waiting');var sig=parseFloat((lane||{}).signal_score||0);var trig=parseFloat((lane||{}).trigger_threshold||0.56);var dist=parseFloat((lane||{}).distance_to_trigger||0);var sym=String((lane||{}).symbol||'KRW-BTC');var act=String((lane||{}).action||'watchlist_only');var card=document.getElementById('signal-card');var badge=document.getElementById('signal-badge');card.className='signal-card '+(ts==='ready'?'ready':ts==='arming'?'arming':'');badge.className='signal-badge '+ts;badge.textContent=ts==='ready'?'진입 준비':ts==='arming'?'접근 중':'대기';document.getElementById('signal-sym').textContent=sym+' · '+actionKo(act);var pct=trig>0?Math.min(sig/trig*100,100):0;var fill=document.getElementById('gauge-fill');fill.style.width=pct.toFixed(1)+'%';fill.className='gauge-fill '+(ts==='ready'?'ready':ts==='arming'?'arming':'');document.getElementById('gauge-cur').textContent='현재 '+sig.toFixed(2);document.getElementById('gauge-trig').textContent='진입 '+trig.toFixed(2);document.getElementById('signal-meta').textContent=ts==='ready'?'진입 조건 충족 — 파일럿 주문 실행 중':ts==='arming'?'진입까지 거리 '+dist.toFixed(2)+' — 모니터링 강화':'진입까지 거리 '+dist.toFixed(2)+' (필요: '+trig.toFixed(2)+')';var chips=(history||[]).slice(-4).map(function(r){var rs=parseFloat(r.signal_score||0),rt=parseFloat(r.trigger_threshold||0);return '<span class="trend-chip">'+(r.time||'--:--')+' '+rs.toFixed(2)+'</span>';});document.getElementById('trend-mini').innerHTML=chips.join('');}
  function renderDesks(desks){var map=[['crypto','dk-crypto'],['korea','dk-korea'],['us','dk-us']];var dg=document.getElementById('desk-grid');var cards=dg.querySelectorAll('.desk-card');map.forEach(function(m,i){var key=m[0],pfx=m[1],item=(desks||{})[key]||{};if(cards[i])cards[i].style.display=item.disabled?'none':'';var cls=actionCls(item.action);cards[i].className='desk-card '+(cls==='watch'?'':''+cls);document.getElementById(pfx+'-act').textContent=actionKo(item.action);document.getElementById(pfx+'-act').className='desk-action '+cls;document.getElementById(pfx+'-focus').textContent=item.focus||'신호 없음';var sizeEl=document.getElementById(pfx+'-size');sizeEl.textContent=item.size||'0.00x';sizeEl.className='desk-size'+(item.size&&item.size!=='0.00x'?' active':'');var qfill=document.getElementById(pfx+'-qfill');if(qfill){var qs=parseFloat(item.quality_score||0),qt=parseFloat(item.quality_threshold||0.58);var qpct=qt>0?Math.min(qs/qt*100,100):0;qfill.style.width=qpct.toFixed(1)+'%';qfill.className='mini-gauge-fill'+(qpct>=100?' ready':qpct>=70?' arming':'');var qvalEl=document.getElementById(pfx+'-qval');var qthrEl=document.getElementById(pfx+'-qthr');if(qvalEl)qvalEl.textContent='품질 '+qs.toFixed(2);if(qthrEl)qthrEl.textContent='기준 '+qt.toFixed(2);}
      // Breakout badge for Korea / Crypto desks
      var bkEl=document.getElementById(pfx+'-bk');if(bkEl){var bkC=parseInt(item.breakout_confirmed_count||0),bkP=parseInt(item.breakout_partial_count||0);if(bkC>0){bkEl.textContent='BK '+bkC;bkEl.className='desk-bk-badge full';bkEl.style.display='inline-block';}else if(bkP>0){bkEl.textContent='BK ~'+bkP;bkEl.className='desk-bk-badge partial';bkEl.style.display='inline-block';}else{bkEl.style.display='none';}}
    });}
  function renderOrderBar(exec){var pending=parseInt(exec.pending_count||0),partial=parseInt(exec.partial_count||0),stale=parseInt(exec.stale_count||0),live=parseInt(exec.live_count||0);var items=[];if(stale>0)items.push('<div class="o-badge bad"><span class="num">'+stale+'</span> 미처리 주문</div>');if(partial>0)items.push('<div class="o-badge warn"><span class="num">'+partial+'</span> 부분 체결</div>');if(pending>0)items.push('<div class="o-badge warn"><span class="num">'+pending+'</span> 대기 중</div>');if(live>0&&!pending&&!partial&&!stale)items.push('<div class="o-badge ok"><span class="num">'+live+'</span> 실전 주문 정상</div>');var bar=document.getElementById('order-bar');if(items.length){bar.innerHTML=items.join('');bar.style.display='flex';}else{bar.style.display='none';}}
  function renderPositions(pos){var cnt=document.getElementById('pos-count'),body=document.getElementById('positions-body');if(!pos||!pos.length){cnt.textContent='0';body.innerHTML='<div class="empty-row">보유 포지션 없음</div>';return;}cnt.textContent=String(pos.length);var rows=pos.map(function(p){var pnl=parseFloat(p.pnl_pct||p.unrealized_pnl_pct||0);return '<tr><td class="sym">'+(p.symbol||'--')+'</td><td><span class="desk-tag">'+(p.desk||'--')+'</span></td><td>'+Number(p.entry_price||0).toLocaleString('ko-KR')+'</td><td class="'+(pnl>0?'pos':pnl<0?'neg':'')+'" style="font-weight:700">'+fmtPct(pnl)+'</td><td style="color:var(--muted);font-size:.78rem">'+toKST(p.opened_at||'')+'</td></tr>';}).join('');body.innerHTML='<table class="pos-table"><thead><tr><th>종목</th><th>데스크</th><th>진입가</th><th>미실현</th><th>시각</th></tr></thead><tbody>'+rows+'</tbody></table>';}
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
  async function loadData(){try{var dr=await fetch('/dashboard-data'),hr=await fetch('/health');var data=await dr.json(),health=await hr.json();var state=data.state||{},dash=data.dashboard||{},readiness=data.live_readiness_checklist||{},brokerH=data.broker_live_health||{},exec=(dash.execution_summary||{}),perf=(dash.performance||{}),cap=(dash.capital||{}),blockSummary=((dash.exposure||{}).entry_block_summary)||((readiness||{}).entry_block_summary)||{};var isLive=String(readiness.execution_mode||'').indexOf('live')>=0;var dot=document.getElementById('status-dot');dot.className='status-dot'+(blockSummary.blocked?' err':isLive?' ':'');var modeEl=document.getElementById('mode-tag');modeEl.textContent=String(readiness.execution_mode||'모의투자');modeEl.className='mode-tag'+(isLive?' live':'');document.getElementById('update-time').textContent=toKST(state.updated_at);if(blockSummary&&blockSummary.blocked){var ab=document.getElementById('alert-bar');ab.textContent='\\u26a0\\ufe0f '+String(blockSummary.detail||blockSummary.headline||'실행 차단');ab.className='alert-bar visible';}else{document.getElementById('alert-bar').className='alert-bar';}renderOperatorBriefing(dash.operator_briefing||{});renderPnl(perf,cap);renderStatusBar(state,readiness,blockSummary);renderSignal(dash.crypto_live_lane||null,dash.crypto_live_lane_history||[]);window.__deskDrilldown=dash.desk_drilldown||{};renderDesks(dash.desk_status||{});renderOrderBar(exec);renderPositions(dash.open_positions||[]);renderTrades(dash.closed_positions||[]);renderEquity(dash.equity_curve||[]);renderBroker(brokerH,readiness);renderAgentLog(dash.agent_log||[]);}catch(e){var dot2=document.getElementById('status-dot');dot2.className='status-dot err';document.getElementById('alert-bar').textContent='\\u26a0\\ufe0f 데이터 로딩 실패: '+e.message;document.getElementById('alert-bar').className='alert-bar visible';}}
  async function runCycle(){var btn=document.getElementById('cycle-btn');btn.disabled=true;btn.textContent='실행 중...';try{await fetch('/cycle',{method:'POST'});await loadData();}catch(e){console.error(e);}finally{btn.disabled=false;btn.textContent='사이클 실행';}}
  setInterval(function(){loadData().catch(function(){});},20000);loadData().catch(function(){});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return _embedded_dashboard_html()


# ─────────────────────────────────────────────────────────────────
#  스캐너 API + 페이지
# ─────────────────────────────────────────────────────────────────
def _scanner_price_change_pct(raw_value: object) -> float:
    try:
        value = float(raw_value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return round(value if abs(value) > 1.0 else value * 100, 2)


def _scanner_chart_payload(market: str) -> dict:
    symbol = str(market or "").strip()
    if not symbol:
        return {"candles_15m": [], "sparkline": [], "sparkline_change_pct": 0.0, "chart_cached": False}
    now = time.time()
    cached = _SCANNER_CHART_CACHE.get(symbol) or {}
    if cached and now - float(cached.get("fetched_at", 0.0) or 0.0) < _SCANNER_CHART_TTL_SECONDS:
        return {
            "candles_15m": cached.get("candles_15m", []),
            "sparkline": cached.get("sparkline", []),
            "sparkline_change_pct": cached.get("sparkline_change_pct", 0.0),
            "chart_cached": True,
        }
    candles = get_upbit_15m_candles(symbol, count=_SCANNER_CHART_COUNT)
    compact = [
        {
            "t": item.get("date", ""),
            "o": round(float(item.get("open", 0.0) or 0.0), 8),
            "h": round(float(item.get("high", 0.0) or 0.0), 8),
            "l": round(float(item.get("low", 0.0) or 0.0), 8),
            "c": round(float(item.get("close", 0.0) or 0.0), 8),
        }
        for item in candles
        if float(item.get("close", 0.0) or 0.0) > 0
    ][-_SCANNER_CHART_COUNT:]
    closes = [item["c"] for item in compact]
    change_pct = 0.0
    if len(closes) >= 2 and closes[0] > 0:
        change_pct = round((closes[-1] - closes[0]) / closes[0] * 100, 2)
    payload = {
        "candles_15m": compact,
        "sparkline": closes,
        "sparkline_change_pct": change_pct,
        "chart_cached": False,
    }
    _SCANNER_CHART_CACHE[symbol] = {"fetched_at": now, **payload}
    return payload


@app.get("/scanner-data")
def scanner_data() -> dict:
    """전체 스캔 코인 데이터 반환 (스캐너 페이지용)"""
    state = load_company_state()
    snapshot_crypto_view = state.market_snapshot.get("crypto_view", {}) if state.market_snapshot else {}
    desk_crypto_view = state.desk_views.get("crypto_desk", {}) if state.desk_views else {}
    crypto_view = desk_crypto_view or snapshot_crypto_view or {}
    all_candidates = list(
        crypto_view.get("all_candidates")
        or crypto_view.get("candidate_markets")
        or (state.strategy_book.get("crypto_plan", {}) if state.strategy_book else {}).get("candidate_markets")
        or []
    )
    scanned_count = int(crypto_view.get("scanned_market_count", len(all_candidates)) or len(all_candidates))
    direction_bias = str(crypto_view.get("direction_bias", "balanced") or "balanced")
    direction_score = float(crypto_view.get("direction_score", 0.5) or 0.5)
    from datetime import datetime, timezone
    markets = [str(item.get("market") or "").strip() for item in all_candidates if isinstance(item, dict)]
    price_map = get_upbit_ticker_prices(markets)
    with ThreadPoolExecutor(max_workers=6) as executor:
        chart_map = dict(zip(markets, executor.map(_scanner_chart_payload, markets))) if markets else {}
    enriched_candidates = []
    for item in all_candidates:
        if not isinstance(item, dict):
            continue
        market = str(item.get("market") or "").strip()
        row = dict(item)
        current_price = float(price_map.get(market) or row.get("trade_price") or row.get("current_price") or 0.0)
        row["current_price"] = current_price
        row["trade_price"] = current_price
        row["price_change_pct"] = _scanner_price_change_pct(row.get("change_rate", 0.0))
        row.update(chart_map.get(market) or _scanner_chart_payload(market))
        enriched_candidates.append(row)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "scanned_count": scanned_count,
        "direction_bias": direction_bias,
        "direction_score": round(direction_score, 3),
        "price_source": "upbit_ws_cache_or_rest",
        "chart_ttl_seconds": _SCANNER_CHART_TTL_SECONDS,
        "candidates": enriched_candidates,
    }


def _scanner_html() -> str:
    return """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>코인 스캐너</title>
  <style>
    :root{
      --bg:#0d1117;--surface:rgba(22,27,34,.95);--border:rgba(48,54,61,1);--border-sub:rgba(48,54,61,.5);
      --text:#e6edf3;--muted:#7d8590;
      --green:#3fb950;--green-bg:rgba(63,185,80,.12);--green-bd:rgba(63,185,80,.35);
      --red:#f85149;--red-bg:rgba(248,81,73,.12);--red-bd:rgba(248,81,73,.35);
      --blue:#58a6ff;--blue-bg:rgba(88,166,255,.10);--blue-bd:rgba(88,166,255,.3);
      --yellow:#d29922;--yellow-bg:rgba(210,153,34,.12);--yellow-bd:rgba(210,153,34,.3);
      --purple:#bc8cff;--purple-bg:rgba(188,140,255,.12);--purple-bd:rgba(188,140,255,.3);
      --cyan:#39d0d0;--cyan-bg:rgba(57,208,208,.10);--cyan-bd:rgba(57,208,208,.28);
      --font:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;
      --mono:'D2Coding','Consolas',monospace;
    }
    *{box-sizing:border-box}html,body{margin:0;padding:0}
    body{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;-webkit-font-smoothing:antialiased}
    .wrap{max-width:1300px;margin:0 auto;padding:14px 16px 80px}

    /* ── 헤더 ── */
    .hdr{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;
         padding:12px 16px;border:1px solid var(--border);border-radius:12px;
         background:rgba(22,27,34,.9);margin-bottom:14px}
    .hdr-left{display:flex;align-items:center;gap:10px}
    .hdr-title{font-size:1rem;font-weight:700;color:var(--text)}
    .hdr-sub{font-size:.75rem;color:var(--muted);font-family:var(--mono)}
    .hdr-right{display:flex;align-items:center;gap:8px}
    .back-btn{font-size:.78rem;padding:5px 12px;border:1px solid var(--border);border-radius:8px;
              background:transparent;color:var(--muted);cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:4px}
    .back-btn:hover{border-color:var(--blue-bd);color:var(--blue)}
    .refresh-dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

    /* ── 시장 개요 바 ── */
    .market-bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
    .mbar-card{flex:1;min-width:120px;padding:10px 14px;border:1px solid var(--border-sub);border-radius:10px;
               background:rgba(255,255,255,.03)}
    .mbar-label{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}
    .mbar-val{font-size:.95rem;font-weight:700}
    .mbar-val.bull{color:var(--green)}.mbar-val.bear{color:var(--red)}.mbar-val.neutral{color:var(--muted)}

    /* ── 필터 칩 ── */
    .filter-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
    .filter-label{font-size:.72rem;color:var(--muted);margin-right:2px}
    .chip{padding:5px 12px;border-radius:999px;border:1px solid var(--border);font-size:.76rem;font-weight:600;
          cursor:pointer;background:transparent;color:var(--muted);transition:all .15s}
    .chip:hover{border-color:var(--blue-bd);color:var(--blue)}
    .chip.active{background:var(--blue-bg);border-color:var(--blue-bd);color:var(--blue)}
    .chip.c-offense{}.chip.c-offense.active{background:var(--green-bg);border-color:var(--green-bd);color:var(--green)}
    .chip.c-defense.active{background:var(--red-bg);border-color:var(--red-bd);color:var(--red)}
    .chip.c-breakout.active{background:var(--yellow-bg);border-color:var(--yellow-bd);color:var(--yellow)}
    .chip.c-pullback.active{background:var(--purple-bg);border-color:var(--purple-bd);color:var(--purple)}
    .chip.c-ict.active{background:var(--cyan-bg);border-color:var(--cyan-bd);color:var(--cyan)}
    .chip.c-stream.active{background:rgba(255,210,80,.10);border-color:rgba(255,210,80,.3);color:#ffd250}

    /* ── 정렬 안내 ── */
    .sort-hint{font-size:.7rem;color:var(--muted);margin-bottom:8px}
    .sort-hint span{color:var(--blue);font-weight:600}

    /* ── 테이블 래퍼 ── */
    .tbl-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:12px;background:rgba(22,27,34,.9)}
    table{width:100%;border-collapse:collapse;font-size:.8rem}
    thead tr{border-bottom:1px solid var(--border)}
    th{padding:10px 10px;text-align:left;font-size:.7rem;color:var(--muted);text-transform:uppercase;
       letter-spacing:.06em;white-space:nowrap;cursor:pointer;user-select:none;position:sticky;top:0;
       background:rgba(22,27,34,.98);z-index:2}
    th:hover{color:var(--blue)}
    th.sorted-asc::after{content:' ↑';color:var(--blue)}
    th.sorted-desc::after{content:' ↓';color:var(--blue)}
    td{padding:9px 10px;border-bottom:1px solid var(--border-sub);white-space:nowrap;vertical-align:middle}
    tr:last-child td{border-bottom:none}
    tr.hidden{display:none}
    tr.rank-1{background:rgba(63,185,80,.06);border-left:2px solid var(--green)}
    tr.rank-2{background:rgba(88,166,255,.04)}
    tr.rank-3{background:rgba(88,166,255,.025)}
    tr:hover td{background:rgba(255,255,255,.025)}

    /* ── 스코어 바 ── */
    .score-wrap{display:flex;align-items:center;gap:6px}
    .score-bar{width:52px;height:5px;border-radius:3px;background:rgba(255,255,255,.08);overflow:hidden;flex-shrink:0}
    .score-fill{height:100%;border-radius:3px;transition:width .3s}
    .score-num{font-family:var(--mono);font-size:.78rem;font-weight:600;min-width:34px}
    .score-num.hi{color:var(--green)}.score-num.mid{color:var(--yellow)}.score-num.lo{color:var(--muted)}

    /* ── 심볼 셀 ── */
    .sym-cell{display:flex;align-items:center;gap:7px}
    .rank-badge{font-size:.65rem;font-weight:800;width:18px;height:18px;border-radius:50%;
                display:flex;align-items:center;justify-content:center;flex-shrink:0}
    .rank-badge.r1{background:var(--green);color:#000}
    .rank-badge.r2{background:rgba(88,166,255,.7);color:#000}
    .rank-badge.r3{background:rgba(255,255,255,.2);color:var(--text)}
    .rank-badge.rN{background:rgba(255,255,255,.05);color:var(--muted)}
    .sym-name{font-weight:700;font-size:.86rem}
    .sym-pair{font-size:.68rem;color:var(--muted)}
    .sym-change{font-family:var(--mono);font-size:.72rem}
    .sym-change.pos{color:var(--green)}.sym-change.neg{color:var(--red)}.sym-change.flat{color:var(--muted)}
    .price-cell{font-family:var(--mono);font-weight:800;font-size:.82rem}
    .price-sub{font-family:var(--mono);font-size:.66rem;color:var(--muted);margin-top:2px}
    .mini-chart{width:128px;height:42px;display:block}
    .mini-chart-wrap{display:flex;align-items:center;gap:8px;min-width:170px}
    .mini-chart-change{font-family:var(--mono);font-size:.72rem;font-weight:800;min-width:44px}
    .mini-chart-empty{font-size:.72rem;color:var(--muted)}
    .candle-up{fill:rgba(63,185,80,.78);stroke:rgba(63,185,80,.9)}
    .candle-down{fill:rgba(248,81,73,.72);stroke:rgba(248,81,73,.9)}
    .candle-wick{stroke:rgba(230,237,243,.42);stroke-width:1}
    .spark-line{fill:none;stroke:var(--blue);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}

    /* ── 상태 뱃지 ── */
    .badge-row{display:flex;gap:3px;flex-wrap:wrap}
    .sbadge{font-size:.62rem;font-weight:700;padding:2px 6px;border-radius:4px;white-space:nowrap}
    .sbadge.ignition{background:var(--green-bg);color:var(--green);border:1px solid var(--green-bd)}
    .sbadge.breakout{background:var(--yellow-bg);color:var(--yellow);border:1px solid var(--yellow-bd)}
    .sbadge.pullback{background:var(--purple-bg);color:var(--purple);border:1px solid var(--purple-bd)}
    .sbadge.ict{background:var(--cyan-bg);color:var(--cyan);border:1px solid var(--cyan-bd)}
    .sbadge.stream{background:rgba(255,210,80,.12);color:#ffd250;border:1px solid rgba(255,210,80,.3)}
    .sbadge.reversal{background:var(--red-bg);color:var(--red);border:1px solid var(--red-bd)}
    .sbadge.ob{background:var(--blue-bg);color:var(--blue);border:1px solid var(--blue-bd)}
    .sbadge.exhaust{background:rgba(255,100,100,.1);color:#f87;border:1px solid rgba(255,100,100,.25)}

    /* ── Bias 태그 ── */
    .bias-tag{font-size:.68rem;font-weight:700;padding:2px 7px;border-radius:5px}
    .bias-tag.offense{background:var(--green-bg);color:var(--green);border:1px solid var(--green-bd)}
    .bias-tag.defense{background:var(--red-bg);color:var(--red);border:1px solid var(--red-bd)}
    .bias-tag.balanced{background:rgba(255,255,255,.06);color:var(--muted);border:1px solid var(--border-sub)}

    /* ── RSI 셀 ── */
    .rsi-val{font-family:var(--mono);font-size:.78rem;font-weight:600}
    .rsi-val.hot{color:var(--red)}.rsi-val.warm{color:var(--yellow)}.rsi-val.cool{color:var(--green)}.rsi-val.flat{color:var(--muted)}

    /* ── 자동발견 섹션 ── */
    .discovery-section{margin-top:18px}
    .disc-title{font-size:.8rem;font-weight:700;color:var(--muted);text-transform:uppercase;
                letter-spacing:.08em;margin-bottom:8px;display:flex;align-items:center;gap:6px}
    .disc-count{font-size:.68rem;font-weight:600;padding:2px 7px;border-radius:999px;background:rgba(255,255,255,.08)}
    .disc-grid{display:flex;gap:8px;flex-wrap:wrap}
    .disc-card{flex:1;min-width:140px;max-width:200px;padding:10px 12px;border-radius:10px;
               border:1px solid var(--border-sub);background:rgba(255,255,255,.025);cursor:pointer}
    .disc-card:hover{border-color:var(--blue-bd);background:rgba(88,166,255,.06)}
    .disc-card-sym{font-size:.85rem;font-weight:700;margin-bottom:4px}
    .disc-card-score{font-size:.75rem;color:var(--muted);font-family:var(--mono)}
    .disc-card-tags{display:flex;gap:4px;flex-wrap:wrap;margin-top:5px}

    /* ── 하단 여백 ── */
    .spacer{height:40px}
    .mobile-bottom-nav{display:none}
    .mobile-bottom-nav a{color:var(--muted);text-decoration:none;font-size:.74rem;font-weight:900;text-align:center;padding:9px 6px;border-radius:12px;border:1px solid transparent}
    .mobile-bottom-nav a.active{color:var(--blue);background:var(--blue-bg);border-color:var(--blue-bd)}
    @media(max-width:700px){
      .wrap{padding:12px 10px 96px}
      .hdr{align-items:stretch}
      .hdr-left,.hdr-right{width:100%}
      .hdr-right{display:grid;grid-template-columns:1fr}
      .back-btn{justify-content:center}
      .filter-row{flex-wrap:nowrap;overflow-x:auto;padding-bottom:4px;-webkit-overflow-scrolling:touch}
      .chip{white-space:nowrap}
      .market-bar{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
      .mobile-bottom-nav{position:fixed;left:10px;right:10px;bottom:10px;z-index:50;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:7px;border:1px solid var(--border);border-radius:18px;background:rgba(13,17,23,.96);box-shadow:0 14px 38px rgba(0,0,0,.45);backdrop-filter:blur(10px)}
    }
  </style>
</head>
<body>
<div class="wrap">

  <!-- 헤더 -->
  <div class="hdr">
    <div class="hdr-left">
      <div class="refresh-dot" id="rdot"></div>
      <div>
        <div class="hdr-title">📡 코인 스캐너</div>
        <div class="hdr-sub" id="hdr-sub">로딩 중…</div>
      </div>
    </div>
    <div class="hdr-right">
      <a class="back-btn" href="/">← 대시보드</a>
    </div>
  </div>

  <nav class="mobile-bottom-nav" aria-label="Mobile navigation">
    <a href="/">Dashboard</a>
    <a class="active" href="/scanner">Scanner</a>
    <a href="/performance">Performance</a>
  </nav>

  <!-- 시장 개요 바 -->
  <div class="market-bar" id="market-bar">
    <div class="mbar-card"><div class="mbar-label">방향</div><div class="mbar-val neutral" id="mb-dir">—</div></div>
    <div class="mbar-card"><div class="mbar-label">스캔 코인</div><div class="mbar-val neutral" id="mb-cnt">—</div></div>
    <div class="mbar-card"><div class="mbar-label">공세 코인</div><div class="mbar-val" id="mb-off">—</div></div>
    <div class="mbar-card"><div class="mbar-label">브레이크아웃</div><div class="mbar-val" id="mb-bk">—</div></div>
    <div class="mbar-card"><div class="mbar-label">눌림목</div><div class="mbar-val" id="mb-pb">—</div></div>
    <div class="mbar-card"><div class="mbar-label">스트림 점화</div><div class="mbar-val" id="mb-si">—</div></div>
  </div>

  <!-- 필터 칩 -->
  <div class="filter-row">
    <span class="filter-label">필터:</span>
    <button class="chip active" data-f="all" onclick="setFilter('all',this)">전체</button>
    <button class="chip c-offense" data-f="offense" onclick="setFilter('offense',this)">🟢 공세</button>
    <button class="chip c-defense" data-f="defense" onclick="setFilter('defense',this)">🔴 방어</button>
    <button class="chip c-breakout" data-f="breakout" onclick="setFilter('breakout',this)">🚀 브레이크아웃</button>
    <button class="chip c-pullback" data-f="pullback" onclick="setFilter('pullback',this)">🔄 눌림목</button>
    <button class="chip c-ict" data-f="ict" onclick="setFilter('ict',this)">🎯 ICT</button>
    <button class="chip c-stream" data-f="stream" onclick="setFilter('stream',this)">⚡ 스트림</button>
  </div>
  <div class="sort-hint">컬럼 클릭으로 정렬 · 현재 정렬: <span id="sort-label">Combined ↓</span></div>

  <!-- 테이블 -->
  <div class="tbl-wrap">
    <table id="scan-tbl">
      <thead>
        <tr>
          <th onclick="sortBy('rank')">#</th>
          <th onclick="sortBy('market')">코인</th>
          <th onclick="sortBy('current_price')">현재가</th>
          <th onclick="sortBy('sparkline_change_pct')">15m 차트</th>
          <th onclick="sortBy('combined_score')">Combined</th>
          <th onclick="sortBy('signal_score')">Signal</th>
          <th onclick="sortBy('micro_score')">Micro</th>
          <th onclick="sortBy('orderbook_score')">OB</th>
          <th onclick="sortBy('stream_score')">Stream</th>
          <th onclick="sortBy('rsi')">RSI</th>
          <th onclick="sortBy('vol_ratio')">Vol배율</th>
          <th onclick="sortBy('atr_pct')">ATR%</th>
          <th onclick="sortBy('bias')">Bias</th>
          <th>상태</th>
        </tr>
      </thead>
      <tbody id="scan-body">
        <tr><td colspan="14" style="text-align:center;padding:30px;color:var(--muted)">로딩 중…</td></tr>
      </tbody>
    </table>
  </div>

  <!-- 자동발견 섹션 -->
  <div class="discovery-section">
    <div class="disc-title">🚀 브레이크아웃 확정 <span class="disc-count" id="disc-bk-cnt">0</span></div>
    <div class="disc-grid" id="disc-bk"></div>
  </div>
  <div class="discovery-section">
    <div class="disc-title">🔄 눌림목 진입 <span class="disc-count" id="disc-pb-cnt">0</span></div>
    <div class="disc-grid" id="disc-pb"></div>
  </div>
  <div class="discovery-section">
    <div class="disc-title">🎯 ICT 구조 <span class="disc-count" id="disc-ict-cnt">0</span></div>
    <div class="disc-grid" id="disc-ict"></div>
  </div>
  <div class="discovery-section">
    <div class="disc-title">⚡ 스트림 점화 <span class="disc-count" id="disc-si-cnt">0</span></div>
    <div class="disc-grid" id="disc-si"></div>
  </div>

  <div class="spacer"></div>
</div>

<script>
var _data = [];
var _sortKey = 'combined_score';
var _sortAsc = false;
var _filter = 'all';

// ── 유틸 ──
function sc(v){ return typeof v==='number'?v:parseFloat(v||0)||0; }
function rsi(v){ return typeof v==='number'?v:parseFloat(v||0)||50; }
function sym(m){ return String(m||'').replace('KRW-',''); }
function fmtPrice(v){
  v=sc(v);
  if(v>=1000000) return Math.round(v).toLocaleString('ko-KR');
  if(v>=1000) return v.toLocaleString('ko-KR',{maximumFractionDigits:0});
  if(v>=100) return v.toLocaleString('ko-KR',{maximumFractionDigits:1});
  if(v>=10) return v.toLocaleString('ko-KR',{maximumFractionDigits:2});
  return v.toLocaleString('ko-KR',{maximumFractionDigits:4});
}
function pctText(v){ v=sc(v); return (v>0?'+':'')+v.toFixed(2)+'%'; }

function scoreColor(v){
  if(v>=0.65) return 'var(--green)';
  if(v>=0.50) return 'var(--yellow)';
  if(v>=0.35) return 'var(--muted)';
  return 'var(--red)';
}
function scoreCls(v){
  if(v>=0.62) return 'hi';
  if(v>=0.46) return 'mid';
  return 'lo';
}
function scoreBar(v, color){
  var pct = Math.min(100, Math.round(sc(v)*100));
  return '<div class="score-wrap">'
    +'<div class="score-bar"><div class="score-fill" style="width:'+pct+'%;background:'+color+'"></div></div>'
    +'<span class="score-num '+scoreCls(sc(v))+'">'+sc(v).toFixed(2)+'</span>'
    +'</div>';
}
function miniChart(c){
  var candles=(c.candles_15m||[]).slice(-24);
  if(!candles.length) return '<div class="mini-chart-empty">15m 데이터 없음</div>';
  var w=128,h=42,pad=3;
  var highs=candles.map(function(x){return sc(x.h)}), lows=candles.map(function(x){return sc(x.l)}), closes=candles.map(function(x){return sc(x.c)});
  var hi=Math.max.apply(null,highs), lo=Math.min.apply(null,lows), rng=(hi-lo)||1;
  function x(i){return pad+(i/(Math.max(1,candles.length-1)))*(w-pad*2)}
  function y(v){return pad+((hi-v)/rng)*(h-pad*2)}
  var cw=Math.max(2,Math.min(5,(w-pad*2)/candles.length*.55));
  var parts='';
  candles.forEach(function(row,i){
    var xx=x(i), yo=y(sc(row.o)), yc=y(sc(row.c)), yh=y(sc(row.h)), yl=y(sc(row.l));
    var up=sc(row.c)>=sc(row.o), top=Math.min(yo,yc), bh=Math.max(1,Math.abs(yc-yo));
    parts+='<line class="candle-wick" x1="'+xx.toFixed(1)+'" y1="'+yh.toFixed(1)+'" x2="'+xx.toFixed(1)+'" y2="'+yl.toFixed(1)+'"></line>';
    parts+='<rect class="'+(up?'candle-up':'candle-down')+'" x="'+(xx-cw/2).toFixed(1)+'" y="'+top.toFixed(1)+'" width="'+cw.toFixed(1)+'" height="'+bh.toFixed(1)+'" rx="1"></rect>';
  });
  var line=closes.map(function(v,i){return x(i).toFixed(1)+','+y(v).toFixed(1)}).join(' ');
  var ch=sc(c.sparkline_change_pct), chCls=ch>0?'pos':ch<0?'neg':'flat';
  return '<div class="mini-chart-wrap"><svg class="mini-chart" viewBox="0 0 '+w+' '+h+'" aria-label="15분 미니 차트">'+parts+'<polyline class="spark-line" points="'+line+'"></polyline></svg><span class="mini-chart-change '+chCls+'">'+pctText(ch)+'</span></div>';
}
function rsiCls(v){
  if(v>=75) return 'hot';
  if(v>=60) return 'warm';
  if(v>=42) return 'cool';
  return 'flat';
}
function biasCls(b){ return String(b||'').toLowerCase(); }
function biasKo(b){
  if(b==='offense') return '공세';
  if(b==='defense') return '방어';
  return '균형';
}

function getStatuses(c){
  var tags = [];
  if(sc(c.combined_score)>=0.62 && c.micro_ready && c.orderbook_ready) tags.push({cls:'ignition',txt:'🔥점화'});
  if(c.breakout_confirmed) tags.push({cls:'breakout',txt:'🚀BK'});
  else if(c.breakout_partial) tags.push({cls:'breakout',txt:'🟡BK'});
  if(c.pullback_detected && sc(c.pullback_score)>=0.60) tags.push({cls:'pullback',txt:'🔄눌림'});
  if(c.ict_bullish_count>=3) tags.push({cls:'ict',txt:'🎯ICT'+c.ict_bullish_count});
  if(c.stream_ignition) tags.push({cls:'stream',txt:'⚡점화'});
  else if(c.stream_fresh && sc(c.stream_score)>=0.5) tags.push({cls:'stream',txt:'⚡활성'});
  if(c.stream_reversal) tags.push({cls:'reversal',txt:'↩반전'});
  if(c.orderbook_ready) tags.push({cls:'ob',txt:'🟢OB'});
  if(c.micro_exhausted) tags.push({cls:'exhaust',txt:'⚠소진'});
  return tags;
}

function passesFilter(c){
  if(_filter==='all') return true;
  if(_filter==='offense') return String(c.bias||'').toLowerCase()==='offense';
  if(_filter==='defense') return String(c.bias||'').toLowerCase()==='defense';
  if(_filter==='breakout') return c.breakout_confirmed || c.breakout_partial;
  if(_filter==='pullback') return c.pullback_detected && sc(c.pullback_score)>=0.50;
  if(_filter==='ict') return (c.ict_bullish_count||0)>=2;
  if(_filter==='stream') return c.stream_ignition || (c.stream_fresh && sc(c.stream_score)>=0.4);
  return true;
}

// ── 정렬 ──
function sortBy(key){
  if(_sortKey===key){ _sortAsc=!_sortAsc; }
  else { _sortKey=key; _sortAsc=(key==='market'||key==='bias'||key==='rank'); }
  renderTable();
}
function sortedData(){
  return _data.slice().sort(function(a,b){
    var va, vb;
    if(_sortKey==='market'){ va=sym(a.market); vb=sym(b.market); return _sortAsc?(va>vb?1:-1):(vb>va?1:-1); }
    if(_sortKey==='bias'){ va=String(a.bias||''); vb=String(b.bias||''); return _sortAsc?(va>vb?1:-1):(vb>va?1:-1); }
    if(_sortKey==='rsi'){ va=rsi(a.rsi); vb=rsi(b.rsi); }
    else { va=sc(a[_sortKey]); vb=sc(b[_sortKey]); }
    return _sortAsc?(va-vb):(vb-va);
  });
}

// ── 필터 ──
function setFilter(f, btn){
  _filter=f;
  document.querySelectorAll('.chip').forEach(function(c){ c.classList.remove('active'); });
  btn.classList.add('active');
  renderTable();
}

// ── 테이블 렌더 ──
function renderTable(){
  var sorted = sortedData();
  var body = document.getElementById('scan-body');
  var rows = '';
  var visCount = 0;
  sorted.forEach(function(c, idx){
    var rank = idx+1;
    var show = passesFilter(c);
    if(show) visCount++;
    var rkCls = rank===1?'rank-1':rank===2?'rank-2':rank===3?'rank-3':'';
    var hidCls = show?'':' hidden';
    var rkBadgeCls = rank===1?'r1':rank===2?'r2':rank===3?'r3':'rN';
    var chg = c.price_change_pct!=null?sc(c.price_change_pct):sc(c.change_rate)*100;
    var chgCls = chg>0.3?'pos':chg<-0.3?'neg':'flat';
    var chgTxt = pctText(chg);
    var rsiV = rsi(c.rsi);
    var statuses = getStatuses(c);
    var statusHtml = statuses.map(function(s){ return '<span class="sbadge '+s.cls+'">'+s.txt+'</span>'; }).join('');
    var biasB = String(c.bias||'').toLowerCase();

    rows += '<tr class="'+rkCls+hidCls+'" data-market="'+c.market+'">'
      +'<td><div class="rank-badge '+rkBadgeCls+'">'+rank+'</div></td>'
      +'<td><div class="sym-cell">'
        +'<div><div class="sym-name">'+sym(c.market)+'</div>'
        +'<div class="sym-pair" style="display:flex;gap:4px;align-items:center">'
          +'<span style="color:var(--muted);font-size:.65rem">KRW</span>'
          +'<span class="sym-change '+chgCls+'">'+chgTxt+'</span>'
        +'</div></div>'
      +'</div></td>'
      +'<td><div class="price-cell">'+fmtPrice(c.current_price||c.trade_price)+'</div><div class="price-sub">KRW · 실시간</div></td>'
      +'<td>'+miniChart(c)+'</td>'
      +'<td>'+scoreBar(c.combined_score,'linear-gradient(90deg,var(--blue),var(--green))')+'</td>'
      +'<td>'+scoreBar(c.signal_score, scoreColor(sc(c.signal_score)))+'</td>'
      +'<td>'+scoreBar(c.micro_score,  scoreColor(sc(c.micro_score)))+'</td>'
      +'<td>'+scoreBar(c.orderbook_score, scoreColor(sc(c.orderbook_score)))+'</td>'
      +'<td>'+scoreBar(c.stream_score, '#ffd250')+'</td>'
      +'<td><span class="rsi-val '+rsiCls(rsiV)+'">'+(isNaN(rsiV)?'—':rsiV.toFixed(0))+'</span></td>'
      +'<td><span style="font-family:var(--mono);font-size:.78rem">'+sc(c.vol_ratio).toFixed(1)+'x</span></td>'
      +'<td><span style="font-family:var(--mono);font-size:.78rem;color:var(--muted)">'+sc(c.atr_pct).toFixed(2)+'%</span></td>'
      +'<td><span class="bias-tag '+biasB+'">'+biasKo(biasB)+'</span></td>'
      +'<td><div class="badge-row">'+statusHtml+'</div></td>'
      +'</tr>';
  });

  body.innerHTML = rows || '<tr><td colspan="14" style="text-align:center;padding:24px;color:var(--muted)">조건에 맞는 코인 없음</td></tr>';

  // 정렬 헤더 표시
  document.querySelectorAll('th').forEach(function(th){ th.className=''; });
  var ths = document.querySelectorAll('thead th');
  var keyMap = {rank:0,market:1,current_price:2,sparkline_change_pct:3,combined_score:4,signal_score:5,micro_score:6,orderbook_score:7,stream_score:8,rsi:9,vol_ratio:10,atr_pct:11,bias:12};
  var idx2 = keyMap[_sortKey];
  if(idx2!==undefined) ths[idx2].className = _sortAsc?'sorted-asc':'sorted-desc';
  document.getElementById('sort-label').textContent = (_sortKey||'combined_score') + (_sortAsc?' ↑':' ↓');
}

// ── 자동발견 섹션 ──
function discCard(c, tagsHtml){
  return '<div class="disc-card" data-market="'+String(c.market||'').replace(/"/g,'&quot;')+'" onclick="highlightRow(this.dataset.market)">'
    +'<div class="disc-card-sym">'+sym(c.market)+'</div>'
    +'<div class="disc-card-score">'+sc(c.combined_score).toFixed(3)+' combined</div>'
    +'<div class="disc-card-tags">'+tagsHtml+'</div>'
    +'</div>';
}

function renderDiscovery(){
  var bkList=[], pbList=[], ictList=[], siList=[];
  _data.forEach(function(c){
    if(c.breakout_confirmed) bkList.push(c);
    if(c.pullback_detected && sc(c.pullback_score)>=0.55) pbList.push(c);
    if((c.ict_bullish_count||0)>=3) ictList.push(c);
    if(c.stream_ignition) siList.push(c);
  });
  function renderList(id, cntId, list, tagsF){
    var el=document.getElementById(id), cnt=document.getElementById(cntId);
    cnt.textContent=list.length;
    if(!list.length){ el.innerHTML='<div style="font-size:.75rem;color:var(--muted);padding:6px 0">해당 없음</div>'; return; }
    el.innerHTML = list.slice(0,8).map(function(c){ return discCard(c, tagsF(c)); }).join('');
  }
  renderList('disc-bk','disc-bk-cnt',bkList,function(c){
    return '<span class="sbadge breakout">BK'+c.breakout_count+'/4</span>'
      +'<span class="sbadge ob" style="font-size:.6rem">vol '+sc(c.vol_ratio).toFixed(1)+'x</span>';
  });
  renderList('disc-pb','disc-pb-cnt',pbList,function(c){
    return '<span class="sbadge pullback">눌림 '+sc(c.pullback_score).toFixed(2)+'</span>';
  });
  renderList('disc-ict','disc-ict-cnt',ictList,function(c){
    return '<span class="sbadge ict">'+String(c.ict_structure||'').slice(0,8)+'</span>'
      +'<span class="sbadge ict" style="font-size:.6rem">ICT'+c.ict_bullish_count+'</span>';
  });
  renderList('disc-si','disc-si-cnt',siList,function(c){
    return '<span class="sbadge stream">⚡'+sc(c.stream_move_15s_pct).toFixed(3)+'%/15s</span>';
  });
}

// ── 시장 개요 ──
function renderMarketBar(meta){
  var dirEl = document.getElementById('mb-dir');
  var dir = String(meta.direction_bias||'balanced').toLowerCase();
  dirEl.textContent = dir==='offense'?'📈 공세':dir==='defense'?'📉 방어':'⚖ 균형';
  dirEl.className = 'mbar-val '+(dir==='offense'?'bull':dir==='defense'?'bear':'neutral');
  document.getElementById('mb-cnt').textContent = meta.scanned_count+'개';

  var offCnt = _data.filter(function(c){ return String(c.bias||'').toLowerCase()==='offense'; }).length;
  var bkCnt  = _data.filter(function(c){ return c.breakout_confirmed; }).length;
  var pbCnt  = _data.filter(function(c){ return c.pullback_detected && sc(c.pullback_score)>=0.55; }).length;
  var siCnt  = _data.filter(function(c){ return c.stream_ignition; }).length;

  var mbOff = document.getElementById('mb-off');
  mbOff.textContent = offCnt+'개';
  mbOff.className = 'mbar-val '+(offCnt>0?'bull':'neutral');

  var mbBk = document.getElementById('mb-bk');
  mbBk.textContent = bkCnt+'개';
  mbBk.className = 'mbar-val '+(bkCnt>0?'bull':'neutral');

  var mbPb = document.getElementById('mb-pb');
  mbPb.textContent = pbCnt+'개';
  mbPb.className = 'mbar-val '+(pbCnt>0?'bull':'neutral');

  var mbSi = document.getElementById('mb-si');
  mbSi.textContent = siCnt+'개';
  mbSi.className = 'mbar-val '+(siCnt>0?'bull':'neutral');
}

function highlightRow(market){
  document.querySelectorAll('#scan-body tr').forEach(function(r){ r.style.outline=''; });
  var el = document.querySelector('[data-market="'+market+'"]');
  if(el){ el.style.outline='1px solid var(--blue)'; el.scrollIntoView({behavior:'smooth',block:'center'}); }
}

function toKST(isoStr){
  if(!isoStr) return '—';
  try{
    var d=new Date(isoStr);
    return d.toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }catch(e){ return isoStr; }
}

// ── 메인 로드 ──
async function loadScanner(){
  try{
    var res = await fetch('/scanner-data');
    var json = await res.json();
    _data = json.candidates || [];
    document.getElementById('hdr-sub').textContent =
      toKST(json.updated_at)+' KST · '+_data.length+'개 코인 스캔';
    renderMarketBar(json);
    renderTable();
    renderDiscovery();
    document.getElementById('rdot').style.background='var(--green)';
  }catch(e){
    document.getElementById('hdr-sub').textContent = '로딩 실패: '+e.message;
    document.getElementById('rdot').style.background='var(--red)';
  }
}

loadScanner();
setInterval(loadScanner, 10000);
</script>
</body>
</html>"""


@app.get("/scanner", response_class=HTMLResponse)
def scanner_page() -> str:
    return _scanner_html()


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return _embedded_dashboard_html()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)

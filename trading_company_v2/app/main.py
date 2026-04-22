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
            "leaders": ((state.market_snapshot.get("gap_candidates") or state.market_snapshot.get("stock_leaders") or []) if state.market_snapshot else [])[:3],
            "latest_order": latest_korea_order,
        },
        "us": {
            "title": "미국주식 데스크",
            "bias": us_view.get("desk_bias", "n/a"),
            "action": us_plan.get("action", "n/a"),
            "focus": us_plan.get("focus", "미국 플랜 없음"),
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
    return {
        "company_name": settings.company_name,
        "operator_name": settings.operator_name,
        "access": local_access_urls(),
        "state": state.model_dump(),
        "dashboard": _build_dashboard_payload(state),
        "broker_live_health": broker_live_health(),
        "live_readiness_checklist": live_readiness_checklist(),
        "upbit_live_pilot": upbit_live_pilot(),
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

    return {
        "updated_at": state.updated_at,
        "overall": overall,
        "block_count": block_count,
        "warn_count": warn_count,
        "execution_mode": mode,
        "execution_summary": execution_summary,
        "entry_block_summary": _entry_block_summary(state),
        "checklist": checklist,
        "next_actions": next_actions[:5],
        "notes": (state.notes or [])[-8:],
    }


@app.get("/diagnostics/upbit-live-pilot")
def upbit_live_pilot() -> dict:
    state = load_company_state()
    broker_health = broker_live_health()
    readiness = live_readiness_checklist()
    upbit = broker_health.get("upbit", {}) or {}
    execution_summary = broker_health.get("execution_summary", {}) or {}

    blockers: list[str] = []
    cautions: list[str] = []

    if settings.live_capital_krw <= 0:
        blockers.append("LIVE_CAPITAL_KRW is not configured.")
    if not bool(upbit.get("configured")):
        blockers.append("Upbit API credentials are missing.")
    if not bool(settings.upbit_allow_live):
        blockers.append("UPBIT_ALLOW_LIVE is false.")
    if str(state.execution_mode or "paper") != "upbit_live":
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
    return {
        "updated_at": state.updated_at,
        "go_live_ready": go_live_ready,
        "broker": "upbit",
        "execution_mode": state.execution_mode,
        "pilot_cap_krw": pilot_cap_krw,
        "pilot_guardrails": {
            "max_order_krw": settings.upbit_pilot_max_krw,
            "single_order_only": settings.upbit_pilot_single_order_only,
        },
        "suggested_sequence": [
            "Verify balances and credentials with Upbit health check.",
            "Set EXECUTION_MODE=upbit_live only after readiness blockers clear.",
            "Run one tiny-size crypto entry/exit cycle first.",
            "Confirm order lookup, fill state, and position sync before scaling.",
        ],
        "blockers": blockers,
        "cautions": cautions[:5],
        "upbit_health": upbit,
        "entry_block_summary": _entry_block_summary(state),
        "readiness_overall": readiness.get("overall", "blocked"),
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


def _embedded_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#09111f">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Trading Company V2">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/app-icon.svg" type="image/svg+xml">
  <title>Trading Company V2</title>
  <style>
    :root {
      --bg:#09111f; --surface:rgba(10,19,35,.84); --surface2:rgba(19,34,57,.92); --border:rgba(141,177,199,.18);
      --text:#eef6ff; --muted:#97aabf; --green:#67e8a5; --red:#ff7c7c; --blue:#6bc7ff; --yellow:#ffd36e; --orange:#ff9a62;
      --font:'Aptos','Bahnschrift','Malgun Gothic',sans-serif; --mono:'IBM Plex Mono','D2Coding','Consolas',monospace;
    }
    *{box-sizing:border-box} html,body{margin:0;padding:0}
    body{min-height:100vh;color:var(--text);font-family:var(--font);background:
      radial-gradient(circle at 10% 0%, rgba(107,199,255,.16), transparent 34%),
      radial-gradient(circle at 92% 18%, rgba(103,232,165,.08), transparent 30%),
      linear-gradient(180deg, #07101d 0%, #09111f 38%, #09111f 100%); line-height:1.45}
    .app{position:relative;max-width:1480px;margin:0 auto;padding:20px 18px 72px}
    .app-glow{position:fixed;left:50%;transform:translateX(-50%);width:900px;height:420px;pointer-events:none;z-index:0;background:radial-gradient(ellipse at top, rgba(107,199,255,.10) 0%, transparent 70%)}
    .hero-shell,.overview-card,.signal-panel,.panel,.access-card,.priority-chip,.check-item,.broker-card,.desk-row,.metric-card{backdrop-filter:blur(20px)}
    .hero-shell{position:relative;z-index:1;display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding:24px;border:1px solid var(--border);border-radius:28px;background:linear-gradient(180deg, rgba(19,34,57,.94), rgba(10,19,35,.82));margin-bottom:16px;overflow:hidden}
    .hero-shell::after{content:'';position:absolute;inset:auto -60px -80px auto;width:240px;height:240px;border-radius:50%;background:radial-gradient(circle, rgba(107,199,255,.18), transparent 68%)}
    .hero-copy{max-width:760px;position:relative;z-index:1}.hero-kicker{font-size:.72rem;text-transform:uppercase;letter-spacing:.16em;color:var(--blue);margin-bottom:10px}
    .hero-title-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.hero-title{font-size:clamp(1.8rem,3vw,3rem);font-weight:800;line-height:1.05;letter-spacing:-.03em;margin:0}
    .hero-pill{display:inline-flex;align-items:center;gap:8px;padding:7px 12px;border-radius:999px;border:1px solid rgba(107,199,255,.24);background:rgba(107,199,255,.10);font-size:.78rem;color:var(--blue);font-weight:700}
    .hero-pill::before{content:'';width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 18px currentColor}
    .hero-summary{margin-top:10px;font-size:.95rem;color:#d7e7f7;max-width:700px}.hero-meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:16px}
    .access-card{min-width:0;padding:14px 16px;border-radius:16px;border:1px solid rgba(141,177,199,.16);background:rgba(7,16,29,.64)}
    .access-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin-bottom:8px}
    .access-url{font-family:var(--mono);font-size:.82rem;color:var(--text);word-break:break-all}
    .hero-actions{position:relative;z-index:1;display:flex;flex-direction:column;align-items:flex-end;gap:12px;min-width:250px}
    .hero-action-row{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}
    .btn-cycle,.btn-ghost{appearance:none;border:none;cursor:pointer;border-radius:12px;padding:10px 16px;font-weight:700}
    .btn-cycle{background:linear-gradient(135deg, rgba(107,199,255,.22), rgba(107,199,255,.08));color:var(--blue);border:1px solid rgba(107,199,255,.30)}
    .btn-ghost{background:rgba(151,170,191,.10);color:var(--text);border:1px solid rgba(151,170,191,.24)} .btn-cycle:disabled{opacity:.55;cursor:not-allowed}
    .meta-stamp{font-family:var(--mono);font-size:.78rem;color:var(--muted)}
    .hero-overview{position:relative;z-index:1;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
    .overview-card{padding:18px;border-radius:18px;border:1px solid var(--border);background:rgba(10,19,35,.76)}
    .ov-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin-bottom:8px}.ov-value{font-size:1.28rem;font-weight:800}.ov-sub{margin-top:6px;font-size:.78rem;color:var(--muted)}
    .tone-ok{color:var(--green)!important}.tone-warn{color:var(--yellow)!important}.tone-risk{color:var(--orange)!important}.tone-danger{color:var(--red)!important}.tone-muted{color:var(--muted)!important}.tone-blue{color:var(--blue)!important}
    .overview-card.tone-ok,.signal-panel.tone-ok,.broker-card.tone-ok{border-color:rgba(103,232,165,.28)} .overview-card.tone-warn,.signal-panel.tone-warn,.broker-card.tone-warn{border-color:rgba(255,211,110,.28)}
    .overview-card.tone-risk,.signal-panel.tone-risk,.broker-card.tone-risk{border-color:rgba(255,154,98,.30)} .overview-card.tone-danger,.signal-panel.tone-danger,.broker-card.tone-danger{border-color:rgba(255,124,124,.28)}
    .signal-grid{position:relative;z-index:1;display:grid;grid-template-columns:1.1fr .9fr;gap:14px;margin-bottom:16px}
    .signal-panel,.panel{border-radius:24px;border:1px solid var(--border);background:linear-gradient(180deg, rgba(10,19,35,.90), rgba(10,19,35,.72));overflow:hidden}
    .panel-head{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:16px 18px 12px;border-bottom:1px solid var(--border)}
    .panel-title{font-size:.78rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);font-weight:700}.panel-value{font-family:var(--mono);font-size:.82rem;color:var(--blue)}
    .priority-wrap{display:flex;flex-wrap:wrap;gap:10px;padding:16px 18px 6px}.priority-chip{display:inline-flex;align-items:center;gap:8px;padding:9px 12px;border-radius:999px;background:rgba(151,170,191,.10);border:1px solid rgba(151,170,191,.18);font-size:.76rem;color:var(--text)}
    .priority-chip::before{content:'';width:8px;height:8px;border-radius:50%;background:currentColor}
    .metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:10px 18px 18px}.metric-card{padding:14px;border-radius:16px;background:rgba(19,34,57,.78);border:1px solid rgba(141,177,199,.12)}
    .metric-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.10em;color:var(--muted)} .metric-value{margin-top:6px;font-size:1.12rem;font-weight:800} .metric-sub{margin-top:4px;font-size:.76rem;color:var(--muted)}
    .readiness-list,.broker-list,.desk-list,.insights-list,.journal-list{padding:8px 18px 18px}
    .check-item,.broker-card,.desk-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:12px 14px;margin-top:10px;border-radius:16px;background:rgba(19,34,57,.72);border:1px solid rgba(141,177,199,.12)}
    .check-main,.broker-main,.desk-main{min-width:0;flex:1}.check-title,.broker-title,.desk-title{font-size:.82rem;font-weight:700;color:var(--text)} .check-detail,.broker-detail,.desk-detail{margin-top:4px;font-size:.76rem;color:var(--muted)}
    .state-badge{display:inline-flex;align-items:center;gap:8px;padding:7px 11px;border-radius:999px;font-size:.73rem;font-weight:700;border:1px solid rgba(151,170,191,.22);background:rgba(151,170,191,.10);white-space:nowrap}
    .state-badge.pass{color:var(--green);border-color:rgba(103,232,165,.26);background:rgba(103,232,165,.10)} .state-badge.warn{color:var(--yellow);border-color:rgba(255,211,110,.26);background:rgba(255,211,110,.10)} .state-badge.block{color:var(--red);border-color:rgba(255,124,124,.26);background:rgba(255,124,124,.10)}
    .desk-row{align-items:center}.desk-tag{width:56px;flex-shrink:0;font-size:.70rem;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;font-weight:700}
    .action-pill{display:inline-flex;align-items:center;justify-content:center;padding:5px 9px;border-radius:999px;font-size:.72rem;font-weight:700;flex-shrink:0}
    .action-pill.buy{background:rgba(103,232,165,.15);color:var(--green)} .action-pill.sell{background:rgba(255,124,124,.15);color:var(--red)} .action-pill.watch{background:rgba(151,170,191,.12);color:var(--muted)} .action-pill.probe{background:rgba(107,199,255,.12);color:var(--blue)}
    .desk-size{font-family:var(--mono);font-size:.76rem;color:var(--blue)} .content-grid{position:relative;z-index:1;display:grid;grid-template-columns:1.06fr .94fr;gap:14px}
    .col{display:flex;flex-direction:column;gap:14px}.panel-body{padding:0 18px 18px}.table-wrap{overflow:auto}.pos-table{width:100%;border-collapse:collapse;font-size:.80rem}
    .pos-table th{padding:10px 12px;text-align:left;font-size:.68rem;text-transform:uppercase;letter-spacing:.10em;color:var(--muted);border-bottom:1px solid var(--border)} .pos-table td{padding:12px;border-bottom:1px solid rgba(141,177,199,.10)}
    .pos-table tr:last-child td{border-bottom:none}.symbol-cell{font-family:var(--mono);font-weight:700}.desk-chip{display:inline-flex;padding:4px 8px;border-radius:999px;background:rgba(107,199,255,.12);color:var(--blue);font-size:.68rem;font-weight:700}
    .empty-msg{padding:18px 0;color:var(--muted);font-size:.82rem;text-align:center}.badge{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:24px;padding:0 8px;border-radius:999px;background:rgba(107,199,255,.12);color:var(--blue);font-size:.72rem;font-weight:700}
    #equity-svg{display:block;width:100%;height:170px} .insight-row{display:grid;grid-template-columns:minmax(0,1fr) 100px 42px;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid rgba(141,177,199,.08)}
    .insight-row:last-child,.journal-row:last-child{border-bottom:none}.ins-name{font-size:.82rem;color:var(--text)} .ins-bar{width:100%;height:6px;border-radius:999px;background:rgba(141,177,199,.12);overflow:hidden} .ins-bar-fill{height:100%;border-radius:999px}
    .ins-score{font-family:var(--mono);text-align:right;font-size:.80rem;font-weight:700}.journal-row{padding:10px 0;border-bottom:1px solid rgba(141,177,199,.08);font-size:.78rem;color:var(--muted)} .journal-time{font-family:var(--mono);color:var(--blue);margin-right:8px}
    .pos{color:var(--green)!important}.neg{color:var(--red)!important}.neutral{color:var(--text)!important}
    @media (max-width:1180px){.signal-grid,.content-grid{grid-template-columns:1fr}.hero-shell{flex-direction:column}.hero-actions{align-items:flex-start}}
    @media (max-width:820px){.hero-overview{grid-template-columns:repeat(2,1fr)}.metric-grid{grid-template-columns:repeat(2,1fr)}}
    @media (max-width:560px){.app{padding:14px 12px 48px}.hero-shell{padding:18px}.hero-title{font-size:1.7rem}.hero-overview,.metric-grid,.hero-meta{grid-template-columns:1fr}.hero-action-row{width:100%}.btn-cycle,.btn-ghost{width:100%}.check-item,.broker-card,.desk-row{flex-direction:column;align-items:flex-start}.insight-row{grid-template-columns:1fr}.panel-head{align-items:flex-start;flex-direction:column}.panel-value,.meta-stamp{font-size:.74rem}.access-url{font-size:.76rem}}
  </style>
</head>
<body>
<div class="app">
  <div class="app-glow"></div>
  <section class="hero-shell">
    <div class="hero-copy">
      <div class="hero-kicker">트레이딩 관제 레이어</div>
      <div class="hero-title-row">
        <h1 class="hero-title">Trading Company V2</h1>
        <span class="hero-pill" id="status-pill">동기화 중</span>
      </div>
      <div class="hero-summary" id="hero-summary">실전 준비도, 브로커 상태, 운영 시그널을 한 화면에서 확인하세요.</div>
      <div class="hero-meta" id="access-grid"><div class="access-card"><div class="access-label">접속</div><div class="access-url">로딩 중...</div></div></div>
    </div>
    <div class="hero-actions">
      <div class="hero-action-row">
        <button class="btn-cycle" id="cycle-btn" onclick="runCycle()">사이클 실행</button>
        <button class="btn-ghost" onclick="loadData()">새로고침</button>
      </div>
      <div class="meta-stamp">마지막업데이트 <span id="update-time">--:--</span></div>
      <div class="meta-stamp">다음실행 <span id="next-run">--</span></div>
    </div>
  </section>
  <section class="hero-overview">
    <div class="overview-card" id="ov-stance"><div class="ov-label">스탠스</div><div class="ov-value" id="ov-stance-val">--</div><div class="ov-sub">위험 포지션</div></div>
    <div class="overview-card" id="ov-regime"><div class="ov-label">시장국면</div><div class="ov-value" id="ov-regime-val">--</div><div class="ov-sub">시장 상태</div></div>
    <div class="overview-card" id="ov-exposure"><div class="ov-label">보유포지션</div><div class="ov-value" id="ov-exposure-val">--</div><div class="ov-sub" id="ov-entries">--</div></div>
    <div class="overview-card" id="ov-ops"><div class="ov-label">운영</div><div class="ov-value" id="ov-ops-val">--</div><div class="ov-sub" id="ov-ops-sub">런타임 상태</div></div>
  </section>
  <section class="signal-grid">
    <div class="signal-panel" id="signal-panel">
      <div class="panel-head"><div class="panel-title">실행 시그널 덱</div><div class="panel-value" id="exec-mode">모의투자</div></div>
      <div class="priority-wrap" id="priority-wrap"><div class="priority-chip tone-muted">시그널 없음</div></div>
      <div class="metric-grid">
        <div class="metric-card"><div class="metric-label">실전주문</div><div class="metric-value" id="metric-live">0</div><div class="metric-sub">활성 로그</div></div>
        <div class="metric-card"><div class="metric-label">대기중</div><div class="metric-value" id="metric-pending">0</div><div class="metric-sub">브로커 대기</div></div>
        <div class="metric-card"><div class="metric-label">부분체결</div><div class="metric-value" id="metric-partial">0</div><div class="metric-sub">검토 필요</div></div>
        <div class="metric-card"><div class="metric-label">미처리</div><div class="metric-value" id="metric-stale">0</div><div class="metric-sub">임계치 초과</div></div>
      </div>
      <div class="panel-head"><div class="panel-title">데스크 플랜</div><div class="panel-value" id="desk-caption">3 데스크</div></div>
      <div class="desk-list" id="desk-rows"></div>
    </div>
    <div class="signal-panel" id="readiness-panel">
      <div class="panel-head"><div class="panel-title">실전 준비도</div><div class="panel-value" id="readiness-overall">차단</div></div>
      <div class="readiness-list" id="readiness-list"></div>
      <div class="panel-head"><div class="panel-title">브로커 상태</div><div class="panel-value" id="broker-caption">상태</div></div>
      <div class="broker-list" id="broker-list"></div>
    </div>
  </section>
  <section class="content-grid">
    <div class="col">
      <div class="panel">
        <div class="panel-head"><div class="panel-title">손익 현황</div><div class="panel-value" id="performance-caption">일간 요약</div></div>
        <div class="metric-grid">
          <div class="metric-card"><div class="metric-label">실현손익</div><div class="metric-value" id="sc-realized">--</div><div class="metric-sub" id="sc-realized-krw">--</div></div>
          <div class="metric-card"><div class="metric-label">미실현손익</div><div class="metric-value" id="sc-unrealized">--</div><div class="metric-sub" id="sc-unrealized-krw">--</div></div>
          <div class="metric-card"><div class="metric-label">승률</div><div class="metric-value" id="sc-winrate">--</div><div class="metric-sub" id="sc-trades">--</div></div>
          <div class="metric-card"><div class="metric-label">실전자본</div><div class="metric-value" id="sc-capital">--</div><div class="metric-sub" id="sc-capital-base">--</div></div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><div class="panel-title">보유포지션</div><div class="badge" id="pos-count">0</div></div>
        <div class="panel-body table-wrap" id="positions-body"><div class="empty-msg">보유 포지션 없음</div></div>
      </div>
      <div class="panel">
        <div class="panel-head"><div class="panel-title">최근 청산내역</div><div class="panel-value">최근 6건</div></div>
        <div class="panel-body table-wrap" id="trades-body"><div class="empty-msg">청산 거래 없음</div></div>
      </div>
    </div>
    <div class="col">
      <div class="panel">
        <div class="panel-head"><div class="panel-title">수익곡선</div><div class="panel-value">시작 대비 현재</div></div>
        <div class="panel-body"><svg id="equity-svg" viewBox="0 0 400 170" preserveAspectRatio="none"></svg></div>
      </div>
      <div class="panel">
        <div class="panel-head"><div class="panel-title">인사이트 점수</div><div class="badge" id="insight-score">--</div></div>
        <div class="panel-body insights-list" id="insights-body"><div class="empty-msg">에이전트 인사이트 없음</div></div>
      </div>
      <div class="panel">
        <div class="panel-head"><div class="panel-title">운영 저널</div><div class="panel-value">최근 노트</div></div>
        <div class="panel-body journal-list" id="journal-body"><div class="empty-msg">노트 없음</div></div>
      </div>
    </div>
  </section>
</div>
<script>
  function fmtPct(v){var n=parseFloat(v)||0;return (n>=0?'+':'')+n.toFixed(2)+'%';}
  function fmtKrw(v,sign){var n=parseInt(v)||0;var prefix=sign?(n>=0?'+':''):'';return prefix+'KRW '+Math.abs(n).toLocaleString('ko-KR');}
  function pctCls(v){var n=parseFloat(v)||0;return n>0?'pos':n<0?'neg':'neutral';}
  function toneClass(label){var txt=String(label||'').toLowerCase(); if(txt.indexOf('blocked')>=0||txt.indexOf('block')>=0||txt.indexOf('stale')>=0) return 'tone-risk'; if(txt.indexOf('warning')>=0||txt.indexOf('warn')>=0||txt.indexOf('partial')>=0) return 'tone-warn'; if(txt.indexOf('ready')>=0||txt.indexOf('pass')>=0||txt.indexOf('stable')>=0||txt.indexOf('trend')>=0||txt.indexOf('offense')>=0) return 'tone-ok'; if(txt.indexOf('stress')>=0) return 'tone-danger'; if(txt.indexOf('range')>=0||txt.indexOf('defense')>=0) return 'tone-warn'; return 'tone-blue';}
  function actionCls(a){var s=String(a||'').toLowerCase(); if(!s) return 'watch'; if(s.indexOf('attack')>=0||s.indexOf('buy')>=0||s.indexOf('probe_long')>=0) return 'buy'; if(s.indexOf('reduce')>=0||s.indexOf('sell')>=0||s.indexOf('preservation')>=0) return 'sell'; if(s.indexOf('probe')>=0||s.indexOf('selective')>=0) return 'probe'; return 'watch';}
  function renderAccess(access){var grid=document.getElementById('access-grid'); var cards=[]; if(access&&access.local_url){cards.push('<div class=\"access-card\"><div class=\"access-label\">로컬 URL</div><div class=\"access-url\">'+access.local_url+'</div></div>');} if(access&&access.lan_url){cards.push('<div class=\"access-card\"><div class=\"access-label\">LAN URL</div><div class=\"access-url\">'+access.lan_url+'</div></div>');} if(access&&access.public_url){cards.push('<div class=\"access-card\"><div class=\"access-label\">'+(access.public_label||'공개 URL')+'</div><div class=\"access-url\">'+access.public_url+'</div></div>');} if(!cards.length){cards.push('<div class=\"access-card\"><div class=\"access-label\">접속</div><div class=\"access-url\">접속 URL 없음</div></div>');} grid.innerHTML=cards.join('');}
  function renderPrioritySignals(readiness,summary,brokerHealth,blockSummary){var signals=[]; var overall=String((readiness||{}).overall||''); if(blockSummary&&blockSummary.blocked){signals.push({text:String(blockSummary.detail||blockSummary.headline||'실행 차단'),tone:'tone-risk'});} else if(overall==='blocked') signals.push({text:'준비도 점검으로 실행 차단',tone:'tone-risk'}); if(Number((summary||{}).stale_count||0)>0) signals.push({text:'미처리 실전 주문: '+summary.stale_count,tone:'tone-risk'}); if(Number((summary||{}).partial_count||0)>0) signals.push({text:'부분체결 검토 필요: '+summary.partial_count,tone:'tone-warn'}); if(Number((summary||{}).pending_count||0)>0) signals.push({text:'대기중 실전 주문: '+summary.pending_count,tone:'tone-warn'}); if((brokerHealth||{}).upbit&&brokerHealth.upbit.configured===false&&(brokerHealth||{}).kis&&brokerHealth.kis.configured===false) signals.push({text:'실전 브로커 인증 미설정',tone:'tone-muted'}); if(!signals.length) signals.push({text:'실행 경로 안정',tone:'tone-ok'}); document.getElementById('priority-wrap').innerHTML=signals.map(function(item){return '<div class=\"priority-chip '+item.tone+'\">'+item.text+'</div>';}).join(''); document.getElementById('signal-panel').className='signal-panel '+(signals[0].tone||'tone-blue');}
  function renderOverview(state,dash,readiness){var exposure=((dash||{}).exposure||{}); var ops=((dash||{}).ops_flags||{}); var stance=String((state||{}).stance||'--'); var regime=String((state||{}).regime||'--'); var gross=Number(exposure.gross_open_notional_pct||0); var overall=String((readiness||{}).overall||'caution'); document.getElementById('ov-stance-val').textContent=stance; document.getElementById('ov-regime-val').textContent=regime; document.getElementById('ov-exposure-val').textContent=Math.round(gross*100)+'%'; document.getElementById('ov-entries').textContent=exposure.allow_new_entries?'신규 진입 허용':'신규 진입 차단'; document.getElementById('ov-ops-val').textContent=String(ops.severity||overall).toUpperCase(); document.getElementById('ov-stance').className='overview-card '+toneClass(stance); document.getElementById('ov-regime').className='overview-card '+toneClass(regime); document.getElementById('ov-exposure').className='overview-card '+(gross>=0.8?'tone-warn':'tone-ok'); document.getElementById('ov-ops').className='overview-card '+toneClass(ops.severity||overall); document.getElementById('ov-stance-val').className='ov-value '+toneClass(stance); document.getElementById('ov-regime-val').className='ov-value '+toneClass(regime); document.getElementById('ov-exposure-val').className='ov-value '+(gross>=0.8?'tone-warn':'tone-ok'); document.getElementById('ov-ops-val').className='ov-value '+toneClass(ops.severity||overall);}
  function renderMetrics(dash,readiness){var perf=(dash||{}).performance||{}; var cap=(dash||{}).capital||{}; var exec=(dash||{}).execution_summary||{}; document.getElementById('metric-live').textContent=String(exec.live_count||0); document.getElementById('metric-pending').textContent=String(exec.pending_count||0); document.getElementById('metric-partial').textContent=String(exec.partial_count||0); document.getElementById('metric-stale').textContent=String(exec.stale_count||0); document.getElementById('metric-pending').className='metric-value '+((exec.pending_count||0)>0?'tone-warn':'tone-ok'); document.getElementById('metric-partial').className='metric-value '+((exec.partial_count||0)>0?'tone-warn':'tone-ok'); document.getElementById('metric-stale').className='metric-value '+((exec.stale_count||0)>0?'tone-risk':'tone-ok'); document.getElementById('exec-mode').textContent=(readiness&&readiness.execution_mode)||'모의투자'; document.getElementById('readiness-overall').textContent=String((readiness||{}).overall||'caution').toUpperCase(); document.getElementById('readiness-overall').className='panel-value '+toneClass((readiness||{}).overall); document.getElementById('sc-realized').textContent=fmtPct(perf.realized_pnl_pct); document.getElementById('sc-realized').className='metric-value '+pctCls(perf.realized_pnl_pct); document.getElementById('sc-realized-krw').textContent=fmtKrw(perf.realized_pnl_krw,true); document.getElementById('sc-unrealized').textContent=fmtPct(perf.unrealized_pnl_pct); document.getElementById('sc-unrealized').className='metric-value '+pctCls(perf.unrealized_pnl_pct); document.getElementById('sc-unrealized-krw').textContent=fmtKrw(perf.unrealized_pnl_krw,true); var winRate=Number(perf.win_rate||0); document.getElementById('sc-winrate').textContent=winRate.toFixed(1)+'%'; document.getElementById('sc-winrate').className='metric-value '+(winRate>=55?'pos':winRate<40?'neg':'neutral'); document.getElementById('sc-trades').textContent=String(perf.wins||0)+' 승 / '+String(perf.losses||0)+' 패 / 기대값 '+fmtPct(perf.expectancy_pct); document.getElementById('sc-capital').textContent=fmtKrw(cap.total_krw||0,false); document.getElementById('sc-capital-base').textContent='기준 '+fmtKrw(cap.base_krw||0,false); document.getElementById('performance-caption').textContent='실현 '+fmtPct(perf.realized_pnl_pct)+' / 미실현 '+fmtPct(perf.unrealized_pnl_pct);}
  function renderReadiness(readiness){var statusLabels={'pass':'통과','warn':'주의','block':'차단'}; var list=((readiness||{}).checklist||[]).slice(0,8); if(!list.length){document.getElementById('readiness-list').innerHTML='<div class=\"empty-msg\">준비도 데이터 없음</div>'; return;} document.getElementById('readiness-list').innerHTML=list.map(function(item){var st=item.status||'warn'; return '<div class=\"check-item\"><div class=\"check-main\"><div class=\"check-title\">'+(item.label||'--')+'</div><div class=\"check-detail\">'+(item.detail||'')+'</div></div><div class=\"state-badge '+st+'\">'+(statusLabels[st]||st.toUpperCase())+'</div></div>';}).join(''); document.getElementById('readiness-panel').className='signal-panel '+toneClass((readiness||{}).overall);}
  function renderBrokerHealth(health){var items=['upbit','kis']; var html=''; for(var i=0;i<items.length;i++){var key=items[i]; var item=(health||{})[key]||{}; var tone=item.configured===false?'tone-muted':item.balances_ok?'tone-ok':'tone-warn'; var stateText=item.configured===false?'미설정':item.balances_ok?'잔고 확인 완료':'점검 필요'; var detail='활성='+String(!!item.enabled)+' / 잔고='+String(item.balances_count||0)+(item.latest_order_state?' / 최근='+String(item.latest_order_state.request_status||'n/a'):'')+(item.latest_order_error?' / '+item.latest_order_error:''); html+='<div class=\"broker-card '+tone+'\"><div class=\"broker-main\"><div class=\"broker-title\">'+key.toUpperCase()+'</div><div class=\"broker-detail\">'+detail+'</div></div><div class=\"state-badge '+(item.configured===false?'warn':item.balances_ok?'pass':'warn')+'\">'+stateText+'</div></div>';} document.getElementById('broker-list').innerHTML=html||'<div class=\"empty-msg\">브로커 데이터 없음</div>'; document.getElementById('broker-caption').textContent='upbit / kis';}
  function renderDesks(desks){var items=[['크립토','crypto'],['한국주식','korea'],['미국주식','us']]; var html=''; for(var i=0;i<items.length;i++){var label=items[i][0], key=items[i][1], item=(desks||{})[key]||{}; html+='<div class=\"desk-row\"><div class=\"desk-tag\">'+label+'</div><div class=\"desk-main\"><div class=\"desk-title\">'+(item.title||key.toUpperCase())+'</div><div class=\"desk-detail\">'+(item.focus||'활성 플랜 없음')+'</div></div><div class=\"action-pill '+actionCls(item.action)+'\">'+(item.action||'watch')+'</div><div class=\"desk-size\">'+(item.size||'0.00x')+'</div></div>';} document.getElementById('desk-rows').innerHTML=html; document.getElementById('desk-caption').textContent=String(items.length)+' 데스크 모니터링';}
  function renderPositions(pos){if(!pos||!pos.length){document.getElementById('pos-count').textContent='0'; document.getElementById('positions-body').innerHTML='<div class=\"empty-msg\">보유 포지션 없음</div>'; return;} document.getElementById('pos-count').textContent=String(pos.length); var rows=''; for(var i=0;i<pos.length;i++){var p=pos[i], pnl=Number(p.unrealized_pnl_pct||0); rows+='<tr><td><span class=\"symbol-cell\">'+(p.symbol||'--')+'</span></td><td><span class=\"desk-chip\">'+(p.desk||'--')+'</span></td><td>'+Number(p.entry_price||0).toLocaleString('ko-KR')+'</td><td class=\"'+pctCls(pnl)+'\">'+fmtPct(pnl)+'</td><td>'+String(p.opened_at||'').slice(11,16)+'</td></tr>';} document.getElementById('positions-body').innerHTML='<table class=\"pos-table\"><thead><tr><th>종목</th><th>데스크</th><th>진입가</th><th>미실현손익</th><th>진입시각</th></tr></thead><tbody>'+rows+'</tbody></table>';}
  function renderTrades(closed){var items=(closed||[]).slice(0,6); if(!items.length){document.getElementById('trades-body').innerHTML='<div class=\"empty-msg\">청산 거래 없음</div>'; return;} var rows=''; for(var i=0;i<items.length;i++){var t=items[i], pnl=Number(t.pnl_pct||0); rows+='<tr><td><span class=\"symbol-cell\">'+(t.symbol||'--')+'</span></td><td><span class=\"desk-chip\">'+(t.desk||'--')+'</span></td><td class=\"'+pctCls(pnl)+'\">'+fmtPct(pnl)+'</td><td>'+(t.closed_reason||'--')+'</td><td>'+String(t.closed_at||'').slice(11,16)+'</td></tr>';} document.getElementById('trades-body').innerHTML='<table class=\"pos-table\"><thead><tr><th>종목</th><th>데스크</th><th>손익</th><th>사유</th><th>청산시각</th></tr></thead><tbody>'+rows+'</tbody></table>';}
  function renderInsights(runs){if(!runs||!runs.length){document.getElementById('insights-body').innerHTML='<div class=\"empty-msg\">에이전트 인사이트 없음</div>'; return;} document.getElementById('insights-body').innerHTML=runs.map(function(item){var score=Math.round((Number(item.score)||0)*100); var color=score>=75?'var(--green)':score>=55?'var(--blue)':'var(--red)'; return '<div class=\"insight-row\"><div class=\"ins-name\">'+(item.agent_name||item.name||'--')+'</div><div class=\"ins-bar\"><div class=\"ins-bar-fill\" style=\"width:'+score+'%;background:'+color+'\"></div></div><div class=\"ins-score '+(score>=75?'tone-ok':score>=55?'tone-blue':'tone-danger')+'\">'+score+'</div></div>';}).join('');}
  function renderJournal(notes){var items=(notes||[]).slice(0,8); if(!items.length){document.getElementById('journal-body').innerHTML='<div class=\"empty-msg\">노트 없음</div>'; return;} document.getElementById('journal-body').innerHTML=items.map(function(note){var txt=typeof note==='string'?note:JSON.stringify(note); var match=txt.match(/^(\\d\\d:\\d\\d)/); return match?'<div class=\"journal-row\"><span class=\"journal-time\">'+match[1]+'</span>'+txt.slice(match[1].length).trim()+'</div>':'<div class=\"journal-row\">'+txt+'</div>';}).join('');}
  function renderEquity(points){var svg=document.getElementById('equity-svg'); if(!points||points.length<2){svg.innerHTML='<text x=\"50%\" y=\"50%\" text-anchor=\"middle\" fill=\"#97aabf\" font-size=\"12\">수익 데이터 없음</text>'; return;} var width=400,height=170,pad=24,values=points.map(function(point){return Number(point.equity||0);}),min=Math.min.apply(null,values),max=Math.max.apply(null,values),range=max-min||1; var x=function(i){return pad+(i/(points.length-1))*(width-pad*2);}; var y=function(val){return pad+((max-val)/range)*(height-pad*2);}; var coords=points.map(function(point,i){return x(i).toFixed(1)+','+y(Number(point.equity||0)).toFixed(1);}).join(' '); var last=Number(points[points.length-1].equity||0), color=last>=100?'#67e8a5':'#ff7c7c', fill=x(0).toFixed(1)+','+height+' '+coords+' '+x(points.length-1).toFixed(1)+','+height; svg.innerHTML='<defs><linearGradient id=\"equity-grad\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\"><stop offset=\"0%\" stop-color=\"'+color+'\" stop-opacity=\".30\"/><stop offset=\"100%\" stop-color=\"'+color+'\" stop-opacity=\"0\"/></linearGradient></defs><polygon points=\"'+fill+'\" fill=\"url(#equity-grad)\"/><polyline points=\"'+coords+'\" fill=\"none\" stroke=\"'+color+'\" stroke-width=\"3\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><circle cx=\"'+x(points.length-1).toFixed(1)+'\" cy=\"'+y(last).toFixed(1)+'\" r=\"4.5\" fill=\"'+color+'\"/>';}
  async function loadData(){try{var dashboardRes=await fetch('/dashboard-data'); var healthRes=await fetch('/health'); var data=await dashboardRes.json(); var health=await healthRes.json(); var state=data.state||{}, dash=data.dashboard||{}, readiness=data.live_readiness_checklist||{}, brokerHealth=data.broker_live_health||{}, exec=dash.execution_summary||{}, ops=dash.ops_flags||{}, blockSummary=((dash.exposure||{}).entry_block_summary)||((readiness||{}).entry_block_summary)||{}; var statusText=readiness.overall||state.regime||'live'; document.getElementById('status-pill').textContent=String(statusText).toUpperCase(); document.getElementById('status-pill').className='hero-pill '+toneClass(statusText); document.getElementById('hero-summary').textContent=blockSummary.blocked?String(blockSummary.detail||blockSummary.headline||'진입 차단'):'모드 '+String(readiness.execution_mode||'모의투자')+' / 실전주문 '+String(exec.live_count||0)+' / 보유포지션 '+String((dash.open_positions||[]).length); document.getElementById('update-time').textContent=String(state.updated_at||'--').slice(11,16)||'--:--'; document.getElementById('next-run').textContent=String(((dash.runtime_profile||{}).next_run||'--')).slice(11,16)||'--'; renderAccess((health||{}).access||{}); renderOverview(state,dash,readiness); renderPrioritySignals(readiness,exec,brokerHealth,blockSummary); renderMetrics(dash,readiness); renderReadiness(readiness); renderBrokerHealth(brokerHealth); renderDesks(dash.desk_status||{}); renderPositions(dash.open_positions||[]); renderTrades(dash.closed_positions||[]); document.getElementById('insight-score').textContent=String(dash.insight_score||'--'); renderInsights(state.agent_runs||[]); renderJournal(state.notes||[]); renderEquity(dash.equity_curve||[]); document.getElementById('ov-ops-sub').textContent=blockSummary.blocked?String(blockSummary.detail||'위험 게이트 닫힘'):String(ops.severity||'stable')+' / '+String((ops.items||[]).slice(0,2).join(' | ')||'활성 운영 노트 없음');}catch(err){document.getElementById('status-pill').textContent='오류'; document.getElementById('status-pill').className='hero-pill tone-danger'; document.getElementById('hero-summary').textContent='대시보드 새로고침 실패: '+err.message;}}
  async function runCycle(){var btn=document.getElementById('cycle-btn'); btn.disabled=true; btn.textContent='실행 중...'; try{await fetch('/cycle',{method:'POST'}); await loadData();}catch(err){console.error(err);}finally{btn.disabled=false; btn.textContent='사이클 실행';}}
  setInterval(function(){loadData().catch(function(){});},20000); loadData().catch(function(){});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return _embedded_dashboard_html()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)

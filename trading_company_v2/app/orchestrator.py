from __future__ import annotations

from datetime import datetime, timezone
import re

from app.agents.chief_market_officer import CIOAgent, build_compounding_profile
from app.agents.crypto_desk_agent import CryptoDeskAgent
from app.agents.debate_agents import BearCaseAgent, BullCaseAgent, PortfolioManagerAgent
from app.agents.execution_agent import ExecutionAgent
from app.agents.korea_stock_desk_agent import KoreaStockDeskAgent
from app.agents.macro_sentiment_agent import MacroSentimentAgent
from app.agents.market_data_agent import MarketDataAgent
from app.agents.ops_agent import OpsAgent
from app.agents.risk_committee_agent import RiskCommitteeAgent
from app.agents.strategy_allocator_agent import StrategyAllocatorAgent
from app.agents.trend_structure_agent import TrendStructureAgent
from app.agents.us_stock_desk_agent import USStockDeskAgent
from app.config import settings
from app.core.models import AgentResult, AgentSnapshot, CompanyState, CycleJournalEntry, PaperOrder
from app.core.state_store import (
    auto_exit_positions,
    close_positions_for_desk,
    close_position_by_symbol,
    load_closed_positions,
    load_paper_closed_positions,
    load_company_state,
    load_active_live_order_locks,
    open_or_skip_position,
    reconcile_live_order_effects,
    refresh_live_order_statuses,
    save_company_state,
    save_cycle_journal,
    save_live_order_attempts,
    save_paper_orders,
    sync_live_positions,
    sync_paper_positions,
    update_positions_unrealized,
)
from app.notifier import notifier
from app.services.broker_router import normalize_execution_mode, route_orders
from app.services.kis_broker import (
    get_account_positions as get_kis_account_positions,
    get_order as get_kis_order,
    normalize_order_state as normalize_kis_order_state,
)
from app.services.market_gateway import get_us_data_status
from app.services.recommendation_engine import build_crypto_plan, build_korea_plan, build_us_plan
from app.services.upbit_broker import get_account_positions, get_order, get_ticker_prices, normalize_order_state

_BUY_ACTIONS = {"probe_longs", "attack_opening_drive", "selective_probe"}
_SELL_ACTIONS = {"reduce_risk", "capital_preservation"}


def _safe_parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _execution_summary_snapshot(current_state: dict) -> dict[str, int]:
    execution_log = list(current_state.get("execution_log", []) or [])
    live_rows = [item for item in execution_log if item.get("source") == "live"]
    partial_count = sum(
        1
        for item in live_rows
        if str(item.get("status") or "") == "partial"
        or str(item.get("effect_status") or "").startswith("partial")
    )
    pending_rows = [
        item
        for item in live_rows
        if str(item.get("status") or "") in {"submitted", "partial"}
        or str(item.get("effect_status") or "") in {"pending", "awaiting_balance_sync", "partial_balance_sync"}
    ]
    now_utc = datetime.now(timezone.utc)
    stale_count = 0
    for item in pending_rows:
        created_at = _safe_parse_utc(str(item.get("created_at") or ""))
        if created_at is None:
            continue
        if (now_utc - created_at).total_seconds() / 60 >= 15:
            stale_count += 1
    return {
        "partial_count": partial_count,
        "pending_count": len(pending_rows),
        "stale_count": stale_count,
    }


def _extract_prices(market_snapshot: dict) -> dict[str, float]:
    prices: dict[str, float] = {}
    for item in market_snapshot.get("crypto_leaders", []):
        market = item.get("market")
        price = item.get("trade_price")
        if market and price:
            prices[str(market)] = float(price)
    for item in list(market_snapshot.get("stock_leaders", [])) + list(market_snapshot.get("gap_candidates", [])):
        ticker = item.get("ticker")
        price = item.get("current_price")
        if ticker and price:
            prices[str(ticker)] = float(price)
    return prices


def _extract_symbol(order: PaperOrder, market_snapshot: dict) -> str:
    # Use the symbol the execution agent already picked
    if order.symbol:
        return order.symbol
    if order.desk == "crypto":
        match = re.search(r"KRW-[A-Z]+", order.focus)
        if match:
            return match.group(0)
        leaders = market_snapshot.get("crypto_leaders", [])
        return str(leaders[0]["market"]) if leaders else ""
    candidates = market_snapshot.get("gap_candidates") or market_snapshot.get("stock_leaders", [])
    return str(candidates[0].get("ticker", "")) if candidates else ""


def _manage_positions(paper_orders: list[PaperOrder], market_snapshot: dict, skip_desks: set[str] | None = None) -> None:
    skip_desks = skip_desks or set()
    prices = _extract_prices(market_snapshot)
    update_positions_unrealized(prices)
    auto_exit_positions(prices, skip_desks=skip_desks)
    for order in paper_orders:
        if order.desk in skip_desks:
            continue
        if order.action in _BUY_ACTIONS and order.status == "planned":
            symbol = _extract_symbol(order, market_snapshot)
            entry_price = prices.get(symbol, 0.0)
            if symbol and entry_price > 0:
                open_or_skip_position(order.desk, symbol, entry_price, order.notional_pct, order.action)
        elif order.action in _SELL_ACTIONS and order.status == "planned":
            if order.symbol:
                close_position_by_symbol(order.desk, order.symbol, prices, reason="desk_exit")
            else:
                close_positions_for_desk(order.desk, prices)


def _live_desks_for_mode(execution_mode: str) -> set[str]:
    if execution_mode == "upbit_live":
        return {"crypto"}
    if execution_mode == "kis_live":
        return {"korea"}
    return set()


def _order_intent(action: str) -> str:
    if action in _BUY_ACTIONS:
        return "entry"
    if action in _SELL_ACTIONS:
        return "exit"
    return "other"


def _parse_size_multiplier(size: str) -> float:
    try:
        return float(str(size or "0.00x").replace("x", ""))
    except ValueError:
        return 0.0


def _format_size_multiplier(value: float) -> str:
    return f"{max(value, 0.0):.2f}x"


def _apply_compounding_overlays(strategy_book: dict, capital_profile: dict) -> tuple[dict, list[str]]:
    if not strategy_book:
        return strategy_book, []

    updated_book = dict(strategy_book)
    desk_map = {
        "crypto": "crypto_plan",
        "korea": "korea_plan",
        "us": "us_plan",
    }
    desk_multipliers = capital_profile.get("desk_multipliers", {}) or {}
    global_multiplier = float(capital_profile.get("global_multiplier", 1.0) or 1.0)
    notes: list[str] = []

    for desk_name, plan_key in desk_map.items():
        plan = dict(updated_book.get(plan_key, {}) or {})
        base_size = _parse_size_multiplier(str(plan.get("size", "0.00x")))
        if base_size <= 0:
            updated_book[plan_key] = plan
            continue
        desk_multiplier = float(desk_multipliers.get(desk_name, 1.0) or 1.0)
        adjusted_size = round(base_size * global_multiplier * desk_multiplier, 2)
        adjusted_size = min(adjusted_size, 0.95)
        if abs(adjusted_size - base_size) >= 0.01:
            plan["size"] = _format_size_multiplier(adjusted_size)
            plan_notes = list(plan.get("notes", []) or [])
            plan_notes.append(
                f"capital overlay {capital_profile.get('mode', 'neutral')} adjusted size {base_size:.2f}x -> {adjusted_size:.2f}x"
            )
            plan["notes"] = plan_notes
            notes.append(f"{desk_name} capital overlay {base_size:.2f}x -> {adjusted_size:.2f}x")
        updated_book[plan_key] = plan

    updated_book["capital_profile"] = capital_profile
    return updated_book, notes


def _filter_conflicting_live_orders(
    paper_orders: list[PaperOrder],
    requested_execution_mode: str,
    market_snapshot: dict,
) -> tuple[list[PaperOrder], list[str]]:
    live_desks = _live_desks_for_mode(requested_execution_mode)
    if not live_desks:
        return paper_orders, []

    locks = load_active_live_order_locks()
    if not locks:
        return paper_orders, []

    lock_keys = {
        (str(item.get("desk") or ""), str(item.get("symbol") or ""), str(item.get("intent") or ""))
        for item in locks
        if item.get("symbol")
    }
    desk_intents = {
        (str(item.get("desk") or ""), str(item.get("intent") or ""))
        for item in locks
    }

    kept: list[PaperOrder] = []
    blocked_notes: list[str] = []
    for order in paper_orders:
        if order.status != "planned" or order.desk not in live_desks:
            kept.append(order)
            continue
        intent = _order_intent(order.action)
        symbol = str(order.symbol or _extract_symbol(order, market_snapshot) or "")
        has_symbol_lock = bool(symbol) and (order.desk, symbol, intent) in lock_keys
        has_desk_exit_lock = (not symbol) and intent == "exit" and (order.desk, intent) in desk_intents
        if has_symbol_lock or has_desk_exit_lock:
            blocked_target = symbol or order.desk
            blocked_notes.append(f"live duplicate guard blocked {order.desk} {intent} for {blocked_target}")
            continue
        kept.append(order)
    return kept, blocked_notes


def _live_execution_guardrails(live_locks: list[dict]) -> dict[str, object]:
    if not live_locks:
        return {
            "block_new_entries": False,
            "risk_budget_cap": None,
            "notes": [],
        }

    has_partial = any(
        str(item.get("request_status") or "") == "partial"
        or str(item.get("effect_status") or "").startswith("partial")
        for item in live_locks
    )
    has_pending_entry = any(
        str(item.get("intent") or "") == "entry"
        and str(item.get("effect_status") or "") in {"pending", "awaiting_balance_sync", "partial_balance_sync", "linked_partial_open"}
        for item in live_locks
    )
    has_pending_exit = any(
        str(item.get("intent") or "") == "exit"
        and str(item.get("effect_status") or "") in {"pending", "awaiting_balance_sync", "partial_close_pending"}
        for item in live_locks
    )

    block_new_entries = has_partial or has_pending_entry or has_pending_exit
    risk_budget_cap = 0.15 if (has_partial or has_pending_exit) else 0.25 if has_pending_entry else None
    notes: list[str] = []
    if has_partial:
        notes.append("live conservative mode: partial fill unresolved, new entries paused")
    elif has_pending_exit:
        notes.append("live conservative mode: exit fill unresolved, new entries paused")
    elif has_pending_entry:
        notes.append("live conservative mode: entry sync pending, sizing capped")

    return {
        "block_new_entries": block_new_entries,
        "risk_budget_cap": risk_budget_cap,
        "notes": notes,
    }


class CompanyOrchestrator:
    def __init__(self):
        active_desks = settings.active_desk_set
        self.analysis_agents = [
            MarketDataAgent(),
            MacroSentimentAgent(),
            TrendStructureAgent(),
            StrategyAllocatorAgent(),
            CryptoDeskAgent(),
            CIOAgent(),
            RiskCommitteeAgent(),
        ]
        if "korea" in active_desks:
            self.analysis_agents.insert(5, KoreaStockDeskAgent())
        if "us" in active_desks:
            self.analysis_agents.insert(6 if "korea" in active_desks else 5, USStockDeskAgent())
        self.execution_agent = ExecutionAgent()
        self.ops_agent = OpsAgent()

    @staticmethod
    def _inactive_plan(desk: str) -> dict:
        label = {"korea": "Korea stock", "us": "U.S. stock"}.get(desk, desk)
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"{label} desk disabled while crypto trend engine is being validated.",
            "symbol": "",
            "candidate_symbols": [],
            "notes": ["ACTIVE_DESKS=crypto; keep broker/settings ready but do not trade or display this desk."],
        }

    @staticmethod
    def _determine_stance(macro_score: float, trend_score: float) -> str:
        combined = (macro_score + trend_score) / 2
        if combined >= 0.66:
            return "OFFENSE"
        if combined <= 0.42:
            return "DEFENSE"
        return "BALANCED"

    @staticmethod
    def _determine_regime(macro_score: float, trend_score: float) -> str:
        if macro_score <= 0.35:
            return "STRESSED"
        if abs(trend_score - 0.5) <= 0.08:
            return "RANGING"
        return "TRENDING"

    @staticmethod
    def _risk_alert_needed(previous_state: dict, current_state: dict) -> bool:
        prev_daily = previous_state.get("daily_summary", {})
        curr_daily = current_state.get("daily_summary", {})
        prev_combined = float(prev_daily.get("realized_pnl_pct", 0.0) or 0.0) + float(prev_daily.get("unrealized_pnl_pct", 0.0) or 0.0)
        curr_combined = float(curr_daily.get("realized_pnl_pct", 0.0) or 0.0) + float(curr_daily.get("unrealized_pnl_pct", 0.0) or 0.0)
        prev_blocked = not bool(previous_state.get("allow_new_entries", True))
        curr_blocked = not bool(current_state.get("allow_new_entries", True))
        prev_losses = int(prev_daily.get("losses", 0) or 0)
        prev_wins = int(prev_daily.get("wins", 0) or 0)
        curr_losses = int(curr_daily.get("losses", 0) or 0)
        curr_wins = int(curr_daily.get("wins", 0) or 0)
        crossed_drawdown = prev_combined > -1.0 and curr_combined <= -1.0
        turned_blocked = not prev_blocked and curr_blocked
        loss_balance_worsened = (prev_losses <= prev_wins) and (curr_losses > curr_wins and (curr_losses + curr_wins) >= 2)
        return crossed_drawdown or turned_blocked or loss_balance_worsened

    @staticmethod
    def _ops_alert_lines(previous_state: dict, current_state: dict) -> list[tuple[str, list[str]]]:
        alerts: list[tuple[str, list[str]]] = []
        prev_daily = previous_state.get("daily_summary", {})
        curr_daily = current_state.get("daily_summary", {})
        curr_session = current_state.get("session_state", {})
        desk_stats = curr_daily.get("desk_stats", {}) or {}
        close_reason_stats = curr_daily.get("close_reason_stats", {}) or {}
        us_status = get_us_data_status()
        execution_summary = _execution_summary_snapshot(current_state)

        if (curr_session.get("us_premarket") or curr_session.get("us_regular")) and not us_status.get("ok", False):
            alerts.append(
                (
                    "U.S. data alert",
                    [
                        f"phase: {curr_session.get('market_phase', 'n/a')}",
                        f"provider: {us_status.get('provider', 'n/a')}",
                        f"message: {us_status.get('message', 'n/a')}",
                    ],
                )
            )

        stop_stats = close_reason_stats.get("stop_hit", {}) or {}
        if int(stop_stats.get("count", 0) or 0) >= 3 and float(stop_stats.get("pnl_pct", 0.0) or 0.0) <= -3.0:
            alerts.append(
                (
                    "Stop pressure alert",
                    [
                        f"stop_hit count: {stop_stats.get('count', 0)}",
                        f"stop_hit pnl: {stop_stats.get('pnl_pct', 0.0)}%",
                        f"expectancy: {curr_daily.get('expectancy_pct', 0.0)}%",
                    ],
                )
            )

        prev_current_cycle_orders = int(prev_daily.get("current_cycle_planned_orders", 0) or 0)
        curr_current_cycle_orders = int(curr_daily.get("current_cycle_planned_orders", 0) or 0)
        if prev_current_cycle_orders > 0 and curr_current_cycle_orders == 0:
            blocked_desks = []
            for desk_name in ("korea", "us"):
                stats = desk_stats.get(desk_name, {}) or {}
                if int(stats.get("losses", 0) or 0) > int(stats.get("wins", 0) or 0):
                    blocked_desks.append(desk_name)
            if blocked_desks:
                alerts.append(
                    (
                        "Desk pause alert",
                        [
                            f"paused desks: {', '.join(blocked_desks)}",
                            f"risk budget: {current_state.get('risk_budget')}",
                            f"entries: {'ON' if current_state.get('allow_new_entries') else 'BLOCKED'}",
                        ],
                    )
                )
        if int(execution_summary.get("stale_count", 0) or 0) > 0:
            alerts.append(
                (
                    "Live stale execution alert",
                    [
                        f"stale orders: {execution_summary.get('stale_count', 0)}",
                        f"pending: {execution_summary.get('pending_count', 0)}",
                        f"partial: {execution_summary.get('partial_count', 0)}",
                    ],
                )
            )
        return alerts

    @staticmethod
    def _strategy_hold_alert_lines(current_state: dict) -> list[tuple[str, list[str]]]:
        alerts: list[tuple[str, list[str]]] = []
        strategy_book = current_state.get("strategy_book", {}) or {}
        for desk_name, plan_key in (("crypto", "crypto_plan"), ("korea", "korea_plan"), ("us", "us_plan")):
            plan = strategy_book.get(plan_key, {}) or {}
            action = str(plan.get("action", "") or "")
            notes = [str(item) for item in (plan.get("notes", []) or [])]
            if action in {"stand_by", "watchlist_only"} and any("overheated" in note or "weakly confirmed" in note for note in notes):
                alerts.append(
                    (
                        f"{desk_name.upper()} hold alert",
                        [
                            f"focus: {plan.get('focus', 'n/a')}",
                            f"symbol: {plan.get('symbol', 'n/a')}",
                            *notes[:2],
                        ],
                    )
                )
        return alerts

    @staticmethod
    def _realtime_decision_changed(previous_state: dict, current_state: dict) -> bool:
        prev_session = previous_state.get("session_state", {}) or {}
        curr_session = current_state.get("session_state", {}) or {}
        prev_book = previous_state.get("strategy_book", {}) or {}
        curr_book = current_state.get("strategy_book", {}) or {}
        if prev_session.get("market_phase") != curr_session.get("market_phase"):
            return True
        for plan_key in ("crypto_plan", "korea_plan", "us_plan"):
            prev_plan = prev_book.get(plan_key, {}) or {}
            curr_plan = curr_book.get(plan_key, {}) or {}
            if prev_plan.get("action") != curr_plan.get("action"):
                return True
            if prev_plan.get("symbol") != curr_plan.get("symbol"):
                return True
        return False

    @staticmethod
    def _crypto_pilot_lane(state: CompanyState) -> dict:
        signals = list(state.latest_signals or [])
        signal_score: float | None = None
        trigger_threshold: float | None = None
        action = "watchlist_only"
        for entry in signals:
            if entry.startswith("crypto_signal="):
                try:
                    signal_score = round(float(entry.split("=", 1)[1]), 2)
                except ValueError:
                    pass
            elif entry.startswith("crypto_trigger="):
                try:
                    trigger_threshold = round(float(entry.split("=", 1)[1]), 2)
                except ValueError:
                    pass
            elif entry.startswith("crypto_action="):
                action = str(entry.split("=", 1)[1]).strip()
        if signal_score is None or trigger_threshold is None:
            return {}
        distance = round(max(trigger_threshold - signal_score, 0.0), 2)
        trigger_state = (
            "ready" if signal_score >= trigger_threshold
            else "arming" if distance <= 0.08
            else "waiting"
        )
        return {
            "crypto_live_lane": {
                "signal_score": signal_score,
                "trigger_threshold": trigger_threshold,
                "distance_to_trigger": distance,
                "trigger_state": trigger_state,
                "action": action,
                "symbol": str((state.strategy_book or {}).get("crypto_plan", {}).get("symbol") or "KRW-BTC"),
            }
        }

    @staticmethod
    def _ops_flag_snapshot(current_state: dict) -> dict:
        daily = current_state.get("daily_summary", {}) or {}
        strategy_book = current_state.get("strategy_book", {}) or {}
        close_reason_stats = daily.get("close_reason_stats", {}) or {}
        desk_close_reason_stats = daily.get("desk_close_reason_stats", {}) or {}
        symbol_performance_stats = daily.get("symbol_performance_stats", []) or []
        stop_stats = close_reason_stats.get("stop_hit", {}) or {}
        flags: list[dict] = []

        def add(level: str, code: str, message: str) -> None:
            flags.append({"level": level, "code": code, "message": message})

        gross = float(daily.get("gross_open_notional_pct", 0.0) or 0.0)
        expectancy = float(daily.get("expectancy_pct", 0.0) or 0.0)
        realized = float(daily.get("realized_pnl_pct", 0.0) or 0.0)
        win_rate = float(daily.get("win_rate", 0.0) or 0.0)

        if not bool(current_state.get("allow_new_entries", True)):
            add("critical", "entries_blocked", "신규 진입이 차단된 상태")
        if gross >= 0.9:
            add("warning", "gross_exposure_high", f"총 노출이 높음: {gross:.2f}x")
        if expectancy < 0:
            add("warning", "negative_expectancy", f"거래 기대값이 음수: {expectancy:.2f}%")
        if realized <= -1.0:
            add("warning", "daily_drawdown", f"일일 실현 손익 악화: {realized:.2f}%")
        if int(stop_stats.get("count", 0) or 0) >= 3 and float(stop_stats.get("pnl_pct", 0.0) or 0.0) <= -3.0:
            add("warning", "stop_pressure", f"stop_hit 압력 높음: {stop_stats.get('count', 0)}회 / {stop_stats.get('pnl_pct', 0.0)}%")
        if daily.get("closed_positions", 0) and win_rate < 40.0:
            add("warning", "low_win_rate", f"승률 저하: {win_rate:.1f}%")

        for desk_name, label in (("crypto", "코인"), ("korea", "한국"), ("us", "미국")):
            desk_stop = ((desk_close_reason_stats.get(desk_name, {}) or {}).get("stop_hit", {}) or {})
            if int(desk_stop.get("count", 0) or 0) >= 2 and float(desk_stop.get("pnl_pct", 0.0) or 0.0) <= -1.5:
                add("warning", f"{desk_name}_desk_stop", f"{label} 데스크 stop 압력: {desk_stop.get('count', 0)}회 / {desk_stop.get('pnl_pct', 0.0)}%")

        for item in symbol_performance_stats[:2]:
            if int(item.get("stop_like_count", 0) or 0) >= 2 or float(item.get("pnl_pct", 0.0) or 0.0) <= -2.0:
                add(
                    "warning",
                    f"{item.get('desk', 'n/a')}_{item.get('symbol', 'n/a')}",
                    f"{item.get('desk', 'n/a')} {item.get('symbol', 'n/a')} 손실 반복: {item.get('stop_like_count', 0)}회 / {item.get('pnl_pct', 0.0)}%",
                )

        for desk_name, label in (("crypto_plan", "코인"), ("korea_plan", "한국"), ("us_plan", "미국")):
            plan = strategy_book.get(desk_name, {}) or {}
            action = str(plan.get("action", "") or "")
            notes = [str(item) for item in (plan.get("notes", []) or [])]
            if action in {"stand_by", "watchlist_only"} and any("overheated" in note or "weakly confirmed" in note for note in notes):
                add("info", f"{desk_name}_hold", f"{label} 데스크 보류: {plan.get('focus', 'n/a')}")

        execution_summary = _execution_summary_snapshot(current_state)
        if int(execution_summary.get("partial_count", 0) or 0) > 0:
            add("warning", "live_partial_fill", f"live partial fill {execution_summary.get('partial_count', 0)} pending review")
        elif int(execution_summary.get("pending_count", 0) or 0) > 0:
            add("info", "live_pending", f"live orders pending {execution_summary.get('pending_count', 0)}")
        if int(execution_summary.get("stale_count", 0) or 0) > 0:
            add("warning", "live_stale_pending", f"live stale pending {execution_summary.get('stale_count', 0)} order(s)")
        if not bool(current_state.get("allow_new_entries", True)) and int(execution_summary.get("pending_count", 0) or 0) > 0:
            add("warning", "live_conservative_mode", "live execution unresolved, conservative entry pause active")

        severity = "stable"
        if any(item["level"] == "critical" for item in flags):
            severity = "critical"
        elif any(item["level"] == "warning" for item in flags):
            severity = "warning"
        return {"severity": severity, "items": flags}

    def run_cycle(self) -> dict:
        state = load_company_state()
        previous_state = state.model_dump()
        active_desks = settings.active_desk_set
        results: list[AgentResult] = [agent.safe_run() for agent in self.analysis_agents]

        macro_result = next((r for r in results if r.name == "macro_sentiment_agent"), AgentResult(name="macro_sentiment_agent", reason="missing"))
        trend_result = next((r for r in results if r.name == "trend_structure_agent"), AgentResult(name="trend_structure_agent", reason="missing"))
        market_data_result = next((r for r in results if r.name == "market_data_agent"), AgentResult(name="market_data_agent", reason="missing"))
        strategy_allocator_result = next((r for r in results if r.name == "strategy_allocator_agent"), AgentResult(name="strategy_allocator_agent", reason="missing"))
        crypto_desk_result = next((r for r in results if r.name == "crypto_desk_agent"), AgentResult(name="crypto_desk_agent", reason="missing"))
        stock_desk_result = next((r for r in results if r.name == "korea_stock_desk_agent"), AgentResult(name="korea_stock_desk_agent", reason="missing"))
        us_desk_result = next((r for r in results if r.name == "us_stock_desk_agent"), AgentResult(name="us_stock_desk_agent", reason="missing"))
        state.stance = self._determine_stance(macro_result.score, trend_result.score)
        state.regime = self._determine_regime(macro_result.score, trend_result.score)
        state.risk_budget = 0.5 if state.stance == "BALANCED" else 0.7 if state.stance == "OFFENSE" else 0.3
        requested_execution_mode = normalize_execution_mode(settings.execution_mode)
        state.execution_mode = requested_execution_mode
        state.notes = [
            f"{settings.company_name} operating on {settings.operator_name}'s personal-PC-first stack",
            f"execution requested={requested_execution_mode}",
            "portable to personal PC",
        ]
        state.trader_principles = [
            "Paul Tudor Jones: defense first",
            "Stan Druckenmiller: size with conviction",
            "Linda Raschke: price confirms",
            "Ed Seykota: systems and risk over prediction",
        ]
        state.latest_signals = [
            f"macro_bias={macro_result.payload.get('macro_bias', 'unknown')}",
            f"trend_bias={trend_result.payload.get('trend_bias', 'unknown')}",
            f"regime={state.regime.lower()}",
            f"session_focus={strategy_allocator_result.payload.get('company_focus', 'unknown')}",
            f"crypto_desk={crypto_desk_result.payload.get('desk_bias', 'unknown')}",
            f"active_desks={','.join(sorted(active_desks))}",
        ]
        state.market_snapshot = {
            "as_of": market_data_result.payload.get("as_of"),
            "crypto_leaders": market_data_result.payload.get("crypto_leaders", []),
            "crypto_view": crypto_desk_result.payload,
            "stock_leaders": market_data_result.payload.get("stock_leaders", []),
            "gap_candidates": market_data_result.payload.get("gap_candidates", []),
            "us_leaders": market_data_result.payload.get("us_leaders", []),
        }
        state.session_state = strategy_allocator_result.payload.get("session", {})
        state.desk_views = {
            "crypto_desk": crypto_desk_result.payload,
            "korea_stock_desk": stock_desk_result.payload if "korea" in active_desks else {"disabled": True},
            "us_stock_desk": us_desk_result.payload if "us" in active_desks else {"disabled": True},
        }
        korea_plan = (
            build_korea_plan(
                state.stance,
                state.regime,
                stock_desk_result.payload,
                strategy_allocator_result.payload.get("session", {}),
            )
            if "korea" in active_desks
            else self._inactive_plan("korea")
        )
        us_plan = (
            build_us_plan(
                state.stance,
                state.regime,
                us_desk_result.payload,
                strategy_allocator_result.payload.get("session", {}),
            )
            if "us" in active_desks
            else self._inactive_plan("us")
        )
        crypto_plan = build_crypto_plan(state.stance, state.regime, crypto_desk_result.payload)
        atr_multiplier = float(crypto_desk_result.payload.get("atr_size_multiplier", 1.0) or 1.0)
        crypto_plan["atr_size_multiplier"] = atr_multiplier
        crypto_plan["atr_pct"] = float(crypto_desk_result.payload.get("atr_pct", 0.0) or 0.0)
        crypto_plan["volatility_tier"] = str(crypto_desk_result.payload.get("volatility_tier", "unknown") or "unknown")
        crypto_plan["candidate_markets"] = list(crypto_desk_result.payload.get("candidate_markets") or [])
        crypto_plan["btc_corr_15m"] = float(crypto_desk_result.payload.get("btc_corr_15m", 1.0) or 1.0)
        crypto_plan["signal_freshness"] = float(crypto_desk_result.payload.get("signal_freshness", 1.0) or 1.0)
        crypto_plan["signal_age_minutes"] = float(crypto_desk_result.payload.get("signal_age_minutes", 999.0) or 999.0)
        crypto_plan["freshness_reason"] = str(crypto_desk_result.payload.get("freshness_reason", "") or "")
        atr_reason = str(crypto_desk_result.payload.get("atr_sizing_reason", "") or "")
        if atr_reason:
            crypto_notes = list(crypto_plan.get("notes", []) or [])
            crypto_notes.append(atr_reason)
            crypto_plan["notes"] = crypto_notes
        freshness_reason = str(crypto_plan.get("freshness_reason", "") or "")
        if freshness_reason and float(crypto_plan.get("signal_freshness", 1.0) or 1.0) < 1.0:
            crypto_notes = list(crypto_plan.get("notes", []) or [])
            crypto_notes.append(f"signal freshness adjusted score: {freshness_reason}")
            crypto_plan["notes"] = crypto_notes

        state.strategy_book = {
            "company_focus": strategy_allocator_result.payload.get("company_focus"),
            "desk_priorities": strategy_allocator_result.payload.get("desk_priorities", []),
            "active_desks": sorted(active_desks),
            "crypto_plan": crypto_plan,
            "korea_plan": korea_plan,
            "us_plan": us_plan,
        }
        crypto_signal = float(crypto_desk_result.payload.get("signal_score", 0.0) or 0.0)
        crypto_recent = float(crypto_desk_result.payload.get("recent_change_pct", 0.0) or 0.0)
        crypto_lead = str(
            state.strategy_book.get("crypto_plan", {}).get("symbol")
            or crypto_desk_result.payload.get("lead_market")
            or "KRW-BTC"
        )
        crypto_weight = float((crypto_desk_result.payload.get("backtest_weights", {}) or {}).get(crypto_lead, 0.0) or 0.0)
        crypto_trigger = 0.56 if crypto_weight >= 0.10 and crypto_recent >= -0.4 else 0.58
        state.latest_signals.extend(
            [
                f"crypto_signal={crypto_signal:.2f}",
                f"crypto_trigger={crypto_trigger:.2f}",
                f"crypto_action={state.strategy_book.get('crypto_plan', {}).get('action', 'watchlist_only')}",
            ]
        )
        capital_profile = build_compounding_profile(state.stance, state.regime, state.daily_summary)
        state.strategy_book, capital_overlay_notes = _apply_compounding_overlays(state.strategy_book, capital_profile)
        for note in capital_overlay_notes[:3]:
            state.notes.append(note)
        bull_agent = BullCaseAgent()
        bull_agent.configure(state)
        bull_result = bull_agent.safe_run()
        bear_agent = BearCaseAgent()
        bear_agent.configure(state)
        bear_result = bear_agent.safe_run()
        portfolio_manager = PortfolioManagerAgent()
        portfolio_manager.configure(state, bull_result.payload, bear_result.payload)
        portfolio_result = portfolio_manager.safe_run()
        adjusted_strategy_book = portfolio_result.payload.get("strategy_book")
        portfolio_decision_payload = {
            key: value
            for key, value in portfolio_result.payload.items()
            if key != "strategy_book"
        }
        debate_payload = {
            "bull_case": bull_result.payload,
            "bear_case": bear_result.payload,
            "portfolio_manager": portfolio_decision_payload,
        }
        if adjusted_strategy_book:
            state.strategy_book = adjusted_strategy_book
        state.strategy_book["decision_debate"] = debate_payload
        portfolio_result.payload = portfolio_decision_payload
        for decision in (portfolio_decision_payload.get("decisions") or [])[:3]:
            state.notes.append(
                "portfolio manager "
                f"{decision.get('decision', 'review')} {decision.get('desk', 'desk')}: "
                f"{decision.get('reason', 'no reason')}"
            )
        results.extend([bull_result, bear_result, portfolio_result])
        company_focus = str(state.strategy_book.get("company_focus") or "Capital preservation and watchlist maintenance")
        active_desk_stats = state.daily_summary.get("desk_stats", {}) or {}
        active_combined_pnl = sum(
            float((active_desk_stats.get(desk, {}) or {}).get("realized_pnl_pct", 0.0) or 0.0)
            + float((active_desk_stats.get(desk, {}) or {}).get("unrealized_pnl_pct", 0.0) or 0.0)
            for desk in active_desks
        )
        drawdown_entry_floor = -6.0 if active_desks == {"crypto"} else -1.5
        provisional_allow_new_entries = state.regime != "STRESSED" and active_combined_pnl > drawdown_entry_floor
        if active_desks == {"crypto"} and active_combined_pnl <= -1.5 and provisional_allow_new_entries:
            state.notes.append(
                f"crypto recovery mode: entries remain open at throttled risk despite {active_combined_pnl:.2f}% active P&L"
            )
        live_locks = load_active_live_order_locks()
        live_guardrails = _live_execution_guardrails(live_locks)
        execution_risk_budget = state.risk_budget
        risk_budget_cap = live_guardrails.get("risk_budget_cap")
        if isinstance(risk_budget_cap, (int, float)):
            execution_risk_budget = min(float(state.risk_budget), float(risk_budget_cap))
        if bool(live_guardrails.get("block_new_entries")):
            provisional_allow_new_entries = False
        for note in list(live_guardrails.get("notes") or [])[:2]:
            state.notes.append(str(note))
        self.execution_agent.configure(
            strategy_book=state.strategy_book,
            regime=state.regime,
            market_snapshot=state.market_snapshot,
            open_positions=state.open_positions,
            closed_positions=load_paper_closed_positions(limit=12),
            daily_summary=state.daily_summary,
            allow_new_entries=provisional_allow_new_entries,
            risk_budget=execution_risk_budget,
        )
        execution_result = self.execution_agent.safe_run()
        ops_result = self.ops_agent.safe_run()
        results.extend([execution_result, ops_result])
        paper_orders = [PaperOrder.model_validate(item) for item in execution_result.payload.get("orders", [])]
        paper_orders, live_guard_notes = _filter_conflicting_live_orders(
            paper_orders,
            requested_execution_mode,
            state.market_snapshot,
        )
        route_summary = route_orders(paper_orders, requested_execution_mode)
        execution_result.payload["execution_router"] = route_summary
        state.execution_mode = requested_execution_mode
        if route_summary.get("broker_live"):
            state.notes.append("broker live routing active")
        elif route_summary.get("requested_mode") != route_summary.get("applied_mode"):
            state.notes.append(f"execution fallback: {route_summary.get('requested_mode')} -> {route_summary.get('applied_mode')}")
        for warning in route_summary.get("warnings", [])[:2]:
            state.notes.append(warning)
        for note in live_guard_notes[:3]:
            state.notes.append(note)
        save_live_order_attempts(route_summary, paper_orders)
        broker_prices: dict[str, float] = dict(_extract_prices(state.market_snapshot))
        if state.execution_mode == "upbit_live":
            try:
                refresh_summary = refresh_live_order_statuses(
                    lambda row: normalize_order_state(get_order(str(row.get("broker_order_id") or "")))
                )
                if refresh_summary.get("checked"):
                    state.notes.append(
                        f"live order refresh checked={refresh_summary.get('checked', 0)} updated={refresh_summary.get('updated', 0)} failed={refresh_summary.get('failed', 0)}"
                    )
            except Exception as exc:
                state.notes.append(f"live order refresh failed: {exc}")
        elif state.execution_mode == "kis_live":
            try:
                refresh_summary = refresh_live_order_statuses(
                    _refresh_kis_order
                )
                if refresh_summary.get("checked"):
                    state.notes.append(
                        f"live order refresh checked={refresh_summary.get('checked', 0)} updated={refresh_summary.get('updated', 0)} failed={refresh_summary.get('failed', 0)}"
                    )
            except Exception as exc:
                state.notes.append(f"live order refresh failed: {exc}")
        save_paper_orders(paper_orders)
        sync_paper_positions(paper_orders=paper_orders, market_snapshot=state.market_snapshot)
        live_crypto_enabled = bool(route_summary.get("broker_live")) and state.execution_mode == "upbit_live"
        if live_crypto_enabled:
            try:
                account_positions = get_account_positions()
                missing_markets = [item["market"] for item in account_positions if item.get("market") and item["market"] not in broker_prices]
                if missing_markets:
                    broker_prices.update(get_ticker_prices(missing_markets))
                live_sync = sync_live_positions("crypto", account_positions, broker_prices, default_action="live_sync")
                state.notes.append(
                    f"crypto broker sync opened={live_sync.get('opened', 0)} updated={live_sync.get('updated', 0)} closed={live_sync.get('closed', 0)}"
                )
            except Exception as exc:
                state.notes.append(f"crypto broker sync failed: {exc}")
            try:
                effect_summary = reconcile_live_order_effects(broker_prices)
                if effect_summary.get("checked"):
                    state.notes.append(
                        f"live effect reconcile checked={effect_summary.get('checked', 0)} updated={effect_summary.get('updated', 0)}"
                    )
            except Exception as exc:
                state.notes.append(f"live effect reconcile failed: {exc}")
        live_korea_enabled = bool(route_summary.get("broker_live")) and state.execution_mode == "kis_live"
        if live_korea_enabled:
            try:
                account_positions = get_kis_account_positions()
                live_sync = sync_live_positions("korea", account_positions, broker_prices, default_action="kis_live_sync")
                state.notes.append(
                    f"korea broker sync opened={live_sync.get('opened', 0)} updated={live_sync.get('updated', 0)} closed={live_sync.get('closed', 0)}"
                )
            except Exception as exc:
                state.notes.append(f"korea broker sync failed: {exc}")
            try:
                effect_summary = reconcile_live_order_effects(broker_prices)
                if effect_summary.get("checked"):
                    state.notes.append(
                        f"live effect reconcile checked={effect_summary.get('checked', 0)} updated={effect_summary.get('updated', 0)}"
                    )
            except Exception as exc:
                state.notes.append(f"live effect reconcile failed: {exc}")
        skip_desks: set[str] = set()
        if live_crypto_enabled:
            skip_desks.add("crypto")
        if live_korea_enabled:
            skip_desks.add("korea")

        # Signal-based exit: close long positions when downtrend reversal detected.
        # Faster than waiting for trailing stop — exit on the signal, not the drawdown.
        # Conditions for signal reversal exit:
        #   (A) CHoCH bearish + weak signal_score → structure has flipped down
        #   (B) micro_score collapsed (<= 0.18) + orderbook selling pressure → real-time reversal
        if "crypto" not in skip_desks:
            candidate_signal_map: dict[str, dict] = {
                str(item.get("market", "")).strip(): item
                for item in (crypto_desk_result.payload.get("candidate_markets") or [])
                if str(item.get("market", "")).strip()
            }
            current_prices = _extract_prices(state.market_snapshot)
            for pos in list(state.open_positions):
                if pos.get("desk") != "crypto":
                    continue
                symbol = str(pos.get("symbol") or "").strip()
                if not symbol:
                    continue
                sig = candidate_signal_map.get(symbol)
                if not sig:
                    continue
                choch_bear = bool(sig.get("choch_bearish", False))
                signal_score_val = float(sig.get("signal_score", 0.5) or 0.5)
                micro_score_val = float(sig.get("micro_score", 0.5) or 0.5)
                orderbook_bid_ask = float(sig.get("orderbook_bid_ask_ratio", 1.0) or 1.0)
                # (A) structural reversal: CHoCH bearish with weak signal
                # (B) momentum collapse: micro dead AND orderbook dominated by sellers
                reversal_a = choch_bear and signal_score_val < 0.40
                reversal_b = micro_score_val <= 0.18 and orderbook_bid_ask <= 0.78
                if reversal_a or reversal_b:
                    reason = "choch_bearish_exit" if reversal_a else "momentum_collapse_exit"
                    close_position_by_symbol("crypto", symbol, current_prices, reason=reason)
                    state.notes.append(
                        f"signal exit {symbol}: {reason} "
                        f"(signal={signal_score_val:.2f} micro={micro_score_val:.2f} "
                        f"choch_bear={choch_bear} ob_ratio={orderbook_bid_ask:.2f})"
                    )

        _manage_positions(paper_orders, state.market_snapshot, skip_desks=skip_desks or None)
        save_cycle_journal(
            CycleJournalEntry(
                stance=state.stance,
                regime=state.regime,
                company_focus=company_focus,
                summary=state.latest_signals[:5],
                orders=paper_orders,
            )
        )
        refreshed_state = load_company_state()
        state.daily_summary = refreshed_state.daily_summary
        state.execution_log = refreshed_state.execution_log
        state.open_positions = refreshed_state.open_positions
        state.recent_journal = refreshed_state.recent_journal
        state.agent_runs = [
            AgentSnapshot(
                name=result.name,
                score=result.score,
                reason=result.reason,
                payload=result.payload,
                generated_at=result.generated_at,
            )
            for result in results
        ]

        risk_agent = RiskCommitteeAgent()
        state = risk_agent.apply(state)
        if bool(live_guardrails.get("block_new_entries")):
            state.allow_new_entries = False
        if isinstance(risk_budget_cap, (int, float)):
            state.risk_budget = min(float(state.risk_budget), float(risk_budget_cap))
        save_company_state(state)
        current_state = state.model_dump()
        current_state["execution_router"] = route_summary
        current_state["ops_flags"] = self._ops_flag_snapshot(current_state)
        notifier.send_cycle_summary(previous_state=previous_state, current_state=state.model_dump())
        if self._risk_alert_needed(previous_state, current_state):
            notifier.send_risk_alert(current_state)
        execution_summary = _execution_summary_snapshot(current_state)
        if int(execution_summary.get("stale_count", 0) or 0) > 0:
            notifier.send_stale_execution_alert(execution_summary)
        if self._realtime_decision_changed(previous_state, current_state):
            notifier.send_realtime_decision_alert(
                {
                    "runtime_profile": {
                        "mode": current_state.get("session_state", {}).get("market_phase", "n/a"),
                        "interval_seconds": "dynamic",
                        "reason": "market phase or desk action changed",
                    },
                    "strategy_book": current_state.get("strategy_book", {}),
                    "orders": current_state.get("execution_log", [])[:3],
                }
            )
        for title, lines in self._ops_alert_lines(previous_state, current_state):
            notifier.send_ops_alert(title, lines)
        for title, lines in self._strategy_hold_alert_lines(current_state):
            notifier.send_ops_alert(title, lines)
        notifier.send_crypto_pilot_alert(self._crypto_pilot_lane(state))

        return {
            "state": current_state,
            "results": [result.model_dump() for result in results],
        }


def _refresh_kis_order(row: dict[str, str]) -> dict[str, str]:
    action = str(row.get("action") or "")
    side_hint = "sell" if action in _SELL_ACTIONS else "buy"
    payload = get_kis_order(
        str(row.get("broker_order_id") or ""),
        symbol=str(row.get("symbol") or ""),
        side_hint=side_hint,
    )
    normalized = normalize_kis_order_state(payload)
    normalized["message"] = str(payload.get("ord_tmd") or payload.get("ORD_TMD") or "")
    return normalized

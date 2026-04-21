from __future__ import annotations

import re

from app.agents.chief_market_officer import CIOAgent
from app.agents.crypto_desk_agent import CryptoDeskAgent
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
    close_positions_for_desk,
    load_closed_positions,
    load_company_state,
    open_or_skip_position,
    save_company_state,
    save_cycle_journal,
    save_paper_orders,
    sync_paper_positions,
    update_positions_unrealized,
)
from app.notifier import notifier
from app.services.market_gateway import get_us_data_status
from app.services.recommendation_engine import build_crypto_plan, build_korea_plan, build_us_plan

_BUY_ACTIONS = {"probe_longs", "attack_opening_drive", "selective_probe"}
_SELL_ACTIONS = {"reduce_risk", "capital_preservation"}


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


def _manage_positions(paper_orders: list[PaperOrder], market_snapshot: dict) -> None:
    prices = _extract_prices(market_snapshot)
    update_positions_unrealized(prices)
    for order in paper_orders:
        if order.action in _BUY_ACTIONS and order.status == "planned":
            symbol = _extract_symbol(order, market_snapshot)
            entry_price = prices.get(symbol, 0.0)
            if symbol and entry_price > 0:
                open_or_skip_position(order.desk, symbol, entry_price, order.notional_pct, order.action)
        elif order.action in _SELL_ACTIONS and order.status == "planned":
            close_positions_for_desk(order.desk, prices)


class CompanyOrchestrator:
    def __init__(self):
        self.analysis_agents = [
            MarketDataAgent(),
            MacroSentimentAgent(),
            TrendStructureAgent(),
            StrategyAllocatorAgent(),
            CryptoDeskAgent(),
            KoreaStockDeskAgent(),
            USStockDeskAgent(),
            CIOAgent(),
            RiskCommitteeAgent(),
        ]
        self.execution_agent = ExecutionAgent()
        self.ops_agent = OpsAgent()

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

        severity = "stable"
        if any(item["level"] == "critical" for item in flags):
            severity = "critical"
        elif any(item["level"] == "warning" for item in flags):
            severity = "warning"
        return {"severity": severity, "items": flags}

    def run_cycle(self) -> dict:
        state = load_company_state()
        previous_state = state.model_dump()
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
        state.execution_mode = "paper"
        state.notes = [
            f"{settings.company_name} operating on {settings.operator_name}'s personal-PC-first stack",
            "paper trading only",
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
            f"korea_gap_candidates={stock_desk_result.payload.get('active_gap_count', 0)}",
            f"us_leaders={us_desk_result.payload.get('active_us_count', 0)}",
        ]
        state.market_snapshot = {
            "as_of": market_data_result.payload.get("as_of"),
            "crypto_leaders": market_data_result.payload.get("crypto_leaders", []),
            "stock_leaders": market_data_result.payload.get("stock_leaders", []),
            "gap_candidates": market_data_result.payload.get("gap_candidates", []),
            "us_leaders": market_data_result.payload.get("us_leaders", []),
        }
        state.session_state = strategy_allocator_result.payload.get("session", {})
        state.desk_views = {
            "crypto_desk": crypto_desk_result.payload,
            "korea_stock_desk": stock_desk_result.payload,
            "us_stock_desk": us_desk_result.payload,
        }
        state.strategy_book = {
            "company_focus": strategy_allocator_result.payload.get("company_focus"),
            "desk_priorities": strategy_allocator_result.payload.get("desk_priorities", []),
            "crypto_plan": build_crypto_plan(state.stance, state.regime, crypto_desk_result.payload),
            "korea_plan": build_korea_plan(
                state.stance,
                state.regime,
                stock_desk_result.payload,
                strategy_allocator_result.payload.get("session", {}),
            ),
            "us_plan": build_us_plan(
                state.stance,
                state.regime,
                us_desk_result.payload,
                strategy_allocator_result.payload.get("session", {}),
            ),
        }
        company_focus = str(state.strategy_book.get("company_focus") or "Capital preservation and watchlist maintenance")
        provisional_allow_new_entries = state.regime != "STRESSED" and (
            float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0)
            + float(state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0)
            > -1.5
        )
        self.execution_agent.configure(
            strategy_book=state.strategy_book,
            regime=state.regime,
            market_snapshot=state.market_snapshot,
            open_positions=state.open_positions,
            closed_positions=load_closed_positions(limit=12),
            allow_new_entries=provisional_allow_new_entries,
            risk_budget=state.risk_budget,
        )
        execution_result = self.execution_agent.safe_run()
        ops_result = self.ops_agent.safe_run()
        results.extend([execution_result, ops_result])
        paper_orders = [PaperOrder.model_validate(item) for item in execution_result.payload.get("orders", [])]
        save_paper_orders(paper_orders)
        sync_paper_positions(paper_orders=paper_orders, market_snapshot=state.market_snapshot)
        _manage_positions(paper_orders, state.market_snapshot)
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
        save_company_state(state)
        current_state = state.model_dump()
        current_state["ops_flags"] = self._ops_flag_snapshot(current_state)
        notifier.send_cycle_summary(previous_state=previous_state, current_state=state.model_dump())
        if self._risk_alert_needed(previous_state, current_state):
            notifier.send_risk_alert(current_state)
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

        return {
            "state": current_state,
            "results": [result.model_dump() for result in results],
        }

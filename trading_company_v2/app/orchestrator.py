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
from app.config import settings
from app.core.models import AgentResult, AgentSnapshot, CompanyState, CycleJournalEntry, PaperOrder
from app.core.state_store import (
    close_positions_for_desk,
    load_company_state,
    open_or_skip_position,
    save_company_state,
    save_cycle_journal,
    save_paper_orders,
    update_positions_unrealized,
)
from app.notifier import notifier
from app.services.recommendation_engine import build_crypto_plan, build_korea_plan

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
        if order.action in _BUY_ACTIONS:
            symbol = _extract_symbol(order, market_snapshot)
            entry_price = prices.get(symbol, 0.0)
            if symbol and entry_price > 0:
                open_or_skip_position(order.desk, symbol, entry_price, order.notional_pct, order.action)
        elif order.action in _SELL_ACTIONS:
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
        ]
        state.market_snapshot = {
            "as_of": market_data_result.payload.get("as_of"),
            "crypto_leaders": market_data_result.payload.get("crypto_leaders", []),
            "stock_leaders": market_data_result.payload.get("stock_leaders", []),
            "gap_candidates": market_data_result.payload.get("gap_candidates", []),
        }
        state.session_state = strategy_allocator_result.payload.get("session", {})
        state.desk_views = {
            "crypto_desk": crypto_desk_result.payload,
            "korea_stock_desk": stock_desk_result.payload,
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
        }
        company_focus = str(state.strategy_book.get("company_focus") or "Capital preservation and watchlist maintenance")
        self.execution_agent.configure(strategy_book=state.strategy_book, regime=state.regime)
        execution_result = self.execution_agent.safe_run()
        ops_result = self.ops_agent.safe_run()
        results.extend([execution_result, ops_result])
        paper_orders = [PaperOrder.model_validate(item) for item in execution_result.payload.get("orders", [])]
        save_paper_orders(paper_orders)
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
        notifier.send_cycle_summary(previous_state=previous_state, current_state=state.model_dump())

        return {
            "state": state.model_dump(),
            "results": [result.model_dump() for result in results],
        }

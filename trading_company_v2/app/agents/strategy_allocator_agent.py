from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.session_clock import current_session_snapshot


class StrategyAllocatorAgent(BaseAgent):
    def __init__(self):
        super().__init__("strategy_allocator_agent")

    def run(self) -> AgentResult:
        session = current_session_snapshot()
        priorities: list[str] = []
        if session["korea_open"]:
            priorities.append("KOSDAQ opening drive and liquidity leaders")
        if session["us_premarket"] or session["us_regular"]:
            priorities.append("U.S. core leaders and ETF momentum rotation")
        if session["crypto_focus"] and not session["us_regular"]:
            priorities.append("BTC-led crypto continuation and risk rotation")
        elif session["crypto_focus"]:
            priorities.append("Crypto watch and non-Korea market continuity")
        if not priorities:
            priorities.append("Capital preservation and watchlist maintenance")

        return AgentResult(
            name=self.name,
            score=0.7,
            reason="session-aware allocator assigned desk focus for this cycle",
            payload={
                "session": session,
                "desk_priorities": priorities,
                "company_focus": priorities[0],
            },
        )

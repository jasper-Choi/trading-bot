from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult, PaperOrder


class ExecutionAgent(BaseAgent):
    def __init__(self):
        super().__init__("execution_agent")

    def run(self) -> AgentResult:
        orders = [
            PaperOrder(
                desk="crypto",
                action="watchlist_only",
                focus="KRW-BTC continuation board",
                size="0.35x",
                rationale=["paper mode", "waiting for stronger crypto bias"],
            ).model_dump(),
            PaperOrder(
                desk="korea",
                action="stand_by",
                focus="KOSDAQ opening-drive board",
                size="0.00x",
                rationale=["paper mode", "only execute after desk confirmation"],
            ).model_dump(),
        ]
        return AgentResult(
            name=self.name,
            score=1.0,
            reason="paper execution ledger active, real broker routing intentionally disabled",
            payload={"mode": "paper", "orders_sent": len(orders), "broker_live": False, "orders": orders},
        )

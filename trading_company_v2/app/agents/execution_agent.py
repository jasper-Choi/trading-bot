from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult


class ExecutionAgent(BaseAgent):
    def __init__(self):
        super().__init__("execution_agent")

    def run(self) -> AgentResult:
        return AgentResult(
            name=self.name,
            score=1.0,
            reason="paper execution ledger active, real broker routing intentionally disabled",
            payload={"mode": "paper", "orders_sent": 0, "broker_live": False},
        )

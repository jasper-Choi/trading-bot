from __future__ import annotations

from app.agents.base import BaseAgent
from app.config import settings
from app.core.models import AgentResult


class OpsAgent(BaseAgent):
    def __init__(self):
        super().__init__("ops_agent")

    def run(self) -> AgentResult:
        return AgentResult(
            name=self.name,
            score=1.0,
            reason="ops agent online for home PC runtime and mobile check-ins",
            payload={
                "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
                "notify_every_cycle": settings.telegram_notify_every_cycle,
                "operator": settings.operator_name,
            },
        )

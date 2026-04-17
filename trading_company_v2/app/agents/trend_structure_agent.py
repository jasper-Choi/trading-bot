from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult


class TrendStructureAgent(BaseAgent):
    def __init__(self):
        super().__init__("trend_structure_agent")

    def run(self) -> AgentResult:
        return AgentResult(
            name=self.name,
            score=0.58,
            reason="Linda Raschke style structure check favors selective continuation setups",
            payload={
                "trend_bias": "mild_bullish",
                "breakout_quality": "moderate",
                "principle": "price first",
            },
        )

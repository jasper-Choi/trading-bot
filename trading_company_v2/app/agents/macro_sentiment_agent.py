from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult


class MacroSentimentAgent(BaseAgent):
    def __init__(self):
        super().__init__("macro_sentiment_agent")

    def run(self) -> AgentResult:
        return AgentResult(
            name=self.name,
            score=0.45,
            reason="Paul Tudor Jones style defensive bias until macro/news connectors are live",
            payload={
                "macro_bias": "cautious",
                "stress_level": "medium",
                "principle": "defense first",
            },
        )

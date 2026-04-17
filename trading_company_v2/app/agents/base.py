from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.models import AgentResult


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def run(self) -> AgentResult:
        raise NotImplementedError

    def safe_run(self) -> AgentResult:
        try:
            return self.run()
        except Exception as exc:
            return AgentResult(name=self.name, score=0.5, reason=f"error: {exc}", payload={})

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.insight_agents.base_agent import BaseAgent

from .state import update_agent_status


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class TradingAgent(BaseAgent):
    """Base class for stateful trading agents built on top of BaseAgent."""

    def __init__(self, name: str):
        super().__init__(name=name)

    def safe_run(self) -> dict:
        result = super().safe_run()
        status = "ok" if not str(result.get("reason", "")).startswith("error:") else "error"
        summary = {
            "score": result.get("score"),
            "reason": result.get("reason"),
            "updated_at": utcnow_iso(),
        }
        update_agent_status(self.name, status=status, summary=summary)
        return result

    def state_payload(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"updated_at": utcnow_iso()}
        payload.update(kwargs)
        return payload

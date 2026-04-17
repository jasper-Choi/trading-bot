from __future__ import annotations

from typing import Any

import config
from src.market_regime import BEAR, VOLATILE, market_regime

from .base import TradingAgent, utcnow_iso
from .state import merge_state


class CEOAgent(TradingAgent):
    """Top-level strategy allocator that converts insights into a stance."""

    def __init__(self):
        super().__init__(name="ceo_agent")

    def run(self) -> dict:
        insight = self._load_insight()
        insight_score = float(insight.get("insight_score", 0.5))
        regime = market_regime.regime

        direction = self._decide_direction(insight_score=insight_score, regime=regime)
        risk_budget = self._risk_budget(direction)
        notes = self._build_notes(insight_score=insight_score, regime=regime, direction=direction)

        payload: dict[str, Any] = {
            "strategy": {
                "direction": direction,
                "insight_score": insight_score,
                "market_regime": regime,
                "risk_budget": risk_budget,
                "notes": notes,
                "last_decision_at": utcnow_iso(),
            }
        }
        merge_state(payload)

        return {
            "score": insight_score,
            "reason": f"{direction.lower()} stance from insight {insight_score:.2f} in {regime}",
            "raw": {
                "direction": direction,
                "market_regime": regime,
                "risk_budget": risk_budget,
                "notes": notes,
                "insight": insight,
            },
        }

    @staticmethod
    def _load_insight() -> dict:
        try:
            from src.insight_agents.orchestrator import OrchestratorAgent
        except ModuleNotFoundError as exc:
            return {
                "insight_score": 0.5,
                "timestamp": utcnow_iso(),
                "agents": {},
                "fallback_reason": f"missing dependency: {exc.name}",
            }
        return OrchestratorAgent().run()

    @staticmethod
    def _decide_direction(insight_score: float, regime: str) -> str:
        if regime == VOLATILE:
            return "DEFENSIVE"
        if regime == BEAR and insight_score < 0.55:
            return "DEFENSIVE"
        if insight_score >= 0.65 and regime != BEAR:
            return "AGGRESSIVE"
        if insight_score <= 0.35:
            return "DEFENSIVE"
        return "NEUTRAL"

    @staticmethod
    def _risk_budget(direction: str) -> float:
        if direction == "AGGRESSIVE":
            return round(config.DAILY_LOSS_LIMIT_PCT, 4)
        if direction == "DEFENSIVE":
            return round(config.DAILY_LOSS_LIMIT_PCT * 0.5, 4)
        return round(config.DAILY_LOSS_LIMIT_PCT * 0.75, 4)

    @staticmethod
    def _build_notes(insight_score: float, regime: str, direction: str) -> list[str]:
        notes = [
            f"market_regime={regime}",
            f"insight_score={insight_score:.4f}",
            f"daily_loss_limit_pct={config.DAILY_LOSS_LIMIT_PCT:.4f}",
        ]
        if direction == "AGGRESSIVE":
            notes.append("expand risk within configured loss limit")
        elif direction == "DEFENSIVE":
            notes.append("favor capital preservation and tighter filters")
        else:
            notes.append("keep baseline allocations and entries")
        return notes

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


MarketStance = Literal["OFFENSE", "BALANCED", "DEFENSE"]
MarketRegime = Literal["TRENDING", "RANGING", "STRESSED"]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AgentResult(BaseModel):
    name: str
    score: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str
    payload: dict = Field(default_factory=dict)
    generated_at: str = Field(default_factory=utcnow_iso)


class AgentSnapshot(BaseModel):
    name: str
    score: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str
    payload: dict = Field(default_factory=dict)
    generated_at: str = Field(default_factory=utcnow_iso)


class CompanyState(BaseModel):
    stance: MarketStance = "BALANCED"
    regime: MarketRegime = "RANGING"
    risk_budget: float = 0.5
    allow_new_entries: bool = True
    execution_mode: str = "paper"
    notes: list[str] = Field(default_factory=list)
    trader_principles: list[str] = Field(default_factory=list)
    latest_signals: list[str] = Field(default_factory=list)
    agent_runs: list[AgentSnapshot] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utcnow_iso)

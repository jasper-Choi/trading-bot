from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult, CompanyState


class RiskCommitteeAgent(BaseAgent):
    def __init__(self):
        super().__init__("risk_committee_agent")

    def run(self) -> AgentResult:
        return AgentResult(
            name=self.name,
            score=1.0,
            reason="Ed Seykota style risk control online with capped gross exposure",
            payload={"allow_new_entries": True, "risk_budget": 0.4, "max_daily_loss_pct": 1.5},
        )

    def apply(self, state: CompanyState) -> CompanyState:
        state.allow_new_entries = state.regime != "STRESSED"
        if state.stance == "OFFENSE":
            state.risk_budget = min(state.risk_budget, 0.65)
        elif state.stance == "DEFENSE":
            state.risk_budget = min(state.risk_budget, 0.25)
        else:
            state.risk_budget = min(state.risk_budget, 0.4)
        if "risk committee enforcing conservative defaults" not in state.notes:
            state.notes.append("risk committee enforcing conservative defaults")
        if not state.allow_new_entries and "new entries blocked under stressed regime" not in state.notes:
            state.notes.append("new entries blocked under stressed regime")
        return state

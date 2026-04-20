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
        combined_pnl = float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0) + float(
            state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0
        )
        gross_open_notional = float(state.daily_summary.get("gross_open_notional_pct", 0.0) or 0.0)
        wins = int(state.daily_summary.get("wins", 0) or 0)
        losses = int(state.daily_summary.get("losses", 0) or 0)
        state.allow_new_entries = state.regime != "STRESSED" and combined_pnl > -1.5
        if state.stance == "OFFENSE":
            state.risk_budget = min(state.risk_budget, 0.65)
        elif state.stance == "DEFENSE":
            state.risk_budget = min(state.risk_budget, 0.25)
        else:
            state.risk_budget = min(state.risk_budget, 0.4)
        if combined_pnl < 0:
            state.risk_budget = min(state.risk_budget, 0.3)
        if combined_pnl <= -0.75 or losses > wins:
            state.risk_budget = min(state.risk_budget, 0.2)
        if gross_open_notional >= 0.9:
            state.risk_budget = min(state.risk_budget, 0.18)
        if gross_open_notional >= 1.05:
            state.allow_new_entries = False
        if "risk committee enforcing conservative defaults" not in state.notes:
            state.notes.append("risk committee enforcing conservative defaults")
        if gross_open_notional >= 0.9 and "risk committee reduced sizing due to gross exposure" not in state.notes:
            state.notes.append("risk committee reduced sizing due to gross exposure")
        if not state.allow_new_entries and "new entries blocked under stressed regime" not in state.notes:
            block_reason = (
                "new entries blocked under stressed regime"
                if state.regime == "STRESSED"
                else "new entries blocked after daily drawdown or exposure breach"
            )
            if block_reason not in state.notes:
                state.notes.append(block_reason)
        return state

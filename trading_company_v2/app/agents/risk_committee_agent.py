from __future__ import annotations

from app.agents.base import BaseAgent
from app.config import settings
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
        active_desks = set((state.strategy_book or {}).get("active_desks") or settings.active_desk_set)
        desk_stats = state.daily_summary.get("desk_stats", {}) or {}
        if active_desks:
            combined_pnl = sum(
                float((desk_stats.get(desk, {}) or {}).get("realized_pnl_pct", 0.0) or 0.0)
                + float((desk_stats.get(desk, {}) or {}).get("unrealized_pnl_pct", 0.0) or 0.0)
                for desk in active_desks
            )
            gross_open_notional = sum(
                float((desk_stats.get(desk, {}) or {}).get("open_notional_pct", 0.0) or 0.0)
                for desk in active_desks
            )
            wins = sum(int((desk_stats.get(desk, {}) or {}).get("wins", 0) or 0) for desk in active_desks)
            losses = sum(int((desk_stats.get(desk, {}) or {}).get("losses", 0) or 0) for desk in active_desks)
        else:
            combined_pnl = float(state.daily_summary.get("realized_pnl_pct", 0.0) or 0.0) + float(
                state.daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0
            )
            gross_open_notional = float(state.daily_summary.get("gross_open_notional_pct", 0.0) or 0.0)
            wins = int(state.daily_summary.get("wins", 0) or 0)
            losses = int(state.daily_summary.get("losses", 0) or 0)
        capital_profile = state.strategy_book.get("capital_profile", {}) or {}
        compounding_mode = str(capital_profile.get("mode", "neutral") or "neutral")
        profit_buffer_pct = float(capital_profile.get("profit_buffer_pct", 0.0) or 0.0)
        global_multiplier = float(capital_profile.get("global_multiplier", 1.0) or 1.0)
        drawdown_entry_floor = -6.0 if active_desks == {"crypto"} else -1.5
        state.allow_new_entries = state.regime != "STRESSED" and combined_pnl > drawdown_entry_floor
        if active_desks == {"crypto"} and combined_pnl <= -1.5 and state.allow_new_entries:
            note = f"crypto recovery mode keeps entries open at throttled risk ({combined_pnl:.2f}% active P&L)"
            if note not in state.notes:
                state.notes.append(note)
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
        exposure_warn = 1.35 if active_desks == {"crypto"} else 0.9
        exposure_block = 1.65 if active_desks == {"crypto"} else 1.05
        if gross_open_notional >= exposure_warn:
            state.risk_budget = min(state.risk_budget, 0.18)
        if gross_open_notional >= exposure_block:
            state.allow_new_entries = False
        if compounding_mode in {"drift_up", "measured_press", "press_advantage"} and state.allow_new_entries:
            compounding_cap = min(0.72, state.risk_budget + profit_buffer_pct)
            boosted_budget = min(state.risk_budget * global_multiplier, compounding_cap)
            state.risk_budget = round(max(state.risk_budget, boosted_budget), 2)
        if compounding_mode == "capital_protect":
            state.risk_budget = min(state.risk_budget, 0.18)
        if "risk committee enforcing conservative defaults" not in state.notes:
            state.notes.append("risk committee enforcing conservative defaults")
        if compounding_mode in {"drift_up", "measured_press", "press_advantage"}:
            note = f"capital compounding mode {compounding_mode} active with budget {state.risk_budget:.2f}"
            if note not in state.notes:
                state.notes.append(note)
        elif compounding_mode == "capital_protect":
            note = "capital compounding paused while edge is weak"
            if note not in state.notes:
                state.notes.append(note)
        if gross_open_notional >= exposure_warn and "risk committee reduced sizing due to gross exposure" not in state.notes:
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

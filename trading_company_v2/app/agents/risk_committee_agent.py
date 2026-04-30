from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.agents.base import BaseAgent
from app.config import settings
from app.core.models import AgentResult, CompanyState
from app.core.state_store import load_current_loss_streak, load_hourly_win_rates

_KST = ZoneInfo("Asia/Seoul")


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
        drawdown_entry_floor = -20.0 if active_desks == {"crypto"} else -1.5
        state.allow_new_entries = state.regime != "STRESSED" and combined_pnl > drawdown_entry_floor
        if active_desks == {"crypto"} and combined_pnl <= -1.5 and state.allow_new_entries:
            note = f"crypto recovery mode keeps entries open at throttled risk ({combined_pnl:.2f}% active P&L)"
            if note not in state.notes:
                state.notes.append(note)
        crypto_growth_mode = active_desks == {"crypto"}
        if state.stance == "OFFENSE":
            state.risk_budget = min(state.risk_budget, 0.72 if crypto_growth_mode else 0.65)
        elif state.stance == "DEFENSE":
            state.risk_budget = min(state.risk_budget, 0.30 if crypto_growth_mode else 0.25)
        else:
            state.risk_budget = min(state.risk_budget, 0.48 if crypto_growth_mode else 0.4)
        if combined_pnl < 0:
            state.risk_budget = min(state.risk_budget, 0.36 if crypto_growth_mode else 0.3)
        if combined_pnl <= -0.75 or losses > wins:
            state.risk_budget = min(state.risk_budget, 0.28 if crypto_growth_mode else 0.2)
        exposure_warn = 1.65 if crypto_growth_mode else 0.9
        exposure_block = 2.05 if crypto_growth_mode else 1.05
        if gross_open_notional >= exposure_warn:
            state.risk_budget = min(state.risk_budget, 0.24 if crypto_growth_mode else 0.18)
        if gross_open_notional >= exposure_block:
            state.allow_new_entries = False
        if compounding_mode in {"drift_up", "measured_press", "press_advantage"} and state.allow_new_entries:
            compounding_cap = min(0.72, state.risk_budget + profit_buffer_pct)
            boosted_budget = min(state.risk_budget * global_multiplier, compounding_cap)
            state.risk_budget = round(max(state.risk_budget, boosted_budget), 2)
        if compounding_mode == "capital_protect":
            state.risk_budget = min(state.risk_budget, 0.22 if crypto_growth_mode else 0.18)
        # ── 연패 후 사이징 축소 ──────────────────────────────────────────
        # 3연패부터 risk_budget을 10%씩 감산 (최대 45% 감산, floor 55%)
        # 연승이 다시 나오면 다음 사이클에서 자동 해제됨
        try:
            loss_streak = load_current_loss_streak(desk="crypto")
        except Exception:
            loss_streak = 0
        if loss_streak >= 3:
            streak_mult = max(0.55, 1.0 - (loss_streak - 2) * 0.10)
            state.risk_budget = round(state.risk_budget * streak_mult, 2)
            streak_note = f"risk budget reduced to {streak_mult:.0%} after {loss_streak}-loss streak"
            if streak_note not in state.notes:
                state.notes.append(streak_note)

        # ── 시간대별 소프트 필터 ────────────────────────────────────────
        # 최근 30일 기준으로 현재 시간대 승률이 35% 미만 + 샘플 5건 이상이면
        # risk_budget을 15% 소프트 감산 (하드 블록 아님 — 시장은 매일 다름)
        try:
            current_hour = datetime.now(_KST).hour
            hourly_stats = load_hourly_win_rates(desk="crypto", days=30)
            hour_data = hourly_stats.get(current_hour)
            if hour_data and hour_data["win_rate"] < 0.35 and hour_data["trades"] >= 5:
                state.risk_budget = round(state.risk_budget * 0.85, 2)
                hour_note = (
                    f"risk budget -15% (hour {current_hour:02d}:xx win_rate "
                    f"{hour_data['win_rate']:.0%} over {hour_data['trades']} trades)"
                )
                if hour_note not in state.notes:
                    state.notes.append(hour_note)
        except Exception:
            pass
        if crypto_growth_mode and state.allow_new_entries:
            # Crypto-only growth mode should throttle after losses, not suffocate.
            # The execution/hot-path guards still cap exposure and cut failures fast.
            state.risk_budget = max(state.risk_budget, 0.32)

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
        # Purge stale adaptive notes every cycle so they don't accumulate.
        # Streak notes: removed when streak recovers (< 3). Hourly notes: always
        # removed and re-added fresh (the text changes each cycle as conditions change).
        state.notes = [
            n for n in state.notes
            if not (n.startswith("risk budget reduced") and loss_streak < 3)
            and not n.startswith("risk budget -15% (hour")
        ]
        _stale_block_notes = {
            "new entries blocked after daily drawdown or exposure breach",
            "new entries blocked under stressed regime",
        }
        if state.allow_new_entries:
            state.notes = [n for n in state.notes if n not in _stale_block_notes]
        else:
            block_reason = (
                "new entries blocked under stressed regime"
                if state.regime == "STRESSED"
                else "new entries blocked after daily drawdown or exposure breach"
            )
            if block_reason not in state.notes:
                state.notes.append(block_reason)
        return state

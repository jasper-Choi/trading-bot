from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.core.models import AgentResult


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def build_compounding_profile(stance: str, regime: str, daily_summary: dict[str, Any] | None) -> dict[str, Any]:
    daily_summary = daily_summary or {}
    realized = float(daily_summary.get("realized_pnl_pct", 0.0) or 0.0)
    unrealized = float(daily_summary.get("unrealized_pnl_pct", 0.0) or 0.0)
    expectancy = float(daily_summary.get("expectancy_pct", 0.0) or 0.0)
    win_rate = float(daily_summary.get("win_rate", 0.0) or 0.0)
    closed_positions = int(daily_summary.get("closed_positions", 0) or 0)
    gross_open_notional = float(daily_summary.get("gross_open_notional_pct", 0.0) or 0.0)
    desk_stats = daily_summary.get("desk_stats", {}) or {}

    multiplier = 1.0
    mode = "neutral"
    notes = ["keep compounding dormant until edge is proven"]

    aligned_day = closed_positions >= 3 and expectancy >= 0.18 and win_rate >= 50.0 and realized >= 0.6
    strong_day = closed_positions >= 4 and expectancy >= 0.35 and win_rate >= 54.0 and realized >= 1.2
    exceptional_day = closed_positions >= 5 and expectancy >= 0.55 and win_rate >= 58.0 and realized >= 2.4

    if regime == "STRESSED" or realized <= -0.75 or expectancy < 0:
        multiplier = 0.9
        mode = "capital_protect"
        notes = ["compounding paused because risk conditions are not supportive"]
    elif exceptional_day and gross_open_notional <= 0.7:
        multiplier = 1.22
        mode = "press_advantage"
        notes = ["press only proven strength and fund added size from realized gains"]
    elif strong_day and gross_open_notional <= 0.8:
        multiplier = 1.14
        mode = "measured_press"
        notes = ["increase size modestly while expectancy and win rate stay supportive"]
    elif aligned_day and gross_open_notional <= 0.9:
        multiplier = 1.08
        mode = "drift_up"
        notes = ["allow a small offense increase on a green, aligned day"]

    profit_buffer_pct = _clamp(realized * 0.18, 0.0, 0.14)
    if mode == "capital_protect":
        profit_buffer_pct = 0.0
    if unrealized < -0.4:
        multiplier = min(multiplier, 1.0)
        notes.append("unrealized drawdown cancels fresh offense escalation")

    desk_multipliers: dict[str, float] = {}
    for desk_name in ("crypto", "korea", "us"):
        stats = desk_stats.get(desk_name, {}) or {}
        desk_realized = float(stats.get("realized_pnl_pct", 0.0) or 0.0)
        desk_win_rate = float(stats.get("win_rate", 0.0) or 0.0)
        desk_closed = int(stats.get("closed_positions", 0) or 0)

        desk_multiplier = 1.0
        if desk_closed >= 3 and desk_realized >= 1.0 and desk_win_rate >= 50.0 and mode in {"drift_up", "measured_press", "press_advantage"}:
            desk_multiplier = 1.12
        if desk_closed >= 4 and desk_realized >= 2.0 and desk_win_rate >= 55.0 and mode in {"measured_press", "press_advantage"}:
            desk_multiplier = 1.2
        if desk_realized <= -1.0 or (desk_closed >= 4 and desk_win_rate < 35.0):
            desk_multiplier = 0.82
        desk_multipliers[desk_name] = round(desk_multiplier, 2)

    return {
        "mode": mode,
        "global_multiplier": round(multiplier, 2),
        "profit_buffer_pct": round(profit_buffer_pct, 2),
        "desk_multipliers": desk_multipliers,
        "notes": notes,
        "stance_hint": "OFFENSE" if mode in {"measured_press", "press_advantage"} else stance,
    }


class CIOAgent(BaseAgent):
    def __init__(self):
        super().__init__("cio_agent")

    def run(self) -> AgentResult:
        return AgentResult(
            name=self.name,
            score=0.6,
            reason="Druckenmiller-style sizing framework ready: press only when macro and structure align",
            payload={
                "stance_hint": "BALANCED",
                "capital_policy": "concentrate only on aligned conviction",
                "principle": "conviction-weighted sizing",
                "compounding_policy": "raise size only after proven expectancy and realized gains",
            },
        )

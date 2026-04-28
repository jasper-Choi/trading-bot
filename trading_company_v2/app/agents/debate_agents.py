from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agents.base import BaseAgent
from app.core.models import AgentResult, CompanyState

_ENTRY_ACTIONS = {"probe_longs", "attack_opening_drive", "selective_probe"}
_PASSIVE_ACTIONS = {"stand_by", "watchlist_only", "pre_market_watch"}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _size_to_float(size: str) -> float:
    try:
        return float(str(size or "0").replace("x", ""))
    except ValueError:
        return 0.0


def _float_to_size(value: float) -> str:
    return f"{max(value, 0.0):.2f}x"


def _desk_inputs(state: CompanyState, desk: str) -> tuple[dict, dict]:
    plan_key = f"{desk}_plan"
    view_key = "korea_stock_desk" if desk == "korea" else f"{desk}_desk"
    return state.strategy_book.get(plan_key, {}) or {}, state.desk_views.get(view_key, {}) or {}


class BullCaseAgent(BaseAgent):
    def __init__(self):
        super().__init__("bull_case_agent")
        self.state: CompanyState | None = None

    def configure(self, state: CompanyState) -> None:
        self.state = state

    def _score_crypto(self, plan: dict, view: dict) -> tuple[float, list[str]]:
        action = str(plan.get("action", "watchlist_only"))
        signal = _f(view.get("signal_score"), 0.5)
        micro = _f(view.get("micro_score"), 0.0)
        orderbook = _f(view.get("orderbook_score"), 0.0)
        breakout_count = int(view.get("breakout_count", 0) or 0)
        discovery = max(_f(item.get("discovery_score"), 0.0) for item in view.get("ranked_candidates", [])[:3] or [{}])
        score = 0.24 + signal * 0.34 + micro * 0.16 + orderbook * 0.08 + min(breakout_count, 4) * 0.035 + discovery * 0.10
        if action in _ENTRY_ACTIONS:
            score += 0.08
        reasons = [
            f"crypto signal={signal:.2f}",
            f"micro={micro:.2f}",
            f"orderbook={orderbook:.2f}",
            f"breakout={breakout_count}/4",
        ]
        if discovery:
            reasons.append(f"full-universe discovery={discovery:.2f}")
        return _clamp(score), reasons

    def _score_korea(self, plan: dict, view: dict) -> tuple[float, list[str]]:
        action = str(plan.get("action", "stand_by"))
        quality = _f(view.get("quality_score"), 0.0)
        avg_signal = _f(view.get("avg_signal_score_top3"), 0.0)
        active_gap = int(view.get("active_gap_count", 0) or 0)
        breakout_confirmed = int(view.get("breakout_confirmed_count", 0) or 0)
        breakout_partial = int(view.get("breakout_partial_count", 0) or 0)
        score = 0.22 + quality * 0.42 + avg_signal * 0.18 + min(active_gap, 4) * 0.025
        score += min(breakout_confirmed, 2) * 0.10 + min(breakout_partial, 2) * 0.05
        if action in _ENTRY_ACTIONS:
            score += 0.06
        return _clamp(score), [
            f"korea quality={quality:.2f}",
            f"avg_signal={avg_signal:.2f}",
            f"gaps={active_gap}",
            f"breakouts={breakout_confirmed}+{breakout_partial}",
        ]

    def _score_us(self, plan: dict, view: dict) -> tuple[float, list[str]]:
        action = str(plan.get("action", "stand_by"))
        quality = _f(view.get("quality_score"), 0.0)
        avg_signal = _f(view.get("avg_signal_score_top3"), 0.0)
        active = int(view.get("active_us_count", 0) or 0)
        avg_change = _f(view.get("avg_change_pct_top3"), 0.0)
        score = 0.20 + quality * 0.42 + avg_signal * 0.18 + min(active, 5) * 0.025 + max(avg_change, 0.0) * 0.035
        if action in _ENTRY_ACTIONS:
            score += 0.05
        return _clamp(score), [
            f"us quality={quality:.2f}",
            f"avg_signal={avg_signal:.2f}",
            f"leaders={active}",
            f"avg_change={avg_change:.2f}%",
        ]

    def run(self) -> AgentResult:
        if self.state is None:
            return AgentResult(name=self.name, score=0.5, reason="not configured", payload={})
        desks = {}
        score_sum = 0.0
        for desk, scorer in (("crypto", self._score_crypto), ("korea", self._score_korea), ("us", self._score_us)):
            plan, view = _desk_inputs(self.state, desk)
            score, reasons = scorer(plan, view)
            desks[desk] = {"score": round(score, 3), "reasons": reasons, "action": plan.get("action")}
            score_sum += score
        avg_score = round(score_sum / 3, 3)
        return AgentResult(
            name=self.name,
            score=avg_score,
            reason="bull case weighs momentum, volatility expansion, liquidity and setup confirmation",
            payload={"desks": desks},
        )


class BearCaseAgent(BaseAgent):
    def __init__(self):
        super().__init__("bear_case_agent")
        self.state: CompanyState | None = None

    def configure(self, state: CompanyState) -> None:
        self.state = state

    def _shared_risk(self) -> tuple[float, list[str]]:
        if self.state is None:
            return 0.0, []
        daily = self.state.daily_summary or {}
        combined = _f(daily.get("realized_pnl_pct")) + _f(daily.get("unrealized_pnl_pct"))
        gross = _f(daily.get("gross_open_notional_pct"))
        wins = int(daily.get("cumulative_wins", daily.get("wins", 0)) or 0)
        losses = int(daily.get("cumulative_losses", daily.get("losses", 0)) or 0)
        win_rate = _f(daily.get("cumulative_win_rate", daily.get("win_rate", 0.0)))
        risk = 0.0
        reasons: list[str] = []
        if self.state.regime == "STRESSED":
            risk += 0.35
            reasons.append("market regime is stressed")
        if losses >= 3 and losses > wins and win_rate < 35.0:
            risk += 0.18
            reasons.append(f"loss streak control active ({wins}W/{losses}L, win_rate {win_rate:.1f}%)")
        if combined < -0.75:
            risk += 0.16
            reasons.append(f"combined pnl pressure {combined:.2f}%")
        if gross >= 1.05:
            risk += 0.20
            reasons.append(f"gross exposure {gross:.2f}x")
        elif gross >= 0.85:
            risk += 0.10
            reasons.append(f"gross exposure elevated {gross:.2f}x")
        if not self.state.allow_new_entries:
            risk += 0.16
            reasons.append("entry gate currently closed")
        return risk, reasons

    def _score_crypto(self, plan: dict, view: dict) -> tuple[float, list[str]]:
        shared, reasons = self._shared_risk()
        recent = _f(view.get("recent_change_pct"))
        burst = _f(view.get("burst_change_pct"))
        ema_gap = _f(view.get("ema_gap_pct"))
        rsi = view.get("rsi")
        score = 0.18 + shared
        if recent >= 3.4 or burst >= 3.8:
            score += 0.18
            reasons.append(f"late chase risk recent={recent:.2f}% burst={burst:.2f}%")
        if ema_gap >= 2.8:
            score += 0.10
            reasons.append(f"ema gap extended {ema_gap:.2f}%")
        if rsi is not None and _f(rsi) >= 80.0:
            score += 0.13
            reasons.append(f"rsi overheat {float(rsi):.1f}")
        if not bool(view.get("rsi_quality_ok", True)):
            score += 0.12
            reasons.append("rsi quality failed")
        if str(plan.get("action")) in _PASSIVE_ACTIONS:
            score += 0.04
        return _clamp(score), reasons or ["no major crypto bear case"]

    def _score_korea(self, plan: dict, view: dict) -> tuple[float, list[str]]:
        shared, reasons = self._shared_risk()
        quality = _f(view.get("quality_score"))
        avg_volume = _f(view.get("avg_volume_top3"))
        active_gap = int(view.get("active_gap_count", 0) or 0)
        score = 0.16 + shared
        if quality < 0.50:
            score += 0.18
            reasons.append(f"low korea quality {quality:.2f}")
        if active_gap == 0:
            score += 0.10
            reasons.append("no active gap candidate")
        if 0 < avg_volume < 3500:
            score += 0.08
            reasons.append(f"thin volume {avg_volume:.0f}")
        if str(plan.get("action")) in _PASSIVE_ACTIONS:
            score += 0.04
        return _clamp(score), reasons or ["no major korea bear case"]

    def _score_us(self, plan: dict, view: dict) -> tuple[float, list[str]]:
        shared, reasons = self._shared_risk()
        quality = _f(view.get("quality_score"))
        active = int(view.get("active_us_count", 0) or 0)
        score = 0.16 + shared
        if quality < 0.62:
            score += 0.16
            reasons.append(f"low us quality {quality:.2f}")
        if active < 2:
            score += 0.10
            reasons.append(f"too few us leaders {active}")
        if str(plan.get("action")) in _PASSIVE_ACTIONS:
            score += 0.04
        return _clamp(score), reasons or ["no major us bear case"]

    def run(self) -> AgentResult:
        if self.state is None:
            return AgentResult(name=self.name, score=0.5, reason="not configured", payload={})
        desks = {}
        score_sum = 0.0
        for desk, scorer in (("crypto", self._score_crypto), ("korea", self._score_korea), ("us", self._score_us)):
            plan, view = _desk_inputs(self.state, desk)
            score, reasons = scorer(plan, view)
            desks[desk] = {"score": round(score, 3), "reasons": reasons[:4], "action": plan.get("action")}
            score_sum += score
        avg_score = round(score_sum / 3, 3)
        return AgentResult(
            name=self.name,
            score=avg_score,
            reason="bear case weighs late-chase, weak confirmation, drawdown and exposure risks",
            payload={"desks": desks},
        )


class PortfolioManagerAgent(BaseAgent):
    def __init__(self):
        super().__init__("portfolio_manager_agent")
        self.state: CompanyState | None = None
        self.bull_payload: dict[str, Any] = {}
        self.bear_payload: dict[str, Any] = {}

    def configure(self, state: CompanyState, bull_payload: dict[str, Any], bear_payload: dict[str, Any]) -> None:
        self.state = state
        self.bull_payload = bull_payload or {}
        self.bear_payload = bear_payload or {}

    def _loss_control_active(self) -> bool:
        if self.state is None:
            return False
        daily = self.state.daily_summary or {}
        wins = int(daily.get("cumulative_wins", daily.get("wins", 0)) or 0)
        losses = int(daily.get("cumulative_losses", daily.get("losses", 0)) or 0)
        win_rate = _f(daily.get("cumulative_win_rate", daily.get("win_rate", 0.0)))
        return losses >= 3 and losses > wins and win_rate < 35.0

    def _adjust_plan(self, desk: str, plan: dict) -> tuple[dict, dict]:
        adjusted = dict(plan or {})
        bull = ((self.bull_payload.get("desks", {}) or {}).get(desk, {}) or {})
        bear = ((self.bear_payload.get("desks", {}) or {}).get(desk, {}) or {})
        bull_score = _f(bull.get("score"), 0.5)
        bear_score = _f(bear.get("score"), 0.5)
        action = str(adjusted.get("action", "stand_by"))
        original_size = str(adjusted.get("size", "0.00x"))
        size = _size_to_float(original_size)
        edge = bull_score - bear_score
        decision = "hold"
        multiplier = 1.0
        reason = f"bull={bull_score:.2f} bear={bear_score:.2f} edge={edge:.2f}"
        loss_control = self._loss_control_active()

        if action in _ENTRY_ACTIONS and size > 0:
            if bear_score >= 0.78 and bull_score < 0.72:
                adjusted["action"] = "stand_by"
                adjusted["size"] = "0.00x"
                decision = "block"
                reason += " / severe bear case blocks entry"
            elif edge <= -0.18:
                adjusted["action"] = "selective_probe"
                multiplier = 0.55
                adjusted["size"] = _float_to_size(size * multiplier)
                decision = "cut"
                reason += " / negative edge cuts size"
            elif edge <= 0.05:
                multiplier = 0.75
                adjusted["size"] = _float_to_size(size * multiplier)
                decision = "throttle"
                reason += " / mixed debate throttles size"
            elif loss_control:
                multiplier = 0.72
                adjusted["size"] = _float_to_size(size * multiplier)
                decision = "loss_control"
                reason += " / losing streak disables pressing and cuts size"
            elif bull_score >= 0.74 and bear_score <= 0.46 and self.state and self.state.stance != "DEFENSE":
                multiplier = 1.12 if desk == "crypto" else 1.08
                adjusted["size"] = _float_to_size(size * multiplier)
                decision = "press"
                reason += " / clean bull case presses size"
            else:
                decision = "approve"
                reason += " / approved unchanged"
        elif action in _PASSIVE_ACTIONS and bull_score >= 0.78 and bear_score <= 0.40:
            decision = "watch_upgrade_candidate"
            reason += " / watchlist has upgrade potential next cycle"

        notes = list(adjusted.get("notes", []) or [])
        notes.append(f"portfolio manager debate: {reason}")
        adjusted["notes"] = notes
        return adjusted, {
            "desk": desk,
            "decision": decision,
            "action": adjusted.get("action"),
            "size": adjusted.get("size"),
            "original_size": original_size,
            "size_multiplier": round(multiplier, 2),
            "bull_score": round(bull_score, 3),
            "bear_score": round(bear_score, 3),
            "edge": round(edge, 3),
            "reason": reason,
        }

    def run(self) -> AgentResult:
        if self.state is None:
            return AgentResult(name=self.name, score=0.5, reason="not configured", payload={})
        adjusted_book = deepcopy(self.state.strategy_book or {})
        decisions = []
        for desk in ("crypto", "korea", "us"):
            key = f"{desk}_plan"
            adjusted_book[key], decision = self._adjust_plan(desk, adjusted_book.get(key, {}) or {})
            decisions.append(decision)
        avg_edge = sum(_f(item.get("edge")) for item in decisions) / len(decisions) if decisions else 0.0
        score = _clamp(0.5 + avg_edge * 0.55)
        return AgentResult(
            name=self.name,
            score=round(score, 3),
            reason="portfolio manager applies TradingAgents-style debate before execution",
            payload={"strategy_book": adjusted_book, "decisions": decisions},
        )

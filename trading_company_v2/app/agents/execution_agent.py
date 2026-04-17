from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult, PaperOrder


class ExecutionAgent(BaseAgent):
    def __init__(self):
        super().__init__("execution_agent")
        self.strategy_book: dict = {}
        self.regime: str = "RANGING"

    def configure(self, strategy_book: dict, regime: str) -> None:
        self.strategy_book = strategy_book
        self.regime = regime

    @staticmethod
    def _size_to_notional(size: str) -> float:
        try:
            return float(size.replace("x", ""))
        except ValueError:
            return 0.0

    def _plan_to_order(self, desk: str, plan: dict) -> PaperOrder:
        action = str(plan.get("action", "stand_by"))
        size = str(plan.get("size", "0.00x"))
        notional_pct = self._size_to_notional(size)
        pnl_map = {
            "probe_longs": 0.45,
            "attack_opening_drive": 0.6,
            "selective_probe": 0.2,
            "watchlist_only": 0.05,
            "reduce_risk": -0.05,
            "stand_by": 0.0,
            "capital_preservation": 0.0,
            "pre_market_watch": 0.0,
        }
        pnl_estimate_pct = pnl_map.get(action, 0.0)
        meta = {
            "notional_pct": notional_pct,
            "status": "planned" if action not in {"stand_by", "pre_market_watch"} else "idle",
            "pnl_estimate_pct": pnl_estimate_pct,
        }
        rationale = [meta, *plan.get("notes", [])]
        return PaperOrder(
            desk=desk,
            action=action,
            focus=str(plan.get("focus", "")),
            size=size,
            notional_pct=notional_pct,
            status=meta["status"],
            pnl_estimate_pct=pnl_estimate_pct,
            rationale=rationale,
        )

    def run(self) -> AgentResult:
        crypto_plan = self.strategy_book.get("crypto_plan", {})
        korea_plan = self.strategy_book.get("korea_plan", {})
        orders = [
            self._plan_to_order("crypto", crypto_plan).model_dump(),
            self._plan_to_order("korea", korea_plan).model_dump(),
        ]
        active_orders = [item for item in orders if item["status"] == "planned"]
        return AgentResult(
            name=self.name,
            score=1.0,
            reason="paper execution ledger active, real broker routing intentionally disabled",
            payload={
                "mode": "paper",
                "orders_sent": len(active_orders),
                "broker_live": False,
                "orders": orders,
            },
        )

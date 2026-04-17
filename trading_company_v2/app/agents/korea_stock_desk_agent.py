from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_kosdaq_snapshot


class KoreaStockDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("korea_stock_desk_agent")

    def run(self) -> AgentResult:
        leaders = get_kosdaq_snapshot(top_n=10)
        gap_candidates = [item for item in leaders if float(item.get("gap_pct", 0)) >= 2.0]
        score = 0.65 if len(gap_candidates) >= 3 else 0.5 if gap_candidates else 0.35
        return AgentResult(
            name=self.name,
            score=score,
            reason="KOSDAQ opening-drive desk ranked gap and liquidity leaders",
            payload={
                "gap_candidates": gap_candidates[:5],
                "leader_count": len(leaders),
                "active_gap_count": len(gap_candidates),
                "top_focus": gap_candidates[0]["name"] if gap_candidates else None,
            },
        )

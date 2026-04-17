from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import build_market_snapshot


class MarketDataAgent(BaseAgent):
    def __init__(self):
        super().__init__("market_data_agent")

    def run(self) -> AgentResult:
        snapshot = build_market_snapshot()
        return AgentResult(
            name=self.name,
            score=0.7,
            reason=f"captured {len(snapshot.crypto_leaders)} crypto leaders and {len(snapshot.stock_leaders)} KOSDAQ leaders",
            payload={
                "crypto_leaders": snapshot.crypto_leaders,
                "crypto_watchlist": snapshot.crypto_watchlist,
                "stock_leaders": snapshot.stock_leaders,
                "gap_candidates": snapshot.gap_candidates,
                "as_of": snapshot.as_of,
            },
        )


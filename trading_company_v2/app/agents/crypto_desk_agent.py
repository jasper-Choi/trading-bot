from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_upbit_15m_candles
from app.services.signal_engine import summarize_crypto_signal


class CryptoDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("crypto_desk_agent")

    def run(self) -> AgentResult:
        candles = get_upbit_15m_candles("KRW-BTC", count=40)
        signal = summarize_crypto_signal(candles)
        return AgentResult(
            name=self.name,
            score=float(signal["score"]),
            reason="global crypto desk evaluated KRW-BTC lead contract",
            payload={
                "lead_market": "KRW-BTC",
                "desk_bias": signal["bias"],
                "reasons": signal["reasons"],
            },
        )


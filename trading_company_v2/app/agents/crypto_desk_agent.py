from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.backtest_advisor import get_crypto_weights
from app.services.market_gateway import get_upbit_15m_candles
from app.services.signal_engine import summarize_crypto_signal


class CryptoDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("crypto_desk_agent")

    def run(self) -> AgentResult:
        weights = get_crypto_weights()
        direction_symbol = "KRW-BTC"
        candles = get_upbit_15m_candles(direction_symbol, count=40)
        signal = summarize_crypto_signal(candles)
        lead_market = next(iter(weights), "KRW-BTC")
        candidate_symbols = [str(market) for market in weights.keys() if str(market).strip()][:5]
        if lead_market and lead_market not in candidate_symbols:
            candidate_symbols = [lead_market, *candidate_symbols][:5]
        recent_change = float(signal.get("recent_change_pct", 0.0) or 0.0)
        burst_change = float(signal.get("burst_change_pct", 0.0) or 0.0)
        ema_gap = float(signal.get("ema_gap_pct", 0.0) or 0.0)
        rsi_value = signal.get("rsi")
        return AgentResult(
            name=self.name,
            score=float(signal["score"]),
            reason=f"BTC direction {signal['bias']} -> top crypto candidate {lead_market} (weight {weights.get(lead_market, 0):.2f})",
            payload={
                "lead_market": lead_market,
                "direction_market": direction_symbol,
                "candidate_symbols": candidate_symbols,
                "desk_bias": signal["bias"],
                "reasons": signal["reasons"],
                "signal_score": signal["score"],
                "recent_change_pct": recent_change,
                "burst_change_pct": burst_change,
                "ema_gap_pct": ema_gap,
                "rsi": rsi_value,
                "backtest_weights": weights,
            },
        )

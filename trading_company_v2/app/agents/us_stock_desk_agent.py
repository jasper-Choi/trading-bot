from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_us_core_snapshot, get_us_daily_prices
from app.services.signal_engine import summarize_equity_signal


class USStockDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("us_stock_desk_agent")

    def run(self) -> AgentResult:
        leaders = get_us_core_snapshot()
        if not leaders:
            return AgentResult(
                name=self.name,
                score=0.35,
                reason="U.S. desk could not collect core ticker snapshot",
                payload={
                    "leaders": [],
                    "leader_count": 0,
                    "active_us_count": 0,
                    "top_focus": None,
                    "quality_score": 0.35,
                    "avg_change_pct_top3": 0.0,
                    "avg_volume_top3": 0.0,
                    "desk_bias": "neutral",
                    "signal_score": 0.5,
                    "reasons": ["no U.S. snapshot available"],
                },
            )

        enriched_leaders = []
        for item in leaders[:5]:
            ticker = str(item.get("ticker", "")).strip()
            candles = get_us_daily_prices(ticker, count=40) if ticker else []
            signal = summarize_equity_signal(candles)
            signal_score = float(signal.get("score", 0.5) or 0.5)
            candidate_score = round(
                (signal_score * 0.62)
                + (max(float(item.get("change_pct", 0.0) or 0.0), -2.0) * 0.12)
                + (min(float(item.get("volume", 0.0) or 0.0), 80000000) / 80000000 * 0.12),
                2,
            )
            enriched_leaders.append(
                {
                    **item,
                    "signal_bias": signal.get("bias", "neutral"),
                    "signal_score": signal_score,
                    "signal_reasons": signal.get("reasons", []),
                    "candidate_score": candidate_score,
                }
            )

        ranked_leaders = sorted(
            enriched_leaders,
            key=lambda entry: (entry.get("candidate_score", 0.0), entry.get("change_pct", 0.0), entry.get("volume", 0.0)),
            reverse=True,
        )
        lead = ranked_leaders[0]
        signal = {
            "bias": lead.get("signal_bias", "neutral"),
            "score": lead.get("signal_score", 0.5),
            "reasons": lead.get("signal_reasons", []),
        }
        top3 = ranked_leaders[:3]
        avg_change = round(sum(float(item.get("change_pct", 0.0) or 0.0) for item in top3) / len(top3), 2) if top3 else 0.0
        avg_volume = round(sum(float(item.get("volume", 0.0) or 0.0) for item in top3) / len(top3)) if top3 else 0.0
        avg_signal = round(sum(float(item.get("signal_score", 0.0) or 0.0) for item in top3) / len(top3), 2) if top3 else 0.0
        quality_score = min(round(float(signal["score"]) + (0.08 if avg_change > 0.6 else 0.0) + (0.06 if avg_signal > 0.64 else 0.0), 2), 0.95)
        return AgentResult(
            name=self.name,
            score=max(quality_score, 0.2),
            reason="U.S. desk ranked core ETF/mega-cap leaders with trend confirmation",
            payload={
                "leaders": ranked_leaders[:5],
                "leader_count": len(leaders),
                "active_us_count": sum(1 for item in leaders if float(item.get("change_pct", 0.0) or 0.0) > 0),
                "top_focus": lead.get("ticker"),
                "quality_score": quality_score,
                "avg_change_pct_top3": avg_change,
                "avg_volume_top3": avg_volume,
                "avg_signal_score_top3": avg_signal,
                "desk_bias": signal["bias"],
                "signal_score": signal["score"],
                "reasons": signal["reasons"],
            },
        )

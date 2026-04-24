from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_kosdaq_snapshot, get_naver_daily_prices
from app.services.signal_engine import summarize_equity_signal


class KoreaStockDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("korea_stock_desk_agent")

    def run(self) -> AgentResult:
        leaders = get_kosdaq_snapshot(top_n=30)
        enriched_candidates = []
        for item in leaders:
            gap_pct = float(item.get("gap_pct", 0.0) or 0.0)
            if gap_pct < 1.2 or gap_pct > 12.0:
                continue
            ticker = str(item.get("ticker", "")).strip()
            candles = get_naver_daily_prices(ticker, count=40) if ticker else []
            signal = summarize_equity_signal(candles)
            signal_score = float(signal.get("score", 0.5) or 0.5)
            volume = float(item.get("volume", 0.0) or 0.0)
            rsi_value = signal.get("rsi")
            burst_change = float(signal.get("burst_change_pct", 0.0) or 0.0)
            ema_gap = float(signal.get("ema_gap_pct", 0.0) or 0.0)
            liquidity_score = min(volume, 250000) / 250000 * 0.24 if volume > 0 else 0.0
            overheat_penalty = 0.0
            if gap_pct >= 10.0:
                overheat_penalty += 0.08
            if rsi_value is not None and float(rsi_value) >= 78.0:
                overheat_penalty += 0.12
            if burst_change >= 12.0:
                overheat_penalty += 0.08
            if ema_gap >= 12.0:
                overheat_penalty += 0.06
            candidate_score = round(
                (gap_pct * 0.022)
                + liquidity_score
                + (signal_score * 0.68)
                - overheat_penalty,
                2,
            )
            enriched_candidates.append(
                {
                    **item,
                    "signal_bias": signal.get("bias", "neutral"),
                    "signal_score": signal_score,
                    "signal_reasons": signal.get("reasons", []),
                    "rsi": rsi_value,
                    "burst_change_pct": burst_change,
                    "ema_gap_pct": ema_gap,
                    "overheat_penalty": round(overheat_penalty, 2),
                    "candidate_score": candidate_score,
                }
            )
        gap_candidates = sorted(
            enriched_candidates,
            key=lambda entry: (entry.get("candidate_score", 0.0), entry.get("gap_pct", 0.0), entry.get("volume", 0.0)),
            reverse=True,
        )
        top_candidates = gap_candidates[:3]
        avg_gap = round(
            sum(float(item.get("gap_pct", 0) or 0.0) for item in top_candidates) / len(top_candidates),
            2,
        ) if top_candidates else 0.0
        avg_volume = round(
            sum(float(item.get("volume", 0) or 0.0) for item in top_candidates) / len(top_candidates)
        ) if top_candidates else 0.0
        avg_signal = round(
            sum(float(item.get("signal_score", 0.0) or 0.0) for item in top_candidates) / len(top_candidates),
            2,
        ) if top_candidates else 0.0

        score = 0.34
        if gap_candidates:
            score += 0.12
        if len(gap_candidates) >= 3:
            score += 0.10
        if avg_gap >= 3.0:
            score += 0.12
        elif avg_gap >= 1.8:
            score += 0.06
        if avg_volume >= 50000:
            score += 0.11
        elif avg_volume >= 12000:
            score += 0.05
        elif avg_volume < 3500 and gap_candidates:
            score -= 0.14
        if avg_signal >= 0.62:
            score += 0.12
        elif avg_signal >= 0.54:
            score += 0.06
        elif avg_signal < 0.44 and gap_candidates:
            score -= 0.08
        score = min(round(score, 2), 0.95)

        return AgentResult(
            name=self.name,
            score=max(score, 0.2),
            reason="KOSDAQ opening-drive desk ranked gap, liquidity, and trend-confirmed leaders",
            payload={
                "gap_candidates": gap_candidates[:5],
                "leader_count": len(leaders),
                "active_gap_count": len(gap_candidates),
                "top_focus": gap_candidates[0]["name"] if gap_candidates else None,
                "quality_score": score,
                "avg_gap_pct_top3": avg_gap,
                "avg_volume_top3": avg_volume,
                "avg_signal_score_top3": avg_signal,
            },
        )

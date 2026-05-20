from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_us_core_snapshot, get_us_daily_prices, get_us_market_context
from app.services.signal_engine import summarize_equity_signal


class USStockDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("us_stock_desk_agent")

    def run(self) -> AgentResult:
        # ── 1. 확장 US 컨텍스트 (VIX + 섹터 + 메가캡) ────────────────────────
        us_ctx = get_us_market_context()

        # ── 2. 핵심 ETF/메가캡 스냅샷 (기존 로직 유지) ───────────────────────
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
                    **us_ctx,
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

        # ── 3. VIX 레짐에 따른 점수 조정 ───────────────────────────────────
        vix_regime = us_ctx.get("vix_regime", "unknown")
        if vix_regime in ("fear", "panic"):
            quality_score = min(quality_score, 0.40)  # 공포 구간 진입 억제
        elif vix_regime == "elevated":
            quality_score = min(quality_score, 0.60)

        us_regime = us_ctx.get("us_regime", "neutral")

        # ── 4. 요약 reason 구성 ──────────────────────────────────────────────
        spy_chg = us_ctx.get("spy_chg", 0.0)
        qqq_chg = us_ctx.get("qqq_chg", 0.0)
        vix_val = us_ctx.get("vix", 0.0)
        sector_leader  = us_ctx.get("sector_leader", "")
        sector_laggard = us_ctx.get("sector_laggard", "")
        reason_parts = [
            f"US {us_regime}: SPY{spy_chg:+.1f}% QQQ{qqq_chg:+.1f}%",
            f"VIX={vix_val:.1f} ({vix_regime})",
            f"섹터↑{sector_leader} ↓{sector_laggard}",
        ]

        return AgentResult(
            name=self.name,
            score=max(quality_score, 0.2),
            reason=" | ".join(reason_parts),
            payload={
                # 기존 리더보드
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
                # ── 확장 US 컨텍스트 (전 에이전트 참조용) ──
                **us_ctx,
            },
        )

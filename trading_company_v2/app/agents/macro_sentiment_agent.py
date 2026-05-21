"""매크로 감성 에이전트 — 글로벌 뉴스/SNS 인텔리전스.

전세계 뉴스·X(Twitter) 핵심 계정을 스캔하여 시장 분위기를 점수화한다.
orchestrator의 _determine_stance / _determine_regime에 직접 반영된다.

score 해석:
  0.00 ~ 0.30  → macro "STRESSED"  (패닉 뉴스, 트럼프 관세, 연준 충격 등)
  0.30 ~ 0.45  → macro "CAUTIOUS"  (부정적 흐름, 진입 신중)
  0.45 ~ 0.65  → macro "NEUTRAL"   (이슈 없음, 정상 운영)
  0.65 ~ 0.80  → macro "POSITIVE"  (호재 뉴스, 금리 인하, 무역 합의 등)
  0.80 ~ 1.00  → macro "OFFENSIVE" (강한 긍정 신호)
"""
from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult


class MacroSentimentAgent(BaseAgent):
    def __init__(self):
        super().__init__("macro_sentiment_agent")

    def run(self) -> AgentResult:
        from app.services.global_news_intel import get_market_news_intel

        intel: dict = {}
        try:
            intel = get_market_news_intel()
        except Exception as exc:
            # 뉴스 수집 실패 → 중립 바이어스 유지 (진입 차단하지 않음)
            return AgentResult(
                name=self.name,
                score=0.50,
                reason=f"뉴스 수집 실패 — 중립 유지 ({exc!r})",
                payload={
                    "macro_bias":   "neutral",
                    "stress_level": "unknown",
                    "impact":       "calm",
                    "error":        str(exc),
                },
            )

        score      = float(intel.get("macro_score", 0.50) or 0.50)
        impact     = str(intel.get("impact", "calm") or "calm")
        trump_alert = bool(intel.get("trump_alert", False))
        tariff_alert = bool(intel.get("tariff_alert", False))
        crypto_boost = bool(intel.get("crypto_boost", False))
        korea_risk  = bool(intel.get("korea_risk", False))
        keywords   = intel.get("keywords", [])
        sources    = intel.get("sources", [])
        breaking   = intel.get("breaking", [])

        # ── 매크로 바이어스 해석 ─────────────────────────────────────────────
        if score <= 0.30:
            macro_bias   = "panic"
            stress_level = "critical"
        elif score <= 0.45:
            macro_bias   = "cautious"
            stress_level = "high"
        elif score <= 0.65:
            macro_bias   = "neutral"
            stress_level = "medium"
        elif score <= 0.80:
            macro_bias   = "positive"
            stress_level = "low"
        else:
            macro_bias   = "bullish"
            stress_level = "minimal"

        # ── 요약 reason 문자열 ────────────────────────────────────────────────
        parts = [f"macro_score={score:.2f} ({macro_bias})"]
        if trump_alert:
            parts.append("🚨 트럼프 시장 언급 감지")
        if tariff_alert:
            parts.append("⚠️ 관세/무역 뉴스")
        if crypto_boost:
            parts.append("🟢 코인 긍정 뉴스")
        if korea_risk:
            parts.append("⚠️ 한국 리스크 뉴스")
        if keywords:
            parts.append(f"키워드: {', '.join(keywords[:4])}")
        if sources:
            parts.append(f"소스: {', '.join(sources)}")
        reason = " | ".join(parts)

        return AgentResult(
            name=self.name,
            score=score,
            reason=reason,
            payload={
                # orchestrator가 직접 읽는 필드
                "macro_bias":    macro_bias,
                "stress_level":  stress_level,
                "impact":        impact,
                # 알림/대시보드용
                "trump_alert":   trump_alert,
                "tariff_alert":  tariff_alert,
                "crypto_boost":  crypto_boost,
                "korea_risk":    korea_risk,
                "panic_score":   intel.get("panic_score", 0.0),
                "pos_score":     intel.get("pos_score", 0.0),
                "keywords":      keywords,
                "sources":       sources,
                "breaking_news": breaking[:5],  # 최신 5개 헤드라인
                "cached_at":     intel.get("cached_at", ""),
                "from_cache":    intel.get("from_cache", False),
                # recommendation_engine 통합용 — vix_regime 호환 인터페이스
                "vix_regime":    "panic" if score <= 0.30 else ("fear" if score <= 0.40 else "normal"),
            },
        )

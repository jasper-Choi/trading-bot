from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_kosdaq_snapshot, get_naver_daily_prices
from app.services.signal_engine import summarize_equity_signal, summarize_breakout_signal


# ─────────────────────────────────────────────────────────────────
# Korea breakout watchlist — from stock_backtest_v3 universe
# 코스닥 바이오/성장주 + 코스피 모멘텀 대표 20종목
# ─────────────────────────────────────────────────────────────────
KOREA_BREAKOUT_WATCHLIST: dict[str, str] = {
    # 코스닥
    "247540": "에코프로비엠",
    "196170": "알테오젠",
    "028300": "HLB",
    "141080": "리가켐바이오",
    "000250": "삼천당제약",
    "214150": "클래시스",
    "277810": "레인보우로보틱스",
    "086520": "에코프로",
    "068270": "셀트리온",
    "293490": "카카오게임즈",
    # 코스피
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "035720": "카카오",
    "005490": "POSCO홀딩스",
    "373220": "LG에너지솔루션",
    "006400": "삼성SDI",
    "259960": "크래프톤",
    "035420": "네이버",
    "034020": "두산에너빌리티",
}


class KoreaStockDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("korea_stock_desk_agent")

    def run(self) -> AgentResult:
        # ── Path A: Gap-up candidates (existing KOSDAQ movers) ─────────────
        leaders = get_kosdaq_snapshot(top_n=30)
        enriched_candidates: list[dict] = []
        for item in leaders:
            gap_pct = float(item.get("gap_pct", 0.0) or 0.0)
            if gap_pct < 1.2 or gap_pct > 12.0:
                continue
            ticker = str(item.get("ticker", "")).strip()
            candles = get_naver_daily_prices(ticker, count=42) if ticker else []
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
                    "is_breakout": False,
                    "breakout_count": 0,
                }
            )

        # ── Path B: Momentum breakout watchlist scan (stock_backtest_v3 strategy) ──
        # Scans fixed universe for: 20-day high breakout + vol surge 2.5x + RSI 55-78 + EMA20
        # These do NOT require a gap-up today — catches intraday breakouts
        already_tickers = {str(c.get("ticker", "")).strip() for c in enriched_candidates}
        breakout_candidates: list[dict] = []

        for ticker, name in KOREA_BREAKOUT_WATCHLIST.items():
            if ticker in already_tickers:
                continue  # already captured via gap-up path
            candles = get_naver_daily_prices(ticker, count=42)
            if len(candles) < 22:
                continue
            bk = summarize_breakout_signal(candles, breakout_period=20, vol_surge_mult=2.5,
                                           rsi_min=55.0, rsi_max=78.0)
            confirmed_count = int(bk.get("confirmed_count", 0) or 0)
            if confirmed_count < 2:
                continue  # skip weak / no signal
            signal = summarize_equity_signal(candles)
            signal_score = float(signal.get("score", 0.5) or 0.5)
            breakout_score = float(bk.get("breakout_score", 0.0) or 0.0)
            # Volume from last candle (proxy for today's volume)
            last_volume = float(candles[-1].get("volume") or 0.0)
            last_close = float(candles[-1].get("close") or 0.0)
            # Overheat check
            rsi_value = bk.get("last_rsi")
            overheat_penalty = 0.0
            if rsi_value is not None and float(rsi_value) >= 78.0:
                overheat_penalty += 0.12
            # Composite candidate score (weighted toward breakout quality)
            candidate_score = round(
                breakout_score * 0.65
                + signal_score * 0.35
                - overheat_penalty,
                2,
            )
            breakout_candidates.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "current_price": last_close,
                    "gap_pct": 0.0,  # no gap-up today
                    "volume": int(last_volume),
                    "signal_bias": signal.get("bias", "neutral"),
                    "signal_score": signal_score,
                    "signal_reasons": signal.get("reasons", []),
                    "rsi": rsi_value,
                    "burst_change_pct": float(signal.get("burst_change_pct", 0.0) or 0.0),
                    "ema_gap_pct": float(signal.get("ema_gap_pct", 0.0) or 0.0),
                    "overheat_penalty": round(overheat_penalty, 2),
                    "candidate_score": candidate_score,
                    "is_breakout": confirmed_count >= 3,
                    "breakout_count": confirmed_count,
                    "vol_ratio": float(bk.get("vol_ratio", 0.0) or 0.0),
                    "breakout_reasons": bk.get("reasons", []),
                }
            )

        # ── Merge and rank all candidates ───────────────────────────────────
        all_candidates = enriched_candidates + breakout_candidates
        gap_candidates = sorted(
            all_candidates,
            key=lambda entry: (
                entry.get("candidate_score", 0.0),
                entry.get("breakout_count", 0),
                entry.get("gap_pct", 0.0),
                entry.get("volume", 0.0),
            ),
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

        # Count by type
        active_gap_count = sum(1 for c in enriched_candidates if float(c.get("gap_pct", 0) or 0) >= 1.2)
        breakout_confirmed_count = sum(1 for c in breakout_candidates if int(c.get("breakout_count", 0) or 0) >= 4)
        breakout_partial_count = sum(1 for c in breakout_candidates if int(c.get("breakout_count", 0) or 0) == 3)

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
        # Breakout bonus — strategy-validated signal
        if breakout_confirmed_count >= 1:
            score += 0.15
        elif breakout_partial_count >= 1:
            score += 0.08
        score = min(round(score, 2), 0.95)

        return AgentResult(
            name=self.name,
            score=max(score, 0.2),
            reason="KOSDAQ gap-drive + momentum breakout desk ranked by candidate score",
            payload={
                "gap_candidates": gap_candidates[:5],
                "leader_count": len(leaders),
                "active_gap_count": active_gap_count,
                "breakout_confirmed_count": breakout_confirmed_count,
                "breakout_partial_count": breakout_partial_count,
                "top_focus": gap_candidates[0]["name"] if gap_candidates else None,
                "quality_score": score,
                "avg_gap_pct_top3": avg_gap,
                "avg_volume_top3": avg_volume,
                "avg_signal_score_top3": avg_signal,
            },
        )

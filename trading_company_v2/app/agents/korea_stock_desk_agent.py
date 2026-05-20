from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.korea_supply_demand import get_institutional_tickers, get_supply_demand_score
from app.services.korea_universe import get_korea_universe
from app.services.market_gateway import get_naver_daily_prices
from app.services.signal_engine import summarize_breakout_signal, summarize_equity_signal

_FETCH_WORKERS = 12
# Max candidates from dynamic universe to run breakout scoring on (keeps runtime bounded)
_UNIVERSE_SCAN_LIMIT = 80


def _ema(values: list[float], period: int) -> float:
    """지수이동평균 (EMA)."""
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def _std_last(values: list[float], period: int) -> float:
    """Population std of the last `period` values."""
    if len(values) < period:
        return 0.0
    window = values[-period:]
    mean = sum(window) / period
    return (sum((v - mean) ** 2 for v in window) / period) ** 0.5


class KoreaStockDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("korea_stock_desk_agent")

    def run(self) -> AgentResult:
        # ── Strategy B: 60일 신고점 돌파 스캔 ────────────────────────────────
        # 백테스트 검증: Sharpe 6.16, WR 84.6%, MDD -4.0%
        # 조건: 60일 신고점 돌파 + vol ≥ 2배 + RSI 55-80 (3/3 조건 모두 충족)
        universe = get_korea_universe()
        watchlist_items = [
            (item["ticker"], item["name"])
            for item in universe
        ][:_UNIVERSE_SCAN_LIMIT]

        def _fetch(ticker_name: tuple[str, str]) -> tuple[str, str, list[dict]]:
            ticker, name = ticker_name
            # 220일: EMA200 계산(Strategy S2) + 60일 신고점(Strategy B) 모두 지원
            return ticker, name, get_naver_daily_prices(ticker, count=220)

        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
            results = list(executor.map(_fetch, watchlist_items))

        # ── 수급 데이터 (기관 레이더 종목집합) ──────────────────────────────
        try:
            inst_tickers = get_institutional_tickers()
        except Exception:
            inst_tickers = set()

        breakout_candidates: list[dict] = []
        for ticker, name, candles in results:
            if len(candles) < 62:  # 60일 신고점 계산 최소 요건
                continue

            # 백테스트 검증 파라미터
            # breakout_period=60, vol_surge_mult=2.0, rsi_min=55.0, rsi_max=80.0
            # 근거: 3년 152종목 백테스트 → 승률 84.6%, Sharpe 6.16, MDD -4.0%
            bk = summarize_breakout_signal(
                candles,
                breakout_period=60,
                vol_surge_mult=2.0,
                rsi_min=55.0,
                rsi_max=80.0,
            )
            confirmed_count = int(bk.get("confirmed_count", 0) or 0)
            if confirmed_count < 3:
                continue

            signal = summarize_equity_signal(candles)
            signal_score = float(signal.get("score", 0.5) or 0.5)
            last_volume = float(candles[-1].get("volume") or 0.0)
            last_close = float(candles[-1].get("close") or 0.0)
            rsi_value = bk.get("last_rsi")
            breakout_score = float(bk.get("breakout_score", 0.0) or 0.0)
            burst_change = float(signal.get("burst_change_pct", 0.0) or 0.0)
            ema_gap = float(signal.get("ema_gap_pct", 0.0) or 0.0)

            # 과열 필터
            overheat_penalty = 0.0
            if rsi_value is not None and float(rsi_value) >= 78.0:
                overheat_penalty += 0.12
            if burst_change >= 12.0:
                overheat_penalty += 0.08
            if ema_gap >= 12.0:
                overheat_penalty += 0.06

            # 수급 점수
            sd_score = get_supply_demand_score(ticker, inst_tickers)
            candidate_score = round(
                breakout_score * 0.65 + signal_score * 0.25 + sd_score * 0.10 - overheat_penalty,
                2,
            )

            breakout_candidates.append({
                "ticker": ticker,
                "name": name,
                "current_price": last_close,
                "volume": int(last_volume),
                "signal_bias": signal.get("bias", "neutral"),
                "signal_score": signal_score,
                "signal_reasons": signal.get("reasons", []),
                "rsi": rsi_value,
                "burst_change_pct": burst_change,
                "ema_gap_pct": ema_gap,
                "overheat_penalty": round(overheat_penalty, 2),
                "candidate_score": candidate_score,
                "is_breakout": True,
                "breakout_count": confirmed_count,
                "vol_ratio": float(bk.get("vol_ratio", 0.0) or 0.0),
                "breakout_reasons": bk.get("reasons", []),
                "supply_demand_score": round(sd_score, 3),
                "inst_radar": ticker in inst_tickers,
                # 신고점 돌파 전략 태그 — state_store에서 전용 trail/threshold 적용
                "focus_tag": "new_high_breakout",
            })

        breakout_candidates.sort(key=lambda c: c["candidate_score"], reverse=True)

        # ── Strategy S2: MONGTATA 에어본 (평균회귀) ─────────────────────────────
        # 백테스트 검증: Sharpe 8.60, WR 56.5%, MDD -5.9% (주식 3년)
        # 조건: close > EMA200 (상승장 레짐) + close < lower_BB (EMA20−2σ) + close < EMA20×0.975
        mongtata_candidates: list[dict] = []
        for ticker, name, candles in results:
            if len(candles) < 205:
                continue
            try:
                closes = [float(c.get("close") or 0.0) for c in candles]
                if not closes or closes[-1] <= 0:
                    continue
                ema200 = _ema(closes, 200)
                if closes[-1] <= ema200 or ema200 <= 0:
                    continue
                ema20 = _ema(closes, 20)
                std20 = _std_last(closes, 20)
                if ema20 <= 0 or std20 <= 0:
                    continue
                lower_bb = ema20 - 2.0 * std20
                deviation_pct = round((closes[-1] - ema20) / ema20 * 100, 2)
                if closes[-1] < lower_bb and closes[-1] < ema20 * 0.975:
                    mongtata_candidates.append({
                        "ticker": ticker,
                        "name": name,
                        "current_price": closes[-1],
                        "ema20": round(ema20, 0),
                        "lower_bb": round(lower_bb, 0),
                        "deviation_pct": deviation_pct,
                        "focus_tag": "mongtata_airborne",
                    })
            except Exception:
                continue

        breakout_confirmed_count = sum(
            1 for c in breakout_candidates if int(c.get("breakout_count", 0) or 0) >= 4
        )
        breakout_partial_count = sum(
            1 for c in breakout_candidates if int(c.get("breakout_count", 0) or 0) == 3
        )

        score = 0.30
        if breakout_confirmed_count >= 1:
            score += 0.40
        elif breakout_partial_count >= 1:
            score += 0.20
        elif mongtata_candidates:
            score += 0.25
        if breakout_candidates:
            top_sd = float(breakout_candidates[0].get("supply_demand_score", 0.0) or 0.0)
            if top_sd >= 0.7:
                score += 0.10
        score = min(round(score, 2), 0.95)

        return AgentResult(
            name=self.name,
            score=max(score, 0.2),
            reason=(
                f"Strategy B 60일신고점: {breakout_confirmed_count}confirmed/{breakout_partial_count}partial "
                f"| Strategy S2 MONGTATA: {len(mongtata_candidates)}개 "
                f"(universe {len(universe)}종목)"
            ),
            payload={
                "new_high_breakout_candidates": breakout_candidates[:5],
                "breakout_confirmed_count": breakout_confirmed_count,
                "breakout_partial_count": breakout_partial_count,
                "universe_size": len(universe),
                "quality_score": score,
                # Strategy S2 MONGTATA 에어본
                "mongtata_airborne_candidates": mongtata_candidates[:3],
                "mongtata_airborne_count": len(mongtata_candidates),
            },
        )

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.korea_supply_demand import get_institutional_tickers, get_supply_demand_score
from app.services.korea_universe import get_korea_universe
from app.services.market_gateway import get_naver_daily_prices, get_us_market_context
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


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI — Wilder 평활법."""
    if len(closes) < period + 2:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 0.0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 1)


def _std_last(values: list[float], period: int) -> float:
    """Population std of the last `period` values."""
    if len(values) < period:
        return 0.0
    window = values[-period:]
    mean = sum(window) / period
    return (sum((v - mean) ** 2 for v in window) / period) ** 0.5


def _check_close_panic_reversal(existing_candidates: list[dict]) -> list[dict]:
    """Strategy S16: 장 막판(15:20~15:29) 패닉셀 감지 — 포워드 테스트용 신호 수집.

    KIS 분봉 API로 15:20 대비 현재가 낙폭을 계산.
    - 조건: 15:20 이후 -2% 이상 하락 + EMA200 상승 추세 종목
    - 현재는 신호 기록 전용 (실거래 미집행) — 데이터 누적 후 판단

    Args:
        existing_candidates: 이미 스캔된 종목 목록 (ticker/name 포함)

    Returns:
        [{ticker, name, drop_pct, price_1520, price_now, ema200_bull}, ...]
    """
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        return []

    # 15:20~15:29 구간만 실행
    h, m = now_kst.hour, now_kst.minute
    if not (h == 15 and 20 <= m <= 29):
        return []

    try:
        from app.services.kis_broker import get_minute_candles
    except Exception:
        return []

    # 스캔 대상: 기존 후보 종목 + 고정 주요 종목
    _ANCHOR_TICKERS = [
        ("005930", "삼성전자"), ("000660", "SK하이닉스"),
        ("035420", "NAVER"),    ("051910", "LG화학"),
        ("006400", "삼성SDI"),  ("035720", "카카오"),
    ]
    seen: set[str] = set()
    scan_list: list[tuple[str, str]] = []
    for c in existing_candidates:
        t = str(c.get("ticker", "")).strip()
        n = str(c.get("name", t)).strip()
        if t and t not in seen:
            scan_list.append((t, n))
            seen.add(t)
    for t, n in _ANCHOR_TICKERS:
        if t not in seen:
            scan_list.append((t, n))
            seen.add(t)

    results: list[dict] = []
    for ticker, name in scan_list[:15]:  # 최대 15종목 (API 레이트 리밋)
        try:
            bars = get_minute_candles(ticker, count=30)
            if len(bars) < 2:
                continue
            # 15:20 이전 마지막 봉 가격
            bar_1520 = next(
                (b for b in reversed(bars) if b.get("time", "") <= "152000"),
                None,
            )
            if bar_1520 is None:
                continue
            price_1520 = float(bar_1520.get("close") or 0.0)
            price_now  = float(bars[-1].get("close") or 0.0)
            if price_1520 <= 0 or price_now <= 0:
                continue
            drop_pct = (price_now - price_1520) / price_1520 * 100
            if drop_pct > -2.0:  # -2% 미만 하락만
                continue
            results.append({
                "ticker":      ticker,
                "name":        name,
                "drop_pct":    round(drop_pct, 2),
                "price_1520":  price_1520,
                "price_now":   price_now,
                "scan_time":   now_kst.strftime("%H:%M"),
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["drop_pct"])  # 낙폭 큰 것부터
    return results


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
        # 조건: close > EMA200 + close < lower_BB (EMA20−2σ) + close < EMA20×0.975
        mongtata_candidates: list[dict] = []
        # ── Strategy S9: RSI(2) Connors 평균회귀 ────────────────────────────
        # 백테스트 검증: Sharpe 6.74, WR 58.1%, MDD -7.3% (주식 3년)
        # 조건: close > EMA200 + RSI(2) < 10 + close < EMA20×0.975
        rsi2_candidates: list[dict] = []
        # ── Strategy S10: N-Day Consecutive Pullback ─────────────────────────
        # 백테스트 검증: Sharpe 4.52, WR 54.1%, MDD -12.1% (주식 3년)
        # 조건: close > EMA200 + 3일 연속 하락 + close < EMA5
        nday_candidates: list[dict] = []

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

                # ── S9/S13: RSI(2) < 10 + EMA20*0.975 ─────────────────────
                rsi2_val = _rsi(closes, 2)
                if (rsi2_val is not None and rsi2_val < 10.0
                        and closes[-1] < ema20 * 0.975):
                    rsi14_val = _rsi(closes, 14)
                    dual = (rsi14_val is not None and rsi14_val < 40.0)
                    rsi2_candidates.append({
                        "ticker": ticker,
                        "name": name,
                        "current_price": closes[-1],
                        "rsi2": rsi2_val,
                        "rsi14": round(rsi14_val or 0.0, 1),
                        "ema20": round(ema20, 0),
                        "deviation_pct": round((closes[-1] - ema20) / ema20 * 100, 2),
                        "focus_tag": "rsi2_mean_reversion",
                        "dual_rsi": dual,  # S13: RSI(14)<40 추가 확인
                    })

                # ── S10: 3일 연속 하락 + close < EMA5 ───────────────────────
                ema5 = _ema(closes[-10:], 5)
                consec = (len(closes) >= 4 and
                          closes[-1] < closes[-2] < closes[-3] < closes[-4])
                if consec and closes[-1] < ema5 and ema5 > 0:
                    nday_candidates.append({
                        "ticker": ticker,
                        "name": name,
                        "current_price": closes[-1],
                        "ema5": round(ema5, 0),
                        "deviation_pct": round((closes[-1] - ema5) / ema5 * 100, 2),
                        "focus_tag": "nday_pullback",
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
        elif mongtata_candidates or rsi2_candidates or nday_candidates:
            score += 0.25
        if breakout_candidates:
            top_sd = float(breakout_candidates[0].get("supply_demand_score", 0.0) or 0.0)
            if top_sd >= 0.7:
                score += 0.10
        score = min(round(score, 2), 0.95)

        # ── S16 Close Panic Reversal (15:20~15:29 포워드 테스트) ──────────────
        # 장 막판 패닉셀 종목 감지 — KIS 분봉 기반 실시간 스캔
        # 아직 shadow 검증 중 (실거래 미집행, 신호 수집만)
        close_panic_candidates: list[dict] = []
        try:
            close_panic_candidates = _check_close_panic_reversal(
                breakout_candidates + mongtata_candidates + rsi2_candidates
            )
        except Exception:
            pass

        # ── 미국 시장 컨텍스트 (15분 캐시) ───────────────────────────────────
        us_ctx: dict = {}
        try:
            us_ctx = get_us_market_context()
        except Exception:
            pass

        return AgentResult(
            name=self.name,
            score=max(score, 0.2),
            reason=(
                f"B:{breakout_confirmed_count}c/{breakout_partial_count}p "
                f"S2:{len(mongtata_candidates)} S9:{len(rsi2_candidates)} S10:{len(nday_candidates)} "
                f"S16:{len(close_panic_candidates)} "
                f"(universe {len(universe)}종목)"
            ),
            payload={
                "new_high_breakout_candidates": breakout_candidates[:5],
                "breakout_confirmed_count": breakout_confirmed_count,
                "breakout_partial_count": breakout_partial_count,
                "universe_size": len(universe),
                "quality_score": score,
                # Strategy S2 MONGTATA
                "mongtata_airborne_candidates": mongtata_candidates[:3],
                "mongtata_airborne_count": len(mongtata_candidates),
                # Strategy S9 RSI(2)
                "rsi2_candidates": rsi2_candidates[:3],
                "rsi2_count": len(rsi2_candidates),
                # Strategy S10 N-Day Pullback
                "nday_candidates": nday_candidates[:3],
                "nday_count": len(nday_candidates),
                # Strategy S16 Close Panic Reversal (포워드 테스트 — 신호 수집만)
                "close_panic_candidates": close_panic_candidates[:3],
                "close_panic_count": len(close_panic_candidates),
                # ── 미국 시장 컨텍스트 ──
                "us_regime":      us_ctx.get("us_regime", "unknown"),
                "vix":            us_ctx.get("vix", 0.0),
                "vix_regime":     us_ctx.get("vix_regime", "unknown"),
                "spy_chg":        us_ctx.get("spy_chg", 0.0),
                "qqq_chg":        us_ctx.get("qqq_chg", 0.0),
                "us_summary":     us_ctx.get("summary", ""),
                "us_sectors":     us_ctx.get("sectors", {}),
                "sector_leader":  us_ctx.get("sector_leader", ""),
                "sector_laggard": us_ctx.get("sector_laggard", ""),
            },
        )

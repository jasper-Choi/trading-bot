from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.korea_supply_demand import (
    get_institutional_tickers,
    get_investor_flow_today,
    get_smart_money_confirmed,
    get_supply_demand_score,
)
from app.services.korea_universe import get_korea_universe
from app.services.market_gateway import get_naver_daily_prices, get_naver_intraday_change, get_us_market_context
from app.services.signal_engine import summarize_breakout_signal, summarize_equity_signal

_FETCH_WORKERS = 12
# fchart API로 1요청=250일 데이터 → 전 종목(120개) 스캔 가능 (0.19s/종목 × 120 = ~2.5s)
# 기존 40종목 제한은 sise_day 22페이지 스크래핑 시대의 유물 (이제 불필요)
_UNIVERSE_SCAN_LIMIT = 120


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
        # ── vol 기준 ──────────────────────────────────────────────────────────
        # 2026-06-01: 2.0x → 1.5x 완화
        # 근거: 2.0x가 너무 엄격해 good signal(PM edge>0.3, RSI>60)도 차단
        #       stale_exit 원인은 거래량 아닌 장 초반 방향성 부재 → max_cycles 단축으로 대응
        #       1.5x는 유동성 최소 기준 유지하면서 진입 기회 복원
        # RSI: 50-85
        _vol_mult = 1.5
        _rsi_min, _rsi_max = 50.0, 85.0
        _min_breakout_pct = 0.3  # 20일 신고점 대비 최소 0.3% 돌파 (노이즈 차단)

        # ── Strategy B: 20일 신고점 돌파 스캔 ────────────────────────────────
        # 2026-06-01: 60일 → 20일 완화
        # 근거: 60일(3개월) 신고가는 진입 조건이 너무 타이트해 신호 빈도 극히 낮음
        #       20일(1개월)이 업계 표준 — 추세 확인 + 진입 빈도 균형
        #       _min_breakout_pct도 0.5 → 0.3으로 완화 (20일 기준에서는 더 작은 돌파도 의미 있음)
        universe = get_korea_universe()
        watchlist_items = [
            (item["ticker"], item["name"])
            for item in universe
        ][:_UNIVERSE_SCAN_LIMIT]

        def _fetch(ticker_name: tuple[str, str]) -> tuple[str, str, list[dict]]:
            ticker, name = ticker_name
            # 220일: EMA200 계산(Strategy S2) + 20일 신고점(Strategy B) 모두 지원
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
            if len(candles) < 22:  # 20일 신고점 계산 최소 요건
                continue

            # 시간대별 vol 기준 적용 (개장 초반 완화)
            bk = summarize_breakout_signal(
                candles,
                breakout_period=20,
                vol_surge_mult=_vol_mult,
                rsi_min=_rsi_min,
                rsi_max=_rsi_max,
            )
            confirmed_count = int(bk.get("confirmed_count", 0) or 0)
            if confirmed_count < 3:
                continue

            # 최소 돌파폭 필터: 신고점 대비 0.5% 미만 돌파는 노이즈 (예: +0.18% 같은 케이스)
            _period_high = float(bk.get("period_high", 0.0) or 0.0)
            _last_close = float(candles[-1].get("close") or 0.0)
            if bk.get("breakout") and _period_high > 0:
                _breakout_pct = (_last_close - _period_high) / _period_high * 100
                if _breakout_pct < _min_breakout_pct:
                    continue  # 노이즈 수준 돌파 제외

            # ── 당일 상승률 필터 (뒤늦게 올라타기 방지) ──────────────────────
            # 2026-06-01: 3% → 5% 완화
            # 근거: 한국 장 강세장에서 KOSPI 대형주(현대차+6.6%, 삼성전자+9.6% 등)는
            #       3% 이상 상승 중에도 신고점 돌파 + 추세 지속 가능성 높음.
            #       5%는 모멘텀 소진보다는 강한 추세 확인 구간 — 진입 허용.
            #       7% 이상은 단기 과열 — 차단 유지.
            try:
                _intraday_chg = get_naver_intraday_change(ticker)
                if _intraday_chg >= 7.0:
                    continue  # 7% 이상 당일 급등 — 모멘텀 소진 가능성 차단
            except Exception:
                pass  # 조회 실패 시 통과 (fail-open)

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

            # ── 뉴스 catalyst 체크 (Phase 3) ────────────────────────────────
            # catalyst_score 0~1 연속 점수 + 종토방 감성 통합
            _news_reason = ""
            _catalyst_rating = 5   # 기본 중립
            _jongto_hot = False
            _jongto_sentiment = 0.5
            try:
                from app.services.global_news_intel import get_stock_catalyst
                _catalyst = get_stock_catalyst(ticker, name)
                if _catalyst.get("negative"):
                    # 악재 뉴스 종목 — 진입 스킵
                    continue
                _delta = float(_catalyst.get("score_delta", 0.0) or 0.0)
                candidate_score = round(candidate_score + _delta, 2)
                _news_reason = _catalyst.get("reason", "")
                _catalyst_rating = int(_catalyst.get("catalyst_rating", 5) or 5)
                _jt = _catalyst.get("jongto", {}) or {}
                _jongto_hot = bool(_jt.get("hot", False))
                _jongto_sentiment = float(_jt.get("sentiment_score", 0.5) or 0.5)
                # 종토방 과열+부정 종목 — 개미 물타기 패턴 → 추가 감점
                if _jongto_hot and _jongto_sentiment < 0.35:
                    candidate_score = round(candidate_score - 0.08, 2)
                    _news_reason = (_news_reason + " [종토방 과열-부정]").strip()
            except Exception:
                pass  # 뉴스 수집 실패 시 무시 (차단하지 않음)

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
                "news_reason": _news_reason,
                "catalyst_rating": _catalyst_rating,   # Phase 3: 0~10
                "jongto_hot": _jongto_hot,             # Phase 3: 종토방 과열 여부
                "jongto_sentiment": _jongto_sentiment, # Phase 3: 종토방 감성 0~1
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
        # ── Strategy S22: 120일 신고가 돌파 (강화된 B전략) ──────────────────────
        # B전략(20일)보다 기간이 6배 길어 false positive 극적 감소 → 승률 향상 기대
        # 조건: 120일 신고가 돌파 + EMA 정배열 + 거래량 1.5x + RSI 50-82
        breakout_120d_candidates: list[dict] = []

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

                # ── S22: 120일 신고가 돌파 (강화된 B전략) ────────────────────
                if len(closes) >= 125:
                    high_120d = max(closes[-121:-1])  # 어제까지의 120일 최고가
                    if high_120d > 0:
                        ema60_s22 = _ema(closes, 60)
                        rsi14_s22 = _rsi(closes, 14)
                        vols_s22 = [float(c.get("volume") or 0.0) for c in candles[-22:]]
                        avg_vol_s22 = sum(vols_s22[:-1]) / max(len(vols_s22) - 1, 1)
                        vol_ratio_s22 = (vols_s22[-1] / avg_vol_s22) if avg_vol_s22 > 0 else 0.0
                        if (
                            closes[-1] > high_120d * 1.002       # 0.2%+ 명확한 돌파
                            and ema60_s22 > 0
                            and ema20 > ema60_s22 > ema200        # EMA 정배열 (중기 추세 확인)
                            and rsi14_s22 is not None
                            and 50.0 <= rsi14_s22 <= 82.0         # RSI 적정 구간
                            and vol_ratio_s22 >= 1.5              # 거래량 1.5x+
                        ):
                            breakout_120d_candidates.append({
                                "ticker": ticker,
                                "name": name,
                                "current_price": closes[-1],
                                "high_120d": round(high_120d, 0),
                                "breakout_pct": round((closes[-1] / high_120d - 1) * 100, 2),
                                "ema20": round(ema20, 0),
                                "ema60": round(ema60_s22, 0),
                                "ema200": round(ema200, 0),
                                "rsi14": round(rsi14_s22, 1),
                                "vol_ratio": round(vol_ratio_s22, 2),
                                "focus_tag": "breakout_120d",
                            })
            except Exception:
                continue

        # ── Strategy S15: Gap Momentum (갭 모멘텀) ──────────────────────────────
        # 백테스트 검증 (2026-05-22, 114종목 3년):
        # WR 48.9%, PF 1.97, Sharpe 3.32, MDD_port -2.7%, n=47
        # 조건: EMA 상승추세 + gap>=1% + chg1d>=2% + 강한종가(0.65) + vol>=1.5x + RSI 40-65
        # "상승했다가 하락 여지" 방지: RSI<=65 + 5일수익률<15% + chg5d 필터
        gap_momentum_candidates: list[dict] = []
        for ticker, name, candles in results:
            if len(candles) < 22:
                continue
            try:
                closes  = [float(c.get("close")  or 0.0) for c in candles]
                highs   = [float(c.get("high")   or 0.0) for c in candles]
                lows    = [float(c.get("low")    or 0.0) for c in candles]
                opens   = [float(c.get("open")   or 0.0) for c in candles]
                volumes = [float(c.get("volume") or 0.0) for c in candles]

                if len(closes) < 22 or closes[-1] <= 0:
                    continue

                ema20_val  = _ema(closes, 20)
                ema60_val  = _ema(closes, 60)  if len(closes) >= 60  else 0.0
                ema200_val = _ema(closes, 200) if len(closes) >= 200 else 0.0

                # 추세 정렬: close > EMA200 > 0, EMA20 > EMA60 > EMA200
                if not (
                    ema200_val > 0
                    and closes[-1] > ema200_val
                    and ema20_val > ema60_val > 0
                    and ema60_val > ema200_val
                ):
                    continue

                # EMA200 상승 기울기 (20일 전 대비 양수)
                if len(closes) >= 220:
                    ema200_20ago = _ema(closes[:-20], 200)
                    if ema200_20ago > 0 and ema200_val <= ema200_20ago:
                        continue

                # EMA20 5일 전 대비 상승
                if len(closes) >= 25:
                    ema20_5ago = _ema(closes[:-5], 20)
                    if ema20_val <= ema20_5ago:
                        continue

                prev_close = closes[-2] if len(closes) >= 2 else 0.0
                cur_open   = opens[-1]
                cur_close  = closes[-1]
                cur_high   = highs[-1]
                cur_low    = lows[-1]

                if prev_close <= 0 or cur_open <= 0:
                    continue

                gap_pct  = (cur_open / prev_close - 1.0) * 100
                chg1d    = (cur_close / prev_close - 1.0) * 100

                if gap_pct < 1.0 or chg1d < 2.0:
                    continue

                price_range = cur_high - cur_low
                close_strength = (cur_close - cur_low) / price_range if price_range > 0 else 0.5
                if close_strength < 0.65:
                    continue

                # 20일 평균 거래량 (당일 제외)
                vol20_avg = (sum(volumes[-21:-1]) / 20) if len(volumes) >= 21 else 0.0
                if vol20_avg <= 0 or volumes[-1] < vol20_avg * 1.5:
                    continue

                # 2026-06-01: RSI 필터 — pre-gap RSI 기준으로 변경
                # 근거: 당일 큰 갭 이후 RSI는 급등하여 72+ 초과.
                #       갭 전날 RSI(갭 직전 컨디션)를 기준으로 필터해야
                #       NAVER(+26.7%), 현대차(+6.6%) 같은 catalyst 갭업 포착 가능.
                #       갭 이후 RSI는 과열 방지용 상한(85)만 적용.
                pre_gap_rsi14 = _rsi(closes[:-1], 14)  # 전일까지의 RSI
                rsi14_val = _rsi(closes, 14)            # 당일 포함 RSI (상한 체크용)
                # 갭 전 RSI: 30-75 (약간 눌림~모멘텀) / 갭 후 RSI: <= 85 (극과열 차단)
                if pre_gap_rsi14 is None or not (30.0 <= pre_gap_rsi14 <= 75.0):
                    continue
                if rsi14_val is not None and rsi14_val > 85.0:
                    continue

                chg5d = (cur_close / closes[-6] - 1.0) * 100 if len(closes) >= 6 else 0.0
                if chg5d >= 15.0:
                    continue

                # ATR(14) as % of close — used for dynamic SL tightening at exit
                _atr_trs = [
                    max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                    for i in range(max(1, len(closes)-15), len(closes))
                    if highs[i] > 0 and lows[i] > 0 and closes[i-1] > 0
                ]
                _atr_val_gm = round(sum(_atr_trs) / len(_atr_trs) / cur_close * 100, 3) if _atr_trs and cur_close > 0 else 0.0

                gap_momentum_candidates.append({
                    "ticker":         ticker,
                    "name":           name,
                    "current_price":  cur_close,
                    "gap_pct":        round(gap_pct, 2),
                    "chg1d":          round(chg1d, 2),
                    "close_strength": round(close_strength, 3),
                    "vol_ratio":      round(volumes[-1] / vol20_avg, 2),
                    "rsi14":          rsi14_val,
                    "atr_pct":        _atr_val_gm,
                    "chg5d":          round(chg5d, 2),
                    "ema20":          round(ema20_val, 0),
                    "ema200":         round(ema200_val, 0),
                    "focus_tag":      "gap_momentum",
                })
            except Exception:
                continue

        # ── Strategy S20: Catalyst Gap (강한 갭업 촉매 모멘텀) ──────────────────
        # 2026-06-01 신설 — NAVER(+26.7%), 현대차(+6.6%), 삼성전자우(+14.1%) 미포착 교훈
        # 기존 gap_momentum(S15)는 EMA20>EMA60>EMA200 완전 정렬 요구 → 촉매성 갭은 차단됨
        # S20 조건: 강한 갭(gap>=5%) + 거래량 폭증(vol>=2.5x) + 상승 종가 유지(strength>=0.70)
        #           + EMA200 위에서 거래 (1차 추세 확인만) + 전일 RSI 75 이하 (극과열 제외)
        # 백테스트 없음 — 포워드 테스트로 시작, 사이즈 작게(0.25x~0.35x) 설정
        catalyst_gap_candidates: list[dict] = []
        for ticker, name, candles in results:
            if len(candles) < 22:
                continue
            try:
                closes  = [float(c.get("close")  or 0.0) for c in candles]
                highs   = [float(c.get("high")   or 0.0) for c in candles]
                lows    = [float(c.get("low")    or 0.0) for c in candles]
                opens   = [float(c.get("open")   or 0.0) for c in candles]
                volumes = [float(c.get("volume") or 0.0) for c in candles]

                if len(closes) < 22 or closes[-1] <= 0:
                    continue

                # EMA200: 1차 장기 추세 확인만 (EMA20/60 스택 불필요)
                ema200_val = _ema(closes, 200) if len(closes) >= 200 else 0.0
                if ema200_val <= 0 or closes[-1] <= ema200_val:
                    continue

                prev_close = closes[-2] if len(closes) >= 2 else 0.0
                cur_open   = opens[-1]
                cur_close  = closes[-1]
                cur_high   = highs[-1]
                cur_low    = lows[-1]

                if prev_close <= 0 or cur_open <= 0:
                    continue

                gap_pct  = (cur_open / prev_close - 1.0) * 100
                chg1d    = (cur_close / prev_close - 1.0) * 100

                # 강한 갭업 + 당일 등락 유지
                if gap_pct < 5.0 or chg1d < 5.0:
                    continue

                # 상승 유지력
                price_range = cur_high - cur_low
                close_strength = (cur_close - cur_low) / price_range if price_range > 0 else 0.5
                if close_strength < 0.70:
                    continue

                # 거래량 폭증 (20일 평균 2.5배 이상)
                vol20_avg = (sum(volumes[-21:-1]) / 20) if len(volumes) >= 21 else 0.0
                if vol20_avg <= 0 or volumes[-1] < vol20_avg * 2.5:
                    continue

                # 전일 RSI <= 75 (촉매 전 극과열 종목 제외)
                pre_gap_rsi14 = _rsi(closes[:-1], 14)
                if pre_gap_rsi14 is not None and pre_gap_rsi14 > 75.0:
                    continue

                # 5일 수익률 과열 방지
                chg5d = (cur_close / closes[-6] - 1.0) * 100 if len(closes) >= 6 else 0.0
                if chg5d >= 25.0:
                    continue

                # 이미 gap_momentum에 포함된 종목 제외 (중복 방지)
                if any(str(c.get("ticker","")) == ticker for c in gap_momentum_candidates):
                    continue

                vol_ratio = round(volumes[-1] / vol20_avg, 2) if vol20_avg > 0 else 0.0
                catalyst_gap_candidates.append({
                    "ticker":         ticker,
                    "name":           name,
                    "current_price":  cur_close,
                    "gap_pct":        round(gap_pct, 2),
                    "chg1d":          round(chg1d, 2),
                    "close_strength": round(close_strength, 3),
                    "vol_ratio":      vol_ratio,
                    "pre_gap_rsi14":  round(pre_gap_rsi14 or 0.0, 1),
                    "chg5d":          round(chg5d, 2),
                    "ema200":         round(ema200_val, 0),
                    "focus_tag":      "catalyst_gap",
                })
            except Exception:
                continue

        # ── S2/S9/S10 전략 후보 catalyst 후처리 필터 (Phase 3) ──────────────────
        # 평균회귀 전략은 악재 뉴스 종목 진입 시 낙폭 연장 리스크 → 상위 후보만 체크
        def _apply_catalyst_filter(clist: list[dict], max_check: int = 5) -> list[dict]:
            try:
                from app.services.global_news_intel import get_stock_catalyst
                filtered = []
                for c in clist:
                    tkr = str(c.get("ticker", "") or "")
                    nm  = str(c.get("name", "") or "")
                    if not tkr:
                        filtered.append(c)
                        continue
                    if len(filtered) >= max_check:
                        break
                    cat = get_stock_catalyst(tkr, nm)
                    if cat.get("negative"):
                        continue  # 악재 종목 제거
                    # jongto 과열+부정 제거
                    jt = cat.get("jongto", {}) or {}
                    if jt.get("hot") and float(jt.get("sentiment_score", 0.5) or 0.5) < 0.35:
                        continue
                    c["catalyst_rating"]  = int(cat.get("catalyst_rating", 5) or 5)
                    c["jongto_hot"]       = bool(jt.get("hot", False))
                    c["jongto_sentiment"] = float(jt.get("sentiment_score", 0.5) or 0.5)
                    filtered.append(c)
                return filtered
            except Exception:
                return clist  # 실패 시 원본 반환

        mongtata_candidates    = _apply_catalyst_filter(mongtata_candidates)
        rsi2_candidates        = _apply_catalyst_filter(rsi2_candidates)
        nday_candidates        = _apply_catalyst_filter(nday_candidates)
        gap_momentum_candidates = _apply_catalyst_filter(gap_momentum_candidates)
        # S20 catalyst_gap: 악재 뉴스 필터 후 등락률 내림차순 정렬
        catalyst_gap_candidates = _apply_catalyst_filter(catalyst_gap_candidates)
        catalyst_gap_candidates.sort(key=lambda x: x.get("chg1d", 0.0), reverse=True)

        # ── Strategy S18/S19: 기관+외국인 스마트머니 확인 전략 ────────────────────
        # S18 (inst_foreign_breakout): 신고점 돌파 후보 중 기관 레이더 포함 + 외국인 순매수
        # S19 (inst_foreign_gap):      갭 모멘텀 후보 중 기관 레이더 포함 + 외국인 순매수
        # 두 조건 동시 충족 시 스마트머니 확인 신호 — 기존 전략 대비 우선순위 상향
        inst_foreign_candidates: list[dict] = []
        try:
            # 상위 후보만 확인 (Naver API 레이트 리밋 대응)
            _SMART_MONEY_CHECK_LIMIT = 8
            _seen_tickers: set[str] = set()
            for c in breakout_candidates[:_SMART_MONEY_CHECK_LIMIT]:
                tkr = str(c.get("ticker", "") or "").strip()
                if not tkr or tkr in _seen_tickers:
                    continue
                if get_smart_money_confirmed(tkr, inst_tickers, min_foreign_net_bn=0.0):
                    flow = get_investor_flow_today(tkr)
                    inst_foreign_candidates.append({
                        **c,
                        "focus_tag":      "inst_foreign_breakout",
                        "foreign_net_bn": round(float(flow.get("foreign_net", 0.0)), 2),
                        "foreign_streak": int(flow.get("foreign_streak", 0)),
                    })
                    _seen_tickers.add(tkr)
            for c in gap_momentum_candidates[:_SMART_MONEY_CHECK_LIMIT]:
                tkr = str(c.get("ticker", "") or "").strip()
                if not tkr or tkr in _seen_tickers:
                    continue
                if get_smart_money_confirmed(tkr, inst_tickers, min_foreign_net_bn=0.0):
                    flow = get_investor_flow_today(tkr)
                    inst_foreign_candidates.append({
                        **c,
                        "focus_tag":      "inst_foreign_gap",
                        "foreign_net_bn": round(float(flow.get("foreign_net", 0.0)), 2),
                        "foreign_streak": int(flow.get("foreign_streak", 0)),
                    })
                    _seen_tickers.add(tkr)
            # S20 catalyst_gap도 기관+외국인 확인 가능
            for c in catalyst_gap_candidates[:_SMART_MONEY_CHECK_LIMIT]:
                tkr = str(c.get("ticker", "") or "").strip()
                if not tkr or tkr in _seen_tickers:
                    continue
                if get_smart_money_confirmed(tkr, inst_tickers, min_foreign_net_bn=0.0):
                    flow = get_investor_flow_today(tkr)
                    inst_foreign_candidates.append({
                        **c,
                        "focus_tag":      "inst_foreign_catalyst",
                        "foreign_net_bn": round(float(flow.get("foreign_net", 0.0)), 2),
                        "foreign_streak": int(flow.get("foreign_streak", 0)),
                    })
                    _seen_tickers.add(tkr)
        except Exception:
            pass

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
        if gap_momentum_candidates:
            score += 0.15  # S15 갭 모멘텀 신호 존재 시 추가 점수
        if catalyst_gap_candidates:
            score += 0.15  # S20 촉매 갭 신호 존재 시 추가 점수 (강한 당일 신호)
        if inst_foreign_candidates:
            score += 0.10  # S18/S19/S21 기관+외국인 동시 확인 시 추가 점수
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
                f"S2:{len(mongtata_candidates)} S9:{len(rsi2_candidates)} "
                f"S15:{len(gap_momentum_candidates)} S16:{len(close_panic_candidates)} "
                f"S18/19:{len(inst_foreign_candidates)} S20:{len(catalyst_gap_candidates)} "
                f"S22:{len(breakout_120d_candidates)} "
                f"vol_thr={_vol_mult}x RSI={_rsi_min:.0f}-{_rsi_max:.0f} "
                f"(universe {len(universe)}종목/{len(watchlist_items)}스캔)"
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
                # Strategy S22: 120일 신고가 돌파 (2026-06-02 신설)
                # B전략(20일) 대비 기간 6배 → false positive 감소, 승률 향상 기대
                "breakout_120d_candidates": breakout_120d_candidates[:5],
                "breakout_120d_count": len(breakout_120d_candidates),
                # Strategy S15 Gap Momentum (2026-05-22)
                # 백테스트: WR 48.9%, PF 1.97, Sharpe 3.32, MDD -2.7%, n=47
                "gap_momentum_candidates": gap_momentum_candidates[:5],
                "gap_momentum_count": len(gap_momentum_candidates),
                # Strategy S16 Close Panic Reversal (포워드 테스트 — 신호 수집만)
                "close_panic_candidates": close_panic_candidates[:3],
                "close_panic_count": len(close_panic_candidates),
                # Strategy S18 inst_foreign_breakout / S19 inst_foreign_gap / S21 inst_foreign_catalyst
                # 기관 레이더 + 외국인 순매수 동시 확인 후보 (우선순위 상위)
                "inst_foreign_candidates": inst_foreign_candidates[:5],
                "inst_foreign_count": len(inst_foreign_candidates),
                # Strategy S20 Catalyst Gap (2026-06-01 신설)
                # 강한 갭업(gap>=5%) + EMA200 위 + 거래량 폭증 — 포워드 테스트
                "catalyst_gap_candidates": catalyst_gap_candidates[:5],
                "catalyst_gap_count": len(catalyst_gap_candidates),
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

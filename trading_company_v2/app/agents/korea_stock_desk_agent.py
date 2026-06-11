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


def _compute_korea_market_regime() -> dict:
    """KOSPI/KOSDAQ 지수 일봉 기반 한국 시장 자체 레짐 판정 (2026-06-11).

    배경(구조 결함): orchestrator._determine_regime은 crypto 신호 유무로 레짐을
    결정하고 trend_structure_agent는 score 0.58 고정 스텁 → KOSPI가 추세장이어도
    crypto 무신호면 글로벌 레짐이 RANGING으로 고정되어 한국 추세 전략
    (B/new_high_breakout, S15/gap_momentum, S23/pre_gap_watch)이 영구 차단됨.

    규칙 (지수별 판정):
      STRESSED: 5일 수익률 ≤ -3.5% (급성 폭락만 — 5거래일 내 자동 해제됨)
        단, 당일 +2% 이상 반등 중이면 RANGING으로 완화 (반등일 조기 포착)
        근거: 2026-06 폭락에서 06-09 하루 +8.2% 반등 — chg5 기준만으로는
        V반등 첫날을 통째로 차단함. RANGING 완화는 평균회귀(stop -1.4%)만
        허용하므로 데드캣 반등이어도 손실 제한적.
      TRENDING: 종가 > EMA20 > EMA60 & 20일 수익률 ≥ +1.5% (상승 추세만 — long-only)
      RANGING : 그 외 (만성 하락장 포함 — 평균회귀 전략은 유지, 모멘텀만 차단)
    결합: 하나라도 STRESSED → STRESSED / 아니면 하나라도 TRENDING → TRENDING / 그 외 RANGING
    실패 시 빈 dict 반환 → 글로벌 레짐 폴백 (fail-open, 기존 동작 유지)

    설계 노트: 만성 하락(EMA60 아래 + 20일 -5%)을 STRESSED로 두면 몇 주간
    전면 차단될 수 있어 "영구적 진입 제한 금지" 운영 원칙과 충돌 → RANGING으로
    분류해 평균회귀(S2/S9/S13)와 개별 돌파(S18/S22/S27/S29)는 계속 허용.
    """
    out: dict = {}
    details: list[str] = []
    verdicts: list[str] = []
    for idx_symbol in ("KOSPI", "KOSDAQ"):
        try:
            candles = get_naver_daily_prices(idx_symbol, count=130)
            closes = [float(c.get("close") or 0.0) for c in candles if float(c.get("close") or 0.0) > 0]
            if len(closes) < 65:
                continue
            ema20 = _ema(closes, 20)
            ema60 = _ema(closes, 60)
            chg1d = (closes[-1] / closes[-2] - 1.0) * 100.0 if len(closes) >= 2 else 0.0
            chg5 = (closes[-1] / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 else 0.0
            chg20 = (closes[-1] / closes[-21] - 1.0) * 100.0 if len(closes) >= 21 else 0.0
            # 당일 +2% 이상 반등 중이면 급성 STRESSED를 RANGING으로 완화
            # (fchart 마지막 캔들은 장중 실시간 갱신 — 당일 반등을 즉시 반영)
            if chg5 <= -3.5 and chg1d < 2.0:
                verdict = "STRESSED"
            elif closes[-1] > ema20 > ema60 and chg20 >= 1.5:
                verdict = "TRENDING"
            else:
                verdict = "RANGING"
            verdicts.append(verdict)
            details.append(
                f"{idx_symbol}:{verdict} chg1d={chg1d:+.1f}% chg5={chg5:+.1f}% chg20={chg20:+.1f}%"
            )
            out[f"{idx_symbol.lower()}_chg20_pct"] = round(chg20, 2)
        except Exception:
            continue
    if not verdicts:
        return {}
    if "STRESSED" in verdicts:
        combined = "STRESSED"
    elif "TRENDING" in verdicts:
        combined = "TRENDING"
    else:
        combined = "RANGING"
    out["korea_market_regime"] = combined
    out["korea_regime_detail"] = " / ".join(details)
    return out


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
        # 2026-06-01: 2.0x → 1.5x 완화 (진입 빈도 복원 목적)
        # 2026-06-08: 1.5x → 2.0x 재강화
        # 근거: 백테스트(2022-2025, 117종목) 결과 1.5x 설정에서
        #       WR=38.6%, PF=1.15, Sharpe=1.02 (기준 미달)
        #       2.0x 설정(B_MID)에서 Sharpe=1.43으로 유의미하게 개선됨
        #       신호 빈도 917→520건으로 감소하지만 품질 상승이 우선
        # RSI: 50-80 (과매수 진입 방지, 85→80 조정)
        _vol_mult = 2.0
        _rsi_min, _rsi_max = 50.0, 80.0
        _min_breakout_pct = 0.5  # 20일 신고점 대비 최소 0.5% 돌파 (노이즈 차단)

        # ── Strategy B: 20일 신고점 돌파 스캔 ────────────────────────────────
        # 2026-06-01: 60일 → 20일 완화 (업계 표준, 유지)
        # 근거: 60일(3개월) 신고가는 진입 조건이 너무 타이트해 신호 빈도 극히 낮음
        #       20일(1개월)이 업계 표준 — 추세 확인 + 진입 빈도 균형
        #       _min_breakout_pct: 0.3 → 0.5 (2026-06-08 재강화, 노이즈 감소)
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

        # ── 2026-06-02 백테스트 결과 — 비활성 전략 목록 ────────────────────
        # S2 MONGTATA: P&L비율 0.27 (-5% stop 구조적 문제) → 비활성화
        # S22 120일: P&L비율 0.93 → 포워드 테스트 전환 (스캔은 유지, 진입만 차단)
        # 스캔은 계속 (signal 모니터링용), 진입은 recommendation_engine에서 차단
        mongtata_candidates: list[dict] = []   # S2: 스캔 유지 (포워드 모니터링)
        rsi2_candidates: list[dict] = []       # S9/S13: ACTIVATE/WATCH
        breakout_120d_candidates: list[dict] = []  # S22: 포워드 모니터링
        near_120d_candidates: list[dict] = []  # S24: 120일 고점 접근 (pre-breakout)
        # [2026-06-11] S27/S29 스캐너 — 80de8e9에서 recommendation_engine 소비측만
        # 커밋되고 데스크 스캐너(생산측)가 누락됐던 버그 복구 (이전까지 항상 빈 리스트)
        bb_squeeze_candidates: list[dict] = []   # S27: BB 스퀴즈 → 상향 돌파
        volume_surge_candidates: list[dict] = [] # S29: 거래량 폭발 + 가격 횡보

        for ticker, name, candles in results:
            if len(candles) < 205:
                continue
            try:
                closes = [float(c.get("close") or 0.0) for c in candles]
                if not closes or closes[-1] <= 0:
                    continue
                ema200 = _ema(closes, 200)
                # [2026-06-11] EMA200 필터 완화 (ema200 * 0.95): 80de8e9 커밋 메시지에
                # 포함됐으나 실제 코드 반영이 누락됐던 변경 복구 — RANGING 장에서
                # EMA200 부근 종목도 mongtata/RSI2 후보에 포함
                if ema200 <= 0 or closes[-1] <= ema200 * 0.95:
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

                # ── S9/S13: RSI(2) < 15 + EMA20*0.975 ─────────────────────
                # 2026-06-02: 10→15로 완화 — RANGING 장에서 극단 과매도(RSI<10) 희귀
                # RSI<15: 상단 과매도 제외하면서도 신호 빈도 확보 (백테스트 PASS 유지)
                rsi2_val = _rsi(closes, 2)
                if (rsi2_val is not None and rsi2_val < 15.0
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

                        # ── S24: 120일 고점 접근 (pre-breakout accumulation) ─────
                        # 돌파 전 7% 이내 접근 + 거래량 축적 → 다음 돌파 선행 포착
                        # EMA 완전 정배열 불요 — 섹터 촉매로 하락장 종목도 급등 가능
                        elif ticker not in {c["ticker"] for c in breakout_120d_candidates}:
                            dist_pct = (closes[-1] / high_120d - 1) * 100
                            if -7.0 <= dist_pct < 0.2:
                                vols_s24 = [float(c.get("volume") or 0.0) for c in candles[-22:]]
                                valid_v = [v for v in vols_s24 if v > 0]
                                avg10_s24 = sum(valid_v[-11:-1]) / 10 if len(valid_v) >= 11 else 0
                                avg3_s24 = sum(valid_v[-4:-1]) / 3 if len(valid_v) >= 4 else 0
                                vr_s24 = avg3_s24 / avg10_s24 if avg10_s24 > 0 else 0
                                ema60_s24 = _ema(closes, 60)
                                if (
                                    vr_s24 >= 1.3                 # 거래량 축적
                                    and rsi14_s22 is not None
                                    and 40.0 <= rsi14_s22 <= 78.0
                                    and ema20 > ema60_s24 > 0     # 중기 상승추세만 요구
                                    and closes[-1] > ema20        # 가격 > EMA20
                                ):
                                    near_120d_candidates.append({
                                        "ticker": ticker,
                                        "name": name,
                                        "current_price": closes[-1],
                                        "high_120d": round(high_120d, 0),
                                        "dist_pct": round(dist_pct, 2),
                                        "vol_ratio": round(vr_s24, 2),
                                        "rsi14": round(rsi14_s22, 1),
                                        "ema20": round(ema20, 0),
                                        "ema60": round(ema60_s24, 0),
                                        "focus_tag": "near_120d",
                                    })

                # ── S27: 볼린저 밴드 스퀴즈 → 상향 돌파 (2026-06-11 스캐너 복구) ──
                # 전일 기준 BB폭(4σ/EMA20) < 4% 압축 + 당일 종가가 전일 상단밴드 돌파
                # + 거래량 1.3x 이상 동반 — 압축된 에너지의 방향 분출 포착
                _vols_s27 = [float(c.get("volume") or 0.0) for c in candles[-22:]]
                _avg_vol20_s27 = sum(_vols_s27[:-1]) / max(len(_vols_s27) - 1, 1)
                _vol_ratio_today = (_vols_s27[-1] / _avg_vol20_s27) if _avg_vol20_s27 > 0 else 0.0
                _closes_prev = closes[:-1]
                _ema20_prev = _ema(_closes_prev, 20)
                _std20_prev = _std_last(_closes_prev, 20)
                if _ema20_prev > 0 and _std20_prev > 0:
                    _bb_width_pct = (4.0 * _std20_prev) / _ema20_prev * 100.0
                    _upper_bb_prev = _ema20_prev + 2.0 * _std20_prev
                    if (
                        _bb_width_pct < 4.0                # 에너지 압축 완료
                        and closes[-1] > _upper_bb_prev    # 상향 돌파
                        and _vol_ratio_today >= 1.3        # 거래량 동반 확인
                    ):
                        bb_squeeze_candidates.append({
                            "ticker": ticker,
                            "name": name,
                            "current_price": closes[-1],
                            "bb_width_ratio_pct": round(_bb_width_pct, 2),
                            "upper_bb": round(_upper_bb_prev, 0),
                            "vol_ratio": round(_vol_ratio_today, 2),
                            "focus_tag": "bb_squeeze_breakout",
                        })

                # ── S29: 거래량 폭발(5x+) + 가격 횡보(<2%) — 세력 매집 징후 ──
                if len(closes) >= 2 and closes[-2] > 0:
                    _price_chg_pct = (closes[-1] / closes[-2] - 1.0) * 100.0
                    if _vol_ratio_today >= 5.0 and abs(_price_chg_pct) < 2.0:
                        volume_surge_candidates.append({
                            "ticker": ticker,
                            "name": name,
                            "current_price": closes[-1],
                            "vol_ratio": round(_vol_ratio_today, 2),
                            "price_chg_pct": round(_price_chg_pct, 2),
                            "focus_tag": "volume_surge",
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
        gap_momentum_candidates = _apply_catalyst_filter(gap_momentum_candidates)
        # S20 catalyst_gap: 악재 뉴스 필터 후 등락률 내림차순 정렬
        catalyst_gap_candidates = _apply_catalyst_filter(catalyst_gap_candidates)
        catalyst_gap_candidates.sort(key=lambda x: x.get("chg1d", 0.0), reverse=True)

        # ── Strategy S23: Universal Pre-Gap Catalyst Scanner ────────────────────
        # 2026-06-08: 비활성화 (실전 WR=0%, n=2, 로보티즈/043260 2거래 모두 손절)
        # 재활성화 조건: 포워드 테스트 n≥20 달성 후 WR≥45% 확인 시
        # TODO: pre_gap_watch 전략 심층 파라미터 재설계 필요
        pre_gap_watch_candidates: list[dict] = []
        try:
            # S23 비활성화 (2026-06-08): WR=0%, n=2 (로보티즈·043260 모두 손절)
            # 재활성화 조건: 포워드 테스트 n≥20 + WR≥45% 확인 후 아래 raise 제거
            raise StopIteration("S23_disabled_20260608")
            from concurrent.futures import ThreadPoolExecutor as _S23Executor, as_completed as _s23_as_completed

            # ── 매크로 부스트 맵 (섹터 매핑 기반, boost multiplier로만 사용) ──
            _s23_boosted: dict[str, float] = {}
            _s23_news_intel: dict = {}
            try:
                from app.services.macro_sector_map import get_boosted_tickers
                try:
                    from app.services.global_news_intel import get_market_news_intel
                    _s23_news_intel = get_market_news_intel()
                except Exception:
                    pass
                _s23_boosted = get_boosted_tickers(_s23_news_intel, us_ctx)
            except Exception:
                pass

            # ── 1단계: 전 유니버스 기술적 1차 필터 ─────────────────────────
            _s23_quick: list[tuple[str, str, float]] = []  # (ticker, name, vol_score)
            _already_in_other_s23 = set(
                str(c.get("ticker", ""))
                for c in (breakout_candidates + gap_momentum_candidates
                          + catalyst_gap_candidates + breakout_120d_candidates)
            )
            for _tkr, _nm, _cands in results:
                if _tkr in _already_in_other_s23:
                    continue
                if len(_cands) < 22:
                    continue
                try:
                    _cls = [float(c.get("close") or 0.0) for c in _cands]
                    _vls = [float(c.get("volume") or 0.0) for c in _cands]
                    if _cls[-1] <= 0 or _cls[-2] <= 0:
                        continue

                    # EMA200 위 확인 (200봉 미만이면 스킵)
                    if len(_cls) >= 200:
                        _ema200_s23 = _ema(_cls, 200)
                        if _cls[-1] <= _ema200_s23 or _ema200_s23 <= 0:
                            continue

                    # 오늘 이미 갭 발생 종목 제외 (S20이 처리)
                    _gap_today = (_cls[-1] - _cls[-2]) / _cls[-2] * 100
                    if _gap_today > 3.0:
                        continue

                    # 거래량 축적 신호
                    _valid_vols = [v for v in _vls[-22:] if v > 0]
                    if len(_valid_vols) < 10:
                        continue
                    _vol3d = sum(_valid_vols[-4:-1]) / 3 if len(_valid_vols) >= 4 else 0
                    _vol10d = sum(_valid_vols[-11:-1]) / 10 if len(_valid_vols) >= 11 else 0
                    if _vol3d <= 0 or _vol10d <= 0:
                        continue
                    _vrat = _vol3d / _vol10d
                    if _vrat < 1.15:  # 거래량 축적 없으면 제외
                        continue

                    # RSI 필터
                    _rsi14_s23 = _rsi(_cls, 14)
                    if _rsi14_s23 is not None and (_rsi14_s23 < 30 or _rsi14_s23 > 78):
                        continue

                    # 매크로 부스트 추가 (있으면 가산)
                    _macro_b = float(_s23_boosted.get(_tkr, 0.0))
                    _vol_score = _vrat + _macro_b * 0.5  # 매크로 있으면 우선순위 올림
                    _s23_quick.append((_tkr, _nm, _vol_score))
                except Exception:
                    continue

            # 거래량+매크로 복합 점수 순 정렬, 상위 20개만 딥스캔
            _s23_quick.sort(key=lambda x: x[2], reverse=True)
            _s23_to_scan = _s23_quick[:20]

            # ── 2단계: 상위 20개 병렬 catalyst 딥스캔 ─────────────────────
            def _s23_check(item: tuple[str, str, float]) -> dict | None:
                _t, _n, _vr = item
                try:
                    _cat = get_stock_catalyst(_t, _n)
                    _cr = int(_cat.get("catalyst_rating", 0) or 0)
                    _jt = _cat.get("jongto", {}) or {}
                    _jt_hot = bool(_jt.get("hot", False))
                    _jt_sent = float(_jt.get("sentiment_score", 0.5) or 0.5)
                    _mb = float(_s23_boosted.get(_t, 0.0))
                    # 매크로 부스트 있으면 threshold 1점 완화
                    _thr = 4 if _mb >= 0.4 else 5
                    _ok = (
                        (_cr >= _thr and not _cat.get("negative"))
                        or (_jt_hot and _jt_sent >= 0.65 and not _cat.get("negative"))
                    )
                    if _ok:
                        return {
                            "ticker": _t,
                            "name": _n,
                            "current_price": 0.0,
                            "vol_ratio": round(_vr, 2),
                            "macro_boost": round(_mb, 2),
                            "catalyst_rating": _cr,
                            "catalyst_score": float(_cat.get("catalyst_score", 0.5) or 0.5),
                            "jongto_hot": _jt_hot,
                            "jongto_sentiment": _jt_sent,
                            "headlines": (_cat.get("headlines", []) or [])[:2],
                            "focus_tag": "pre_gap_watch",
                        }
                except Exception:
                    pass
                return None

            if _s23_to_scan:
                with _S23Executor(max_workers=6) as _ex23:
                    _futs = {_ex23.submit(_s23_check, item): item for item in _s23_to_scan}
                    for _fut in _s23_as_completed(_futs, timeout=30):
                        try:
                            _res = _fut.result()
                            if _res:
                                pre_gap_watch_candidates.append(_res)
                        except Exception:
                            pass

            # catalyst_rating × vol_ratio + macro_boost 복합 정렬
            pre_gap_watch_candidates.sort(
                key=lambda x: (
                    x.get("catalyst_rating", 0) * max(x.get("vol_ratio", 1.0), 1.0)
                    + x.get("macro_boost", 0.0) * 3.0
                ),
                reverse=True,
            )
        except Exception:
            pass

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
        elif mongtata_candidates or rsi2_candidates:
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

        # ── 한국 시장 자체 레짐 (KOSPI/KOSDAQ 지수 일봉 기반) ────────────────
        # 글로벌 레짐(crypto 신호 기반)의 한국 데스크 오판 보정 — fail-open
        korea_regime_ctx: dict = {}
        try:
            korea_regime_ctx = _compute_korea_market_regime()
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
                f"S22:{len(breakout_120d_candidates)} S23:{len(pre_gap_watch_candidates)} "
                f"S24:{len(near_120d_candidates)} "
                f"S27:{len(bb_squeeze_candidates)} S29:{len(volume_surge_candidates)} "
                f"K-regime={korea_regime_ctx.get('korea_market_regime', 'n/a')} "
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
                "breakout_120d_candidates": breakout_120d_candidates[:5],
                "breakout_120d_count": len(breakout_120d_candidates),
                # Strategy S24: 120일 고점 접근 pre-breakout (2026-06-04 신설)
                "near_120d_candidates": sorted(
                    near_120d_candidates, key=lambda c: c.get("dist_pct", -99), reverse=True
                )[:5],
                "near_120d_count": len(near_120d_candidates),
                # Strategy S23: Pre-Market Macro/News Catalyst (2026-06-02 신설)
                # 트럼프/머스크/US 밤사이 급등 + 종목 뉴스 급증 → 갭 발생 전 선행 포착
                "pre_gap_watch_candidates": pre_gap_watch_candidates[:5],
                "pre_gap_watch_count": len(pre_gap_watch_candidates),
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
                # Strategy S27 BB Squeeze Breakout (2026-06-11 스캐너 복구)
                # BB폭 좁은 순(압축 강한 순) 정렬 — 분출 에너지 큰 종목 우선
                "bb_squeeze_candidates": sorted(
                    bb_squeeze_candidates, key=lambda c: c.get("bb_width_ratio_pct", 99)
                )[:5],
                "bb_squeeze_count": len(bb_squeeze_candidates),
                # Strategy S29 Volume Surge + 횡보 (2026-06-11 스캐너 복구)
                "volume_surge_candidates": sorted(
                    volume_surge_candidates, key=lambda c: c.get("vol_ratio", 0), reverse=True
                )[:5],
                "volume_surge_count": len(volume_surge_candidates),
                # ── 한국 시장 자체 레짐 (KOSPI/KOSDAQ 기반, 2026-06-11) ──
                # build_korea_plan에서 글로벌(crypto 기반) 레짐 대신 우선 사용
                **korea_regime_ctx,
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

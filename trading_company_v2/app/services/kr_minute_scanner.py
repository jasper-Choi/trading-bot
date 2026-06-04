"""한국주식 분봉 실시간 스캐너 — PPP (Peak→Pullback→Profit) 패턴 감지.

PPP 스캘핑 원리:
  1. Peak   : 강한 1분봉 상승 (세력/모멘텀 확인)
  2. Pullback: 단기 눌림 (차익실현/공매도 소화)
  3. Profit  : 눌림 후 재상승 시 진입 → 빠른 익절

특징:
  - 일봉 기반 전략이 잡지 못하는 장 중 급등 초기 포착
  - 60초 주기로 watchlist 종목 전체 스캔
  - 손절: 눌림 저점 하단, 목표: +1.5~2%
"""
from __future__ import annotations

import time
import threading
from datetime import datetime
from typing import Any

# ── 캐시 ─────────────────────────────────────────────────────────────────────
_SCAN_CACHE: dict[str, dict] = {}        # ticker → last scan result
_CACHE_TTL  = 55                         # 55초 캐시 (60초 주기와 맞춤)
_cache_lock = threading.Lock()

# ── 앵커 워치리스트 (항상 스캔할 핵심 종목) ─────────────────────────────────
_ANCHOR_WATCH = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대차
    "000270",  # 기아
    "035420",  # NAVER
    "035720",  # 카카오
    "005490",  # POSCO홀딩스
    "373220",  # LG에너지솔루션
    "006400",  # 삼성SDI
    "086520",  # 에코프로
    "003670",  # 포스코퓨처엠
    "207940",  # 삼성바이오로직스
    "068270",  # 셀트리온
    "105560",  # KB금융
    "055550",  # 신한지주
]

# ── PPP 패턴 파라미터 ─────────────────────────────────────────────────────────
_PEAK_BODY_PCT  = 0.40   # Peak 봉 최소 body 크기 (%)
_PEAK_VOL_RATIO = 1.8    # Peak 봉 최소 거래량 배수
_PULLBACK_MIN   = 0.25   # 눌림 최소 깊이 (peak 고점 대비 %)
_PULLBACK_CANDLES_MAX = 6  # 눌림 구간 최대 봉 수 (6분)
_RECOVERY_MIN   = 0.40   # 눌림 구간 내 회복 최소 비율 (0~1)
_DAY_MOM_MIN    = 1.5    # 오늘 시가 대비 최소 상승률 (%)
_SIGNAL_WINDOW  = 25     # 패턴 탐색 창 (최근 25분봉)


def detect_ppp(candles: list[dict], today_open: float = 0.0) -> dict | None:
    """PPP 패턴 감지.

    Args:
        candles   : KIS get_minute_candles() 반환값 (오래된→최신 순)
        today_open: 오늘 시가 (0이면 체크 생략)

    Returns:
        신호 없으면 None, 있으면 {entry_price, stop, target, strength, ...}
    """
    if len(candles) < 10:
        return None

    window = candles[-_SIGNAL_WINDOW:]  # 최근 25봉만 사용
    closes  = [c["close"]  for c in window]
    highs   = [c["high"]   for c in window]
    lows    = [c["low"]    for c in window]
    opens_  = [c["open"]   for c in window]
    volumes = [c["volume"] for c in window]

    cur_close = closes[-1]
    if cur_close <= 0:
        return None

    # ── 전체 방향성 필터: 오늘 시가 대비 최소 상승 ───────────────────────
    if today_open > 0 and cur_close < today_open * (1 + _DAY_MOM_MIN / 100):
        return None

    # ── 거래량 기준선 ─────────────────────────────────────────────────────
    vol_baseline = sum(volumes[:-5]) / max(len(volumes[:-5]), 1)
    if vol_baseline <= 0:
        return None

    # ── ① Peak 봉 탐색 (최근 5~25봉 범위) ──────────────────────────────────
    peak_idx  = None
    peak_high = 0.0
    for i in range(len(window) - 6, -1, -1):   # 최소 5봉 이후 시간 필요
        o, c, h = opens_[i], closes[i], highs[i]
        if o <= 0:
            continue
        body_pct  = (c - o) / o * 100
        vol_ratio = volumes[i] / vol_baseline if vol_baseline > 0 else 0
        if body_pct >= _PEAK_BODY_PCT and vol_ratio >= _PEAK_VOL_RATIO:
            if h > peak_high:           # 가장 높은 peak 선택
                peak_high = h
                peak_idx  = i

    if peak_idx is None or peak_high <= 0:
        return None

    # ── ② Pullback 구간 분석 (peak 이후 봉들) ──────────────────────────────
    post = window[peak_idx + 1:]         # peak 이후 봉
    if len(post) < 2:
        return None
    post = post[:_PULLBACK_CANDLES_MAX]  # 최대 N분만 봄

    pullback_low  = min(c["low"]  for c in post)
    pullback_vols = [c["volume"] for c in post]
    avg_pb_vol    = sum(pullback_vols) / len(pullback_vols)

    # 눌림 깊이 확인
    drawdown_pct = (peak_high - pullback_low) / peak_high * 100
    if drawdown_pct < _PULLBACK_MIN:
        return None                      # 눌림이 너무 얕음 (미진행)

    # 눌림 구간 거래량이 peak보다 낮아야 함 (분배/세력이탈 아님 확인)
    peak_vol = volumes[peak_idx]
    if avg_pb_vol > peak_vol * 0.85:    # 눌림 중 거래량이 너무 많으면 분배
        return None

    # ── ③ Profit Signal: 재상승 확인 ────────────────────────────────────────
    recovery_range = peak_high - pullback_low
    if recovery_range <= 0:
        return None

    # 현재가가 눌림 구간의 몇 % 회복했는지
    recovery_ratio = (cur_close - pullback_low) / recovery_range
    if recovery_ratio < _RECOVERY_MIN:
        return None                      # 아직 충분히 회복 안 됨

    # 최신 2봉 거래량이 눌림 평균보다 높아야 함 (재가속 확인)
    recent_vol_avg = (volumes[-1] + volumes[-2]) / 2
    if recent_vol_avg < vol_baseline * 0.9:
        return None

    # ── 진입 파라미터 계산 ────────────────────────────────────────────────────
    entry_price  = cur_close
    stop_price   = pullback_low * 0.9975          # 눌림 저점 -0.25% 아래
    target_price = entry_price * 1.020            # +2% 목표 (빠른 스캘핑)
    rr_ratio     = (target_price - entry_price) / max(entry_price - stop_price, 0.001)
    strength     = round(recovery_ratio, 2)

    if rr_ratio < 1.5:                            # R/R < 1.5 → 비효율 진입 스킵
        return None

    return {
        "entry_price"   : round(entry_price, 0),
        "stop_price"    : round(stop_price, 0),
        "target_price"  : round(target_price, 0),
        "peak_high"     : round(peak_high, 0),
        "pullback_low"  : round(pullback_low, 0),
        "drawdown_pct"  : round(drawdown_pct, 2),
        "recovery_ratio": round(recovery_ratio, 2),
        "rr_ratio"      : round(rr_ratio, 2),
        "strength"      : strength,
        "vol_baseline"  : round(vol_baseline, 0),
        "recent_vol"    : round(recent_vol_avg, 0),
    }


def build_watchlist(market_snapshot: dict) -> list[str]:
    """오늘의 스캔 대상 종목 목록 구성.

    기존 Korea 전략 후보 + 앵커 종목 합산, 중복 제거.
    최대 50종목 (API 부하 제한).
    """
    tickers: list[str] = list(_ANCHOR_WATCH)

    candidate_keys = (
        "new_high_breakout_candidates",
        "gap_momentum_candidates",
        "inst_foreign_candidates",
        "catalyst_gap_candidates",
        "breakout_120d_candidates",
        "pre_gap_watch_candidates",
        "rsi2_candidates",
    )
    for key in candidate_keys:
        for item in (market_snapshot.get(key) or []):
            tkr = str(item.get("ticker", "") or "").strip()
            if tkr and tkr not in tickers:
                tickers.append(tkr)

    return tickers[:50]


def scan_for_ppp(
    market_snapshot: dict,
    open_position_symbols: set[str],
    name_lookup: dict[str, str] | None = None,
    max_signals: int = 3,
) -> list[dict]:
    """분봉 PPP 패턴 스캔.

    Args:
        market_snapshot      : orchestrator state.market_snapshot
        open_position_symbols: 이미 열린 포지션 심볼 집합 (중복 진입 방지)
        name_lookup          : ticker → 종목명 (없으면 코드 표시)
        max_signals          : 최대 반환 신호 수

    Returns:
        [{ticker, name, signal_info, ...}, ...] — 강도 순 정렬
    """
    now = time.time()
    watchlist = build_watchlist(market_snapshot)
    signals: list[dict] = []

    from app.services.kis_broker import get_minute_candles  # 지연 임포트

    for ticker in watchlist:
        if ticker in open_position_symbols:
            continue  # 이미 보유 중 → 스킵

        # 캐시 확인
        with _cache_lock:
            cached = _SCAN_CACHE.get(ticker)
            if cached and (now - cached["ts"]) < _CACHE_TTL:
                if cached.get("signal"):
                    signals.append(cached["signal"])
                continue

        # KIS 분봉 조회
        try:
            candles = get_minute_candles(ticker, count=30)
            if len(candles) < 10:
                continue

            # 오늘 시가 = 첫 봉의 open (당일 봉만 있다고 가정)
            today_open = float(candles[0].get("open") or 0.0)
            ppp = detect_ppp(candles, today_open)

            result: dict | None = None
            if ppp:
                name = (name_lookup or {}).get(ticker, ticker)
                result = {
                    "ticker"      : ticker,
                    "name"        : name,
                    "current_price": ppp["entry_price"],
                    "stop_price"  : ppp["stop_price"],
                    "target_price": ppp["target_price"],
                    "peak_high"   : ppp["peak_high"],
                    "pullback_low": ppp["pullback_low"],
                    "drawdown_pct": ppp["drawdown_pct"],
                    "rr_ratio"    : ppp["rr_ratio"],
                    "strength"    : ppp["strength"],
                    "focus_tag"   : "ppp_scalp",
                }
                signals.append(result)

            with _cache_lock:
                _SCAN_CACHE[ticker] = {"ts": now, "signal": result}

        except Exception:
            continue

    # 강도(strength) 내림차순 정렬
    signals.sort(key=lambda x: x.get("strength", 0), reverse=True)
    return signals[:max_signals]

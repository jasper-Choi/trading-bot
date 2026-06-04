"""한국주식 분봉 실시간 스캐너 — 핑퓽팽 프로 스캘퍼 (PPP + Stop Hunt).

핑퓽팽 원리:
  핑 (Peak)     : 강한 1분봉 급등 + 거래량 폭증 (세력 존재 확인)
  퓽 (Stop Hunt): 세력 역가 — 지지선 아래 순간 찍고 즉시 회복 (개미 털기)
  팽 (Profit)   : 퓽 확인 후 재상승 진입 → 목표 +2~2.5%

Stop Hunt 식별:
  - 역가 봉: 하단 꼬리 ≥ 전체 범위 45%, 종가 범위 상위 55%+ 위치
  - 역가 거래량: 눌림 평균 1.5배+ (패닉 물량 소화 확인)
  - 1~2봉 내 지지선 회복 (빠른 반등)
  Stop Hunt 확인 시: 목표 +2.5%, 사이즈 0.25x (vs 일반 +2%, 0.20x)
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
_RECOVERY_MIN   = 0.40   # 일반 회복 최소 비율 (stop hunt 확인 시 0.30으로 완화)
_DAY_MOM_MIN    = 1.5    # 오늘 시가 대비 최소 상승률 (%)
_SIGNAL_WINDOW  = 25     # 패턴 탐색 창 (최근 25분봉)

# ── Stop Hunt(퓽) 감지 파라미터 ──────────────────────────────────────────────
_SH_WICK_RATIO  = 0.45   # 역가 봉 하단 꼬리 / 전체 범위 ≥ 45%
_SH_CLOSE_RATIO = 0.55   # 역가 봉 종가 / 전체 범위 위치 ≥ 55% (위쪽에 닫힘)
_SH_VOL_SPIKE   = 1.5    # 역가 봉 거래량 ≥ 눌림 평균 × 1.5 (패닉 흡수)
_SH_MAX_CANDLES = 2      # Stop Hunt는 최대 2봉 내에서 완료돼야 함


def _detect_stop_hunt(
    post: list[dict],
    vol_baseline: float,
    pullback_low: float,
) -> dict:
    """퓽(Stop Hunt/역가) 패턴 감지.

    세력 역가 특징:
    - 하단 꼬리가 몸통보다 긴 해머형 봉 (_SH_WICK_RATIO)
    - 해당 봉 거래량이 눌림 평균보다 크게 높음 (패닉 물량 흡수)
    - 종가가 전체 범위 상위 55%+ 위치 (즉시 회복)
    - 직후 봉에서 거래량 급감 + 가격 유지 (세력 흡수 완료)
    """
    empty = {"stop_hunt_confirmed": False, "stop_hunt_strength": 0.0, "stop_hunt_idx": -1}
    if len(post) < 1 or vol_baseline <= 0:
        return empty

    # 눌림 구간 내 봉별 분석 (최대 _SH_MAX_CANDLES 확인)
    check = post[:_SH_MAX_CANDLES]
    avg_pb_vol = sum(c["volume"] for c in post) / len(post) if post else 0.0

    best_strength = 0.0
    best_idx = -1

    for i, c in enumerate(check):
        o = float(c.get("open") or 0.0)
        h = float(c.get("high") or 0.0)
        lo = float(c.get("low") or 0.0)
        cl = float(c.get("close") or 0.0)
        vol = float(c.get("volume") or 0.0)

        if h <= lo or lo <= 0:
            continue

        total_range = h - lo
        body = abs(cl - o)
        lower_wick = min(o, cl) - lo
        close_pos = (cl - lo) / total_range  # 0=바닥, 1=상단

        # 하단 꼬리 비율
        wick_ratio = lower_wick / total_range if total_range > 0 else 0.0

        # Stop Hunt 판단 기준
        is_hammer = wick_ratio >= _SH_WICK_RATIO and close_pos >= _SH_CLOSE_RATIO
        vol_spike = avg_pb_vol > 0 and vol >= avg_pb_vol * _SH_VOL_SPIKE

        if not is_hammer:
            continue

        # 강도: 꼬리 비율 + 거래량 스파이크 + 회복 위치 조합
        strength = 0.0
        strength += min(wick_ratio / 0.7, 1.0) * 0.40         # 꼬리 길이 (최대 0.40)
        strength += min(close_pos / 0.8, 1.0) * 0.30          # 회복 위치 (최대 0.30)
        if vol_spike:
            spike_mult = vol / avg_pb_vol if avg_pb_vol > 0 else 1.0
            strength += min(spike_mult / 3.0, 1.0) * 0.30     # 거래량 스파이크 (최대 0.30)

        # 다음 봉에서 거래량 감소 + 가격 유지 확인 (보너스)
        if i + 1 < len(post):
            nxt = post[i + 1]
            nxt_vol = float(nxt.get("volume") or 0.0)
            nxt_cl = float(nxt.get("close") or 0.0)
            if nxt_vol < vol * 0.70 and nxt_cl >= lo:  # 거래량 급감 + 가격 유지
                strength = min(strength + 0.10, 1.0)

        strength = round(strength, 3)
        if strength > best_strength:
            best_strength = strength
            best_idx = i

    confirmed = best_strength >= 0.40  # 기준 강도 이상만 인정
    return {
        "stop_hunt_confirmed": confirmed,
        "stop_hunt_strength": best_strength,
        "stop_hunt_idx": best_idx,
    }


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

    # ── 퓽(Stop Hunt) 감지 ──────────────────────────────────────────────────
    sh = _detect_stop_hunt(post, vol_baseline, pullback_low)
    stop_hunt_confirmed = bool(sh["stop_hunt_confirmed"])
    stop_hunt_strength  = float(sh["stop_hunt_strength"])

    # Stop Hunt 확인 시 회복 최소값 완화 (0.40 → 0.30) — 더 이른 진입 허용
    recovery_min = 0.30 if stop_hunt_confirmed else _RECOVERY_MIN

    # ── ③ Profit Signal: 재상승 확인 ────────────────────────────────────────
    recovery_range = peak_high - pullback_low
    if recovery_range <= 0:
        return None

    # 현재가가 눌림 구간의 몇 % 회복했는지
    recovery_ratio = (cur_close - pullback_low) / recovery_range
    if recovery_ratio < recovery_min:
        return None                      # 아직 충분히 회복 안 됨

    # 최신 2봉 거래량이 눌림 평균보다 높아야 함 (재가속 확인)
    recent_vol_avg = (volumes[-1] + volumes[-2]) / 2
    if recent_vol_avg < vol_baseline * 0.9:
        return None

    # ── 진입 파라미터 계산 ────────────────────────────────────────────────────
    entry_price = cur_close
    # Stop Hunt 확인 시: 역가 저점 기준 타이트한 손절 (-0.15%)
    # 일반 눌림: 저점 -0.25% 아래
    stop_buffer = 0.0015 if stop_hunt_confirmed else 0.0025
    stop_price   = pullback_low * (1.0 - stop_buffer)
    # Stop Hunt 확인 시 목표 +2.5% (팽 폭발력 반영), 일반 +2.0%
    target_pct   = 0.025 if stop_hunt_confirmed else 0.020
    target_price = entry_price * (1.0 + target_pct)
    rr_ratio     = (target_price - entry_price) / max(entry_price - stop_price, 0.001)

    # 종합 강도: 회복 비율 + stop hunt 보정
    base_strength = recovery_ratio
    if stop_hunt_confirmed:
        base_strength = min(base_strength + stop_hunt_strength * 0.3, 1.0)
    strength = round(base_strength, 2)

    # Stop Hunt 확인 시 R/R 기준 상향 (1.5 → 1.8) — 더 엄격한 효율성 검사
    rr_min = 1.8 if stop_hunt_confirmed else 1.5
    if rr_ratio < rr_min:
        return None

    return {
        "entry_price"         : round(entry_price, 0),
        "stop_price"          : round(stop_price, 0),
        "target_price"        : round(target_price, 0),
        "peak_high"           : round(peak_high, 0),
        "pullback_low"        : round(pullback_low, 0),
        "drawdown_pct"        : round(drawdown_pct, 2),
        "recovery_ratio"      : round(recovery_ratio, 2),
        "rr_ratio"            : round(rr_ratio, 2),
        "strength"            : strength,
        "vol_baseline"        : round(vol_baseline, 0),
        "recent_vol"          : round(recent_vol_avg, 0),
        "stop_hunt_confirmed" : stop_hunt_confirmed,
        "stop_hunt_strength"  : stop_hunt_strength,
        "target_pct"          : target_pct,
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
                stop_hunt = bool(ppp.get("stop_hunt_confirmed", False))
                result = {
                    "ticker"             : ticker,
                    "name"               : name,
                    "current_price"      : ppp["entry_price"],
                    "stop_price"         : ppp["stop_price"],
                    "target_price"       : ppp["target_price"],
                    "peak_high"          : ppp["peak_high"],
                    "pullback_low"       : ppp["pullback_low"],
                    "drawdown_pct"       : ppp["drawdown_pct"],
                    "rr_ratio"           : ppp["rr_ratio"],
                    "strength"           : ppp["strength"],
                    "stop_hunt_confirmed": stop_hunt,
                    "stop_hunt_strength" : ppp.get("stop_hunt_strength", 0.0),
                    "target_pct"         : ppp.get("target_pct", 0.020),
                    # 핑퓽팽 확인 시 우선순위 상향 (strength 보정)
                    "focus_tag"          : "ppp_scalp_sh" if stop_hunt else "ppp_scalp",
                }
                signals.append(result)

            with _cache_lock:
                _SCAN_CACHE[ticker] = {"ts": now, "signal": result}

        except Exception:
            continue

    # 핑퓽팽(stop hunt 확인) 신호 우선, 그 다음 strength 내림차순
    signals.sort(
        key=lambda x: (int(x.get("stop_hunt_confirmed", False)), x.get("strength", 0)),
        reverse=True,
    )
    return signals[:max_signals]

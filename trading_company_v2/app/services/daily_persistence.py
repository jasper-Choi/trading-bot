"""daily_persistence.py — PersistenceCNN 룰 기반 일봉 추세 지속성 필터

PersistenceCNN 인사이트 (best3_strategies_for_crypto.md 기반):
  "최근 20일 OHLCV 패턴으로 추세 지속 확률 예측"
  → PyTorch CNN 없이 동일 개념을 t-통계량 기반 룰로 구현.

공식 (기술적 폴백):
  returns = daily_close의 일일 수익률
  z = mean(returns) / std(returns) * sqrt(N)  # t-통계량
  score = clip(0.5 + z / 10.0, 0.0, 1.0)

해석:
  score > 0.60 → 상승 추세 지속 중 (TRENDING long에 우호적)
  score > 0.55 → 소폭 상승 추세 (진입 허용 하한선)
  score  0.45~0.55 → 중립 (횡보)
  score < 0.45 → 하락 추세 지속 중 (TRENDING long 차단)

사용 위치:
  hot_path_guard._candidate_is_hot_entry_eligible — TRENDING 전략 진입 시 일봉 필터
  obvious_trend Path B — 추세 지속성 확인 (선택적 강화)
"""

from __future__ import annotations

import math
import time
import threading
from typing import Optional

import requests

# ── 캐시 설정 ──────────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_score_cache: dict[str, tuple[float, float]] = {}   # symbol → (score, loaded_at)
_CACHE_TTL_SEC = 14400.0                             # 4시간 캐시 (일봉 데이터는 자주 안 바뀜)
_REQUEST_TIMEOUT = 4.0                               # Upbit API 타임아웃 (핫패스 블락 방지)
_UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/days"
_DEFAULT_SCORE = 0.5                                 # 데이터 없을 때 중립값 (진입 차단 안 함)
_LOOKBACK_DAYS = 20                                  # 최근 20일 일봉 (PersistenceCNN WIN=20)


def _fetch_daily_closes(symbol: str) -> list[float]:
    """Upbit REST API에서 일봉 종가 리스트 반환 (최신 순 → 역정렬해서 오래된 것부터)."""
    try:
        resp = requests.get(
            _UPBIT_CANDLES_URL,
            params={"market": symbol, "count": _LOOKBACK_DAYS},
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list) or len(data) < 5:
            return []
        # Upbit는 최신 → 오래된 순 → 역정렬
        closes = [float(d["trade_price"]) for d in reversed(data)]
        return closes
    except Exception:
        return []


def _t_stat_persistence(closes: list[float]) -> float:
    """PersistenceCNN 기술적 폴백: 일봉 수익률의 t-통계량으로 추세 지속성 점수 계산.

    t-통계량 = mean(returns) / std(returns) * sqrt(N)
      양수 → 상승 지속, 음수 → 하락 지속
    score = clip(0.5 + t / 10.0, 0.0, 1.0)
      t=+2 → score=0.70 (강한 상승)
      t= 0 → score=0.50 (중립)
      t=-2 → score=0.30 (하락)
    """
    if len(closes) < 5:
        return _DEFAULT_SCORE
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
    ]
    n = len(returns)
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
    std_r = math.sqrt(variance)
    if std_r < 1e-10:
        return 0.5
    t_stat = mean_r / std_r * math.sqrt(n)
    score = max(0.0, min(1.0, 0.5 + t_stat / 10.0))
    return round(score, 4)


def get_daily_persistence_score(symbol: str) -> float:
    """코인의 일봉 기반 추세 지속성 점수 [0.0, 1.0].

    Args:
        symbol: 'KRW-BTC' 형태의 Upbit 심볼

    Returns:
        0.0~1.0 사이 점수.
        0.55+ = 상승 추세 지속 (TRENDING long 진입 우호)
        0.45~0.55 = 중립
        0.45- = 하락 추세 (long 진입 비우호)
        0.5 = 데이터 없음 또는 오류 (중립 처리)
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _score_cache.get(symbol)
        if cached is not None:
            score, loaded_at = cached
            if now - loaded_at < _CACHE_TTL_SEC:
                return score

    # 캐시 미스 → API 호출 (hot path에서 처음 조회 시에만 발생)
    closes = _fetch_daily_closes(symbol)
    score = _t_stat_persistence(closes) if closes else _DEFAULT_SCORE

    with _cache_lock:
        _score_cache[symbol] = (score, now)

    return score


def get_daily_persistence_score_cached_only(symbol: str) -> float:
    """캐시된 점수만 반환 — 네트워크 호출 없이 hot path에서 안전하게 사용.

    캐시 미스 시 중립값(0.5) 반환.
    background_warm_cache() 로 사전 워밍 필요.
    """
    with _cache_lock:
        cached = _score_cache.get(symbol)
        if cached is not None:
            score, _ = cached
            return score
    return _DEFAULT_SCORE


def background_warm_cache(symbols: list[str]) -> None:
    """백그라운드 스레드에서 여러 심볼의 캐시를 사전 워밍.

    호출 방법 (사이클 루프 또는 캔디데이트 갱신 시):
        threading.Thread(
            target=daily_persistence.background_warm_cache,
            args=[candidate_symbols],
            daemon=True
        ).start()
    """
    now = time.monotonic()
    for symbol in symbols:
        with _cache_lock:
            cached = _score_cache.get(symbol)
            if cached is not None:
                _, loaded_at = cached
                if now - loaded_at < _CACHE_TTL_SEC:
                    continue   # 캐시 유효, 스킵
        # 캐시 만료 또는 없음 → 갱신
        closes = _fetch_daily_closes(symbol)
        score = _t_stat_persistence(closes) if closes else _DEFAULT_SCORE
        with _cache_lock:
            _score_cache[symbol] = (score, time.monotonic())

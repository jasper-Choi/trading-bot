"""ensemble_signal.py — Best3 앙상블 신호 합산 (best3_strategies_for_crypto.md 기반)

3개 전략 소프트맥스 정규화 가중 평균:
  ml_strategy        weight=0.20 → 정규화 후 ≈ 38.5%
  narrative_momentum weight=0.18 → 정규화 후 ≈ 34.6%
  persistence_cnn    weight=0.14 → 정규화 후 ≈ 26.9%

BUY  threshold: confidence >= 0.58
SELL threshold: confidence <= 0.40

캐시: symbol → (confidence, ts), TTL 15분 (가장 짧은 ML 캐시 기준)

hot_path_guard 연동:
  get_ensemble_signal_cached_only(symbol) — 네트워크 없이 hot path 사용
  background_warm_cache(symbols)          — 캐시 사전 워밍
"""

from __future__ import annotations

import math
import time
import threading
from typing import Optional

from app.services.ml_strategy import (
    get_ml_signal_cached_only as _ml_cached,
    get_ml_signal as _ml_fetch,
)
from app.services.narrative_momentum import (
    get_narrative_signal_cached_only as _narr_cached,
    get_narrative_signal as _narr_fetch,
)
from app.services.persistence_cnn_model import (
    get_persistence_signal_cached_only as _persist_cached,
    get_persistence_signal as _persist_fetch,
)

# ── 가중치 ──────────────────────────────────────────────────────────────────────
_RAW_WEIGHTS = {
    "ml":        0.20,
    "narrative": 0.18,
    "persistence": 0.14,
}

def _softmax_weights() -> dict[str, float]:
    """소프트맥스 정규화 가중치 계산."""
    exp = {k: math.exp(v) for k, v in _RAW_WEIGHTS.items()}
    total = sum(exp.values())
    return {k: v / total for k, v in exp.items()}

_WEIGHTS = _softmax_weights()

# BUY / SELL 임계값
BUY_THRESHOLD  = 0.58
SELL_THRESHOLD = 0.40

# ── 캐시 ──────────────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_ensemble_cache: dict[str, tuple[float, float]] = {}  # symbol → (confidence, ts)
_CACHE_TTL_SEC = 900.0   # 15분 (ML 캐시 TTL 기준)
_DEFAULT_CONFIDENCE = 0.5


def _weighted_avg(ml: float, narr: float, persist: float) -> float:
    """소프트맥스 가중 평균."""
    return round(
        _WEIGHTS["ml"] * ml +
        _WEIGHTS["narrative"] * narr +
        _WEIGHTS["persistence"] * persist,
        4,
    )


# ── 메인 API ───────────────────────────────────────────────────────────────────
def get_ensemble_signal(symbol: str) -> float:
    """3개 전략 앙상블 신호 [0.0, 1.0] — 네트워크 호출 포함.

    Returns:
        >= 0.58 → BUY (진입 우호)
        <= 0.40 → SELL / 진입 차단
        ~0.50   → 중립
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _ensemble_cache.get(symbol)
        if cached and now - cached[1] < _CACHE_TTL_SEC:
            return cached[0]

    ml      = _ml_fetch(symbol)
    narr    = _narr_fetch(symbol)
    persist = _persist_fetch(symbol)

    confidence = _weighted_avg(ml, narr, persist)
    with _cache_lock:
        _ensemble_cache[symbol] = (confidence, now)

    return confidence


def get_ensemble_signal_cached_only(symbol: str) -> float:
    """캐시된 앙상블 신호만 반환 — 네트워크 호출 없음 (hot path 안전).

    각 개별 캐시에서 읽어 합산.
    캐시 미스 시 0.5 반환 (background_warm_cache 사전 워밍 필요).
    """
    ml      = _ml_cached(symbol)
    narr    = _narr_cached(symbol)
    persist = _persist_cached(symbol)
    return _weighted_avg(ml, narr, persist)


def is_buy_signal(symbol: str, use_cache: bool = True) -> bool:
    """앙상블 BUY 신호 여부 (confidence >= 0.58)."""
    sig = get_ensemble_signal_cached_only(symbol) if use_cache else get_ensemble_signal(symbol)
    return sig >= BUY_THRESHOLD


def is_sell_signal(symbol: str, use_cache: bool = True) -> bool:
    """앙상블 SELL / 진입 차단 신호 여부 (confidence <= 0.40)."""
    sig = get_ensemble_signal_cached_only(symbol) if use_cache else get_ensemble_signal(symbol)
    return sig <= SELL_THRESHOLD


def get_signal_breakdown(symbol: str) -> dict:
    """개별 전략 신호 + 앙상블 신호 상세 반환 (디버깅용)."""
    ml      = _ml_cached(symbol)
    narr    = _narr_cached(symbol)
    persist = _persist_cached(symbol)
    ensemble = _weighted_avg(ml, narr, persist)
    return {
        "symbol":      symbol,
        "ml":          ml,
        "narrative":   narr,
        "persistence": persist,
        "ensemble":    ensemble,
        "weights":     _WEIGHTS,
        "buy":         ensemble >= BUY_THRESHOLD,
        "sell":        ensemble <= SELL_THRESHOLD,
    }


def background_warm_cache(symbols: list[str]) -> None:
    """모든 심볼의 3개 전략 캐시 동시 워밍.

    호출 방법:
        threading.Thread(
            target=ensemble_signal.background_warm_cache,
            args=[candidate_symbols],
            daemon=True
        ).start()
    """
    import threading as _t

    def _warm_one(symbol: str):
        now = time.monotonic()
        with _cache_lock:
            cached = _ensemble_cache.get(symbol)
            if cached and now - cached[1] < _CACHE_TTL_SEC:
                return
        # 3개 병렬로 워밍
        results = {}
        barrier = _t.Barrier(3)

        def _do_ml():
            results["ml"] = _ml_fetch(symbol)
            barrier.wait()

        def _do_narr():
            results["narr"] = _narr_fetch(symbol)
            barrier.wait()

        def _do_persist():
            results["persist"] = _persist_fetch(symbol)
            barrier.wait()

        for fn in (_do_ml, _do_narr, _do_persist):
            _t.Thread(target=fn, daemon=True).start()

        barrier.wait(timeout=12.0)
        ml      = results.get("ml", 0.5)
        narr    = results.get("narr", 0.5)
        persist = results.get("persist", 0.5)
        confidence = _weighted_avg(ml, narr, persist)
        with _cache_lock:
            _ensemble_cache[symbol] = (confidence, time.monotonic())

    for s in symbols:
        _t.Thread(target=_warm_one, args=[s], daemon=True).start()

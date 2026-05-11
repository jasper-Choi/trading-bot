"""ml_strategy.py — MLStrategy LightGBM 코인 신호 (best3_strategies_for_crypto.md 기반)

LightGBM 이진 분류: HORIZON=3일 후 수익 > 0% → 1 (코인판 HORIZON=3)

피처 (10개):
  rsi_14, macd_hist, bb_pos, atr_ratio, vol_ratio_20,
  mom_5d, mom_20d, mom_60d, foreign_5d=0, institution_5d=0

모델 없을 때 규칙 기반 폴백:
  bar_return 기반 모멘텀 (foreign/institution=0 고정)
  signal = clip(0.5 + raw/6.0, 0.0, 1.0)

캐시: symbol → (signal, ts), TTL 15분
모델 경로: models/lgbm_model.pkl
"""

from __future__ import annotations

import math
import os
import time
import threading
import pickle
from typing import Optional

import requests

# ── 경로 ──────────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODEL_PATH = os.path.join(_BASE_DIR, "models", "lgbm_model.pkl")

# ── 캐시 ──────────────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_signal_cache: dict[str, tuple[float, float]] = {}  # symbol → (signal, ts)
_CACHE_TTL_SEC = 900.0   # 15분
_DEFAULT_SIGNAL = 0.5
_REQUEST_TIMEOUT = 5.0
_UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/days"

# ── 모델 싱글톤 ────────────────────────────────────────────────────────────────
_model_lock = threading.Lock()
_model = None           # LightGBM Booster or None
_model_loaded = False   # 로드 시도 완료 여부


def _load_model():
    """models/lgbm_model.pkl 로드 (없으면 None 유지)."""
    global _model, _model_loaded
    with _model_lock:
        if _model_loaded:
            return
        _model_loaded = True
        if not os.path.exists(_MODEL_PATH):
            return
        try:
            with open(_MODEL_PATH, "rb") as f:
                _model = pickle.load(f)
        except Exception:
            _model = None


# ── OHLCV 데이터 수집 ──────────────────────────────────────────────────────────
def _fetch_daily_ohlcv(symbol: str, count: int = 80) -> list[dict]:
    """Upbit 일봉 OHLCV (최신→오래된 순 → 역정렬 반환)."""
    try:
        resp = requests.get(
            _UPBIT_CANDLES_URL,
            params={"market": symbol, "count": count},
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list) or len(data) < 20:
            return []
        return list(reversed(data))  # 오래된 것 먼저
    except Exception:
        return []


# ── 피처 계산 ──────────────────────────────────────────────────────────────────
def _ema(values: list[float], span: int) -> list[float]:
    """지수이동평균 (EWM)."""
    k = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    """RSI(period)."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    # EWM 방식
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 4)


def _compute_features(bars: list[dict]) -> Optional[list[float]]:
    """10개 피처 벡터 계산. 데이터 부족 시 None."""
    if len(bars) < 65:
        return None

    closes  = [float(b["trade_price"]) for b in bars]
    opens   = [float(b["opening_price"]) for b in bars]
    highs   = [float(b["high_price"]) for b in bars]
    lows    = [float(b["low_price"]) for b in bars]
    volumes = [float(b["candle_acc_trade_volume"]) for b in bars]

    n = len(closes)

    # rsi_14
    rsi_14 = _rsi(closes, 14)

    # macd_hist: EMA12 - EMA26, signal=EMA9(diff)
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    macd_signal = _ema(macd_line, 9)
    macd_hist = macd_line[-1] - macd_signal[-1]
    # close 기준 정규화
    macd_hist_norm = macd_hist / closes[-1] if closes[-1] > 0 else 0.0

    # bb_pos: BB(20, 2σ) 내 상대 위치 ∈ [-1, 1]
    bb_window = closes[-20:]
    bb_mid = sum(bb_window) / 20
    bb_var = sum((c - bb_mid) ** 2 for c in bb_window) / 20
    bb_std = math.sqrt(bb_var) if bb_var > 0 else 1e-8
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_range = bb_upper - bb_lower
    bb_pos = (closes[-1] - bb_mid) / (bb_range / 2) if bb_range > 1e-10 else 0.0
    bb_pos = max(-1.0, min(1.0, bb_pos))

    # atr_ratio: ATR(14) / close
    trs = []
    for i in range(max(1, n - 14), n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr = sum(trs) / len(trs) if trs else 0.0
    atr_ratio = atr / closes[-1] if closes[-1] > 0 else 0.0

    # vol_ratio_20: 최근 거래량 / 20일 평균
    vol_avg20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1.0
    vol_ratio_20 = volumes[-1] / vol_avg20 if vol_avg20 > 0 else 1.0

    # mom_5d, mom_20d, mom_60d
    def mom(n_days: int) -> float:
        if len(closes) > n_days:
            base = closes[-(n_days + 1)]
            return (closes[-1] - base) / base if base > 0 else 0.0
        return 0.0

    mom_5d  = mom(5)
    mom_20d = mom(20)
    mom_60d = mom(60)

    # foreign/institution = 0 (코인은 해당 데이터 없음)
    foreign_5d = 0.0
    institution_5d = 0.0

    return [
        rsi_14,
        macd_hist_norm,
        bb_pos,
        atr_ratio,
        vol_ratio_20,
        mom_5d,
        mom_20d,
        mom_60d,
        foreign_5d,
        institution_5d,
    ]


# ── 규칙 기반 폴백 ──────────────────────────────────────────────────────────────
def _rule_based_signal(bars: list[dict]) -> float:
    """모델 없을 때 bar_return 기반 모멘텀 신호."""
    if not bars:
        return _DEFAULT_SIGNAL
    last = bars[-1]
    open_p = float(last.get("opening_price", 0) or 0)
    close_p = float(last.get("trade_price", 0) or 0)
    if open_p <= 0:
        return _DEFAULT_SIGNAL
    bar_return = (close_p - open_p) / open_p
    raw = 1.0 * math.tanh(bar_return * 20)
    signal = max(0.0, min(1.0, 0.5 + raw / 6.0))
    return round(signal, 4)


# ── 메인 API ───────────────────────────────────────────────────────────────────
def get_ml_signal(symbol: str) -> float:
    """ML 전략 신호 [0.0, 1.0].

    Returns:
        0.58+ → BUY (상승 확률 높음)
        0.40- → SELL (하락 확률 높음)
        ~0.50 → 중립
        0.5   → 캐시 미스 또는 오류
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _signal_cache.get(symbol)
        if cached and now - cached[1] < _CACHE_TTL_SEC:
            return cached[0]

    bars = _fetch_daily_ohlcv(symbol, count=80)
    _load_model()

    signal = _DEFAULT_SIGNAL
    if bars:
        features = _compute_features(bars)
        if features is not None and _model is not None:
            try:
                import numpy as np
                feat_arr = np.array([features], dtype=np.float32)
                prob = float(_model.predict(feat_arr)[0])
                signal = max(0.0, min(1.0, prob))
            except Exception:
                signal = _rule_based_signal(bars)
        else:
            signal = _rule_based_signal(bars)

    with _cache_lock:
        _signal_cache[symbol] = (signal, now)

    return round(signal, 4)


def get_ml_signal_cached_only(symbol: str) -> float:
    """캐시된 ML 신호만 반환. 네트워크 호출 없음. 미스 시 0.5."""
    with _cache_lock:
        cached = _signal_cache.get(symbol)
        if cached:
            return cached[0]
    return _DEFAULT_SIGNAL


def background_warm_cache(symbols: list[str]) -> None:
    """백그라운드에서 여러 심볼 ML 신호 캐시 워밍."""
    now = time.monotonic()
    for symbol in symbols:
        with _cache_lock:
            cached = _signal_cache.get(symbol)
            if cached and now - cached[1] < _CACHE_TTL_SEC:
                continue
        get_ml_signal(symbol)  # 내부적으로 캐시 갱신

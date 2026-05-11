"""persistence_cnn_model.py — PersistenceCNN PyTorch 추론 + 폴백

아키텍처 (best3_strategies_for_crypto.md):
  입력:  (batch, 5, 20) — [norm_open, norm_high, norm_low, norm_close, norm_volume]
  Conv1d(5→16, k=3, p=1) → ReLU
  Conv1d(16→32, k=3, p=1) → ReLU
  AdaptiveAvgPool1d(1) → (B, 32, 1) → squeeze
  Linear(32→1) → Sigmoid

모델 없을 때: daily_persistence.py 의 t-통계량 폴백 사용
모델 경로: models/persistence_cnn.pt

캐시: symbol → (signal, ts), TTL 4시간
"""

from __future__ import annotations

import os
import time
import threading
import math
from typing import Optional

import requests

# ── 경로 ──────────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODEL_PATH = os.path.join(_BASE_DIR, "models", "persistence_cnn.pt")

# ── 캐시 ──────────────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_signal_cache: dict[str, tuple[float, float]] = {}  # symbol → (signal, ts)
_CACHE_TTL_SEC = 14400.0   # 4시간
_DEFAULT_SIGNAL = 0.5
_REQUEST_TIMEOUT = 4.0
_UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/days"
_WIN = 20      # 입력 윈도우 (일봉)
_N_FEAT = 5    # O, H, L, C, V

# ── PyTorch 모델 싱글톤 ────────────────────────────────────────────────────────
_model_lock = threading.Lock()
_model = None
_model_loaded = False


def _define_model():
    """Conv1d CNN 아키텍처 정의 (torch 있을 때만)."""
    import torch
    import torch.nn as nn

    class PersistenceCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1 = nn.Conv1d(in_channels=5,  out_channels=16, kernel_size=3, padding=1)
            self.conv2 = nn.Conv1d(in_channels=16, out_channels=32, kernel_size=3, padding=1)
            self.pool  = nn.AdaptiveAvgPool1d(1)
            self.fc    = nn.Linear(32, 1)
            self.relu  = nn.ReLU()
            self.sigmoid = nn.Sigmoid()

        def forward(self, x):
            x = self.relu(self.conv1(x))   # (B, 16, 20)
            x = self.relu(self.conv2(x))   # (B, 32, 20)
            x = self.pool(x).squeeze(-1)   # (B, 32)
            return self.sigmoid(self.fc(x)).squeeze(-1)  # (B,)

    return PersistenceCNN


def _load_model():
    """models/persistence_cnn.pt 로드."""
    global _model, _model_loaded
    with _model_lock:
        if _model_loaded:
            return
        _model_loaded = True
        if not os.path.exists(_MODEL_PATH):
            return
        try:
            import torch
            torch.set_num_threads(1)  # fork 환경 deadlock 방지
            ModelClass = _define_model()
            net = ModelClass()
            state = torch.load(_MODEL_PATH, map_location="cpu")
            net.load_state_dict(state)
            net.eval()
            _model = net
        except Exception:
            _model = None


# ── OHLCV 수집 ────────────────────────────────────────────────────────────────
def _fetch_daily_ohlcv(symbol: str) -> list[dict]:
    try:
        resp = requests.get(
            _UPBIT_CANDLES_URL,
            params={"market": symbol, "count": _WIN + 2},
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list) or len(data) < _WIN:
            return []
        return list(reversed(data))
    except Exception:
        return []


# ── 정규화 ────────────────────────────────────────────────────────────────────
def _normalize_window(bars: list[dict]) -> Optional[list[list[float]]]:
    """최근 WIN개 봉 → (5, WIN) 텐서용 리스트.

    정규화:
      norm_close/open/high/low: (x - c_min) / (c_max - c_min + ε)
      norm_volume: v / (max_volume + ε)
    """
    if len(bars) < _WIN:
        return None
    recent = bars[-_WIN:]
    closes  = [float(b["trade_price"])              for b in recent]
    opens   = [float(b["opening_price"])            for b in recent]
    highs   = [float(b["high_price"])               for b in recent]
    lows    = [float(b["low_price"])                for b in recent]
    volumes = [float(b["candle_acc_trade_volume"])  for b in recent]

    c_min, c_max = min(closes), max(closes)
    c_range = max(c_max - c_min, 1e-8)
    max_vol = max(volumes) if volumes else 1.0
    max_vol = max(max_vol, 1e-8)

    norm_close  = [(c - c_min) / c_range for c in closes]
    norm_open   = [(o - c_min) / c_range for o in opens]
    norm_high   = [(h - c_min) / c_range for h in highs]
    norm_low    = [(l - c_min) / c_range for l in lows]
    norm_volume = [v / max_vol for v in volumes]

    # (5, WIN) = [open, high, low, close, volume]
    return [norm_open, norm_high, norm_low, norm_close, norm_volume]


# ── t-통계량 폴백 (daily_persistence.py 와 동일 공식) ─────────────────────────
def _t_stat_fallback(bars: list[dict]) -> float:
    if len(bars) < 5:
        return _DEFAULT_SIGNAL
    closes = [float(b["trade_price"]) for b in bars[-21:]]
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    n = len(returns)
    if n < 2:
        return _DEFAULT_SIGNAL
    mean_r = sum(returns) / n
    var = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
    std_r = math.sqrt(var)
    if std_r < 1e-10:
        return 0.5
    t_stat = mean_r / std_r * math.sqrt(n)
    return round(max(0.0, min(1.0, 0.5 + t_stat / 10.0)), 4)


# ── CNN 추론 ──────────────────────────────────────────────────────────────────
def _cnn_infer(channels: list[list[float]]) -> float:
    """(5, WIN) 텐서 → 추세 지속 확률 [0, 1]."""
    try:
        import torch
        torch.set_num_threads(1)
        x = torch.tensor([channels], dtype=torch.float32)  # (1, 5, WIN)
        with torch.no_grad():
            prob = float(_model(x).item())
        return max(0.0, min(1.0, prob))
    except Exception:
        return _DEFAULT_SIGNAL


# ── 메인 API ───────────────────────────────────────────────────────────────────
def get_persistence_signal(symbol: str) -> float:
    """PersistenceCNN 신호 [0.0, 1.0].

    CNN 모델 있으면 → CNN 추론
    없으면 → t-통계량 폴백 (daily_persistence.py 동일 공식)

    Returns:
        0.55+ → 상승 추세 지속 중
        ~0.50 → 중립
        0.45- → 하락 추세 지속
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _signal_cache.get(symbol)
        if cached and now - cached[1] < _CACHE_TTL_SEC:
            return cached[0]

    bars = _fetch_daily_ohlcv(symbol)
    _load_model()

    if bars:
        if _model is not None:
            channels = _normalize_window(bars)
            if channels is not None:
                signal = _cnn_infer(channels)
            else:
                signal = _t_stat_fallback(bars)
        else:
            signal = _t_stat_fallback(bars)
    else:
        signal = _DEFAULT_SIGNAL

    with _cache_lock:
        _signal_cache[symbol] = (signal, now)

    return round(signal, 4)


def get_persistence_signal_cached_only(symbol: str) -> float:
    """캐시된 신호만 반환. 미스 시 0.5."""
    with _cache_lock:
        cached = _signal_cache.get(symbol)
        if cached:
            return cached[0]
    return _DEFAULT_SIGNAL


def background_warm_cache(symbols: list[str]) -> None:
    """백그라운드 캐시 워밍."""
    now = time.monotonic()
    for symbol in symbols:
        with _cache_lock:
            cached = _signal_cache.get(symbol)
            if cached and now - cached[1] < _CACHE_TTL_SEC:
                continue
        get_persistence_signal(symbol)

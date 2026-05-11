"""narrative_momentum.py — NarrativeMomentum 코인 버전 (best3_strategies_for_crypto.md 기반)

KRX 원본: KR-FinBert + DART 공시 + PER/PBR/배당
코인 대체:
  Narrative(50%): Fear & Greed Index (alternative.me)
                  + CoinGecko 마켓 센티먼트 (sentiment_votes_up_percentage)
  Value(50%):     NVT 프록시 (price / 30일 평균 거래량 비율)
                  + MVRV 프록시 (현재가 / 30일 평균가 비율 역수)

캐시: symbol → (signal, ts), TTL 4시간 (일봉 데이터 기반)
글로벌 캐시: Fear & Greed TTL 1시간 (전 코인 공유)
"""

from __future__ import annotations

import math
import time
import threading
from typing import Optional

import requests

# ── 공통 설정 ──────────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_signal_cache: dict[str, tuple[float, float]] = {}  # symbol → (signal, ts)
_CACHE_TTL_SEC = 14400.0   # 4시간
_REQUEST_TIMEOUT = 4.0
_DEFAULT_SIGNAL = 0.5

# Fear & Greed 글로벌 캐시 (모든 심볼 공유)
_fg_lock = threading.Lock()
_fg_cache: tuple[float, float] = (0.5, 0.0)   # (score, ts)
_FG_TTL_SEC = 3600.0   # 1시간

# CoinGecko 코인ID 매핑 (Upbit 심볼 → CoinGecko ID)
_SYMBOL_TO_CG_ID: dict[str, str] = {
    "KRW-BTC":   "bitcoin",
    "KRW-ETH":   "ethereum",
    "KRW-SOL":   "solana",
    "KRW-XRP":   "ripple",
    "KRW-ADA":   "cardano",
    "KRW-DOGE":  "dogecoin",
    "KRW-AVAX":  "avalanche-2",
    "KRW-DOT":   "polkadot",
    "KRW-LINK":  "chainlink",
    "KRW-MATIC": "matic-network",
    "KRW-ATOM":  "cosmos",
    "KRW-UNI":   "uniswap",
    "KRW-LTC":   "litecoin",
    "KRW-BCH":   "bitcoin-cash",
    "KRW-ETC":   "ethereum-classic",
    "KRW-TRX":   "tron",
    "KRW-XLM":   "stellar",
    "KRW-EOS":   "eos",
    "KRW-NEAR":  "near",
    "KRW-APT":   "aptos",
    "KRW-OP":    "optimism",
    "KRW-ARB":   "arbitrum",
    "KRW-SUI":   "sui",
    "KRW-SEI":   "sei-network",
    "KRW-INJ":   "injective-protocol",
    "KRW-SAND":  "the-sandbox",
    "KRW-MANA":  "decentraland",
    "KRW-AXS":   "axie-infinity",
    "KRW-IMX":   "immutable-x",
    "KRW-FTM":   "fantom",
}

_UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/days"


# ── Fear & Greed Index ──────────────────────────────────────────────────────────
def _fetch_fear_greed() -> float:
    """alternative.me Fear & Greed Index → [0, 1] 정규화.

    0 = Extreme Fear → 하락 확률 ↑
    1 = Extreme Greed → 상승 확률 ↑
    """
    global _fg_cache
    now = time.monotonic()
    with _fg_lock:
        score, ts = _fg_cache
        if now - ts < _FG_TTL_SEC:
            return score
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            val = int(resp.json()["data"][0]["value"])  # 0-100
            score = round(val / 100.0, 4)
        else:
            score = 0.5
    except Exception:
        score = 0.5
    with _fg_lock:
        _fg_cache = (score, time.monotonic())
    return score


def _fetch_fear_greed_ema() -> float:
    """최근 7일 Fear & Greed EMA로 모멘텀 방향 판단.

    EMA 상승 중 → narrative_signal > 0.5 (상승 모멘텀)
    EMA 하락 중 → narrative_signal < 0.5 (하락 모멘텀)
    """
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=7",
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return _fetch_fear_greed()
        data = resp.json()["data"]
        values = [int(d["value"]) / 100.0 for d in reversed(data)]
        if len(values) < 2:
            return values[0] if values else 0.5

        # EMA(7) 스팬
        k = 2.0 / (7 + 1)
        ema_now = values[0]
        for v in values[1:]:
            ema_now = v * k + ema_now * (1 - k)

        # EMA(7) 1일 전 (baseline)
        ema_prev = values[0]
        for v in values[1:-1]:
            ema_prev = v * k + ema_prev * (1 - k)

        current_fg = values[-1]
        momentum = current_fg - ema_prev  # -1 ~ +1 범위
        narrative = max(0.0, min(1.0, 0.5 + momentum))
        return round(narrative, 4)
    except Exception:
        return _fetch_fear_greed()


# ── CoinGecko 센티먼트 ──────────────────────────────────────────────────────────
def _fetch_coingecko_sentiment(cg_id: str) -> float:
    """CoinGecko sentiment_votes_up_percentage → [0, 1].

    votes_up% = 70 → 0.70
    없으면 0.5 반환.
    """
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization": "false", "tickers": "false",
                    "market_data": "false", "community_data": "false",
                    "developer_data": "false"},
            timeout=_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return 0.5
        data = resp.json()
        up_pct = data.get("sentiment_votes_up_percentage")
        if up_pct is None:
            return 0.5
        return round(float(up_pct) / 100.0, 4)
    except Exception:
        return 0.5


# ── 내러티브 신호 합산 (Fear&Greed 60% + CoinGecko 40%) ──────────────────────────
def _narrative_signal(symbol: str) -> float:
    """Fear&Greed EMA 모멘텀(60%) + CoinGecko 감성(40%)."""
    fg = _fetch_fear_greed_ema()
    cg_id = _SYMBOL_TO_CG_ID.get(symbol, "")
    if cg_id:
        cg_sent = _fetch_coingecko_sentiment(cg_id)
    else:
        cg_sent = 0.5
    return round(0.60 * fg + 0.40 * cg_sent, 4)


# ── 가치 신호 (NVT 프록시 + MVRV 프록시) ─────────────────────────────────────────
def _fetch_upbit_daily(symbol: str, count: int = 35) -> list[dict]:
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
        return list(reversed(data)) if isinstance(data, list) else []
    except Exception:
        return []


def _value_signal(bars: list[dict]) -> float:
    """NVT 프록시(50%) + MVRV 프록시(50%) 합산 가치 점수.

    NVT 프록시:
      avg_vol_30d / current_vol  (거래량 대비 현재 시총 상대적 저평가)
      current_vol > avg → 거래 활발 → NVT 낮음 → 저평가 → score ↑

    MVRV 프록시:
      avg_price_30d / current_price
      현재가 < 30d 평균 → MVRV<1 → 저평가 → score ↑
    """
    if len(bars) < 20:
        return 0.5

    closes  = [float(b["trade_price"]) for b in bars]
    volumes = [float(b["candle_acc_trade_volume"]) for b in bars]

    # NVT 프록시
    avg_vol = sum(volumes[-30:]) / min(30, len(volumes))
    cur_vol = volumes[-1] if volumes[-1] > 0 else avg_vol
    nvt_ratio = avg_vol / cur_vol if cur_vol > 0 else 1.0
    # nvt_ratio < 1 → 거래 활발 (저평가), > 1 → 거래 부진 (고평가)
    # clip to [25/nvt*0.5] 형태 → NVT=1 → 0.5, NVT=0.5 → 1.0, NVT=2.0 → 0.25
    nvt_score = max(0.0, min(1.0, (1.0 / nvt_ratio) * 0.5))

    # MVRV 프록시
    avg_price_30d = sum(closes[-30:]) / min(30, len(closes))
    cur_price = closes[-1]
    mvrv = cur_price / avg_price_30d if avg_price_30d > 0 else 1.0
    # mvrv < 1 → 저평가(score ↑), mvrv > 1 → 고평가(score ↓)
    mvrv_score = max(0.0, min(1.0, (1.0 / mvrv) * 0.5))

    return round(0.50 * nvt_score + 0.50 * mvrv_score, 4)


# ── 메인 API ───────────────────────────────────────────────────────────────────
def get_narrative_signal(symbol: str) -> float:
    """NarrativeMomentum 신호 [0.0, 1.0].

    narrative(50%) + value(50%) 합산.

    Returns:
        0.58+ → BUY 우호 (감성 + 가치 모두 상승)
        ~0.50 → 중립
        0.40- → 약세
        0.5   → 오류/캐시 미스
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _signal_cache.get(symbol)
        if cached and now - cached[1] < _CACHE_TTL_SEC:
            return cached[0]

    bars = _fetch_upbit_daily(symbol, count=35)
    narr = _narrative_signal(symbol)
    val  = _value_signal(bars)

    # md 가이드: narrative 50%, value 50% (코인 버전)
    signal = round(0.50 * narr + 0.50 * val, 4)

    with _cache_lock:
        _signal_cache[symbol] = (signal, now)

    return signal


def get_narrative_signal_cached_only(symbol: str) -> float:
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
        get_narrative_signal(symbol)

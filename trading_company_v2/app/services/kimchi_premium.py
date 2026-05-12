"""김치 프리미엄 — 업비트 vs 바이낸스 가격 차이.

get_kimchi_premium(upbit_krw_price, base_currency) → float (% 프리미엄)
get_usd_krw() → float (달러/원 환율)

양수 = 국내 프리미엄 (업비트가 더 비쌈 → 국내 수요 강세)
음수 = 역프리미엄 (업비트가 더 쌈 → 국내 매수 신중)
"""
from __future__ import annotations

import logging
import threading
import time

import requests

_log = logging.getLogger(__name__)

_BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"
# Dunamu(업비트 계열) 환율 API — KRW/USD 실시간
_FOREX_URL = "https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD"

_PREMIUM_TTL = 300.0    # 5분
_FOREX_TTL   = 3600.0   # 1시간

_lock = threading.Lock()
_premium_cache: dict[str, tuple[float, float]] = {}   # base → (ts, premium_pct)
_forex_cache: tuple[float, float] = (0.0, 0.0)        # (ts, usd_krw_rate)


def get_usd_krw() -> float:
    """달러/원 환율. 실패 시 fallback 1380."""
    global _forex_cache
    with _lock:
        ts, rate = _forex_cache
        if rate and time.monotonic() - ts < _FOREX_TTL:
            return rate
    try:
        resp = requests.get(_FOREX_URL, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        rate = float(data[0].get("basePrice", 0) or 0)
        if rate > 1000:   # sanity check (원/달러는 수백~수천 범위)
            with _lock:
                _forex_cache = (time.monotonic(), rate)
            return rate
    except Exception as exc:
        _log.debug("USD/KRW fetch failed: %s", exc)
    return 1380.0


def get_kimchi_premium(upbit_krw_price: float, base_currency: str) -> float:
    """
    김치 프리미엄 계산.

    upbit_krw_price : 업비트 현재 원화 가격
    base_currency   : "BTC", "ETH", "XRP" 등 (Binance USDT 쌍 기준)
    반환값          : % (양수 = 국내 프리미엄)
    """
    if upbit_krw_price <= 0:
        return 0.0
    key = base_currency.upper()

    with _lock:
        entry = _premium_cache.get(key)
        if entry and time.monotonic() - entry[0] < _PREMIUM_TTL:
            return entry[1]

    try:
        resp = requests.get(_BINANCE_PRICE_URL.format(symbol=key), timeout=6)
        resp.raise_for_status()
        binance_usd = float(resp.json().get("price", 0) or 0)
        if binance_usd <= 0:
            return 0.0
        usd_krw = get_usd_krw()
        binance_krw_equiv = binance_usd * usd_krw
        premium = round((upbit_krw_price - binance_krw_equiv) / binance_krw_equiv * 100, 2)
        with _lock:
            _premium_cache[key] = (time.monotonic(), premium)
        _log.debug("kimchi_premium %s: %.2f%%", key, premium)
        return premium
    except Exception as exc:
        _log.debug("kimchi_premium %s: %s", base_currency, exc)
        return 0.0


def get_kimchi_premium_score(premium_pct: float) -> float:
    """프리미엄을 0.0~1.0 점수로 변환.

    premium >= 5%  → 0.90 (국내 수요 폭발, 강세 신호)
    premium 2~5%   → 0.70
    premium 0~2%   → 0.55
    premium -2~0%  → 0.45
    premium < -2%  → 0.30 (역프리미엄, 주의)
    """
    if premium_pct >= 5.0:
        return 0.90
    if premium_pct >= 2.0:
        return 0.70
    if premium_pct >= 0.0:
        return 0.55
    if premium_pct >= -2.0:
        return 0.45
    return 0.30

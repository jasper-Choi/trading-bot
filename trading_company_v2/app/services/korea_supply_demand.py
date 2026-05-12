"""기관/외국인 수급 — Naver sise_invest 스크래핑.

get_institutional_tickers()      → KOSPI+KOSDAQ 기관 레이더 종목코드 집합 (1h TTL)
get_supply_demand_score(ticker)  → 0.0~1.0 수급 점수
"""
from __future__ import annotations

import logging
import re
import threading
import time

import requests

from app.services.market_gateway import NAVER_HEADERS, REQUEST_TIMEOUT

_log = logging.getLogger(__name__)

_SISE_INVEST_URL = "https://finance.naver.com/sise/sise_invest.naver?sosok={sosok}&page={page}"

_CACHE_TTL = 3600.0   # 1시간 (장중 완만히 변함)
_lock = threading.Lock()
_cache: dict[str, tuple[float, set[str]]] = {}   # market_key → (ts, ticker_set)


def _fetch_sise_invest_tickers(sosok: int, pages: int = 3) -> set[str]:
    """sise_invest 페이지에서 종목코드 추출.

    sise_invest는 기관/외국인/개인 순매수 상위 종목을 보여준다.
    페이지에 등장하는 모든 종목코드를 '기관 레이더 종목'으로 수집한다.
    """
    tickers: set[str] = set()
    for page in range(1, pages + 1):
        try:
            url = _SISE_INVEST_URL.format(sosok=sosok, page=page)
            resp = requests.get(url, headers=NAVER_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            html = resp.content.decode("euc-kr", errors="ignore")
            # 종목코드는 href="/item/main.naver?code=XXXXXX" 형태로 등장
            found = re.findall(r"code=(\d{6})", html)
            tickers.update(found)
        except Exception as exc:
            _log.debug("sise_invest sosok=%d page=%d: %s", sosok, page, exc)
    return tickers


def _get_cached(market_key: str, sosok: int) -> set[str]:
    with _lock:
        entry = _cache.get(market_key)
        if entry and time.monotonic() - entry[0] < _CACHE_TTL:
            return entry[1]
    tickers = _fetch_sise_invest_tickers(sosok)
    with _lock:
        _cache[market_key] = (time.monotonic(), tickers)
    _log.debug("supply_demand cache refreshed: %s → %d tickers", market_key, len(tickers))
    return tickers


def get_institutional_tickers() -> set[str]:
    """KOSPI + KOSDAQ 기관 레이더 종목코드 집합 (1시간 캐시)."""
    kospi = _get_cached("kospi", 0)
    kosdaq = _get_cached("kosdaq", 1)
    return kospi | kosdaq


def get_supply_demand_score(ticker: str, inst_tickers: set[str] | None = None) -> float:
    """수급 점수 0.0~1.0.

    기관 레이더에 있으면 0.78, 없으면 0.40 (수급 미확인 페널티).
    호출 시 inst_tickers를 넘기면 재사용(배치 처리 효율화).
    """
    if inst_tickers is None:
        inst_tickers = get_institutional_tickers()
    return 0.78 if ticker.strip() in inst_tickers else 0.40

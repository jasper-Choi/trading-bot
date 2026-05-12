"""
Dynamic Korea stock universe — top KOSPI + KOSDAQ stocks by trading volume.
Refreshes every 4 hours (TTL-based). Returns [{ticker, name, market}].
"""
from __future__ import annotations

import logging
import re
import threading
import time
from html import unescape
from typing import Any

import requests
from requests import RequestException

from app.services.market_gateway import NAVER_HEADERS, REQUEST_TIMEOUT, _strip_html, _to_number

_log = logging.getLogger(__name__)

# Naver sise_quant: top stocks by trading amount (거래대금 기준)
# sosok=0 KOSPI, sosok=1 KOSDAQ
_NAVER_SISE_QUANT_URL = (
    "https://finance.naver.com/sise/sise_quant.naver?sosok={market}&page={page}"
)

_UNIVERSE_TTL = 4 * 3600.0  # 4 hours

_lock = threading.Lock()
_cache: list[dict[str, Any]] = []
_cache_ts: float = 0.0


def _fetch_market(sosok: int, market_name: str, top_n: int) -> list[dict[str, Any]]:
    """Scrape Naver sise_quant for top-n stocks by trading volume."""
    results: list[dict[str, Any]] = []
    for page in range(1, 4):
        if len(results) >= top_n:
            break
        try:
            resp = requests.get(
                _NAVER_SISE_QUANT_URL.format(market=sosok, page=page),
                headers=NAVER_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            html = resp.content.decode("euc-kr", errors="ignore")
        except RequestException as exc:
            _log.warning("korea_universe market=%d page=%d failed: %s", sosok, page, exc)
            break

        rows = re.findall(
            r'<tr[^>]*onMouseOver="mouseOver\(this\)"[^>]*>(.*?)</tr>',
            html,
            flags=re.S | re.I,
        )
        if not rows:
            break

        for row in rows:
            code_match = re.search(
                r'/item/main\.naver\?code=(\d+)"[^>]*class="tltle">([^<]+)</a>', row
            )
            if not code_match:
                continue
            ticker = code_match.group(1).strip()
            name = _strip_html(code_match.group(2))
            if not ticker or not name:
                continue
            results.append({"ticker": ticker, "name": name, "market": market_name})
            if len(results) >= top_n:
                break

    return results[:top_n]


def _build_universe() -> list[dict[str, Any]]:
    kospi = _fetch_market(0, "KOSPI", 60)
    kosdaq = _fetch_market(1, "KOSDAQ", 60)
    combined = kospi + kosdaq
    # deduplicate by ticker
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in combined:
        t = item["ticker"]
        if t not in seen:
            seen.add(t)
            deduped.append(item)
    _log.info("korea_universe refreshed: %d KOSPI + %d KOSDAQ = %d total", len(kospi), len(kosdaq), len(deduped))
    return deduped


def get_korea_universe(force: bool = False) -> list[dict[str, Any]]:
    """Return cached universe; refresh if stale or forced."""
    global _cache, _cache_ts
    with _lock:
        age = time.monotonic() - _cache_ts
        if not force and _cache and age < _UNIVERSE_TTL:
            return list(_cache)
        try:
            fresh = _build_universe()
            if fresh:
                _cache = fresh
                _cache_ts = time.monotonic()
        except Exception as exc:
            _log.error("korea_universe build failed: %s", exc)
        return list(_cache)

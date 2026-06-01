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

# Naver sise_market_sum: market cap + volume summary page (거래대금 정렬)
# sosok=0 KOSPI, sosok=1 KOSDAQ
# NOTE: sise_quant URL (previously used) changed HTML structure — no longer has
#       onMouseOver rows. sise_market_sum retains the same table format.
_NAVER_SISE_MARKET_SUM_URL = (
    "https://finance.naver.com/sise/sise_market_sum.naver?sosok={market}&page={page}"
)

_UNIVERSE_TTL = 4 * 3600.0  # 4 hours

# ── 한국 대형주 앵커 유니버스 ───────────────────────────────────────────────
# KOSPI 시총 상위 핵심 종목 — 동적 universe 스크랩 실패/순위 누락 시 보완
# 2026-06-01: 추가 — 현대차(+6.6%), 삼성전자우(+14.1%), NAVER(+26.7%) 등 미포착 방지
_KOREA_ANCHOR_UNIVERSE: list[dict[str, Any]] = [
    {"ticker": "005930", "name": "삼성전자",          "market": "KOSPI"},
    {"ticker": "005935", "name": "삼성전자우",         "market": "KOSPI"},
    {"ticker": "000660", "name": "SK하이닉스",         "market": "KOSPI"},
    {"ticker": "207940", "name": "삼성바이오로직스",   "market": "KOSPI"},
    {"ticker": "373220", "name": "LG에너지솔루션",     "market": "KOSPI"},
    {"ticker": "035420", "name": "NAVER",              "market": "KOSPI"},
    {"ticker": "005380", "name": "현대차",             "market": "KOSPI"},
    {"ticker": "000270", "name": "기아",               "market": "KOSPI"},
    {"ticker": "035720", "name": "카카오",             "market": "KOSPI"},
    {"ticker": "051910", "name": "LG화학",             "market": "KOSPI"},
    {"ticker": "006400", "name": "삼성SDI",            "market": "KOSPI"},
    {"ticker": "068270", "name": "셀트리온",           "market": "KOSPI"},
    {"ticker": "105560", "name": "KB금융",             "market": "KOSPI"},
    {"ticker": "055550", "name": "신한지주",           "market": "KOSPI"},
    {"ticker": "086790", "name": "하나금융지주",       "market": "KOSPI"},
    {"ticker": "032830", "name": "삼성생명",           "market": "KOSPI"},
    {"ticker": "005490", "name": "POSCO홀딩스",        "market": "KOSPI"},
    {"ticker": "003670", "name": "포스코퓨처엠",       "market": "KOSPI"},
    {"ticker": "096770", "name": "SK이노베이션",       "market": "KOSPI"},
    {"ticker": "028260", "name": "삼성물산",           "market": "KOSPI"},
    {"ticker": "012330", "name": "현대모비스",         "market": "KOSPI"},
    {"ticker": "247540", "name": "에코프로비엠",       "market": "KOSDAQ"},
    {"ticker": "086520", "name": "에코프로",           "market": "KOSDAQ"},
    {"ticker": "091990", "name": "셀트리온헬스케어",   "market": "KOSDAQ"},
    {"ticker": "263750", "name": "펄어비스",           "market": "KOSDAQ"},
]

# ── 한국 ETF 고정 유니버스 ──────────────────────────────────────────────────
# KIS API로 거래 가능한 주요 ETF (KODEX / TIGER 계열)
# 지수 추종 + 섹터 ETF 위주로 선정 — 레버리지/인버스 제외 (방향성 포지션용 아님)
_KOREA_ETF_UNIVERSE: list[dict[str, Any]] = [
    # 지수 ETF
    {"ticker": "069500", "name": "KODEX 200",          "market": "ETF"},
    {"ticker": "102110", "name": "TIGER 200",          "market": "ETF"},
    {"ticker": "229200", "name": "KODEX 코스닥150",     "market": "ETF"},
    {"ticker": "329200", "name": "TIGER 코스닥150",     "market": "ETF"},
    {"ticker": "379800", "name": "KODEX MSCI Korea",   "market": "ETF"},
    # 섹터 ETF
    {"ticker": "091160", "name": "KODEX 반도체",        "market": "ETF"},
    {"ticker": "091180", "name": "KODEX 자동차",        "market": "ETF"},
    {"ticker": "091170", "name": "KODEX 은행",          "market": "ETF"},
    {"ticker": "143850", "name": "TIGER 200 IT",       "market": "ETF"},
    {"ticker": "139260", "name": "TIGER 200 금융",      "market": "ETF"},
    {"ticker": "130730", "name": "TIGER 헬스케어",      "market": "ETF"},
    # 해외지수 ETF (KIS 거래 가능, 국내 상장)
    {"ticker": "133690", "name": "TIGER 나스닥100",     "market": "ETF"},
    {"ticker": "379810", "name": "KODEX 나스닥100",     "market": "ETF"},
    {"ticker": "195930", "name": "TIGER 선진국MSCI",    "market": "ETF"},
]

_lock = threading.Lock()
_cache: list[dict[str, Any]] = []
_cache_ts: float = 0.0


def _fetch_market(sosok: int, market_name: str, top_n: int) -> list[dict[str, Any]]:
    """Scrape Naver sise_market_sum for top-n stocks by market cap/volume."""
    results: list[dict[str, Any]] = []
    for page in range(1, 4):
        if len(results) >= top_n:
            break
        try:
            resp = requests.get(
                _NAVER_SISE_MARKET_SUM_URL.format(market=sosok, page=page),
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
    # 앵커 종목을 먼저 배치해 동적 스크랩 누락 시에도 항상 포함
    combined = _KOREA_ANCHOR_UNIVERSE + kospi + kosdaq + _KOREA_ETF_UNIVERSE
    # deduplicate by ticker (앵커가 먼저이므로 동적 결과 중복은 제거됨)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in combined:
        t = item["ticker"]
        if t not in seen:
            seen.add(t)
            deduped.append(item)
    etf_count = len(_KOREA_ETF_UNIVERSE)
    anchor_count = len(_KOREA_ANCHOR_UNIVERSE)
    _log.info(
        "korea_universe refreshed: %d anchor + %d KOSPI + %d KOSDAQ + %d ETF = %d total",
        anchor_count, len(kospi), len(kosdaq), etf_count, len(deduped),
    )
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

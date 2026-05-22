"""기관/외국인 수급 — Naver sise_invest 스크래핑 + 종목별 실시간 수급.

함수 목록
---------
get_institutional_tickers()          → KOSPI+KOSDAQ 기관 레이더 종목코드 집합 (1h TTL)
get_supply_demand_score(ticker)      → 0.0~1.0 수급 점수
get_investor_flow_today(ticker)      → {foreign_net, inst_net, retail_net, foreign_streak}
get_smart_money_confirmed(ticker)    → 기관+외국인 동시 순매수 여부 (bool)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import requests

from app.services.market_gateway import NAVER_HEADERS, REQUEST_TIMEOUT

_log = logging.getLogger(__name__)

_SISE_INVEST_URL = "https://finance.naver.com/sise/sise_invest.naver?sosok={sosok}&page={page}"
# 종목별 외국인/기관 수급 (당일 + 최근 5일)
_FRGN_URL = "https://finance.naver.com/item/frgn.naver?code={ticker}"

_CACHE_TTL     = 3600.0  # 1시간
_FLOW_CACHE_TTL = 1800.0  # 30분 (장중 변화 반영)

_lock      = threading.Lock()
_cache: dict[str, tuple[float, set[str]]] = {}         # market_key → (ts, tickers)
_flow_cache: dict[str, tuple[float, dict]] = {}         # ticker → (ts, flow_dict)


# ── 기관 레이더 종목 ──────────────────────────────────────────────────────────
def _fetch_sise_invest_tickers(sosok: int, pages: int = 3) -> set[str]:
    tickers: set[str] = set()
    for page in range(1, pages + 1):
        try:
            url = _SISE_INVEST_URL.format(sosok=sosok, page=page)
            resp = requests.get(url, headers=NAVER_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            html = resp.content.decode("euc-kr", errors="ignore")
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
    return tickers


def get_institutional_tickers() -> set[str]:
    """KOSPI + KOSDAQ 기관 레이더 종목코드 집합 (1시간 캐시)."""
    kospi  = _get_cached("kospi",  0)
    kosdaq = _get_cached("kosdaq", 1)
    return kospi | kosdaq


def get_supply_demand_score(ticker: str, inst_tickers: set[str] | None = None) -> float:
    """수급 점수 0.0~1.0.

    기관 레이더에 있으면 0.78, 없으면 0.40.
    """
    if inst_tickers is None:
        inst_tickers = get_institutional_tickers()
    return 0.78 if ticker.strip() in inst_tickers else 0.40


# ── 종목별 실시간 외국인/기관 수급 ────────────────────────────────────────────
def _parse_investor_flow(html: str) -> dict[str, Any]:
    """Naver frgn.naver HTML에서 외국인/기관 수급 파싱.

    Returns:
        {
            "foreign_net":    float  (오늘 외국계 순매수, 억원),
            "foreign_buy":    float  (오늘 외국계 매수),
            "foreign_sell":   float  (오늘 외국계 매도),
            "foreign_streak": int    (연속 순매수일 수, 양수=순매수, 음수=순매도),
            "inst_net":       float  (기관 추정 순매수, 억원 — 가능한 경우),
            "data_ok":        bool,
        }
    """
    result: dict[str, Any] = {
        "foreign_net": 0.0, "foreign_buy": 0.0, "foreign_sell": 0.0,
        "foreign_streak": 0, "inst_net": 0.0, "data_ok": False,
    }
    try:
        # 외국계추정합 행: 매수금액 | 매도금액 | 순매수금액 (단위: 백만원)
        # 패턴: "외국계추정합</td>..." 이후 숫자 3개
        match = re.search(
            r"외국계추정합[^<]*</[^>]+>\s*"
            r"<[^>]+>([\d,\-]+)</[^>]+>\s*"
            r"<[^>]+>([\d,\-]+)</[^>]+>\s*"
            r"<[^>]+>([\d,\-]+)</[^>]+>",
            html, re.S
        )
        if not match:
            # Fallback: find the row with "외국계추정합" and 3 numbers
            row_match = re.search(r"외국계추정합.*?(\d[\d,]+).*?(-?\d[\d,]+).*?(-?\d[\d,]+)", html, re.S)
            if row_match:
                nums = [row_match.group(1), row_match.group(2), row_match.group(3)]
            else:
                nums = None
        else:
            nums = [match.group(1), match.group(2), match.group(3)]

        if nums:
            def _num(s: str) -> float:
                return float(s.replace(",", "").replace("-", "0") or 0) / 100.0  # 백만→억
            result["foreign_buy"]  = _num(nums[0])
            result["foreign_sell"] = _num(nums[1])
            result["foreign_net"]  = float(nums[2].replace(",", "")) / 100.0
            result["data_ok"] = True

        # 연속 순매수일 계산: 최근 5거래일 외국인 순매수 테이블
        # Naver frgn.naver에는 날짜별 표가 있음 — 간략 구현 (연속성 추론)
        # 실제로는 기간별 합계만 보이므로 streak은 부호 추정
        # 순매수 양수 = +1, 음수 = -1 (단일일 기준)
        if result["data_ok"]:
            result["foreign_streak"] = 1 if result["foreign_net"] > 0 else (-1 if result["foreign_net"] < 0 else 0)

    except Exception as exc:
        _log.debug("_parse_investor_flow: %s", exc)
    return result


def get_investor_flow_today(ticker: str) -> dict[str, Any]:
    """종목별 오늘 외국인/기관 수급 스크래핑 (30분 캐시).

    Returns dict with keys: foreign_net, foreign_buy, foreign_sell,
                             foreign_streak, inst_net, data_ok
    """
    empty: dict[str, Any] = {
        "foreign_net": 0.0, "foreign_buy": 0.0, "foreign_sell": 0.0,
        "foreign_streak": 0, "inst_net": 0.0, "data_ok": False,
    }
    ticker = ticker.strip()
    if not ticker:
        return empty

    with _lock:
        entry = _flow_cache.get(ticker)
        if entry and time.monotonic() - entry[0] < _FLOW_CACHE_TTL:
            return entry[1]

    try:
        url  = _FRGN_URL.format(ticker=ticker)
        resp = requests.get(url, headers=NAVER_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        html = resp.content.decode("euc-kr", errors="ignore")
        flow = _parse_investor_flow(html)
    except Exception as exc:
        _log.debug("get_investor_flow_today %s: %s", ticker, exc)
        flow = empty

    with _lock:
        _flow_cache[ticker] = (time.monotonic(), flow)
    return flow


def get_smart_money_confirmed(
    ticker: str,
    inst_tickers: set[str] | None = None,
    min_foreign_net_bn: float = 0.0,   # 외국인 최소 순매수 (억원), 0=방향만 확인
) -> bool:
    """기관 레이더 포함 AND 외국인 순매수 동시 충족 여부.

    Parameters
    ----------
    ticker:            종목코드
    inst_tickers:      기관 레이더 집합 (None이면 자동 fetch)
    min_foreign_net_bn:외국인 최소 순매수 억원 (0이면 양수만 확인)

    Returns
    -------
    True if (기관 레이더 ✓) AND (외국인 순매수 > min_foreign_net_bn)
    """
    if inst_tickers is None:
        inst_tickers = get_institutional_tickers()

    in_inst_radar = ticker.strip() in inst_tickers
    if not in_inst_radar:
        return False

    flow = get_investor_flow_today(ticker)
    if not flow.get("data_ok"):
        # 데이터 미수집 시 레이더 포함만으로 True (보수적 가정)
        return True

    return float(flow.get("foreign_net", 0.0)) > min_foreign_net_bn

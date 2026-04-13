"""
코스닥 주식 스크리너 — 네이버 금융 실시간 API (pykrx 불필요).
Railway 해외 서버에서도 작동합니다.

장 시간: 09:00~15:30 KST
갭 스캔 창: config.STOCK_SCAN_START ~ STOCK_SCAN_END (기본 09:00~09:30)

데이터 소스 우선순위:
  1차: https://finance.naver.com/sise/sise_rise.naver?sosok=1  (HTML)
  2차: https://m.stock.naver.com/api/stock/exchange/KOSDAQ     (JSON)
  3차: https://finance.naver.com/sise/sise_market_sum.naver?sosok=1  (HTML)
"""

import re
import requests
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://finance.naver.com",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

MARKET_OPEN  = (9,  0)
MARKET_CLOSE = (15, 30)


def _kst_now() -> datetime:
    return datetime.now(config.KST)


def _in_market_hours() -> bool:
    """현재 시각이 장 운영 시간(09:00~15:30 KST) 내인지 확인합니다."""
    now    = _kst_now()
    open_  = now.replace(hour=MARKET_OPEN[0],  minute=MARKET_OPEN[1],  second=0, microsecond=0)
    close_ = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return open_ <= now <= close_


def _in_scan_window() -> bool:
    """갭 스캔 허용 범위(config.STOCK_SCAN_START ~ STOCK_SCAN_END, KST) 내인지 확인합니다."""
    now   = _kst_now()
    start = now.replace(hour=config.STOCK_SCAN_START[0], minute=config.STOCK_SCAN_START[1], second=0, microsecond=0)
    end   = now.replace(hour=config.STOCK_SCAN_END[0],   minute=config.STOCK_SCAN_END[1],   second=0, microsecond=0)
    return start <= now <= end


# ── 파서 ─────────────────────────────────────────────────────────────────────

_ROW_PATTERN = re.compile(
    r'code=(\d{6})"[^>]*>\s*([^<]{2,20})\s*</a>'
    r'.*?<td[^>]*class="number"[^>]*>([\d,]+)</td>'          # 현재가
    r'.*?<td[^>]*>(.*?)</td>'                                 # 전일비 (부호 포함)
    r'.*?<td[^>]*class="number"[^>]*>([\d,]+(?:\.\d+)?)</td>'# 등락률
    r'.*?<td[^>]*class="number"[^>]*>([\d,]+)</td>',         # 거래량
    re.DOTALL,
)


def _parse_naver_html(html: str, top_n: int) -> list[dict]:
    """네이버 금융 HTML에서 종목 데이터를 파싱합니다."""
    results: list[dict] = []
    for m in _ROW_PATTERN.finditer(html):
        if len(results) >= top_n:
            break
        ticker  = m.group(1)
        name    = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        cur_raw = m.group(3).replace(",", "")
        chg_raw = re.sub(r'<[^>]+>', '', m.group(4)).strip()
        vol_raw = m.group(6).replace(",", "")

        if not name or len(name) < 2:
            continue
        try:
            current_price = float(cur_raw)
            volume        = int(vol_raw)
        except ValueError:
            continue

        try:
            chg_num    = float(chg_raw.replace(",", "").replace("+", "").replace("▲", "").replace("▼", "-"))
            prev_close = current_price - chg_num
        except ValueError:
            prev_close = current_price

        if prev_close <= 0:
            prev_close = current_price

        gap_pct = (current_price - prev_close) / prev_close * 100
        results.append({
            "ticker":        ticker,
            "name":          name,
            "current_price": current_price,
            "prev_close":    prev_close,
            "today_open":    current_price,
            "gap_pct":       round(gap_pct, 2),
            "volume":        volume,
            "prev_volume":   volume,
        })

    results.sort(key=lambda x: x["gap_pct"], reverse=True)
    return results


def _parse_naver_mobile_json(data, top_n: int) -> list[dict]:
    """네이버 모바일 API JSON 응답을 파싱합니다 (다양한 응답 구조 대응)."""
    # 응답 구조 탐색
    if isinstance(data, list):
        stocks = data
    else:
        stocks = (
            data.get("stocks")
            or data.get("result", {}).get("stocks")
            or data.get("data", {}).get("stocks")
            or data.get("stockList")
            or []
        )

    results: list[dict] = []
    for s in stocks[:top_n * 2]:
        try:
            ticker    = str(s.get("itemCode") or s.get("code") or "").strip()
            name      = str(s.get("stockName") or s.get("name") or "").strip()
            cur_str   = str(s.get("closePrice") or s.get("currentPrice") or 0).replace(",", "")
            chg_str   = str(s.get("compareToPreviousClosePrice") or s.get("change") or 0).replace(",", "")
            open_str  = str(s.get("openingPrice") or s.get("openPrice") or cur_str).replace(",", "")
            vol_str   = str(s.get("accumulatedTradingVolume") or s.get("volume") or 0).replace(",", "")

            if not ticker or not name or len(name) < 2:
                continue

            current_price = float(cur_str)
            chg_num       = float(chg_str)
            open_price    = float(open_str) if open_str else current_price
            volume        = int(float(vol_str)) if vol_str else 0

            if current_price <= 0:
                continue

            prev_close = current_price - chg_num
            if prev_close <= 0:
                prev_close = current_price

            gap_pct = (current_price - prev_close) / prev_close * 100
            results.append({
                "ticker":        ticker,
                "name":          name,
                "current_price": current_price,
                "prev_close":    prev_close,
                "today_open":    open_price,
                "gap_pct":       round(gap_pct, 2),
                "volume":        volume,
                "prev_volume":   volume,
            })
        except (ValueError, TypeError):
            continue

    results.sort(key=lambda x: x["gap_pct"], reverse=True)
    return results[:top_n]


def get_kosdaq_realtime(top_n: int = 50) -> list[dict]:
    """
    네이버 금융 코스닥 등락률 상위 종목 실시간 데이터 반환.
    세 가지 URL을 순서대로 시도합니다 (각각 실패 시 다음으로 fallback).

    Returns:
        [{"ticker", "name", "current_price", "prev_close",
          "today_open", "gap_pct", "volume", "prev_volume"}, ...]
    """
    # 1차: 기존 HTML (등락률 상위)
    try:
        resp = requests.get(
            "https://finance.naver.com/sise/sise_rise.naver?sosok=1",
            headers=HEADERS, timeout=10,
        )
        resp.encoding = "euc-kr"
        results = _parse_naver_html(resp.text, top_n)
        if results:
            return results
    except Exception:
        pass

    # 2차: 네이버 모바일 JSON API
    try:
        resp = requests.get(
            "https://m.stock.naver.com/api/stock/exchange/KOSDAQ",
            headers=HEADERS, timeout=10,
        )
        data    = resp.json()
        results = _parse_naver_mobile_json(data, top_n)
        if results:
            return results
    except Exception:
        pass

    # 3차: sise_market_sum HTML
    try:
        resp = requests.get(
            "https://finance.naver.com/sise/sise_market_sum.naver?sosok=1",
            headers=HEADERS, timeout=10,
        )
        resp.encoding = "euc-kr"
        results = _parse_naver_html(resp.text, top_n)
        if results:
            return results
    except Exception:
        pass

    return []


def get_gap_up_stocks() -> list[dict]:
    """
    코스닥 갭 상승 종목 리스트.

    - 갭 +config.STOCK_GAP_MIN% 이상
    - 갭 스캔 창(09:00~09:30) 내에서만 반환
    - Railway 해외 서버에서도 동작 (pykrx 불필요)

    Returns:
        [
          {
            "ticker", "name", "prev_close", "today_open",
            "gap_pct", "prev_volume"
          },
          ...
        ]
    """
    if not _in_scan_window():
        return []

    all_stocks = get_kosdaq_realtime(config.STOCK_TOP_N)
    gap_stocks = [
        s for s in all_stocks if s.get("gap_pct", 0) >= config.STOCK_GAP_MIN
    ]
    gap_stocks.sort(key=lambda x: x["gap_pct"], reverse=True)
    return gap_stocks

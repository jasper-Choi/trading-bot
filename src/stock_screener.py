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
    now    = _kst_now()
    open_  = now.replace(hour=MARKET_OPEN[0],  minute=MARKET_OPEN[1],  second=0, microsecond=0)
    close_ = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return open_ <= now <= close_


def _in_scan_window() -> bool:
    now   = _kst_now()
    start = now.replace(hour=config.STOCK_SCAN_START[0], minute=config.STOCK_SCAN_START[1], second=0, microsecond=0)
    end   = now.replace(hour=config.STOCK_SCAN_END[0],   minute=config.STOCK_SCAN_END[1],   second=0, microsecond=0)
    return start <= now <= end


# ── 파서 ─────────────────────────────────────────────────────────────────────

_ROW_PATTERN = re.compile(
    r'code=(\d{6})"[^>]*>\s*([^<]{2,20})\s*</a>'
    r'.*?<td[^>]*class="number"[^>]*>([\d,]+)</td>'
    r'.*?<td[^>]*>(.*?)</td>'
    r'.*?<td[^>]*class="number"[^>]*>([\d,]+(?:\.\d+)?)</td>'
    r'.*?<td[^>]*class="number"[^>]*>([\d,]+)</td>',
    re.DOTALL,
)


def _parse_naver_html(html: str, top_n: int) -> list[dict]:
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


def get_kosdaq_realtime(top_n: int = 50, verbose: bool = False) -> list[dict]:
    """
    네이버 금융 코스닥 등락률 상위 종목 실시간 데이터 반환.
    세 가지 URL을 순서대로 시도합니다 (각각 실패 시 다음으로 fallback).

    Args:
        top_n:   최대 반환 종목 수
        verbose: True이면 각 단계 상세 로그 출력 (stock-test용)

    Returns:
        [{"ticker", "name", "current_price", "prev_close",
          "today_open", "gap_pct", "volume", "prev_volume"}, ...]
    """
    def vprint(msg: str):
        if verbose:
            print(msg)

    # 1차: 네이버 금융 HTML (등락률 상위)
    url1 = "https://finance.naver.com/sise/sise_rise.naver?sosok=1"
    try:
        vprint(f"[주식스크리너] 1차 시도: {url1}")
        resp = requests.get(url1, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        vprint(f"[주식스크리너] 1차 HTTP 상태: {resp.status_code}, 응답 길이: {len(resp.text):,}자")
        results = _parse_naver_html(resp.text, top_n)
        vprint(f"[주식스크리너] 1차 파싱 결과: {len(results)}개 종목")
        if results:
            return results
        print("[주식스크리너] 1차(sise_rise) 파싱 결과 0개 — 2차 시도")
    except requests.exceptions.Timeout:
        print(f"[주식스크리너] 1차(sise_rise) 타임아웃 — 2차 시도")
    except requests.exceptions.ConnectionError as e:
        print(f"[주식스크리너] 1차(sise_rise) 연결 오류: {e} — 2차 시도")
    except Exception as e:
        print(f"[주식스크리너] 1차(sise_rise) 오류: {type(e).__name__}: {e}")

    # 2차: 네이버 모바일 JSON API
    url2 = "https://m.stock.naver.com/api/stock/exchange/KOSDAQ"
    try:
        vprint(f"[주식스크리너] 2차 시도: {url2}")
        resp = requests.get(url2, headers=HEADERS, timeout=10)
        vprint(f"[주식스크리너] 2차 HTTP 상태: {resp.status_code}")
        data    = resp.json()
        vprint(f"[주식스크리너] 2차 JSON 키: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
        results = _parse_naver_mobile_json(data, top_n)
        vprint(f"[주식스크리너] 2차 파싱 결과: {len(results)}개 종목")
        if results:
            return results
        print("[주식스크리너] 2차(mobile JSON) 파싱 결과 0개 — 3차 시도")
    except requests.exceptions.Timeout:
        print(f"[주식스크리너] 2차(mobile JSON) 타임아웃 — 3차 시도")
    except requests.exceptions.ConnectionError as e:
        print(f"[주식스크리너] 2차(mobile JSON) 연결 오류: {e}")
    except Exception as e:
        print(f"[주식스크리너] 2차(mobile JSON) 오류: {type(e).__name__}: {e}")

    # 3차: sise_market_sum HTML
    url3 = "https://finance.naver.com/sise/sise_market_sum.naver?sosok=1"
    try:
        vprint(f"[주식스크리너] 3차 시도: {url3}")
        resp = requests.get(url3, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        vprint(f"[주식스크리너] 3차 HTTP 상태: {resp.status_code}, 응답 길이: {len(resp.text):,}자")
        results = _parse_naver_html(resp.text, top_n)
        vprint(f"[주식스크리너] 3차 파싱 결과: {len(results)}개 종목")
        if results:
            return results
        print("[주식스크리너] 3차(sise_market_sum) 파싱 결과 0개 — 데이터 없음")
    except requests.exceptions.Timeout:
        print(f"[주식스크리너] 3차(sise_market_sum) 타임아웃")
    except requests.exceptions.ConnectionError as e:
        print(f"[주식스크리너] 3차(sise_market_sum) 연결 오류: {e}")
    except Exception as e:
        print(f"[주식스크리너] 3차(sise_market_sum) 오류: {type(e).__name__}: {e}")

    print("[주식스크리너] 모든 API 실패 — 빈 리스트 반환")
    return []


def get_gap_up_stocks(force: bool = False, verbose: bool = False) -> list[dict]:
    """
    코스닥 갭 상승 종목 리스트.

    - 갭 +config.STOCK_GAP_MIN% 이상
    - 갭 스캔 창(09:00~09:30) 내에서만 반환 (force=True 시 시간 무시)
    - Railway 해외 서버에서도 동작 (pykrx 불필요)

    Args:
        force:   True이면 장 시간/스캔 창 체크를 건너뜁니다 (테스트용).
        verbose: True이면 각 단계 상세 로그 출력 (stock-test용)
    """
    if not force and not _in_scan_window():
        return []

    all_stocks = get_kosdaq_realtime(config.STOCK_TOP_N, verbose=verbose)
    gap_stocks = [
        s for s in all_stocks if s.get("gap_pct", 0) >= config.STOCK_GAP_MIN
    ]
    gap_stocks.sort(key=lambda x: x["gap_pct"], reverse=True)
    return gap_stocks

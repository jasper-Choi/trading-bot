"""
코스닥 주식 스크리너 — 네이버 금융 실시간 API (pykrx 불필요).
Railway 해외 서버에서도 작동합니다.

장 시간: 09:00~15:30 KST
갭 스캔 창: config.STOCK_SCAN_START ~ STOCK_SCAN_END (기본 09:00~09:30)
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


def _in_market_hours() -> bool:
    """현재 시각이 장 운영 시간(09:00~15:30) 내인지 확인합니다."""
    now  = datetime.now()
    open_  = now.replace(hour=MARKET_OPEN[0],  minute=MARKET_OPEN[1],  second=0)
    close_ = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0)
    return open_ <= now <= close_


def _in_scan_window() -> bool:
    """갭 스캔 허용 범위(config.STOCK_SCAN_START ~ STOCK_SCAN_END) 내인지 확인합니다."""
    now   = datetime.now()
    start = now.replace(hour=config.STOCK_SCAN_START[0], minute=config.STOCK_SCAN_START[1], second=0)
    end   = now.replace(hour=config.STOCK_SCAN_END[0],   minute=config.STOCK_SCAN_END[1],   second=0)
    return start <= now <= end


def get_kosdaq_realtime(top_n: int = 50) -> list[dict]:
    """
    네이버 금융 코스닥 등락률 상위 종목 실시간 데이터 반환.
    장 시간 무관하게 동작합니다.

    Returns:
        [
          {
            "ticker":        "035720",
            "name":          "카카오",
            "current_price": 52000,
            "prev_close":    50000,
            "today_open":    51000,
            "gap_pct":       4.0,
            "volume":        1234567,
            "prev_volume":   900000,
          },
          ...
        ]
    """
    url = "https://finance.naver.com/sise/sise_rise.naver?sosok=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        html = resp.text
    except Exception:
        return []

    results: list[dict] = []

    # 각 행에서 종목코드·종목명·현재가·전일종가·거래량 추출
    # Naver Finance 테이블 구조:
    #   <a href=".../code=XXXXXX">종목명</a> ... <td class="number">현재가</td> ... 전일비 ... 등락률 ... 거래량
    row_pattern = re.compile(
        r'code=(\d{6})"[^>]*>\s*([^<]{2,20})\s*</a>'
        r'.*?<td[^>]*class="number"[^>]*>([\d,]+)</td>'   # 현재가
        r'.*?<td[^>]*>(.*?)</td>'                          # 전일비 (부호 포함)
        r'.*?<td[^>]*class="number"[^>]*>([\d,]+(?:\.\d+)?)</td>'  # 등락률
        r'.*?<td[^>]*class="number"[^>]*>([\d,]+)</td>',  # 거래량
        re.DOTALL,
    )

    for m in row_pattern.finditer(html):
        if len(results) >= top_n:
            break
        ticker   = m.group(1)
        name     = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        cur_raw  = m.group(3).replace(",", "")
        chg_raw  = re.sub(r'<[^>]+>', '', m.group(4)).strip()
        vol_raw  = m.group(6).replace(",", "")

        if not name or len(name) < 2:
            continue

        try:
            current_price = float(cur_raw)
            volume        = int(vol_raw)
        except ValueError:
            continue

        # 전일 종가 추정 (현재가 - 전일비)
        try:
            chg_num   = float(chg_raw.replace(",", "").replace("+", "").replace("▲", "").replace("▼", "-"))
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
            "today_open":    current_price,   # 장 초기 ≈ 시가
            "gap_pct":       round(gap_pct, 2),
            "volume":        volume,
            "prev_volume":   volume,          # 당일 거래량 (전일 데이터는 별도 요청 필요)
        })

    # 등락률 기준 정렬
    results.sort(key=lambda x: x["gap_pct"], reverse=True)
    return results


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

"""
코스닥 주식 스크리너 — 장 시작(09:00~09:30) 갭 상승 종목 탐지.

조건:
  1. 코스닥 전체 종목 중 전일 거래량 상위 STOCK_TOP_N 개 선별
  2. 오늘 시가가 전일 종가 대비 +STOCK_GAP_MIN% 이상 상승한 종목만 추출
  3. 09:00~09:30 사이에만 실행 (이 외 시간은 빈 리스트 반환)

결과는 포지션 진입 없이 로그 표시용으로만 사용됩니다.
pykrx 미설치 시 ImportError 없이 빈 리스트를 반환합니다.
"""

from datetime import datetime, date, timedelta
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

try:
    from pykrx import stock as _pykrx
    _PYKRX_OK = True
except ImportError:
    _PYKRX_OK = False


def _last_trading_day() -> str:
    """오늘 이전 가장 가까운 영업일(YYYYMMDD)을 반환합니다."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:   # 토(5), 일(6) 스킵
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _in_scan_window() -> bool:
    """현재 시각이 설정된 스캔 허용 범위 안인지 확인합니다."""
    now = datetime.now()
    start = now.replace(hour=config.STOCK_SCAN_START[0], minute=config.STOCK_SCAN_START[1], second=0)
    end   = now.replace(hour=config.STOCK_SCAN_END[0],   minute=config.STOCK_SCAN_END[1],   second=0)
    return start <= now <= end


def get_gap_up_stocks() -> list[dict]:
    """
    코스닥 갭 상승 종목 리스트를 반환합니다.

    Returns:
        [
          {
            "ticker":       "035720",
            "name":         "카카오",
            "prev_close":   50000,
            "today_open":   52000,
            "gap_pct":      4.0,
            "prev_volume":  1234567,
          },
          ...
        ]
        조건 미충족 시 빈 리스트.
    """
    if not _PYKRX_OK:
        return []   # pykrx 미설치

    if not _in_scan_window():
        return []   # 장 시작 시간 외

    today_str = date.today().strftime("%Y%m%d")
    prev_str  = _last_trading_day()

    try:
        # 전일 OHLCV (종가·거래량 추출)
        prev_df = _pykrx.get_market_ohlcv_by_ticker(prev_str, market="KOSDAQ")
        if prev_df is None or prev_df.empty:
            return []

        # 오늘 OHLCV (시가 추출)
        today_df = _pykrx.get_market_ohlcv_by_ticker(today_str, market="KOSDAQ")
        if today_df is None or today_df.empty:
            return []

    except Exception:
        return []

    # 전일 거래량 상위 N개 필터
    col_vol   = "거래량"
    col_close = "종가"
    col_open  = "시가"

    if col_vol not in prev_df.columns:
        return []

    top_prev = (
        prev_df[[col_close, col_vol]]
        .sort_values(col_vol, ascending=False)
        .head(config.STOCK_TOP_N)
    )

    results: list[dict] = []
    for ticker in top_prev.index:
        if ticker not in today_df.index:
            continue

        prev_close  = float(top_prev.loc[ticker, col_close])
        prev_volume = int(top_prev.loc[ticker, col_vol])
        today_open  = float(today_df.loc[ticker, col_open])

        if prev_close <= 0:
            continue

        gap_pct = (today_open - prev_close) / prev_close * 100

        if gap_pct >= config.STOCK_GAP_MIN:
            try:
                name = _pykrx.get_market_ticker_name(ticker)
            except Exception:
                name = ticker

            results.append(
                {
                    "ticker":      ticker,
                    "name":        name,
                    "prev_close":  prev_close,
                    "today_open":  today_open,
                    "gap_pct":     round(gap_pct, 2),
                    "prev_volume": prev_volume,
                }
            )

    results.sort(key=lambda x: x["gap_pct"], reverse=True)
    return results

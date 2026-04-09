"""
업비트 15분봉 API 데이터 수집.

screener가 넘겨준 동적 코인 리스트로 수집하며,
고정 코인 리스트에 의존하지 않습니다.

endpoint: GET https://api.upbit.com/v1/candles/minutes/15
"""

import time
import requests
import pandas as pd

UPBIT_15M_URL  = "https://api.upbit.com/v1/candles/minutes/15"
REQUEST_TIMEOUT = 10


def fetch_15m_candles(market: str, count: int = 100) -> pd.DataFrame:
    """
    업비트 15분봉 데이터를 가져와 DataFrame으로 반환합니다.

    반환 컬럼: date (Timestamp), open, high, low, close, volume
    시간 오름차순 정렬 (과거 → 최신)
    """
    params  = {"market": market, "count": min(count, 200)}
    headers = {"Accept": "application/json"}

    for attempt in range(3):
        try:
            resp = requests.get(
                UPBIT_15M_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == 2:
                raise RuntimeError(f"[15M] API 호출 실패 ({market}): {e}") from e
            time.sleep(1)

    data = resp.json()
    if not data:
        raise ValueError(f"[15M] 빈 응답: {market}")

    df = pd.DataFrame(data).rename(
        columns={
            "candle_date_time_kst":      "date",
            "opening_price":             "open",
            "high_price":                "high",
            "low_price":                 "low",
            "trade_price":               "close",
            "candle_acc_trade_volume":   "volume",
        }
    )
    df["date"] = pd.to_datetime(df["date"])
    return (
        df[["date", "open", "high", "low", "close", "volume"]]
        .sort_values("date")
        .reset_index(drop=True)
    )


def fetch_15m_candles_batch(
    markets: list[str], count: int = 100
) -> dict[str, pd.DataFrame]:
    """
    여러 코인의 15분봉 데이터를 순차적으로 수집해 딕셔너리로 반환합니다.
    실패한 코인은 결과에서 제외됩니다.
    """
    result: dict[str, pd.DataFrame] = {}
    for market in markets:
        try:
            result[market] = fetch_15m_candles(market, count)
            time.sleep(0.1)   # Rate Limit 대응
        except Exception:
            pass
    return result


def fetch_current_price(market: str) -> float:
    """현재 체결가(가장 최근 15분봉 종가)를 반환합니다."""
    df = fetch_15m_candles(market, count=1)
    return float(df.iloc[-1]["close"])


# API 라우터 등 기존 호출부와의 호환성 유지
fetch_daily_candles = fetch_15m_candles

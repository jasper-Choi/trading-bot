"""
업비트 전체 KRW 마켓 스캔 — 24시간 거래대금 상위 N개 코인 추출.

endpoints:
  GET https://api.upbit.com/v1/market/all   전체 마켓 목록
  GET https://api.upbit.com/v1/ticker       24시간 티커 (거래대금 포함)

Upbit ticker API 는 요청당 최대 100개 마켓을 지원하므로
KRW 마켓이 100개를 초과할 경우 청크 단위로 분할 요청합니다.
"""

import time
import requests
import pandas as pd

MARKET_ALL_URL = "https://api.upbit.com/v1/market/all"
TICKER_URL     = "https://api.upbit.com/v1/ticker"
REQUEST_TIMEOUT = 10
CHUNK_SIZE      = 100


def _get_krw_markets() -> list[str]:
    """업비트 전체 마켓 중 KRW 마켓만 반환합니다."""
    resp = requests.get(MARKET_ALL_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return [m["market"] for m in resp.json() if m["market"].startswith("KRW-")]


def _get_tickers(markets: list[str]) -> list[dict]:
    """마켓 리스트에 대한 24시간 티커를 반환합니다 (청크 단위 요청)."""
    tickers: list[dict] = []
    for i in range(0, len(markets), CHUNK_SIZE):
        chunk = markets[i : i + CHUNK_SIZE]
        params = {"markets": ",".join(chunk)}
        for attempt in range(3):
            try:
                resp = requests.get(TICKER_URL, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                tickers.extend(resp.json())
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(0.5)
        time.sleep(0.1)   # Upbit Rate Limit 대응 (초당 10회)
    return tickers


def get_top_krw_coins(top_n: int = 30) -> list[str]:
    """
    KRW 마켓 전체를 스캔해 24시간 거래대금 상위 top_n 개 코인을 반환합니다.

    Returns:
        ["KRW-BTC", "KRW-ETH", ...]  (거래대금 내림차순)
    """
    krw_markets = _get_krw_markets()
    tickers     = _get_tickers(krw_markets)

    df = pd.DataFrame(tickers)[["market", "acc_trade_price_24h"]]
    df["acc_trade_price_24h"] = pd.to_numeric(df["acc_trade_price_24h"], errors="coerce")
    df = df.dropna().sort_values("acc_trade_price_24h", ascending=False)

    top = df.head(top_n)["market"].tolist()
    return top


def get_ticker_snapshot(markets: list[str]) -> dict[str, dict]:
    """
    주어진 마켓 리스트의 현재 티커 정보를 딕셔너리로 반환합니다.
    { "KRW-BTC": { "trade_price": ..., "acc_trade_price_24h": ..., ... }, ... }
    """
    tickers = _get_tickers(markets)
    return {t["market"]: t for t in tickers}

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests


UPBIT_MARKETS_URL = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"
UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
NAVER_KOSDAQ_URL = "https://m.stock.naver.com/api/stock/exchange/KOSDAQ"
REQUEST_TIMEOUT = 8

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


@dataclass(slots=True)
class MarketSnapshot:
    crypto_leaders: list[dict[str, Any]]
    crypto_watchlist: list[str]
    stock_leaders: list[dict[str, Any]]
    gap_candidates: list[dict[str, Any]]
    as_of: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def get_top_krw_coins(top_n: int = 10) -> list[dict[str, Any]]:
    market_resp = requests.get(UPBIT_MARKETS_URL, timeout=REQUEST_TIMEOUT)
    market_resp.raise_for_status()
    krw_markets = [item["market"] for item in market_resp.json() if item["market"].startswith("KRW-")]

    tickers: list[dict[str, Any]] = []
    for chunk in _chunked(krw_markets, 100):
        ticker_resp = requests.get(
            UPBIT_TICKER_URL,
            params={"markets": ",".join(chunk)},
            timeout=REQUEST_TIMEOUT,
        )
        ticker_resp.raise_for_status()
        tickers.extend(ticker_resp.json())

    leaders = sorted(
        tickers,
        key=lambda item: float(item.get("acc_trade_price_24h") or 0),
        reverse=True,
    )[:top_n]
    return [
        {
            "market": item.get("market"),
            "trade_price": float(item.get("trade_price") or 0),
            "change_rate": round(float(item.get("signed_change_rate") or 0) * 100, 2),
            "volume_24h_krw": int(float(item.get("acc_trade_price_24h") or 0)),
        }
        for item in leaders
    ]


def get_upbit_15m_candles(market: str, count: int = 40) -> list[dict[str, Any]]:
    resp = requests.get(
        UPBIT_CANDLES_URL.format(unit=15),
        params={"market": market, "count": min(count, 200)},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = list(reversed(resp.json()))
    return [
        {
            "date": row.get("candle_date_time_kst"),
            "open": float(row.get("opening_price") or 0),
            "high": float(row.get("high_price") or 0),
            "low": float(row.get("low_price") or 0),
            "close": float(row.get("trade_price") or 0),
            "volume": float(row.get("candle_acc_trade_volume") or 0),
        }
        for row in rows
    ]


def get_kosdaq_snapshot(top_n: int = 20) -> list[dict[str, Any]]:
    resp = requests.get(NAVER_KOSDAQ_URL, headers=NAVER_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    rows = payload if isinstance(payload, list) else payload.get("stocks") or payload.get("stockList") or []

    snapshots: list[dict[str, Any]] = []
    for item in rows[: top_n * 2]:
        try:
            current_price = float(str(item.get("closePrice") or item.get("currentPrice") or 0).replace(",", ""))
            change_value = float(str(item.get("compareToPreviousClosePrice") or item.get("change") or 0).replace(",", ""))
            volume = int(float(str(item.get("accumulatedTradingVolume") or item.get("volume") or 0).replace(",", "")))
            name = str(item.get("stockName") or item.get("name") or "").strip()
            ticker = str(item.get("itemCode") or item.get("code") or "").strip()
            if not ticker or not name or current_price <= 0:
                continue
            prev_close = current_price - change_value
            if prev_close <= 0:
                prev_close = current_price
            gap_pct = round(((current_price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0
            snapshots.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "current_price": current_price,
                    "gap_pct": gap_pct,
                    "volume": volume,
                }
            )
        except (TypeError, ValueError):
            continue

    snapshots.sort(key=lambda item: item["gap_pct"], reverse=True)
    return snapshots[:top_n]


def build_market_snapshot() -> MarketSnapshot:
    crypto_leaders = get_top_krw_coins(top_n=8)
    stock_leaders = get_kosdaq_snapshot(top_n=12)
    return MarketSnapshot(
        crypto_leaders=crypto_leaders,
        crypto_watchlist=[item["market"] for item in crypto_leaders[:5]],
        stock_leaders=stock_leaders,
        gap_candidates=[item for item in stock_leaders if item.get("gap_pct", 0) >= 2.0][:5],
        as_of=_now_iso(),
    )


from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import logging
import re
from pathlib import Path
from typing import Any

import requests
from requests import RequestException

from app.config import settings

_log = logging.getLogger(__name__)


UPBIT_MARKETS_URL = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"
UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
NAVER_KOSDAQ_URL = "https://m.stock.naver.com/api/stock/exchange/KOSDAQ"
NAVER_KOSDAQ_FALLBACK_URL = "https://finance.naver.com/sise/sise_market_sum.naver?sosok=1&page={page}"
NAVER_STOCK_DAY_URL = "https://finance.naver.com/item/sise_day.naver?code={ticker}&page={page}"
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/?s={ticker}.us&i=d"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
ALPHAVANTAGE_DAILY_URL = "https://www.alphavantage.co/query"
REQUEST_TIMEOUT = 8
US_CORE_TICKERS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA"]
LAST_US_DATA_STATUS: dict[str, Any] = {"provider": "none", "ok": False, "message": "not requested yet"}
US_CACHE_PATH = Path(settings.db_path).resolve().parent / "us_daily_cache.json"

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
    us_leaders: list[dict[str, Any]]
    us_watchlist: list[str]
    as_of: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_us_cache() -> dict[str, Any]:
    if not US_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(US_CACHE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_us_cache(cache: dict[str, Any]) -> None:
    try:
        US_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        US_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def get_top_krw_coins(top_n: int = 10) -> list[dict[str, Any]]:
    try:
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
    except RequestException:
        return []


def get_upbit_15m_candles(market: str, count: int = 40) -> list[dict[str, Any]]:
    try:
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
    except RequestException:
        return []


def get_kosdaq_snapshot(top_n: int = 20) -> list[dict[str, Any]]:
    try:
        resp = requests.get(NAVER_KOSDAQ_URL, headers=NAVER_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload if isinstance(payload, list) else payload.get("stocks") or payload.get("stockList") or []
    except (RequestException, ValueError) as exc:
        _log.warning("kosdaq_snapshot API failed (%s), falling back to HTML scrape", exc)
        return _get_kosdaq_snapshot_from_naver_html(top_n)

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


def _strip_html(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _to_number(value: str) -> float:
    cleaned = value.replace(",", "").replace("%", "").strip()
    return float(cleaned) if cleaned else 0.0


def _get_kosdaq_snapshot_from_naver_html(top_n: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for page in range(1, 3):
        try:
            resp = requests.get(NAVER_KOSDAQ_FALLBACK_URL.format(page=page), headers=NAVER_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            html = resp.content.decode("euc-kr", errors="ignore")
        except RequestException as exc:
            _log.warning("kosdaq_snapshot HTML fallback page=%d failed: %s", page, exc)
            continue

        rows = re.findall(r'<tr[^>]*onMouseOver="mouseOver\(this\)"[^>]*>(.*?)</tr>', html, flags=re.S | re.I)
        for row in rows:
            code_match = re.search(r'/item/main\.naver\?code=(\d+)"[^>]*class="tltle">([^<]+)</a>', row)
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)
            if not code_match or len(cells) < 7:
                continue
            try:
                ticker = code_match.group(1)
                name = _strip_html(code_match.group(2))
                current_price = _to_number(_strip_html(cells[2]))
                gap_pct = _to_number(_strip_html(cells[4]))
                volume = int(_to_number(_strip_html(cells[6])))
            except (TypeError, ValueError):
                continue
            if not ticker or not name or current_price <= 0:
                continue
            candidates.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "current_price": current_price,
                    "gap_pct": round(gap_pct, 2),
                    "volume": volume,
                }
            )

    candidates.sort(key=lambda item: (item["gap_pct"], item["volume"]), reverse=True)
    return candidates[:top_n]


_PINNED_CRYPTO = ["KRW-DOGE", "KRW-XRP", "KRW-SOL", "KRW-BTC", "KRW-ETH"]


def build_market_snapshot() -> MarketSnapshot:
    crypto_leaders = get_top_krw_coins(top_n=20)
    # Ensure backtest-validated symbols are always present with a live price
    leader_markets = {item["market"] for item in crypto_leaders}
    missing = [m for m in _PINNED_CRYPTO if m not in leader_markets]
    if missing:
        try:
            resp = requests.get(UPBIT_TICKER_URL, params={"markets": ",".join(missing)}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            for item in resp.json():
                market = str(item.get("market") or "")
                price = float(item.get("trade_price") or 0)
                change_rate = round(float(item.get("signed_change_rate") or 0) * 100, 2)
                volume_24h = int(float(item.get("acc_trade_price_24h") or 0))
                if market and price > 0:
                    crypto_leaders.append({"market": market, "trade_price": price, "change_rate": change_rate, "volume_24h_krw": volume_24h})
        except Exception:
            pass
    stock_leaders = get_kosdaq_snapshot(top_n=30)
    us_leaders = get_us_core_snapshot()
    return MarketSnapshot(
        crypto_leaders=crypto_leaders,
        crypto_watchlist=[item["market"] for item in crypto_leaders[:5]],
        stock_leaders=stock_leaders,
        gap_candidates=[
            item
            for item in stock_leaders
            if 1.2 <= float(item.get("gap_pct", 0.0) or 0.0) <= 12.0
        ][:8],
        us_leaders=us_leaders,
        us_watchlist=[item["ticker"] for item in us_leaders[:4]],
        as_of=_now_iso(),
    )


def get_naver_daily_prices(ticker: str, count: int = 20) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    if not ticker:
        return candles

    for page in range(1, 4):
        try:
            resp = requests.get(NAVER_STOCK_DAY_URL.format(ticker=ticker, page=page), headers=NAVER_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            html = resp.content.decode("euc-kr", errors="ignore")
        except RequestException as exc:
            _log.warning("naver_daily_prices ticker=%s page=%d failed: %s", ticker, page, exc)
            continue

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I)
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)
            if len(cells) < 7:
                continue
            date_text = _strip_html(cells[0])
            close_text = _strip_html(cells[1])
            open_text = _strip_html(cells[3])
            high_text = _strip_html(cells[4])
            low_text = _strip_html(cells[5])
            volume_text = _strip_html(cells[6])
            try:
                if not date_text or "." not in date_text:
                    continue
                candles.append(
                    {
                        "date": date_text,
                        "open": _to_number(open_text),
                        "high": _to_number(high_text),
                        "low": _to_number(low_text),
                        "close": _to_number(close_text),
                        "volume": _to_number(volume_text),
                    }
                )
            except (TypeError, ValueError):
                continue
            if len(candles) >= count:
                break
        if len(candles) >= count:
            break

    candles.reverse()
    return candles[:count]


def get_us_daily_prices(ticker: str, count: int = 60) -> list[dict[str, Any]]:
    global LAST_US_DATA_STATUS
    today = datetime.now(timezone.utc).date().isoformat()
    cache = _load_us_cache()
    cached = cache.get(ticker.upper()) or {}
    if cached.get("as_of") == today and cached.get("candles"):
        LAST_US_DATA_STATUS = {"provider": cached.get("provider", "cache"), "ok": True, "message": "cached daily candles"}
        return list(cached.get("candles", []))[-count:]

    if settings.alphavantage_api_key:
        candles = _get_us_daily_prices_alphavantage(ticker, count)
        if candles:
            LAST_US_DATA_STATUS = {"provider": "alphavantage", "ok": True, "message": "ok"}
            cache[ticker.upper()] = {"as_of": today, "provider": "alphavantage", "candles": candles[-count:]}
            _save_us_cache(cache)
            return candles
        LAST_US_DATA_STATUS = {"provider": "alphavantage", "ok": False, "message": "empty or throttled response"}
    try:
        resp = requests.get(STOOQ_DAILY_URL.format(ticker=ticker.lower()), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
    except RequestException:
        candles = _get_us_daily_prices_yahoo(ticker, count)
        if candles:
            LAST_US_DATA_STATUS = {"provider": "yahoo", "ok": True, "message": "ok"}
            cache[ticker.upper()] = {"as_of": today, "provider": "yahoo", "candles": candles[-count:]}
            _save_us_cache(cache)
            return candles
        LAST_US_DATA_STATUS = {"provider": "stooq/yahoo", "ok": False, "message": "request failed"}
        return []

    if len(lines) < 2:
        candles = _get_us_daily_prices_yahoo(ticker, count)
        if candles:
            LAST_US_DATA_STATUS = {"provider": "yahoo", "ok": True, "message": "ok"}
            cache[ticker.upper()] = {"as_of": today, "provider": "yahoo", "candles": candles[-count:]}
            _save_us_cache(cache)
            return candles
        LAST_US_DATA_STATUS = {"provider": "stooq/yahoo", "ok": False, "message": "stooq requires apikey and yahoo returned empty"}
        return []

    candles: list[dict[str, Any]] = []
    for row in lines[1:]:
        parts = row.split(",")
        if len(parts) < 6:
            continue
        try:
            open_price = float(parts[1])
            high_price = float(parts[2])
            low_price = float(parts[3])
            close_price = float(parts[4])
            volume = float(parts[5])
        except ValueError:
            continue
        if close_price <= 0:
            continue
        candles.append(
            {
                "date": parts[0],
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        )
    if candles[-count:]:
        LAST_US_DATA_STATUS = {"provider": "stooq", "ok": True, "message": "ok"}
        cache[ticker.upper()] = {"as_of": today, "provider": "stooq", "candles": candles[-count:]}
        _save_us_cache(cache)
        return candles[-count:]
    yahoo_candles = _get_us_daily_prices_yahoo(ticker, count)
    if yahoo_candles:
        LAST_US_DATA_STATUS = {"provider": "yahoo", "ok": True, "message": "ok"}
        cache[ticker.upper()] = {"as_of": today, "provider": "yahoo", "candles": yahoo_candles[-count:]}
        _save_us_cache(cache)
        return yahoo_candles
    LAST_US_DATA_STATUS = {"provider": "stooq/yahoo", "ok": False, "message": "no daily candles available"}
    return []


def _get_us_daily_prices_yahoo(ticker: str, count: int = 60) -> list[dict[str, Any]]:
    try:
        resp = requests.get(
            YAHOO_CHART_URL.format(ticker=ticker.upper()),
            params={"interval": "1d", "range": "6mo", "includePrePost": "false"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
        timestamps = result.get("timestamp") or []
        quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
    except (RequestException, ValueError, TypeError):
        return []

    candles: list[dict[str, Any]] = []
    for idx, timestamp in enumerate(timestamps):
        try:
            open_price = float(opens[idx])
            high_price = float(highs[idx])
            low_price = float(lows[idx])
            close_price = float(closes[idx])
            volume = float(volumes[idx] or 0.0)
        except (TypeError, ValueError, IndexError):
            continue
        if close_price <= 0:
            continue
        candles.append(
            {
                "date": datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date().isoformat(),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        )
    return candles[-count:]


def _get_us_daily_prices_alphavantage(ticker: str, count: int = 60) -> list[dict[str, Any]]:
    try:
        resp = requests.get(
            ALPHAVANTAGE_DAILY_URL,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker.upper(),
                "apikey": settings.alphavantage_api_key,
                "outputsize": "compact",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (RequestException, ValueError):
        return []

    series = payload.get("Time Series (Daily)") or {}
    candles: list[dict[str, Any]] = []
    for date_key in sorted(series.keys()):
        row = series.get(date_key) or {}
        try:
            candles.append(
                {
                    "date": date_key,
                    "open": float(row.get("1. open") or 0.0),
                    "high": float(row.get("2. high") or 0.0),
                    "low": float(row.get("3. low") or 0.0),
                    "close": float(row.get("4. close") or 0.0),
                    "volume": float(row.get("5. volume") or 0.0),
                }
            )
        except ValueError:
            continue
    return [item for item in candles if item["close"] > 0][-count:]


def get_us_core_snapshot(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    symbols = tickers or US_CORE_TICKERS
    leaders: list[dict[str, Any]] = []
    for ticker in symbols:
        candles = get_us_daily_prices(ticker, count=30)
        if len(candles) < 2:
            continue
        last = candles[-1]
        prev_close = float(candles[-2].get("close") or 0.0)
        last_close = float(last.get("close") or 0.0)
        last_volume = float(last.get("volume") or 0.0)
        change_pct = round(((last_close - prev_close) / prev_close) * 100, 2) if prev_close else 0.0
        momentum_20d = round(((last_close - float(candles[-20].get("close") or last_close)) / float(candles[-20].get("close") or last_close)) * 100, 2) if len(candles) >= 20 and float(candles[-20].get("close") or 0.0) else 0.0
        leaders.append(
            {
                "ticker": ticker,
                "name": ticker,
                "current_price": last_close,
                "change_pct": change_pct,
                "momentum_20d_pct": momentum_20d,
                "volume": int(last_volume),
            }
        )
    leaders.sort(key=lambda item: (item.get("momentum_20d_pct", 0.0), item.get("change_pct", 0.0), item.get("volume", 0)), reverse=True)
    return leaders


def get_us_data_status() -> dict[str, Any]:
    return dict(LAST_US_DATA_STATUS)

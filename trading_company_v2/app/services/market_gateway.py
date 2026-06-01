from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests
from requests import RequestException

from app.config import settings
from app.services.upbit_stream_cache import get_cached_ticker_prices, get_cached_ticker_rows

_log = logging.getLogger(__name__)


UPBIT_MARKETS_URL = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"
UPBIT_ORDERBOOK_URL = "https://api.upbit.com/v1/orderbook"
UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
NAVER_KOSDAQ_URL = "https://m.stock.naver.com/api/stock/exchange/KOSDAQ"
NAVER_KOSDAQ_FALLBACK_URL = "https://finance.naver.com/sise/sise_market_sum.naver?sosok=1&page={page}"
NAVER_STOCK_DAY_URL = "https://finance.naver.com/item/sise_day.naver?code={ticker}&page={page}"
# fchart: 1 요청으로 최대 250일 OHLCV (XML 형식) — page-by-page scraping 대체
NAVER_FCHART_URL = "https://fchart.stock.naver.com/sise.nhn?symbol={ticker}&timeframe=day&count=250&requestType=0"
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/?s={ticker}.us&i=d"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
ALPHAVANTAGE_DAILY_URL = "https://www.alphavantage.co/query"
REQUEST_TIMEOUT = 8
US_CORE_TICKERS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA"]
LAST_US_DATA_STATUS: dict[str, Any] = {"provider": "none", "ok": False, "message": "not requested yet"}
US_CACHE_PATH = Path(settings.db_path).resolve().parent / "us_daily_cache.json"

# ── US Market Context — 확장 티커 목록 ────────────────────────────────────────
# 지수, VIX, 섹터 ETF, 메가캡 — get_us_market_context() 에서 사용
_US_CONTEXT_TICKERS: dict[str, str] = {
    # 지수
    "SPY": "S&P500", "QQQ": "NASDAQ", "DIA": "DOW", "IWM": "Russell2000",
    # 공포지수
    "^VIX": "VIX",
    # 섹터 ETF
    "XLK": "Tech", "XLF": "Finance", "XLE": "Energy",
    "XLV": "Healthcare", "XLY": "ConsumerDisc", "XLC": "Comm", "XLI": "Industrial",
    # 메가캡
    "NVDA": "Nvidia", "AAPL": "Apple", "MSFT": "Microsoft",
    "TSLA": "Tesla", "META": "Meta", "AMZN": "Amazon", "GOOGL": "Google",
}
# 15분 캐시 — 사이클마다 재조회 방지
_US_CONTEXT_CACHE: dict[str, Any] = {}
_US_CONTEXT_CACHE_TS: float = 0.0
_US_CONTEXT_TTL: float = 15 * 60  # 15분

# ── Naver daily price cache ───────────────────────────────────────────────────
# 종목별 일봉 데이터를 캐시하여 55초 사이클마다 반복 요청 방지.
# 장중에는 5분 TTL (현재가 반영), 장외에는 1시간 TTL.
_naver_daily_cache: dict[str, tuple[float, list]] = {}  # ticker -> (ts, candles)
_NAVER_DAILY_CACHE_TTL_MARKET = 1800.0   # 30분 (일봉 전략 — 분단위 변화 불필요)
_NAVER_DAILY_CACHE_TTL_OFFHOUR = 3600.0  # 1시간 (장외 시간)

# ── Naver data-failure tracking ──────────────────────────────────────────────
# 연속 실패 횟수를 추적하고 임계값 초과 시 Telegram 알림 발송.
# 순환 임포트를 피하기 위해 notifier는 첫 경고 시점에 lazy-import.
_naver_failure_count: int = 0       # 연속 0-candle / 파싱실패 횟수
_NAVER_FAIL_ALERT_THRESHOLD: int = 5  # 연속 N회 실패 시 알림 발송
_NAVER_FAIL_LAST_ALERTED: int = 0   # 마지막 알림 발송 시점의 실패 횟수 (중복 방지)

def _naver_record_failure(ticker: str) -> None:
    """연속 실패 카운터 증가 + 임계값 초과 시 Telegram 경고."""
    global _naver_failure_count, _NAVER_FAIL_LAST_ALERTED
    _naver_failure_count += 1
    # 임계값의 배수마다 한 번씩 알림 (1차: 5회, 2차: 10회, ...)
    if _naver_failure_count >= _NAVER_FAIL_ALERT_THRESHOLD and (
        _naver_failure_count // _NAVER_FAIL_ALERT_THRESHOLD
        > _NAVER_FAIL_LAST_ALERTED // _NAVER_FAIL_ALERT_THRESHOLD
    ):
        _NAVER_FAIL_LAST_ALERTED = _naver_failure_count
        _log.error(
            "naver_daily_prices: %d consecutive failures (last ticker=%s) — Telegram alert fired",
            _naver_failure_count, ticker,
        )
        try:
            from app.notifier import notifier  # lazy import — avoids circular dep
            notifier.send_ops_alert(
                "Naver data failure",
                [
                    f"연속 실패: {_naver_failure_count}회 (최근 ticker={ticker})",
                    "get_naver_daily_prices()가 빈 캔들을 반환하고 있습니다.",
                    "Naver finance 스크래핑 차단 또는 HTML 구조 변경 가능성 확인 요망.",
                ],
            )
        except Exception as _e:
            _log.warning("naver_failure_alert: notifier call failed: %s", _e)


def _naver_record_success() -> None:
    """성공 시 연속 실패 카운터 리셋."""
    global _naver_failure_count, _NAVER_FAIL_LAST_ALERTED
    if _naver_failure_count > 0:
        _naver_failure_count = 0
        _NAVER_FAIL_LAST_ALERTED = 0


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


def _rank_krw_ticker_rows(tickers: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    max_volume = max((float(item.get("volume_24h_krw", item.get("acc_trade_price_24h", 0.0)) or 0.0) for item in tickers), default=1.0)
    for item in tickers:
        market = str(item.get("market") or "").strip()
        price = float(item.get("trade_price") or 0.0)
        volume_24h = float(item.get("volume_24h_krw", item.get("acc_trade_price_24h", 0.0)) or 0.0)
        change_rate = float(item.get("change_rate", item.get("signed_change_rate", 0.0)) or 0.0)
        if abs(change_rate) <= 1.0 and "signed_change_rate" in item:
            change_rate *= 100
        if not market or price <= 0 or volume_24h <= 0:
            continue
        liquidity_score = min(volume_24h / max_volume, 1.0)
        momentum_score = min(max(change_rate, 0.0) / 12.0, 1.0)
        volatility_score = min(abs(change_rate) / 18.0, 1.0)
        discovery_score = round(
            (liquidity_score * 0.45)
            + (momentum_score * 0.35)
            + (volatility_score * 0.20),
            4,
        )
        rows.append(
            {
                "market": market,
                "trade_price": price,
                "change_rate": round(change_rate, 2),
                "volume_24h_krw": int(volume_24h),
                "discovery_score": discovery_score,
                "source": str(item.get("source") or "upbit_rest"),
            }
        )
    rows.sort(key=lambda item: (item["discovery_score"], item["volume_24h_krw"]), reverse=True)
    return rows[:limit]


def get_top_krw_coins(top_n: int = 10) -> list[dict[str, Any]]:
    cached_rows = get_cached_ticker_rows()
    if len(cached_rows) >= min(top_n, 8):
        leaders = sorted(cached_rows, key=lambda item: int(item.get("volume_24h_krw") or 0), reverse=True)[:top_n]
        return [
            {
                "market": item.get("market"),
                "trade_price": float(item.get("trade_price") or 0),
                "change_rate": round(float(item.get("change_rate") or 0), 2),
                "volume_24h_krw": int(float(item.get("volume_24h_krw") or 0)),
            }
            for item in leaders
        ]
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


def get_krw_crypto_candidates(limit: int = 18) -> list[dict[str, Any]]:
    """
    Scan the full Upbit KRW universe, then return a tradable shortlist.

    We keep the market open to every KRW coin, but only run expensive candle
    and orderbook analysis on the best live candidates for the current cycle.
    """
    cached_rows = get_cached_ticker_rows()
    if len(cached_rows) >= min(limit, 12):
        return _rank_krw_ticker_rows(cached_rows, limit)

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

        return _rank_krw_ticker_rows(tickers, limit)
    except (RequestException, ValueError, TypeError):
        return []


def get_upbit_ticker_prices(markets: list[str]) -> dict[str, float]:
    symbols = [str(item).strip() for item in markets if str(item).strip()]
    if not symbols:
        return {}
    cached = get_cached_ticker_prices(symbols)
    missing = [symbol for symbol in dict.fromkeys(symbols) if symbol not in cached]
    if not missing:
        return cached
    try:
        prices: dict[str, float] = dict(cached)
        for chunk in _chunked(missing, 100):
            resp = requests.get(
                UPBIT_TICKER_URL,
                params={"markets": ",".join(chunk)},
                timeout=min(REQUEST_TIMEOUT, 3),
            )
            resp.raise_for_status()
            for item in resp.json():
                market = str(item.get("market") or "").strip()
                price = float(item.get("trade_price") or 0.0)
                if market and price > 0:
                    prices[market] = price
        return prices
    except (RequestException, ValueError, TypeError):
        return {}


def get_upbit_minute_candles(market: str, unit: int = 15, count: int = 40) -> list[dict[str, Any]]:
    try:
        resp = requests.get(
            UPBIT_CANDLES_URL.format(unit=unit),
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


def get_upbit_15m_candles(market: str, count: int = 40) -> list[dict[str, Any]]:
    return get_upbit_minute_candles(market, unit=15, count=count)


def get_upbit_1m_candles(market: str, count: int = 80) -> list[dict[str, Any]]:
    return get_upbit_minute_candles(market, unit=1, count=count)


def get_upbit_4h_candles(market: str, count: int = 25) -> list[dict[str, Any]]:
    """4시간봉 캔들 (240분봉)."""
    return get_upbit_minute_candles(market, unit=240, count=count)


def get_upbit_daily_candles(market: str, count: int = 220) -> list[dict[str, Any]]:
    """일봉 캔들 (최대 200개/요청, 자동 페이지네이션으로 count개 확보).

    MONGTATA 에어본 전략에 사용 — EMA200 레짐 필터 위해 최소 220개 필요.
    """
    url = "https://api.upbit.com/v1/candles/days"
    all_rows: list[dict] = []
    to_param: str | None = None
    remaining = count
    try:
        while remaining > 0:
            params: dict[str, Any] = {"market": market, "count": min(remaining, 200)}
            if to_param:
                params["to"] = to_param
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            all_rows.extend(rows)
            remaining -= len(rows)
            if len(rows) < 200:
                break
            to_param = rows[-1].get("candle_date_time_utc")
            if not to_param:
                break
    except RequestException:
        pass
    # Upbit returns newest-first → reverse to oldest-first
    all_rows.sort(key=lambda r: r.get("candle_date_time_utc", ""))
    return [
        {
            "date": row.get("candle_date_time_kst"),
            "open": float(row.get("opening_price") or 0),
            "high": float(row.get("high_price") or 0),
            "low": float(row.get("low_price") or 0),
            "close": float(row.get("trade_price") or 0),
            "volume": float(row.get("candle_acc_trade_volume") or 0),
        }
        for row in all_rows
    ]


def get_upbit_orderbook(market: str) -> dict[str, Any]:
    try:
        resp = requests.get(
            UPBIT_ORDERBOOK_URL,
            params={"markets": market},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return {}
        row = rows[0]
        return {
            "market": row.get("market") or market,
            "timestamp": row.get("timestamp"),
            "total_ask_size": float(row.get("total_ask_size") or 0.0),
            "total_bid_size": float(row.get("total_bid_size") or 0.0),
            "orderbook_units": [
                {
                    "ask_price": float(unit.get("ask_price") or 0.0),
                    "bid_price": float(unit.get("bid_price") or 0.0),
                    "ask_size": float(unit.get("ask_size") or 0.0),
                    "bid_size": float(unit.get("bid_size") or 0.0),
                }
                for unit in row.get("orderbook_units", [])[:15]
            ],
        }
    except (RequestException, ValueError, TypeError):
        return {}


def get_kosdaq_snapshot(top_n: int = 20, top_n_down: int = 0) -> list[dict[str, Any]]:
    # The previous mobile API endpoint now returns 404 on Oracle; use the
    # stable HTML fallback directly to keep the loop quiet and predictable.
    return _get_kosdaq_snapshot_from_naver_html(top_n, top_n_down)


def _strip_html(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _to_number(value: str) -> float:
    cleaned = value.replace(",", "").replace("%", "").strip()
    return float(cleaned) if cleaned else 0.0


def _get_kosdaq_snapshot_from_naver_html(top_n: int, top_n_down: int = 0) -> list[dict[str, Any]]:
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
    result = candidates[:top_n]
    if top_n_down > 0:
        gap_down = sorted(
            [c for c in candidates if c["gap_pct"] < -0.5],
            key=lambda item: (item["gap_pct"], -item["volume"]),
        )[:top_n_down]
        result = result + gap_down
    return result


_PINNED_CRYPTO = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-AVAX", "KRW-TRX", "KRW-LINK",
]


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

    # 신호 엔진이 분석하는 전체 후보군(get_krw_crypto_candidates)에서 가격 정보를 가져와
    # crypto_leaders에 없는 코인을 자동으로 추가한다.
    # 이유: 신규 상장 코인 또는 거래량 상위 20위 밖의 코인이 시그널을 발생시켜도
    #       reference_price=0 → unsupported_order_shape 로 주문이 불가한 문제 해결.
    # get_krw_crypto_candidates는 캐시된 ticker_rows를 재사용하므로 추가 API 호출 없음.
    leader_markets = {item["market"] for item in crypto_leaders}
    try:
        candidates = get_krw_crypto_candidates(limit=50)
        for item in candidates:
            market = str(item.get("market") or "")
            price = float(item.get("trade_price") or 0)
            if market and price > 0 and market not in leader_markets:
                crypto_leaders.append({
                    "market": market,
                    "trade_price": price,
                    "change_rate": round(float(item.get("change_rate") or 0), 2),
                    "volume_24h_krw": int(float(item.get("volume_24h_krw") or 0)),
                })
                leader_markets.add(market)
    except Exception:
        pass
    active_desks = settings.active_desk_set
    stock_leaders = get_kosdaq_snapshot(top_n=30) if "korea" in active_desks else []
    us_leaders = get_us_core_snapshot() if "us" in active_desks else []
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


_naver_intraday_cache: dict[str, tuple[float, float]] = {}  # ticker -> (ts, change_pct)

def get_naver_intraday_change(ticker: str) -> float:
    """당일 등락률(%) 실시간 조회 — Naver mobile API.

    진입 전 "이미 너무 올랐는지" 체크용 (뒤늦게 올라타기 방지).
    캐시 TTL: 60초 (사이클 간 재조회 최소화).
    실패 시 0.0 반환 (차단하지 않음 — fail-open).
    """
    _now_ts = time.monotonic()
    _cached = _naver_intraday_cache.get(ticker)
    if _cached and (_now_ts - _cached[0]) < 60.0:
        return _cached[1]
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/basic"
        resp = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
        # fluctuationsRatio: 등락률 문자열 (예: "+2.34" / "-1.52")
        ratio = float(data.get("fluctuationsRatio", 0) or 0)
        _naver_intraday_cache[ticker] = (_now_ts, ratio)
        return ratio
    except Exception:
        return 0.0


def get_naver_daily_prices(ticker: str, count: int = 20) -> list[dict[str, Any]]:
    if not ticker:
        return []

    # ── 캐시 확인 ──────────────────────────────────────────────────────────
    _now_ts = time.monotonic()
    _cached = _naver_daily_cache.get(ticker)
    if _cached:
        _cached_ts, _cached_candles = _cached
        # 장중(09:00~15:30 KST = 00:00~06:30 UTC)이면 5분 TTL, 장외는 1시간 TTL
        try:
            from zoneinfo import ZoneInfo
            _kst_h = datetime.now(ZoneInfo("Asia/Seoul")).hour
        except Exception:
            _kst_h = 12  # fallback: assume market hours
        _ttl = _NAVER_DAILY_CACHE_TTL_MARKET if 9 <= _kst_h < 16 else _NAVER_DAILY_CACHE_TTL_OFFHOUR
        if (_now_ts - _cached_ts) < _ttl and len(_cached_candles) >= count:
            return list(_cached_candles)[-count:]

    candles: list[dict[str, Any]] = []

    # ── Primary: fchart API (1 요청 = 250일 OHLCV, XML 형식) ─────────────────
    # sise_day page-by-page scraping(22페이지×40종목) 대신 1 요청으로 처리
    try:
        resp = requests.get(
            NAVER_FCHART_URL.format(ticker=ticker),
            headers=NAVER_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        xml_text = resp.content.decode("euc-kr", errors="ignore")
        # <item data="YYYYMMDD|open|high|low|close|volume" />
        for m in re.finditer(r'<item\s+data="(\d{8})\|([^"]+)"', xml_text):
            parts = m.group(2).split("|")
            if len(parts) < 5:
                continue
            try:
                candles.append({
                    "date": m.group(1),
                    "open":   float(parts[0]),
                    "high":   float(parts[1]),
                    "low":    float(parts[2]),
                    "close":  float(parts[3]),
                    "volume": float(parts[4]),
                })
            except (ValueError, IndexError):
                continue
    except RequestException as exc:
        _log.warning("naver_daily_prices fchart ticker=%s failed: %s", ticker, exc)

    # ── Fallback: sise_day page-by-page (fchart 실패 시) ────────────────────
    if not candles:
        _rows_per_page = 10
        _max_pages = max(4, count // _rows_per_page + 3)
        for page in range(1, _max_pages + 1):
            try:
                resp = requests.get(
                    NAVER_STOCK_DAY_URL.format(ticker=ticker, page=page),
                    headers=NAVER_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                html = resp.content.decode("euc-kr", errors="ignore")
            except RequestException as exc:
                _log.warning("naver_daily_prices sise_day ticker=%s page=%d failed: %s", ticker, page, exc)
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
                    candles.append({
                        "date": date_text,
                        "open": _to_number(open_text),
                        "high": _to_number(high_text),
                        "low": _to_number(low_text),
                        "close": _to_number(close_text),
                        "volume": _to_number(volume_text),
                    })
                except (TypeError, ValueError):
                    continue
                if len(candles) >= count:
                    break
            if len(candles) >= count:
                break
        candles.reverse()  # sise_day는 최신→과거 순이므로 역정렬

    result = candles[-count:] if len(candles) > count else candles
    # 실패 감지: 최소 5개 캔들도 반환되지 않으면 데이터 소스 이상으로 간주
    if len(result) < 5:
        _naver_record_failure(ticker)
    else:
        _naver_record_success()
        # 캐시 저장 (성공 시만)
        _naver_daily_cache[ticker] = (time.monotonic(), result)
    return result


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
    # Stooq URL: 지수 티커(^로 시작)는 .us 접미사 없이 조회 (예: ^vix, ^spx)
    _stooq_sym = ticker.lower()
    if _stooq_sym.startswith("^"):
        _stooq_url = f"https://stooq.com/q/d/l/?s={_stooq_sym}&i=d"
    else:
        _stooq_url = STOOQ_DAILY_URL.format(ticker=_stooq_sym)
    try:
        resp = requests.get(_stooq_url, timeout=REQUEST_TIMEOUT)
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
        from urllib.parse import quote as _url_quote
        # Yahoo Finance: ^ 문자를 %5E로 URL 인코딩해야 정상 조회됨 (예: ^VIX → %5EVIX)
        _encoded = _url_quote(ticker.upper(), safe="")
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{_encoded}",
            params={"interval": "1d", "range": "6mo", "includePrePost": "false"},
            headers={"User-Agent": "Mozilla/5.0"},
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


def get_us_current_prices(tickers: list[str]) -> dict[str, float]:
    """Yahoo Finance에서 미국 주식 현재가(실시간) 일괄 조회.

    Yahoo `/v8/finance/chart` 엔드포인트의 meta.regularMarketPrice 사용.
    장중/장전/장후 모두 최신 거래가를 반환.
    """
    from urllib.parse import quote as _url_quote
    prices: dict[str, float] = {}
    for ticker in tickers:
        try:
            encoded = _url_quote(ticker.upper(), safe="")
            resp = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}",
                params={"interval": "1m", "range": "1d", "includePrePost": "true"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
            meta = result.get("meta") or {}
            price = float(
                meta.get("regularMarketPrice")
                or meta.get("chartPreviousClose")
                or 0.0
            )
            if price > 0:
                prices[ticker.upper()] = price
        except Exception:
            pass
    return prices


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


def get_us_market_context(force: bool = False) -> dict[str, Any]:
    """미국 시장 컨텍스트 — 전 에이전트 공유 (15분 캐시).

    Returns:
        indices   : SPY/QQQ/DIA/IWM 등락률
        vix       : VIX 레벨 + 변화율 + 레짐(calm/neutral/elevated/fear/panic)
        sectors   : 섹터별 등락률 + 상위/하위 섹터
        megacaps  : NVDA/AAPL/MSFT/TSLA/META/AMZN/GOOGL 등락률
        us_regime : "risk_on" / "neutral" / "risk_off"
        summary   : 한 줄 요약 문자열
    """
    import time as _time

    global _US_CONTEXT_CACHE, _US_CONTEXT_CACHE_TS
    now_ts = _time.time()
    if not force and _US_CONTEXT_CACHE and (now_ts - _US_CONTEXT_CACHE_TS) < _US_CONTEXT_TTL:
        return dict(_US_CONTEXT_CACHE)

    # ── 전 티커 일봉 조회 ────────────────────────────────────────────────────
    raw: dict[str, dict] = {}
    for sym in _US_CONTEXT_TICKERS:
        try:
            candles = get_us_daily_prices(sym, count=5)
            if len(candles) >= 2:
                c0 = float(candles[-1]["close"])
                c1 = float(candles[-2]["close"])
                chg = round((c0 - c1) / c1 * 100, 2) if c1 else 0.0
                raw[sym] = {"price": c0, "chg": chg}
        except Exception:
            pass

    def _chg(sym: str) -> float:
        return float((raw.get(sym) or {}).get("chg", 0.0))

    def _price(sym: str) -> float:
        return float((raw.get(sym) or {}).get("price", 0.0))

    # ── VIX 레짐 ─────────────────────────────────────────────────────────────
    vix_level = _price("^VIX")
    vix_chg   = _chg("^VIX")
    if vix_level <= 0:
        vix_regime = "unknown"
    elif vix_level < 15:
        vix_regime = "calm"       # 극단적 공포 없음 — risk-on
    elif vix_level < 20:
        vix_regime = "neutral"
    elif vix_level < 25:
        vix_regime = "elevated"   # 주의
    elif vix_level < 30:
        vix_regime = "fear"       # 공포 — 포지션 축소
    else:
        vix_regime = "panic"      # 패닉 — 진입 차단

    # ── 섹터 분석 ─────────────────────────────────────────────────────────────
    sector_keys = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLC", "XLI"]
    sector_map  = {_US_CONTEXT_TICKERS[k]: _chg(k) for k in sector_keys if k in raw}
    sector_leader  = max(sector_map, key=sector_map.get) if sector_map else ""
    sector_laggard = min(sector_map, key=sector_map.get) if sector_map else ""

    # ── US 레짐 판단 ──────────────────────────────────────────────────────────
    spy_chg = _chg("SPY")
    qqq_chg = _chg("QQQ")
    up_count   = sum(1 for s in ["SPY","QQQ","DIA","IWM"] if _chg(s) > 0)
    down_count = sum(1 for s in ["SPY","QQQ","DIA","IWM"] if _chg(s) < 0)
    if vix_regime == "unknown":
        # VIX fetch 실패 시: 지수 방향만으로 레짐 판단 (VIX 가용 시 덮어씀)
        if up_count >= 3:
            us_regime = "risk_on"
        elif down_count >= 3:
            us_regime = "risk_off"
        else:
            us_regime = "neutral"
    elif up_count >= 3 and vix_chg <= 0 and vix_regime in ("calm", "neutral"):
        us_regime = "risk_on"
    elif down_count >= 3 or vix_regime in ("fear", "panic"):
        us_regime = "risk_off"
    else:
        us_regime = "neutral"

    # ── 한 줄 요약 ───────────────────────────────────────────────────────────
    vix_str = f"VIX={vix_level:.1f}({vix_regime})" if vix_level > 0 else "VIX=n/a"
    summary = (
        f"US {us_regime.upper()}: "
        f"SPY{spy_chg:+.1f}% QQQ{qqq_chg:+.1f}% {vix_str} "
        f"↑{sector_leader} ↓{sector_laggard}"
    )

    ctx = {
        # 지수
        "spy_chg":  spy_chg,
        "qqq_chg":  qqq_chg,
        "dia_chg":  _chg("DIA"),
        "iwm_chg":  _chg("IWM"),
        # VIX
        "vix":       vix_level,
        "vix_chg":   vix_chg,
        "vix_regime": vix_regime,   # calm/neutral/elevated/fear/panic
        # 섹터
        "sectors":        sector_map,           # {"Tech": +1.2, "Finance": -0.3, ...}
        "sector_leader":  sector_leader,
        "sector_laggard": sector_laggard,
        "tech_leading":   bool(_chg("XLK") > spy_chg),
        # 메가캡
        "nvda_chg":  _chg("NVDA"),
        "aapl_chg":  _chg("AAPL"),
        "msft_chg":  _chg("MSFT"),
        "tsla_chg":  _chg("TSLA"),
        "meta_chg":  _chg("META"),
        "amzn_chg":  _chg("AMZN"),
        "googl_chg": _chg("GOOGL"),
        # 종합
        "us_regime": us_regime,     # risk_on / neutral / risk_off
        "summary":   summary,
        "raw":       raw,           # 원시 가격 데이터 (디버그용)
    }

    _US_CONTEXT_CACHE    = ctx
    _US_CONTEXT_CACHE_TS = now_ts
    return ctx

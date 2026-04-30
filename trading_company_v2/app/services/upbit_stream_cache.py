from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

import requests

from app.config import settings

try:
    import websockets
except Exception:  # pragma: no cover - optional dependency guard
    websockets = None


UPBIT_MARKETS_URL = "https://api.upbit.com/v1/market/all"
UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
REQUEST_TIMEOUT = 8

_lock = threading.Lock()
_ticker_cache: dict[str, dict[str, Any]] = {}
_trade_ticks: dict[str, deque[dict[str, Any]]] = {}
_stream_thread: threading.Thread | None = None
_stream_started_at = 0.0
_stream_error = ""


def _now() -> float:
    return time.time()


def _default_krw_markets_provider() -> list[str]:
    try:
        resp = requests.get(UPBIT_MARKETS_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        markets = [
            str(item.get("market") or "").strip()
            for item in resp.json()
            if str(item.get("market") or "").startswith("KRW-")
        ]
        return markets[: max(1, int(settings.upbit_ws_codes_limit))]
    except Exception:
        return []


def _normalize_ticker_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    market = str(payload.get("code") or payload.get("cd") or "").strip()
    if not market:
        return None
    try:
        trade_price = float(payload.get("trade_price", payload.get("tp")) or 0.0)
    except (TypeError, ValueError):
        trade_price = 0.0
    if trade_price <= 0:
        return None
    try:
        change_rate = float(payload.get("signed_change_rate", payload.get("scr")) or 0.0) * 100
    except (TypeError, ValueError):
        change_rate = 0.0
    try:
        volume_24h = float(payload.get("acc_trade_price_24h", payload.get("atp24h")) or 0.0)
    except (TypeError, ValueError):
        volume_24h = 0.0
    try:
        trade_ts = int(payload.get("trade_timestamp", payload.get("ttms")) or 0)
    except (TypeError, ValueError):
        trade_ts = 0
    try:
        trade_volume = float(payload.get("trade_volume", payload.get("tv")) or 0.0)
    except (TypeError, ValueError):
        trade_volume = 0.0
    return {
        "market": market,
        "trade_price": trade_price,
        "change_rate": round(change_rate, 4),
        "volume_24h_krw": int(volume_24h),
        "trade_volume": trade_volume,
        "ask_bid": str(payload.get("ask_bid", payload.get("ab")) or "").upper(),
        "trade_timestamp": trade_ts,
        "received_at": _now(),
        "source": "upbit_ws",
    }


def _normalize_trade_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    market = str(payload.get("code") or payload.get("cd") or "").strip()
    if not market:
        return None
    try:
        trade_price = float(payload.get("trade_price", payload.get("tp")) or 0.0)
    except (TypeError, ValueError):
        trade_price = 0.0
    if trade_price <= 0:
        return None
    try:
        trade_ts = int(payload.get("trade_timestamp", payload.get("ttms")) or 0)
    except (TypeError, ValueError):
        trade_ts = 0
    try:
        trade_volume = float(payload.get("trade_volume", payload.get("tv")) or 0.0)
    except (TypeError, ValueError):
        trade_volume = 0.0
    return {
        "market": market,
        "trade_price": trade_price,
        "trade_volume": trade_volume,
        "ask_bid": str(payload.get("ask_bid", payload.get("ab")) or "").upper(),
        "trade_timestamp": trade_ts,
        "received_at": _now(),
        "source": "upbit_ws_trade",
    }


def _append_trade_tick(row: dict[str, Any]) -> None:
    ticks = _trade_ticks.setdefault(row["market"], deque(maxlen=360))
    ticks.append(
        {
            "received_at": row["received_at"],
            "price": row["trade_price"],
            "volume": row.get("trade_volume", 0.0),
            "ask_bid": row.get("ask_bid", ""),
        }
    )
    cutoff = row["received_at"] - 180.0
    while ticks and float(ticks[0].get("received_at") or 0.0) < cutoff:
        ticks.popleft()


async def _stream_loop(markets_provider: Callable[[], list[str]]) -> None:
    global _stream_error
    backoff = 1.0
    while True:
        markets = [item for item in dict.fromkeys(markets_provider()) if item.startswith("KRW-")]
        if not markets:
            _stream_error = "no KRW markets available"
            await asyncio.sleep(min(backoff, 30.0))
            backoff = min(backoff * 1.5, 30.0)
            continue
        try:
            if websockets is None:
                _stream_error = "websockets package is not installed"
                await asyncio.sleep(30.0)
                continue
            async with websockets.connect(
                UPBIT_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_queue=2048,
            ) as ws:
                request = [
                    {"ticket": f"trading-company-{uuid.uuid4()}"},
                    {"type": "ticker", "codes": markets, "is_only_realtime": True},
                    {"type": "trade", "codes": markets, "is_only_realtime": True},
                    {"format": "DEFAULT"},
                ]
                await ws.send(json.dumps(request))
                _stream_error = ""
                backoff = 1.0
                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        payload = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    message_type = str(payload.get("type") or payload.get("ty") or "").lower()
                    with _lock:
                        if message_type == "trade":
                            row = _normalize_trade_message(payload)
                            if row is None:
                                continue
                            cached = dict(_ticker_cache.get(row["market"]) or {})
                            cached.update(
                                {
                                    "market": row["market"],
                                    "trade_price": row["trade_price"],
                                    "trade_volume": row["trade_volume"],
                                    "ask_bid": row["ask_bid"],
                                    "trade_timestamp": row["trade_timestamp"],
                                    "received_at": row["received_at"],
                                }
                            )
                            cached.setdefault("source", "upbit_ws")
                            _ticker_cache[row["market"]] = cached
                            _append_trade_tick(row)
                        else:
                            row = _normalize_ticker_message(payload)
                            if row is None:
                                continue
                            _ticker_cache[row["market"]] = row
        except Exception as exc:
            _stream_error = str(exc)[:240]
            await asyncio.sleep(min(backoff, 30.0))
            backoff = min(backoff * 1.5, 30.0)


def _thread_main(markets_provider: Callable[[], list[str]]) -> None:
    asyncio.run(_stream_loop(markets_provider))


def start_upbit_ticker_stream(markets_provider: Callable[[], list[str]] | None = None) -> bool:
    """Start a background Upbit ticker stream once per process."""
    global _stream_thread, _stream_started_at
    if not settings.upbit_ws_enabled:
        return False
    if _stream_thread and _stream_thread.is_alive():
        return True
    provider = markets_provider or _default_krw_markets_provider
    _stream_started_at = _now()
    _stream_thread = threading.Thread(
        target=_thread_main,
        args=(provider,),
        name="upbit-ticker-stream",
        daemon=True,
    )
    _stream_thread.start()
    return True


def get_cached_ticker_rows(max_age_seconds: float | None = None) -> list[dict[str, Any]]:
    max_age = float(settings.upbit_ws_fresh_seconds if max_age_seconds is None else max_age_seconds)
    cutoff = _now() - max_age
    with _lock:
        return [dict(row) for row in _ticker_cache.values() if float(row.get("received_at") or 0.0) >= cutoff]


def get_cached_ticker_prices(markets: list[str], max_age_seconds: float | None = None) -> dict[str, float]:
    symbols = {str(item).strip() for item in markets if str(item).strip()}
    if not symbols:
        return {}
    max_age = float(settings.upbit_ws_fresh_seconds if max_age_seconds is None else max_age_seconds)
    cutoff = _now() - max_age
    with _lock:
        result: dict[str, float] = {}
        for symbol in symbols:
            row = _ticker_cache.get(symbol) or {}
            if float(row.get("received_at") or 0.0) < cutoff:
                continue
            price = float(row.get("trade_price") or 0.0)
            if price > 0:
                result[symbol] = price
        return result


def _price_at_or_before(ticks: list[dict[str, Any]], timestamp: float) -> float:
    price = float(ticks[0].get("price") or 0.0) if ticks else 0.0
    for tick in ticks:
        tick_time = float(tick.get("received_at") or 0.0)
        if tick_time > timestamp:
            break
        value = float(tick.get("price") or 0.0)
        if value > 0:
            price = value
    return price


def summarize_stream_momentum(market: str, max_age_seconds: float | None = None) -> dict[str, Any]:
    """Summarize sub-minute momentum from the in-process Upbit ticker stream."""
    symbol = str(market or "").strip()
    if not symbol:
        return {"stream_fresh": False, "stream_score": 0.0, "stream_reasons": ["no market"]}
    max_age = float(settings.upbit_ws_fresh_seconds if max_age_seconds is None else max_age_seconds)
    now = _now()
    with _lock:
        ticks = list(_trade_ticks.get(symbol) or [])
    if len(ticks) < 2:
        return {"stream_fresh": False, "stream_score": 0.0, "stream_reasons": ["not enough stream ticks"]}

    latest = ticks[-1]
    latest_time = float(latest.get("received_at") or 0.0)
    latest_price = float(latest.get("price") or 0.0)
    age = now - latest_time
    if latest_price <= 0 or age > max_age:
        return {
            "stream_fresh": False,
            "stream_score": 0.0,
            "stream_age_seconds": round(age, 3),
            "stream_reasons": [f"stream stale ({age:.2f}s)"],
        }

    def _move(seconds: float) -> float:
        ref = _price_at_or_before(ticks, now - seconds)
        return ((latest_price - ref) / ref * 100) if ref > 0 else 0.0

    move_5 = _move(5.0)
    move_15 = _move(15.0)
    move_60 = _move(60.0)
    ticks_15 = [tick for tick in ticks if float(tick.get("received_at") or 0.0) >= now - 15.0]
    buy_ticks = [tick for tick in ticks_15 if str(tick.get("ask_bid") or "").upper() == "BID"]
    buy_ratio = len(buy_ticks) / len(ticks_15) if ticks_15 else 0.0

    score = 0.20
    reasons: list[str] = []
    if move_5 >= 0.12:
        score += 0.18
        reasons.append(f"5s stream lift {move_5:.2f}%")
    elif move_5 <= -0.18:
        score -= 0.16
        reasons.append(f"5s stream fade {move_5:.2f}%")
    if move_15 >= 0.30:
        score += 0.24
        reasons.append(f"15s stream ignition {move_15:.2f}%")
    elif move_15 <= -0.35:
        score -= 0.22
        reasons.append(f"15s stream reversal {move_15:.2f}%")
    if move_60 >= 0.75:
        score += 0.18
        reasons.append(f"60s stream trend {move_60:.2f}%")
    elif move_60 <= -0.80:
        score -= 0.18
        reasons.append(f"60s stream downtrend {move_60:.2f}%")
    if len(ticks_15) >= 3:
        score += 0.08
        reasons.append(f"stream activity {len(ticks_15)} ticks/15s")
    if buy_ratio >= 0.58:
        score += 0.12
        reasons.append(f"stream buy pressure {buy_ratio:.0%}")
    elif buy_ratio <= 0.35 and len(ticks_15) >= 3:
        score -= 0.10
        reasons.append(f"stream sell pressure {buy_ratio:.0%}")

    score = round(max(0.0, min(1.0, score)), 3)
    ignition = score >= 0.62 and move_15 >= 0.25 and move_60 >= -0.15 and len(ticks_15) >= 2
    reversal = move_15 <= -0.35 or (move_5 <= -0.20 and buy_ratio <= 0.40)
    if not reasons:
        reasons.append("stream neutral")
    return {
        "stream_fresh": True,
        "stream_score": score,
        "stream_ignition": ignition,
        "stream_reversal": reversal,
        "stream_age_seconds": round(age, 3),
        "stream_move_5s_pct": round(move_5, 3),
        "stream_move_15s_pct": round(move_15, 3),
        "stream_move_60s_pct": round(move_60, 3),
        "stream_ticks_15s": len(ticks_15),
        "stream_buy_ratio_15s": round(buy_ratio, 3),
        "stream_reasons": reasons,
    }


def upbit_stream_status() -> dict[str, Any]:
    now = _now()
    with _lock:
        latest_age = min(
            (round(now - float(row.get("received_at") or 0.0), 3) for row in _ticker_cache.values()),
            default=None,
        )
        cached_count = len(_ticker_cache)
        tick_count = sum(len(ticks) for ticks in _trade_ticks.values())
    return {
        "enabled": bool(settings.upbit_ws_enabled),
        "running": bool(_stream_thread and _stream_thread.is_alive()),
        "cached_count": cached_count,
        "tick_count": tick_count,
        "latest_age_seconds": latest_age,
        "started_at_epoch": _stream_started_at,
        "last_error": _stream_error,
    }

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
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
    return {
        "market": market,
        "trade_price": trade_price,
        "change_rate": round(change_rate, 4),
        "volume_24h_krw": int(volume_24h),
        "trade_timestamp": trade_ts,
        "received_at": _now(),
        "source": "upbit_ws",
    }


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
                    row = _normalize_ticker_message(payload)
                    if row is None:
                        continue
                    with _lock:
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


def upbit_stream_status() -> dict[str, Any]:
    now = _now()
    with _lock:
        latest_age = min(
            (round(now - float(row.get("received_at") or 0.0), 3) for row in _ticker_cache.values()),
            default=None,
        )
        cached_count = len(_ticker_cache)
    return {
        "enabled": bool(settings.upbit_ws_enabled),
        "running": bool(_stream_thread and _stream_thread.is_alive()),
        "cached_count": cached_count,
        "latest_age_seconds": latest_age,
        "started_at_epoch": _stream_started_at,
        "last_error": _stream_error,
    }

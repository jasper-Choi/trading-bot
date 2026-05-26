"""
KIS 실시간 체결 틱 스트림 (H0STCNT0)

- WebSocket으로 관심 종목의 체결 틱을 수신
- 종목당 최근 300틱 ring buffer 보관
- 아침 오픈 리버설(매도 소진 → 반전) 감지 신호 제공

구조: upbit_stream_cache.py와 동일 (asyncio loop → background thread)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import requests

from app.config import settings

try:
    import websockets
except Exception:
    websockets = None

_log = logging.getLogger(__name__)

# ── KIS WebSocket 엔드포인트 ────────────────────────────────────────────────
_KIS_WS_LIVE = "ws://ops.koreainvestment.com:21000"
_KIS_WS_MOCK = "ws://ops.koreainvestment.com:31000"
_KIS_APPROVAL_LIVE = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
_KIS_APPROVAL_MOCK = "https://openapivts.koreainvestment.com:29443/oauth2/Approval"

_TICK_BUFFER = 300       # 종목당 보관 틱 수
_RECONNECT_DELAY = 5.0   # 재연결 대기(초)
_REQUEST_TIMEOUT = 8

# ── 전역 상태 ────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_tick_cache: dict[str, deque[dict[str, Any]]] = {}   # ticker → deque of ticks
_orderbook_cache: dict[str, dict[str, Any]] = {}     # ticker → latest orderbook snapshot
_subscribed: set[str] = set()
_stream_thread: threading.Thread | None = None
_stream_error: str = ""
_stream_started_at: float = 0.0
_approval_key: str = ""
_approval_key_ts: float = 0.0
_APPROVAL_KEY_TTL = 82800.0  # 23시간

# ── 틱 콜백 (upbit_stream_cache.register_trade_callback 과 동일 구조) ─────────
_tick_callbacks: list[Callable[[dict[str, Any]], None]] = []

# H0STCNT0 응답 필드 인덱스 ('^' 분리 후)
_F_CODE   = 0   # 종목코드
_F_TIME   = 1   # 체결시간 HHMMSS
_F_PRICE  = 2   # 현재가
_F_OPEN   = 7   # 시가
_F_HIGH   = 8   # 고가
_F_LOW    = 9   # 저가
_F_VOL    = 12  # 체결거래량
_F_SIDE   = 21  # 체결구분 1=매수 5=매도 3=장전시간외

# H0STASP0 호가 필드 인덱스 ('^' 분리 후)
# ask(매도) 1~5호가 잔량: fields 13..17 (KIS 실전 스펙 기준)
# bid(매수) 1~5호가 잔량: fields 32..36
# 총매도잔량: field 52 / 총매수잔량: field 53
_ASP_F_CODE      = 0
_ASP_F_TOTAL_ASK = 52   # 총매도잔량
_ASP_F_TOTAL_BID = 53   # 총매수잔량


def _is_mock() -> bool:
    return bool(settings.kis_mock)


def _ws_url() -> str:
    return _KIS_WS_MOCK if _is_mock() else _KIS_WS_LIVE


def _approval_url() -> str:
    return _KIS_APPROVAL_MOCK if _is_mock() else _KIS_APPROVAL_LIVE


# ── Approval Key ─────────────────────────────────────────────────────────────
def _get_approval_key(force: bool = False) -> str:
    global _approval_key, _approval_key_ts
    with _lock:
        age = time.monotonic() - _approval_key_ts
        if not force and _approval_key and age < _APPROVAL_KEY_TTL:
            return _approval_key
    try:
        resp = requests.post(
            _approval_url(),
            json={
                "grant_type": "client_credentials",
                "appkey": settings.kis_app_key,
                "secretkey": settings.kis_app_secret,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        key = str(resp.json().get("approval_key", ""))
        if key:
            with _lock:
                _approval_key = key
                _approval_key_ts = time.monotonic()
            _log.info("KIS stream approval_key refreshed")
            return key
    except Exception as exc:
        _log.warning("KIS approval_key failed: %s", exc)
    return ""


# ── 틱 파싱 ─────────────────────────────────────────────────────────────────
def _parse_tick(raw: str) -> dict[str, Any] | None:
    """H0STCNT0 단일 레코드('^' 분리 문자열) → tick dict"""
    parts = raw.split("^")
    if len(parts) < 22:
        return None
    try:
        side_raw = parts[_F_SIDE].strip()
        # 1=매수(buy), 5=매도(sell), 3=장전/기타
        if side_raw == "1":
            side = "buy"
        elif side_raw == "5":
            side = "sell"
        else:
            side = "neutral"
        return {
            "ticker": parts[_F_CODE].strip(),
            "time": parts[_F_TIME].strip(),
            "price": float(parts[_F_PRICE] or 0),
            "open_price": float(parts[_F_OPEN] or 0),
            "high": float(parts[_F_HIGH] or 0),
            "low": float(parts[_F_LOW] or 0),
            "volume": int(float(parts[_F_VOL] or 0)),
            "side": side,
            "ts": time.time(),
        }
    except (ValueError, IndexError):
        return None


def _parse_orderbook(raw: str) -> dict[str, Any] | None:
    """H0STASP0 단일 레코드 → orderbook dict (총매수/매도잔량)."""
    parts = raw.split("^")
    if len(parts) < 54:
        return None
    try:
        return {
            "ticker": parts[_ASP_F_CODE].strip(),
            "total_ask": int(float(parts[_ASP_F_TOTAL_ASK] or 0)),
            "total_bid": int(float(parts[_ASP_F_TOTAL_BID] or 0)),
            "ts": time.time(),
        }
    except (ValueError, IndexError):
        return None


def _process_message(raw_text: str) -> None:
    """WebSocket 메시지 처리 (PINGPONG 무시, 틱/호가 파싱 후 캐시 저장)"""
    if not raw_text or "PINGPONG" in raw_text:
        return
    # 포맷: {recv_type}|{tr_id}|{tr_cnt}|{data}
    parts = raw_text.split("|", 3)
    if len(parts) < 4:
        return
    tr_id = parts[1].strip()
    try:
        cnt = int(parts[2].strip())
    except ValueError:
        cnt = 1
    data_block = parts[3]
    records = data_block.strip().split("\n") if cnt > 1 else [data_block]

    if tr_id == "H0STCNT0":
        fired_ticks: list[dict[str, Any]] = []
        for record in records:
            tick = _parse_tick(record.strip())
            if tick is None:
                continue
            ticker = tick["ticker"]
            with _lock:
                if ticker not in _tick_cache:
                    _tick_cache[ticker] = deque(maxlen=_TICK_BUFFER)
                _tick_cache[ticker].append(tick)
            fired_ticks.append(tick)
        # ── 콜백 발화 (lock 밖에서, tick guard 지연 방지) ──────────────────
        if fired_ticks:
            with _lock:
                callbacks = list(_tick_callbacks)
            for tick in fired_ticks:
                for cb in callbacks:
                    try:
                        cb(dict(tick))
                    except Exception as exc:
                        _log.debug("KIS tick callback error: %s", exc)

    elif tr_id == "H0STASP0":
        for record in records:
            ob = _parse_orderbook(record.strip())
            if ob is None:
                continue
            ticker = ob["ticker"]
            with _lock:
                _orderbook_cache[ticker] = ob


# ── WebSocket 비동기 루프 ─────────────────────────────────────────────────────
def _subscribe_msg(approval_key: str, ticker: str, tr_id: str = "H0STCNT0") -> str:
    return json.dumps({
        "header": {
            "approval_key": approval_key,
            "custtype": "P",
            "tr_type": "1",
            "content-type": "utf-8",
        },
        "body": {
            "input": {
                "tr_id": tr_id,
                "tr_key": ticker,
            }
        },
    })


async def _stream_loop() -> None:
    global _stream_error, _stream_started_at
    while True:
        approval_key = _get_approval_key()
        if not approval_key:
            _log.warning("KIS stream: no approval key, retrying in %ss", _RECONNECT_DELAY)
            await asyncio.sleep(_RECONNECT_DELAY)
            continue
        with _lock:
            tickers = list(_subscribed)
        if not tickers:
            await asyncio.sleep(2.0)
            continue
        url = _ws_url()
        try:
            _log.info("KIS stream connecting to %s for %d ticker(s)", url, len(tickers))
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                _stream_started_at = time.monotonic()
                _stream_error = ""
                for ticker in tickers:
                    await ws.send(_subscribe_msg(approval_key, ticker, "H0STCNT0"))
                    await ws.send(_subscribe_msg(approval_key, ticker, "H0STASP0"))
                    _log.debug("KIS stream subscribed: %s", ticker)
                async for message in ws:
                    if isinstance(message, bytes):
                        message = message.decode("utf-8", errors="ignore")
                    _process_message(message)
                    # 구독 목록 변경 시 새 종목 추가
                    with _lock:
                        new = _subscribed - set(tickers)
                    for t in new:
                        await ws.send(_subscribe_msg(approval_key, t, "H0STCNT0"))
                        await ws.send(_subscribe_msg(approval_key, t, "H0STASP0"))
                        tickers.append(t)
                        _log.debug("KIS stream subscribed (live): %s", t)
        except Exception as exc:
            _stream_error = str(exc)
            _log.warning("KIS stream disconnected: %s — reconnecting in %ss", exc, _RECONNECT_DELAY)
        await asyncio.sleep(_RECONNECT_DELAY)


def _run_loop() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_stream_loop())


# ── 공개 API ─────────────────────────────────────────────────────────────────
def ensure_stream_started() -> None:
    global _stream_thread
    if websockets is None:
        return
    if not settings.kis_app_key or not settings.kis_app_secret:
        return
    with _lock:
        if _stream_thread and _stream_thread.is_alive():
            return
        _stream_thread = threading.Thread(target=_run_loop, daemon=True, name="kis-stream")
        _stream_thread.start()
        _log.info("KIS stream thread started")


def subscribe_tickers(tickers: list[str]) -> None:
    """관심 종목 구독 추가 (스트림 자동 시작)"""
    with _lock:
        for t in tickers:
            _subscribed.add(t.strip())
    ensure_stream_started()


def get_tick_buffer(ticker: str) -> list[dict[str, Any]]:
    """최근 최대 300틱 반환 (오래된 것 먼저)"""
    with _lock:
        buf = _tick_cache.get(ticker.strip())
        return list(buf) if buf else []


def get_orderbook_imbalance(ticker: str) -> float:
    """매수 잔량 비율 0.0~1.0. 0.5=균형, >0.6=매수 우세, <0.4=매도 우세.

    H0STASP0 데이터 미수신 시 0.5 반환.
    """
    with _lock:
        ob = _orderbook_cache.get(ticker.strip())
    if ob is None:
        return 0.5
    total_ask = ob.get("total_ask", 0)
    total_bid = ob.get("total_bid", 0)
    total = total_ask + total_bid
    if total == 0:
        return 0.5
    return round(total_bid / total, 3)


def register_tick_callback(callback: Callable[[dict[str, Any]], None]) -> None:
    """H0STCNT0 체결 틱마다 호출되는 콜백 등록 (upbit_stream_cache 와 동일 구조).

    콜백 인수: dict with keys ticker, time, price, volume, side, ts
    """
    with _lock:
        if callback not in _tick_callbacks:
            _tick_callbacks.append(callback)


def get_latest_prices(tickers: list[str]) -> dict[str, float]:
    """구독 중인 종목의 최신 체결가 반환. {ticker: price}

    틱이 없거나 가격이 0이면 포함하지 않음.
    """
    result: dict[str, float] = {}
    with _lock:
        for ticker in tickers:
            buf = _tick_cache.get(ticker.strip())
            if buf:
                p = float(buf[-1].get("price", 0.0) or 0.0)
                if p > 0:
                    result[ticker.strip()] = p
    return result


def get_stream_status() -> dict[str, Any]:
    with _lock:
        return {
            "active": bool(_stream_thread and _stream_thread.is_alive()),
            "subscribed": sorted(_subscribed),
            "ticker_count": len(_tick_cache),
            "total_ticks": sum(len(v) for v in _tick_cache.values()),
            "error": _stream_error,
            "uptime_s": round(time.monotonic() - _stream_started_at, 1) if _stream_started_at else 0,
        }


# ── 오픈 리버설 신호 분석 ────────────────────────────────────────────────────
def get_opening_reversal_signal(ticker: str) -> dict[str, Any]:
    """
    아침 매도 소진 → 반전 신호 감지.

    반환:
        cascade:    True = 오픈 후 강한 매도 쏟아짐 (매도 65%↑, 가격 -0.8%↓)
        exhaustion: True = 매도 소진 징후 (최근 10틱 매도비율 하락, 틱볼륨 수축)
        reversal:   True = 반전 시작 (최근 5틱 매수 우세, 저점 위)
        score:      0~100 (100에 가까울수록 진입 적합)
        price_vs_open_pct: 현재가 vs 시가 %
        session_low_pct:   저점 vs 시가 %
        sell_ratio_30:     최근 30틱 매도 비율
        sell_ratio_10:     최근 10틱 매도 비율
        tick_count:        보유 틱 수
    """
    ticks = get_tick_buffer(ticker)
    empty = {
        "cascade": False, "exhaustion": False, "reversal": False,
        "score": 0, "price_vs_open_pct": 0.0, "session_low_pct": 0.0,
        "sell_ratio_30": 0.0, "sell_ratio_10": 0.0, "tick_count": 0,
    }
    if len(ticks) < 10:
        return empty

    open_price = ticks[0].get("open_price") or ticks[0]["price"]
    if not open_price:
        return empty

    current_price = ticks[-1]["price"]
    session_low = min(t["price"] for t in ticks)
    price_vs_open_pct = (current_price - open_price) / open_price * 100
    session_low_pct = (session_low - open_price) / open_price * 100

    # ── 30틱 매도 비율 ──────────────────────────────────────────────────────
    last30 = ticks[-30:]
    sell30 = sum(t["volume"] for t in last30 if t["side"] == "sell")
    buy30  = sum(t["volume"] for t in last30 if t["side"] == "buy")
    total30 = sell30 + buy30
    sell_ratio_30 = sell30 / total30 if total30 > 0 else 0.5

    # ── 10틱 매도 비율 (최근) ───────────────────────────────────────────────
    last10 = ticks[-10:]
    sell10 = sum(t["volume"] for t in last10 if t["side"] == "sell")
    buy10  = sum(t["volume"] for t in last10 if t["side"] == "buy")
    total10 = sell10 + buy10
    sell_ratio_10 = sell10 / total10 if total10 > 0 else 0.5

    # ── 5틱 (최근) ──────────────────────────────────────────────────────────
    last5 = ticks[-5:]
    sell5 = sum(t["volume"] for t in last5 if t["side"] == "sell")
    buy5  = sum(t["volume"] for t in last5 if t["side"] == "buy")

    # ── 틱볼륨 수축 여부 (소진 감지) ────────────────────────────────────────
    avg_vol_30 = (sell30 + buy30) / max(len(last30), 1)
    avg_vol_10 = (sell10 + buy10) / max(len(last10), 1)
    vol_contracting = avg_vol_10 < avg_vol_30 * 0.65

    # ── 패턴 판별 ───────────────────────────────────────────────────────────
    cascade    = sell_ratio_30 >= 0.65 and price_vs_open_pct <= -0.8
    exhaustion = cascade and sell_ratio_10 < 0.52 and vol_contracting
    reversal   = buy5 > sell5 and current_price > session_low * 1.001

    # ── 호가 잔량 (H0STASP0) ────────────────────────────────────────────────
    bid_ratio = get_orderbook_imbalance(ticker)
    orderbook_bullish = bid_ratio >= 0.60   # 매수 잔량이 60% 이상

    # ── 점수 (0~100) ─────────────────────────────────────────────────────────
    score = 0
    if cascade:
        score += 30
        score += min(20, int(abs(price_vs_open_pct) * 8))
    if exhaustion:
        score += 25
    if reversal:
        score += 25
    if sell_ratio_10 < sell_ratio_30 - 0.10:
        score += 10
    if orderbook_bullish:
        score += 10   # 매수잔량 우세 = 추가 확인
    score = min(score, 100)

    return {
        "cascade": cascade,
        "exhaustion": exhaustion,
        "reversal": reversal,
        "score": score,
        "price_vs_open_pct": round(price_vs_open_pct, 3),
        "session_low_pct": round(session_low_pct, 3),
        "sell_ratio_30": round(sell_ratio_30, 3),
        "sell_ratio_10": round(sell_ratio_10, 3),
        "tick_count": len(ticks),
        "bid_ratio": round(bid_ratio, 3),
        "orderbook_bullish": orderbook_bullish,
    }

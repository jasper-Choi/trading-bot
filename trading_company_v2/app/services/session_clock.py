from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz

from app.config import settings


def _resolve_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return pytz.timezone(name)


def _is_between(current: time, start: time, end: time) -> bool:
    return start <= current <= end


def _market_label(korea_open: bool, us_premarket: bool, us_regular: bool, crypto_focus: bool) -> str:
    if korea_open:
        return "korea_regular"
    if us_regular:
        return "us_regular"
    if us_premarket:
        return "us_premarket"
    if crypto_focus:
        return "crypto_only"
    return "watch"


def current_session_snapshot() -> dict:
    local_tz = _resolve_timezone(settings.timezone)
    us_tz = _resolve_timezone("America/New_York")
    korea_tz = _resolve_timezone("Asia/Seoul")

    now_local = datetime.now(local_tz)
    now_us = now_local.astimezone(us_tz)
    now_korea = now_local.astimezone(korea_tz)

    korea_open = _is_between(now_korea.time(), time(9, 0), time(15, 30))
    korea_opening_window = _is_between(now_korea.time(), time(9, 0), time(10, 0))
    korea_mid_session = _is_between(now_korea.time(), time(10, 0), time(14, 30))
    us_premarket = _is_between(now_us.time(), time(4, 0), time(9, 30))
    us_regular = _is_between(now_us.time(), time(9, 30), time(16, 0))
    crypto_focus = not korea_open

    active_desks: list[str] = []
    if korea_open:
        active_desks.append("korea_stock_desk")
    if crypto_focus:
        active_desks.append("crypto_desk")
    if us_premarket or us_regular:
        active_desks.append("us_stock_desk")

    return {
        "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": settings.timezone,
        "korea_time": now_korea.strftime("%Y-%m-%d %H:%M:%S"),
        "us_time": now_us.strftime("%Y-%m-%d %H:%M:%S"),
        "korea_open": korea_open,
        "korea_opening_window": korea_opening_window,
        "korea_mid_session": korea_mid_session,
        "us_premarket": us_premarket,
        "us_regular": us_regular,
        "crypto_focus": crypto_focus,
        "active_desks": active_desks,
        "market_phase": _market_label(korea_open, us_premarket, us_regular, crypto_focus),
    }

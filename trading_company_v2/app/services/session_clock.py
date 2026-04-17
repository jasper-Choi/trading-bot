from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings


def current_session_snapshot() -> dict:
    now = datetime.now(ZoneInfo(settings.timezone))
    hour_minute = now.hour * 100 + now.minute

    korea_open = 900 <= hour_minute <= 1530
    us_premarket = 1700 <= hour_minute <= 2230
    us_regular = hour_minute >= 2230 or hour_minute <= 500
    crypto_focus = not korea_open

    active_desks: list[str] = []
    if korea_open:
        active_desks.append("korea_stock_desk")
    if crypto_focus:
        active_desks.append("crypto_desk")
    if us_premarket or us_regular:
        active_desks.append("macro_watch")

    return {
        "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": settings.timezone,
        "korea_open": korea_open,
        "us_premarket": us_premarket,
        "us_regular": us_regular,
        "crypto_focus": crypto_focus,
        "active_desks": active_desks,
    }


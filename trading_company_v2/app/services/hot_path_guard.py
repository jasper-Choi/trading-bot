from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.state_store import (
    PaperPositionRecord,
    SessionLocal,
    _crypto_no_lift_exit_reason,
    _crypto_trail_rules,
    _paper_net_pnl_pct,
    _position_thresholds,
    init_db,
    rapid_guard_crypto_positions,
)
from app.services.upbit_stream_cache import summarize_stream_momentum


_lock = threading.Lock()
_cache: dict[str, list[dict[str, Any]]] = {}
_loaded_at = 0.0


def _minutes_open(opened_at: str) -> float:
    try:
        opened_dt = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        if opened_dt.tzinfo is None:
            opened_dt = opened_dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
    except (ValueError, TypeError):
        return 0.0


def refresh_hot_crypto_positions(force: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Refresh open crypto paper positions into memory for the tick hot path."""
    global _cache, _loaded_at
    now = time.monotonic()
    with _lock:
        if not force and now - _loaded_at <= 1.0:
            return {key: [dict(item) for item in value] for key, value in _cache.items()}
    init_db()
    next_cache: dict[str, list[dict[str, Any]]] = {}
    with SessionLocal() as db:
        rows = db.execute(
            select(PaperPositionRecord).where(
                PaperPositionRecord.status == "open",
                PaperPositionRecord.desk == "crypto",
            )
        ).scalars().all()
        for row in rows:
            item = {
                "id": int(row.id),
                "symbol": str(row.symbol or ""),
                "desk": str(row.desk or ""),
                "action": str(row.action or ""),
                "entry_price": float(row.entry_price or 0.0),
                "current_price": float(row.current_price or 0.0),
                "pnl_pct": float(row.pnl_pct or 0.0),
                "peak_pnl_pct": float(row.peak_pnl_pct or 0.0),
                "opened_at": str(row.opened_at or ""),
            }
            if item["symbol"] and item["entry_price"] > 0:
                next_cache.setdefault(item["symbol"], []).append(item)
    with _lock:
        _cache = next_cache
        _loaded_at = now
        return {key: [dict(item) for item in value] for key, value in _cache.items()}


def hot_guard_symbols() -> set[str]:
    return set(refresh_hot_crypto_positions().keys())


def _update_cached_position(symbol: str, position_id: int, pnl_pct: float, peak_pnl: float, current_price: float) -> None:
    with _lock:
        for item in _cache.get(symbol, []):
            if int(item.get("id", 0) or 0) == position_id:
                item["pnl_pct"] = pnl_pct
                item["peak_pnl_pct"] = peak_pnl
                item["current_price"] = current_price
                return


def hot_guard_crypto_tick(symbol: str, price: float) -> dict[str, Any]:
    """Evaluate one crypto symbol from memory; touch DB only when a close is required."""
    if not symbol or price <= 0:
        return {"checked": 0, "paper_closed": 0, "live_closed": 0, "reason": "invalid_tick"}
    positions = refresh_hot_crypto_positions().get(symbol, [])
    if not positions:
        return {"checked": 0, "paper_closed": 0, "live_closed": 0, "reason": "no_open_position"}
    checked = 0
    for item in positions:
        checked += 1
        entry_price = float(item.get("entry_price", 0.0) or 0.0)
        pnl_pct = _paper_net_pnl_pct(entry_price, price, symbol, "hot")
        peak_pnl = max(float(item.get("peak_pnl_pct", 0.0) or 0.0), pnl_pct)
        target_pct, stop_pct, _ = _position_thresholds("crypto", str(item.get("action") or ""))
        trail_giveback, profit_floor = _crypto_trail_rules(peak_pnl)
        protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
        minutes_open = _minutes_open(str(item.get("opened_at") or ""))
        reason = ""
        if pnl_pct >= target_pct:
            reason = "rapid_target_hit"
        elif 0.40 <= peak_pnl < 0.80 and minutes_open >= 1.0 and pnl_pct <= max(-0.55, peak_pnl - 1.10):
            reason = "failed_breakout_exit"
        else:
            stream = summarize_stream_momentum(symbol, max_age_seconds=3.5)
            if (
                bool(stream.get("stream_reversal", False))
                and minutes_open >= 0.5
                and pnl_pct <= 0.15
                and (
                    (peak_pnl <= 0.15 and pnl_pct <= -0.12)
                    or (peak_pnl >= 0.20 and pnl_pct <= max(-0.15, peak_pnl - 0.55))
                )
            ):
                reason = "rapid_tick_failed_start" if peak_pnl <= 0.15 else "rapid_tick_reversal"
        if not reason and pnl_pct <= stop_pct:
            reason = "rapid_stop_hit"
        if not reason and minutes_open >= 4.0 and peak_pnl <= 0.05 and pnl_pct <= -0.75:
            reason = "rapid_failed_start"
        if not reason and (no_lift_reason := _crypto_no_lift_exit_reason(minutes_open, peak_pnl, pnl_pct, rapid=True)):
            reason = no_lift_reason
        if not reason and trail_giveback and pnl_pct <= protect_level:
            reason = "rapid_profit_protect" if peak_pnl < 1.8 else "rapid_trend_trail"
        if reason:
            result = rapid_guard_crypto_positions({symbol: price})
            refresh_hot_crypto_positions(force=True)
            return {
                "checked": checked,
                "paper_closed": int(result.get("paper_closed", 0) or 0),
                "live_closed": int(result.get("live_closed", 0) or 0),
                "reason": reason,
            }
        _update_cached_position(symbol, int(item.get("id", 0) or 0), pnl_pct, peak_pnl, price)
    return {"checked": checked, "paper_closed": 0, "live_closed": 0, "reason": "checked_memory"}

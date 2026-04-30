from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.models import PaperOrder
from app.core.state_store import (
    PaperOrderRecord,
    PaperPositionRecord,
    SessionLocal,
    _crypto_no_lift_exit_reason,
    _crypto_trail_rules,
    _notify_trade_entry,
    _paper_entry_price,
    _paper_net_pnl_pct,
    _paper_trade_payload,
    _position_thresholds,
    init_db,
    rapid_guard_crypto_positions,
)
from app.services.upbit_stream_cache import summarize_stream_momentum


_lock = threading.Lock()
_cache: dict[str, list[dict[str, Any]]] = {}
_loaded_at = 0.0
_entry_candidates: dict[str, dict[str, Any]] = {}
_entry_loaded_at = 0.0
_entry_last_opened_by_symbol: dict[str, float] = {}
_ENTRY_CANDIDATE_TTL_SECONDS = 18.0
_ENTRY_COOLDOWN_SECONDS = 75.0
_MAX_HOT_OPEN_POSITIONS = 5
_MAX_HOT_OPEN_NOTIONAL = 1.15


def _minutes_open(opened_at: str) -> float:
    try:
        opened_dt = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        if opened_dt.tzinfo is None:
            opened_dt = opened_dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
    except (ValueError, TypeError):
        return 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _size_to_notional(size: str) -> float:
    return _float(str(size).replace("x", ""), 0.0)


def _hot_entry_size(candidate: dict[str, Any], stream: dict[str, Any]) -> float:
    combined = _float(candidate.get("combined_score", candidate.get("signal_score", 0.0)))
    trend = _float(candidate.get("trend_follow_score", 0.0))
    chart = _float(candidate.get("signal_score", 0.0))
    stream_score = _float(stream.get("stream_score", 0.0))
    entry_profile = str(candidate.get("entry_profile", "") or "")
    if entry_profile == "obvious_trend":
        if chart >= 0.84 and trend >= 0.82 and stream_score >= 0.55:
            return 0.12
        if chart >= 0.78 and trend >= 0.76:
            return 0.09
        return 0.07
    if entry_profile == "range_impulse":
        if combined >= 0.62 and stream_score >= 0.84:
            return 0.04
        return 0.03
    if combined >= 0.86 and trend >= 0.82 and stream_score >= 0.76:
        return 0.12
    if combined >= 0.78 and stream_score >= 0.70:
        return 0.09
    return 0.06


def _open_position_summary() -> tuple[int, float]:
    positions = refresh_hot_crypto_positions()
    open_count = sum(len(items) for items in positions.values())
    open_notional = 0.0
    for items in positions.values():
        for item in items:
            open_notional += _size_to_notional(str(item.get("size", "0.00x") or "0.00x"))
    return open_count, round(open_notional, 4)


def _candidate_is_hot_entry_eligible(item: dict[str, Any]) -> bool:
    symbol = str(item.get("market") or item.get("symbol") or "").strip()
    if not symbol.startswith("KRW-"):
        return False
    combined = _float(item.get("combined_score", item.get("signal_score", 0.0)))
    trend_score = _float(item.get("trend_follow_score", 0.0))
    chart_score = _float(item.get("signal_score", 0.0))
    trend_alignment = str(item.get("trend_alignment", "") or "")
    orderbook_bid_ask = _float(item.get("orderbook_bid_ask_ratio", 0.0))
    signal_freshness = _float(item.get("signal_freshness", 1.0), 1.0)
    micro_move_3 = _float(item.get("micro_move_3_pct", 0.0))
    micro_vwap_gap = _float(item.get("micro_vwap_gap_pct", 0.0))
    recent_change = _float(item.get("recent_change_pct", 0.0))
    burst_change = _float(item.get("burst_change_pct", 0.0))
    change_rate = _float(item.get("change_rate", 0.0))
    ema_gap = _float(item.get("ema_gap_pct", 0.0))
    rsi = item.get("rsi")
    rsi_value = _float(rsi, 0.0) if rsi is not None else 0.0
    trend_extension_pct = _float(item.get("trend_extension_pct", 0.0))
    hard_overheat = recent_change >= 12.0 or burst_change >= 10.0 or ema_gap >= 8.0 or rsi_value >= 92.0
    common_guards = (
        signal_freshness >= 0.55
        and -0.45 <= micro_move_3 <= 1.20
        and micro_vwap_gap <= 1.80
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
    )
    # Standard path: full EMA stack confirmed, price not overextended
    standard_ok = (
        bool(item.get("trend_entry_allowed", False))
        and trend_alignment in {"trend_long", "pullback_long"}
        and trend_score >= 0.76
        and combined >= 0.72
        and orderbook_bid_ask >= 1.08
        and trend_extension_pct <= 3.0
    )
    # Early trend path: CHoCH/BOS structural break before EMA stack catches up
    # Requires stricter score + stronger orderbook since structure is less confirmed
    early_ok = (
        bool(item.get("trend_early_entry", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.70
        and combined >= 0.74
        and orderbook_bid_ask >= 1.20
        and trend_extension_pct <= 2.0
    )
    # RANGING impulse path:
    # In box/ranging markets, scanner leaders can show weak orderbook at the snapshot
    # but still be worth arming if the chart impulse is strong. We do NOT open on the
    # snapshot; we only subscribe them for a stricter tick-ignition trigger.
    range_impulse_ok = (
        trend_alignment in {"trend_long", "pullback_long", "range"}
        and chart_score >= 0.74
        and combined >= 0.38
        and max(recent_change, change_rate) >= 3.0
        and signal_freshness >= 0.55
        and trend_extension_pct <= 7.0
        and rsi_value <= 82.0
        and micro_vwap_gap <= 4.2
        and not bool(item.get("rsi_bearish_divergence", False))
    )
    # Obvious trend path:
    # If the 15m chart is already in a clear rising trigger, do not bury it
    # behind orderbook/micro snapshot gates. The websocket still checks that
    # the current tick is not an immediate sell reversal before opening.
    obvious_top_risk = ema_gap >= 10.0 or rsi_value >= 88.0 or bool(item.get("rsi_bearish_divergence", False))
    obvious_trend_ok = (
        trend_alignment in {"trend_long", "pullback_long", "range"}
        and (bool(item.get("trend_entry_allowed", False)) or bool(item.get("trend_early_entry", False)) or trend_score >= 0.76)
        and chart_score >= 0.76
        and max(recent_change, change_rate, burst_change) >= 2.0
        and signal_freshness >= 0.50
        and trend_extension_pct <= 8.5
        and micro_vwap_gap <= 6.5
        and not obvious_top_risk
    )
    if common_guards and (standard_ok or early_ok):
        item["entry_profile"] = "trend_ignition"
        return True
    if obvious_trend_ok:
        item["entry_profile"] = "obvious_trend"
        return True
    if range_impulse_ok:
        item["entry_profile"] = "range_impulse"
        return True
    return False


def refresh_hot_entry_candidates(state: dict[str, Any] | None = None, force: bool = False) -> dict[str, dict[str, Any]]:
    """Refresh tick-entry candidates from the latest agent cycle.

    The cycle does structural work; this cache only lets the websocket tick stream
    fire immediately when a prepared candidate shows fresh ignition.
    """
    global _entry_candidates, _entry_loaded_at
    now = time.monotonic()
    if state is None:
        with _lock:
            if not force and now - _entry_loaded_at <= _ENTRY_CANDIDATE_TTL_SECONDS:
                return {key: dict(value) for key, value in _entry_candidates.items()}
            if now - _entry_loaded_at > _ENTRY_CANDIDATE_TTL_SECONDS:
                return {}
            return {key: dict(value) for key, value in _entry_candidates.items()}

    desk_views = state.get("desk_views", {}) or {}
    crypto_view = desk_views.get("crypto_desk", {}) or {}
    raw_candidates = list(crypto_view.get("all_candidates") or crypto_view.get("candidate_markets") or [])
    prepared: dict[str, dict[str, Any]] = {}
    for item in raw_candidates:
        if not isinstance(item, dict) or not _candidate_is_hot_entry_eligible(item):
            continue
        symbol = str(item.get("market") or item.get("symbol") or "").strip()
        prepared[symbol] = {
            **item,
            "symbol": symbol,
            "loaded_at": now,
        }
        if len(prepared) >= 10:
            break
    with _lock:
        _entry_candidates = prepared
        _entry_loaded_at = now
        return {key: dict(value) for key, value in _entry_candidates.items()}


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
                "size": str(row.size or "0.00x"),
                "focus": str(row.focus or ""),
            }
            if item["symbol"] and item["entry_price"] > 0:
                next_cache.setdefault(item["symbol"], []).append(item)
    with _lock:
        _cache = next_cache
        _loaded_at = now
        return {key: [dict(item) for item in value] for key, value in _cache.items()}


def hot_guard_symbols() -> set[str]:
    return set(refresh_hot_crypto_positions().keys())


def hot_runtime_symbols() -> set[str]:
    """Symbols that deserve websocket callbacks: open positions + prepared entries."""
    return set(refresh_hot_crypto_positions().keys()) | set(refresh_hot_entry_candidates().keys())


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
        is_range_impulse = "range_impulse" in str(item.get("focus", "") or "")
        reason = ""
        if pnl_pct >= target_pct:
            reason = "rapid_target_hit"
        elif is_range_impulse and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.25:
            reason = "rapid_range_impulse_fail"
        elif is_range_impulse and pnl_pct <= -0.40:
            reason = "rapid_range_impulse_fail"
        elif is_range_impulse and peak_pnl >= 0.28 and pnl_pct <= max(0.02, peak_pnl - 0.35):
            reason = "rapid_range_impulse_protect"
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


def _open_hot_entry(symbol: str, price: float, candidate: dict[str, Any], stream: dict[str, Any]) -> dict[str, Any]:
    open_count, open_notional = _open_position_summary()
    size_notional = _hot_entry_size(candidate, stream)
    if open_count >= _MAX_HOT_OPEN_POSITIONS:
        return {"entry_opened": 0, "reason": "entry_position_cap"}
    if open_notional + size_notional > _MAX_HOT_OPEN_NOTIONAL:
        return {"entry_opened": 0, "reason": "entry_exposure_cap"}

    now = time.monotonic()
    last_opened = _entry_last_opened_by_symbol.get(symbol, 0.0)
    if now - last_opened < _ENTRY_COOLDOWN_SECONDS:
        return {"entry_opened": 0, "reason": "entry_symbol_cooldown"}
    _entry_last_opened_by_symbol[symbol] = now

    combined = _float(candidate.get("combined_score", candidate.get("signal_score", 0.0)))
    trend = _float(candidate.get("trend_follow_score", 0.0))
    micro = _float(candidate.get("micro_score", 0.0))
    orderbook_score = _float(candidate.get("orderbook_score", 0.0))
    orderbook_bid_ask = _float(candidate.get("orderbook_bid_ask_ratio", 0.0))
    stream_score = _float(stream.get("stream_score", 0.0))
    meta = {
        "symbol": symbol,
        "reference_price": price,
        "notional_pct": size_notional,
        "combined_score": round(combined, 3),
        "signal_score": round(combined, 3),
        "micro_score": round(micro, 3),
        "orderbook_score": round(orderbook_score, 3),
        "orderbook_bid_ask_ratio": round(orderbook_bid_ask, 3),
        "stream_score": round(stream_score, 3),
        "stream_ignition": bool(stream.get("stream_ignition", False)),
        "stream_reversal": bool(stream.get("stream_reversal", False)),
        "stream_move_15s_pct": _float(stream.get("stream_move_15s_pct", 0.0)),
        "stream_buy_ratio_15s": _float(stream.get("stream_buy_ratio_15s", 0.0)),
        "trend_follow_score": round(trend, 3),
        "trend_alignment": str(candidate.get("trend_alignment", "") or ""),
        "trend_entry_allowed": bool(candidate.get("trend_entry_allowed", False)),
        "bias": str(candidate.get("bias", "") or ""),
        "entry_path": str(candidate.get("entry_profile", "tick_ignition_entry") or "tick_ignition_entry"),
        "status": "planned",
    }
    order = PaperOrder(
        desk="crypto",
        action="probe_longs",
        focus=(
            f"{symbol} {meta['entry_path']} tick entry - combined {combined:.2f}, "
            f"stream {stream_score:.2f}, move15 {_float(stream.get('stream_move_15s_pct', 0.0)):.2f}%."
        ),
        size=f"{size_notional:.2f}x",
        symbol=symbol,
        reference_price=price,
        notional_pct=size_notional,
        status="planned",
        rationale=[
            meta,
            "tick ignition opened from websocket trade stream after cycle-prepared trend candidate",
            f"stream buy {_float(stream.get('stream_buy_ratio_15s', 0.0)):.0%}, ticks15 {int(stream.get('stream_ticks_15s', 0) or 0)}",
        ],
    )
    entry_price = _paper_entry_price(price, symbol, order.created_at)
    opened_payload: dict[str, Any] | None = None
    init_db()
    with SessionLocal() as db:
        existing = db.execute(
            select(PaperPositionRecord).where(
                PaperPositionRecord.status == "open",
                PaperPositionRecord.desk == "crypto",
                PaperPositionRecord.symbol == symbol,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"entry_opened": 0, "reason": "entry_already_open"}
        db.add(
            PaperOrderRecord(
                created_at=order.created_at,
                desk=order.desk,
                action=order.action,
                focus=order.focus,
                size=order.size,
                rationale=order.rationale,
            )
        )
        position = PaperPositionRecord(
            desk="crypto",
            symbol=symbol,
            status="open",
            action=order.action,
            size=order.size,
            opened_at=order.created_at,
            entry_price=entry_price,
            current_price=price,
            pnl_pct=0.0,
            peak_pnl_pct=0.0,
            cycles_open=0,
            focus=order.focus,
        )
        db.add(position)
        db.flush()
        opened_payload = _paper_trade_payload(position, meta)
        db.commit()
    if opened_payload is not None:
        _notify_trade_entry(opened_payload)
    refresh_hot_crypto_positions(force=True)
    return {
        "entry_opened": 1,
        "reason": "tick_entry_opened",
        "size": size_notional,
        "combined_score": round(combined, 3),
        "stream_score": round(stream_score, 3),
    }


def hot_process_crypto_tick(symbol: str, price: float) -> dict[str, Any]:
    """Process one trade tick for exits first, then prepared tick-ignition entries."""
    guard_summary = hot_guard_crypto_tick(symbol, price)
    if guard_summary.get("checked") or guard_summary.get("paper_closed") or guard_summary.get("live_closed"):
        return {**guard_summary, "entry_opened": 0}
    candidate = refresh_hot_entry_candidates().get(symbol)
    if not candidate:
        return {**guard_summary, "entry_opened": 0}
    if refresh_hot_crypto_positions().get(symbol):
        return {**guard_summary, "entry_opened": 0, "reason": "entry_already_open"}
    stream = summarize_stream_momentum(symbol, max_age_seconds=3.5)
    ticks_15 = int(stream.get("stream_ticks_15s", 0) or 0)
    stream_score = _float(stream.get("stream_score", 0.0))
    move_15 = _float(stream.get("stream_move_15s_pct", 0.0))
    move_60 = _float(stream.get("stream_move_60s_pct", 0.0))
    move_5 = _float(stream.get("stream_move_5s_pct", 0.0))
    buy_ratio = _float(stream.get("stream_buy_ratio_15s", 0.0))
    entry_profile = str(candidate.get("entry_profile", "trend_ignition") or "trend_ignition")
    stream_ok = bool(stream.get("stream_fresh", False)) and not bool(stream.get("stream_reversal", False))
    if entry_profile == "obvious_trend":
        ignition = (
            stream_ok
            and ticks_15 >= 1
            and move_15 >= -0.18
            and move_60 >= -0.28
            and buy_ratio >= 0.42
        )
    elif entry_profile == "range_impulse":
        ignition = (
            stream_ok
            and ticks_15 >= 4
            and stream_score >= 0.76
            and move_5 >= 0.12
            and 0.35 <= move_15 <= 1.15
            and move_60 >= -0.08
            and buy_ratio >= 0.64
        )
    else:
        ignition = (
            stream_ok
            and ticks_15 >= 3
            and stream_score >= 0.70
            and move_5 >= 0.08
            and 0.28 <= move_15 <= 0.85
            and move_60 >= -0.12
            and buy_ratio >= 0.60
        )
    if not ignition:
        return {**guard_summary, "entry_opened": 0, "reason": "entry_wait_tick_ignition"}
    return {**guard_summary, **_open_hot_entry(symbol, price, candidate, stream)}

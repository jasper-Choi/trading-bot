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
    _range_scalp_trail_rules,
    _range_scalp_no_lift_exit,
    init_db,
    load_strategy_performance_stats,
    rapid_guard_crypto_positions,
    save_shadow_signal,
)
from app.services.upbit_stream_cache import summarize_stream_momentum


_lock = threading.Lock()
_cache: dict[str, list[dict[str, Any]]] = {}
_loaded_at = 0.0
_entry_candidates: dict[str, dict[str, Any]] = {}
_entry_loaded_at = 0.0
_entry_last_opened_by_symbol: dict[str, float] = {}
_ENTRY_CANDIDATE_TTL_SECONDS = 18.0
_ENTRY_COOLDOWN_SECONDS = 150.0          # 75s → 150s: 같은 코인 재진입 최소 2.5분 대기
_MAX_HOT_OPEN_POSITIONS = 4             # 5 → 4: 동시 오픈 포지션 제한
_MAX_HOT_OPEN_NOTIONAL = 1.00           # 1.15 → 1.00: 전체 노출 축소
_ENABLE_EXPERIMENTAL_IMPULSE_ENTRIES = False
_HOT_RECENT_FAILURE_REASONS = {
    "rapid_tick_failed_start",
    "rapid_obvious_trend_fail",
    "rapid_range_impulse_fail",
    "rapid_range_breakout_fail",
    "rapid_high_tight_flag_fail",
    "rapid_ema_cross_fail",
    "rapid_vwap_reclaim_fail",
    "rapid_failed_start",
    "rapid_stop_hit",
    "rapid_repeat_symbol_failure",
    "rapid_range_scalp_stop",
    "rapid_range_scalp_no_lift",
}
# 실패 후 블랙리스트 쿨다운 (초): 연속 2회 실패 시 이 시간만큼 재진입 차단
_FAILURE_BLACKLIST_SECONDS = 360.0      # 6분
_failure_blacklist: dict[str, float] = {}
_STRATEGY_BLOCKLIST_TTL_SECONDS = 60.0
_strategy_blocklist_loaded_at = 0.0
_strategy_blocklist: set[str] = set()


def _disabled_strategy_ids(force: bool = False) -> set[str]:
    """Cache disabled strategies so websocket ticks do not hit SQLite per tick."""
    global _strategy_blocklist_loaded_at, _strategy_blocklist
    now = time.monotonic()
    if not force and now - _strategy_blocklist_loaded_at < _STRATEGY_BLOCKLIST_TTL_SECONDS:
        return set(_strategy_blocklist)
    try:
        _strategy_blocklist = {
            str(item.get("strategy_id", "") or "")
            for item in load_strategy_performance_stats(window=80)
            if item.get("health") == "disabled_candidate" and str(item.get("strategy_id", "") or "")
        }
    except Exception:
        # Hot path must never stall because diagnostics failed.
        pass
    _strategy_blocklist_loaded_at = now
    return set(_strategy_blocklist)


def _strategy_is_disabled(strategy_id: str) -> bool:
    return bool(strategy_id) and strategy_id in _disabled_strategy_ids()


def _strategy_id_for_entry_profile(entry_profile: str) -> str:
    profile = str(entry_profile or "").strip() or "tick_ignition"
    if profile == "trend_ignition":
        return "crypto.tick_ignition"
    return f"crypto.{profile}"


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
    if entry_profile == "range_breakout":
        if combined >= 0.60 and stream_score >= 0.82:
            return 0.06
        return 0.04
    if entry_profile == "high_tight_flag":
        if combined >= 0.58 and stream_score >= 0.78:
            return 0.05
        return 0.035
    if entry_profile == "ema_cross":
        if combined >= 0.62 and stream_score >= 0.72:
            return 0.06
        return 0.045
    if entry_profile == "vwap_reclaim":
        if combined >= 0.60 and stream_score >= 0.70:
            return 0.055
        return 0.04
    if entry_profile == "range_scalp":
        airborne_score = _float(candidate.get("airborne_score", 0.0))
        if airborne_score >= 0.70:
            return 0.06
        return 0.04
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
    regime = str(item.get("regime", "") or "")
    range_scalp_eligible = bool(item.get("range_scalp_eligible", False))
    airborne_long = bool(item.get("airborne_long", False))
    airborne_score = _float(item.get("airborne_score", 0.0))
    hard_overheat = recent_change >= 12.0 or burst_change >= 10.0 or ema_gap >= 8.0 or rsi_value >= 92.0

    # ── RANGING regime: block all trend-following, only range_scalp allowed ──
    if regime == "RANGING":
        bb_squeeze_bounce = bool(item.get("bb_squeeze_bounce", False))
        vwap_deviation_long = bool(item.get("vwap_deviation_long", False))
        rsi_extreme_long = bool(item.get("rsi_extreme_long", False))
        stoch_oversold_cross = bool(item.get("stoch_oversold_cross", False))
        macd_histogram_reversal = bool(item.get("macd_histogram_reversal", False))
        hammer_candle = bool(item.get("hammer_candle", False))
        doji_candle = bool(item.get("doji_candle", False))
        volume_climax_reversal = bool(item.get("volume_climax_reversal", False))
        support_reclaim_long = bool(item.get("support_reclaim_long", False))
        range_breakout_long = bool(item.get("range_breakout_long", False))
        high_tight_flag_long = bool(item.get("high_tight_flag_long", False))
        # Batch 2 RANGING 신호
        williams_r_oversold = bool(item.get("williams_r_oversold", False))
        cci_oversold_bounce = bool(item.get("cci_oversold_bounce", False))
        keltner_lower_touch = bool(item.get("keltner_lower_touch", False))
        mfi_oversold = bool(item.get("mfi_oversold", False))
        local_continuation_ok = (
            (range_breakout_long or high_tight_flag_long)
            and orderbook_bid_ask >= 0.98
            and -0.35 <= micro_move_3 <= 1.55
            and micro_vwap_gap <= 4.8
            and signal_freshness >= 0.50
            and not bool(item.get("rsi_bearish_divergence", False))
            and not bool(item.get("micro_exhausted", False))
            and not hard_overheat
        )
        if local_continuation_ok:
            profile = "range_breakout" if range_breakout_long else "high_tight_flag"
            strategy_id = f"crypto.{profile}"
            item["entry_profile"] = profile
            if _strategy_is_disabled(strategy_id):
                item["hot_block_reason"] = "strategy_disabled"
                save_shadow_signal(
                    desk="crypto",
                    symbol=symbol,
                    strategy_id=strategy_id,
                    entry_profile=profile,
                    source="hot_candidate",
                    action="probe_longs",
                    focus=str(item.get("focus", f"{profile} hot candidate") or f"{profile} hot candidate"),
                    reason="strategy_disabled",
                    score=combined,
                    stream_score=_float(item.get("stream_score", 0.0)),
                    payload={"candidate": item},
                    dedupe_seconds=60,
                )
                return False
            return True
        # 멀티 신호: 14개 중 1개 이상
        ranging_signal = (
            (range_scalp_eligible and airborne_long and airborne_score >= 0.40)
            or bb_squeeze_bounce
            or vwap_deviation_long
            or (rsi_extreme_long and airborne_score >= 0.25)
            or stoch_oversold_cross
            or macd_histogram_reversal
            or hammer_candle
            or doji_candle
            or volume_climax_reversal
            or support_reclaim_long
            or williams_r_oversold
            or cci_oversold_bounce
            or keltner_lower_touch
            or mfi_oversold
        )
        # dev > +1.0%: 가격이 EMA 위에서 롱 평균회귀 진입 차단
        # STORJ dev=+1.32% 진입 → 방향 반대(EMA 위에서 long mean-reversion = wrong)
        airborne_dev_pct = _float(item.get("airborne_deviation_pct", 0.0))
        dev_blocks_long_meanrev = airborne_dev_pct > 1.0
        range_scalp_hot_ok = (
            ranging_signal
            and not dev_blocks_long_meanrev
            and orderbook_bid_ask >= 1.05
            and -1.0 <= micro_move_3 <= 1.50
            and not bool(item.get("rsi_bearish_divergence", False))
            and not bool(item.get("micro_exhausted", False))
            and signal_freshness >= 0.50
            and not hard_overheat
        )
        if range_scalp_hot_ok:
            item["entry_profile"] = "range_scalp"
            if _strategy_is_disabled("crypto.range_scalp"):
                item["hot_block_reason"] = "strategy_disabled"
                save_shadow_signal(
                    desk="crypto",
                    symbol=symbol,
                    strategy_id="crypto.range_scalp",
                    entry_profile="range_scalp",
                    source="hot_candidate",
                    action="probe_longs",
                    focus=str(item.get("focus", "range_scalp hot candidate") or "range_scalp hot candidate"),
                    reason="strategy_disabled",
                    score=combined,
                    stream_score=_float(item.get("stream_score", 0.0)),
                    payload={"candidate": item},
                    dedupe_seconds=60,
                )
                return False
            return True
        return False

    # ── Batch 2 TRENDING 신호 추출 ─────────────────────────────────────────
    ema_cross_long = bool(item.get("ema_cross_long", False))
    vwap_cross_long_signal = bool(item.get("vwap_cross_long", False))

    # EMA Crossover Long: EMA8/21 골든크로스 직후 조기 진입
    # standard_ok보다 낮은 threshold(trend_score ≥ 0.65) — 크로스 직후라 EMA 스택 미완성
    ema_cross_ok = (
        ema_cross_long
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.65
        and combined >= 0.60
        and orderbook_bid_ask >= 1.08
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.55
        and trend_extension_pct <= 4.0
    )
    if ema_cross_ok:
        item["entry_profile"] = "ema_cross"
        if _strategy_is_disabled("crypto.ema_cross"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.ema_cross",
                entry_profile="ema_cross",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "ema_cross hot candidate") or "ema_cross hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    # VWAP Reclaim Long: 가격이 VWAP 아래 → 위로 재탈환
    # 기관 평균단가 복귀 — trend_entry_allowed 또는 pullback_long이면 진입
    vwap_reclaim_ok = (
        vwap_cross_long_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.62
        and combined >= 0.58
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.55
        and trend_extension_pct <= 4.5
    )
    if vwap_reclaim_ok:
        item["entry_profile"] = "vwap_reclaim"
        if _strategy_is_disabled("crypto.vwap_reclaim"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.vwap_reclaim",
                entry_profile="vwap_reclaim",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "vwap_reclaim hot candidate") or "vwap_reclaim hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    common_guards = (
        signal_freshness >= 0.58               # 0.55 → 0.58: 더 신선한 신호만
        and -0.30 <= micro_move_3 <= 0.95      # 상한 1.20 → 0.95: 이미 올라간 뒤 진입 차단
        and micro_vwap_gap <= 1.50             # 1.80 → 1.50: VWAP 이격 타이트
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
    )
    # Standard path: full EMA stack confirmed, price not overextended
    standard_ok = (
        bool(item.get("trend_entry_allowed", False))
        and trend_alignment in {"trend_long", "pullback_long"}
        and trend_score >= 0.78               # 0.76 → 0.78
        and combined >= 0.74                  # 0.72 → 0.74
        and orderbook_bid_ask >= 1.10         # 1.08 → 1.10
        and trend_extension_pct <= 2.8        # 3.0 → 2.8: 과확장 구간 차단 강화
    )
    # Early trend path: CHoCH/BOS structural break before EMA stack catches up
    early_ok = (
        bool(item.get("trend_early_entry", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.72               # 0.70 → 0.72
        and combined >= 0.76                  # 0.74 → 0.76
        and orderbook_bid_ask >= 1.22         # 1.20 → 1.22
        and trend_extension_pct <= 1.8        # 2.0 → 1.8
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
        and recent_change >= 0.00
        and (
            combined >= 0.52
            or (chart_score >= 0.90 and trend_score >= 0.90 and max(change_rate, burst_change) >= 3.0)
            or (chart_score >= 0.76 and change_rate >= 20.0 and rsi_value <= 70.0)
        )
        and signal_freshness >= 0.50
        and trend_extension_pct <= 8.5
        and micro_vwap_gap <= 6.5
        and not obvious_top_risk
    )
    if common_guards and (standard_ok or early_ok):
        item["entry_profile"] = "trend_ignition"
        disabled_id = _strategy_id_for_entry_profile("trend_ignition")
        if _strategy_is_disabled(disabled_id):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id=disabled_id,
                entry_profile="trend_ignition",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "trend_ignition hot candidate") or "trend_ignition hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True
    if obvious_trend_ok:
        item["entry_profile"] = "obvious_trend"
        if _strategy_is_disabled("crypto.obvious_trend"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.obvious_trend",
                entry_profile="obvious_trend",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "obvious_trend hot candidate") or "obvious_trend hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True
    if range_impulse_ok and _ENABLE_EXPERIMENTAL_IMPULSE_ENTRIES:
        item["entry_profile"] = "range_impulse"
        if _strategy_is_disabled("crypto.range_impulse"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.range_impulse",
                entry_profile="range_impulse",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "range_impulse hot candidate") or "range_impulse hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
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
    cycle_regime = str(state.get("regime", "") or "")
    prepared: dict[str, dict[str, Any]] = {}
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        # Inject cycle-level regime so _candidate_is_hot_entry_eligible can gate on it
        item_with_regime = {**item, "regime": cycle_regime}
        if not _candidate_is_hot_entry_eligible(item_with_regime):
            continue
        symbol = str(item_with_regime.get("market") or item_with_regime.get("symbol") or "").strip()
        prepared[symbol] = {
            **item_with_regime,
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
        pos_focus = str(item.get("focus", "") or "")
        target_pct, stop_pct, _ = _position_thresholds("crypto", str(item.get("action") or ""), focus=pos_focus)
        is_range_scalp_hot = "range_scalp" in pos_focus
        is_range_impulse = "range_impulse" in pos_focus
        is_obvious_trend = "obvious_trend" in pos_focus
        is_ema_cross = "ema_cross" in pos_focus
        is_vwap_reclaim = "vwap_reclaim" in pos_focus
        minutes_open = _minutes_open(str(item.get("opened_at") or ""))
        reason = ""

        # ── Range Scalp: dedicated rapid guard (tight target/stop/trail) ──
        if is_range_scalp_hot:
            rs_trail_giveback, rs_profit_floor = _range_scalp_trail_rules(peak_pnl)
            rs_protect = max(rs_profit_floor, peak_pnl - rs_trail_giveback) if rs_trail_giveback else 0.0
            if pnl_pct >= target_pct:
                reason = "rapid_range_scalp_target"
            elif peak_pnl <= 0.0 and minutes_open >= 0.20 and pnl_pct <= -0.15:
                # 진입 후 한 번도 반등 없이 낙하 → 12s 후 -0.15%에서 즉시 청산 (24s→12s)
                # (state_store rapid_guard와 일관성 유지)
                reason = "rapid_range_scalp_no_lift"
            elif pnl_pct <= stop_pct:
                reason = "rapid_range_scalp_stop"
            elif rs_trail_giveback and pnl_pct <= rs_protect:
                reason = "rapid_range_scalp_trail"
            elif (no_lift := _range_scalp_no_lift_exit(minutes_open, peak_pnl, pnl_pct)):
                reason = no_lift
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
            continue

        trail_giveback, profit_floor = _crypto_trail_rules(peak_pnl)
        protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
        if pnl_pct >= target_pct:
            reason = "rapid_target_hit"
        elif is_obvious_trend and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.22:
            # obvious_trend 조기 실패 -0.35% → -0.22%
            reason = "rapid_obvious_trend_fail"
        elif is_obvious_trend and pnl_pct <= -0.38:
            # obvious_trend 최대 손실 -0.45% → -0.38%: avg -0.50% 절감
            reason = "rapid_obvious_trend_fail"
        elif is_range_impulse and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.25:
            reason = "rapid_range_impulse_fail"
        elif is_range_impulse and pnl_pct <= -0.40:
            reason = "rapid_range_impulse_fail"
        elif is_range_impulse and peak_pnl >= 0.28 and pnl_pct <= max(0.02, peak_pnl - 0.35):
            reason = "rapid_range_impulse_protect"
        elif is_ema_cross and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
            reason = "rapid_ema_cross_fail"
        elif is_ema_cross and pnl_pct <= -0.50:
            reason = "rapid_ema_cross_fail"
        elif is_vwap_reclaim and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
            reason = "rapid_vwap_reclaim_fail"
        elif is_vwap_reclaim and pnl_pct <= -0.50:
            reason = "rapid_vwap_reclaim_fail"
        elif 0.40 <= peak_pnl < 0.80 and minutes_open >= 1.0 and pnl_pct <= max(-0.55, peak_pnl - 1.10):
            reason = "failed_breakout_exit"
        elif (
            # 진입 후 peak=0 AND 빠른 역행 → 즉시 청산 (stream 확인 불필요)
            # rapid_tick_failed_start avg -0.616% → -0.22% 손실 목표
            not is_range_scalp_hot
            and peak_pnl <= 0.05
            and minutes_open >= 0.33
            and pnl_pct <= -0.22
        ):
            reason = "rapid_tick_failed_start"
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
    # 연속 실패 블랙리스트 확인 (in-memory)
    blacklist_until = _failure_blacklist.get(symbol, 0.0)
    if now < blacklist_until:
        return {"entry_opened": 0, "reason": "entry_failure_blacklist"}

    combined = _float(candidate.get("combined_score", candidate.get("signal_score", 0.0)))
    trend = _float(candidate.get("trend_follow_score", 0.0))
    micro = _float(candidate.get("micro_score", 0.0))
    orderbook_score = _float(candidate.get("orderbook_score", 0.0))
    orderbook_bid_ask = _float(candidate.get("orderbook_bid_ask_ratio", 0.0))
    stream_score = _float(stream.get("stream_score", 0.0))
    entry_profile = str(candidate.get("entry_profile", "tick_ignition") or "tick_ignition")
    strategy_id = _strategy_id_for_entry_profile(entry_profile)
    if _strategy_is_disabled(strategy_id):
        save_shadow_signal(
            desk="crypto",
            symbol=symbol,
            strategy_id=strategy_id,
            entry_profile=entry_profile,
            source="hot_path",
            action="probe_longs",
            focus=f"{symbol} {entry_profile} blocked by strategy kill switch",
            reason="strategy_disabled",
            score=combined,
            stream_score=stream_score,
            notional_pct=size_notional,
            payload={"candidate": candidate, "stream": stream},
            dedupe_seconds=60,
        )
        return {"entry_opened": 0, "reason": "entry_strategy_disabled", "strategy_id": strategy_id}
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
        "entry_path": entry_profile,
        "strategy_id": strategy_id,
        "entry_profile": entry_profile,
        "status": "planned",
    }
    entry_path = meta["entry_path"]
    move15_val = _float(stream.get("stream_move_15s_pct", 0.0))
    if entry_path == "range_scalp":
        airborne_score_val = _float(candidate.get("airborne_score", 0.0))
        deviation_pct_val = _float(candidate.get("airborne_deviation_pct", 0.0))
        order_focus = (
            f"range_scalp: {symbol} hot tick entry - airborne {airborne_score_val:.2f} "
            f"dev {deviation_pct_val:+.2f}%, stream {stream_score:.2f}, move15 {move15_val:.2f}%."
        )
    elif entry_path == "range_breakout":
        range_high_val = _float(candidate.get("range_high_20", 0.0))
        order_focus = (
            f"range_breakout: {symbol} hot tick entry - range high {range_high_val:.4f}, "
            f"stream {stream_score:.2f}, move15 {move15_val:.2f}%."
        )
    elif entry_path == "high_tight_flag":
        impulse_val = _float(candidate.get("impulse_12_pct", 0.0))
        flag_val = _float(candidate.get("flag_range_pct", 0.0))
        order_focus = (
            f"high_tight_flag: {symbol} hot tick entry - impulse {impulse_val:.2f}% "
            f"flag {flag_val:.2f}%, stream {stream_score:.2f}, move15 {move15_val:.2f}%."
        )
    else:
        order_focus = (
            f"{symbol} {entry_path} tick entry - combined {combined:.2f}, "
            f"stream {stream_score:.2f}, move15 {move15_val:.2f}%."
        )
    order = PaperOrder(
        desk="crypto",
        action="probe_longs",
        focus=order_focus,
        size=f"{size_notional:.2f}x",
        symbol=symbol,
        reference_price=price,
        notional_pct=size_notional,
        status="planned",
        strategy_id=strategy_id,
        entry_profile=entry_profile,
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
        recent_closed = db.execute(
            select(PaperPositionRecord)
            .where(
                PaperPositionRecord.status == "closed",
                PaperPositionRecord.desk == "crypto",
                PaperPositionRecord.symbol == symbol,
            )
            .order_by(PaperPositionRecord.id.desc())
            .limit(3)                              # 2 → 3: 최근 3건 확인
        ).scalars().all()
        fail_count = 0
        for row in recent_closed:
            is_failure = row.closed_reason in _HOT_RECENT_FAILURE_REASONS
            pnl = float(row.pnl_pct or 0.0)
            # -0.15% 이상 손실로 실패한 경우 차단 (기존 -0.30% → -0.15%)
            if is_failure and pnl <= -0.15:
                return {"entry_opened": 0, "reason": "entry_recent_failure_cooldown"}
            if is_failure and pnl < 0.0:
                fail_count += 1
        # 소손실 실패가 2건 이상이면 블랙리스트 등록 후 차단
        if fail_count >= 2:
            _failure_blacklist[symbol] = now + _FAILURE_BLACKLIST_SECONDS
            return {"entry_opened": 0, "reason": "entry_failure_blacklist"}
        db.add(
            PaperOrderRecord(
                created_at=order.created_at,
                desk=order.desk,
                action=order.action,
                focus=order.focus,
                size=order.size,
                strategy_id=order.strategy_id,
                entry_profile=order.entry_profile,
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
            strategy_id=order.strategy_id,
            entry_profile=order.entry_profile,
        )
        db.add(position)
        db.flush()
        opened_payload = _paper_trade_payload(position, meta)
        db.commit()
    if opened_payload is not None:
        _notify_trade_entry(opened_payload)
    # 진입 성공 시 블랙리스트 해제
    _failure_blacklist.pop(symbol, None)
    _entry_last_opened_by_symbol[symbol] = now
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
        # obvious_trend: 과거 82.5% peak=0 실패 원인 → 더 강한 틱 모멘텀 요구
        ignition = (
            stream_ok
            and ticks_15 >= 2          # 1 → 2: 틱 밀도 강화
            and stream_score >= 0.65   # 0.55 → 0.65: 스트림 강도 강화
            and move_5 >= 0.06         # NEW: 5s 모멘텀 확인 (진입 직전 오르고 있어야)
            and move_15 >= 0.22        # 0.18 → 0.22: 15s 방향 강화
            and move_60 >= -0.15       # -0.28 → -0.15: 60s 역행 허용 줄임
            and buy_ratio >= 0.58      # 0.55 → 0.58: 매수세 강화
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
    elif entry_profile == "range_breakout":
        ignition = (
            stream_ok
            and ticks_15 >= 4
            and stream_score >= 0.74
            and move_5 >= 0.10
            and 0.25 <= move_15 <= 1.25
            and move_60 >= -0.12
            and buy_ratio >= 0.60
        )
    elif entry_profile == "high_tight_flag":
        ignition = (
            stream_ok
            and ticks_15 >= 3
            and stream_score >= 0.68
            and move_5 >= 0.06
            and 0.12 <= move_15 <= 0.95
            and move_60 >= -0.18
            and buy_ratio >= 0.57
        )
    elif entry_profile == "range_scalp":
        # Mean reversion entry: price overextended below EMA, expect bounce.
        # peak=0% 실패 패턴 차단: 더 강한 초기 반등 필요
        ignition = (
            stream_ok
            and ticks_15 >= 2
            and stream_score >= 0.55      # 0.52 → 0.55
            and move_5 >= 0.08            # 0.05 → 0.08: 5s 반등 강화
            and move_15 >= 0.12           # 0.08 → 0.12: 15s 반등 강화
            and move_60 >= -0.30          # -0.35 → -0.30: 60s 하락 허용 줄임
            and buy_ratio >= 0.54         # 0.52 → 0.54: 매수세 강화
        )
    else:
        # trend_ignition: 추세 확인 + 틱 모멘텀 동시 충족
        # 핵심 원칙: 이미 오른 뒤가 아니라 '지금 오르는 중'에 진입
        ignition = (
            stream_ok
            and ticks_15 >= 4                      # 3 → 4: 틱 밀도 강화
            and stream_score >= 0.74               # 0.70 → 0.74
            and move_5 >= 0.12                     # 0.08 → 0.12: 현재 5초 모멘텀 강해야
            and 0.35 <= move_15 <= 0.75            # 0.28 → 0.35: 너무 작은 움직임 제외
                                                   # 0.85 → 0.75: 너무 많이 오른 것도 제외
            and -0.10 <= move_60 <= 1.20           # 상한 추가: 60초에 1.2% 이상 오른 건 늦은 진입
            and buy_ratio >= 0.63                  # 0.60 → 0.63
            # 모멘텀 지속 확인: 5초 움직임이 15초 움직임 대비 너무 약하면 모멘텀 죽은 것
            and move_5 >= move_15 * 0.20
        )
    if not ignition:
        return {**guard_summary, "entry_opened": 0, "reason": "entry_wait_tick_ignition"}
    return {**guard_summary, **_open_hot_entry(symbol, price, candidate, stream)}

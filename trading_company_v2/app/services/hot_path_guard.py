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
    "rapid_rsi_flip_fail",
    "rapid_macd_cross_fail",
    "rapid_triple_bull_fail",
    "rapid_pullback_cont_fail",
    "rapid_choch_momentum_fail",
    "rapid_ict_level_fail",
    "rapid_supertrend_fail",
    "rapid_engulfing_fail",
    "rapid_vol_surge_fail",
    "rapid_adx_trend_fail",
    "rapid_bb_squeeze_fail",
    "rapid_higher_lows_fail",
    "rapid_pin_bar_fail",
    "rapid_morning_star_fail",
    "rapid_inside_bar_fail",
    "rapid_rsi_keep_fail",
    "rapid_oi_momentum_fail",
    "rapid_demand_zone_fail",
    "rapid_b6_fail",
    "rapid_failed_start",
    "rapid_stop_hit",
    "rapid_repeat_symbol_failure",
    "rapid_range_scalp_stop",
    "rapid_range_scalp_no_lift",
}
# 실패 후 블랙리스트 쿨다운 (초): 연속 2회 실패 시 이 시간만큼 재진입 차단
_FAILURE_BLACKLIST_SECONDS = 900.0      # 6분 → 15분: 반복 실패 코인 블랙리스트 연장
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
    if entry_profile == "rsi_flip":
        if combined >= 0.60 and stream_score >= 0.68:
            return 0.05
        return 0.038
    if entry_profile == "macd_cross":
        if combined >= 0.60 and stream_score >= 0.68:
            return 0.05
        return 0.038
    if entry_profile == "triple_bull":
        if combined >= 0.62 and stream_score >= 0.72:
            return 0.055
        return 0.04
    if entry_profile == "pullback_continuation":
        if combined >= 0.62 and stream_score >= 0.72:
            return 0.06
        return 0.045
    if entry_profile == "choch_momentum":
        if combined >= 0.62 and stream_score >= 0.72:
            return 0.06
        return 0.045
    if entry_profile == "ict_level_long":
        if combined >= 0.62 and stream_score >= 0.72:
            return 0.065
        return 0.05
    if entry_profile == "supertrend":
        if combined >= 0.62 and stream_score >= 0.70:
            return 0.055
        return 0.042
    if entry_profile == "engulfing_bull":
        if combined >= 0.60 and stream_score >= 0.68:
            return 0.05
        return 0.038
    if entry_profile == "vol_surge":
        if combined >= 0.60 and stream_score >= 0.68:
            return 0.055
        return 0.042
    if entry_profile == "adx_trend":
        if combined >= 0.62 and stream_score >= 0.70:
            return 0.055
        return 0.042
    if entry_profile == "bb_squeeze_break":
        if combined >= 0.62 and stream_score >= 0.72:
            return 0.06
        return 0.045
    if entry_profile == "higher_lows":
        if combined >= 0.63 and stream_score >= 0.72:
            return 0.055
        return 0.042
    if entry_profile == "pin_bar":
        if combined >= 0.58 and stream_score >= 0.68:
            return 0.045
        return 0.035
    if entry_profile == "morning_star":
        if combined >= 0.58 and stream_score >= 0.68:
            return 0.048
        return 0.036
    if entry_profile == "inside_bar_break":
        if combined >= 0.60 and stream_score >= 0.70:
            return 0.05
        return 0.038
    if entry_profile == "rsi_keep":
        if combined >= 0.64 and stream_score >= 0.72:
            return 0.055
        return 0.042
    if entry_profile == "oi_momentum":
        if combined >= 0.60 and stream_score >= 0.70:
            return 0.052
        return 0.040
    if entry_profile == "demand_zone":
        if combined >= 0.58 and stream_score >= 0.68:
            return 0.048
        return 0.036
    # Batch 6 공용 size (단일 테이블로 처리)
    _b6_profiles = {
        "vwap_rsi_combo", "breakout_vol_confirm", "hammer_at_support",
        "trend_reversal_early", "ema_bounce", "rsi_bullish_div",
        "multi_ranging", "momentum_bk_cont",
    }
    if entry_profile in _b6_profiles:
        if combined >= 0.60 and stream_score >= 0.70:
            return 0.05
        return 0.038
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
        rs_vol_ratio = _float(item.get("vol_ratio", 0.0))
        rs_vol_24h = _float(item.get("volume_24h_krw", 0.0))
        # 유동성 필터: ANKR/STORJ/SC 등 소형 저유동성 코인 진입 차단
        # → 2초 내 -0.99% 갭점프 패턴 방지 (rapid_range_scalp_stop avg -0.74%)
        range_scalp_liquidity_ok = rs_vol_ratio >= 0.8 or rs_vol_24h >= 5_000_000_000
        range_scalp_hot_ok = (
            ranging_signal
            and not dev_blocks_long_meanrev
            and range_scalp_liquidity_ok
            and orderbook_bid_ask >= 1.10  # 1.05 → 1.10: 갭점프 방지 (B3 ob=1.085 차단)
            and -1.0 <= micro_move_3 <= 1.50
            and not bool(item.get("rsi_bearish_divergence", False))
            and not bool(item.get("micro_exhausted", False))
            and signal_freshness >= 0.50
            and not hard_overheat
            and rsi_value <= 42.0          # 진짜 과매도 구간만 평균회귀 (71% peak=0 개선)
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

        # ── RANGING 호환 Batch 3-6 전략 ──────────────────────────────────────
        # RANGING 시장에서도 발화 가능한 전략들: 평균회귀/구조개선/압축돌파
        # (TRENDING 전용 전략은 위 RANGING 블록에서 이미 차단됨)
        _ranging_base_ok = (
            not hard_overheat
            and not bool(item.get("rsi_bearish_divergence", False))
            and not bool(item.get("micro_exhausted", False))
            and signal_freshness >= 0.48
            and orderbook_bid_ask >= 1.05
            and combined >= 0.54
            # 유동성/안정성: 소형코인·펌프코인 차단 (G vol=1.6B, vol_ratio=285x 진입 방지)
            and (rs_vol_24h >= 2_000_000_000 or rs_vol_ratio >= 0.8)
            and rs_vol_ratio <= 80.0  # 극단적 거래량 스파이크(펌프) 차단
        )

        def _ranging_b_check(
            signal_val: bool,
            profile: str,
            min_combined: float = 0.54,
            max_rsi: float = 65.0,
        ) -> bool:
            """RANGING 블록 내 Batch 전략 간이 체크.
            max_rsi: 전략 유형별 RSI 상한
              평균회귀(demand_zone/vwap_rsi/hammer/rsi_div/multi): ≤ 50
              구조개선(higher_lows/trend_reversal): ≤ 58
              압축돌파(bb_squeeze/breakout_vol/inside_bar/ema_bounce): ≤ 65
            """
            if not signal_val or not _ranging_base_ok:
                return False
            if combined < min_combined:
                return False
            if rsi_value > max_rsi:          # RSI 방향 필터: 과매수 구간 진입 차단
                return False
            item["entry_profile"] = profile
            sid = f"crypto.{profile}"
            if _strategy_is_disabled(sid):
                item["hot_block_reason"] = "strategy_disabled"
                save_shadow_signal(
                    desk="crypto", symbol=symbol, strategy_id=sid, entry_profile=profile,
                    source="hot_candidate", action="probe_longs",
                    focus=str(item.get("focus", f"{profile} ranging") or f"{profile} ranging"),
                    reason="strategy_disabled", score=combined,
                    stream_score=_float(item.get("stream_score", 0.0)),
                    payload={"candidate": item}, dedupe_seconds=60,
                )
                return False
            return True

        # ── RANGING 평균회귀 전략 (RSI ≤ 50: 진짜 과매도 구간에서만) ──────────
        # 1. multi_ranging_combo — RANGING 전용 복합 신호 (최우선)
        if _ranging_b_check(bool(item.get("multi_ranging_combo", False)), "multi_ranging", min_combined=0.52, max_rsi=50.0):
            return True
        # 2. demand_zone_bounce — 지지구간 반등 (평균회귀 핵심)
        if _ranging_b_check(bool(item.get("demand_zone_bounce", False)), "demand_zone", min_combined=0.52, max_rsi=50.0):
            return True
        # 3. vwap_rsi_combo — VWAP+RSI 과매도 복합
        if _ranging_b_check(bool(item.get("vwap_rsi_combo", False)), "vwap_rsi_combo", min_combined=0.53, max_rsi=50.0):
            return True
        # 4. hammer_at_support — 지지선 망치형 (RSI ≤ 52: 망치형은 조금 더 허용)
        if _ranging_b_check(bool(item.get("hammer_at_support", False)), "hammer_at_support", min_combined=0.52, max_rsi=52.0):
            return True
        # 9. rsi_bullish_div — RSI 불리쉬 다이버전스 (다이버전스 = 과매도에서 발생)
        if _ranging_b_check(bool(item.get("rsi_bullish_div", False)), "rsi_bullish_div", min_combined=0.52, max_rsi=48.0):
            return True

        # ── RANGING 구조개선 전략 (RSI ≤ 58: 방향성 전환 초기 포착) ──────────
        # 5. consecutive_higher_lows — 저점 높아지는 구조 개선
        if _ranging_b_check(bool(item.get("consecutive_higher_lows", False)), "higher_lows", min_combined=0.55, max_rsi=58.0):
            return True
        # 10. trend_reversal_early — CHoCH 단독 조기 포착
        if _ranging_b_check(bool(item.get("trend_reversal_early", False)), "trend_reversal_early", min_combined=0.54, max_rsi=58.0):
            return True

        # ── RANGING 압축돌파 전략 (RSI ≤ 65: 과열 전 단계까지 허용) ──────────
        # 6. inside_bar_breakout — 압축 후 돌파
        if _ranging_b_check(bool(item.get("inside_bar_breakout", False)), "inside_bar_break", min_combined=0.55, max_rsi=65.0):
            return True
        # 7. bb_squeeze_breakout — BB 스퀴즈 → 이탈
        if _ranging_b_check(bool(item.get("bb_squeeze_breakout", False)), "bb_squeeze_break", min_combined=0.56, max_rsi=65.0):
            return True
        # 8. breakout_vol_confirm — 거래량 동반 돌파 확인
        if _ranging_b_check(bool(item.get("breakout_vol_confirm", False)), "breakout_vol_confirm", min_combined=0.56, max_rsi=63.0):
            return True
        # 11. ema_bounce_long — EMA 지지 반등 (추가: 구조 개선 + EMA 지지선)
        if _ranging_b_check(bool(item.get("ema_bounce_long", False)), "ema_bounce", min_combined=0.55, max_rsi=55.0):
            return True

        return False

    # ── Batch 2 TRENDING 신호 추출 ─────────────────────────────────────────
    ema_cross_long = bool(item.get("ema_cross_long", False))
    vwap_cross_long_signal = bool(item.get("vwap_cross_long", False))
    # ── Batch 3 TRENDING 신호 추출 ─────────────────────────────────────────
    rsi_flip_long_signal = bool(item.get("rsi_flip_long", False))
    macd_bull_cross_signal = bool(item.get("macd_bull_cross", False))
    triple_candle_bull_signal = bool(item.get("triple_candle_bull", False))
    # ── Batch 4 TRENDING/DUAL 신호 추출 ────────────────────────────────────
    supertrend_long_signal = bool(item.get("supertrend_long", False))
    engulfing_bull_signal = bool(item.get("engulfing_bull", False))
    vol_surge_long_signal = bool(item.get("vol_surge_long", False))
    adx_trend_strong_signal = bool(item.get("adx_trend_strong", False))
    adx_val_hp = float(item.get("adx_val", 0.0) or 0.0)
    bb_squeeze_breakout_signal = bool(item.get("bb_squeeze_breakout", False))
    consecutive_higher_lows_signal = bool(item.get("consecutive_higher_lows", False))
    # ── Batch 5 신호 추출 ───────────────────────────────────────────────────
    pin_bar_long_signal = bool(item.get("pin_bar_long", False))
    morning_star_signal = bool(item.get("morning_star", False))
    inside_bar_breakout_signal = bool(item.get("inside_bar_breakout", False))
    rsi_momentum_keep_signal = bool(item.get("rsi_momentum_keep", False))
    oi_momentum_long_signal = bool(item.get("oi_momentum_long", False))
    demand_zone_bounce_signal = bool(item.get("demand_zone_bounce", False))
    # ── Batch 6 신호 추출 ───────────────────────────────────────────────────
    vwap_rsi_combo_signal = bool(item.get("vwap_rsi_combo", False))
    breakout_vol_confirm_signal = bool(item.get("breakout_vol_confirm", False))
    hammer_at_support_signal = bool(item.get("hammer_at_support", False))
    trend_reversal_early_signal = bool(item.get("trend_reversal_early", False))
    ema_bounce_long_signal = bool(item.get("ema_bounce_long", False))
    rsi_bullish_div_signal = bool(item.get("rsi_bullish_div", False))
    multi_ranging_combo_signal = bool(item.get("multi_ranging_combo", False))
    momentum_breakout_cont_signal = bool(item.get("momentum_breakout_cont", False))

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

    # RSI Momentum Flip Long: RSI가 50 아래 → 위로 교차 — 모멘텀 전환 초기
    rsi_flip_ok = (
        rsi_flip_long_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.60
        and combined >= 0.56
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
        and trend_extension_pct <= 4.5
    )
    if rsi_flip_ok:
        item["entry_profile"] = "rsi_flip"
        if _strategy_is_disabled("crypto.rsi_flip"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.rsi_flip",
                entry_profile="rsi_flip",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "rsi_flip hot candidate") or "rsi_flip hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    # MACD Bullish Cross: MACD가 시그널선을 상향돌파 (음의 영역) — 중기 모멘텀 전환
    macd_cross_ok = (
        macd_bull_cross_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.60
        and combined >= 0.56
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
        and trend_extension_pct <= 4.5
    )
    if macd_cross_ok:
        item["entry_profile"] = "macd_cross"
        if _strategy_is_disabled("crypto.macd_cross"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.macd_cross",
                entry_profile="macd_cross",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "macd_cross hot candidate") or "macd_cross hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    # Triple Candle Bull: 3연속 양봉 + 연속 고점 — 강한 매수 압력 지속
    triple_bull_ok = (
        triple_candle_bull_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.65
        and combined >= 0.60
        and orderbook_bid_ask >= 1.08
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.55
        and trend_extension_pct <= 3.5
    )
    if triple_bull_ok:
        item["entry_profile"] = "triple_bull"
        if _strategy_is_disabled("crypto.triple_bull"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.triple_bull",
                entry_profile="triple_bull",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "triple_bull hot candidate") or "triple_bull hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    # Pullback Continuation: 급등 후 조정 재진입 — Holy Grail 스타일
    pullback_cont_signal = bool(item.get("pullback_detected", False))
    pullback_score_val = float(item.get("pullback_score", 0.0) or 0.0)
    vol_contracted = bool(item.get("vol_contracted_on_pullback", False))
    pullback_cont_ok = (
        pullback_cont_signal
        and pullback_score_val >= 0.55
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.62
        and combined >= 0.58
        and vol_contracted
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
    )
    if pullback_cont_ok:
        item["entry_profile"] = "pullback_continuation"
        if _strategy_is_disabled("crypto.pullback_continuation"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.pullback_continuation",
                entry_profile="pullback_continuation",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "pullback_cont hot candidate") or "pullback_cont hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    # CHoCH Momentum: CHoCH + BOS 구조적 반전 진입 — ICT 스타일
    choch_bull_signal = bool(item.get("choch_bullish", False))
    bos_bull_signal = bool(item.get("bos_bullish", False))
    ict_bullish_cnt = int(item.get("ict_bullish_count", 0) or 0)
    choch_momentum_ok = (
        choch_bull_signal
        and bos_bull_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.62
        and combined >= 0.58
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
    )
    if choch_momentum_ok:
        item["entry_profile"] = "choch_momentum"
        if _strategy_is_disabled("crypto.choch_momentum"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.choch_momentum",
                entry_profile="choch_momentum",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "choch_momentum hot candidate") or "choch_momentum hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    # ICT Level Long: 불리시 OB 또는 FVG 진입 — 기관 매수 구간
    price_at_ob = bool(item.get("price_at_bull_ob", False))
    price_in_fvg = bool(item.get("price_in_bull_fvg", False))
    ict_level_ok = (
        (price_at_ob or price_in_fvg)
        and ict_bullish_cnt >= 2
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.62
        and combined >= 0.58
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
    )
    if ict_level_ok:
        item["entry_profile"] = "ict_level_long"
        if _strategy_is_disabled("crypto.ict_level_long"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto",
                symbol=symbol,
                strategy_id="crypto.ict_level_long",
                entry_profile="ict_level_long",
                source="hot_candidate",
                action="probe_longs",
                focus=str(item.get("focus", "ict_level_long hot candidate") or "ict_level_long hot candidate"),
                reason="strategy_disabled",
                score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item},
                dedupe_seconds=60,
            )
            return False
        return True

    # Supertrend Long: Supertrend(10,3) 불리쉬 전환 — ATR 기반 추세 확인
    supertrend_ok = (
        supertrend_long_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.62
        and combined >= 0.58
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
        and trend_extension_pct <= 4.5
    )
    if supertrend_ok:
        item["entry_profile"] = "supertrend"
        if _strategy_is_disabled("crypto.supertrend"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.supertrend", entry_profile="supertrend",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "supertrend hot") or "supertrend hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # Bullish Engulfing: 직전 음봉 → 현재 양봉 완전 흡수 — 반전/지속 캔들
    engulfing_ok = (
        engulfing_bull_signal
        and trend_alignment not in {"downtrend", "late_extension"}
        and combined >= 0.55
        and orderbook_bid_ask >= 1.05
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.50
    )
    if engulfing_ok:
        item["entry_profile"] = "engulfing_bull"
        if _strategy_is_disabled("crypto.engulfing_bull"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.engulfing_bull", entry_profile="engulfing_bull",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "engulfing hot") or "engulfing hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # Volume Surge Long: 거래량 2.5배 급증 + 양봉 — 기관 매집 포착
    vol_surge_ok = (
        vol_surge_long_signal
        and trend_alignment not in {"downtrend", "late_extension"}
        and combined >= 0.54
        and orderbook_bid_ask >= 1.04
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.48
    )
    if vol_surge_ok:
        item["entry_profile"] = "vol_surge"
        if _strategy_is_disabled("crypto.vol_surge"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.vol_surge", entry_profile="vol_surge",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "vol_surge hot") or "vol_surge hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # ADX Trend Strong: ADX ≥ 22 + DI+>DI- — 방향성 있는 강한 추세 진입
    adx_strong_ok = (
        adx_trend_strong_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.62
        and combined >= 0.58
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
        and trend_extension_pct <= 4.5
    )
    if adx_strong_ok:
        item["entry_profile"] = "adx_trend"
        if _strategy_is_disabled("crypto.adx_trend"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.adx_trend", entry_profile="adx_trend",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "adx_trend hot") or "adx_trend hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # BB Squeeze Breakout: BB 스퀴즈 + 상단 돌파 — 압축 후 추세 시작
    bb_sq_break_ok = (
        bb_squeeze_breakout_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.62
        and combined >= 0.58
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.53
    )
    if bb_sq_break_ok:
        item["entry_profile"] = "bb_squeeze_break"
        if _strategy_is_disabled("crypto.bb_squeeze_break"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.bb_squeeze_break", entry_profile="bb_squeeze_break",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "bb_squeeze_break hot") or "bb_squeeze_break hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # Consecutive Higher Lows: 3연속 HL 구조 — 상승 추세 구조 진입
    higher_lows_hp_ok = (
        consecutive_higher_lows_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.65
        and combined >= 0.60
        and orderbook_bid_ask >= 1.07
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.55
    )
    if higher_lows_hp_ok:
        item["entry_profile"] = "higher_lows"
        if _strategy_is_disabled("crypto.higher_lows"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.higher_lows", entry_profile="higher_lows",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "higher_lows hot") or "higher_lows hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # ── Batch 5 진입 경로 ───────────────────────────────────────────────────
    # Pin Bar Long: 아래꼬리 매수 캔들 — RANGING/TRENDING 반전
    pin_bar_ok = (
        pin_bar_long_signal
        and trend_alignment not in {"downtrend", "late_extension"}
        and combined >= 0.52
        and orderbook_bid_ask >= 1.04
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.48
    )
    if pin_bar_ok:
        item["entry_profile"] = "pin_bar"
        if _strategy_is_disabled("crypto.pin_bar"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.pin_bar", entry_profile="pin_bar",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "pin_bar hot") or "pin_bar hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # Morning Star: 3봉 반전 패턴 — 바닥 확인
    morning_star_ok = (
        morning_star_signal
        and trend_alignment not in {"downtrend", "late_extension"}
        and combined >= 0.52
        and orderbook_bid_ask >= 1.04
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.48
    )
    if morning_star_ok:
        item["entry_profile"] = "morning_star"
        if _strategy_is_disabled("crypto.morning_star"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.morning_star", entry_profile="morning_star",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "morning_star hot") or "morning_star hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # Inside Bar Breakout: 압축 후 상단 돌파 — 방향성 발생
    inside_bar_ok = (
        inside_bar_breakout_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and trend_score >= 0.60
        and combined >= 0.56
        and orderbook_bid_ask >= 1.06
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.52
    )
    if inside_bar_ok:
        item["entry_profile"] = "inside_bar_break"
        if _strategy_is_disabled("crypto.inside_bar_break"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.inside_bar_break", entry_profile="inside_bar_break",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "inside_bar_break hot") or "inside_bar_break hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # RSI Momentum Keep: RSI 55~72 유지 추세 — 지속 진입
    rsi_keep_ok = (
        rsi_momentum_keep_signal
        and bool(item.get("trend_entry_allowed", False))
        and trend_score >= 0.68
        and combined >= 0.62
        and orderbook_bid_ask >= 1.07
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.55
        and trend_extension_pct <= 3.5
    )
    if rsi_keep_ok:
        item["entry_profile"] = "rsi_keep"
        if _strategy_is_disabled("crypto.rsi_keep"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.rsi_keep", entry_profile="rsi_keep",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "rsi_keep hot") or "rsi_keep hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # OI Momentum Long: 3봉 연속 거래량+가격 상승 — 매수 압력
    oi_mom_hp_ok = (
        oi_momentum_long_signal
        and trend_alignment not in {"downtrend", "late_extension"}
        and combined >= 0.55
        and orderbook_bid_ask >= 1.05
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.50
    )
    if oi_mom_hp_ok:
        item["entry_profile"] = "oi_momentum"
        if _strategy_is_disabled("crypto.oi_momentum"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.oi_momentum", entry_profile="oi_momentum",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "oi_momentum hot") or "oi_momentum hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # Demand Zone Bounce: 수요 구간 하단 터치 후 반등 — 기관 지지
    demand_zone_ok = (
        demand_zone_bounce_signal
        and trend_alignment not in {"downtrend", "late_extension"}
        and combined >= 0.52
        and orderbook_bid_ask >= 1.04
        and not bool(item.get("rsi_bearish_divergence", False))
        and not bool(item.get("micro_exhausted", False))
        and not hard_overheat
        and signal_freshness >= 0.48
    )
    if demand_zone_ok:
        item["entry_profile"] = "demand_zone"
        if _strategy_is_disabled("crypto.demand_zone"):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol,
                strategy_id="crypto.demand_zone", entry_profile="demand_zone",
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", "demand_zone hot") or "demand_zone hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    # ── Batch 6 진입 경로 ────────────────────────────────────────────────────

    def _b6_check(signal_val: bool, profile: str, min_combined: float = 0.54, min_ob: float = 1.05, need_trend: bool = False) -> bool:
        if not signal_val:
            return False
        if need_trend and not bool(item.get("trend_entry_allowed", False)):
            return False
        if combined < min_combined or orderbook_bid_ask < min_ob:
            return False
        if bool(item.get("rsi_bearish_divergence", False)) or bool(item.get("micro_exhausted", False)) or hard_overheat:
            return False
        if trend_alignment in {"downtrend", "late_extension"}:
            return False
        item["entry_profile"] = profile
        sid = f"crypto.{profile}"
        if _strategy_is_disabled(sid):
            item["hot_block_reason"] = "strategy_disabled"
            save_shadow_signal(
                desk="crypto", symbol=symbol, strategy_id=sid, entry_profile=profile,
                source="hot_candidate", action="probe_longs",
                focus=str(item.get("focus", f"{profile} hot") or f"{profile} hot"),
                reason="strategy_disabled", score=combined,
                stream_score=_float(item.get("stream_score", 0.0)),
                payload={"candidate": item}, dedupe_seconds=60,
            )
            return False
        return True

    if _b6_check(vwap_rsi_combo_signal, "vwap_rsi_combo", 0.52, 1.04):
        return True
    if _b6_check(breakout_vol_confirm_signal, "breakout_vol_confirm", 0.57, 1.06, need_trend=True):
        return True
    if _b6_check(hammer_at_support_signal, "hammer_at_support", 0.52, 1.04):
        return True
    if _b6_check(trend_reversal_early_signal, "trend_reversal_early", 0.55, 1.05, need_trend=True):
        return True
    if _b6_check(ema_bounce_long_signal, "ema_bounce", 0.53, 1.05):
        return True
    if _b6_check(rsi_bullish_div_signal, "rsi_bullish_div", 0.51, 1.04):
        return True
    if _b6_check(multi_ranging_combo_signal, "multi_ranging", 0.52, 1.05):
        return True
    if _b6_check(momentum_breakout_cont_signal, "momentum_bk_cont", 0.57, 1.06, need_trend=True):
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
    # obvious_trend: 진단 결과 13건 ALL peak=0.000% (RANGING 시장에서 방향 오류)
    # 강화 조건: "range" 제거, stream_ignition 필수, 임계값 대폭 상향
    stream_ignition_val = bool(item.get("stream_ignition", False))
    obvious_trend_ok = (
        trend_alignment in {"trend_long", "pullback_long"}   # "range" 제거
        and trend_alignment != "range"                        # 명시적 이중 차단
        and bool(item.get("trend_entry_allowed", False))      # trend_early 불인정
        and trend_score >= 0.85                               # 0.68 → 0.85
        and chart_score >= 0.82                               # 0.76 → 0.82
        and combined >= 0.72                                  # 0.52 → 0.72
        and stream_ignition_val                               # stream_ignition 필수
        and signal_freshness >= 0.55                          # 0.50 → 0.55
        and trend_extension_pct <= 6.0                        # 8.5 → 6.0
        and micro_vwap_gap <= 4.0                             # 6.5 → 4.0
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
    if range_impulse_ok:
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
        is_rsi_flip = "rsi_flip" in pos_focus
        is_macd_cross = "macd_cross" in pos_focus
        is_triple_bull = "triple_bull" in pos_focus
        is_pullback_cont = "pullback_continuation" in pos_focus
        is_choch_momentum = "choch_momentum" in pos_focus
        is_ict_level = "ict_level_long" in pos_focus
        is_supertrend = "supertrend" in pos_focus and "bb_squeeze" not in pos_focus
        is_engulfing = "engulfing" in pos_focus
        is_vol_surge = "vol_surge" in pos_focus
        is_adx_trend = "adx_trend" in pos_focus
        is_bb_squeeze_break = "bb_squeeze_break" in pos_focus
        is_higher_lows = "higher_lows" in pos_focus
        is_pin_bar = "pin_bar" in pos_focus
        is_morning_star = "morning_star" in pos_focus
        is_inside_bar = "inside_bar_break" in pos_focus
        is_rsi_keep = "rsi_keep" in pos_focus
        is_oi_momentum = "oi_momentum" in pos_focus
        is_demand_zone = "demand_zone" in pos_focus
        # Batch 6
        is_b6 = any(p in pos_focus for p in (
            "vwap_rsi_combo", "breakout_vol_confirm", "hammer_at_support",
            "trend_reversal_early", "ema_bounce", "rsi_bullish_div",
            "multi_ranging", "momentum_bk_cont",
        ))
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
        elif is_rsi_flip and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.28:
            reason = "rapid_rsi_flip_fail"
        elif is_rsi_flip and pnl_pct <= -0.46:
            reason = "rapid_rsi_flip_fail"
        elif is_macd_cross and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.28:
            reason = "rapid_macd_cross_fail"
        elif is_macd_cross and pnl_pct <= -0.46:
            reason = "rapid_macd_cross_fail"
        elif is_triple_bull and minutes_open >= 0.28 and peak_pnl <= 0.05 and pnl_pct <= -0.26:
            reason = "rapid_triple_bull_fail"
        elif is_triple_bull and pnl_pct <= -0.44:
            reason = "rapid_triple_bull_fail"
        elif is_pullback_cont and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.28:
            reason = "rapid_pullback_cont_fail"
        elif is_pullback_cont and pnl_pct <= -0.46:
            reason = "rapid_pullback_cont_fail"
        elif is_choch_momentum and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
            reason = "rapid_choch_momentum_fail"
        elif is_choch_momentum and pnl_pct <= -0.48:
            reason = "rapid_choch_momentum_fail"
        elif is_ict_level and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
            reason = "rapid_ict_level_fail"
        elif is_ict_level and pnl_pct <= -0.48:
            reason = "rapid_ict_level_fail"
        elif is_supertrend and minutes_open >= 0.28 and peak_pnl <= 0.05 and pnl_pct <= -0.28:
            reason = "rapid_supertrend_fail"
        elif is_supertrend and pnl_pct <= -0.46:
            reason = "rapid_supertrend_fail"
        elif is_engulfing and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.26:
            reason = "rapid_engulfing_fail"
        elif is_engulfing and pnl_pct <= -0.44:
            reason = "rapid_engulfing_fail"
        elif is_vol_surge and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.28:
            reason = "rapid_vol_surge_fail"
        elif is_vol_surge and pnl_pct <= -0.46:
            reason = "rapid_vol_surge_fail"
        elif is_adx_trend and minutes_open >= 0.28 and peak_pnl <= 0.05 and pnl_pct <= -0.28:
            reason = "rapid_adx_trend_fail"
        elif is_adx_trend and pnl_pct <= -0.46:
            reason = "rapid_adx_trend_fail"
        elif is_bb_squeeze_break and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
            reason = "rapid_bb_squeeze_fail"
        elif is_bb_squeeze_break and pnl_pct <= -0.48:
            reason = "rapid_bb_squeeze_fail"
        elif is_higher_lows and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
            reason = "rapid_higher_lows_fail"
        elif is_higher_lows and pnl_pct <= -0.48:
            reason = "rapid_higher_lows_fail"
        elif is_pin_bar and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.24:
            reason = "rapid_pin_bar_fail"
        elif is_pin_bar and pnl_pct <= -0.42:
            reason = "rapid_pin_bar_fail"
        elif is_morning_star and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.26:
            reason = "rapid_morning_star_fail"
        elif is_morning_star and pnl_pct <= -0.44:
            reason = "rapid_morning_star_fail"
        elif is_inside_bar and minutes_open >= 0.28 and peak_pnl <= 0.05 and pnl_pct <= -0.28:
            reason = "rapid_inside_bar_fail"
        elif is_inside_bar and pnl_pct <= -0.46:
            reason = "rapid_inside_bar_fail"
        elif is_rsi_keep and minutes_open >= 0.30 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
            reason = "rapid_rsi_keep_fail"
        elif is_rsi_keep and pnl_pct <= -0.48:
            reason = "rapid_rsi_keep_fail"
        elif is_oi_momentum and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.26:
            reason = "rapid_oi_momentum_fail"
        elif is_oi_momentum and pnl_pct <= -0.44:
            reason = "rapid_oi_momentum_fail"
        elif is_demand_zone and minutes_open >= 0.25 and peak_pnl <= 0.05 and pnl_pct <= -0.26:
            reason = "rapid_demand_zone_fail"
        elif is_demand_zone and pnl_pct <= -0.44:
            reason = "rapid_demand_zone_fail"
        elif is_b6 and minutes_open >= 0.27 and peak_pnl <= 0.05 and pnl_pct <= -0.27:
            reason = "rapid_b6_fail"
        elif is_b6 and pnl_pct <= -0.45:
            reason = "rapid_b6_fail"
        elif 0.40 <= peak_pnl < 0.80 and minutes_open >= 1.0 and pnl_pct <= max(-0.55, peak_pnl - 1.10):
            reason = "failed_breakout_exit"
        elif (
            # 진입 후 peak=0 AND 빠른 역행: 2단계 조기화
            # 1단계: 12s(0.20min) 후 -0.25% 이하 → 즉시청산 (강한 실패 신호)
            # 2단계: 20s(0.33min) 후 -0.18% 이하 → 청산 (0.22% → 0.18% 타이트)
            not is_range_scalp_hot
            and peak_pnl <= 0.05
            and (
                (minutes_open >= 0.20 and pnl_pct <= -0.25)
                or (minutes_open >= 0.33 and pnl_pct <= -0.18)
            )
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
    elif entry_path in {
        "multi_ranging", "demand_zone", "vwap_rsi_combo", "hammer_at_support",
        "higher_lows", "inside_bar_break", "bb_squeeze_break",
        "breakout_vol_confirm", "rsi_bullish_div", "trend_reversal_early", "ema_bounce",
    }:
        # ranging_b36 마커 → state_store에서 range_scalp 스타일 trail/stop 적용
        order_focus = (
            f"ranging_b36: {symbol} {entry_path} tick entry - combined {combined:.2f}, "
            f"stream {stream_score:.2f}, move15 {move15_val:.2f}%."
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
        # obvious_trend: SONIC -0.52% 실패(move_60=-0.05% 허용됐으나 즉시 반전)
        # → move_60 ≥ 0.0: 1분 추세가 최소한 양수여야 진입 (하락 중 spike 차단)
        ignition = (
            stream_ok
            and ticks_15 >= 2          # 틱 밀도
            and stream_score >= 0.65   # 스트림 강도
            and move_5 >= 0.06         # 5s 모멘텀
            and move_15 >= 0.22        # 15s 방향
            and move_60 >= 0.00        # -0.15 → 0.00: 60s 하락 중 진입 차단
            and buy_ratio >= 0.58      # 매수세
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
    elif entry_profile in {
        "multi_ranging", "demand_zone", "vwap_rsi_combo", "hammer_at_support",
        "higher_lows", "inside_bar_break", "bb_squeeze_break",
        "breakout_vol_confirm", "rsi_bullish_div", "trend_reversal_early",
        "ema_bounce",  # 신규 추가
    }:
        # RANGING Batch 3-6 + ema_bounce 전략
        # trend_ignition보다 완화 (RANGING에서 move_15≥0.35 불가),
        # range_scalp보다 조금 강화 (방향성 변화 확인 필요)
        ignition = (
            stream_ok
            and ticks_15 >= 2
            and stream_score >= 0.58
            and move_5 >= 0.06
            and move_15 >= 0.14
            and move_60 >= -0.25
            and buy_ratio >= 0.54
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

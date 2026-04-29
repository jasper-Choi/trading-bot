from __future__ import annotations

from typing import Any


def build_crypto_plan(stance: str, regime: str, payload: dict[str, Any]) -> dict[str, Any]:
    bias = str(payload.get("desk_bias", "balanced") or "balanced")
    signal_score = float(payload.get("signal_score", 0.5) or 0.5)
    recent_change = float(payload.get("recent_change_pct", 0.0) or 0.0)
    burst_change = float(payload.get("burst_change_pct", 0.0) or 0.0)
    ema_gap = float(payload.get("ema_gap_pct", 0.0) or 0.0)
    rsi_value = payload.get("rsi")
    reasons = [str(item) for item in (payload.get("reasons", []) or [])]
    backtest_weights = payload.get("backtest_weights", {}) or {}
    lead_market = str(payload.get("lead_market", "") or "")
    candidate_symbols = [str(item).strip() for item in (payload.get("candidate_symbols", []) or []) if str(item).strip()]
    lead_weight = float(backtest_weights.get(lead_market, 0.0) or 0.0)
    discovery_score = float(payload.get("discovery_score", 0.0) or 0.0)
    volume_24h_krw = float(payload.get("volume_24h_krw", 0.0) or 0.0)
    change_rate = float(payload.get("change_rate", 0.0) or 0.0)
    validated_support = lead_weight >= 0.08
    discovery_support = discovery_score >= 0.50 and volume_24h_krw >= 8_000_000_000
    liquidity_support = volume_24h_krw >= 30_000_000_000
    research_support = validated_support or discovery_support or (liquidity_support and signal_score >= 0.58)
    support_note = (
        f"research_support={research_support} / validated={validated_support} / discovery={discovery_score:.2f} "
        f"/ liquidity KRW {int(volume_24h_krw):,} / change {change_rate:.2f}%"
    )
    rsi_quality_ok = bool(payload.get("rsi_quality_ok", True))
    rsi_bearish_divergence = bool(payload.get("rsi_bearish_divergence", False))
    rsi_extreme = bool(payload.get("rsi_extreme", False))
    micro_ready = bool(payload.get("micro_ready", False))
    micro_score = float(payload.get("micro_score", 0.0) or 0.0)
    micro_vol_ratio = float(payload.get("micro_vol_ratio", 0.0) or 0.0)
    micro_move_3 = float(payload.get("micro_move_3_pct", 0.0) or 0.0)
    micro_move_10 = float(payload.get("micro_move_10_pct", 0.0) or 0.0)
    micro_vwap_gap = float(payload.get("micro_vwap_gap_pct", 0.0) or 0.0)
    micro_range_5 = float(payload.get("micro_range_5_pct", 0.0) or 0.0)
    micro_exhausted = bool(payload.get("micro_exhausted", False))
    stream_fresh = bool(payload.get("stream_fresh", False))
    stream_score = float(payload.get("stream_score", 0.0) or 0.0)
    stream_ignition = bool(payload.get("stream_ignition", False))
    stream_reversal = bool(payload.get("stream_reversal", False))
    stream_age = float(payload.get("stream_age_seconds", 999.0) or 999.0)
    stream_move_5 = float(payload.get("stream_move_5s_pct", 0.0) or 0.0)
    stream_move_15 = float(payload.get("stream_move_15s_pct", 0.0) or 0.0)
    stream_move_60 = float(payload.get("stream_move_60s_pct", 0.0) or 0.0)
    stream_ticks_15 = int(payload.get("stream_ticks_15s", 0) or 0)
    stream_buy_ratio = float(payload.get("stream_buy_ratio_15s", 0.0) or 0.0)
    orderbook_ready = bool(payload.get("orderbook_ready", False))
    orderbook_score = float(payload.get("orderbook_score", 0.0) or 0.0)
    orderbook_bid_ask = float(payload.get("orderbook_bid_ask_ratio", 0.0) or 0.0)
    breakout_count = int(payload.get("breakout_count", 0) or 0)
    vol_ratio = float(payload.get("vol_ratio", 0.0) or 0.0)
    pullback_detected = bool(payload.get("pullback_detected", False))
    pullback_score = float(payload.get("pullback_score", 0.0) or 0.0)
    spike_pct_15m = float(payload.get("spike_pct_15m", 0.0) or 0.0)
    retrace_from_high_pct = float(payload.get("retrace_from_high_pct", 0.0) or 0.0)
    vol_contracted_on_pullback = bool(payload.get("vol_contracted_on_pullback", False))
    trend_follow_score = float(payload.get("trend_follow_score", 0.0) or 0.0)
    trend_alignment = str(payload.get("trend_alignment", "unknown") or "unknown")
    trend_entry_allowed = bool(payload.get("trend_entry_allowed", False))
    trend_slope_pct = float(payload.get("trend_slope_pct", 0.0) or 0.0)
    trend_extension_pct = float(payload.get("trend_extension_pct", 0.0) or 0.0)
    trend_ignition_score = round(
        min(
            1.0,
            min(max(signal_score, 0.0), 1.0) * 0.24
            + min(max(trend_follow_score, 0.0), 1.0) * 0.22
            + min(max(micro_score, 0.0), 1.0) * 0.20
            + min(max(orderbook_score, 0.0), 1.0) * 0.13
            + min(max(discovery_score, 0.0), 1.0) * 0.07
            + min(max(vol_ratio / 3.0, 0.0), 1.0) * 0.08
            + min(max(stream_score, 0.0), 1.0) * 0.06
        ),
        3,
    )
    flow_support = orderbook_score >= 0.48 or orderbook_bid_ask >= 1.02 or stream_ignition
    # research_support removed from ignition_ready: historical backtest weight shouldn't block fresh movers.
    # CryptoDeskAgent already integrated all signals into combined_score — trust it here.
    ignition_ready = trend_ignition_score >= 0.56 and flow_support and trend_entry_allowed
    ignition_note = (
        f"trend_ignition={trend_ignition_score:.2f} / chart={trend_follow_score:.2f} {trend_alignment} "
        f"/ micro={micro_score:.2f} "
        f"/ flow={orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / stream={stream_score:.2f} "
        f"({stream_move_15:.2f}%/15s) / breakout={breakout_count}/4"
    )
    trend_note = (
        f"chart trend gate: {trend_alignment} score={trend_follow_score:.2f} "
        f"slope={trend_slope_pct:.2f}% extension={trend_extension_pct:.2f}%"
    )
    # Pullback entry: prior spike + EMA-zone retracement + volume contraction
    # Better entry price and tighter stop than chasing raw momentum
    pullback_entry_ok = (
        pullback_detected
        and pullback_score >= 0.55
        and trend_entry_allowed
        and trend_follow_score >= 0.52
        and signal_score >= 0.40
        and micro_score >= 0.38
        and (orderbook_score >= 0.44 or orderbook_bid_ask >= 1.0)
        and not rsi_bearish_divergence
    )
    pullback_note = (
        f"pullback score {pullback_score:.2f} / spike {spike_pct_15m:.1f}% / "
        f"retrace {retrace_from_high_pct:.1f}% / vol contracted: {vol_contracted_on_pullback}"
    )
    # Volume gate: ignition entries need real volume confirmation.
    # Pullback entries intentionally have low current volume (contracting on retracement).
    ignition_vol_ok = vol_ratio >= 1.4 or micro_vol_ratio >= 1.5
    late_chase_risk = (
        micro_exhausted
        or micro_move_3 >= 1.8
        or micro_move_10 >= 3.8
        or micro_vwap_gap >= 2.3
        or micro_range_5 >= 3.4
    )
    clean_momentum_window = (
        micro_score >= 0.55
        and micro_vol_ratio >= 1.1
        and -0.35 <= micro_move_3 <= 1.55
        and micro_vwap_gap <= 2.2
        and micro_range_5 <= 3.2
        and not micro_exhausted
    )
    stream_entry_ok = (
        stream_fresh
        and stream_ignition
        and stream_age <= 2.5
        and stream_move_15 >= 0.25
        and stream_move_60 >= -0.15
        and stream_ticks_15 >= 2
        and stream_buy_ratio >= 0.48
        and orderbook_bid_ask >= 0.98
        and trend_entry_allowed
        and trend_follow_score >= 0.52
        and not stream_reversal
        and not late_chase_risk
    )
    strong_late_breakout_exception = (
        signal_score >= 0.76
        and micro_ready
        and orderbook_bid_ask >= 1.15
        and micro_vwap_gap <= 2.8
        and trend_entry_allowed
        and trend_follow_score >= 0.58
    )

    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "High-stress regime. Preserve crypto capital.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + ["Stress regime blocks aggressive crypto entries."],
        }
    hard_overheat = recent_change >= 12.0 or burst_change >= 10.0 or ema_gap >= 8.0 or (rsi_value is not None and float(rsi_value) >= 92.0)
    soft_overheat = recent_change >= 6.0 or burst_change >= 6.5 or ema_gap >= 5.0 or (rsi_value is not None and float(rsi_value) >= 85.0)
    if hard_overheat and not (signal_score >= 0.68 and micro_score >= 0.50 and flow_support):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is extremely overheated. Watch only.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"hard overheat: recent {recent_change:.2f}% / burst {burst_change:.2f}% / ema gap {ema_gap:.2f}% / rsi {rsi_value}", support_note, ignition_note],
        }
    if rsi_bearish_divergence and not ignition_ready:
        reason = "bearish RSI divergence" if rsi_bearish_divergence else "RSI extreme zone" if rsi_extreme else "RSI quality failed"
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} RSI quality filter blocked late chase.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"{reason}; wait for RSI reset before new entry.", ignition_note],
        }
    if stream_reversal and stream_fresh and stream_move_15 <= -0.45:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} stream reversal detected. No fresh long entry.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"stream reversal: 5s {stream_move_5:.2f}% / 15s {stream_move_15:.2f}% / "
                f"60s {stream_move_60:.2f}% / buy {stream_buy_ratio:.0%}",
                ignition_note,
            ],
        }
    if recent_change <= -2.8 or burst_change <= -3.2:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Crypto structure is weakening. Preserve capital.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"recent {recent_change:.2f}% / burst {burst_change:.2f}% triggered protection."],
        }
    if not trend_entry_allowed:
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} chart trend is not aligned for a long entry.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                trend_note,
                "Fast 1m/stream triggers are only allowed inside a 15m uptrend or first-pullback structure.",
                ignition_note,
            ],
        }
    if late_chase_risk and not (pullback_entry_ok or strong_late_breakout_exception):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is moving, but entry is late. Wait for first pullback/reclaim.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"late chase guard: move3 {micro_move_3:.2f}% / move10 {micro_move_10:.2f}% / "
                f"vwap gap {micro_vwap_gap:.2f}% / range5 {micro_range_5:.2f}%",
                "blocks failed-ignition style chase entries while preserving pullback/ICT entries.",
                ignition_note,
            ],
        }

    breakout_partial = bool(payload.get("breakout_partial", False))

    ict_score = float(payload.get("ict_score", 0.0) or 0.0)
    kill_zone_active = bool(payload.get("kill_zone_active", False))
    ssl_sweep_confirmed = bool(payload.get("ssl_sweep_confirmed", False))
    choch_bullish = bool(payload.get("choch_bullish", False))
    choch_bearish = bool(payload.get("choch_bearish", False))
    bos_bearish = bool(payload.get("bos_bearish", False))
    ict_bullish_count = int(payload.get("ict_bullish_count", 0) or 0)
    ict_structure = str(payload.get("ict_structure", "undecided") or "undecided")

    # ICT CHoCH bearish: 추세 반전 하락 — 신규 진입 차단
    if choch_bearish and signal_score < 0.58:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "ICT CHoCH bearish — trend reversing down. No new entries.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"ict_structure: {ict_structure} / signal {signal_score:.2f}"],
        }

    # ICT 컨플루언스 진입: breakout_partial 없어도 ICT 3개 이상이면 허용
    ict_entry_ok = ict_bullish_count >= 3 or (ssl_sweep_confirmed and kill_zone_active) or (choch_bullish and ict_bullish_count >= 2)
    # micro_entry_ok: simplified — needs 1m momentum + volume without requiring all 5 sub-flags of micro_ready
    micro_entry_ok = (
        clean_momentum_window
        and micro_score >= 0.48
        and micro_vol_ratio >= 1.1
        and micro_move_3 >= -1.5
        and orderbook_bid_ask >= 1.0
        and trend_entry_allowed
        and trend_follow_score >= 0.52
    )
    # discovery_entry_ok: removed research_support gate — backtest history shouldn't block fresh opportunities.
    # 2026-04-29: signal 0.52 → 0.56, micro 0.44 → 0.46, ob 0.98 → 1.0 after data showed
    # all selective_probe entries at 0.48~0.55 hit -0.7~-0.9% within minutes (failed_ignition).
    discovery_entry_ok = (
        signal_score >= 0.56
        and micro_score >= 0.46
        and orderbook_bid_ask >= 1.0
        and trend_entry_allowed
        and trend_follow_score >= 0.54
        and not late_chase_risk
        and not hard_overheat
    )
    # direct_entry_ok: the bot's core purpose — if CryptoDeskAgent's combined_score is high,
    # don't re-run the same gates. Catch the moment immediately.
    direct_entry_ok = (
        signal_score >= 0.63
        and (clean_momentum_window or strong_late_breakout_exception or stream_entry_ok)
        and orderbook_bid_ask >= 1.02
        and trend_entry_allowed
        and trend_follow_score >= 0.56
        and not rsi_bearish_divergence
    )
    # Volume gate: direct_entry_ok (high combined score) also bypasses
    if not ignition_vol_ok and not pullback_entry_ok and not ict_entry_ok and not direct_entry_ok and not stream_entry_ok and stance != "DEFENSE":
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} volume too low for entry. Wait for volume confirmation.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"volume gate: 15m vol {vol_ratio:.1f}x / 1m vol {micro_vol_ratio:.1f}x — need 1.4x/1.5x",
                ignition_note, support_note,
            ],
        }

    # Pullback entry path: prior spike + controlled retracement to EMA + volume contraction
    # This is the Ross Cameron 'first red candle' / Raschke Holy Grail entry
    if pullback_entry_ok and stance != "DEFENSE" and not hard_overheat:
        entry_size = "0.65x" if validated_support else "0.50x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} pullback entry — retracement near EMA after spike.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [pullback_note, trend_note, ignition_note, support_note],
        }

    # Direct entry: combined_score is the signal — enter now without extra gate chains
    if direct_entry_ok and stance != "DEFENSE" and not hard_overheat:
        if signal_score >= 0.76:
            entry_size = "0.90x"
        elif signal_score >= 0.70:
            entry_size = "0.75x"
        else:
            entry_size = "0.58x"
        if soft_overheat:
            entry_size = "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} direct momentum entry — combined signal {signal_score:.2f}.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"direct entry: combined={signal_score:.2f} micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x",
                f"clean momentum window: move3={micro_move_3:.2f}% move10={micro_move_10:.2f}% vwap_gap={micro_vwap_gap:.2f}% range5={micro_range_5:.2f}%",
                f"stream: score={stream_score:.2f} move15={stream_move_15:.2f}% ticks15={stream_ticks_15} buy={stream_buy_ratio:.0%}",
                trend_note, ignition_note, support_note,
            ],
        }

    if stream_entry_ok and stance != "DEFENSE" and signal_score >= 0.58:
        return {
            "action": "selective_probe",
            "size": "0.48x",
            "focus": f"{lead_market or 'KRW-BTC'} tick ignition entry from Upbit stream.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"stream ignition: score={stream_score:.2f} age={stream_age:.2f}s "
                f"move5={stream_move_5:.2f}% move15={stream_move_15:.2f}% move60={stream_move_60:.2f}% "
                f"ticks15={stream_ticks_15} buy={stream_buy_ratio:.0%}",
                trend_note,
                ignition_note,
                support_note,
            ],
        }

    if ignition_ready and stance != "DEFENSE" and (micro_entry_ok or stream_entry_ok or breakout_count >= 2 or trend_ignition_score >= 0.60):
        entry_size = "0.88x" if trend_ignition_score >= 0.68 else "0.68x"
        if soft_overheat:
            entry_size = "0.42x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} trend ignition long. Trail instead of fixed early take-profit.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [trend_note, ignition_note, support_note, "RSI is treated as momentum context, not an automatic sell signal."],
        }

    if soft_overheat and discovery_entry_ok and stance != "DEFENSE":
        return {
            "action": "selective_probe",
            "size": "0.32x",
            "focus": f"{lead_market or 'KRW-BTC'} is hot, but discovery signal allows a small test.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, support_note, f"soft overheat controlled by small size: recent {recent_change:.2f}% / burst {burst_change:.2f}% / rsi {rsi_value}"],
        }

    # 단타 스윙: 3/4 이상이면 풀사이즈 진입, 임계값 대폭 완화
    # lead_weight threshold lowered to 0.08 to accommodate 9-coin neutral-weight universe (max ~0.14)
    offense_threshold = 0.58 if regime == "RANGING" else 0.55
    if bias == "offense" and signal_score >= offense_threshold and stance != "DEFENSE" and ema_gap <= 5.0 and (breakout_partial or ict_entry_ok or micro_entry_ok or stream_entry_ok or discovery_entry_ok or direct_entry_ok):
        entry_size = "0.85x" if discovery_support and not validated_support else "1.0x"
        if stance == "OFFENSE":
            entry_size = "0.95x" if discovery_support and not validated_support else "1.15x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} 단타 스윙 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, f"signal {signal_score:.2f} / micro {micro_score:.2f} / stream {stream_score:.2f} ({stream_move_15:.2f}%/15s) / orderbook {orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / ema gap {ema_gap:.2f}% / vol {vol_ratio:.1f}x / 1m vol {micro_vol_ratio:.1f}x / breakout {breakout_count}/4 / ict {ict_bullish_count}/5 {ict_structure}"],
        }
    # 신호 점수만 충분하면 선택적 진입 (research_support 게이트 제거 — 새로운 모멘텀 코인도 포착)
    # 2026-04-29: threshold 0.48 → 0.54 — selective_probe entries below 0.55 dominated failed_ignition list.
    if bias == "offense" and signal_score >= max(offense_threshold - 0.04, 0.54) and stance != "DEFENSE":
        return {
            "action": "selective_probe",
            "size": "0.55x" if discovery_support and not validated_support else "0.70x",
            "focus": f"{lead_market or 'KRW-BTC'} 공격적 탐색 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, f"offense bias / signal {signal_score:.2f} / breakout {breakout_count}/4 / weight {lead_weight:.2f}", support_note],
        }

    # micro_entry_ok 단독 진입: signal_score >= 0.48 → 0.55 (failed_ignition 데이터 기반 상향)
    if micro_entry_ok and stance != "DEFENSE" and signal_score >= 0.55:
        return {
            "action": "selective_probe",
            "size": "0.45x" if discovery_support and not validated_support else "0.55x",
            "focus": f"{lead_market or 'KRW-BTC'} 1m momentum entry while swing setup is forming.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, f"1m micro ready / micro {micro_score:.2f} / orderbook {orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / 1m vol {micro_vol_ratio:.1f}x / move3 {micro_move_3:.2f}% / swing {signal_score:.2f}", support_note],
        }

    mild_defense = (
        regime == "RANGING"
        and stance != "DEFENSE"
        and signal_score >= 0.33
        and recent_change > -0.5
        and ema_gap > -0.35
        and research_support
    )
    if bias == "defense" and mild_defense:
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is still defensive, but close to a pilot watch state.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"signal {signal_score:.2f} / recent {recent_change:.2f}% / ema gap {ema_gap:.2f}% / weight {lead_weight:.2f}", support_note],
        }
    if bias == "defense":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Crypto structure remains weak. No new exposure.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + ["Wait for momentum recovery before new crypto entries."],
        }

    # balanced 바이어스: research_support 게이트 제거 — 신호가 있으면 진입
    # 2026-04-29: threshold 0.48/0.52 → 0.54/0.58 (failed_ignition 패턴 차단)
    pilot_probe_threshold = 0.54 if recent_change >= -0.3 else 0.58
    if bias == "balanced" and signal_score >= pilot_probe_threshold and stance != "DEFENSE" and ema_gap <= 5.0 and recent_change > -1.5:
        return {
            "action": "probe_longs",
            "size": "0.42x" if discovery_support and not validated_support else "0.60x",
            "focus": f"{lead_market or 'KRW-BTC'} balanced 단타 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"signal {signal_score:.2f} / ema gap {ema_gap:.2f}% / threshold {pilot_probe_threshold:.2f}", support_note],
        }
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": "Crypto confirmation watch.",
        "symbol": lead_market,
        "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"waiting for stronger confirmation (current {signal_score:.2f}, target {pilot_probe_threshold:.2f}+)", ignition_note, support_note],
    }


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_gap_count = int(payload.get("active_gap_count", 0) or 0)
    breakout_confirmed_count = int(payload.get("breakout_confirmed_count", 0) or 0)
    breakout_partial_count = int(payload.get("breakout_partial_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_gap = float(payload.get("avg_gap_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    gap_candidates = payload.get("gap_candidates", []) or []
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in gap_candidates if str(item.get("ticker", "")).strip()]
    top_name = str(gap_candidates[0].get("name", "No leader")) if gap_candidates else "No leader"
    top_ticker = str(gap_candidates[0].get("ticker", "")) if gap_candidates else ""
    top_signal = float(gap_candidates[0].get("signal_score", 0.0) or 0.0) if gap_candidates else 0.0
    top_gap = float(gap_candidates[0].get("gap_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_rsi = float(gap_candidates[0].get("rsi", 0.0) or 0.0) if gap_candidates else 0.0
    top_burst = float(gap_candidates[0].get("burst_change_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_penalty = float(gap_candidates[0].get("overheat_penalty", 0.0) or 0.0) if gap_candidates else 0.0
    top_candidate_score = float(gap_candidates[0].get("candidate_score", 0.0) or 0.0) if gap_candidates else 0.0
    top_signal_bias = str(gap_candidates[0].get("signal_bias", "neutral") or "neutral") if gap_candidates else "neutral"
    # Best breakout candidate among merged gap_candidates
    bk_leader = next((c for c in gap_candidates if int(c.get("breakout_count", 0) or 0) >= 3), None)
    opening_window = bool(session.get("korea_opening_window"))
    mid_session = bool(session.get("korea_mid_session"))
    _qmeta = {"quality_score": quality_score, "avg_signal": avg_signal, "quality_threshold": 0.54}

    if not session.get("korea_open"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "Korea desk is outside market hours.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Scan the next open for rotation leaders."],
            **_qmeta,
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Stress regime. No new Korea stock exposure.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Risk committee blocked fresh Korea entries in stress mode."],
            **_qmeta,
        }
    if gap_candidates and (top_signal < 0.5 or top_gap >= 12.0 or top_rsi >= 80.0 or top_burst >= 12.0 or avg_volume < 2200):
        # Even if gap leader is overheated, a clean breakout candidate can still fire
        if bk_leader and breakout_confirmed_count >= 1 and stance != "DEFENSE":
            bk_ticker = str(bk_leader.get("ticker", ""))
            bk_name = str(bk_leader.get("name", bk_ticker))
            bk_score = float(bk_leader.get("candidate_score", 0.0) or 0.0)
            return {
                "action": "probe_longs",
                "size": "0.35x",
                "focus": f"Breakout confirmed: {bk_name} (gap leader overheated, using breakout path).",
                "symbol": bk_ticker,
                "candidate_symbols": [bk_ticker],
                "notes": [
                    f"gap leader overheated but breakout candidate {bk_name} all-4 confirmed",
                    f"breakout score {bk_score:.2f} / vol_ratio {bk_leader.get('vol_ratio', 0):.1f}x",
                ] + list(bk_leader.get("breakout_reasons", []))[:3],
                **_qmeta,
            }
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"Leader {top_name} is overheated or under-confirmed.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"top signal {top_signal:.2f} / gap {top_gap:.2f}% / rsi {top_rsi:.1f} / burst {top_burst:.2f}% / penalty {top_penalty:.2f}",
                "Wait for cleaner follow-through before touching the leader.",
            ],
            **_qmeta,
        }

    # ── Momentum breakout path (stock_backtest_v3 validated strategy) ──────
    # Fires independently of gap-up conditions; works in any session window
    if breakout_confirmed_count >= 1 and stance != "DEFENSE" and bk_leader:
        bk_ticker = str(bk_leader.get("ticker", ""))
        bk_name = str(bk_leader.get("name", bk_ticker))
        bk_score = float(bk_leader.get("candidate_score", 0.0) or 0.0)
        return {
            "action": "probe_longs",
            "size": "0.55x" if stance == "OFFENSE" else "0.40x",
            "focus": f"Momentum breakout: {bk_name} — all 4 signals confirmed.",
            "symbol": bk_ticker,
            "candidate_symbols": [bk_ticker] + [
                str(c.get("ticker", "")) for c in gap_candidates
                if str(c.get("ticker", "")) != bk_ticker
            ][:2],
            "notes": [
                f"breakout confirmed {breakout_confirmed_count} stock(s) / partial {breakout_partial_count}",
                f"candidate score {bk_score:.2f} / vol_ratio {bk_leader.get('vol_ratio', 0):.1f}x",
            ] + list(bk_leader.get("breakout_reasons", []))[:3],
            **_qmeta,
        }
    if breakout_partial_count >= 1 and stance != "DEFENSE" and bk_leader:
        bk_ticker = str(bk_leader.get("ticker", ""))
        bk_name = str(bk_leader.get("name", bk_ticker))
        bk_score = float(bk_leader.get("candidate_score", 0.0) or 0.0)
        return {
            "action": "selective_probe",
            "size": "0.30x",
            "focus": f"Breakout partial ({bk_name}) — 3/4 signals confirmed.",
            "symbol": bk_ticker,
            "candidate_symbols": [bk_ticker],
            "notes": [
                f"partial breakout {breakout_partial_count} stock(s) / candidate score {bk_score:.2f}",
            ] + list(bk_leader.get("breakout_reasons", []))[:3],
            **_qmeta,
        }

    if opening_window and active_gap_count >= 2 and quality_score >= 0.56 and avg_gap >= 1.8 and avg_volume >= 8000 and avg_signal >= 0.52 and top_candidate_score >= 0.58 and top_signal_bias != "neutral" and stance != "DEFENSE":
        return {
            "action": "attack_opening_drive",
            "size": "0.55x" if stance == "BALANCED" else "0.75x",
            "focus": f"Opening drive follow-through on {top_name}.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"active gaps {active_gap_count}",
                f"quality {quality_score:.2f} / top candidate {top_candidate_score:.2f} / avg gap {avg_gap:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
            **_qmeta,
        }
    if active_gap_count >= 1 and quality_score >= 0.5 and avg_signal >= 0.48 and avg_volume >= 3500 and top_candidate_score >= 0.52:
        return {
            "action": "selective_probe",
            "size": "0.40x",
            "focus": f"{top_name} selective probe while confirmation improves.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"watchlist candidates {active_gap_count}",
                f"quality {quality_score:.2f} / top candidate {top_candidate_score:.2f} / avg gap {avg_gap:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
                "Only selective exploration until follow-through proves itself.",
            ],
            **_qmeta,
        }
    # Single strong candidate — smaller size, tighter criteria
    if active_gap_count >= 1 and quality_score >= 0.54 and avg_signal >= 0.5 and top_candidate_score >= 0.56 and not mid_session:
        return {
            "action": "selective_probe",
            "size": "0.25x",
            "focus": f"{top_name} cautious single-candidate probe (opening window).",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"single candidate quality {quality_score:.2f} / signal {avg_signal:.2f} / candidate_score {top_candidate_score:.2f}",
                "Small size — only 1 gap candidate confirmed.",
            ],
            **_qmeta,
        }
    if mid_session and active_gap_count >= 1 and quality_score >= 0.58 and avg_signal >= 0.52 and top_candidate_score >= 0.58:
        return {
            "action": "selective_probe",
            "size": "0.18x",
            "focus": f"{top_name} mid-session follow-through probe.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"mid-session quality {quality_score:.2f} / signal {avg_signal:.2f} / candidate_score {top_candidate_score:.2f}",
                "Small size — mid-session entry, requires high conviction.",
            ],
            **_qmeta,
        }
    if mid_session:
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "No strong Korea afternoon follow-through candidate.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Preserve capital unless a cleaner afternoon drive appears."],
            **_qmeta,
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No Korea setup is strong enough right now.",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"stay patient (quality {quality_score:.2f} / signal {avg_signal:.2f})"],
        **_qmeta,
    }


def build_us_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_us_count = int(payload.get("active_us_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_change = float(payload.get("avg_change_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    leaders = payload.get("leaders", []) or []
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in leaders if str(item.get("ticker", "")).strip()]
    top_ticker = candidate_symbols[0] if candidate_symbols else ""
    top_signal = float(leaders[0].get("signal_score", 0.0) or 0.0) if leaders else 0.0
    top_change = float(leaders[0].get("change_pct", 0.0) or 0.0) if leaders else 0.0
    _qmeta = {"quality_score": quality_score, "avg_signal": avg_signal, "quality_threshold": 0.72}

    if not (session.get("us_premarket") or session.get("us_regular")):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "US desk is outside session hours.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Review leaders again during pre-market or regular hours."],
            **_qmeta,
        }
    if session.get("us_premarket") and not session.get("us_regular"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": f"{top_ticker or 'US leaders'} pre-market watch only.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Wait for regular session confirmation before entering US names."],
            **_qmeta,
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Stress regime. No new US equity exposure.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Risk committee blocked fresh US entries in stress mode."],
            **_qmeta,
        }
    if leaders and (top_signal < 0.56 or top_change >= 8.5):
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"US leader {top_ticker} is overheated or under-confirmed.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"top signal {top_signal:.2f} / top change {top_change:.2f}%",
                "Wait for cleaner regular-session follow-through.",
            ],
            **_qmeta,
        }
    if quality_score < 0.62 or avg_signal < 0.52 or active_us_count < 2 or avg_change < 0.20:
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "US momentum quality is still too weak.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"quality {quality_score:.2f} / active leaders {active_us_count} / avg change {avg_change:.2f}% / avg signal {avg_signal:.2f}",
                "US entries need a stronger regular-session backdrop.",
            ],
            **_qmeta,
        }
    if active_us_count >= 4 and quality_score >= 0.76 and avg_change >= 0.55 and avg_volume >= 2000000 and avg_signal >= 0.66 and stance != "DEFENSE":
        return {
            "action": "probe_longs",
            "size": "0.25x" if stance == "BALANCED" else "0.40x",
            "focus": f"US leader follow-through on {top_ticker}.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"leaders in force {active_us_count}",
                f"quality {quality_score:.2f} / avg change {avg_change:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
            **_qmeta,
        }
    if active_us_count >= 3 and quality_score >= 0.70 and avg_signal >= 0.60:
        return {
            "action": "selective_probe",
            "size": "0.15x",
            "focus": f"{top_ticker} selective probe watch.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"watchlist leaders {active_us_count}",
                f"quality {quality_score:.2f} / avg change {avg_change:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
            **_qmeta,
        }
    # 2-leader fallback — small size, tighter individual stock requirement
    if active_us_count >= 2 and quality_score >= 0.64 and avg_signal >= 0.54 and top_signal >= 0.60 and stance != "DEFENSE":
        return {
            "action": "selective_probe",
            "size": "0.10x",
            "focus": f"{top_ticker} cautious 2-leader probe.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"2-leader setup / quality {quality_score:.2f} / avg signal {avg_signal:.2f} / top signal {top_signal:.2f}",
                "Small size — only 2 confirmed leaders in session.",
            ],
            **_qmeta,
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No US leader is strong enough right now.",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"stay selective (quality {quality_score:.2f} / signal {avg_signal:.2f})"],
        **_qmeta,
    }

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
    weight_support = lead_weight >= 0.28
    rsi_quality_ok = bool(payload.get("rsi_quality_ok", True))
    rsi_bearish_divergence = bool(payload.get("rsi_bearish_divergence", False))
    rsi_extreme = bool(payload.get("rsi_extreme", False))
    micro_ready = bool(payload.get("micro_ready", False))
    micro_score = float(payload.get("micro_score", 0.0) or 0.0)
    micro_vol_ratio = float(payload.get("micro_vol_ratio", 0.0) or 0.0)
    micro_move_3 = float(payload.get("micro_move_3_pct", 0.0) or 0.0)
    orderbook_ready = bool(payload.get("orderbook_ready", False))
    orderbook_score = float(payload.get("orderbook_score", 0.0) or 0.0)
    orderbook_bid_ask = float(payload.get("orderbook_bid_ask_ratio", 0.0) or 0.0)

    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "High-stress regime. Preserve crypto capital.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + ["Stress regime blocks aggressive crypto entries."],
        }
    if recent_change >= 3.4 or burst_change >= 3.8 or ema_gap >= 2.8 or (rsi_value is not None and float(rsi_value) >= 82.0):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is overheated. Watch only.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"recent {recent_change:.2f}% / burst {burst_change:.2f}% / ema gap {ema_gap:.2f}% / rsi {rsi_value}"],
        }
    if not rsi_quality_ok:
        reason = "bearish RSI divergence" if rsi_bearish_divergence else "RSI extreme zone" if rsi_extreme else "RSI quality failed"
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} RSI quality filter blocked late chase.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"{reason}; wait for RSI reset before new entry."],
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

    breakout_confirmed = bool(payload.get("breakout_confirmed", False))
    breakout_partial = bool(payload.get("breakout_partial", False))
    breakout_count = int(payload.get("breakout_count", 0) or 0)
    vol_ratio = float(payload.get("vol_ratio", 0.0) or 0.0)

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
    micro_entry_ok = (
        micro_ready
        and micro_score >= 0.68
        and micro_vol_ratio >= 1.8
        and -0.2 <= micro_move_3 <= 2.2
        and (orderbook_ready or orderbook_score >= 0.58)
    )

    # 단타 스윙: 3/4 이상이면 풀사이즈 진입, 임계값 대폭 완화
    # lead_weight threshold lowered to 0.08 to accommodate 9-coin neutral-weight universe (max ~0.14)
    offense_threshold = 0.60 if regime == "RANGING" else 0.58
    if bias == "offense" and signal_score >= offense_threshold and stance != "DEFENSE" and ema_gap <= 3.0 and lead_weight >= 0.08 and (breakout_partial or ict_entry_ok or micro_entry_ok):
        return {
            "action": "probe_longs",
            "size": "1.0x" if stance == "BALANCED" else "1.3x",
            "focus": f"{lead_market or 'KRW-BTC'} 단타 스윙 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"signal {signal_score:.2f} / micro {micro_score:.2f} / orderbook {orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / ema gap {ema_gap:.2f}% / weight {lead_weight:.2f} / vol {vol_ratio:.1f}x / 1m vol {micro_vol_ratio:.1f}x / breakout {breakout_count}/4 / ict {ict_bullish_count}/5 {ict_structure}"],
        }
    # 신호 점수만 충분하면 선택적 진입
    if bias == "offense" and signal_score >= max(offense_threshold - 0.05, 0.52) and stance != "DEFENSE" and lead_weight >= 0.08:
        return {
            "action": "selective_probe",
            "size": "0.70x",
            "focus": f"{lead_market or 'KRW-BTC'} 공격적 탐색 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"offense bias / signal {signal_score:.2f} / breakout {breakout_count}/4 / weight {lead_weight:.2f}"],
        }

    if micro_entry_ok and stance != "DEFENSE" and lead_weight >= 0.08 and signal_score >= 0.50:
        return {
            "action": "selective_probe",
            "size": "0.55x",
            "focus": f"{lead_market or 'KRW-BTC'} 1m momentum entry while swing setup is forming.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"1m micro ready / micro {micro_score:.2f} / orderbook {orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / 1m vol {micro_vol_ratio:.1f}x / move3 {micro_move_3:.2f}% / swing {signal_score:.2f}"],
        }

    mild_defense = (
        regime == "RANGING"
        and stance != "DEFENSE"
        and signal_score >= 0.33
        and recent_change > -0.5
        and ema_gap > -0.35
        and lead_weight >= 0.10
    )
    if bias == "defense" and mild_defense:
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is still defensive, but close to a pilot watch state.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"signal {signal_score:.2f} / recent {recent_change:.2f}% / ema gap {ema_gap:.2f}% / weight {lead_weight:.2f}"],
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

    # balanced 바이어스에서도 적극 진입
    pilot_probe_threshold = 0.52 if lead_weight >= 0.10 and recent_change >= -0.5 else 0.55
    if bias == "balanced" and signal_score >= pilot_probe_threshold and stance != "DEFENSE" and ema_gap <= 2.5 and recent_change > -1.5:
        return {
            "action": "probe_longs",
            "size": "0.60x",
            "focus": f"{lead_market or 'KRW-BTC'} balanced 단타 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"signal {signal_score:.2f} / ema gap {ema_gap:.2f}% / threshold {pilot_probe_threshold:.2f}"],
        }
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": "Crypto confirmation watch.",
        "symbol": lead_market,
        "candidate_symbols": candidate_symbols,
        "notes": reasons + [f"waiting for stronger confirmation (current {signal_score:.2f}, target {pilot_probe_threshold:.2f}+)"],
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

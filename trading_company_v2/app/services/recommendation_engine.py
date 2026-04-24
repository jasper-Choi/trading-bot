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
    lead_weight = float(backtest_weights.get(lead_market, 0.0) or 0.0)
    weight_support = lead_weight >= 0.28

    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "High-stress regime. Preserve crypto capital.",
            "symbol": lead_market,
            "notes": reasons + ["Stress regime blocks aggressive crypto entries."],
        }
    if recent_change >= 3.4 or burst_change >= 3.8 or ema_gap >= 2.8 or (rsi_value is not None and float(rsi_value) >= 74.0):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is overheated. Watch only.",
            "symbol": lead_market,
            "notes": reasons + [f"recent {recent_change:.2f}% / burst {burst_change:.2f}% / ema gap {ema_gap:.2f}% / rsi {rsi_value}"],
        }
    if recent_change <= -2.8 or burst_change <= -3.2:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Crypto structure is weakening. Preserve capital.",
            "symbol": lead_market,
            "notes": reasons + [f"recent {recent_change:.2f}% / burst {burst_change:.2f}% triggered protection."],
        }

    offense_threshold = 0.68 if regime == "RANGING" else 0.64
    if bias == "offense" and signal_score >= offense_threshold and stance != "DEFENSE" and ema_gap <= 2.3 and lead_weight >= 0.26:
        return {
            "action": "probe_longs",
            "size": "0.65x" if stance == "BALANCED" else "0.85x",
            "focus": f"{lead_market or 'KRW-BTC'} volatility breakout probe.",
            "symbol": lead_market,
            "notes": reasons + [f"signal {signal_score:.2f} / ema gap {ema_gap:.2f}% / weight {lead_weight:.2f} / breakout mode"],
        }
    if bias == "offense" and signal_score >= max(offense_threshold - 0.05, 0.6) and stance != "DEFENSE" and lead_weight >= 0.18:
        return {
            "action": "selective_probe",
            "size": "0.40x",
            "focus": f"{lead_market or 'KRW-BTC'} selective breakout watch.",
            "symbol": lead_market,
            "notes": reasons + [f"offense bias supported but still below full breakout confidence / weight {lead_weight:.2f}"],
        }

    mild_defense = (
        regime == "RANGING"
        and stance != "DEFENSE"
        and signal_score >= 0.33
        and recent_change > -0.5
        and ema_gap > -0.35
        and lead_weight >= 0.28
    )
    if bias == "defense" and mild_defense:
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is still defensive, but close to a pilot watch state.",
            "symbol": lead_market,
            "notes": reasons + [f"signal {signal_score:.2f} / recent {recent_change:.2f}% / ema gap {ema_gap:.2f}% / weight {lead_weight:.2f}"],
        }
    if bias == "defense":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Crypto structure remains weak. No new exposure.",
            "symbol": lead_market,
            "notes": reasons + ["Wait for momentum recovery before new crypto entries."],
        }

    pilot_probe_threshold = 0.54 if lead_weight >= 0.30 and recent_change >= -0.4 else 0.57
    if bias == "balanced" and signal_score >= pilot_probe_threshold and stance != "DEFENSE" and ema_gap <= 1.5 and recent_change > -1.0:
        return {
            "action": "probe_longs",
            "size": "0.30x",
            "focus": f"{lead_market or 'KRW-BTC'} balanced breakout pilot.",
            "symbol": lead_market,
            "notes": reasons + [f"signal {signal_score:.2f} / ema gap {ema_gap:.2f}% / threshold {pilot_probe_threshold:.2f}"],
        }
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": "Crypto confirmation watch.",
        "symbol": lead_market,
        "notes": reasons + [f"waiting for stronger confirmation (current {signal_score:.2f}, target {pilot_probe_threshold:.2f}+)"],
    }


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_gap_count = int(payload.get("active_gap_count", 0) or 0)
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
    opening_window = bool(session.get("korea_opening_window"))
    mid_session = bool(session.get("korea_mid_session"))
    _qmeta = {"quality_score": quality_score, "avg_signal": avg_signal, "quality_threshold": 0.58}

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
    if gap_candidates and (top_signal < 0.52 or top_gap >= 18.0 or top_rsi >= 78.0 or top_burst >= 12.0 or avg_volume < 2500):
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
    if opening_window and active_gap_count >= 2 and quality_score >= 0.62 and avg_gap >= 2.2 and avg_volume >= 10000 and avg_signal >= 0.56 and top_candidate_score >= 0.64 and top_signal_bias != "neutral" and stance != "DEFENSE":
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
    if active_gap_count >= 1 and quality_score >= 0.54 and avg_signal >= 0.5 and avg_volume >= 5000 and top_candidate_score >= 0.56:
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
    if active_gap_count >= 1 and quality_score >= 0.58 and avg_signal >= 0.54 and top_candidate_score >= 0.6 and not mid_session:
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
    if mid_session and active_gap_count >= 1 and quality_score >= 0.64 and avg_signal >= 0.56 and top_candidate_score >= 0.62:
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

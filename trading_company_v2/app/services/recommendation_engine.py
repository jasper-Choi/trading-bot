from __future__ import annotations

from typing import Any


def build_crypto_plan(stance: str, regime: str, payload: dict[str, Any]) -> dict[str, Any]:
    bias = payload.get("desk_bias", "balanced")
    signal_score = float(payload.get("signal_score", 0.5) or 0.5)
    recent_change = float(payload.get("recent_change_pct", 0.0) or 0.0)
    burst_change = float(payload.get("burst_change_pct", 0.0) or 0.0)
    ema_gap = float(payload.get("ema_gap_pct", 0.0) or 0.0)
    rsi_value = payload.get("rsi")
    reasons = payload.get("reasons", [])
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Protect capital until stress fades",
            "symbol": payload.get("lead_market", ""),
            "notes": reasons + ["stress regime blocks offensive crypto entries"],
        }
    if recent_change >= 2.6 or burst_change >= 3.0 or ema_gap >= 2.4 or (rsi_value is not None and float(rsi_value) >= 69.0):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"Skip overheated crypto burst in {payload.get('lead_market', 'KRW-BTC')}",
            "symbol": payload.get("lead_market", ""),
            "notes": reasons + [f"recent {recent_change:.2f}% / burst {burst_change:.2f}% / ema gap {ema_gap:.2f}% / rsi {rsi_value}"],
        }
    if recent_change <= -2.2 or burst_change <= -2.8:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Crypto structure is weakening too quickly",
            "symbol": payload.get("lead_market", ""),
            "notes": reasons + [f"recent {recent_change:.2f}% / burst {burst_change:.2f}% indicates downside pressure"],
        }
    offense_threshold = 0.76 if regime == "RANGING" else 0.72
    if bias == "offense" and signal_score >= offense_threshold and stance != "DEFENSE" and ema_gap <= 2.0:
        return {
            "action": "probe_longs",
            "size": "0.40x" if stance == "BALANCED" else "0.60x",
            "focus": f"Watch {payload.get('lead_market', 'KRW-BTC')} continuation",
            "symbol": payload.get("lead_market", ""),
            "notes": reasons + [f"offense threshold cleared at {signal_score:.2f} / ema gap {ema_gap:.2f}%"],
        }
    if bias == "defense":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "No fresh crypto exposure while structure is weak",
            "symbol": payload.get("lead_market", ""),
            "notes": reasons + ["stand aside until momentum recovers"],
        }
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": "Selective crypto tracking",
        "symbol": payload.get("lead_market", ""),
        "notes": reasons + ["wait for stronger confirmation before probing"],
    }


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_gap_count = int(payload.get("active_gap_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_gap = float(payload.get("avg_gap_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    gap_candidates = payload.get("gap_candidates", [])
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in gap_candidates if str(item.get("ticker", "")).strip()]
    top_name = gap_candidates[0]["name"] if gap_candidates else "No leader"
    top_ticker = gap_candidates[0]["ticker"] if gap_candidates else ""
    top_signal = float(gap_candidates[0].get("signal_score", 0.0) or 0.0) if gap_candidates else 0.0
    top_gap = float(gap_candidates[0].get("gap_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_rsi = float(gap_candidates[0].get("rsi", 0.0) or 0.0) if gap_candidates else 0.0
    top_burst = float(gap_candidates[0].get("burst_change_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_penalty = float(gap_candidates[0].get("overheat_penalty", 0.0) or 0.0) if gap_candidates else 0.0
    opening_window = bool(session.get("korea_opening_window"))
    mid_session = bool(session.get("korea_mid_session"))

    if not session.get("korea_open"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "Korea desk idle outside local market hours",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["review leader rotation after next KOSDAQ open"],
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "No fresh KOSDAQ exposure under stressed regime",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["risk committee blocks new Korea entries"],
        }
    if gap_candidates and (top_signal < 0.55 or top_gap >= 24.0 or top_rsi >= 74.0 or top_burst >= 10.0 or avg_volume < 3000):
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"Skip overheated or weakly confirmed leader {top_name}",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"top candidate signal {top_signal:.2f} / gap {top_gap:.2f}% / rsi {top_rsi:.1f} / burst {top_burst:.2f}% / penalty {top_penalty:.2f}",
                "skip overstretched opening move until structure stabilizes",
            ],
        }
    if opening_window and active_gap_count >= 3 and quality_score >= 0.72 and avg_gap >= 3.2 and avg_volume >= 20000 and avg_signal >= 0.64 and stance != "DEFENSE":
        return {
            "action": "attack_opening_drive",
            "size": "0.50x" if stance == "BALANCED" else "0.70x",
            "focus": f"Track leader {top_name}",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"{active_gap_count} gap candidates with liquidity support",
                f"quality score {quality_score:.2f} / avg gap {avg_gap:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
        }
    if active_gap_count >= 2 and quality_score >= 0.6 and avg_signal >= 0.54 and avg_volume >= 10000:
        return {
            "action": "selective_probe",
            "size": "0.30x",
            "focus": f"Wait for confirmation in {top_name}" if opening_window else f"Late confirmation only in {top_name}",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"{active_gap_count} candidate(s) worth monitoring",
                f"quality score {quality_score:.2f} / avg gap {avg_gap:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
                "opening window selective probe" if opening_window else "mid-session selective only",
            ],
        }
    if mid_session:
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "No late-session Korea breakout setup worth chasing",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["skip mediocre midday continuation and preserve risk budget"],
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No quality opening-drive candidate right now",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"skip weak gaps and preserve attention (quality {quality_score:.2f} / signal {avg_signal:.2f})"],
    }


def build_us_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_us_count = int(payload.get("active_us_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_change = float(payload.get("avg_change_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    leaders = payload.get("leaders", [])
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in leaders if str(item.get("ticker", "")).strip()]
    top_ticker = candidate_symbols[0] if candidate_symbols else ""
    top_signal = float(leaders[0].get("signal_score", 0.0) or 0.0) if leaders else 0.0
    top_change = float(leaders[0].get("change_pct", 0.0) or 0.0) if leaders else 0.0

    if not (session.get("us_premarket") or session.get("us_regular")):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "U.S. desk idle outside market window",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["review U.S. core leaders during premarket or regular session"],
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "No fresh U.S. exposure under stressed regime",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["risk committee blocks new U.S. entries"],
        }
    if leaders and (top_signal < 0.56 or top_change >= 8.5):
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"Skip overheated or weakly confirmed U.S. leader {top_ticker}",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"top candidate signal {top_signal:.2f} / day change {top_change:.2f}%",
                "wait for a cleaner U.S. follow-through setup",
            ],
        }
        
    if active_us_count >= 3 and quality_score >= 0.68 and avg_change >= 0.45 and avg_volume >= 1000000 and avg_signal >= 0.58 and stance != "DEFENSE":
        return {
            "action": "probe_longs",
            "size": "0.35x" if stance == "BALANCED" else "0.50x",
            "focus": f"Follow U.S. leader {top_ticker}",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"{active_us_count} U.S. leaders trading firm",
                f"quality score {quality_score:.2f} / avg change {avg_change:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
        }
    if active_us_count >= 2 and quality_score >= 0.58 and avg_signal >= 0.52:
        return {
            "action": "selective_probe",
            "size": "0.20x",
            "focus": f"Wait for follow-through in {top_ticker}",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"{active_us_count} U.S. candidates worth monitoring",
                f"quality score {quality_score:.2f} / avg change {avg_change:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No quality U.S. leader setup right now",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"skip weak U.S. follow-through (quality {quality_score:.2f} / signal {avg_signal:.2f})"],
    }

from __future__ import annotations

from typing import Any


def build_crypto_plan(stance: str, regime: str, payload: dict[str, Any]) -> dict[str, Any]:
    bias = payload.get("desk_bias", "balanced")
    reasons = payload.get("reasons", [])
    if regime == "STRESSED":
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "Protect capital until stress fades",
            "notes": reasons + ["stress regime blocks offensive crypto entries"],
        }
    if bias == "offense" and stance != "DEFENSE":
        return {
            "action": "probe_longs",
            "size": "0.50x" if stance == "BALANCED" else "0.75x",
            "focus": f"Watch {payload.get('lead_market', 'KRW-BTC')} continuation",
            "notes": reasons,
        }
    if bias == "defense":
        return {
            "action": "reduce_risk",
            "size": "0.25x",
            "focus": "Wait for stronger structure before adding",
            "notes": reasons,
        }
    return {
        "action": "watchlist_only",
        "size": "0.35x",
        "focus": "Selective crypto tracking",
        "notes": reasons,
    }


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_gap_count = int(payload.get("active_gap_count", 0) or 0)
    gap_candidates = payload.get("gap_candidates", [])
    top_name = gap_candidates[0]["name"] if gap_candidates else "No leader"

    if not session.get("korea_open"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "Korea desk idle outside local market hours",
            "notes": ["review leader rotation after next KOSDAQ open"],
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "No fresh KOSDAQ exposure under stressed regime",
            "notes": ["risk committee blocks new Korea entries"],
        }
    if active_gap_count >= 3 and stance != "DEFENSE":
        return {
            "action": "attack_opening_drive",
            "size": "0.50x" if stance == "BALANCED" else "0.70x",
            "focus": f"Track leader {top_name}",
            "notes": [f"{active_gap_count} gap candidates with liquidity support"],
        }
    if active_gap_count >= 1:
        return {
            "action": "selective_probe",
            "size": "0.30x",
            "focus": f"Wait for confirmation in {top_name}",
            "notes": [f"{active_gap_count} candidate(s) worth monitoring"],
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No quality opening-drive candidate right now",
        "notes": ["skip weak gaps and preserve attention"],
    }


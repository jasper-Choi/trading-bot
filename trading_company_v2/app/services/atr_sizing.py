from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_atr_sizing(candles: list[dict[str, Any]], period: int = 14) -> dict[str, Any]:
    """Return a volatility-normalized size multiplier from ATR%."""
    if len(candles) < period + 2:
        return {
            "atr_pct": 0.0,
            "atr_size_multiplier": 1.0,
            "volatility_tier": "unknown",
            "atr_sizing_reason": "not enough candles for ATR sizing",
        }

    true_ranges: list[float] = []
    for idx in range(1, len(candles)):
        high = _f(candles[idx].get("high"))
        low = _f(candles[idx].get("low"))
        prev_close = _f(candles[idx - 1].get("close"))
        if high <= 0 or low <= 0 or prev_close <= 0:
            continue
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    last_close = _f(candles[-1].get("close"))
    if len(true_ranges) < period or last_close <= 0:
        return {
            "atr_pct": 0.0,
            "atr_size_multiplier": 1.0,
            "volatility_tier": "unknown",
            "atr_sizing_reason": "invalid candles for ATR sizing",
        }

    atr = sum(true_ranges[-period:]) / period
    atr_pct = round((atr / last_close) * 100.0, 3)

    if atr_pct >= 2.4:
        tier, multiplier = "extreme", 0.55
    elif atr_pct >= 1.7:
        tier, multiplier = "high", 0.72
    elif atr_pct >= 0.9:
        tier, multiplier = "normal", 1.0
    elif atr_pct >= 0.45:
        tier, multiplier = "quiet", 1.08
    else:
        tier, multiplier = "dead", 0.82

    return {
        "atr_pct": atr_pct,
        "atr_size_multiplier": multiplier,
        "volatility_tier": tier,
        "atr_sizing_reason": f"ATR{period} {atr_pct:.2f}% -> {tier} volatility, size {multiplier:.2f}x",
    }

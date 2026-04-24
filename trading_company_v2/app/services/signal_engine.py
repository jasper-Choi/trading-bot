from __future__ import annotations

from typing import Any


def ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value * alpha) + (result[-1] * (1 - alpha)))
    return result


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(values)):
        diff = values[idx] - values[idx - 1]
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def summarize_crypto_signal(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(item["close"]) for item in candles]
    if len(closes) < 30:
        return {"bias": "neutral", "score": 0.5, "reasons": ["not enough candles"]}

    ema10 = ema(closes, 10)
    ema30 = ema(closes, 30)
    last_close = closes[-1]
    last_rsi = rsi(closes, 14)
    ema_gap_pct = round(((ema10[-1] - ema30[-1]) / ema30[-1]) * 100, 2) if ema30[-1] else 0.0
    reasons: list[str] = []
    score = 0.5

    if ema10[-1] > ema30[-1]:
        score += 0.15
        reasons.append("ema10 above ema30")
    else:
        score -= 0.15
        reasons.append("ema10 below ema30")

    if last_rsi is not None:
        if 45 <= last_rsi <= 68:
            score += 0.1
            reasons.append(f"rsi balanced at {last_rsi:.1f}")
        elif last_rsi < 35:
            reasons.append(f"rsi weak at {last_rsi:.1f}")
        else:
            reasons.append(f"rsi extended at {last_rsi:.1f}")

    recent_change = ((last_close - closes[-5]) / closes[-5]) * 100 if closes[-5] else 0
    burst_change = ((last_close - closes[-3]) / closes[-3]) * 100 if closes[-3] else 0
    pullback_gap = ((last_close - ema10[-1]) / ema10[-1]) * 100 if ema10[-1] else 0
    last_range = ((max(closes[-4:]) - min(closes[-4:])) / min(closes[-4:])) * 100 if min(closes[-4:]) else 0
    if recent_change > 1.0:
        score += 0.1
        reasons.append(f"5-candle momentum {recent_change:.2f}%")
    elif recent_change < -1.0:
        score -= 0.1
        reasons.append(f"5-candle drawdown {recent_change:.2f}%")

    if 0.5 <= recent_change <= 2.4 and burst_change <= 1.8 and abs(pullback_gap) <= 1.6:
        score += 0.08
        reasons.append(f"controlled breakout {recent_change:.2f}% / pullback gap {pullback_gap:.2f}%")
    if burst_change > 2.4:
        score -= 0.1
        reasons.append(f"3-candle burst {burst_change:.2f}%")
    elif burst_change < -2.5:
        score -= 0.08
        reasons.append(f"3-candle flush {burst_change:.2f}%")
    if last_range > 4.8:
        score -= 0.06
        reasons.append(f"4-candle range too wide {last_range:.2f}%")

    score = max(0.0, min(1.0, round(score, 2)))
    if score >= 0.62:
        bias = "offense"
    elif score <= 0.4:
        bias = "defense"
    else:
        bias = "balanced"
    return {
        "bias": bias,
        "score": score,
        "reasons": reasons,
        "recent_change_pct": round(recent_change, 2),
        "burst_change_pct": round(burst_change, 2),
        "ema_gap_pct": ema_gap_pct,
        "pullback_gap_pct": round(pullback_gap, 2),
        "range_4_pct": round(last_range, 2),
        "rsi": round(last_rsi, 1) if last_rsi is not None else None,
    }


def summarize_equity_signal(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(item["close"]) for item in candles]
    if len(closes) < 30:
        return {"bias": "neutral", "score": 0.5, "reasons": ["not enough candles"]}

    ema8 = ema(closes, 8)
    ema21 = ema(closes, 21)
    last_close = closes[-1]
    last_rsi = rsi(closes, 14)
    ema_gap_pct = round(((ema8[-1] - ema21[-1]) / ema21[-1]) * 100, 2) if ema21[-1] else 0.0
    reasons: list[str] = []
    score = 0.5

    if ema8[-1] > ema21[-1]:
        score += 0.16
        reasons.append("ema8 above ema21")
    else:
        score -= 0.16
        reasons.append("ema8 below ema21")

    if last_rsi is not None:
        if 48 <= last_rsi <= 67:
            score += 0.08
            reasons.append(f"rsi constructive at {last_rsi:.1f}")
        elif last_rsi < 38:
            score -= 0.06
            reasons.append(f"rsi weak at {last_rsi:.1f}")
        else:
            reasons.append(f"rsi extended at {last_rsi:.1f}")

    recent_change = ((last_close - closes[-6]) / closes[-6]) * 100 if closes[-6] else 0
    short_burst = ((last_close - closes[-3]) / closes[-3]) * 100 if closes[-3] else 0
    if recent_change > 2.0:
        score += 0.12
        reasons.append(f"6-day momentum {recent_change:.2f}%")
    elif recent_change < -2.0:
        score -= 0.12
        reasons.append(f"6-day drawdown {recent_change:.2f}%")

    if short_burst > 8.0:
        score -= 0.06
        reasons.append(f"3-day burst {short_burst:.2f}%")
    elif short_burst < -6.0:
        score -= 0.06
        reasons.append(f"3-day flush {short_burst:.2f}%")

    score = max(0.0, min(1.0, round(score, 2)))
    if score >= 0.64:
        bias = "offense"
    elif score <= 0.4:
        bias = "defense"
    else:
        bias = "balanced"
    return {
        "bias": bias,
        "score": score,
        "reasons": reasons,
        "recent_change_pct": round(recent_change, 2),
        "burst_change_pct": round(short_burst, 2),
        "ema_gap_pct": ema_gap_pct,
        "rsi": round(last_rsi, 1) if last_rsi is not None else None,
    }

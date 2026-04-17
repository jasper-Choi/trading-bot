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
    if recent_change > 1.0:
        score += 0.1
        reasons.append(f"5-candle momentum {recent_change:.2f}%")
    elif recent_change < -1.0:
        score -= 0.1
        reasons.append(f"5-candle drawdown {recent_change:.2f}%")

    score = max(0.0, min(1.0, round(score, 2)))
    if score >= 0.62:
        bias = "offense"
    elif score <= 0.4:
        bias = "defense"
    else:
        bias = "balanced"
    return {"bias": bias, "score": score, "reasons": reasons}


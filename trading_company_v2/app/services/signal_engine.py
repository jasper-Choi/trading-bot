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


def summarize_breakout_signal(
    candles: list[dict[str, Any]],
    breakout_period: int = 20,
    vol_surge_mult: float = 2.5,
    rsi_min: float = 55.0,
    rsi_max: float = 78.0,
) -> dict[str, Any]:
    """
    Detects momentum breakout entry conditions — matches coin_backtest_v5 / stock_backtest_v3:
      1. Price breaks N-period high  (close > max of prior N closes)
      2. Volume surge  (current vol >= vol_surge_mult × N-period avg)
      3. RSI in momentum zone  [rsi_min, rsi_max]
      4. Price above EMA(breakout_period)
    Works on any timeframe: 15m candles (crypto) or daily candles (Korea stocks).
    """
    _empty = {
        "breakout": False, "vol_surge": False, "rsi_in_zone": False,
        "above_ema20": False, "all_confirmed": False, "partial_confirmed": False,
        "confirmed_count": 0, "breakout_score": 0.0, "vol_ratio": 0.0,
        "period_high": 0.0, "last_rsi": None,
        "reasons": ["not enough candles for breakout check"],
    }
    if len(candles) < breakout_period + 2:
        return _empty

    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("volume") or 0) for c in candles]

    last_close = closes[-1]
    last_vol = volumes[-1]

    # 1. N-period high breakout: close > max(prior N closes), excluding current
    prior_closes = closes[-(breakout_period + 1):-1]
    period_high = max(prior_closes) if prior_closes else last_close
    breakout = last_close > period_high

    # 2. Volume surge vs N-period average (excluding current candle)
    vol_window = volumes[-(breakout_period + 1):-1]
    avg_vol = sum(vol_window) / len(vol_window) if vol_window else 0.0
    vol_ratio = round(last_vol / avg_vol, 2) if avg_vol > 0 else 0.0
    vol_surge = vol_ratio >= vol_surge_mult

    # 3. RSI in momentum zone
    last_rsi = rsi(closes, 14)
    rsi_in_zone = last_rsi is not None and rsi_min <= last_rsi <= rsi_max

    # 4. Price above EMA(breakout_period)
    ema_vals = ema(closes, breakout_period)
    above_ema20 = last_close > ema_vals[-1] if ema_vals else False

    confirmed_count = sum([breakout, vol_surge, rsi_in_zone, above_ema20])
    all_confirmed = confirmed_count == 4
    partial_confirmed = confirmed_count >= 3

    if all_confirmed:
        breakout_score = 0.90
    elif partial_confirmed:
        breakout_score = 0.70
    elif confirmed_count == 2:
        breakout_score = 0.45
    elif confirmed_count == 1:
        breakout_score = 0.20
    else:
        breakout_score = 0.0

    reasons: list[str] = []
    if breakout:
        reasons.append(f"{breakout_period}-period high breakout ({last_close:.4f} > {period_high:.4f})")
    else:
        reasons.append(f"no breakout ({last_close:.4f} vs {period_high:.4f} high)")
    if vol_surge:
        reasons.append(f"vol surge {vol_ratio:.1f}x (threshold {vol_surge_mult}x)")
    else:
        reasons.append(f"vol {vol_ratio:.1f}x below {vol_surge_mult}x threshold")
    if rsi_in_zone:
        reasons.append(f"rsi {last_rsi:.1f} in momentum zone [{rsi_min:.0f}-{rsi_max:.0f}]")
    elif last_rsi is not None:
        reasons.append(f"rsi {last_rsi:.1f} outside zone [{rsi_min:.0f}-{rsi_max:.0f}]")
    if above_ema20:
        reasons.append(f"price above EMA{breakout_period}")
    else:
        reasons.append(f"price below EMA{breakout_period}")

    return {
        "breakout": breakout,
        "vol_surge": vol_surge,
        "vol_ratio": vol_ratio,
        "rsi_in_zone": rsi_in_zone,
        "above_ema20": above_ema20,
        "all_confirmed": all_confirmed,
        "partial_confirmed": partial_confirmed,
        "confirmed_count": confirmed_count,
        "breakout_score": round(breakout_score, 2),
        "period_high": round(period_high, 6),
        "last_rsi": round(last_rsi, 1) if last_rsi is not None else None,
        "reasons": reasons,
    }


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

    # --- Breakout signal overlay (matches coin_backtest_v5 entry logic) ---
    # On 15m candles: 20-candle high = ~5h momentum window; RSI zone relaxed to 45-74
    bk = summarize_breakout_signal(candles, breakout_period=15, vol_surge_mult=2.0,
                                   rsi_min=45.0, rsi_max=78.0)
    bk_count = int(bk.get("confirmed_count", 0) or 0)
    if bk_count == 4:
        score += 0.15
        reasons.append(f"breakout FULL confirmed vol {bk.get('vol_ratio', 0):.1f}x")
    elif bk_count == 3:
        score += 0.08
        reasons.append(f"breakout partial ({bk_count}/4) vol {bk.get('vol_ratio', 0):.1f}x")
    elif bk_count == 2:
        score += 0.03
        reasons.append(f"breakout weak ({bk_count}/4)")

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
        "breakout_confirmed": bool(bk.get("all_confirmed")),
        "breakout_partial": bool(bk.get("partial_confirmed")),
        "breakout_count": bk_count,
        "vol_ratio": float(bk.get("vol_ratio", 0.0) or 0.0),
        "breakout_score": float(bk.get("breakout_score", 0.0) or 0.0),
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

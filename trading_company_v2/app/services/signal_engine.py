from __future__ import annotations

from datetime import datetime, timezone
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


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    """Return a simple rolling RSI series aligned with the input values."""
    result: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return result
    for idx in range(period, len(values)):
        window = values[idx - period: idx + 1]
        result[idx] = rsi(window, period)
    return result


def summarize_rsi_momentum_overlay(
    candles: list[dict[str, Any]],
    period: int = 14,
    lookback: int = 18,
) -> dict[str, Any]:
    """
    RSI quality overlay for momentum breakouts.

    Ross Cameron / Warrior-style usage treats RSI as confirmation and warning:
    strong momentum is acceptable, but late overextension and bearish RSI
    divergence are reasons to avoid chasing a breakout.
    """
    _empty: dict[str, Any] = {
        "rsi_quality_ok": True,
        "rsi_reset_confirmed": False,
        "rsi_bearish_divergence": False,
        "rsi_extreme": False,
        "rsi_score_adjustment": 0.0,
        "rsi_overlay_reasons": ["not enough candles for RSI overlay"],
    }
    if len(candles) < period + 6:
        return _empty

    closes = [float(item["close"]) for item in candles]
    series = rsi_series(closes, period)
    last_rsi = series[-1]
    if last_rsi is None:
        return _empty

    recent_rs = [float(v) for v in series[-lookback:] if v is not None]
    recent_closes = closes[-lookback:]
    if len(recent_rs) < 6 or len(recent_closes) < 6:
        return _empty

    rsi_reset_confirmed = min(recent_rs[:-1]) <= 52.0 and last_rsi >= 55.0
    rsi_extreme = last_rsi >= 82.0 or last_rsi <= 25.0

    compare_closes = closes[-(lookback + 1):-1]
    compare_rs = series[-(lookback + 1):-1]
    bearish_divergence = False
    if compare_closes and any(v is not None for v in compare_rs):
        prev_high_idx = max(range(len(compare_closes)), key=lambda idx: compare_closes[idx])
        prev_high = compare_closes[prev_high_idx]
        prev_high_rsi = compare_rs[prev_high_idx]
        if prev_high_rsi is not None:
            price_made_high = closes[-1] >= prev_high * 0.998
            rsi_lagged = last_rsi <= float(prev_high_rsi) - 3.0
            bearish_divergence = bool(price_made_high and rsi_lagged and last_rsi >= 58.0)

    adjustment = 0.0
    reasons: list[str] = []
    if rsi_reset_confirmed:
        adjustment += 0.04
        reasons.append(f"rsi reset/reclaim confirmed ({last_rsi:.1f})")
    if bearish_divergence:
        adjustment -= 0.12
        reasons.append(f"bearish RSI divergence warning ({last_rsi:.1f})")
    if rsi_extreme:
        adjustment -= 0.07
        reasons.append(f"RSI extreme zone ({last_rsi:.1f})")
    if not reasons:
        reasons.append(f"rsi quality neutral ({last_rsi:.1f})")

    return {
        "rsi_quality_ok": not bearish_divergence and not rsi_extreme,
        "rsi_reset_confirmed": rsi_reset_confirmed,
        "rsi_bearish_divergence": bearish_divergence,
        "rsi_extreme": rsi_extreme,
        "rsi_score_adjustment": round(adjustment, 3),
        "rsi_overlay_reasons": reasons,
    }


# ─── ICT (Inner Circle Trader) Detection Functions ──────────────────────────

def detect_fvg(candles: list[dict[str, Any]], lookback: int = 30) -> dict[str, Any]:
    """
    Fair Value Gap: 3-candle imbalance pattern.
    Bullish FVG: candle[i].low > candle[i-2].high  (price gapped up — discount support zone)
    Bearish FVG: candle[i].high < candle[i-2].low  (price gapped down — premium resistance zone)
    """
    _empty: dict[str, Any] = {
        "fvg_type": None, "fvg_active": False, "price_in_fvg": False,
        "fvg_top": 0.0, "fvg_bottom": 0.0,
        "bullish_fvg_count": 0, "bearish_fvg_count": 0,
    }
    if len(candles) < 3:
        return _empty

    recent = candles[-min(lookback + 2, len(candles)):]
    last_close = float(candles[-1]["close"])

    bullish_fvgs: list[dict] = []
    bearish_fvgs: list[dict] = []

    for i in range(2, len(recent)):
        h0 = float(recent[i - 2]["high"])
        l0 = float(recent[i - 2]["low"])
        h2 = float(recent[i]["high"])
        l2 = float(recent[i]["low"])
        if l2 > h0:
            bullish_fvgs.append({"top": l2, "bottom": h0, "mid": (l2 + h0) / 2})
        if h2 < l0:
            bearish_fvgs.append({"top": l0, "bottom": h2, "mid": (l0 + h2) / 2})

    active_bull = [f for f in bullish_fvgs if last_close >= f["bottom"]]
    active_bear = [f for f in bearish_fvgs if last_close <= f["top"]]
    nearest_bull = min(active_bull, key=lambda f: abs(last_close - f["mid"])) if active_bull else None
    nearest_bear = min(active_bear, key=lambda f: abs(last_close - f["mid"])) if active_bear else None

    fvg_type: str | None = None
    fvg_top = fvg_bottom = 0.0
    fvg_active = price_in_fvg = False

    candidates = []
    if nearest_bull:
        candidates.append(("bullish", nearest_bull, abs(last_close - nearest_bull["mid"])))
    if nearest_bear:
        candidates.append(("bearish", nearest_bear, abs(last_close - nearest_bear["mid"])))
    if candidates:
        fvg_type, best, _ = min(candidates, key=lambda x: x[2])
        fvg_top, fvg_bottom = best["top"], best["bottom"]
        fvg_active = True
        price_in_fvg = fvg_bottom <= last_close <= fvg_top

    return {
        "fvg_type": fvg_type, "fvg_active": fvg_active, "price_in_fvg": price_in_fvg,
        "fvg_top": round(fvg_top, 6), "fvg_bottom": round(fvg_bottom, 6),
        "bullish_fvg_count": len(bullish_fvgs), "bearish_fvg_count": len(bearish_fvgs),
    }


def detect_order_block(
    candles: list[dict[str, Any]],
    lookback: int = 30,
    min_move_pct: float = 1.5,
) -> dict[str, Any]:
    """
    Order Block: last opposing candle before a significant directional move.
    Bullish OB: last bearish candle before >= min_move_pct rally  (institutional demand zone)
    Bearish OB: last bullish candle before >= min_move_pct drop   (institutional supply zone)
    """
    _empty: dict[str, Any] = {
        "ob_type": None, "ob_active": False,
        "ob_high": 0.0, "ob_low": 0.0, "price_at_ob": False,
        "bullish_ob_count": 0, "bearish_ob_count": 0,
    }
    if len(candles) < 6:
        return _empty

    recent = candles[-min(lookback + 5, len(candles)):]
    last_close = float(candles[-1]["close"])

    bullish_obs: list[dict] = []
    bearish_obs: list[dict] = []

    for i in range(len(recent) - 4):
        c = recent[i]
        c_open, c_close = float(c["open"]), float(c["close"])
        c_high, c_low = float(c["high"]), float(c["low"])
        fwd = range(i + 1, min(i + 5, len(recent)))

        if c_close < c_open:  # bearish candle → bullish OB candidate
            future_high = max(float(recent[j]["high"]) for j in fwd)
            move = ((future_high - c_high) / c_high * 100) if c_high > 0 else 0.0
            if move >= min_move_pct:
                bullish_obs.append({"high": c_high, "low": c_low, "idx": i, "move_pct": round(move, 2)})
        elif c_close > c_open:  # bullish candle → bearish OB candidate
            future_low = min(float(recent[j]["low"]) for j in fwd)
            move = ((c_low - future_low) / c_low * 100) if c_low > 0 else 0.0
            if move >= min_move_pct:
                bearish_obs.append({"high": c_high, "low": c_low, "idx": i, "move_pct": round(move, 2)})

    nearest_bull = next((ob for ob in reversed(bullish_obs) if last_close >= ob["low"] * 0.99), None)
    nearest_bear = next((ob for ob in reversed(bearish_obs) if last_close <= ob["high"] * 1.01), None)

    ob_type: str | None = None
    ob_high = ob_low = 0.0
    ob_active = price_at_ob = False

    if nearest_bull and (not nearest_bear or nearest_bull["idx"] > nearest_bear["idx"]):
        ob_type, ob_high, ob_low, ob_active = "bullish", nearest_bull["high"], nearest_bull["low"], True
        price_at_ob = ob_low <= last_close <= ob_high * 1.015
    elif nearest_bear:
        ob_type, ob_high, ob_low, ob_active = "bearish", nearest_bear["high"], nearest_bear["low"], True
        price_at_ob = ob_low * 0.985 <= last_close <= ob_high

    return {
        "ob_type": ob_type, "ob_active": ob_active,
        "ob_high": round(ob_high, 6), "ob_low": round(ob_low, 6), "price_at_ob": price_at_ob,
        "bullish_ob_count": len(bullish_obs), "bearish_ob_count": len(bearish_obs),
    }


def detect_liquidity_sweep(
    candles: list[dict[str, Any]],
    swing_period: int = 12,
    reversal_candles: int = 3,
    min_reversal_pct: float = 0.4,
) -> dict[str, Any]:
    """
    Liquidity Sweep detection.
    SSL sweep: price dips below recent swing low then reverses up  (bullish — smart money accumulated)
    BSL sweep: price spikes above recent swing high then reverses down (bearish — smart money distributed)
    """
    _empty: dict[str, Any] = {
        "sweep_type": None, "sweep_active": False,
        "sweep_level": 0.0, "reversal_confirmed": False,
        "swing_high": 0.0, "swing_low": 0.0,
    }
    if len(candles) < swing_period + reversal_candles + 2:
        return _empty

    recent = candles[-(swing_period + reversal_candles + 2):]
    swing_window = recent[:-reversal_candles]
    check_candles = recent[-reversal_candles:]
    last_close = float(candles[-1]["close"])

    swing_high = max(float(c["high"]) for c in swing_window)
    swing_low = min(float(c["low"]) for c in swing_window)

    ssl_sweep = bsl_sweep = False
    sweep_level = 0.0

    for c in check_candles[:-1]:
        if float(c["low"]) < swing_low and last_close > swing_low:
            ssl_sweep, sweep_level = True, swing_low
            break
    if not ssl_sweep:
        for c in check_candles[:-1]:
            if float(c["high"]) > swing_high and last_close < swing_high:
                bsl_sweep, sweep_level = True, swing_high
                break

    reversal_confirmed = False
    if sweep_level > 0:
        rev_pct = (
            ((last_close - sweep_level) / sweep_level * 100) if ssl_sweep
            else ((sweep_level - last_close) / sweep_level * 100)
        )
        reversal_confirmed = rev_pct >= min_reversal_pct

    return {
        "sweep_type": "ssl" if ssl_sweep else ("bsl" if bsl_sweep else None),
        "sweep_active": ssl_sweep or bsl_sweep,
        "sweep_level": round(sweep_level, 6),
        "reversal_confirmed": reversal_confirmed,
        "swing_high": round(swing_high, 6),
        "swing_low": round(swing_low, 6),
    }


def ict_kill_zone(dt: datetime | None = None) -> dict[str, Any]:
    """
    ICT Kill Zone filter (UTC clock).
    London KZ: 09:00~12:00 UTC  (18:00~21:00 KST)
    New York KZ: 13:30~16:30 UTC (22:30~01:30 KST)
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    total_min = dt.hour * 60 + dt.minute
    in_london = 540 <= total_min < 720
    in_ny = 810 <= total_min < 990
    return {
        "in_kill_zone": in_london or in_ny,
        "kill_zone_name": "london" if in_london else ("ny" if in_ny else None),
    }


def detect_bos_choch(candles: list[dict[str, Any]], swing_n: int = 3) -> dict[str, Any]:
    """
    Break of Structure (BOS) / Change of Character (CHoCH).
    BOS: price continues past the last swing point in the trend direction (continuation).
    CHoCH: price breaks past the last swing point AGAINST the prior trend (reversal — entry signal).
    """
    _empty: dict[str, Any] = {
        "structure": "undecided", "bos": False, "choch": False,
        "choch_bullish": False, "choch_bearish": False,
        "bos_bullish": False, "bos_bearish": False,
        "swing_high": 0.0, "swing_low": 0.0, "prior_trend": "undecided",
    }
    if len(candles) < swing_n * 2 + 4:
        return _empty

    analysis = candles[:-2]
    last_close = float(candles[-1]["close"])

    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(swing_n, len(analysis) - swing_n):
        hi = float(analysis[i]["high"])
        lo = float(analysis[i]["low"])
        if all(hi >= float(analysis[i + j]["high"]) for j in range(-swing_n, swing_n + 1) if j != 0):
            swing_highs.append((i, hi))
        if all(lo <= float(analysis[i + j]["low"]) for j in range(-swing_n, swing_n + 1) if j != 0):
            swing_lows.append((i, lo))

    if not swing_highs or not swing_lows:
        return _empty

    sh_idx, sh_val = swing_highs[-1]
    sl_idx, sl_val = swing_lows[-1]
    prior_trend = "up" if sh_idx > sl_idx else "down"

    bos_bull = prior_trend == "up" and last_close > sh_val
    bos_bear = prior_trend == "down" and last_close < sl_val
    choch_bull = prior_trend == "down" and last_close > sh_val
    choch_bear = prior_trend == "up" and last_close < sl_val

    if choch_bull:
        structure = "choch_bullish"
    elif bos_bull:
        structure = "bos_bullish"
    elif choch_bear:
        structure = "choch_bearish"
    elif bos_bear:
        structure = "bos_bearish"
    else:
        structure = "ranging"

    return {
        "structure": structure, "bos": bos_bull or bos_bear, "choch": choch_bull or choch_bear,
        "choch_bullish": choch_bull, "choch_bearish": choch_bear,
        "bos_bullish": bos_bull, "bos_bearish": bos_bear,
        "swing_high": round(sh_val, 6), "swing_low": round(sl_val, 6),
        "prior_trend": prior_trend,
    }


def summarize_ict_signal(
    candles: list[dict[str, Any]],
    dt: datetime | None = None,
) -> dict[str, Any]:
    """
    Aggregate all ICT signals into a single score overlay.
    Returns ict_score in [-0.30, +0.30] to adjust the base signal score.
    """
    fvg = detect_fvg(candles)
    ob = detect_order_block(candles)
    sweep = detect_liquidity_sweep(candles)
    kz = ict_kill_zone(dt)
    structure = detect_bos_choch(candles)

    ict_score = 0.0
    reasons: list[str] = []

    if kz["in_kill_zone"]:
        ict_score += 0.08
        reasons.append(f"ICT kill zone: {kz['kill_zone_name']}")

    ssl_ok = sweep.get("sweep_type") == "ssl" and bool(sweep.get("reversal_confirmed"))
    bsl_ok = sweep.get("sweep_type") == "bsl" and bool(sweep.get("reversal_confirmed"))
    if ssl_ok:
        ict_score += 0.12
        reasons.append(f"SSL sweep+reversal @ {sweep['sweep_level']:.4f}")
    elif bsl_ok:
        ict_score -= 0.08
        reasons.append(f"BSL sweep bearish @ {sweep['sweep_level']:.4f}")

    choch_bull = bool(structure.get("choch_bullish"))
    choch_bear = bool(structure.get("choch_bearish"))
    bos_bull = bool(structure.get("bos_bullish"))
    bos_bear = bool(structure.get("bos_bearish"))
    if choch_bull:
        ict_score += 0.10
        reasons.append(f"CHoCH bullish > {structure['swing_high']:.4f}")
    elif bos_bull:
        ict_score += 0.05
        reasons.append("BOS bullish continuation")
    elif choch_bear:
        ict_score -= 0.12
        reasons.append("CHoCH bearish — trend reversing down")
    elif bos_bear:
        ict_score -= 0.06
        reasons.append("BOS bearish continuation")

    at_bull_ob = bool(ob.get("ob_active") and ob.get("ob_type") == "bullish" and ob.get("price_at_ob"))
    at_bear_ob = bool(ob.get("ob_active") and ob.get("ob_type") == "bearish" and ob.get("price_at_ob"))
    if at_bull_ob:
        ict_score += 0.08
        reasons.append(f"price at bullish OB [{ob['ob_low']:.4f}~{ob['ob_high']:.4f}]")
    elif at_bear_ob:
        ict_score -= 0.06
        reasons.append(f"price at bearish OB [{ob['ob_low']:.4f}~{ob['ob_high']:.4f}]")

    in_bull_fvg = bool(fvg.get("fvg_active") and fvg.get("fvg_type") == "bullish" and fvg.get("price_in_fvg"))
    in_bear_fvg = bool(fvg.get("fvg_active") and fvg.get("fvg_type") == "bearish" and fvg.get("price_in_fvg"))
    if in_bull_fvg:
        ict_score += 0.06
        reasons.append(f"price in bullish FVG [{fvg['fvg_bottom']:.4f}~{fvg['fvg_top']:.4f}]")
    elif in_bear_fvg:
        ict_score -= 0.05
        reasons.append(f"price in bearish FVG [{fvg['fvg_bottom']:.4f}~{fvg['fvg_top']:.4f}]")

    bullish_count = sum([ssl_ok, choch_bull or bos_bull, at_bull_ob, in_bull_fvg, kz["in_kill_zone"]])
    if bullish_count >= 3:
        ict_score += 0.05
        reasons.append(f"ICT full confluence ({bullish_count}/5)")

    return {
        "ict_score": round(max(-0.30, min(0.30, ict_score)), 3),
        "ict_reasons": reasons,
        "kill_zone_active": kz["in_kill_zone"],
        "kill_zone_name": kz.get("kill_zone_name"),
        "ssl_sweep_confirmed": ssl_ok,
        "bsl_sweep_confirmed": bsl_ok,
        "choch_bullish": choch_bull,
        "choch_bearish": choch_bear,
        "bos_bullish": bos_bull,
        "bos_bearish": bos_bear,
        "price_at_bull_ob": at_bull_ob,
        "price_in_bull_fvg": in_bull_fvg,
        "ict_bullish_count": bullish_count,
        "ict_structure": str(structure.get("structure", "undecided")),
    }


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
    rsi_overlay = summarize_rsi_momentum_overlay(candles)
    rsi_quality_ok = bool(rsi_overlay.get("rsi_quality_ok", True))

    # 4. Price above EMA(breakout_period)
    ema_vals = ema(closes, breakout_period)
    above_ema20 = last_close > ema_vals[-1] if ema_vals else False

    confirmed_count = sum([breakout, vol_surge, rsi_in_zone, above_ema20])
    all_confirmed = confirmed_count == 4
    partial_confirmed = confirmed_count >= 3

    if all_confirmed and rsi_quality_ok:
        breakout_score = 0.90
    elif partial_confirmed:
        breakout_score = 0.70
    elif confirmed_count == 2:
        breakout_score = 0.45
    elif confirmed_count == 1:
        breakout_score = 0.20
    else:
        breakout_score = 0.0
    breakout_score += float(rsi_overlay.get("rsi_score_adjustment", 0.0) or 0.0)
    breakout_score = max(0.0, min(0.95, breakout_score))

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
    reasons.extend(list(rsi_overlay.get("rsi_overlay_reasons", []))[:2])

    return {
        "breakout": breakout,
        "vol_surge": vol_surge,
        "vol_ratio": vol_ratio,
        "rsi_in_zone": rsi_in_zone,
        "rsi_quality_ok": rsi_quality_ok,
        "rsi_reset_confirmed": bool(rsi_overlay.get("rsi_reset_confirmed", False)),
        "rsi_bearish_divergence": bool(rsi_overlay.get("rsi_bearish_divergence", False)),
        "rsi_extreme": bool(rsi_overlay.get("rsi_extreme", False)),
        "above_ema20": above_ema20,
        "all_confirmed": all_confirmed and rsi_quality_ok,
        "partial_confirmed": partial_confirmed and rsi_quality_ok,
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
        elif 68 < last_rsi <= 78:
            score += 0.04
            reasons.append(f"rsi momentum at {last_rsi:.1f}")
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
    rsi_quality_ok = bool(bk.get("rsi_quality_ok", True))
    if not rsi_quality_ok:
        score -= 0.12
        if bk.get("rsi_bearish_divergence"):
            reasons.append("RSI divergence blocks late breakout chase")
        if bk.get("rsi_extreme"):
            reasons.append("RSI extreme blocks late breakout chase")
    elif bk.get("rsi_reset_confirmed"):
        score += 0.04
        reasons.append("RSI reset supports momentum continuation")
    if bk_count == 4 and rsi_quality_ok:
        score += 0.15
        reasons.append(f"breakout FULL confirmed vol {bk.get('vol_ratio', 0):.1f}x")
    elif bk_count == 3 and rsi_quality_ok:
        score += 0.08
        reasons.append(f"breakout partial ({bk_count}/4) vol {bk.get('vol_ratio', 0):.1f}x")
    elif bk_count == 2:
        score += 0.03
        reasons.append(f"breakout weak ({bk_count}/4)")

    # --- ICT overlay (FVG / OB / Liquidity Sweep / Kill Zone / BOS/CHoCH) ---
    ict = summarize_ict_signal(candles)
    score += float(ict.get("ict_score", 0.0) or 0.0)
    reasons.extend(ict.get("ict_reasons", []))

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
        "rsi_quality_ok": rsi_quality_ok,
        "rsi_reset_confirmed": bool(bk.get("rsi_reset_confirmed", False)),
        "rsi_bearish_divergence": bool(bk.get("rsi_bearish_divergence", False)),
        "rsi_extreme": bool(bk.get("rsi_extreme", False)),
        "ict_score": float(ict.get("ict_score", 0.0) or 0.0),
        "kill_zone_active": bool(ict.get("kill_zone_active")),
        "kill_zone_name": ict.get("kill_zone_name"),
        "ssl_sweep_confirmed": bool(ict.get("ssl_sweep_confirmed")),
        "bsl_sweep_confirmed": bool(ict.get("bsl_sweep_confirmed")),
        "choch_bullish": bool(ict.get("choch_bullish")),
        "choch_bearish": bool(ict.get("choch_bearish")),
        "bos_bullish": bool(ict.get("bos_bullish")),
        "bos_bearish": bool(ict.get("bos_bearish")),
        "price_at_bull_ob": bool(ict.get("price_at_bull_ob")),
        "price_in_bull_fvg": bool(ict.get("price_in_bull_fvg")),
        "ict_bullish_count": int(ict.get("ict_bullish_count", 0) or 0),
        "ict_structure": str(ict.get("ict_structure", "undecided")),
    }


def summarize_crypto_micro_momentum_signal(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """
    1-minute crypto momentum layer.

    This is intentionally faster than the 15m swing signal: it looks for a
    fresh VWAP reclaim / high-of-window break with volume expansion, while
    blocking exhausted vertical candles.
    """
    if len(candles) < 35:
        return {
            "micro_ready": False,
            "micro_score": 0.0,
            "micro_bias": "neutral",
            "micro_reasons": ["not enough 1m candles"],
        }

    closes = [float(item["close"]) for item in candles]
    highs = [float(item.get("high") or item["close"]) for item in candles]
    lows = [float(item.get("low") or item["close"]) for item in candles]
    volumes = [float(item.get("volume") or 0.0) for item in candles]
    typical = [(highs[idx] + lows[idx] + closes[idx]) / 3 for idx in range(len(closes))]
    vwap_len = min(60, len(closes))
    recent_typical = typical[-vwap_len:]
    recent_volumes = volumes[-vwap_len:]
    vol_sum = sum(recent_volumes) or 1.0
    vwap = sum(recent_typical[idx] * recent_volumes[idx] for idx in range(vwap_len)) / vol_sum

    last_close = closes[-1]
    ema5 = ema(closes, 5)
    ema20 = ema(closes, 20)
    last_rsi = rsi(closes, 14)
    prior_high_20 = max(highs[-21:-1])
    vol_avg_20 = sum(volumes[-21:-1]) / 20 if sum(volumes[-21:-1]) > 0 else 0.0
    vol_ratio = round(volumes[-1] / vol_avg_20, 2) if vol_avg_20 > 0 else 0.0
    move_3 = ((last_close - closes[-4]) / closes[-4]) * 100 if closes[-4] else 0.0
    move_10 = ((last_close - closes[-11]) / closes[-11]) * 100 if closes[-11] else 0.0
    range_5 = ((max(highs[-5:]) - min(lows[-5:])) / min(lows[-5:])) * 100 if min(lows[-5:]) else 0.0
    vwap_gap = ((last_close - vwap) / vwap) * 100 if vwap else 0.0

    breaks_high = last_close > prior_high_20
    above_vwap = last_close > vwap
    ema_stack = ema5[-1] > ema20[-1]
    vol_expansion = vol_ratio >= 1.8
    rsi_momentum = last_rsi is not None and 52.0 <= last_rsi <= 82.0
    exhausted = move_3 >= 2.2 or move_10 >= 4.5 or range_5 >= 4.0 or vwap_gap >= 2.8

    score = 0.20
    reasons: list[str] = []
    if breaks_high:
        score += 0.22
        reasons.append("1m 20-bar high break")
    if above_vwap:
        score += 0.16
        reasons.append(f"above 1m VWAP ({vwap_gap:.2f}%)")
    if ema_stack:
        score += 0.14
        reasons.append("1m EMA5 above EMA20")
    if vol_expansion:
        score += 0.18
        reasons.append(f"1m volume expansion {vol_ratio:.1f}x")
    if rsi_momentum:
        score += 0.10
        reasons.append(f"1m RSI momentum {last_rsi:.1f}")
    if exhausted:
        score -= 0.25
        reasons.append(f"1m exhaustion risk move3 {move_3:.2f}% / range5 {range_5:.2f}%")
    if move_3 < -1.2:
        score -= 0.12
        reasons.append(f"1m momentum fading {move_3:.2f}%")

    score = round(max(0.0, min(1.0, score)), 2)
    micro_ready = score >= 0.68 and breaks_high and above_vwap and vol_expansion and not exhausted
    if score >= 0.68:
        bias = "offense"
    elif score <= 0.38:
        bias = "defense"
    else:
        bias = "balanced"

    if not reasons:
        reasons.append("1m momentum neutral")

    return {
        "micro_ready": micro_ready,
        "micro_score": score,
        "micro_bias": bias,
        "micro_reasons": reasons,
        "micro_vol_ratio": vol_ratio,
        "micro_move_3_pct": round(move_3, 2),
        "micro_move_10_pct": round(move_10, 2),
        "micro_vwap_gap_pct": round(vwap_gap, 2),
        "micro_range_5_pct": round(range_5, 2),
        "micro_rsi": round(last_rsi, 1) if last_rsi is not None else None,
    }


def summarize_orderbook_pressure(orderbook: dict[str, Any]) -> dict[str, Any]:
    """
    Fast orderbook pressure layer.

    This is a lightweight proxy for tick/order-flow strength until a persistent
    WebSocket collector is added. It rewards clean bid support and penalizes
    wide spreads or heavy ask walls that can turn a breakout into a trap.
    """
    units = orderbook.get("orderbook_units") or []
    if not units:
        return {
            "orderbook_ready": False,
            "orderbook_score": 0.0,
            "orderbook_bias": "neutral",
            "orderbook_reasons": ["no orderbook snapshot"],
        }

    best = units[0]
    best_ask = float(best.get("ask_price") or 0.0)
    best_bid = float(best.get("bid_price") or 0.0)
    mid = (best_ask + best_bid) / 2 if best_ask and best_bid else 0.0
    spread_pct = ((best_ask - best_bid) / mid) * 100 if mid else 99.0

    bid_total = float(orderbook.get("total_bid_size") or 0.0)
    ask_total = float(orderbook.get("total_ask_size") or 0.0)
    bid_ask_ratio = bid_total / ask_total if ask_total > 0 else 0.0
    imbalance = (bid_total - ask_total) / (bid_total + ask_total) if (bid_total + ask_total) > 0 else 0.0

    top_units = units[:5]
    top_bid = sum(float(unit.get("bid_size") or 0.0) for unit in top_units)
    top_ask = sum(float(unit.get("ask_size") or 0.0) for unit in top_units)
    top_ratio = top_bid / top_ask if top_ask > 0 else 0.0

    score = 0.45
    reasons: list[str] = []
    if bid_ask_ratio >= 1.2:
        score += 0.14
        reasons.append(f"bid depth leads {bid_ask_ratio:.2f}x")
    elif bid_ask_ratio <= 0.75:
        score -= 0.14
        reasons.append(f"ask depth dominates {bid_ask_ratio:.2f}x")
    if top_ratio >= 1.15:
        score += 0.12
        reasons.append(f"top-5 bid stack {top_ratio:.2f}x")
    elif top_ratio <= 0.7:
        score -= 0.12
        reasons.append(f"top-5 ask wall {top_ratio:.2f}x")
    if spread_pct <= 0.12:
        score += 0.08
        reasons.append(f"spread tight {spread_pct:.3f}%")
    elif spread_pct >= 0.35:
        score -= 0.16
        reasons.append(f"spread wide {spread_pct:.3f}%")
    if imbalance >= 0.15:
        score += 0.06
        reasons.append(f"positive depth imbalance {imbalance:.2f}")
    elif imbalance <= -0.18:
        score -= 0.08
        reasons.append(f"negative depth imbalance {imbalance:.2f}")

    score = round(max(0.0, min(1.0, score)), 2)
    ready = score >= 0.64 and bid_ask_ratio >= 1.05 and spread_pct <= 0.25
    if score >= 0.64:
        bias = "offense"
    elif score <= 0.36:
        bias = "defense"
    else:
        bias = "balanced"
    if not reasons:
        reasons.append("orderbook neutral")

    return {
        "orderbook_ready": ready,
        "orderbook_score": score,
        "orderbook_bias": bias,
        "orderbook_reasons": reasons,
        "orderbook_bid_ask_ratio": round(bid_ask_ratio, 3),
        "orderbook_top_ratio": round(top_ratio, 3),
        "orderbook_spread_pct": round(spread_pct, 4),
        "orderbook_imbalance": round(imbalance, 3),
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

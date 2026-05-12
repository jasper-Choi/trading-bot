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


def sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def bollinger_bands(
    values: list[float], period: int = 20, std_mult: float = 2.0
) -> dict[str, list[float | None]]:
    """Return upper, middle (SMA), lower Bollinger Bands."""
    middle = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = middle[i]
        if mean is None:
            continue
        std = (sum((v - mean) ** 2 for v in window) / period) ** 0.5
        upper[i] = mean + std_mult * std
        lower[i] = mean - std_mult * std
    return {"upper": upper, "middle": middle, "lower": lower}


def stochastic(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    k_period: int = 14,
    d_period: int = 3,
) -> dict[str, list[float | None]]:
    """Stochastic oscillator — %K (fast) and %D (SMA of K)."""
    k_vals: list[float | None] = [None] * len(closes)
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1 : i + 1])
        ll = min(lows[i - k_period + 1 : i + 1])
        denom = hh - ll
        k_vals[i] = (closes[i] - ll) / denom * 100.0 if denom > 0 else 50.0
    # %D = SMA of %K; treat None slots as 50.0 only for SMA input, then mask back
    valid_k = [v if v is not None else 0.0 for v in k_vals]
    d_raw = sma(valid_k, d_period)
    d_vals: list[float | None] = [
        None if k_vals[i] is None else d_raw[i] for i in range(len(closes))
    ]
    return {"k": k_vals, "d": d_vals}


def detect_airborne_signal(
    candles: list[dict[str, Any]],
    ema_period: int = 20,
    threshold_pct: float = 1.5,
) -> dict[str, Any]:
    """MG 단타 에어본(Airborne) 이격도 신호.

    가격이 EMA에서 임계값(threshold_pct %) 이상 이탈했을 때 평균회귀 진입 신호.
    - airborne_long  : 가격이 EMA 아래로 너무 많이 이격 → 롱 진입 타점
    - airborne_short : 가격이 EMA 위로 너무 많이 이격 → 숏 진입 타점
    - deviation_sigma: 최근 변동성 기준 이탈 강도 (클수록 강한 신호)
    """
    closes = [float(c["close"]) for c in candles]
    if len(closes) < ema_period + 5:
        return {
            "airborne_long": False,
            "airborne_short": False,
            "deviation_pct": 0.0,
            "deviation_sigma": 0.0,
            "airborne_score": 0.0,
            "airborne_ema": 0.0,
            "airborne_reasons": ["not enough candles"],
        }
    ema_vals = ema(closes, ema_period)
    last_close = closes[-1]
    last_ema = ema_vals[-1]
    if not last_ema:
        return {
            "airborne_long": False, "airborne_short": False,
            "deviation_pct": 0.0, "deviation_sigma": 0.0,
            "airborne_score": 0.0, "airborne_ema": 0.0,
            "airborne_reasons": ["ema zero"],
        }
    deviation_pct = (last_close - last_ema) / last_ema * 100

    # 동적 임계값: 최근 20봉의 평균 이격도로 정규화
    lookback = min(20, len(closes))
    avg_abs_dev = sum(
        abs((closes[-(i + 1)] - ema_vals[-(i + 1)]) / ema_vals[-(i + 1)] * 100)
        for i in range(lookback)
        if ema_vals[-(i + 1)]
    ) / lookback
    deviation_sigma = abs(deviation_pct) / avg_abs_dev if avg_abs_dev > 0 else 0.0

    # 에어본 롱: 가격이 EMA 밑으로 이격 (평균회귀 롱 타점)
    airborne_long = deviation_pct <= -threshold_pct
    # 에어본 숏: 가격이 EMA 위로 이격 (평균회귀 숏 타점)
    airborne_short = deviation_pct >= threshold_pct

    # 볼린저밴드 위치
    bb = bollinger_bands(closes, period=20, std_mult=2.0)
    bb_upper = bb["upper"][-1]
    bb_lower = bb["lower"][-1]
    bb_mid = bb["middle"][-1]
    bb_pct_b: float = 0.5  # 기본값 (밴드 중간)
    if bb_upper is not None and bb_lower is not None and bb_upper != bb_lower:
        bb_pct_b = (last_close - bb_lower) / (bb_upper - bb_lower)
    # %B < 0.20 → 하단 밴드 근처 (추가 롱 신호)
    # %B > 0.80 → 상단 밴드 근처 (추가 숏 신호)
    at_bb_lower = bb_pct_b < 0.20
    at_bb_upper = bb_pct_b > 0.80

    # 최종 score: 이격 강도 + BB 위치 보정
    airborne_score = min(1.0, deviation_sigma / 2.5)
    if airborne_long and at_bb_lower:
        airborne_score = min(1.0, airborne_score + 0.15)
    if airborne_short and at_bb_upper:
        airborne_score = min(1.0, airborne_score + 0.15)

    reasons: list[str] = []
    if airborne_long:
        reasons.append(f"airborne_long dev={deviation_pct:.2f}% sigma={deviation_sigma:.1f}x")
    if airborne_short:
        reasons.append(f"airborne_short dev={deviation_pct:.2f}% sigma={deviation_sigma:.1f}x")
    if at_bb_lower:
        reasons.append(f"bb_lower touch pctB={bb_pct_b:.2f}")
    if at_bb_upper:
        reasons.append(f"bb_upper touch pctB={bb_pct_b:.2f}")

    return {
        "airborne_long": airborne_long,
        "airborne_short": airborne_short,
        "deviation_pct": round(deviation_pct, 3),
        "deviation_sigma": round(deviation_sigma, 2),
        "airborne_score": round(airborne_score, 3),
        "airborne_ema": round(last_ema, 6),
        "bb_pct_b": round(bb_pct_b, 3),
        "at_bb_lower": at_bb_lower,
        "at_bb_upper": at_bb_upper,
        "airborne_reasons": reasons,
    }


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


def detect_pullback_entry(
    candles: list[dict[str, Any]],
    spike_lookback: int = 8,
    min_spike_pct: float = 1.0,
) -> dict[str, Any]:
    """
    Detects the 'first pullback' entry pattern used by Ross Cameron, Raschke, Minervini:
    1. Prior N candles showed a momentum spike (>= min_spike_pct)
    2. Current price has pulled back from the spike high (volume contracting)
    3. Price is near the EMA10 sweet spot — not extended, not reversed

    This is the opposite of chasing: we wait for price to come to us after
    a confirmed spike, improving both entry price and stop-loss placement.
    """
    _empty: dict[str, Any] = {
        "pullback_detected": False,
        "pullback_score": 0.0,
        "spike_pct": 0.0,
        "retrace_from_high_pct": 0.0,
        "vol_contracted_on_pullback": False,
        "in_pullback_zone": False,
        "pullback_gap_from_ema_pct": 0.0,
    }
    if len(candles) < spike_lookback + 4:
        return _empty

    closes = [float(c["close"]) for c in candles]
    highs = [float(c.get("high") or c["close"]) for c in candles]
    volumes = [float(c.get("volume") or 0.0) for c in candles]

    ema10_vals = ema(closes, 10)
    ema_val = ema10_vals[-1]
    current_close = closes[-1]

    # Spike window: the lookback+2 candles before the most recent 2
    spike_window = candles[-(spike_lookback + 2):-2]
    if not spike_window:
        return _empty

    spike_closes = [float(c["close"]) for c in spike_window]
    spike_highs = [float(c.get("high") or c["close"]) for c in spike_window]
    spike_volumes = [float(c.get("volume") or 0.0) for c in spike_window]

    anchor_close = spike_closes[0]
    spike_high = max(spike_highs)
    max_spike_vol = max(spike_volumes) if spike_volumes else 0.0

    spike_pct = (spike_high - anchor_close) / anchor_close * 100 if anchor_close > 0 else 0.0
    retrace_from_high_pct = (spike_high - current_close) / spike_high * 100 if spike_high > 0 else 0.0
    pullback_gap_from_ema = (current_close - ema_val) / ema_val * 100 if ema_val > 0 else 0.0
    in_pullback_zone = -1.0 <= pullback_gap_from_ema <= 2.5

    recent_vol_avg = sum(volumes[-3:]) / max(len(volumes[-3:]), 1) if volumes[-3:] else 0.0
    vol_contracted_on_pullback = max_spike_vol > 0 and recent_vol_avg < max_spike_vol * 0.65

    pullback_detected = (
        spike_pct >= min_spike_pct
        and 0.3 <= retrace_from_high_pct <= 3.5
        and in_pullback_zone
        and current_close >= anchor_close
    )

    score = 0.0
    if pullback_detected:
        score = 0.45
        if vol_contracted_on_pullback:
            score += 0.25
        if 0.0 <= pullback_gap_from_ema <= 2.0:
            score += 0.15
        if 0.5 <= retrace_from_high_pct <= 2.5:
            score += 0.15

    return {
        "pullback_detected": pullback_detected,
        "pullback_score": round(min(score, 1.0), 3),
        "spike_pct": round(spike_pct, 2),
        "retrace_from_high_pct": round(retrace_from_high_pct, 2),
        "vol_contracted_on_pullback": vol_contracted_on_pullback,
        "in_pullback_zone": in_pullback_zone,
        "pullback_gap_from_ema_pct": round(pullback_gap_from_ema, 2),
    }


def summarize_trend_following_context(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """15m chart trend gate: trade fast only when the higher-timeframe path is up."""
    empty = {
        "trend_follow_score": 0.0,
        "trend_alignment": "unknown",
        "trend_entry_allowed": False,
        "ema_stack_bullish": False,
        "price_above_trend_ema": False,
        "trend_slope_pct": 0.0,
        "trend_reasons": ["not enough candles for trend-following context"],
    }
    if len(candles) < 36:
        return empty

    closes = [float(item["close"]) for item in candles]
    highs = [float(item.get("high") or item["close"]) for item in candles]
    lows = [float(item.get("low") or item["close"]) for item in candles]
    last_close = closes[-1]
    ema8 = ema(closes, 8)
    ema21 = ema(closes, 21)
    ema34 = ema(closes, 34)
    if not ema8 or not ema21 or not ema34 or ema21[-4] <= 0:
        return empty

    ema_stack_bullish = ema8[-1] > ema21[-1] > ema34[-1]
    ema_stack_bearish = ema8[-1] < ema21[-1] < ema34[-1]
    price_above_trend_ema = last_close > ema21[-1]
    trend_slope_pct = ((ema21[-1] - ema21[-4]) / ema21[-4]) * 100
    fast_slope_pct = ((ema8[-1] - ema8[-4]) / ema8[-4]) * 100 if ema8[-4] > 0 else 0.0
    recent_high = max(highs[-8:])
    prior_high = max(highs[-18:-8])
    recent_low = min(lows[-8:])
    prior_low = min(lows[-18:-8])
    higher_high = recent_high >= prior_high * 0.998
    higher_low = recent_low >= prior_low * 0.995
    extension_pct = ((last_close - ema21[-1]) / ema21[-1]) * 100 if ema21[-1] > 0 else 0.0
    pullback_zone = ema_stack_bullish and -0.7 <= extension_pct <= 2.6 and trend_slope_pct > -0.05
    late_extension = extension_pct >= 5.0 or fast_slope_pct >= 2.8

    score = 0.12
    reasons: list[str] = []
    if ema_stack_bullish:
        score += 0.28
        reasons.append("15m EMA8>EMA21>EMA34 bullish stack")
    elif ema_stack_bearish:
        score -= 0.18
        reasons.append("15m EMA stack bearish")
    else:
        reasons.append("15m EMA stack mixed")

    if price_above_trend_ema:
        score += 0.16
        reasons.append(f"price above EMA21 ({extension_pct:.2f}%)")
    else:
        score -= 0.08
        reasons.append(f"price below EMA21 ({extension_pct:.2f}%)")

    if trend_slope_pct >= 0.18:
        score += 0.18
        reasons.append(f"EMA21 rising {trend_slope_pct:.2f}%/3 bars")
    elif trend_slope_pct >= 0.02:
        score += 0.08
        reasons.append(f"EMA21 slightly rising {trend_slope_pct:.2f}%/3 bars")
    else:
        score -= 0.10
        reasons.append(f"EMA21 flat/falling {trend_slope_pct:.2f}%/3 bars")

    if higher_high:
        score += 0.10
        reasons.append("recent structure made/held higher high")
    if higher_low:
        score += 0.10
        reasons.append("recent structure held higher low")
    if pullback_zone:
        score += 0.10
        reasons.append("trend pullback zone near EMA21")
    if late_extension:
        score -= 0.18
        reasons.append("late extension risk vs EMA trend")

    score = round(max(0.0, min(1.0, score)), 3)
    if ema_stack_bearish and not price_above_trend_ema:
        alignment = "downtrend"
    elif late_extension and score >= 0.52:
        alignment = "late_extension"
    elif pullback_zone and score >= 0.52:
        alignment = "pullback_long"
    elif ema_stack_bullish and price_above_trend_ema and trend_slope_pct >= 0.02 and score >= 0.56:
        alignment = "trend_long"
    else:
        alignment = "range"

    return {
        "trend_follow_score": score,
        "trend_alignment": alignment,
        "trend_entry_allowed": alignment in {"trend_long", "pullback_long"},
        "ema_stack_bullish": ema_stack_bullish,
        "price_above_trend_ema": price_above_trend_ema,
        "trend_slope_pct": round(trend_slope_pct, 3),
        "trend_extension_pct": round(extension_pct, 2),
        "trend_reasons": reasons,
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
    if burst_change > 4.5:
        score -= 0.08
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

    # --- Chart trend-following overlay ---
    trend = summarize_trend_following_context(candles)
    trend_score = float(trend.get("trend_follow_score", 0.0) or 0.0)
    trend_alignment = str(trend.get("trend_alignment", "unknown") or "unknown")
    if trend_alignment in {"trend_long", "pullback_long"}:
        score += 0.10 + (trend_score * 0.08)
    elif trend_alignment == "late_extension":
        score -= 0.08
    elif trend_alignment == "downtrend":
        score -= 0.16
    else:
        score -= 0.04
    reasons.extend(list(trend.get("trend_reasons", []))[:3])

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
    pullback = detect_pullback_entry(candles)
    choch_bullish = bool(ict.get("choch_bullish"))
    bos_bullish = bool(ict.get("bos_bullish"))
    choch_bearish = bool(ict.get("choch_bearish"))
    # Early trend entry: structural break (CHoCH/BOS) before full EMA stack confirms.
    trend_early_entry = (
        bool(trend.get("price_above_trend_ema", False))
        and float(trend.get("trend_slope_pct", 0.0) or 0.0) >= 0.08
        and trend_alignment not in {"downtrend", "late_extension"}
        and (choch_bullish or bos_bullish)
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- 에어본(Airborne) 이격도 신호 (MG 단타 매매법) ---
    # 가격이 EMA에서 너무 많이 이탈 → 평균회귀 타점 포착
    # RANGING 시장에서 핵심 진입 전략
    airborne = detect_airborne_signal(candles, ema_period=20, threshold_pct=1.5)

    # --- BB Squeeze Bounce 신호 ---
    # Bollinger Band 폭이 수축(스퀴즈)된 상태에서 하단 터치 후 반등
    # 변동성 압축 → 이격도 복귀 가능성 높음
    _bb = bollinger_bands(closes, period=20, std_mult=2.0)
    _bb_widths: list[float] = []
    for _i in range(len(_bb["upper"])):
        _u, _l, _m = _bb["upper"][_i], _bb["lower"][_i], _bb["middle"][_i]
        if _u is not None and _l is not None and _m is not None and _m > 0:
            _bb_widths.append((_u - _l) / _m)
    _last_bb_width = _bb_widths[-1] if _bb_widths else None
    _recent_widths = _bb_widths[-20:] if len(_bb_widths) >= 20 else _bb_widths
    _min_bb_width_20 = min(_recent_widths) if _recent_widths else None
    # 현재 BB 폭이 최근 20봉 최솟값 대비 15% 이내 → 스퀴즈 상태
    _bb_squeeze_now = (
        _last_bb_width is not None
        and _min_bb_width_20 is not None
        and _last_bb_width <= _min_bb_width_20 * 1.15
    )
    bb_squeeze_bounce = (
        _bb_squeeze_now
        and bool(airborne.get("at_bb_lower", False))   # BB 하단에 있음
        and last_rsi is not None and 22.0 <= last_rsi <= 52.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -5.0
    )

    # --- VWAP Deviation Long 신호 ---
    # 최근 20봉 거래량 가중 평균(VWAP 근사) 대비 이격 과대 → 기관 평균단가 복귀 기대
    _vwap_period = min(20, len(candles))
    _vwap_candles = candles[-_vwap_period:]
    _vols = [float(c.get("volume", c.get("candle_acc_trade_volume", 0.0)) or 0.0) for c in _vwap_candles]
    _prices_vw = [float(c.get("close", c.get("trade_price", 0.0)) or 0.0) for c in _vwap_candles]
    _total_vol = sum(_vols)
    _vwap_val = (
        sum(p * v for p, v in zip(_prices_vw, _vols)) / _total_vol
        if _total_vol > 0 else None
    )
    vwap_approx = _vwap_val
    vwap_deviation_pct = 0.0
    if _vwap_val and _vwap_val > 0 and last_close:
        vwap_deviation_pct = round((last_close - _vwap_val) / _vwap_val * 100, 2)
    vwap_deviation_long = (
        vwap_deviation_pct <= -1.5                      # VWAP 대비 1.5% 이상 아래
        and last_rsi is not None and 20.0 <= last_rsi <= 50.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -6.0
    )

    # --- RSI Extreme Long (극단적 과매도) ---
    # RSI ≤ 22: 매우 드물지만 리바운드 확률 높음
    rsi_extreme_long = (
        last_rsi is not None and last_rsi <= 22.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and float(recent_change) > -8.0
    )

    # --- Stochastic %K 과매도 교차 ---
    # %K < 25(과매도)에서 %D 위로 교차 → 단기 모멘텀 반전 신호
    highs = [float(c.get("high") or c["close"]) for c in candles]
    lows = [float(c.get("low") or c["close"]) for c in candles]
    _stoch = stochastic(highs, lows, closes, k_period=14, d_period=3)
    _sk = _stoch["k"]
    _sd = _stoch["d"]
    _sk_curr = _sk[-1]
    _sk_prev = _sk[-2] if len(_sk) >= 2 else None
    _sd_curr = _sd[-1]
    _sd_prev = _sd[-2] if len(_sd) >= 2 else None
    stoch_k_val = round(float(_sk_curr), 1) if _sk_curr is not None else 50.0
    stoch_oversold_cross = (
        _sk_curr is not None and _sd_curr is not None
        and _sk_prev is not None and _sd_prev is not None
        and float(_sk_curr) < 25.0                  # 과매도 구간
        and float(_sk_curr) >= float(_sd_curr)      # K가 D 위로 교차
        and float(_sk_prev) < float(_sd_prev)       # 직전에는 K < D였음
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -6.0
    )

    # --- MACD 히스토그램 바닥 반전 ---
    # 히스토그램이 음수 구간에서 2봉 연속 상승 → MACD 모멘텀 회복 신호
    _ema12 = ema(closes, 12)
    _ema26 = ema(closes, 26)
    _macd_line = [_ema12[i] - _ema26[i] for i in range(len(closes))]
    _signal_line = ema(_macd_line, 9)
    _histogram = [_macd_line[i] - _signal_line[i] for i in range(len(closes))]
    _hist_curr = _histogram[-1]
    _hist_prev = _histogram[-2] if len(_histogram) >= 2 else None
    _hist_prev2 = _histogram[-3] if len(_histogram) >= 3 else None
    macd_hist_val = round(_hist_curr, 8)
    macd_histogram_reversal = (
        _hist_prev is not None and _hist_prev2 is not None
        and _hist_curr > _hist_prev             # 히스토그램 상승 전환
        and _hist_prev <= _hist_prev2           # 직전 봉은 하락/횡보 (바닥 확인)
        and _hist_prev < 0.0                    # 음수 구간에서 반전
        and last_rsi is not None and last_rsi <= 55.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -5.0
    )

    # --- 캔들 반전 패턴 (Hammer / Doji) ---
    # Hammer: 하단 꼬리 ≥ 몸통 2배, 상단 꼬리 ≤ 몸통 0.5배, 양봉 → 강한 지지 반등
    # Doji: 몸통 ≤ 전체 범위 10% → 매수/매도 균형, 방향 전환 가능성
    _opens = [float(c.get("open") or c["close"]) for c in candles]
    _c_open = _opens[-1]
    _c_close = closes[-1]
    _c_high = highs[-1]
    _c_low = lows[-1]
    _body = abs(_c_close - _c_open)
    _total_range = _c_high - _c_low
    _lower_wick = min(_c_open, _c_close) - _c_low
    _upper_wick = _c_high - max(_c_open, _c_close)
    hammer_candle = (
        _total_range > 0
        and _body > 0
        and _lower_wick >= _body * 2.0          # 하단 꼬리 ≥ 몸통 2배
        and _upper_wick <= _body * 0.5          # 상단 꼬리 ≤ 몸통 절반
        and _c_close >= _c_open                 # 양봉 (반등 강도 확인)
        and last_rsi is not None and last_rsi <= 50.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
    )
    doji_candle = (
        _total_range > 0
        and _body / _total_range <= 0.10        # 몸통 ≤ 전체 범위 10%
        and _total_range / _c_close >= 0.003    # 전체 범위 ≥ 0.3% (의미있는 꼬리)
        and last_rsi is not None and last_rsi <= 50.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
    )

    # --- Volume Climax Reversal ---
    # Panic/forced selling often prints a large lower wick on abnormal volume.
    # In RANGING markets this is a higher-quality bounce setup than a naked RSI signal.
    _all_volumes = [float(c.get("volume", c.get("candle_acc_trade_volume", 0.0)) or 0.0) for c in candles]
    _avg_vol20 = sum(_all_volumes[-21:-1]) / 20 if len(_all_volumes) >= 21 and sum(_all_volumes[-21:-1]) > 0 else 0.0
    volume_climax_ratio = round(_all_volumes[-1] / _avg_vol20, 2) if _avg_vol20 > 0 else 0.0
    _lower_wick_ratio = _lower_wick / _total_range if _total_range > 0 else 0.0
    _close_position = (_c_close - _c_low) / _total_range if _total_range > 0 else 0.0
    volume_climax_reversal = (
        volume_climax_ratio >= 2.2
        and _lower_wick_ratio >= 0.40
        and _close_position >= 0.55
        and last_rsi is not None and last_rsi <= 55.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and float(recent_change) > -8.0
    )

    # --- Support Reclaim Long ---
    # Price probes the recent range low, then closes back above it.
    _support_level = min(lows[-21:-1]) if len(lows) >= 21 else min(lows[:-1] or lows)
    support_reclaim_long = (
        _support_level > 0
        and _c_low <= _support_level * 1.004
        and _c_close >= _support_level * 1.002
        and _lower_wick_ratio >= 0.22
        and last_rsi is not None and last_rsi <= 55.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and float(recent_change) > -8.0
    )

    # --- Range Breakout Long ---
    # Global regime can be RANGING while a single coin is breaking out of its own box.
    # This path is for HYPER/BIO/SPK-style scanner leaders: recent range high reclaimed
    # with volume and no bearish RSI/ICT warning. It is a continuation setup, not mean reversion.
    _range_high_20 = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1] or highs)
    _range_low_20 = min(lows[-21:-1]) if len(lows) >= 21 else min(lows[:-1] or lows)
    range_width_pct = (
        round((_range_high_20 - _range_low_20) / _range_low_20 * 100, 2)
        if _range_low_20 > 0 else 0.0
    )
    range_breakout_long = (
        _range_high_20 > 0
        and _c_close >= _range_high_20 * 1.002
        and volume_climax_ratio >= 1.35
        and last_rsi is not None and 52.0 <= last_rsi <= 82.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and float(trend.get("trend_extension_pct", 0.0) or 0.0) <= 5.5
        and _close_position >= 0.60
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- High Tight Flag Long ---
    # Strong coin pauses near the highs instead of giving back the move. We wait for a
    # compact last-4-candle range and price holding close to the 20-bar high.
    _pre_flag_low = min(lows[-18:-5]) if len(lows) >= 18 else _range_low_20
    _recent_high_12 = max(highs[-13:-1]) if len(highs) >= 13 else _range_high_20
    impulse_12_pct = (
        round((_recent_high_12 - _pre_flag_low) / _pre_flag_low * 100, 2)
        if _pre_flag_low > 0 else 0.0
    )
    flag_range_pct = (
        round((max(highs[-5:-1]) - min(lows[-5:-1])) / min(lows[-5:-1]) * 100, 2)
        if len(lows) >= 5 and min(lows[-5:-1]) > 0 else 0.0
    )
    high_tight_flag_long = (
        impulse_12_pct >= 4.0
        and 0.15 <= flag_range_pct <= 2.2
        and _c_close >= _recent_high_12 * 0.985
        and last_rsi is not None and 50.0 <= last_rsi <= 82.0
        and volume_climax_ratio >= 0.65
        and trend_alignment not in {"downtrend", "late_extension"}
        and float(trend.get("trend_extension_pct", 0.0) or 0.0) <= 6.5
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- Williams %R Oversold Cross (RANGING) ---
    # %R = (highest_high - close) / (highest_high - lowest_low) × -100
    # 과매도 구간(-80 이하)에서 -80 위로 교차 → 단기 반등 신호
    _wr_period = 14
    _wr_vals: list[float | None] = [None] * len(closes)
    for _wi in range(_wr_period - 1, len(closes)):
        _wh = max(highs[_wi - _wr_period + 1 : _wi + 1])
        _wl = min(lows[_wi - _wr_period + 1 : _wi + 1])
        _wd = _wh - _wl
        _wr_vals[_wi] = (closes[_wi] - _wh) / _wd * 100.0 if _wd > 0 else -50.0
    _wr_curr = _wr_vals[-1]
    _wr_prev = _wr_vals[-2] if len(_wr_vals) >= 2 else None
    wr_val = round(float(_wr_curr), 1) if _wr_curr is not None else -50.0
    williams_r_oversold = (
        _wr_curr is not None and _wr_prev is not None
        and _wr_curr > -80.0            # 현재 과매도 탈출
        and _wr_prev <= -80.0           # 직전 과매도 구간에 있었음
        and _wr_curr < -50.0            # 아직 중립 이하
        and last_rsi is not None and last_rsi <= 50.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -6.0
    )

    # --- CCI Oversold Bounce (RANGING) ---
    # CCI = (Typical Price - SMA(TP, 20)) / (0.015 × Mean Deviation)
    # CCI ≤ -100 (과매도) 상태에서 반등 시작 → 평균회귀 신호
    _cci_period = 20
    _tp_cci = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    _cci_vals: list[float | None] = [None] * len(_tp_cci)
    for _ci in range(_cci_period - 1, len(_tp_cci)):
        _tp_window = _tp_cci[_ci - _cci_period + 1 : _ci + 1]
        _tp_mean = sum(_tp_window) / _cci_period
        _mean_dev = sum(abs(v - _tp_mean) for v in _tp_window) / _cci_period
        _cci_vals[_ci] = (_tp_cci[_ci] - _tp_mean) / (0.015 * _mean_dev) if _mean_dev > 0 else 0.0
    _cci_curr = _cci_vals[-1]
    _cci_prev = _cci_vals[-2] if len(_cci_vals) >= 2 else None
    cci_val = round(float(_cci_curr), 1) if _cci_curr is not None else 0.0
    cci_oversold_bounce = (
        _cci_curr is not None and _cci_prev is not None
        and _cci_curr <= -100.0         # 과매도 구간
        and _cci_curr > _cci_prev       # 반등 시작 (상승 전환)
        and last_rsi is not None and last_rsi <= 52.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -6.0
    )

    # --- Keltner Channel Lower Touch (RANGING) ---
    # KC Middle = EMA20, KC Lower = EMA20 - 1.5 × ATR14
    # 가격이 켈트너 하단 밴드 터치 → 저변동성 구간 평균회귀 설정
    _kc_ema = ema(closes, 20)
    _tr_kc = [
        max(
            highs[_ki] - lows[_ki],
            abs(highs[_ki] - closes[_ki - 1]) if _ki > 0 else highs[_ki] - lows[_ki],
            abs(lows[_ki] - closes[_ki - 1]) if _ki > 0 else highs[_ki] - lows[_ki],
        )
        for _ki in range(len(closes))
    ]
    _atr_kc = ema(_tr_kc, 14)
    _kc_lower_val = _kc_ema[-1] - 1.5 * _atr_kc[-1]
    keltner_lower_touch = (
        last_close <= _kc_lower_val * 1.005   # 켈트너 하단 터치 (0.5% 허용오차)
        and last_rsi is not None and last_rsi <= 52.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -6.0
    )

    # --- MFI (Money Flow Index) Oversold (RANGING) ---
    # 거래량 가중 RSI; MFI ≤ 25 = 자금 유출 극단 → 반등 설정
    _mfi_period = 14
    _tp_mfi = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    _mf_pos_list: list[float] = []
    _mf_neg_list: list[float] = []
    for _mi in range(1, len(_tp_mfi)):
        _mf = _tp_mfi[_mi] * _all_volumes[_mi]
        if _tp_mfi[_mi] > _tp_mfi[_mi - 1]:
            _mf_pos_list.append(_mf)
            _mf_neg_list.append(0.0)
        elif _tp_mfi[_mi] < _tp_mfi[_mi - 1]:
            _mf_pos_list.append(0.0)
            _mf_neg_list.append(_mf)
        else:
            _mf_pos_list.append(0.0)
            _mf_neg_list.append(0.0)
    _mfi_vals: list[float | None] = [None] * len(closes)
    for _mi in range(_mfi_period, len(closes)):
        _pos_s = sum(_mf_pos_list[_mi - _mfi_period : _mi])
        _neg_s = sum(_mf_neg_list[_mi - _mfi_period : _mi])
        if _neg_s > 0:
            _mfi_vals[_mi] = 100.0 - 100.0 / (1.0 + _pos_s / _neg_s)
        elif _pos_s > 0:
            _mfi_vals[_mi] = 100.0
        else:
            _mfi_vals[_mi] = 50.0
    _mfi_curr = _mfi_vals[-1]
    mfi_val = round(float(_mfi_curr), 1) if _mfi_curr is not None else 50.0
    mfi_oversold = (
        _mfi_curr is not None
        and _mfi_curr <= 25.0
        and last_rsi is not None and last_rsi <= 55.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -6.0
    )

    # --- EMA Crossover Long (TRENDING) ---
    # EMA8이 EMA21을 위로 교차 → 단기 모멘텀 전환 신호 (골든크로스 변형)
    _ema8 = ema(closes, 8)
    _ema21 = ema(closes, 21)
    ema_cross_long = (
        len(_ema8) >= 2 and len(_ema21) >= 2
        and _ema8[-1] > _ema21[-1]           # 현재 EMA8 > EMA21
        and _ema8[-2] <= _ema21[-2]          # 직전 EMA8 ≤ EMA21 (교차 직후)
        and last_rsi is not None and 40.0 <= last_rsi <= 72.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- VWAP Reclaim Long (TRENDING) ---
    # 가격이 VWAP 아래에서 VWAP 위로 복귀 → 기관 평균단가 재탈환 신호
    vwap_cross_long = (
        vwap_approx is not None and vwap_approx > 0
        and last_close > vwap_approx            # 현재 봉: VWAP 위
        and float(closes[-2]) <= vwap_approx    # 직전 봉: VWAP 아래
        and last_rsi is not None and 40.0 <= last_rsi <= 72.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- RSI Momentum Flip Long (TRENDING) ---
    # RSI가 50 아래에서 위로 교차 → 약세→강세 모멘텀 전환 확인
    _rsi_series = rsi_series(closes, 14)
    _rsi_curr2 = _rsi_series[-1]
    _rsi_prev2 = _rsi_series[-2] if len(_rsi_series) >= 2 else None
    rsi_flip_long = (
        _rsi_curr2 is not None and _rsi_prev2 is not None
        and float(_rsi_prev2) < 50.0              # 직전: 50 아래 (약세)
        and float(_rsi_curr2) >= 50.0             # 현재: 50 이상 (강세 전환)
        and float(_rsi_curr2) <= 68.0             # 과열 제외
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -3.0
    )

    # --- MACD Bullish Cross (TRENDING) ---
    # MACD 선이 시그널선을 아래서 위로 교차 → 중기 모멘텀 전환 신호 (황금교차)
    _macd_curr = _macd_line[-1]
    _macd_prev2 = _macd_line[-2] if len(_macd_line) >= 2 else None
    _sig_curr = _signal_line[-1]
    _sig_prev2 = _signal_line[-2] if len(_signal_line) >= 2 else None
    macd_bull_cross = (
        _macd_prev2 is not None and _sig_prev2 is not None
        and float(_macd_curr) > float(_sig_curr)   # 현재: MACD > Signal
        and float(_macd_prev2) <= float(_sig_prev2) # 직전: MACD ≤ Signal (교차 직후)
        and float(_macd_curr) < 0.0 * abs(float(last_close)) * 0.01  # 아직 과열 아님
        and last_rsi is not None and last_rsi <= 72.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -3.0
    )

    # --- Triple Candle Bull (TRENDING) ---
    # 3봉 연속 양봉 + 볼륨 증가 → 추세 가속 신호 (모멘텀 연속성 확인)
    volumes = [float(c.get("volume") or 0.0) for c in candles]
    _opens_b3 = [float(c.get("open") or c["close"]) for c in candles]
    triple_candle_bull = (
        len(closes) >= 4
        and closes[-1] > _opens_b3[-1]   # 현재봉 양봉
        and closes[-2] > _opens_b3[-2]   # 직전봉 양봉
        and closes[-3] > _opens_b3[-3]   # 3봉 전 양봉
        and closes[-1] > closes[-2] > closes[-3]  # 연속 상승
        and (volumes[-1] > volumes[-2] or volumes[-1] > volumes[-3])  # 볼륨 증가
        and last_rsi is not None and 42.0 <= last_rsi <= 72.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- Supertrend Long (TRENDING) ---
    # Supertrend(10, 3.0): ATR 기반 추세 필터 — 가격이 슈퍼트렌드 선 위로 교차 시 매수
    _st_period = 10
    _st_mult = 3.0
    _st_atr_vals: list[float] = []
    for _si in range(1, len(closes)):
        _tr = max(
            highs[_si] - lows[_si],
            abs(highs[_si] - closes[_si - 1]),
            abs(lows[_si] - closes[_si - 1]),
        )
        _st_atr_vals.append(_tr)
    # EMA of ATR
    _st_atr_ema: list[float] = []
    if _st_atr_vals:
        _st_k = 2.0 / (_st_period + 1)
        _st_atr_ema.append(sum(_st_atr_vals[:_st_period]) / _st_period if len(_st_atr_vals) >= _st_period else _st_atr_vals[0])
        for _v in _st_atr_vals[len(_st_atr_ema):]:
            _st_atr_ema.append(_v * _st_k + _st_atr_ema[-1] * (1 - _st_k))
    # Supertrend upper/lower bands (aligned to closes index from 1)
    _st_bullish_prev = False
    _st_bullish_curr = False
    if len(_st_atr_ema) >= 2 and len(closes) >= _st_period + 2:
        _offset = max(0, len(_st_atr_ema) - len(closes) + 1)
        for _sj in range(len(_st_atr_ema)):
            _ci = _sj + 1  # closes index
            if _ci >= len(closes):
                break
            _mid = (highs[_ci] + lows[_ci]) / 2
            _atr_v = _st_atr_ema[_sj]
            _up = _mid + _st_mult * _atr_v
            _dn = _mid - _st_mult * _atr_v
            _prev_bull = _st_bullish_curr
            _st_bullish_curr = closes[_ci] > _up if not _prev_bull else closes[_ci] >= _dn
            _st_bullish_prev = _prev_bull
    supertrend_long = (
        _st_bullish_curr and not _st_bullish_prev  # 이번 봉에서 불리쉬 전환
        and last_rsi is not None and last_rsi <= 75.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -4.0
    )

    # --- Bullish Engulfing (DUAL: RANGING 반전 / TRENDING 지속) ---
    # 직전봉 음봉 + 현재봉 양봉으로 완전 흡수 → 강한 매수 전환 신호
    _opens_eng = [float(c.get("open") or c["close"]) for c in candles]
    _vol20_avg = sum(volumes[-21:-1]) / 20.0 if len(volumes) >= 21 else (sum(volumes[:-1]) / max(len(volumes) - 1, 1))
    engulfing_bull = (
        len(closes) >= 3
        and _opens_eng[-2] > closes[-2]          # 직전봉: 음봉 (open > close)
        and closes[-1] > _opens_eng[-1]          # 현재봉: 양봉
        and _opens_eng[-1] <= closes[-2]         # 현재봉 open이 직전봉 close 이하
        and closes[-1] >= _opens_eng[-2]         # 현재봉 close가 직전봉 open 이상 (완전 흡수)
        and (closes[-1] - _opens_eng[-1]) > ((_opens_eng[-2] - closes[-2]) * 0.8)  # 몸통 비교
        and volumes[-1] >= _vol20_avg * 1.1      # 평균 이상 거래량
        and last_rsi is not None and last_rsi <= 70.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- Volume Surge Long (CATALYST: 기관 매집 신호) ---
    # 현재봉 거래량이 20봉 평균의 2.5배 이상 + 양봉 → 수급 강한 상승 신호
    vol_surge_long = (
        len(volumes) >= 5
        and _vol20_avg > 0
        and volumes[-1] >= _vol20_avg * 2.5      # 거래량 2.5배 급증
        and closes[-1] > _opens_eng[-1]          # 양봉 (매수 주도)
        and closes[-1] > closes[-2]              # 전봉 대비 상승
        and last_rsi is not None and last_rsi <= 75.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) < 8.0           # 과열 급등 중 아님
    )

    # --- ADX Trend Strong (TRENDING 강도 확인) ---
    # ADX ≥ 22 + DI+ > DI-: 방향성 있는 강한 추세 진입
    _adx_period = 14
    _adx_plus_dm: list[float] = []
    _adx_minus_dm: list[float] = []
    _adx_tr_list: list[float] = []
    for _ai in range(1, len(closes)):
        _h_diff = highs[_ai] - highs[_ai - 1]
        _l_diff = lows[_ai - 1] - lows[_ai]
        _adx_plus_dm.append(max(_h_diff, 0.0) if _h_diff > _l_diff else 0.0)
        _adx_minus_dm.append(max(_l_diff, 0.0) if _l_diff > _h_diff else 0.0)
        _adx_tr_list.append(max(
            highs[_ai] - lows[_ai],
            abs(highs[_ai] - closes[_ai - 1]),
            abs(lows[_ai] - closes[_ai - 1]),
        ))
    adx_val = 0.0
    di_plus_val = 0.0
    di_minus_val = 0.0
    if len(_adx_tr_list) >= _adx_period * 2:
        _atr14 = sum(_adx_tr_list[:_adx_period])
        _pdm14 = sum(_adx_plus_dm[:_adx_period])
        _mdm14 = sum(_adx_minus_dm[:_adx_period])
        for _ai in range(_adx_period, len(_adx_tr_list)):
            _atr14 = _atr14 - _atr14 / _adx_period + _adx_tr_list[_ai]
            _pdm14 = _pdm14 - _pdm14 / _adx_period + _adx_plus_dm[_ai]
            _mdm14 = _mdm14 - _mdm14 / _adx_period + _adx_minus_dm[_ai]
        di_plus_val = (_pdm14 / _atr14 * 100) if _atr14 > 0 else 0.0
        di_minus_val = (_mdm14 / _atr14 * 100) if _atr14 > 0 else 0.0
        _di_sum = di_plus_val + di_minus_val
        _dx = abs(di_plus_val - di_minus_val) / _di_sum * 100 if _di_sum > 0 else 0.0
        adx_val = _dx  # simplified: use last DX as ADX proxy
    adx_trend_strong = (
        adx_val >= 22.0
        and di_plus_val > di_minus_val           # DI+ > DI-: 상승 방향성
        and bool(trend.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and last_rsi is not None and 42.0 <= last_rsi <= 74.0
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- BB Squeeze Breakout (추세 전환 / 브레이크아웃) ---
    # 볼린저 밴드 폭이 최근 20봉 최소 → 스퀴즈 해소 + 상단 돌파 → 추세 시작
    _bb_sq_period = 20
    _bb_sq_std_list: list[float] = []
    for _bi in range(_bb_sq_period - 1, len(closes)):
        _w = closes[_bi - _bb_sq_period + 1 : _bi + 1]
        _m = sum(_w) / _bb_sq_period
        _s = (sum((v - _m) ** 2 for v in _w) / _bb_sq_period) ** 0.5
        _bb_sq_std_list.append(_s)
    _bb_sq_width_list = [s * 2 / max(closes[_bb_sq_period - 1 + i], 1.0) for i, s in enumerate(_bb_sq_std_list)]
    bb_sq_width_curr = _bb_sq_width_list[-1] if _bb_sq_width_list else 0.0
    bb_sq_width_min20 = min(_bb_sq_width_list[-20:]) if len(_bb_sq_width_list) >= 20 else bb_sq_width_curr
    _bb_sq_upper = (
        (sum(closes[-_bb_sq_period:]) / _bb_sq_period + 2.0 * _bb_sq_std_list[-1])
        if _bb_sq_std_list else closes[-1]
    )
    bb_squeeze_breakout = (
        len(_bb_sq_std_list) >= 20
        and bb_sq_width_curr <= bb_sq_width_min20 * 1.05   # 최근 스퀴즈 구간
        and closes[-1] >= _bb_sq_upper * 0.998             # 상단 밴드 터치/돌파
        and volumes[-1] >= _vol20_avg * 1.3                # 거래량 확인
        and last_rsi is not None and 48.0 <= last_rsi <= 80.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- Consecutive Higher Lows (추세 구조 확인) ---
    # 3개의 스윙 저점이 연속으로 상승 → 상승 추세 구조 확인 (Higher Low 패턴)
    _hl_lows = lows
    _hl_n = len(_hl_lows)
    consecutive_higher_lows = False
    if _hl_n >= 15:
        # 3개의 로컬 최저점: 5봉, 10봉, 15봉 전 구간의 최저값 비교
        _hl_1 = min(_hl_lows[-4:-1])    # 최근 3봉 (직전~3봉 전)
        _hl_2 = min(_hl_lows[-9:-4])    # 4~8봉 전
        _hl_3 = min(_hl_lows[-14:-9])   # 9~13봉 전
        consecutive_higher_lows = (
            _hl_1 > _hl_2 > _hl_3                      # HL1 > HL2 > HL3: 연속 고점저점
            and (_hl_1 - _hl_3) / max(_hl_3, 1e-9) * 100 >= 0.3  # 최소 0.3% 상승
            and last_rsi is not None and 40.0 <= last_rsi <= 75.0
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
            and bool(trend.get("trend_entry_allowed", False))
        )

    # === BATCH 5: 추가 전략 신호 ===

    # --- Pin Bar Long (RANGING/TRENDING 반전) ---
    # 위꼬리 없이 아래꼬리가 몸통의 2배 이상 → 매도 거부 + 매수 개입 신호
    _pb_opens = [float(c.get("open") or c["close"]) for c in candles]
    pin_bar_long = False
    if len(closes) >= 2:
        _pb_body = abs(closes[-1] - _pb_opens[-1])
        _pb_lower = min(closes[-1], _pb_opens[-1]) - lows[-1]
        _pb_upper = highs[-1] - max(closes[-1], _pb_opens[-1])
        _pb_range = highs[-1] - lows[-1]
        pin_bar_long = (
            _pb_range > 0
            and _pb_lower >= _pb_body * 2.0       # 아래꼬리 ≥ 몸통 2배
            and _pb_lower >= _pb_range * 0.45     # 아래꼬리가 전체 봉 45% 이상
            and _pb_upper <= _pb_body * 0.5       # 위꼬리 작음
            and closes[-1] > _pb_opens[-1]        # 양봉 마감 (강한 버전)
            and last_rsi is not None and last_rsi <= 65.0
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
        )

    # --- Morning Star (RANGING 3봉 반전 패턴) ---
    # 큰 음봉 → 도지/소형봉 → 큰 양봉: 바닥 반전 신호
    morning_star = False
    if len(closes) >= 4:
        _ms_o = _pb_opens
        _ms_b1_bear = _ms_o[-3] > closes[-3] and (_ms_o[-3] - closes[-3]) >= (highs[-3] - lows[-3]) * 0.5
        _ms_b2_small = abs(closes[-2] - _ms_o[-2]) <= (highs[-2] - lows[-2]) * 0.35
        _ms_b3_bull = closes[-1] > _ms_o[-1] and (closes[-1] - _ms_o[-1]) >= (highs[-1] - lows[-1]) * 0.45
        _ms_b3_recovers = closes[-1] >= (_ms_o[-3] + closes[-3]) / 2
        morning_star = (
            _ms_b1_bear and _ms_b2_small and _ms_b3_bull and _ms_b3_recovers
            and last_rsi is not None and 22.0 <= last_rsi <= 52.0
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
        )

    # --- Inside Bar Breakout (RANGING → TRENDING 전환) ---
    # 직전봉 안에서 현재봉이 벗어날 때 → 압축 후 방향성 출현
    inside_bar_breakout = False
    if len(closes) >= 3:
        _ib_mother_high = highs[-2]
        _ib_mother_low = lows[-2]
        _ib_prev_inside = highs[-2] <= highs[-3] and lows[-2] >= lows[-3]  # 직전봉이 inside
        _ib_breakout_up = closes[-1] > _ib_mother_high and closes[-1] > _pb_opens[-1]
        inside_bar_breakout = (
            _ib_prev_inside
            and _ib_breakout_up
            and last_rsi is not None and 45.0 <= last_rsi <= 75.0
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
            and float(recent_change) > -3.0
        )

    # --- RSI Momentum Keep (TRENDING 지속 확인) ---
    # RSI 55~72 유지 + 추세 중 → 추세 지속 가능성 높은 구간
    rsi_momentum_keep = (
        last_rsi is not None and 55.0 <= last_rsi <= 72.0
        and bool(trend.get("trend_entry_allowed", False))
        and trend_alignment in {"trend_long", "pullback_long"}
        and float(trend.get("trend_follow_score", 0.0) or 0.0) >= 0.70
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) >= 0.0
        and float(recent_change) < 6.0
    )

    # --- Open Interest Momentum (거래량 기반 강도 확인) ---
    # 현재봉 거래량 > 1.8x avg AND 3봉 연속 볼륨 증가 + 가격 상승
    oi_momentum_long = False
    if len(volumes) >= 5 and _vol20_avg > 0:
        _oi_vol_3 = all(volumes[-i] > volumes[-i - 1] for i in range(1, 4))  # 3봉 연속 증가
        _oi_price_3 = closes[-1] > closes[-2] > closes[-3]                   # 3봉 연속 상승
        oi_momentum_long = (
            _oi_vol_3
            and _oi_price_3
            and volumes[-1] >= _vol20_avg * 1.8
            and last_rsi is not None and 45.0 <= last_rsi <= 74.0
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
        )

    # --- Demand Zone Bounce (기관 수요 구간 반등) ---
    # 가격이 최근 20봉 지지선 근처에서 반등 + 볼륨 확인
    demand_zone_bounce = False
    if len(closes) >= 21 and _vol20_avg > 0:
        _dz_support = min(lows[-21:-1])
        _dz_resistance = max(highs[-21:-1])
        _dz_range = _dz_resistance - _dz_support
        _dz_zone_bottom = _dz_support + _dz_range * 0.15  # 하위 15%
        _dz_near_support = lows[-1] <= _dz_zone_bottom
        _dz_bounce = closes[-1] > lows[-1] + (highs[-1] - lows[-1]) * 0.6  # 하단에서 60% 회복
        demand_zone_bounce = (
            _dz_near_support
            and _dz_bounce
            and volumes[-1] >= _vol20_avg * 1.2
            and last_rsi is not None and 25.0 <= last_rsi <= 52.0
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
        )

    # === BATCH 6: 마지막 8개 전략 신호 ===

    # --- VWAP + RSI 복합 (RANGING 고확률 조합) ---
    # VWAP 이탈 + RSI 과매도: 두 신호 동시 만족 = 더 높은 신뢰도
    vwap_rsi_combo = (
        bool(airborne.get("airborne_long", False))   # VWAP 이탈 (airborne = VWAP 기반)
        and last_rsi is not None and last_rsi <= 38.0  # RSI 깊은 과매도
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -5.0
    )

    # --- Breakout + Volume Confirm (브레이크아웃 + 거래량 동반) ---
    # 20봉 고점 돌파 + 거래량 1.5배: 신뢰도 높은 브레이크아웃
    breakout_vol_confirm = (
        range_breakout_long                          # 로컬 변수 range_breakout_long 활용
        and len(volumes) >= 5 and _vol20_avg > 0
        and volumes[-1] >= _vol20_avg * 1.5          # 거래량 동반 필수
        and last_rsi is not None and 52.0 <= last_rsi <= 78.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- Hammer + Support (망치형 + 지지선) ---
    # 망치형 캔들이 지지선 근처에서 발생 = 반전 신뢰도 높음
    _hs_hammer = pin_bar_long  # pin_bar_long 재활용 (사실상 hammer와 동일)
    _hs_support_nearby = False
    if len(closes) >= 21:
        _hs_sup = min(lows[-21:-1])
        _hs_sup_zone = _hs_sup * 1.015  # 지지선 1.5% 내
        _hs_support_nearby = lows[-1] <= _hs_sup_zone
    hammer_at_support = (
        _hs_hammer
        and _hs_support_nearby
        and last_rsi is not None and last_rsi <= 50.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- Trend Reversal Early (추세 전환 조기 포착) ---
    # CHoCH 불리쉬 단독 (BOS 없이) + 조건 완화 = 더 이른 포착
    trend_reversal_early = (
        choch_bullish
        and not bos_bullish                          # BOS 아직 없음 (조기 포착)
        and bool(trend.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and last_rsi is not None and 35.0 <= last_rsi <= 62.0
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -2.0
    )

    # --- EMA Bounce (EMA 지지 반등) ---
    # 가격이 EMA20 근처에서 반등 + RSI 적정 수준
    ema_bounce_long = False
    if len(closes) >= 21:
        _eb_ema20 = sum(closes[-20:]) / 20.0
        _eb_near = abs(lows[-1] - _eb_ema20) / max(_eb_ema20, 1e-9) <= 0.008  # EMA20 0.8% 이내
        _eb_bounce = closes[-1] > lows[-1] + (highs[-1] - lows[-1]) * 0.55    # 하단 55% 이상 회복
        ema_bounce_long = (
            _eb_near
            and _eb_bounce
            and closes[-1] > _eb_ema20               # EMA 위로 마감
            and last_rsi is not None and 40.0 <= last_rsi <= 62.0
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
        )

    # --- RSI Bullish Divergence (RSI 불리쉬 다이버전스) ---
    # 가격은 저점을 낮추는데 RSI는 저점을 높임 = 반전 선행 신호
    rsi_bullish_div = False
    if len(closes) >= 10 and len(_rsi_series) >= 10:
        _rd_price_lower = closes[-1] < min(closes[-6:-1])   # 가격: 5봉 최저
        _rd_rsi_higher = float(_rsi_series[-1] or 50.0) > float(_rsi_series[-5] or 50.0)  # RSI: 높아짐
        _rd_rsi_low = float(_rsi_series[-1] or 50.0) <= 45.0  # RSI는 과매도 구간
        rsi_bullish_div = (
            _rd_price_lower
            and _rd_rsi_higher
            and _rd_rsi_low
            and trend_alignment not in {"downtrend", "late_extension"}
            and not choch_bearish
            and not bool(bk.get("rsi_bearish_divergence", False))
            and float(recent_change) > -5.0
        )

    # --- Multi-Signal RANGING Combo (멀티 RANGING 신호 조합) ---
    # 단일 신호 약 → 여러 RANGING 신호 동시: 더 높은 신뢰도
    _ms_ranging_count = sum([
        bb_squeeze_bounce,           # 로컬 변수
        vwap_deviation_long,         # 로컬 변수
        williams_r_oversold,
        cci_oversold_bounce,
        keltner_lower_touch,
        mfi_oversold,
        stoch_oversold_cross,        # 로컬 변수
        volume_climax_reversal,      # 로컬 변수
    ])
    multi_ranging_combo = (
        _ms_ranging_count >= 3                       # 3개 이상 동시 만족
        and last_rsi is not None and last_rsi <= 45.0
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -6.0
    )

    # --- Momentum Breakout Continuation (모멘텀 브레이크아웃 지속) ---
    # 강한 1봉 브레이크 후 2봉째 고점 갱신: 모멘텀 지속 확인
    momentum_breakout_cont = (
        len(closes) >= 3
        and closes[-1] > closes[-2]                   # 고점 갱신
        and closes[-2] > closes[-3]                   # 연속 상승
        and (closes[-2] - closes[-3]) / max(closes[-3], 1e-9) * 100 >= 0.8   # 첫봉 0.8% 이상
        and len(volumes) >= 3
        and volumes[-1] >= volumes[-2] * 0.7          # 거래량 유지 (70% 이상)
        and last_rsi is not None and 52.0 <= last_rsi <= 76.0
        and bool(trend.get("trend_entry_allowed", False))
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
    )

    # --- Range Scalp 종합 적격 판정 ---
    # 조건: 에어본 롱 신호 + RSI 과매도 구간 + 하락 구조 아님 + 낙폭 과다 아님
    # RANGING 시장에서 사용하는 평균회귀 전략
    rsi_oversold_range = last_rsi is not None and last_rsi <= 48.0
    rsi_not_crashed = last_rsi is not None and last_rsi >= 22.0  # 완전 붕괴는 제외
    slope_flat = abs(float(trend.get("trend_slope_pct", 0.0) or 0.0)) < 0.60
    range_scalp_eligible = (
        bool(airborne.get("airborne_long"))
        and rsi_oversold_range
        and rsi_not_crashed
        and slope_flat
        and trend_alignment not in {"downtrend", "late_extension"}
        and not choch_bearish
        and not bool(bk.get("rsi_bearish_divergence", False))
        and float(recent_change) > -5.0  # 급락 중 아님
    )

    # --- RSI Mean Reversion (레인징 보조 전략) ---
    rsi_mean_rev_long = (
        last_rsi is not None and last_rsi <= 35.0
        and trend_alignment not in {"downtrend"}
        and not choch_bearish
        and float(recent_change) > -4.0
    )

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
        "trend_follow_score": trend_score,
        "trend_alignment": trend_alignment,
        "trend_entry_allowed": bool(trend.get("trend_entry_allowed", False)),
        "trend_early_entry": trend_early_entry,
        "ema_stack_bullish": bool(trend.get("ema_stack_bullish", False)),
        "price_above_trend_ema": bool(trend.get("price_above_trend_ema", False)),
        "trend_slope_pct": float(trend.get("trend_slope_pct", 0.0) or 0.0),
        "trend_extension_pct": float(trend.get("trend_extension_pct", 0.0) or 0.0),
        "trend_reasons": list(trend.get("trend_reasons", [])),
        "ict_score": float(ict.get("ict_score", 0.0) or 0.0),
        "kill_zone_active": bool(ict.get("kill_zone_active")),
        "kill_zone_name": ict.get("kill_zone_name"),
        "ssl_sweep_confirmed": bool(ict.get("ssl_sweep_confirmed")),
        "bsl_sweep_confirmed": bool(ict.get("bsl_sweep_confirmed")),
        "choch_bullish": choch_bullish,
        "choch_bearish": choch_bearish,
        "bos_bullish": bos_bullish,
        "bos_bearish": bool(ict.get("bos_bearish")),
        "price_at_bull_ob": bool(ict.get("price_at_bull_ob")),
        "price_in_bull_fvg": bool(ict.get("price_in_bull_fvg")),
        "ict_bullish_count": int(ict.get("ict_bullish_count", 0) or 0),
        "ict_structure": str(ict.get("ict_structure", "undecided")),
        # Pullback entry detection (Ross Cameron / Holy Grail style)
        "pullback_detected": bool(pullback.get("pullback_detected", False)),
        "pullback_score": float(pullback.get("pullback_score", 0.0) or 0.0),
        "spike_pct_15m": float(pullback.get("spike_pct", 0.0) or 0.0),
        "retrace_from_high_pct": float(pullback.get("retrace_from_high_pct", 0.0) or 0.0),
        "vol_contracted_on_pullback": bool(pullback.get("vol_contracted_on_pullback", False)),
        # 에어본(Airborne) 이격도 신호 (MG 단타 / 평균회귀)
        "airborne_long": bool(airborne.get("airborne_long", False)),
        "airborne_short": bool(airborne.get("airborne_short", False)),
        "airborne_deviation_pct": float(airborne.get("deviation_pct", 0.0) or 0.0),
        "airborne_deviation_sigma": float(airborne.get("deviation_sigma", 0.0) or 0.0),
        "airborne_score": float(airborne.get("airborne_score", 0.0) or 0.0),
        "airborne_ema": float(airborne.get("airborne_ema", 0.0) or 0.0),
        "bb_pct_b": float(airborne.get("bb_pct_b", 0.5) or 0.5),
        "at_bb_lower": bool(airborne.get("at_bb_lower", False)),
        "at_bb_upper": bool(airborne.get("at_bb_upper", False)),
        "airborne_reasons": list(airborne.get("airborne_reasons", [])),
        # Range scalp 적격 판정 (RANGING 시장 진입용)
        "range_scalp_eligible": range_scalp_eligible,
        "rsi_mean_rev_long": rsi_mean_rev_long,
        # RANGING 보조 전략 신호
        "bb_squeeze_bounce": bb_squeeze_bounce,
        "vwap_deviation_long": vwap_deviation_long,
        "vwap_deviation_pct": vwap_deviation_pct,
        "rsi_extreme_long": rsi_extreme_long,
        # 추가 RANGING 신호 (Fix 4)
        "stoch_k": stoch_k_val,
        "stoch_oversold_cross": stoch_oversold_cross,
        "macd_hist": macd_hist_val,
        "macd_histogram_reversal": macd_histogram_reversal,
        "hammer_candle": hammer_candle,
        "doji_candle": doji_candle,
        "volume_climax_reversal": volume_climax_reversal,
        "volume_climax_ratio": volume_climax_ratio,
        "support_reclaim_long": support_reclaim_long,
        "support_level": round(_support_level, 8),
        "range_breakout_long": range_breakout_long,
        "range_high_20": round(_range_high_20, 8),
        "range_width_pct": range_width_pct,
        "high_tight_flag_long": high_tight_flag_long,
        "impulse_12_pct": impulse_12_pct,
        "flag_range_pct": flag_range_pct,
        # 추가 RANGING 신호 (Batch 2)
        "williams_r": wr_val,
        "williams_r_oversold": williams_r_oversold,
        "cci": cci_val,
        "cci_oversold_bounce": cci_oversold_bounce,
        "keltner_lower_touch": keltner_lower_touch,
        "kc_lower": round(_kc_lower_val, 8),
        "mfi": mfi_val,
        "mfi_oversold": mfi_oversold,
        # 추가 TRENDING 신호 (Batch 2)
        "ema_cross_long": ema_cross_long,
        "vwap_cross_long": vwap_cross_long,
        # 추가 TRENDING 신호 (Batch 3)
        "rsi_flip_long": rsi_flip_long,
        "macd_bull_cross": macd_bull_cross,
        "triple_candle_bull": triple_candle_bull,
        # 추가 TRENDING/DUAL 신호 (Batch 4)
        "supertrend_long": supertrend_long,
        "engulfing_bull": engulfing_bull,
        "vol_surge_long": vol_surge_long,
        "adx_trend_strong": adx_trend_strong,
        "adx_val": round(adx_val, 1),
        "di_plus": round(di_plus_val, 1),
        "di_minus": round(di_minus_val, 1),
        "bb_squeeze_breakout": bb_squeeze_breakout,
        "bb_sq_width": round(bb_sq_width_curr * 100, 3),
        "consecutive_higher_lows": consecutive_higher_lows,
        # 추가 신호 (Batch 5)
        "pin_bar_long": pin_bar_long,
        "morning_star": morning_star,
        "inside_bar_breakout": inside_bar_breakout,
        "rsi_momentum_keep": rsi_momentum_keep,
        "oi_momentum_long": oi_momentum_long,
        "demand_zone_bounce": demand_zone_bounce,
        # 추가 신호 (Batch 6)
        "vwap_rsi_combo": vwap_rsi_combo,
        "breakout_vol_confirm": breakout_vol_confirm,
        "hammer_at_support": hammer_at_support,
        "trend_reversal_early": trend_reversal_early,
        "ema_bounce_long": ema_bounce_long,
        "rsi_bullish_div": rsi_bullish_div,
        "multi_ranging_combo": multi_ranging_combo,
        "momentum_breakout_cont": momentum_breakout_cont,
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
        "micro_exhausted": exhausted,
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


def summarize_pullback_uptrend_signal(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Pullback-to-MA entry using daily candles.
    Pattern: price above MA20 > MA40 (uptrend) + pulled back near MA20 + RSI 28-58 + volume declining.
    Requires >= 42 candles (uses MA40, not MA60 to fit within standard 42-day window).
    """
    _empty: dict[str, Any] = {
        "pullback_ma_detected": False,
        "pullback_ma_score": 0.0,
        "ma20": None,
        "ma40": None,
        "pct_from_ma20": 0.0,
        "pct_from_ma40": 0.0,
        "pullback_rsi": None,
        "vol_declining": False,
    }
    closes = [float(c["close"]) for c in candles if c.get("close")]
    volumes = [float(c.get("volume") or 0.0) for c in candles]
    if len(closes) < 42:
        return _empty

    ma20_series = sma(closes, 20)
    ma40_series = sma(closes, 40)
    ma20 = ma20_series[-1]
    ma40 = ma40_series[-1]
    if ma20 is None or ma40 is None:
        return _empty

    last_close = closes[-1]
    pct_from_ma20 = (last_close - ma20) / ma20 * 100
    pct_from_ma40 = (last_close - ma40) / ma40 * 100

    # Uptrend: MA20 > MA40 (MA 정렬로 추세 확인 — 가격은 MA20 근처에 있으므로 제외)
    is_uptrend = ma20 > ma40
    # Pulled back to within -3% to +1.5% of MA20
    near_ma20 = -3.0 <= pct_from_ma20 <= 1.5

    last_rsi_val = rsi(closes, 14)
    rsi_ok = last_rsi_val is not None and 28 <= last_rsi_val <= 58

    # Volume declining on pullback: last 3 days < avg of prior 7 days
    vol_declining = False
    if len(volumes) >= 10:
        recent_vol = sum(volumes[-3:]) / 3
        prior_vol = sum(volumes[-10:-3]) / 7
        vol_declining = prior_vol > 0 and recent_vol < prior_vol * 0.85

    pullback_ma_detected = is_uptrend and near_ma20 and rsi_ok

    score = 0.0
    if pullback_ma_detected:
        score = 0.50
        if vol_declining:
            score += 0.20
        if -1.5 <= pct_from_ma20 <= 0.5:
            score += 0.15
        if pct_from_ma40 > 5.0:
            score += 0.15

    return {
        "pullback_ma_detected": pullback_ma_detected,
        "pullback_ma_score": round(min(score, 1.0), 3),
        "ma20": round(ma20, 2),
        "ma40": round(ma40, 2),
        "pct_from_ma20": round(pct_from_ma20, 2),
        "pct_from_ma40": round(pct_from_ma40, 2),
        "pullback_rsi": round(last_rsi_val, 1) if last_rsi_val is not None else None,
        "vol_declining": vol_declining,
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

"""
변동성 돌파 + 추세 추종 전략 (15분봉 기준) — 신호 강도 점수 포함.

진입 조건 (모두 충족해야 함):
  1. 현재 종가 > 직전 캔들 시가 + K × 직전 캔들 고저 범위  (변동성 돌파)
  2. EMA10 > EMA30                                          (15분봉 상승 추세)

신호 강도 점수 (0~3점, 높을수록 우선 진입):
  +1 — RSI < 30  (과매도 구간에서 반등 돌파)
  +1 — 현재 캔들 거래량 > 직전 20캔들 평균의 2배  (거래량 급증)
  +1 — 직전 캔들 EMA10 <= EMA30, 현재 캔들 EMA10 > EMA30  (골든크로스)

청산 조건 (position_manager / main 에서 처리):
  1. 현재가 <= ATR × 1.5 손절가
  2. 현재가 <= 고점 - ATR × 3.0  (트레일링 스탑)
  3. 보유 캔들 >= 24개  (6시간 시간 초과)
"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """EMA / ATR / RSI / 변동성 돌파 목표가를 계산합니다."""
    df = df.copy()

    # ── EMA ───────────────────────────────────────────────────────────────
    df["ema_short"] = df["close"].ewm(span=config.EMA_SHORT, adjust=False).mean()
    df["ema_long"]  = df["close"].ewm(span=config.EMA_LONG,  adjust=False).mean()

    # ── ATR ───────────────────────────────────────────────────────────────
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"]  - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.ewm(span=config.ATR_PERIOD, adjust=False).mean()

    # ── RSI ───────────────────────────────────────────────────────────────
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=config.RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(span=config.RSI_PERIOD, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ── 변동성 돌파 목표가 ─────────────────────────────────────────────────
    prev_range   = df["high"].shift(1) - df["low"].shift(1)
    df["target"] = df["open"] + config.K * prev_range

    return df


def _signal_score(df: pd.DataFrame) -> tuple[int, list[str]]:
    """
    신호 강도 점수와 점수 구성 이유를 반환합니다.

    Returns:
        (score: int, reasons: list[str])
    """
    score   = 0
    reasons = []
    last    = df.iloc[-1]

    # +1: RSI 과매도
    if not pd.isna(last.get("rsi")) and last["rsi"] < config.RSI_OVERSOLD:
        score += 1
        reasons.append(f"RSI={last['rsi']:.1f}")

    # +1: 거래량 급증 (직전 20캔들 평균의 VOLUME_SURGE_MULT 배 초과)
    if len(df) >= 21:
        vol_ma = df["volume"].iloc[-21:-1].mean()
        if vol_ma > 0 and float(last["volume"]) > config.VOLUME_SURGE_MULT * vol_ma:
            score += 1
            reasons.append(f"거래량급증×{float(last['volume'])/vol_ma:.1f}")

    # +1: 골든크로스 (직전 캔들 데드, 현재 캔들 골든)
    if len(df) >= 2:
        prev = df.iloc[-2]
        if (prev["ema_short"] <= prev["ema_long"] and
                last["ema_short"] > last["ema_long"]):
            score += 1
            reasons.append("골든크로스")

    return score, reasons


def check_entry_signal(df: pd.DataFrame) -> dict | None:
    """
    가장 최근 완성된 15분봉으로 매수 신호를 확인하고 신호 강도 점수를 포함해 반환합니다.

    Returns:
        신호 있음 → {
            "entry_price", "stop_loss", "atr",
            "candle_time", "score", "score_reasons"
        }
        신호 없음 → None
    """
    df = compute_indicators(df)

    if len(df) < config.EMA_LONG + 5:
        return None

    last = df.iloc[-1]

    if any(pd.isna(last[c]) for c in ["target", "ema_short", "ema_long", "atr"]):
        return None

    breakout = bool(last["close"] > last["target"])
    uptrend  = bool(last["ema_short"] > last["ema_long"])

    if not (breakout and uptrend):
        return None

    entry_price   = float(last["target"])
    atr           = float(last["atr"])
    score, reasons = _signal_score(df)

    return {
        "entry_price":   entry_price,
        "stop_loss":     entry_price - config.ATR_STOP_MULT * atr,
        "atr":           atr,
        "candle_time":   str(last["date"]),
        "score":         score,
        "score_reasons": reasons,
    }


def compute_trailing_stop(peak_price: float, atr: float) -> float:
    """트레일링 스탑 가격을 계산합니다 (15분봉 ATR 기준)."""
    return peak_price - config.ATR_TRAIL_MULT * atr


def effective_stop(position: dict, atr: float) -> float:
    """현재 적용되는 손절가 (초기 손절 vs 트레일링 스탑 중 높은 값)."""
    trail = compute_trailing_stop(position["peak_price"], atr)
    return max(position["stop_loss"], trail)

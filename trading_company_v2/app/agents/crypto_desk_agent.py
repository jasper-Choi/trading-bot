from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_upbit_15m_candles, get_upbit_4h_candles


def _ema(values: list[float], period: int) -> float:
    """지수이동평균 (EMA)."""
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI — Wilder 평활법."""
    if len(closes) < period + 2:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def _check_eth4h_breakout() -> dict:
    """ETH 4H 신고점 돌파 신호 감지 — Strategy D 백테스트 검증 (Sharpe 2.33, WR 61.1%).

    진입 조건:
      1. BTC 4H close > EMA200  (상승장 레짐 필터)
      2. ETH 4H close > max(prev 20봉 최고가)  (신고점 돌파)
      3. ETH 4H vol >= 2.5x × 20봉 평균 거래량  (거래량 급등)
      4. RSI(14) 50–70  (과열 아닌 모멘텀)
      5. ETH 4H close > EMA20  (추세 방향 확인)

    Returns dict with keys: eth_4h_breakout, btc_4h_regime_bull,
      eth_4h_vol_ratio, eth_4h_rsi, eth_4h_breakout_level.
    """
    result: dict = {
        "eth_4h_breakout": False,
        "btc_4h_regime_bull": False,
        "eth_4h_vol_ratio": 0.0,
        "eth_4h_rsi": 0.0,
        "eth_4h_breakout_level": 0.0,
    }
    try:
        # ── 1. BTC 4H EMA200 레짐 ──────────────────────────────────────────
        btc_4h = get_upbit_4h_candles("KRW-BTC", count=210)
        if len(btc_4h) < 200:
            return result
        btc_closes = [float(c.get("close") or 0.0) for c in btc_4h]
        btc_ema200 = _ema(btc_closes, 200)
        btc_bull = btc_closes[-1] > btc_ema200 > 0.0
        result["btc_4h_regime_bull"] = btc_bull
        if not btc_bull:
            return result

        # ── 2-5. ETH 4H 신호 ──────────────────────────────────────────────
        eth_4h = get_upbit_4h_candles("KRW-ETH", count=25)
        if len(eth_4h) < 22:
            return result

        closes = [float(c.get("close") or 0.0) for c in eth_4h]
        highs = [float(c.get("high") or 0.0) for c in eth_4h]
        volumes = [float(c.get("volume") or 0.0) for c in eth_4h]

        # 신고점 돌파: 현재 봉 close > 이전 20봉 최고가
        prev_high_max = max(highs[-21:-1]) if len(highs) >= 21 else 0.0
        if closes[-1] <= prev_high_max or prev_high_max <= 0.0:
            return result
        result["eth_4h_breakout_level"] = round(prev_high_max, 0)

        # 거래량 급등: 현재 vol >= 2.5x × 이전 20봉 평균
        prev_vols = volumes[-21:-1]
        vol_ma = sum(prev_vols) / len(prev_vols) if prev_vols else 0.0
        if vol_ma <= 0.0:
            return result
        vol_ratio = volumes[-1] / vol_ma
        result["eth_4h_vol_ratio"] = round(vol_ratio, 2)
        if vol_ratio < 2.5:
            return result

        # RSI(14) 50–70
        rsi_val = _rsi(closes, 14)
        if rsi_val is None:
            return result
        result["eth_4h_rsi"] = rsi_val
        if not (50.0 <= rsi_val <= 70.0):
            return result

        # EMA20 상향 돌파 확인
        ema20 = _ema(closes, 20)
        if closes[-1] <= ema20 or ema20 <= 0.0:
            return result

        result["eth_4h_breakout"] = True
    except Exception:
        pass
    return result


class CryptoDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("crypto_desk_agent")

    def run(self) -> AgentResult:
        # ── BTC 방향성 (15m 기준 간단 체크) ────────────────────────────────
        btc_bias = "balanced"
        btc_change_30m = 0.0
        try:
            direction_candles = get_upbit_15m_candles("KRW-BTC", count=20)
            if len(direction_candles) >= 3:
                p_now = float(direction_candles[-1].get("close") or 0)
                p_30m = float(direction_candles[-3].get("close") or 0)
                if p_30m > 0:
                    btc_change_30m = round((p_now - p_30m) / p_30m * 100, 2)
                if btc_change_30m >= 0.5:
                    btc_bias = "bullish"
                elif btc_change_30m <= -0.5:
                    btc_bias = "bearish"
        except Exception:
            pass

        # ── Strategy D: ETH 4H 신고점 돌파 ─────────────────────────────────
        eth4h = _check_eth4h_breakout()

        return AgentResult(
            name=self.name,
            score=0.7 if eth4h["eth_4h_breakout"] else 0.4,
            reason=(
                f"ETH 4H breakout={'✓' if eth4h['eth_4h_breakout'] else '✗'} "
                f"BTC bias={btc_bias} change30m={btc_change_30m:+.2f}%"
            ),
            payload={
                "desk_bias": btc_bias,
                "btc_change_30m": btc_change_30m,
                "lead_market": "KRW-ETH" if eth4h["eth_4h_breakout"] else "KRW-BTC",
                "candidate_symbols": [],
                "candidate_markets": [],
                "reasons": [
                    f"BTC 30min change={btc_change_30m:+.2f}%",
                    f"ETH 4H breakout={'confirmed ✓' if eth4h['eth_4h_breakout'] else 'not triggered'}",
                ],
                # Strategy D fields
                **eth4h,
            },
        )

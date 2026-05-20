from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.market_gateway import get_upbit_15m_candles, get_upbit_4h_candles, get_upbit_daily_candles, get_us_market_context


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

        # 거래량 급등: 현재 vol >= 2.0x × 이전 20봉 평균 (2.5x → 2.0x 완화 2026-05-20)
        prev_vols = volumes[-21:-1]
        vol_ma = sum(prev_vols) / len(prev_vols) if prev_vols else 0.0
        if vol_ma <= 0.0:
            return result
        vol_ratio = volumes[-1] / vol_ma
        result["eth_4h_vol_ratio"] = round(vol_ratio, 2)
        if vol_ratio < 2.0:
            return result

        # RSI(14) 45–75 (50-70 → 45-75 완화 2026-05-20)
        rsi_val = _rsi(closes, 14)
        if rsi_val is None:
            return result
        result["eth_4h_rsi"] = rsi_val
        if not (45.0 <= rsi_val <= 75.0):  # 50-70 → 45-75 완화
            return result

        # EMA20 상향 돌파 확인
        ema20 = _ema(closes, 20)
        if closes[-1] <= ema20 or ema20 <= 0.0:
            return result

        result["eth_4h_breakout"] = True
    except Exception:
        pass
    return result


def _std(values: list[float], period: int) -> list[float]:
    """Rolling standard deviation (population)."""
    result = [0.0] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((v - mean) ** 2 for v in window) / period
        result[i] = variance ** 0.5
    return result


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI — Wilder 평활법 (2-period 용도로도 사용)."""
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
    return round(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 1)


def _sma(values: list[float], period: int) -> float:
    """단순이동평균."""
    if len(values) < period:
        return 0.0
    return sum(values[-period:]) / period


def _check_rsi2_mean_reversion_crypto() -> dict:
    """Strategy S9 / S13: RSI(2) 평균회귀 — 백테스트 검증 (2026-05-20).

    S9  (rsi2_long):      RSI(2) < 10 + close < EMA20×0.975 + close > EMA200
    S13 (dual_rsi_long):  S9 조건 전체 + RSI(14) < 40 (중기 과매도 이중 확인)

    S13 백테스트: Crypto Sharpe 7.28, WR 51.2%, P/L 3.20, MDD -8.7% ✅
                  Korea  Sharpe 6.36, WR 58.6%, P/L 2.00, MDD -8.0% ✅
    S9  백테스트: Crypto Sharpe 3.06, WR 48.1%, P/L 1.64, MDD -6.2%
    """
    result: dict = {
        "rsi2_long": False,
        "rsi2_symbol": "",
        "rsi2_value": 0.0,
        "rsi2_ema20": 0.0,
        "rsi2_deviation_pct": 0.0,
        "dual_rsi_long": False,   # S13: RSI(2)<10 AND RSI(14)<40
        "dual_rsi_rsi14": 0.0,
    }
    # KRW-XRP 추가 (2026-05-20): 스캔 유니버스 확장으로 신호 빈도 개선
    for market in ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"):
        try:
            daily = get_upbit_daily_candles(market, count=220)
            if len(daily) < 205:
                continue
            closes = [float(c.get("close") or 0.0) for c in daily]
            if not closes or closes[-1] <= 0:
                continue
            ema200 = _ema(closes, 200)
            if closes[-1] <= ema200 or ema200 <= 0:
                continue
            ema20 = _ema(closes, 20)
            if ema20 <= 0:
                continue
            rsi2_val = _rsi(closes, 2)
            if rsi2_val is None:
                continue
            deviation_pct = round((closes[-1] - ema20) / ema20 * 100, 2)
            if rsi2_val < 10.0 and closes[-1] < ema20 * 0.975:
                # S9 조건 충족 — S13도 충족하는지 추가 확인
                rsi14_val = _rsi(closes, 14)
                dual = (rsi14_val is not None and rsi14_val < 40.0)
                result["rsi2_long"] = True
                result["rsi2_symbol"] = market
                result["rsi2_value"] = rsi2_val
                result["rsi2_ema20"] = round(ema20, 0)
                result["rsi2_deviation_pct"] = deviation_pct
                result["dual_rsi_long"] = dual
                result["dual_rsi_rsi14"] = round(rsi14_val or 0.0, 1)
                return result
        except Exception:
            continue
    return result


def _check_nday_pullback_crypto() -> dict:
    """Strategy S10: N-Day Consecutive Pullback 평균회귀 — 백테스트 검증 (2026-05-20).

    Daily 캔들 기준:
      1. close > EMA200 (상승장 레짐 필터)
      2. 3일 연속 하락 마감
      3. close < EMA5 (단기 하락 추세 확인)

    백테스트 결과:
      Crypto: Sharpe 4.78, WR 55.8%, P/L 1.73, MDD -7.7%
      Stocks: Sharpe 4.52, WR 54.1%, P/L 1.69, MDD -12.1%
    """
    result: dict = {
        "nday_long": False,
        "nday_symbol": "",
        "nday_consec_down": 0,
        "nday_ema5": 0.0,
        "nday_deviation_pct": 0.0,
    }
    for market in ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"):  # XRP 추가
        try:
            daily = get_upbit_daily_candles(market, count=220)
            if len(daily) < 210:
                continue
            closes = [float(c.get("close") or 0.0) for c in daily]
            if not closes or closes[-1] <= 0:
                continue
            ema200 = _ema(closes, 200)
            if closes[-1] <= ema200 or ema200 <= 0:
                continue
            ema5 = _ema(closes[-10:], 5)
            if ema5 <= 0:
                continue
            # 3일 연속 하락
            consec = sum(
                1 for i in range(-3, 0)
                if closes[i] < closes[i - 1]
            )
            deviation_pct = round((closes[-1] - ema5) / ema5 * 100, 2)
            if consec >= 3 and closes[-1] < ema5:
                result["nday_long"] = True
                result["nday_symbol"] = market
                result["nday_consec_down"] = consec
                result["nday_ema5"] = round(ema5, 0)
                result["nday_deviation_pct"] = deviation_pct
                return result
        except Exception:
            continue
    return result


def _check_mongtata_airborne_crypto() -> dict:
    """MONGTATA 에어본 (평균회귀) 신호 — Strategy S2 백테스트 검증.

    Daily 캔들 기준:
      1. price > EMA200  (상승장 레짐 필터)
      2. close < lower Bollinger Band (EMA20 - 2σ)
      3. close < EMA20 × 0.975  (EMA20 대비 2.5% 이상 하락)

    백테스트 결과 (Upbit daily, 2022-2026):
      Crypto: Sharpe 6.66, WR 50.0%, PR 2.94, MDD -9.7%
      Stocks: Sharpe 8.60, WR 56.5%, PR 2.64, MDD -5.9%

    Returns dict with keys: mongtata_long, mongtata_symbol,
      mongtata_ema20, mongtata_lower_bb, mongtata_deviation_pct.
    """
    result: dict = {
        "mongtata_long": False,
        "mongtata_symbol": "",
        "mongtata_ema20": 0.0,
        "mongtata_lower_bb": 0.0,
        "mongtata_deviation_pct": 0.0,
    }
    # BTC → ETH → SOL 순서로 신호 탐색 (첫 번째 신호만 반환)
    for market in ("KRW-BTC", "KRW-ETH", "KRW-SOL"):
        try:
            daily = get_upbit_daily_candles(market, count=220)
            if len(daily) < 205:
                continue
            closes = [float(c.get("close") or 0.0) for c in daily]
            if not closes or closes[-1] <= 0:
                continue

            ema200 = _ema(closes, 200)
            # 레짐 필터: price > EMA200
            if closes[-1] <= ema200 or ema200 <= 0:
                continue

            ema20 = _ema(closes, 20)
            std20 = _std(closes, 20)
            if ema20 <= 0 or std20[-1] <= 0:
                continue

            lower_bb = ema20 - 2.0 * std20[-1]
            deviation_pct = round((closes[-1] - ema20) / ema20 * 100, 2)

            # 진입 조건
            if closes[-1] < lower_bb and closes[-1] < ema20 * 0.975:
                result["mongtata_long"] = True
                result["mongtata_symbol"] = market
                result["mongtata_ema20"] = round(ema20, 0)
                result["mongtata_lower_bb"] = round(lower_bb, 0)
                result["mongtata_deviation_pct"] = deviation_pct
                return result
        except Exception:
            continue
    return result


def _check_bear_oversold_bounce_crypto() -> dict:
    """Strategy S17: Bear Market Oversold Bounce — 백테스트 검증 (2026-05-20).

    Daily 캔들 기준 (EMA200 아래 하락장 전용):
      1. close < EMA200 × 0.97    -- EMA200 아래 3% 이상
      2. RSI(2) < 5               -- 극단 단기 과매도
      3. RSI(14) < 25             -- 중기 과매도 확인
      4. close < EMA20 × 0.975   -- 단기 추세 하락 확인

    백테스트 결과 (2022-2026, fee 0.10%):
      Crypto: Sharpe 10.60, WR 60%, P/L 3.63, MDD -8.9%, n=15 ✅

    파라미터: TP=+4%, SL=-0.8%, HOLD=5일, Size=0.50x (하락장 경감)
    """
    result: dict = {
        "bear_oversold_long": False,
        "bear_oversold_symbol": "",
        "bear_oversold_rsi2": 0.0,
        "bear_oversold_rsi14": 0.0,
        "bear_oversold_ema200_gap_pct": 0.0,
    }
    for market in ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"):
        try:
            daily = get_upbit_daily_candles(market, count=220)
            if len(daily) < 215:
                continue
            closes = [float(c.get("close") or 0.0) for c in daily]
            if not closes or closes[-1] <= 0:
                continue

            ema200 = _ema(closes, 200)
            if ema200 <= 0:
                continue

            # 1. EMA200 아래 3% 이상
            if closes[-1] >= ema200 * 0.97:
                continue

            ema20 = _ema(closes, 20)
            if ema20 <= 0:
                continue

            # 4. close < EMA20 × 0.975
            if closes[-1] >= ema20 * 0.975:
                continue

            # 2. RSI(2) < 5
            rsi2_val = _rsi(closes, 2)
            if rsi2_val is None or rsi2_val >= 5.0:
                continue

            # 3. RSI(14) < 25
            rsi14_val = _rsi(closes, 14)
            if rsi14_val is None or rsi14_val >= 25.0:
                continue

            gap_pct = round((ema200 - closes[-1]) / ema200 * 100, 2)
            result["bear_oversold_long"]          = True
            result["bear_oversold_symbol"]         = market
            result["bear_oversold_rsi2"]           = rsi2_val
            result["bear_oversold_rsi14"]          = rsi14_val
            result["bear_oversold_ema200_gap_pct"] = gap_pct
            return result
        except Exception:
            continue
    return result


def _check_momentum_breakout_crypto() -> dict:
    """Strategy S15: Crypto Momentum Breakout — 백테스트 검증 (2026-05-20).

    Daily 캔들 기준:
      1. close = N일(10일) 신고가 (이전 10봉 close 최고값 돌파)
      2. EMA50 > EMA200 (골든크로스 추세 필터)
      3. close > EMA20  (단기 추세 방향 확인)
      4. volume >= 1.5x 20일 평균거래량 (돌파 거래량 확인)

    백테스트 결과 (2022-2026, fee 0.10%):
      Crypto: Sharpe 11.27, WR 66.7%, P/L 2.32, MDD -5.1%, n=51 ✅

    파라미터: LB=10, VOL=1.5x, SL=-2%, TP=+7%, HOLD=15일
    """
    result: dict = {
        "momentum_breakout_long": False,
        "momentum_breakout_symbol": "",
        "momentum_breakout_vol_ratio": 0.0,
        "momentum_breakout_ema_trend": False,   # EMA50 > EMA200
        "momentum_breakout_high10": 0.0,        # 이전 10봉 최고가
    }
    for market in ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"):
        try:
            daily = get_upbit_daily_candles(market, count=220)
            if len(daily) < 215:
                continue
            closes  = [float(c.get("close") or 0.0) for c in daily]
            volumes = [float(c.get("volume") or 0.0) for c in daily]
            if not closes or closes[-1] <= 0:
                continue

            # EMA200 (추세 레짐) / EMA50 / EMA20
            ema200 = _ema(closes, 200)
            ema50  = _ema(closes[-55:], 50)  # 최근 55봉으로 EMA50 계산 (충분한 warm-up)
            ema20  = _ema(closes, 20)
            if ema200 <= 0 or ema50 <= 0 or ema20 <= 0:
                continue

            # EMA50 > EMA200 (골든크로스 추세 필터)
            if ema50 <= ema200:
                continue

            # close > EMA20 (단기 추세 방향)
            if closes[-1] <= ema20:
                continue

            # N-day 신고가: 현재 close > 이전 10봉 close 최고값
            prev_10_closes = closes[-11:-1]
            high10 = max(prev_10_closes) if prev_10_closes else 0.0
            if high10 <= 0 or closes[-1] <= high10:
                continue

            # 거래량 급등: 현재 vol >= 1.5x × 이전 20봉 평균
            prev_vols = volumes[-21:-1]
            vol_ma = sum(prev_vols) / len(prev_vols) if prev_vols else 0.0
            if vol_ma <= 0:
                continue
            vol_ratio = volumes[-1] / vol_ma
            if vol_ratio < 1.5:
                continue

            result["momentum_breakout_long"]      = True
            result["momentum_breakout_symbol"]     = market
            result["momentum_breakout_vol_ratio"]  = round(vol_ratio, 2)
            result["momentum_breakout_ema_trend"]  = True
            result["momentum_breakout_high10"]     = round(high10, 0)
            return result
        except Exception:
            continue
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

        # ── Strategy S15: Momentum Breakout (신규 추세 전략, 2026-05-20) ─────
        momentum = _check_momentum_breakout_crypto()

        # ── Strategy S2: MONGTATA 에어본 (평균회귀) ──────────────────────────
        mongtata = _check_mongtata_airborne_crypto()

        # ── Strategy S9: RSI(2) Connors 평균회귀 ────────────────────────────
        rsi2 = _check_rsi2_mean_reversion_crypto()

        # ── Strategy S10: N-Day Consecutive Pullback ─────────────────────────
        nday = _check_nday_pullback_crypto()

        # ── Strategy S17: Bear Market Oversold Bounce (하락장 전용, 2026-05-20) ─
        bear_oversold = _check_bear_oversold_bounce_crypto()

        # ── 미국 시장 컨텍스트 (15분 캐시 — 재조회 비용 없음) ────────────────
        us_ctx: dict = {}
        try:
            us_ctx = get_us_market_context()
        except Exception:
            pass

        # 우선순위: D > S15 > S2 > S9/S13 > S10 > S17(하락장)
        _lead = (
            "KRW-ETH" if eth4h["eth_4h_breakout"]
            else momentum["momentum_breakout_symbol"] if momentum["momentum_breakout_long"]
            else mongtata["mongtata_symbol"] if mongtata["mongtata_long"]
            else rsi2["rsi2_symbol"] if rsi2["rsi2_long"]
            else nday["nday_symbol"] if nday["nday_long"]
            else bear_oversold["bear_oversold_symbol"] if bear_oversold["bear_oversold_long"]
            else "KRW-BTC"
        )
        _score = (
            0.75 if eth4h["eth_4h_breakout"]
            else 0.78 if momentum["momentum_breakout_long"]  # S15: highest Sharpe
            else 0.65 if mongtata["mongtata_long"]
            else 0.62 if rsi2["rsi2_long"]
            else 0.60 if nday["nday_long"]
            else 0.68 if bear_oversold["bear_oversold_long"]  # S17: 하락장 특화
            else 0.4
        )

        return AgentResult(
            name=self.name,
            score=_score,
            reason=(
                f"ETH4H={'✓' if eth4h['eth_4h_breakout'] else '✗'} "
                f"S15={'✓ ' + momentum['momentum_breakout_symbol'] if momentum['momentum_breakout_long'] else '✗'} "
                f"MONGTATA={'✓ ' + mongtata['mongtata_symbol'] if mongtata['mongtata_long'] else '✗'} "
                f"RSI2={'✓ ' + rsi2['rsi2_symbol'] if rsi2['rsi2_long'] else '✗'} "
                f"NDAY={'✓ ' + nday['nday_symbol'] if nday['nday_long'] else '✗'} "
                f"S17={'✓ ' + bear_oversold['bear_oversold_symbol'] if bear_oversold['bear_oversold_long'] else '✗'} "
                f"BTC={btc_bias}"
            ),
            payload={
                "desk_bias": btc_bias,
                "btc_change_30m": btc_change_30m,
                "lead_market": _lead,
                "candidate_symbols": [],
                "candidate_markets": [],
                "reasons": [
                    f"BTC 30min change={btc_change_30m:+.2f}%",
                    f"ETH 4H breakout={'confirmed' if eth4h['eth_4h_breakout'] else 'not triggered'}",
                    f"S15 momentum={'confirmed ' + momentum['momentum_breakout_symbol'] if momentum['momentum_breakout_long'] else 'not triggered'}",
                    f"MONGTATA={'confirmed ' + mongtata['mongtata_symbol'] if mongtata['mongtata_long'] else 'not triggered'}",
                    f"RSI2={'confirmed ' + rsi2['rsi2_symbol'] if rsi2['rsi2_long'] else 'not triggered'}",
                    f"NDayPullback={'confirmed ' + nday['nday_symbol'] if nday['nday_long'] else 'not triggered'}",
                    f"S17 bear_oversold={'confirmed ' + bear_oversold['bear_oversold_symbol'] if bear_oversold['bear_oversold_long'] else 'not triggered'}",
                ],
                # Strategy D fields
                **eth4h,
                # Strategy S15 fields
                **momentum,
                # Strategy S2 fields
                **mongtata,
                # Strategy S9 fields
                **rsi2,
                # Strategy S10 fields
                **nday,
                # Strategy S17 fields
                **bear_oversold,
                # ── 미국 시장 컨텍스트 (다른 에이전트/엔진에서 참조) ──
                "us_regime":   us_ctx.get("us_regime", "unknown"),
                "vix":         us_ctx.get("vix", 0.0),
                "vix_regime":  us_ctx.get("vix_regime", "unknown"),
                "spy_chg":     us_ctx.get("spy_chg", 0.0),
                "qqq_chg":     us_ctx.get("qqq_chg", 0.0),
                "us_summary":  us_ctx.get("summary", ""),
                "us_sectors":  us_ctx.get("sectors", {}),
                "sector_leader":  us_ctx.get("sector_leader", ""),
                "sector_laggard": us_ctx.get("sector_laggard", ""),
            },
        )

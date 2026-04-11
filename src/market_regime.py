"""
시장 국면 감지 — 멀티 타임프레임 (1분봉 / 15분봉) 기반.

국면:
  BULL     — 상승장: MAX 10개, 리스크 2.5%, 피라미딩 허용
  NEUTRAL  — 중립:   MAX  5개, 리스크 1.5%
  BEAR     — 하락장: MAX  2개, 리스크 0.5%
  VOLATILE — 완전방어: 신규진입 0, 트레일링 타이트

타임프레임 역할:
  1분봉  — 긴급 시장 감지 (BTC 급락/급등)
  5분봉  — 진입/청산 신호 (main 루프에서 사용)
  15분봉 — 전체 추세 방향 (EMA + ADX 기반)
"""

import threading
import requests
import pandas as pd
from datetime import datetime
from typing import Optional

# ── 국면 상수 ─────────────────────────────────────────────────────────────────
BULL     = "BULL"
NEUTRAL  = "NEUTRAL"
BEAR     = "BEAR"
VOLATILE = "VOLATILE"

# 국면별 설정
REGIME_CONFIG: dict[str, dict] = {
    BULL:     {"max_positions": 10, "risk_pct": 2.5, "pyramiding": True},
    NEUTRAL:  {"max_positions": 5,  "risk_pct": 1.5, "pyramiding": False},
    BEAR:     {"max_positions": 2,  "risk_pct": 0.5, "pyramiding": False},
    VOLATILE: {"max_positions": 0,  "risk_pct": 0.0, "pyramiding": False},
}

BTC_MARKET        = "KRW-BTC"
CANDLES_BASE_URL  = "https://api.upbit.com/v1/candles/minutes/{unit}"


def _fetch_btc_candles(unit: int, count: int = 30) -> pd.DataFrame:
    """BTC 캔들 데이터 수집."""
    url    = CANDLES_BASE_URL.format(unit=unit)
    params = {"market": BTC_MARKET, "count": count}
    resp   = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data   = resp.json()
    if not data:
        raise ValueError("빈 응답")
    df = pd.DataFrame(data).rename(columns={
        "candle_date_time_kst": "date",
        "opening_price":        "open",
        "high_price":           "high",
        "low_price":            "low",
        "trade_price":          "close",
        "candle_acc_trade_volume": "volume",
    })
    df["date"] = pd.to_datetime(df["date"])
    return (
        df[["date", "open", "high", "low", "close", "volume"]]
        .sort_values("date")
        .reset_index(drop=True)
    )


class MarketRegimeDetector:
    """시장 국면 감지 싱글턴."""

    def __init__(self):
        self._lock   = threading.Lock()
        self._regime = NEUTRAL
        self._last_changed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._regime_history: list[dict] = []

    # ── 공개 프로퍼티 ─────────────────────────────────────────────────────────

    @property
    def regime(self) -> str:
        with self._lock:
            return self._regime

    @property
    def last_changed(self) -> str:
        with self._lock:
            return self._last_changed

    def get_config(self) -> dict:
        with self._lock:
            return dict(REGIME_CONFIG[self._regime])

    def set_regime(self, new_regime: str, reason: str = "") -> bool:
        """국면을 전환합니다. 실제 변경이 있으면 True 반환."""
        with self._lock:
            if self._regime == new_regime:
                return False
            old = self._regime
            self._regime        = new_regime
            self._last_changed  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._regime_history.append({
                "time":   self._last_changed,
                "from":   old,
                "to":     new_regime,
                "reason": reason,
            })
            return True

    # ── 1분봉: 긴급 감지 ──────────────────────────────────────────────────────

    def check_1m(self) -> Optional[str]:
        """
        BTC 1분봉으로 긴급 시장 감지.
        ・3캔들 연속 음봉 + 거래량 급증 → VOLATILE
        ・1분봉 -2% 급락              → VOLATILE
        ・1분봉 +2% 급등 (VOLATILE/BEAR 중) → NEUTRAL 회복
        반환: 변경된 국면 or None
        """
        try:
            df = _fetch_btc_candles(1, count=25)
        except Exception:
            return None

        if len(df) < 5:
            return None

        last  = df.iloc[-1]
        prev3 = df.iloc[-4:-1]

        # 3캔들 연속 음봉 + 거래량 급증
        all_bear = all(row["close"] < row["open"] for _, row in prev3.iterrows())
        vol_base = df["volume"].iloc[:-1].mean()
        vol_surge = vol_base > 0 and float(last["volume"]) > vol_base * 2.0

        if all_bear and vol_surge:
            changed = self.set_regime(VOLATILE, "3연속음봉+거래량급증")
            return VOLATILE if changed else None

        # 1분봉 -2% 급락
        open_p  = float(last["open"])
        close_p = float(last["close"])
        if open_p > 0:
            pct_chg = (close_p - open_p) / open_p * 100
            if pct_chg <= -2.0:
                changed = self.set_regime(VOLATILE, f"1분봉급락{pct_chg:.1f}%")
                return VOLATILE if changed else None

            # 1분봉 +2% 급등 → VOLATILE/BEAR에서 NEUTRAL 회복
            if pct_chg >= 2.0:
                with self._lock:
                    cur = self._regime
                if cur in (VOLATILE, BEAR):
                    changed = self.set_regime(NEUTRAL, f"1분봉급등{pct_chg:.1f}%")
                    return NEUTRAL if changed else None

        return None

    # ── 15분봉: 전체 추세 판단 ───────────────────────────────────────────────

    def check_15m(self) -> Optional[str]:
        """
        BTC 15분봉 EMA10/30 + ADX14 기반 국면 판단.
        VOLATILE 상태이면 덮어쓰지 않음.
        반환: 변경된 국면 or None
        """
        with self._lock:
            if self._regime == VOLATILE:
                return None

        try:
            df = _fetch_btc_candles(15, count=60)
        except Exception:
            return None

        if len(df) < 35:
            return None

        df["ema10"] = df["close"].ewm(span=10, adjust=False).mean()
        df["ema30"] = df["close"].ewm(span=30, adjust=False).mean()

        # ADX(14)
        prev_c  = df["close"].shift(1)
        tr      = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"]  - prev_c).abs(),
        ], axis=1).max(axis=1)
        atr14   = tr.ewm(span=14, adjust=False).mean()

        up_mv   = df["high"].diff()
        dn_mv   = -(df["low"].diff())
        plus_dm = up_mv.where((up_mv > dn_mv) & (up_mv > 0), 0.0)
        minus_dm= dn_mv.where((dn_mv > up_mv) & (dn_mv > 0), 0.0)

        plus_di = 100 * plus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, 1e-10)
        minus_di= 100 * minus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, 1e-10)
        dx      = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
        adx     = dx.ewm(span=14, adjust=False).mean()

        last        = df.iloc[-1]
        ema_bull    = float(last["ema10"]) > float(last["ema30"])
        adx_strong  = float(adx.iloc[-1]) >= 25

        if ema_bull and adx_strong:
            new_regime = BULL
        elif not ema_bull and adx_strong:
            new_regime = BEAR
        else:
            new_regime = NEUTRAL

        changed = self.set_regime(new_regime, "15분봉EMA+ADX")
        return new_regime if changed else None


# ── 모듈 수준 싱글턴 ─────────────────────────────────────────────────────────
market_regime = MarketRegimeDetector()

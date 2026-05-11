"""train_ml_strategy.py — MLStrategy LightGBM 학습 스크립트

실행 방법:
    cd trading_company_v2
    pip install lightgbm numpy requests
    python scripts/train_ml_strategy.py

결과:
    models/lgbm_model.pkl  ← inference 시 자동 로드됨

피처 (10개, best3_strategies_for_crypto.md):
    rsi_14, macd_hist_norm, bb_pos, atr_ratio, vol_ratio_20,
    mom_5d, mom_20d, mom_60d, foreign_5d=0, institution_5d=0

레이블: HORIZON=3일 후 수익 > 0 → 1 (코인 버전)

모델 파라미터:
    n_estimators=200, learning_rate=0.05, max_depth=5
"""

from __future__ import annotations

import os
import sys
import math
import time
import pickle
import random
import requests

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

MODEL_PATH = os.path.join(_BASE, "models", "lgbm_model.pkl")
os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)

HORIZON     = 3
MIN_BARS    = 65   # 60일 피처 + 여유
MIN_SAMPLES = 500

SYMBOLS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",
    "KRW-DOGE", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-MATIC",
    "KRW-ATOM", "KRW-UNI", "KRW-LTC", "KRW-BCH", "KRW-ETC",
    "KRW-TRX", "KRW-XLM", "KRW-EOS", "KRW-NEAR", "KRW-APT",
    "KRW-OP", "KRW-ARB", "KRW-SUI", "KRW-SEI", "KRW-INJ",
    "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-IMX", "KRW-FTM",
]

UPBIT_URL = "https://api.upbit.com/v1/candles/days"


# ── 데이터 수집 ────────────────────────────────────────────────────────────────
def fetch_ohlcv(symbol: str, count: int = 200) -> list[dict]:
    try:
        resp = requests.get(
            UPBIT_URL,
            params={"market": symbol, "count": min(count, 200)},
            timeout=8,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return list(reversed(data)) if isinstance(data, list) else []
    except Exception as e:
        print(f"  [{symbol}] 수집 실패: {e}")
        return []


# ── EMA ───────────────────────────────────────────────────────────────────────
def _ema(values: list[float], span: int) -> list[float]:
    k = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al < 1e-10:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 4)


# ── 피처 계산 ──────────────────────────────────────────────────────────────────
def compute_features(bars: list[dict], i: int) -> list[float] | None:
    """bars[0:i+1] 기준으로 i 시점의 10개 피처 계산."""
    if i < MIN_BARS - 1:
        return None
    window = bars[: i + 1]
    closes  = [float(b["trade_price"])             for b in window]
    opens   = [float(b["opening_price"])           for b in window]
    highs   = [float(b["high_price"])              for b in window]
    lows    = [float(b["low_price"])               for b in window]
    volumes = [float(b["candle_acc_trade_volume"]) for b in window]
    n = len(closes)

    # rsi_14
    rsi_14 = _rsi(closes, 14)

    # macd_hist 정규화
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    macd_sig  = _ema(macd_line, 9)
    macd_hist = (macd_line[-1] - macd_sig[-1]) / closes[-1] if closes[-1] > 0 else 0.0

    # bb_pos
    bb_win = closes[-20:]
    bb_mid = sum(bb_win) / 20
    bb_var = sum((c - bb_mid) ** 2 for c in bb_win) / 20
    bb_std = math.sqrt(bb_var) if bb_var > 0 else 1e-8
    bb_range = 4 * bb_std
    bb_pos = (closes[-1] - bb_mid) / (bb_range / 2) if bb_range > 1e-10 else 0.0
    bb_pos = max(-1.0, min(1.0, bb_pos))

    # atr_ratio
    trs = [max(highs[j] - lows[j], abs(highs[j] - closes[j-1]), abs(lows[j] - closes[j-1]))
           for j in range(max(1, n-14), n)]
    atr = sum(trs) / len(trs) if trs else 0.0
    atr_ratio = atr / closes[-1] if closes[-1] > 0 else 0.0

    # vol_ratio_20
    vol_avg20 = sum(volumes[-20:]) / 20 if n >= 20 else sum(volumes) / len(volumes)
    vol_ratio_20 = volumes[-1] / vol_avg20 if vol_avg20 > 0 else 1.0

    def mom(n_days: int) -> float:
        if n > n_days:
            base = closes[-(n_days + 1)]
            return (closes[-1] - base) / base if base > 0 else 0.0
        return 0.0

    return [
        rsi_14,
        macd_hist,
        bb_pos,
        atr_ratio,
        vol_ratio_20,
        mom(5),
        mom(20),
        mom(60),
        0.0,  # foreign_5d
        0.0,  # institution_5d
    ]


# ── 샘플 생성 ──────────────────────────────────────────────────────────────────
def build_samples(bars: list[dict]) -> tuple[list, list]:
    X, y = [], []
    for i in range(MIN_BARS - 1, len(bars) - HORIZON):
        feat = compute_features(bars, i)
        if feat is None:
            continue
        future = float(bars[i + HORIZON]["trade_price"])
        current = float(bars[i]["trade_price"])
        label = 1 if future > current else 0
        X.append(feat)
        y.append(label)
    return X, y


# ── 학습 ──────────────────────────────────────────────────────────────────────
def train():
    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError:
        print("❌ lightgbm/numpy 없음. pip install lightgbm numpy 실행 후 재시도.")
        sys.exit(1)

    print("=== MLStrategy LightGBM 학습 시작 ===")

    X_all, y_all = [], []
    for sym in SYMBOLS:
        bars = fetch_ohlcv(sym, count=200)
        if len(bars) < MIN_BARS + HORIZON:
            print(f"  [{sym}] 데이터 부족 ({len(bars)}봉) — 스킵")
            continue
        X, y = build_samples(bars)
        X_all.extend(X)
        y_all.extend(y)
        print(f"  [{sym}] {len(X)}개 샘플 추가 (총 {len(X_all)})")
        time.sleep(0.12)

    if len(X_all) < MIN_SAMPLES:
        print(f"❌ 샘플 수 부족: {len(X_all)} < {MIN_SAMPLES}")
        sys.exit(1)

    X_np = np.array(X_all, dtype=np.float32)
    y_np = np.array(y_all, dtype=np.int32)

    print(f"\n총 {len(X_all)}개 샘플, 양성={y_np.sum()}, 음성={len(y_np)-y_np.sum()}")

    # 학습/검증 분리 (80:20)
    split = int(len(X_np) * 0.8)
    X_train, X_val = X_np[:split], X_np[split:]
    y_train, y_val = y_np[:split], y_np[split:]

    feature_names = [
        "rsi_14", "macd_hist", "bb_pos", "atr_ratio", "vol_ratio_20",
        "mom_5d", "mom_20d", "mom_60d", "foreign_5d", "institution_5d",
    ]

    # LightGBM 학습 (best3 파라미터)
    model = lgb.LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=5,
        num_leaves=31,
        random_state=42,
        n_jobs=1,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(50)],
        feature_name=feature_names,
    )

    # 검증 정확도
    val_preds = model.predict(X_val)
    acc = (val_preds == y_val).mean()
    print(f"\n✅ 학습 완료 — val_acc={acc*100:.1f}%")

    # 저장
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    print(f"   저장: {MODEL_PATH}")

    # 피처 중요도
    importance = sorted(
        zip(feature_names, model.feature_importances_),
        key=lambda x: -x[1],
    )
    print("\n피처 중요도:")
    for name, imp in importance:
        print(f"  {name:20s}: {imp}")


if __name__ == "__main__":
    train()

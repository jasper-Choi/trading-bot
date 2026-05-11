"""train_persistence_cnn.py — PersistenceCNN 학습 스크립트

실행 방법:
    cd trading_company_v2
    pip install torch numpy requests
    python scripts/train_persistence_cnn.py

결과:
    models/persistence_cnn.pt  ← inference 시 자동 로드됨

학습 파라미터 (best3_strategies_for_crypto.md):
    WIN=20, HORIZON=3 (코인 버전), N_FEAT=5, EPOCHS=30, BATCH=128, LR=1e-3

데이터:
    Upbit REST API 일봉 → 상위 거래대금 코인 30종
    각 심볼 최대 500일 데이터
"""

from __future__ import annotations

import os
import sys
import math
import time
import random
import requests

# ── 경로 설정 ────────────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

# ── 하이퍼파라미터 ──────────────────────────────────────────────────────────────
WIN        = 20
HORIZON    = 3       # 코인 버전: 5일 → 3일
N_FEAT     = 5
N_EPOCHS   = 30
BATCH_SIZE = 128
LR         = 1e-3
MIN_SAMPLES = 500    # 학습 최소 샘플 수

MODEL_PATH = os.path.join(_BASE, "models", "persistence_cnn.pt")
os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)

# 학습 대상 심볼 (Upbit 상위 거래대금)
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
def fetch_ohlcv(symbol: str, count: int = 500) -> list[dict]:
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


# ── 정규화 ─────────────────────────────────────────────────────────────────────
def make_sample(bars: list[dict], i: int) -> tuple[list, int] | None:
    """bars[i:i+WIN] → (features, label).

    features: (5, WIN) = [norm_open, norm_high, norm_low, norm_close, norm_volume]
    label: 1 if bars[i+WIN+HORIZON-1].close > bars[i+WIN-1].close else 0
    """
    if i + WIN + HORIZON > len(bars):
        return None

    window = bars[i : i + WIN]
    closes  = [float(b["trade_price"])             for b in window]
    opens   = [float(b["opening_price"])           for b in window]
    highs   = [float(b["high_price"])              for b in window]
    lows    = [float(b["low_price"])               for b in window]
    volumes = [float(b["candle_acc_trade_volume"]) for b in window]

    c_min, c_max = min(closes), max(closes)
    c_range = max(c_max - c_min, 1e-8)
    max_vol = max(max(volumes), 1e-8)

    norm_close  = [(c - c_min) / c_range for c in closes]
    norm_open   = [(o - c_min) / c_range for o in opens]
    norm_high   = [(h - c_min) / c_range for h in highs]
    norm_low    = [(l - c_min) / c_range for l in lows]
    norm_volume = [v / max_vol for v in volumes]

    # label: HORIZON일 후 수익 > 0 → 1
    future_close = float(bars[i + WIN + HORIZON - 1]["trade_price"])
    current_close = closes[-1]
    label = 1 if future_close > current_close else 0

    return [norm_open, norm_high, norm_low, norm_close, norm_volume], label


# ── 학습 ──────────────────────────────────────────────────────────────────────
def train():
    try:
        import torch
        import torch.nn as nn
        import torch.optim as optim
        torch.set_num_threads(1)
    except ImportError:
        print("❌ torch 없음. pip install torch 실행 후 재시도.")
        sys.exit(1)

    print("=== PersistenceCNN 학습 시작 ===")
    print(f"  WIN={WIN}, HORIZON={HORIZON}, EPOCHS={N_EPOCHS}")

    # 데이터 수집
    X_all, y_all = [], []
    for sym in SYMBOLS:
        bars = fetch_ohlcv(sym, count=200)
        if len(bars) < WIN + HORIZON + 10:
            print(f"  [{sym}] 데이터 부족 ({len(bars)}봉) — 스킵")
            continue
        count_added = 0
        for i in range(len(bars) - WIN - HORIZON):
            result = make_sample(bars, i)
            if result:
                feat, label = result
                X_all.append(feat)
                y_all.append(label)
                count_added += 1
        print(f"  [{sym}] {count_added}개 샘플 추가 (총 {len(X_all)})")
        time.sleep(0.12)  # Upbit rate limit

    if len(X_all) < MIN_SAMPLES:
        print(f"❌ 샘플 수 부족: {len(X_all)} < {MIN_SAMPLES}")
        sys.exit(1)

    print(f"\n총 {len(X_all)}개 샘플, 양성={sum(y_all)}, 음성={len(y_all)-sum(y_all)}")

    # 셔플
    combined = list(zip(X_all, y_all))
    random.shuffle(combined)
    X_all, y_all = zip(*combined)

    # 텐서 변환
    X_tensor = torch.tensor(list(X_all), dtype=torch.float32)  # (N, 5, WIN)
    y_tensor = torch.tensor(list(y_all), dtype=torch.float32)  # (N,)

    # 모델 정의
    class PersistenceCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1   = nn.Conv1d(N_FEAT, 16, kernel_size=3, padding=1)
            self.conv2   = nn.Conv1d(16, 32, kernel_size=3, padding=1)
            self.pool    = nn.AdaptiveAvgPool1d(1)
            self.fc      = nn.Linear(32, 1)
            self.relu    = nn.ReLU()
            self.sigmoid = nn.Sigmoid()

        def forward(self, x):
            x = self.relu(self.conv1(x))
            x = self.relu(self.conv2(x))
            x = self.pool(x).squeeze(-1)
            return self.sigmoid(self.fc(x)).squeeze(-1)

    model = PersistenceCNN()
    optimizer = optim.Adam(model.parameters(), lr=LR)
    criterion = nn.BCELoss()

    N = len(X_tensor)
    best_loss = float("inf")

    for epoch in range(1, N_EPOCHS + 1):
        # 미니배치 셔플
        idx = list(range(N))
        random.shuffle(idx)
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, N, BATCH_SIZE):
            batch_idx = idx[start : start + BATCH_SIZE]
            xb = X_tensor[batch_idx]
            yb = y_tensor[batch_idx]

            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        avg_loss = epoch_loss / max(n_batches, 1)
        if epoch % 5 == 0 or epoch == 1:
            print(f"  Epoch {epoch:02d}/{N_EPOCHS}  loss={avg_loss:.4f}")

        if avg_loss < best_loss:
            best_loss = avg_loss
            torch.save(model.state_dict(), MODEL_PATH)

    print(f"\n✅ 학습 완료 — best_loss={best_loss:.4f}")
    print(f"   저장: {MODEL_PATH}")

    # 간단한 정확도 체크
    model.eval()
    with torch.no_grad():
        preds = (model(X_tensor) >= 0.5).float()
        acc = (preds == y_tensor).float().mean().item()
    print(f"   전체 정확도: {acc*100:.1f}%")


if __name__ == "__main__":
    train()

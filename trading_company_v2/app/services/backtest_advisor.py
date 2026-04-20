from __future__ import annotations

import json
import math
import re
from pathlib import Path


_SEARCH_PATHS = [
    Path.home() / "Desktop" / "backtest" / "coin_result_v4.json",
    Path(__file__).parents[3] / "Desktop" / "backtest" / "coin_result_v4.json",
]

_NEUTRAL_WEIGHTS: dict[str, float] = {
    "KRW-BTC": 0.34,
    "KRW-ETH": 0.33,
    "KRW-XRP": 0.33,
}


def _score(stats: dict) -> float:
    sharpe = float(stats.get("샤프비율", 0) or 0)
    win_rate = float(stats.get("승률(%)", 0) or 0) / 100
    mdd = abs(float(stats.get("최대DD(%)", stats.get("최대드로우다운(%)", -10)) or -10))
    if math.isnan(sharpe):
        sharpe = 0.0
    sharpe_norm = max(0.0, min(sharpe / 3.0, 1.0))
    mdd_norm = max(0.0, 1.0 - mdd / 20.0)
    return sharpe_norm * 0.5 + win_rate * 0.3 + mdd_norm * 0.2


def get_crypto_weights() -> dict[str, float]:
    """
    Returns normalized allocation weights for KRW crypto symbols based on
    backtest Sharpe / win-rate / MDD. Symbols with negative Sharpe are excluded.
    Falls back to equal weights if the backtest file is not found.
    """
    data: dict | None = None
    for path in _SEARCH_PATHS:
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                text = re.sub(r"\bNaN\b", "null", text)
                data = json.loads(text)
                break
            except Exception:
                continue

    if data is None:
        return dict(_NEUTRAL_WEIGHTS)

    scores: dict[str, float] = {}
    for symbol, stats in data.items():
        sharpe = float(stats.get("샤프비율", 0) or 0)
        if math.isnan(sharpe) or sharpe < 0:
            continue
        scores[symbol] = _score(stats)

    if not scores:
        return dict(_NEUTRAL_WEIGHTS)

    total = sum(scores.values())
    return {
        symbol: round(score / total, 4)
        for symbol, score in sorted(scores.items(), key=lambda x: -x[1])
    }

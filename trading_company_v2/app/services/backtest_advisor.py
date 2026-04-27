from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


_SEARCH_PATHS = [
    Path.home() / "Desktop" / "backtest" / "coin_result_v5.json",
    Path.home() / "Desktop" / "backtest" / "coin_result_v4.json",
]

_NEUTRAL_WEIGHTS: dict[str, float] = {
    "KRW-BTC":  0.14,
    "KRW-ETH":  0.13,
    "KRW-XRP":  0.13,
    "KRW-SOL":  0.12,
    "KRW-DOGE": 0.12,
    "KRW-ADA":  0.10,
    "KRW-AVAX": 0.09,
    "KRW-TRX":  0.09,
    "KRW-LINK": 0.08,
}


def _load_results() -> dict[str, Any] | None:
    for path in _SEARCH_PATHS:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            text = re.sub(r"\bNaN\b", "null", text)
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _metric(stats: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = stats.get(key, default)
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        parsed = default
    if math.isnan(parsed):
        return default
    return parsed


def _passes(stats: dict[str, Any]) -> bool:
    return (
        _metric(stats, "승률(%)") >= 45.0
        and _metric(stats, "손익비") >= 2.0
        and _metric(stats, "샤프비율") >= 1.0
        and _metric(stats, "최대DD(%)", -999.0) >= -20.0
        and _metric(stats, "총수익률(%)") > 0.0
        and _metric(stats, "총거래수") >= 20.0
    )


def _score(stats: dict[str, Any]) -> float:
    win_rate = _metric(stats, "승률(%)") / 100.0
    rr = _metric(stats, "손익비")
    sharpe = _metric(stats, "샤프비율")
    total_return = _metric(stats, "총수익률(%)")
    max_dd = abs(_metric(stats, "최대DD(%)"))
    trade_count = _metric(stats, "총거래수")

    rr_norm = max(0.0, min(rr / 3.0, 1.0))
    sharpe_norm = max(0.0, min(sharpe / 6.0, 1.0))
    return_norm = max(0.0, min(total_return / 5.0, 1.0))
    drawdown_norm = max(0.0, 1.0 - (max_dd / 12.0))
    trade_norm = max(0.0, min(trade_count / 40.0, 1.0))

    return (
        (win_rate * 0.18)
        + (rr_norm * 0.26)
        + (sharpe_norm * 0.26)
        + (return_norm * 0.18)
        + (drawdown_norm * 0.07)
        + (trade_norm * 0.05)
    )


def get_crypto_weights() -> dict[str, float]:
    """
    Return normalized allocation weights for KRW crypto symbols based on the
    latest validated backtest results. Only symbols that pass the current
    swing-style criteria are eligible for live emphasis.
    """
    data = _load_results()
    if not data:
        return dict(_NEUTRAL_WEIGHTS)

    passing_scores: dict[str, float] = {}
    fallback_scores: dict[str, float] = {}

    for symbol, stats in data.items():
        if not isinstance(stats, dict):
            continue
        score = _score(stats)
        if _passes(stats):
            passing_scores[symbol] = score
        elif _metric(stats, "총수익률(%)") > 0 and _metric(stats, "샤프비율") > 1.0:
            fallback_scores[symbol] = score

    source = passing_scores or fallback_scores
    if not source:
        return dict(_NEUTRAL_WEIGHTS)

    total = sum(source.values())
    if total <= 0:
        return dict(_NEUTRAL_WEIGHTS)

    return {
        symbol: round(score / total, 4)
        for symbol, score in sorted(source.items(), key=lambda item: item[1], reverse=True)
    }

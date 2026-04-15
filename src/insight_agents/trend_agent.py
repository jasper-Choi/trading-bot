import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.data_fetcher import fetch_15m_candles
from src.strategy import compute_indicators, check_entry_signal
from src.screener import get_top_krw_coins
from .base_agent import BaseAgent

class TrendAgent(BaseAgent):
    def __init__(self, top_n: int = 5):
        super().__init__("TrendAgent")
        self.top_n = top_n

    def run(self) -> dict:
        coins = get_top_krw_coins(self.top_n)
        scores = []
        reasons = []

        for coin in coins:
            try:
                df = fetch_15m_candles(coin)
                if df is None or len(df) < 30:
                    continue
                df = compute_indicators(df)
                signal = check_entry_signal(df)

                coin_score = 0.5
                if signal.get("entry"):
                    coin_score += 0.2
                if signal.get("score", 0) >= 2:
                    coin_score += 0.15
                if signal.get("score", 0) >= 3:
                    coin_score += 0.1
                coin_score = min(1.0, coin_score)

                scores.append(coin_score)
                reasons.append(f"{coin}:{coin_score:.2f}")
            except Exception as e:
                continue

        if not scores:
            return {"score": 0.5, "reason": "no trend data available", "raw": {}}

        avg = round(sum(scores) / len(scores), 4)
        return {
            "score": avg,
            "reason": f"avg trend score across {len(scores)} coins",
            "raw": {"coin_scores": reasons},
        }
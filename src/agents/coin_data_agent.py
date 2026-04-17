from __future__ import annotations

from src.data_fetcher import fetch_15m_candles
from src.screener import get_top_krw_coins

import config

from .base import TradingAgent, utcnow_iso
from .state import CACHE_DIR, write_json_artifact


COIN_CACHE_FILE = CACHE_DIR / "coin_data.json"


class CoinDataAgent(TradingAgent):
    """Caches 15m OHLCV data for the top KRW-traded coins."""

    def __init__(self, top_n: int | None = None, candle_count: int | None = None):
        super().__init__(name="coin_data_agent")
        self.top_n = top_n or config.TOP_COINS_COUNT
        self.candle_count = candle_count or config.CANDLE_COUNT

    def run(self) -> dict:
        markets = get_top_krw_coins(self.top_n)
        candles: dict[str, list[dict]] = {}
        failed: list[str] = []

        for market in markets:
            try:
                df = fetch_15m_candles(market, count=self.candle_count)
                candles[market] = [
                    {
                        "date": row["date"].isoformat(),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
                    for _, row in df.iterrows()
                ]
            except Exception:
                failed.append(market)

        payload = {
            "updated_at": utcnow_iso(),
            "market_count": len(markets),
            "cached_count": len(candles),
            "failed_markets": failed,
            "markets": markets,
            "candles": candles,
        }
        write_json_artifact(COIN_CACHE_FILE, payload)

        return {
            "score": 1.0 if markets and not failed else 0.7 if candles else 0.4,
            "reason": f"cached {len(candles)}/{len(markets)} coin datasets",
            "raw": {
                "cache_file": str(COIN_CACHE_FILE),
                "market_count": len(markets),
                "cached_count": len(candles),
                "failed_markets": failed,
            },
        }

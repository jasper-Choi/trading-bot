from __future__ import annotations

import config
from src.stock_screener import get_gap_up_stocks, get_kosdaq_realtime

from .base import TradingAgent, utcnow_iso
from .state import CACHE_DIR, write_json_artifact


STOCK_CACHE_FILE = CACHE_DIR / "stock_data.json"


class StockDataAgent(TradingAgent):
    """Caches KOSDAQ movers and gap-up candidates for later signal generation."""

    def __init__(self, top_n: int | None = None):
        super().__init__(name="stock_data_agent")
        self.top_n = top_n or config.STOCK_TOP_N

    def run(self) -> dict:
        universe = get_kosdaq_realtime(self.top_n)
        gap_up = get_gap_up_stocks(force=True)

        payload = {
            "updated_at": utcnow_iso(),
            "top_n": self.top_n,
            "universe_count": len(universe),
            "gap_up_count": len(gap_up),
            "universe": universe,
            "gap_up_candidates": gap_up,
        }
        write_json_artifact(STOCK_CACHE_FILE, payload)

        return {
            "score": 1.0 if universe else 0.4,
            "reason": f"cached {len(universe)} stocks and {len(gap_up)} gap-up candidates",
            "raw": {
                "cache_file": str(STOCK_CACHE_FILE),
                "universe_count": len(universe),
                "gap_up_count": len(gap_up),
            },
        }

from __future__ import annotations

from .base import TradingAgent, utcnow_iso
from .state import CACHE_DIR, SIGNALS_DIR, load_json_artifact, load_state, write_json_artifact


STOCK_CACHE_FILE = CACHE_DIR / "stock_data.json"
STOCK_SIGNAL_FILE = SIGNALS_DIR / "stock_signals.json"


class StockSignalAgent(TradingAgent):
    """Builds momentum-ranked stock signals from cached KOSDAQ candidates."""

    def __init__(self):
        super().__init__(name="stock_signal_agent")

    def run(self) -> dict:
        state = load_state()
        insight_score = float(state.get("strategy", {}).get("insight_score") or 0.5)
        cache = load_json_artifact(
            STOCK_CACHE_FILE,
            default={"gap_up_candidates": [], "universe": []},
        )

        signals: list[dict] = []
        for stock in cache.get("gap_up_candidates", []):
            gap_pct = float(stock.get("gap_pct", 0))
            volume = float(stock.get("volume", 0) or 0)
            momentum_score = min(1.0, gap_pct / 10.0)
            volume_score = min(1.0, volume / 1_000_000)
            confidence = round(momentum_score * 0.5 + volume_score * 0.2 + insight_score * 0.3, 4)

            signals.append(
                {
                    "ticker": stock.get("ticker"),
                    "name": stock.get("name"),
                    "current_price": stock.get("current_price"),
                    "gap_pct": gap_pct,
                    "volume": volume,
                    "confidence": min(1.0, confidence),
                    "reasons": [
                        f"gap_up={gap_pct:.2f}%",
                        f"volume={int(volume)}",
                        f"insight_score={insight_score:.2f}",
                    ],
                }
            )

        signals.sort(key=lambda item: item["confidence"], reverse=True)
        payload = {
            "updated_at": utcnow_iso(),
            "insight_score": insight_score,
            "signal_count": len(signals),
            "signals": signals,
        }
        write_json_artifact(STOCK_SIGNAL_FILE, payload)

        return {
            "score": 1.0 if signals else 0.5,
            "reason": f"generated {len(signals)} stock signals",
            "raw": {
                "signal_file": str(STOCK_SIGNAL_FILE),
                "signal_count": len(signals),
            },
        }

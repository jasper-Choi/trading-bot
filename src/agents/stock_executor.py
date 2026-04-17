from __future__ import annotations

from contextlib import contextmanager

from src import stock_strategy

from .base import TradingAgent
from .state import load_json_artifact, load_state
from .stock_signal_agent import STOCK_SIGNAL_FILE


@contextmanager
def _scaled_stock_capital(scale: float):
    original = stock_strategy.STOCK_TOTAL_CAPITAL
    try:
        stock_strategy.STOCK_TOTAL_CAPITAL = max(1.0, original * scale)
        yield
    finally:
        stock_strategy.STOCK_TOTAL_CAPITAL = original


class StockExecutor(TradingAgent):
    """Executes paper stock entries from approved signal output."""

    def __init__(self):
        super().__init__(name="stock_executor")

    def run(self) -> dict:
        state = load_state()
        risk = state.get("risk", {})
        if not risk.get("allow_new_entries", True):
            return {
                "score": 0.2,
                "reason": "stock entries blocked by risk agent",
                "raw": {"executed": 0, "blocked": True},
            }

        signal_payload = load_json_artifact(STOCK_SIGNAL_FILE, default={"signals": []})
        existing = {pos.get("ticker") for pos in stock_strategy.get_stock_positions()}
        executed: list[str] = []

        for signal in signal_payload.get("signals", [])[:3]:
            ticker = signal.get("ticker")
            if not ticker or ticker in existing:
                continue

            scale = min(self._signal_scale(float(signal.get("confidence", 0.0))), float(risk.get("position_scale", 1.0)))
            with _scaled_stock_capital(scale):
                result = stock_strategy.open_stock_position(
                    ticker=ticker,
                    name=signal.get("name") or ticker,
                    entry_price=float(signal.get("current_price") or 0),
                    reason=f"agent_signal:{signal.get('confidence', 0):.2f}",
                )
            if result:
                executed.append(ticker)
                existing.add(ticker)

        return {
            "score": 1.0 if executed else 0.5,
            "reason": f"executed {len(executed)} stock entries",
            "raw": {"executed": executed, "blocked": False},
        }

    @staticmethod
    def _signal_scale(confidence: float) -> float:
        if confidence >= 0.6:
            return 1.0
        if confidence >= 0.4:
            return 0.7
        return 0.5

from __future__ import annotations

from contextlib import contextmanager

import config
from src.position_manager import can_open_new_position, get_position, open_position

from .base import TradingAgent
from .coin_signal_agent import COIN_SIGNAL_FILE
from .state import load_json_artifact, load_state


@contextmanager
def _scaled_coin_capital(scale: float):
    original = config.INITIAL_CAPITAL_PER_COIN
    try:
        config.INITIAL_CAPITAL_PER_COIN = max(1.0, original * scale)
        yield
    finally:
        config.INITIAL_CAPITAL_PER_COIN = original


class CoinExecutor(TradingAgent):
    """Executes paper coin entries from approved signal output."""

    def __init__(self):
        super().__init__(name="coin_executor")

    def run(self) -> dict:
        state = load_state()
        risk = state.get("risk", {})
        if not risk.get("allow_new_entries", True):
            return {
                "score": 0.2,
                "reason": "coin entries blocked by risk agent",
                "raw": {"executed": 0, "blocked": True},
            }

        signal_payload = load_json_artifact(COIN_SIGNAL_FILE, default={"signals": []})
        executed: list[str] = []

        for signal in signal_payload.get("signals", []):
            market = signal.get("market")
            if not market or get_position(market):
                continue

            can_open, reason = can_open_new_position()
            if not can_open:
                break

            scale = min(self._signal_scale(float(signal.get("confidence", 0.0))), float(risk.get("position_scale", 1.0)))
            with _scaled_coin_capital(scale):
                open_position(
                    coin=market,
                    entry_price=float(signal["entry_price"]),
                    stop_loss=float(signal["stop_loss"]),
                    atr=float(signal["atr"]),
                )
            executed.append(market)

        return {
            "score": 1.0 if executed else 0.5,
            "reason": f"executed {len(executed)} coin entries",
            "raw": {"executed": executed, "blocked": False},
        }

    @staticmethod
    def _signal_scale(confidence: float) -> float:
        if confidence >= 0.6:
            return 1.0
        if confidence >= 0.4:
            return 0.7
        return 0.5

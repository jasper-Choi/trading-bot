from __future__ import annotations

from copy import deepcopy

import config

from .base import TradingAgent, utcnow_iso
from .state import load_state, merge_state


BASELINE_PARAMS = {
    "K": config.K,
    "ATR_STOP_MULT": config.ATR_STOP_MULT,
    "ATR_TRAIL_MULT": config.ATR_TRAIL_MULT,
    "RSI_PERIOD": config.RSI_PERIOD,
    "RSI_OVERSOLD": config.RSI_OVERSOLD,
    "STOCK_GAP_MIN": config.STOCK_GAP_MIN,
    "STOCK_TOP_N": config.STOCK_TOP_N,
}


class StrategyAgent(TradingAgent):
    """Applies strategy direction and insight score to runtime parameters."""

    def __init__(self):
        super().__init__(name="strategy_agent")

    def run(self) -> dict:
        state = load_state()
        strategy = state.get("strategy", {})
        insight_score = float(strategy.get("insight_score") or 0.5)
        direction = strategy.get("direction") or "NEUTRAL"

        params = self._derive_parameters(insight_score=insight_score, direction=direction)
        self._apply_runtime_parameters(params)

        merge_state(
            {
                "parameters": {
                    "applied_at": utcnow_iso(),
                    "coin": {
                        "K": params["K"],
                        "ATR_STOP_MULT": params["ATR_STOP_MULT"],
                        "ATR_TRAIL_MULT": params["ATR_TRAIL_MULT"],
                        "RSI_PERIOD": params["RSI_PERIOD"],
                        "RSI_OVERSOLD": params["RSI_OVERSOLD"],
                    },
                    "stock": {
                        "gap_min_pct": params["STOCK_GAP_MIN"],
                        "top_n": params["STOCK_TOP_N"],
                    },
                },
            }
        )

        return {
            "score": insight_score,
            "reason": f"applied {direction.lower()} parameters with K={params['K']:.2f}",
            "raw": {
                "direction": direction,
                "insight_score": insight_score,
                "parameters": params,
            },
        }

    @staticmethod
    def _derive_parameters(insight_score: float, direction: str) -> dict:
        params = deepcopy(BASELINE_PARAMS)

        if insight_score >= 0.6:
            params["K"] = 0.5
            params["ATR_STOP_MULT"] = 1.3
            params["ATR_TRAIL_MULT"] = 2.6
            params["STOCK_GAP_MIN"] = max(1.2, BASELINE_PARAMS["STOCK_GAP_MIN"] - 0.2)
        elif insight_score <= 0.3:
            params["K"] = 0.7
            params["ATR_STOP_MULT"] = 1.8
            params["ATR_TRAIL_MULT"] = 3.4
            params["STOCK_GAP_MIN"] = BASELINE_PARAMS["STOCK_GAP_MIN"] + 0.4

        if direction == "AGGRESSIVE":
            params["RSI_OVERSOLD"] = min(35, BASELINE_PARAMS["RSI_OVERSOLD"] + 2)
            params["STOCK_TOP_N"] = max(BASELINE_PARAMS["STOCK_TOP_N"], 60)
        elif direction == "DEFENSIVE":
            params["RSI_OVERSOLD"] = max(25, BASELINE_PARAMS["RSI_OVERSOLD"] - 2)
            params["STOCK_TOP_N"] = min(BASELINE_PARAMS["STOCK_TOP_N"], 30)

        return params

    @staticmethod
    def _apply_runtime_parameters(params: dict) -> None:
        config.K = params["K"]
        config.ATR_STOP_MULT = params["ATR_STOP_MULT"]
        config.ATR_TRAIL_MULT = params["ATR_TRAIL_MULT"]
        config.RSI_PERIOD = params["RSI_PERIOD"]
        config.RSI_OVERSOLD = params["RSI_OVERSOLD"]
        config.STOCK_GAP_MIN = params["STOCK_GAP_MIN"]
        config.STOCK_TOP_N = params["STOCK_TOP_N"]

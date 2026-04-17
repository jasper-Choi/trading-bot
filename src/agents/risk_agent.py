from __future__ import annotations

import config
from src.market_regime import VOLATILE, market_regime
from src.position_manager import get_daily_pnl, get_open_positions, load_history
from src.stock_strategy import get_stock_positions

from .base import TradingAgent, utcnow_iso
from .state import load_state, merge_state


class RiskAgent(TradingAgent):
    """Evaluates drawdown and determines whether new entries are allowed."""

    def __init__(self):
        super().__init__(name="risk_agent")

    def run(self) -> dict:
        state = load_state()
        insight_score = float(state.get("strategy", {}).get("insight_score") or 0.5)
        regime = market_regime.regime
        daily_pnl = float(get_daily_pnl())
        coin_positions = get_open_positions()
        stock_positions = get_stock_positions()
        drawdown_pct = self._estimate_drawdown_pct(load_history())

        warnings: list[str] = []
        allow_new_entries = True
        position_scale = self._base_position_scale(insight_score)

        total_capital = config.INITIAL_CAPITAL_PER_COIN * max(config.MAX_POSITIONS, 1)
        daily_loss_limit_value = total_capital * config.DAILY_LOSS_LIMIT_PCT
        if daily_pnl <= -daily_loss_limit_value:
            allow_new_entries = False
            warnings.append("daily loss limit reached")

        if insight_score < 0.2:
            allow_new_entries = False
            warnings.append("insight_score below 0.2")

        if regime == VOLATILE:
            position_scale = min(position_scale, 0.5)
            warnings.append("volatile regime: reduce position size")

        if drawdown_pct >= config.DAILY_LOSS_LIMIT_PCT * 100:
            position_scale = min(position_scale, 0.5)
            warnings.append(f"drawdown elevated at {drawdown_pct:.2f}%")

        merge_state(
            {
                "risk": {
                    "allow_new_entries": allow_new_entries,
                    "daily_loss_limit_pct": config.DAILY_LOSS_LIMIT_PCT,
                    "current_daily_pnl": daily_pnl,
                    "drawdown_pct": drawdown_pct,
                    "position_scale": position_scale,
                    "warnings": warnings,
                    "last_checked_at": utcnow_iso(),
                }
            }
        )

        open_count = len(coin_positions) + len(stock_positions)
        return {
            "score": 1.0 if allow_new_entries else 0.3,
            "reason": f"risk check complete for {open_count} open positions",
            "raw": {
                "allow_new_entries": allow_new_entries,
                "position_scale": position_scale,
                "drawdown_pct": drawdown_pct,
                "warnings": warnings,
                "coin_open_positions": len(coin_positions),
                "stock_open_positions": len(stock_positions),
            },
        }

    @staticmethod
    def _base_position_scale(insight_score: float) -> float:
        if insight_score >= 0.6:
            return 1.0
        if insight_score < 0.4:
            return 0.5
        return 0.7

    @staticmethod
    def _estimate_drawdown_pct(history: list[dict]) -> float:
        if not history:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0

        for trade in history:
            cumulative += float(trade.get("pnl", 0) or 0)
            peak = max(peak, cumulative)
            drawdown = peak - cumulative
            max_drawdown = max(max_drawdown, drawdown)

        total_capital = config.INITIAL_CAPITAL_PER_COIN * max(config.MAX_POSITIONS, 1)
        if total_capital <= 0:
            return 0.0
        return round((max_drawdown / total_capital) * 100, 4)

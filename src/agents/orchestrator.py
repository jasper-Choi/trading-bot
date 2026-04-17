from __future__ import annotations

from typing import Any

from src.reporter import log

from .ceo_agent import CEOAgent
from .coin_data_agent import CoinDataAgent
from .coin_executor import CoinExecutor
from .coin_signal_agent import CoinSignalAgent
from .notifier import TelegramNotifier
from .risk_agent import RiskAgent
from .stock_data_agent import StockDataAgent
from .stock_executor import StockExecutor
from .stock_signal_agent import StockSignalAgent
from .strategy_agent import StrategyAgent


def run_agent_cycle(log_fn=log) -> dict[str, Any]:
    """Runs the multi-agent trading pipeline once and returns a summary."""
    summary: dict[str, Any] = {
        "ok": True,
        "steps": [],
        "coin_executed": [],
        "stock_executed": [],
        "warnings": [],
    }

    notifier = TelegramNotifier()
    pipeline = [
        ("ceo", CEOAgent()),
        ("strategy", StrategyAgent()),
        ("coin_data", CoinDataAgent()),
        ("stock_data", StockDataAgent()),
        ("coin_signal", CoinSignalAgent()),
        ("stock_signal", StockSignalAgent()),
        ("risk", RiskAgent()),
        ("coin_executor", CoinExecutor()),
        ("stock_executor", StockExecutor()),
    ]

    for name, agent in pipeline:
        try:
            result = agent.safe_run()
            summary["steps"].append({"name": name, "reason": result.get("reason"), "score": result.get("score")})
            log_fn(f"[Agents] {name}: {result.get('reason')}")

            if name == "risk":
                summary["warnings"] = result.get("raw", {}).get("warnings", [])
            elif name == "coin_executor":
                summary["coin_executed"] = result.get("raw", {}).get("executed", [])
            elif name == "stock_executor":
                summary["stock_executed"] = result.get("raw", {}).get("executed", [])
        except Exception as exc:
            summary["ok"] = False
            summary["error"] = f"{name}: {exc}"
            log_fn(f"[Agents] {name} error: {exc}")
            notifier.send_error(summary["error"])
            break

    if summary["ok"]:
        notifier.safe_run()
        notifier.send_execution_summary(
            executed_coin=summary["coin_executed"],
            executed_stock=summary["stock_executed"],
            warnings=summary["warnings"],
        )
    return summary

from __future__ import annotations

import os
from typing import Iterable

import requests

from .base import TradingAgent


class TelegramNotifier(TradingAgent):
    """Best-effort Telegram notifier for important agent events."""

    def __init__(self):
        super().__init__(name="notifier")
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    def run(self) -> dict:
        configured = bool(self.token and self.chat_id)
        return {
            "score": 1.0 if configured else 0.5,
            "reason": "telegram configured" if configured else "telegram not configured",
            "raw": {"configured": configured},
        }

    def send_message(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception:
            return False

    def send_execution_summary(self, executed_coin: Iterable[str], executed_stock: Iterable[str], warnings: Iterable[str]) -> bool:
        lines = ["[Agent Cycle]"]
        coin_list = list(executed_coin)
        stock_list = list(executed_stock)
        warning_list = list(warnings)

        lines.append(f"coin entries: {', '.join(coin_list) if coin_list else 'none'}")
        lines.append(f"stock entries: {', '.join(stock_list) if stock_list else 'none'}")
        if warning_list:
            lines.append(f"warnings: {' | '.join(warning_list)}")
        return self.send_message("\n".join(lines))

    def send_error(self, message: str) -> bool:
        return self.send_message(f"[Agent Error]\n{message}")

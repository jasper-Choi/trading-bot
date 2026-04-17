from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

import config
import requests
from src.position_manager import load_history

from .base import TradingAgent
from .state import CACHE_DIR, load_json_artifact, write_json_artifact


NOTIFIER_STATE_FILE = CACHE_DIR / "notifier_state.json"


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

    def send_daily_summary_if_needed(self, stock_history: list[dict] | None = None) -> bool:
        now = datetime.now(config.KST)
        if now.hour < 16:
            return False

        marker = load_json_artifact(NOTIFIER_STATE_FILE, default={})
        today = now.strftime("%Y-%m-%d")
        if marker.get("daily_summary_sent_at") == today:
            return False

        coin_history = load_history()
        stock_history = stock_history or []

        coin_pnl = sum(float(item.get("pnl", 0) or 0) for item in coin_history if str(item.get("exit_date", "")).startswith(today))
        stock_pnl = sum(float(item.get("pnl", 0) or 0) for item in stock_history if str(item.get("exit_date", "")).startswith(today))
        total_pnl = coin_pnl + stock_pnl

        text = "\n".join(
            [
                f"[Daily Summary] {today}",
                f"coin pnl: {coin_pnl:+,.0f}",
                f"stock pnl: {stock_pnl:+,.0f}",
                f"total pnl: {total_pnl:+,.0f}",
            ]
        )
        sent = self.send_message(text)
        if sent:
            write_json_artifact(Path(NOTIFIER_STATE_FILE), {"daily_summary_sent_at": today})
        return sent

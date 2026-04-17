from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config import settings


@dataclass(slots=True)
class TelegramNotifier:
    token: str
    chat_id: str

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=8,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException:
            return False

    def send_cycle_summary(self, previous_state: dict[str, Any], current_state: dict[str, Any]) -> bool:
        if not self.enabled:
            return False

        stance_changed = previous_state.get("stance") != current_state.get("stance")
        regime_changed = previous_state.get("regime") != current_state.get("regime")
        risk_changed = previous_state.get("allow_new_entries") != current_state.get("allow_new_entries")
        should_send = settings.telegram_notify_every_cycle or stance_changed or regime_changed or risk_changed
        if not should_send:
            return False

        lines = [
            f"[{settings.company_name}] company cycle update",
            f"stance: {current_state.get('stance')}",
            f"regime: {current_state.get('regime')}",
            f"risk budget: {current_state.get('risk_budget')}",
            f"new entries: {'ON' if current_state.get('allow_new_entries') else 'BLOCKED'}",
            f"signals: {', '.join(current_state.get('latest_signals', [])[:4])}",
        ]
        return self.send("\n".join(lines))

    def send_error(self, message: str) -> bool:
        if not self.enabled:
            return False
        return self.send(f"[{settings.company_name}] runtime error\n{message}")


notifier = TelegramNotifier(
    token=settings.telegram_bot_token,
    chat_id=settings.telegram_chat_id,
)


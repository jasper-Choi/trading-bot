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

        crypto_plan = current_state.get("strategy_book", {}).get("crypto_plan", {})
        korea_plan = current_state.get("strategy_book", {}).get("korea_plan", {})
        desk_priorities = current_state.get("strategy_book", {}).get("desk_priorities", [])
        lines = [
            f"[{settings.company_name}] company cycle update",
            f"time: {current_state.get('session_state', {}).get('local_time', 'n/a')} {current_state.get('session_state', {}).get('timezone', '')}".strip(),
            f"stance/regime: {current_state.get('stance')} / {current_state.get('regime')}",
            f"risk: budget {current_state.get('risk_budget')} / entries {'ON' if current_state.get('allow_new_entries') else 'BLOCKED'}",
            f"focus: {current_state.get('strategy_book', {}).get('company_focus', 'n/a')}",
            f"priorities: {', '.join(desk_priorities[:2]) if desk_priorities else 'n/a'}",
            f"crypto plan: {crypto_plan.get('action', 'n/a')} / {crypto_plan.get('size', 'n/a')} / {crypto_plan.get('focus', 'n/a')}",
            f"korea plan: {korea_plan.get('action', 'n/a')} / {korea_plan.get('size', 'n/a')} / {korea_plan.get('focus', 'n/a')}",
        ]
        execution_log = current_state.get("execution_log", [])
        if execution_log:
            latest = execution_log[0]
            lines.append(
                f"latest paper order: {latest.get('desk')} / {latest.get('action')} / "
                f"{latest.get('size')} / est {latest.get('pnl_estimate_pct', 0.0)}%"
            )
        daily_summary = current_state.get("daily_summary", {})
        if daily_summary:
            lines.append(
                f"today: cycles={daily_summary.get('cycles_run', 0)} / "
                f"orders={daily_summary.get('orders_logged', 0)} / "
                f"est_pnl={daily_summary.get('estimated_pnl_pct', 0.0)}%"
            )
        latest_signals = current_state.get("latest_signals", [])
        if latest_signals:
            lines.append(f"signals: {', '.join(latest_signals[:3])}")
        return self.send("\n".join(lines))

    def send_error(self, message: str) -> bool:
        if not self.enabled:
            return False
        return self.send(f"[{settings.company_name}] runtime error\n{message}")


notifier = TelegramNotifier(
    token=settings.telegram_bot_token,
    chat_id=settings.telegram_chat_id,
)

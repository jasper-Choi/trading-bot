from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config import settings


@dataclass(slots=True)
class TelegramNotifier:
    token: str
    chat_id: str
    last_error: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            self.last_error = "telegram token/chat_id not configured"
            return False
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=8,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("ok", False):
                self.last_error = str(payload.get("description", "telegram api returned ok=false"))
                print(f"[notifier] telegram send failed: {self.last_error}")
                return False
            self.last_error = ""
            return True
        except ValueError:
            self.last_error = "telegram api returned non-json response"
            print(f"[notifier] telegram send failed: {self.last_error}")
            return False
        except requests.RequestException as exc:
            self.last_error = str(exc)
            print(f"[notifier] telegram send failed: {self.last_error}")
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
        us_plan = current_state.get("strategy_book", {}).get("us_plan", {})
        desk_priorities = current_state.get("strategy_book", {}).get("desk_priorities", [])
        lines = [
            f"[{settings.company_name}] company cycle update",
            f"time: {current_state.get('session_state', {}).get('local_time', 'n/a')} {current_state.get('session_state', {}).get('timezone', '')}".strip(),
            f"phase: {current_state.get('session_state', {}).get('market_phase', 'n/a')}",
            f"stance/regime: {current_state.get('stance')} / {current_state.get('regime')}",
            f"risk: budget {current_state.get('risk_budget')} / entries {'ON' if current_state.get('allow_new_entries') else 'BLOCKED'}",
            f"focus: {current_state.get('strategy_book', {}).get('company_focus', 'n/a')}",
            f"priorities: {', '.join(desk_priorities[:3]) if desk_priorities else 'n/a'}",
            f"crypto plan: {crypto_plan.get('action', 'n/a')} / {crypto_plan.get('size', 'n/a')} / {crypto_plan.get('focus', 'n/a')}",
            f"korea plan: {korea_plan.get('action', 'n/a')} / {korea_plan.get('size', 'n/a')} / {korea_plan.get('focus', 'n/a')}",
            f"us plan: {us_plan.get('action', 'n/a')} / {us_plan.get('size', 'n/a')} / {us_plan.get('focus', 'n/a')}",
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
            lines.append(
                f"pnl: realized {daily_summary.get('realized_pnl_pct', 0.0)}% ({daily_summary.get('realized_pnl_krw', 0):,} KRW) / "
                f"expectancy {daily_summary.get('expectancy_pct', 0.0)}% / gross {daily_summary.get('gross_open_notional_pct', 0.0)}x"
            )
        latest_signals = current_state.get("latest_signals", [])
        if latest_signals:
            lines.append(f"signals: {', '.join(latest_signals[:3])}")
        ops_flags = current_state.get("ops_flags", {}) or {}
        flag_items = list((ops_flags.get("items") or [])[:2]) if isinstance(ops_flags, dict) else []
        if flag_items:
            lines.append(f"ops: {ops_flags.get('severity', 'n/a')} / " + " | ".join(item.get("message", "n/a") for item in flag_items))
        return self.send("\n".join(lines))

    def send_error(self, message: str) -> bool:
        if not self.enabled:
            return False
        return self.send(f"[{settings.company_name}] runtime error\n{message}")

    def send_risk_alert(self, current_state: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        daily = current_state.get("daily_summary", {})
        lines = [
            f"[{settings.company_name}] risk alert",
            f"stance/regime: {current_state.get('stance')} / {current_state.get('regime')}",
            f"risk budget: {current_state.get('risk_budget')} / entries {'ON' if current_state.get('allow_new_entries') else 'BLOCKED'}",
            f"realized: {daily.get('realized_pnl_pct', 0.0)}% ({daily.get('realized_pnl_krw', 0):,} KRW) / unrealized: {daily.get('unrealized_pnl_pct', 0.0)}% ({daily.get('unrealized_pnl_krw', 0):,} KRW)",
            f"wins/losses: {daily.get('wins', 0)} / {daily.get('losses', 0)} / win rate {daily.get('win_rate', 0.0)}%",
            f"expectancy: {daily.get('expectancy_pct', 0.0)}% ({daily.get('expectancy_krw', 0):,} KRW)",
            f"open positions: {daily.get('open_positions', 0)} / current cycle planned: {daily.get('current_cycle_planned_orders', 0)} / gross {daily.get('gross_open_notional_pct', 0.0)}x",
        ]
        stop_stats = (daily.get("close_reason_stats", {}) or {}).get("stop_hit", {}) or {}
        if int(stop_stats.get("count", 0) or 0) > 0:
            lines.append(f"stop pressure: {stop_stats.get('count', 0)} stop_hit / {stop_stats.get('pnl_pct', 0.0)}%")
        ops_flags = (((current_state.get("dashboard") or {}).get("ops_flags")) if isinstance(current_state.get("dashboard"), dict) else None) or current_state.get("ops_flags") or {}
        flag_items = list((ops_flags.get("items") or [])[:3]) if isinstance(ops_flags, dict) else []
        if flag_items:
            lines.append(f"ops severity: {ops_flags.get('severity', 'n/a')}")
            for item in flag_items:
                lines.append(f"- {item.get('message', 'n/a')}")
        return self.send("\n".join(lines))

    def send_ops_alert(self, title: str, lines: list[str]) -> bool:
        if not self.enabled:
            return False
        body = "\n".join([f"[{settings.company_name}] {title}", *lines])
        return self.send(body)

    def send_realtime_decision_alert(self, snapshot: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        strategy_book = snapshot.get("strategy_book", {}) or {}
        runtime_profile = snapshot.get("runtime_profile", {}) or {}
        orders = snapshot.get("orders", []) or []
        lines = [
            f"[{settings.company_name}] realtime decision",
            f"runtime: {runtime_profile.get('mode', 'n/a')} / {runtime_profile.get('interval_seconds', 'n/a')}s",
            f"reason: {runtime_profile.get('reason', 'n/a')}",
            f"crypto: {(strategy_book.get('crypto_plan', {}) or {}).get('action', 'n/a')} / {(strategy_book.get('crypto_plan', {}) or {}).get('focus', 'n/a')}",
            f"korea: {(strategy_book.get('korea_plan', {}) or {}).get('action', 'n/a')} / {(strategy_book.get('korea_plan', {}) or {}).get('focus', 'n/a')}",
            f"us: {(strategy_book.get('us_plan', {}) or {}).get('action', 'n/a')} / {(strategy_book.get('us_plan', {}) or {}).get('focus', 'n/a')}",
        ]
        if orders:
            lines.append("orders: " + " | ".join(f"{item.get('desk')}={item.get('action')}/{item.get('status')}" for item in orders))
        return self.send("\n".join(lines))


notifier = TelegramNotifier(
    token=settings.telegram_bot_token,
    chat_id=settings.telegram_chat_id,
)

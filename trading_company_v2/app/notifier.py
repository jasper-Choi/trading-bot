from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timezone
import hashlib
import time
from typing import Any

import requests

from app.config import settings


@dataclass(slots=True)
class TelegramNotifier:
    token: str
    chat_id: str
    last_error: str = ""
    _last_sent_at: dict[str, float] = field(default_factory=dict)
    _last_sent_hash: dict[str, str] = field(default_factory=dict)

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

    @staticmethod
    def _value(payload: Any, key: str, default: Any = None) -> Any:
        if isinstance(payload, dict):
            return payload.get(key, default)
        return getattr(payload, key, default)

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _size_to_notional(size: Any) -> float:
        try:
            return float(str(size or "0").replace("x", ""))
        except ValueError:
            return 0.0

    @staticmethod
    def _fmt_krw(value: float) -> str:
        sign = "+" if value > 0 else ""
        return f"{sign}{round(value):,}원"

    @staticmethod
    def _fmt_pct(value: float) -> str:
        sign = "+" if value > 0 else ""
        return f"{sign}{value:.2f}%"

    @staticmethod
    def _holding_minutes(opened_at: Any, closed_at: Any = None) -> int:
        try:
            opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            if closed_at:
                closed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
                if closed.tzinfo is None:
                    closed = closed.replace(tzinfo=timezone.utc)
            else:
                closed = datetime.now(timezone.utc)
            return max(round((closed - opened).total_seconds() / 60), 0)
        except Exception:
            return 0

    def _position_notional_krw(self, position: Any) -> int:
        explicit = self._float(self._value(position, "notional_krw", 0.0))
        if explicit > 0:
            return round(explicit)
        notional_pct = self._float(self._value(position, "notional_pct", 0.0))
        if notional_pct <= 0:
            notional_pct = self._size_to_notional(self._value(position, "size", "0.00x"))
        capital = self._float(
            self._value(position, "capital_krw", settings.live_capital_krw or settings.paper_capital_krw),
            float(settings.paper_capital_krw),
        )
        return round(capital * notional_pct)

    def send_trade_entry(self, position: Any) -> bool:
        if not self.enabled:
            return False
        symbol = str(self._value(position, "symbol", "UNKNOWN") or "UNKNOWN")
        action = str(self._value(position, "action", "entry") or "entry")
        size = str(self._value(position, "size", "") or "")
        notional_krw = self._position_notional_krw(position)
        entry_price = self._float(self._value(position, "entry_price", self._value(position, "current_price", 0.0)))
        focus = str(self._value(position, "focus", "") or "")
        entry_path = str(self._value(position, "entry_path", action) or action)
        combined = self._float(self._value(position, "combined_score", self._value(position, "signal_score", 0.0)))
        signal = self._float(self._value(position, "signal_score", 0.0))
        micro = self._float(self._value(position, "micro_score", 0.0))
        orderbook = self._float(self._value(position, "orderbook_score", self._value(position, "orderbook_bid_ask_ratio", 0.0)))
        bias = str(self._value(position, "bias", "") or "")
        pullback = self._float(self._value(position, "pullback_score", 0.0))
        stream = self._float(self._value(position, "stream_score", 0.0))
        opened_at = str(self._value(position, "opened_at", "") or "")
        lines = [
            f"🟢 진입 | {symbol}",
            f"사이즈: {notional_krw:,}원{f' ({size})' if size else ''} | 가격: {entry_price:,.8g}",
            f"경로: {entry_path}",
            f"Combined: {combined:.3f} | Signal: {signal:.2f} | Micro: {micro:.2f} | OB: {orderbook:.2f}",
            f"Bias: {bias or 'n/a'} | Pullback: {pullback:.2f} | Stream: {stream:.2f}",
        ]
        if focus:
            lines.append(f"Focus: {focus[:160]}")
        return self._send_keyed(
            f"trade_entry:{symbol}:{action}:{opened_at}",
            "\n".join(lines),
            cooldown_seconds=0,
            suppress_duplicate_seconds=24 * 60 * 60,
        )

    def send_trade_exit(self, position: Any, exit_reason: str) -> bool:
        if not self.enabled:
            return False
        symbol = str(self._value(position, "symbol", "UNKNOWN") or "UNKNOWN")
        pnl_pct = self._float(self._value(position, "pnl_pct", self._value(position, "realized_pnl_pct", 0.0)))
        notional_krw = self._position_notional_krw(position)
        pnl_krw = self._float(self._value(position, "pnl_krw", notional_krw * pnl_pct / 100))
        peak_pnl = self._float(self._value(position, "peak_pnl_pct", pnl_pct))
        opened_at = str(self._value(position, "opened_at", "") or "")
        closed_at = str(self._value(position, "closed_at", "") or "")
        holding = self._holding_minutes(opened_at, closed_at)
        today_pnl_krw = self._value(position, "today_pnl_krw", None)
        lines = [
            f"🔴 청산 | {symbol}",
            f"PnL: {self._fmt_pct(pnl_pct)} ({self._fmt_krw(pnl_krw)}) | 보유: {holding}분",
            f"사유: {exit_reason} | Peak: {self._fmt_pct(peak_pnl)}",
        ]
        if today_pnl_krw is not None:
            lines.append(f"누적 오늘: {self._fmt_krw(self._float(today_pnl_krw))}")
        return self._send_keyed(
            f"trade_exit:{symbol}:{exit_reason}:{opened_at}:{closed_at}",
            "\n".join(lines),
            cooldown_seconds=0,
            suppress_duplicate_seconds=24 * 60 * 60,
        )

    @staticmethod
    def _fingerprint(text: str) -> str:
        return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()

    def _send_keyed(self, key: str, text: str, cooldown_seconds: int, suppress_duplicate_seconds: int | None = None) -> bool:
        if not self.enabled:
            return False
        now = time.time()
        fingerprint = self._fingerprint(text)
        last_at = float(self._last_sent_at.get(key, 0.0) or 0.0)
        last_hash = self._last_sent_hash.get(key, "")
        duplicate_window = suppress_duplicate_seconds if suppress_duplicate_seconds is not None else cooldown_seconds
        if last_hash == fingerprint and (now - last_at) < duplicate_window:
            return False
        if (now - last_at) < cooldown_seconds:
            return False
        sent = self.send(text)
        if sent:
            self._last_sent_at[key] = now
            self._last_sent_hash[key] = fingerprint
        return sent

    def send_cycle_summary(self, previous_state: dict[str, Any], current_state: dict[str, Any]) -> bool:
        if not self.enabled or not settings.telegram_summary_enabled:
            return False

        stance_changed = previous_state.get("stance") != current_state.get("stance")
        regime_changed = previous_state.get("regime") != current_state.get("regime")
        risk_changed = previous_state.get("allow_new_entries") != current_state.get("allow_new_entries")
        prev_capital_mode = str(((previous_state.get("strategy_book") or {}).get("capital_profile") or {}).get("mode") or "")
        curr_capital_mode = str(((current_state.get("strategy_book") or {}).get("capital_profile") or {}).get("mode") or "")
        capital_mode_changed = prev_capital_mode != curr_capital_mode
        should_send = settings.telegram_notify_every_cycle or stance_changed or regime_changed or risk_changed or capital_mode_changed
        if not should_send:
            return False

        crypto_plan = current_state.get("strategy_book", {}).get("crypto_plan", {})
        korea_plan = current_state.get("strategy_book", {}).get("korea_plan", {})
        us_plan = current_state.get("strategy_book", {}).get("us_plan", {})
        capital_profile = (current_state.get("strategy_book", {}) or {}).get("capital_profile", {}) or {}
        desk_priorities = current_state.get("strategy_book", {}).get("desk_priorities", [])
        lines = [
            f"[{settings.company_name}] company cycle update",
            f"time: {current_state.get('session_state', {}).get('local_time', 'n/a')} {current_state.get('session_state', {}).get('timezone', '')}".strip(),
            f"phase: {current_state.get('session_state', {}).get('market_phase', 'n/a')}",
            f"stance/regime: {current_state.get('stance')} / {current_state.get('regime')}",
            f"risk: budget {current_state.get('risk_budget')} / entries {'ON' if current_state.get('allow_new_entries') else 'BLOCKED'}",
            f"capital mode: {capital_profile.get('mode', 'neutral')} / global x{capital_profile.get('global_multiplier', 1.0)}",
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
        return self._send_keyed(
            "cycle_summary",
            "\n".join(lines),
            cooldown_seconds=60 * 60,
            suppress_duplicate_seconds=6 * 60 * 60,
        )

    def send_error(self, message: str) -> bool:
        if not self.enabled or not settings.telegram_error_enabled:
            return False
        return self.send(f"[{settings.company_name}] runtime error\n{message}")

    def send_risk_alert(self, current_state: dict[str, Any]) -> bool:
        if not self.enabled or not settings.telegram_risk_enabled:
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
        return self._send_keyed(
            "risk_alert",
            "\n".join(lines),
            cooldown_seconds=30 * 60,
            suppress_duplicate_seconds=3 * 60 * 60,
        )

    def send_ops_alert(self, title: str, lines: list[str]) -> bool:
        if not self.enabled or not settings.telegram_ops_enabled:
            return False
        body = "\n".join([f"[{settings.company_name}] {title}", *lines])
        lowered = title.lower()
        cooldown_seconds = 4 * 60 * 60 if "hold alert" in lowered else 75 * 60
        duplicate_window = 12 * 60 * 60 if "hold alert" in lowered else 6 * 60 * 60
        return self._send_keyed(
            f"ops_alert:{lowered}",
            body,
            cooldown_seconds=cooldown_seconds,
            suppress_duplicate_seconds=duplicate_window,
        )

    def send_stale_execution_alert(self, summary: dict[str, Any]) -> bool:
        if not self.enabled or not settings.telegram_stale_enabled:
            return False
        stale_items = list(summary.get("stale_live") or [])[:3]
        stale_count = int(summary.get("stale_count", 0) or 0)
        if stale_count <= 0 or not stale_items:
            return False
        lines = [
            f"[{settings.company_name}] stale live execution alert",
            f"stale orders: {stale_count}",
            f"pending: {summary.get('pending_count', 0)} / partial: {summary.get('partial_count', 0)}",
        ]
        for item in stale_items:
            lines.append(
                f"- {item.get('desk', 'n/a')} / {item.get('action', 'n/a')} / "
                f"{item.get('symbol') or item.get('focus') or 'n/a'} / "
                f"{item.get('status', 'n/a')} / {item.get('effect_status', 'n/a')} / "
                f"{item.get('age_minutes', 'n/a')}m"
            )
        return self._send_keyed(
            "stale_live_execution",
            "\n".join(lines),
            cooldown_seconds=6 * 60 * 60,
            suppress_duplicate_seconds=12 * 60 * 60,
        )

    def send_realtime_decision_alert(self, snapshot: dict[str, Any]) -> bool:
        if not self.enabled or not settings.telegram_realtime_enabled:
            return False
        strategy_book = snapshot.get("strategy_book", {}) or {}
        runtime_profile = snapshot.get("runtime_profile", {}) or {}
        orders = snapshot.get("orders", []) or []
        actionable_actions = {
            str((strategy_book.get(plan_name, {}) or {}).get("action", "n/a")).lower()
            for plan_name in ("crypto_plan", "korea_plan", "us_plan")
        }
        passive_actions = {"hold", "observe", "wait", "n/a", "none", "flat", "idle"}
        if not orders and actionable_actions.issubset(passive_actions):
            return False
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
        return self._send_keyed(
            "realtime_decision",
            "\n".join(lines),
            cooldown_seconds=3 * 60 * 60,
            suppress_duplicate_seconds=8 * 60 * 60,
        )


    def send_crypto_pilot_alert(self, current_state: dict[str, Any]) -> bool:
        if not self.enabled or not settings.telegram_ops_enabled:
            return False
        crypto_lane = (current_state.get("dashboard") or {}).get("crypto_live_lane") or {}
        if not crypto_lane:
            crypto_lane = current_state.get("crypto_live_lane") or {}
        trigger_state = str(crypto_lane.get("trigger_state", "") or "")
        if trigger_state not in {"arming", "ready"}:
            return False
        signal_score = float(crypto_lane.get("signal_score", 0.0) or 0.0)
        trigger_threshold = float(crypto_lane.get("trigger_threshold", 0.0) or 0.0)
        distance = float(crypto_lane.get("distance_to_trigger", 0.0) or 0.0)
        symbol = str(crypto_lane.get("symbol", "KRW-BTC") or "KRW-BTC")
        action = str(crypto_lane.get("action", "watchlist_only") or "watchlist_only")
        if trigger_state == "ready":
            title = f"[{settings.company_name}] crypto pilot READY"
            lines = [
                f"signal {signal_score:.2f} >= trigger {trigger_threshold:.2f}",
                f"symbol: {symbol} / plan: {action}",
                "tiny-size pilot order cycle is now active.",
            ]
            key = "crypto_pilot_ready"
            cooldown = 30 * 60
            dup_window = 2 * 60 * 60
        else:
            title = f"[{settings.company_name}] crypto pilot arming"
            lines = [
                f"signal {signal_score:.2f} / trigger {trigger_threshold:.2f} / distance {distance:.2f}",
                f"symbol: {symbol} / approaching entry threshold.",
                "Monitor closely — tiny-size pilot entry imminent if signal holds.",
            ]
            key = "crypto_pilot_arming"
            cooldown = 2 * 60 * 60
            dup_window = 6 * 60 * 60
        body = "\n".join([title, *lines])
        return self._send_keyed(key, body, cooldown_seconds=cooldown, suppress_duplicate_seconds=dup_window)


notifier = TelegramNotifier(
    token=settings.telegram_bot_token,
    chat_id=settings.telegram_chat_id,
)

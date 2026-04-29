from __future__ import annotations

from app.agents.base import BaseAgent
from app.config import settings
from app.core.models import AgentResult, PaperOrder


STOP_LIKE_EXIT_REASONS = {
    "stop_hit",
    "rapid_stop_hit",
    "early_failure",
    "failed_ignition",
    "failed_followthrough",
    "rapid_failed_start",
}


class ExecutionAgent(BaseAgent):
    def __init__(self):
        super().__init__("execution_agent")
        self.strategy_book: dict = {}
        self.regime: str = "RANGING"
        self.market_snapshot: dict = {}
        self.open_positions: list[dict] = []
        self.closed_positions: list[dict] = []
        self.daily_summary: dict = {}
        self.allow_new_entries: bool = True
        self.risk_budget: float = 1.0

    def configure(
        self,
        strategy_book: dict,
        regime: str,
        market_snapshot: dict,
        open_positions: list[dict],
        closed_positions: list[dict],
        daily_summary: dict,
        allow_new_entries: bool,
        risk_budget: float,
    ) -> None:
        self.strategy_book = strategy_book
        self.regime = regime
        self.market_snapshot = market_snapshot
        self.open_positions = open_positions
        self.closed_positions = closed_positions
        self.daily_summary = daily_summary or {}
        self.allow_new_entries = allow_new_entries
        self.risk_budget = risk_budget

    @staticmethod
    def _size_to_notional(size: str) -> float:
        try:
            return float(size.replace("x", ""))
        except ValueError:
            return 0.0

    @staticmethod
    def _is_stop_like_exit(item: dict) -> bool:
        reason = str(item.get("closed_reason", "") or "")
        if reason in STOP_LIKE_EXIT_REASONS:
            return True
        return reason == "stale_exit" and float(item.get("pnl_pct", 0.0) or 0.0) <= -0.5

    def _desk_open_notional(self, desk: str) -> float:
        return round(
            sum(self._size_to_notional(str(item.get("size", "0.00x"))) for item in self.open_positions if item.get("desk") == desk),
            2,
        )

    def _gross_open_notional(self) -> float:
        active_desks = settings.active_desk_set
        return round(
            sum(
                self._size_to_notional(str(item.get("size", "0.00x")))
                for item in self.open_positions
                if str(item.get("desk") or "") in active_desks
            ),
            2,
        )

    def _crypto_high_corr_open_count(self) -> int:
        """Approximate BTC-beta crowding until per-position signal metadata is persisted."""
        return sum(
            1
            for item in self.open_positions
            if item.get("desk") == "crypto" and str(item.get("symbol") or "").startswith("KRW-")
        )

    def _desk_open_count(self, desk: str) -> int:
        return sum(1 for item in self.open_positions if item.get("desk") == desk)

    def _has_open_position(self, desk: str, symbol: str) -> bool:
        if symbol:
            return any(item.get("desk") == desk and item.get("symbol") == symbol for item in self.open_positions)
        return any(item.get("desk") == desk for item in self.open_positions)

    @staticmethod
    def _desk_limits(desk: str) -> tuple[int, float]:
        # (max_concurrent_positions, max_desk_notional_x)
        # Crypto growth mode needs more concurrent probes; risk_budget still scales each order.
        if desk == "crypto":
            return 5, 2.4
        if desk == "us":
            return 3, 1.5
        return 3, 1.5

    @staticmethod
    def _expected_pnl_pct(desk: str, action: str) -> float:
        if action in {"watchlist_only", "reduce_risk", "stand_by", "capital_preservation", "pre_market_watch"}:
            return 0.0
        if desk == "crypto":
            # Recovery-mode target: reachable win first, then compound via sizing.
            return 4.5
        if desk == "korea":
            # Momentum-breakout recovery target calibrated to current live/paper drawdown.
            return 3.8
        if action == "probe_longs":
            return 3.2
        if action == "selective_probe":
            return 2.4
        return 2.8

    def _reference_price(self, desk: str, symbol: str) -> float:
        if desk == "crypto":
            for item in self.market_snapshot.get("crypto_leaders", []):
                if item.get("market") == symbol:
                    return float(item.get("trade_price") or 0.0)
            return 0.0
        if desk == "us":
            for item in self.market_snapshot.get("us_leaders", []):
                if item.get("ticker") == symbol:
                    return float(item.get("current_price") or 0.0)
            return 0.0
        for item in self.market_snapshot.get("gap_candidates", []) + self.market_snapshot.get("stock_leaders", []):
            if item.get("ticker") == symbol:
                return float(item.get("current_price") or 0.0)
        return 0.0

    def _recent_loss_cooldown(self, desk: str, symbol: str) -> bool:
        if not symbol:
            return False
        recent = self.closed_positions[:4]
        for item in recent:
            if item.get("desk") != desk or item.get("symbol") != symbol:
                continue
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            if desk == "crypto":
                if self._is_stop_like_exit(item) and pnl <= -1.0:
                    return True
                continue
            if pnl <= 0:
                return True
        return False

    def _desk_recent_trades(self, desk: str, limit: int = 6) -> list[dict]:
        return [item for item in self.closed_positions[:limit] if item.get("desk") == desk]

    def _desk_recovery_ready(self, desk: str) -> bool:
        recent = self._desk_recent_trades(desk, limit=4)
        if len(recent) < 2:
            return False
        last_trade = recent[0]
        last_two = recent[:2]
        last_three = recent[:3]
        last_trade_positive = float(last_trade.get("pnl_pct", 0.0) or 0.0) > 0
        last_two_realized = sum(float(item.get("pnl_pct", 0.0) or 0.0) for item in last_two)
        last_three_losses = sum(1 for item in last_three if float(item.get("pnl_pct", 0.0) or 0.0) <= 0)
        # With 4% targets, require at least 1.5% cumulative profit over last 2 trades
        return last_trade_positive and last_two_realized >= 1.5 and last_three_losses <= 1

    def _repeated_loss_block(self, desk: str, symbol: str) -> bool:
        if not symbol:
            return False
        losses = 0
        pnl_total = 0.0
        for item in self.closed_positions[:8]:
            if item.get("desk") != desk or item.get("symbol") != symbol:
                continue
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            if pnl <= 0:
                losses += 1
                pnl_total += pnl
            if desk == "crypto" and losses >= 3 and pnl_total <= -3.0:
                return True
            if desk != "crypto" and losses >= 2:
                return True
        return False

    def _extended_symbol_block(self, desk: str, symbol: str) -> bool:
        if desk != "korea" or not symbol:
            return False
        recent = [
            item
            for item in self.closed_positions[:12]
            if item.get("desk") == desk and item.get("symbol") == symbol
        ]
        if len(recent) < 3:
            return False
        stop_like = sum(1 for item in recent[:5] if self._is_stop_like_exit(item))
        pnl_total = sum(float(item.get("pnl_pct", 0.0) or 0.0) for item in recent[:5])
        # With -2.5% stop per trade, 2 stops = -5%; block after -5% cumulative or 2 stops
        return stop_like >= 2 or pnl_total <= -5.0

    def _desk_loss_pressure(self, desk: str) -> bool:
        recent = self._desk_recent_trades(desk, limit=6)
        if len(recent) < 2:
            return False
        if self._desk_recovery_ready(desk):
            return False
        losses = sum(1 for item in recent if float(item.get("pnl_pct", 0.0) or 0.0) < 0)
        realized = sum(float(item.get("pnl_pct", 0.0) or 0.0) for item in recent)
        if realized > 0:
            return False
        # Thresholds calibrated to new P&L scale: -2% stop (crypto/us), -2.5% (korea)
        # "Loss pressure" fires when 3 losses OR cumulative P&L < 2 full stops
        if desk == "us":
            return losses >= 3 or realized <= -4.0    # 2 × -2% stops
        if desk == "crypto":
            return losses >= 3 or realized <= -4.0    # 2 × -2% stops
        return losses >= 3 or realized <= -5.0        # 2 × -2.5% stops

    def _desk_chronic_drawdown(self, desk: str) -> bool:
        recent = self._desk_recent_trades(desk, limit=5)
        if len(recent) < 4:
            return False
        realized = sum(float(item.get("pnl_pct", 0.0) or 0.0) for item in recent)
        wins = sum(1 for item in recent if float(item.get("pnl_pct", 0.0) or 0.0) > 0)
        losses = sum(1 for item in recent if float(item.get("pnl_pct", 0.0) or 0.0) < 0)
        stop_like = sum(1 for item in recent if self._is_stop_like_exit(item))
        # Chronic drawdown: 3 full stops worth of loss with very low win rate
        if desk == "us":
            return wins == 0 and losses >= 4 and realized <= -6.0    # 3 × -2%
        if desk == "crypto":
            return wins <= 1 and losses >= 4 and realized <= -6.0    # 3 × -2%
        return wins <= 1 and losses >= 4 and (realized <= -7.5 or stop_like >= 3)  # 3 × -2.5%

    def _desk_performance_lock(self, desk: str) -> bool:
        desk_stats = (self.daily_summary.get("desk_stats", {}) or {}).get(desk, {}) or {}
        closed_positions = int(desk_stats.get("closed_positions", 0) or 0)
        wins = int(desk_stats.get("wins", 0) or 0)
        losses = int(desk_stats.get("losses", 0) or 0)
        realized = float(desk_stats.get("realized_pnl_pct", 0.0) or 0.0)
        win_rate = float(desk_stats.get("win_rate", 0.0) or 0.0)
        if closed_positions < 4:
            return False
        # Performance lock: daily P&L worse than 3 full stops with poor win rate
        if desk == "us":
            return wins == 0 and losses >= 4 and realized <= -6.0
        if desk == "crypto":
            return win_rate < 25.0 and losses >= 4 and realized <= -6.0
        return win_rate < 25.0 and losses >= 5 and realized <= -7.5

    def _desk_offense_state(self, desk: str) -> dict:
        desk_stats = (self.daily_summary.get("desk_stats", {}) or {}).get(desk, {}) or {}
        capital_profile = (self.strategy_book.get("capital_profile", {}) or {}) if self.strategy_book else {}
        desk_multiplier = float((capital_profile.get("desk_multipliers", {}) or {}).get(desk, 1.0) or 1.0)
        realized = float(desk_stats.get("realized_pnl_pct", 0.0) or 0.0)
        win_rate = float(desk_stats.get("win_rate", 0.0) or 0.0)
        closed_positions = int(desk_stats.get("closed_positions", 0) or 0)
        open_notional = float(desk_stats.get("open_notional_pct", 0.0) or 0.0)

        # No history → fresh start, allow entries at base size
        if closed_positions == 0:
            score = round(50.0 + (desk_multiplier - 1.0) * 50.0, 1)
            return {"score": score, "tone": "balanced", "size_multiplier": 1.0, "entry_allowed": True}

        score = 50.0
        score += max(min(realized * 7.5, 18.0), -22.0)
        score += max(min((win_rate - 50.0) * 0.35, 12.0), -14.0)
        score += min(closed_positions * 1.8, 8.0)
        score += (desk_multiplier - 1.0) * 50.0
        score -= max(open_notional - 0.55, 0.0) * 18.0
        score = round(max(min(score, 100.0), 0.0), 1)

        if score >= 67:
            return {"score": score, "tone": "press", "size_multiplier": 1.1, "entry_allowed": True}
        if score >= 48:
            return {"score": score, "tone": "balanced", "size_multiplier": 1.0, "entry_allowed": True}
        # Cooldown: throttle size but don't block entries — losing runs still get smaller, not zero
        return {"score": score, "tone": "cooldown", "size_multiplier": 0.75, "entry_allowed": True}

    def _desk_stop_pressure(self, desk: str) -> str:
        recent = self._desk_recent_trades(desk, limit=6)
        if len(recent) < 2:
            return "none"
        stop_like_count = 0
        stop_like_pnl = 0.0
        for item in recent:
            if self._is_stop_like_exit(item):
                stop_like_count += 1
                stop_like_pnl += float(item.get("pnl_pct", 0.0) or 0.0)
        # Calibrated to new stops: -2% crypto/us, -2.5% korea
        # "high" after 3 stop-like exits OR cumulative stop P&L < -6% (3 full stops)
        if stop_like_count >= 3 or stop_like_pnl <= -6.0:
            return "high"
        if stop_like_count >= 2 or stop_like_pnl <= -3.0:
            return "medium"
        return "none"

    def _symbol_stop_pressure(self, desk: str, symbol: str) -> str:
        if not symbol:
            return "none"
        recent = [
            item
            for item in self.closed_positions[:8]
            if item.get("desk") == desk and item.get("symbol") == symbol
        ]
        if len(recent) < 2:
            return "none"
        stop_like_count = 0
        stop_like_pnl = 0.0
        for item in recent[:4]:
            if self._is_stop_like_exit(item):
                stop_like_count += 1
                stop_like_pnl += float(item.get("pnl_pct", 0.0) or 0.0)
        # Per-symbol: 2 stops OR cumulative -4% (2 full stops) = high pressure
        if stop_like_count >= 2 or stop_like_pnl <= -4.0:
            return "high"
        if stop_like_count >= 1 or stop_like_pnl <= -2.0:
            return "medium"
        return "none"

    def _candidate_rank(self, desk: str, symbol: str) -> tuple[float, str]:
        if not symbol:
            return (-999.0, "missing symbol")
        symbol_history = [
            item
            for item in self.closed_positions[:12]
            if item.get("desk") == desk and item.get("symbol") == symbol
        ]
        if not symbol_history:
            return (0.0, "fresh candidate")
        weighted_pnl = 0.0
        wins = 0
        losses = 0
        penalty = 0.0
        recent_slice = symbol_history[:4]
        for idx, item in enumerate(recent_slice):
            weight = max(1.0 - (idx * 0.18), 0.45)
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            weighted_pnl += pnl * weight
            if pnl > 0:
                wins += 1
            else:
                losses += 1
            closed_reason = str(item.get("closed_reason", "") or "")
            if self._is_stop_like_exit(item):
                penalty += 0.45
            elif closed_reason == "stale_exit":
                penalty += 0.2
            elif closed_reason == "target_hit":
                penalty -= 0.18
        score = round((wins * 0.38) - (losses * 0.58) + weighted_pnl * 0.09 - penalty, 2)
        return (
            score,
            f"history wins={wins} losses={losses} weighted_pnl={round(weighted_pnl, 2)}% penalty={round(penalty, 2)}",
        )

    def _symbol_edge_state(self, desk: str, symbol: str) -> dict:
        if not symbol:
            return {"score": 0.0, "tone": "neutral", "size_multiplier": 1.0, "entry_allowed": True}
        symbol_history = [
            item
            for item in self.closed_positions[:14]
            if item.get("desk") == desk and item.get("symbol") == symbol
        ]
        if not symbol_history:
            return {"score": 0.0, "tone": "neutral", "size_multiplier": 1.0, "entry_allowed": True}

        recent_slice = symbol_history[:5]
        weighted_pnl = 0.0
        wins = 0
        losses = 0
        stop_like = 0
        for idx, item in enumerate(recent_slice):
            weight = max(1.0 - (idx * 0.16), 0.4)
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            weighted_pnl += pnl * weight
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            if self._is_stop_like_exit(item):
                stop_like += 1

        score = round((wins * 0.55) - (losses * 0.7) + (weighted_pnl * 0.18) - (stop_like * 0.35), 2)
        if score >= 0.7:
            return {"score": score, "tone": "hot", "size_multiplier": 1.08, "entry_allowed": True}
        if score <= -0.9 or stop_like >= 2:
            return {"score": score, "tone": "cold", "size_multiplier": 0.7, "entry_allowed": False}
        if score <= -0.35:
            return {"score": score, "tone": "cool", "size_multiplier": 0.82, "entry_allowed": True}
        return {"score": score, "tone": "neutral", "size_multiplier": 1.0, "entry_allowed": True}

    def _pick_symbol(self, desk: str, plan: dict) -> tuple[str, list[str]]:
        notes: list[str] = []
        candidates = []
        primary = str(plan.get("symbol", "")).strip()
        if primary:
            candidates.append(primary)
        for item in plan.get("candidate_symbols", []) or []:
            candidate = str(item).strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        ranked_candidates = sorted(
            ((symbol, *self._candidate_rank(desk, symbol)) for symbol in candidates),
            key=lambda item: item[1],
            reverse=True,
        )

        for idx, (symbol, rank_score, rank_reason) in enumerate(ranked_candidates):
            existing_open = any(item.get("desk") == desk and item.get("symbol") == symbol for item in self.open_positions)
            cooldown_loss = self._recent_loss_cooldown(desk, symbol)
            repeated_loss_block = self._repeated_loss_block(desk, symbol)
            extended_block = self._extended_symbol_block(desk, symbol)
            if existing_open or cooldown_loss or repeated_loss_block or extended_block:
                continue
            if idx > 0:
                notes.append(f"rotated from primary symbol to alternate candidate {symbol}")
            if rank_reason:
                notes.append(f"candidate rank: {symbol} / score {rank_score} / {rank_reason}")
            return symbol, notes
        return primary, notes

    def _plan_to_order(self, desk: str, plan: dict) -> PaperOrder:
        original_action = str(plan.get("action", "stand_by"))
        action = original_action
        base_size = str(plan.get("size", "0.00x"))
        symbol, rotation_notes = self._pick_symbol(desk, plan)
        desk_offense = self._desk_offense_state(desk)
        symbol_edge = self._symbol_edge_state(desk, symbol)
        base_notional = self._size_to_notional(base_size)
        atr_multiplier = 1.0
        if desk == "crypto":
            try:
                atr_multiplier = max(min(float(plan.get("atr_size_multiplier", 1.0) or 1.0), 1.15), 0.45)
            except (TypeError, ValueError):
                atr_multiplier = 1.0
        try:
            btc_corr_15m = float(plan.get("btc_corr_15m", 1.0) or 1.0)
        except (TypeError, ValueError):
            btc_corr_15m = 1.0
        try:
            signal_freshness = float(plan.get("signal_freshness", 1.0) or 1.0)
        except (TypeError, ValueError):
            signal_freshness = 1.0
        offense_scaled_base = round(
            base_notional
            * float(desk_offense.get("size_multiplier", 1.0) or 1.0)
            * float(symbol_edge.get("size_multiplier", 1.0) or 1.0),
            2,
        )
        volatility_scaled_base = round(offense_scaled_base * atr_multiplier, 2)
        risk_scaled_notional = round(volatility_scaled_base * max(min(self.risk_budget, 1.0), 0.0), 2)
        desk_stop_pressure = self._desk_stop_pressure(desk)
        symbol_stop_pressure = self._symbol_stop_pressure(desk, symbol)
        downgrade_notes: list[str] = []
        if original_action == "attack_opening_drive" and (desk_stop_pressure != "none" or symbol_stop_pressure != "none"):
            action = "selective_probe"
            downgrade_notes.append(f"{desk} desk risk pattern downgraded attack_opening_drive to selective_probe")
        if action in {"probe_longs", "selective_probe"} and symbol_stop_pressure == "high":
            if desk == "crypto":
                action = "selective_probe"
                downgrade_notes.append(f"{symbol} stop pressure high, crypto growth mode keeps a smaller probe")
            else:
                action = "stand_by"
                downgrade_notes.append(f"{symbol} stop pressure high, stand aside this cycle")
        elif action == "probe_longs" and desk_stop_pressure == "high":
            action = "selective_probe"
            downgrade_notes.append(f"{desk} desk stop pressure high, reduced to selective_probe")
        stop_pressure_scale = 0.5 if desk_stop_pressure == "medium" else 1.0
        scaled_notional_pct = round(risk_scaled_notional * stop_pressure_scale, 2)
        size = f"{scaled_notional_pct:.2f}x"
        notional_pct = scaled_notional_pct
        reference_price = self._reference_price(desk, symbol)
        pnl_estimate_pct = self._expected_pnl_pct(desk, action)
        actionable_entries = {"probe_longs", "attack_opening_drive", "selective_probe"}
        actionable_exits = {"reduce_risk", "capital_preservation"}
        existing_open = self._has_open_position(desk, symbol)
        cooldown_loss = self._recent_loss_cooldown(desk, symbol)
        repeated_loss_block = self._repeated_loss_block(desk, symbol)
        extended_symbol_block = self._extended_symbol_block(desk, symbol)
        desk_loss_pressure = self._desk_loss_pressure(desk)
        crypto_recovery_mode = desk == "crypto" and settings.active_desk_set == {"crypto"}
        desk_loss_pressure_blocks = desk_loss_pressure and not crypto_recovery_mode
        desk_chronic_drawdown = self._desk_chronic_drawdown(desk)
        desk_performance_lock = self._desk_performance_lock(desk)
        desk_recovery_ready = self._desk_recovery_ready(desk)
        desk_offense_block = not bool(desk_offense.get("entry_allowed", True)) and action in actionable_entries
        symbol_edge_block = not bool(symbol_edge.get("entry_allowed", True)) and action in actionable_entries
        blocked_by_stop_pressure = (
            desk_stop_pressure == "high"
            and action in actionable_entries
            and not (desk == "crypto" and settings.active_desk_set == {"crypto"})
        )
        blocked_by_risk = not self.allow_new_entries and action in actionable_entries
        blocked_by_desk_drawdown = (desk_chronic_drawdown or desk_performance_lock) and action in actionable_entries
        desk_open_count = self._desk_open_count(desk)
        desk_open_notional = self._desk_open_notional(desk)
        gross_open_notional = self._gross_open_notional()
        max_positions, max_desk_notional = self._desk_limits(desk)
        if settings.active_desk_set == {"crypto"}:
            total_notional_cap = 2.05 if self.risk_budget >= 0.4 else 1.45 if self.risk_budget >= 0.25 else 1.0
        else:
            total_notional_cap = 1.05 if self.risk_budget >= 0.4 else 0.8 if self.risk_budget >= 0.25 else 0.55
        desk_position_cap_hit = desk_open_count >= max_positions
        desk_notional_cap_hit = (desk_open_notional + notional_pct) > max_desk_notional and action in actionable_entries
        gross_notional_cap_hit = (gross_open_notional + notional_pct) > total_notional_cap and action in actionable_entries
        high_corr_cap_hit = (
            desk == "crypto"
            and action in actionable_entries
            and btc_corr_15m >= float(settings.crypto_high_corr_threshold)
            and self._crypto_high_corr_open_count() >= int(settings.crypto_high_corr_max_positions)
        ) or bool(plan.get("force_high_corr_cap", False))
        stale_signal_block = desk == "crypto" and action in actionable_entries and signal_freshness <= 0.55
        exit_status = "planned" if action in actionable_exits and existing_open else "idle"
        meta = {
            "symbol": symbol,
            "reference_price": reference_price,
            "notional_pct": notional_pct,
            "btc_corr_15m": round(btc_corr_15m, 3),
            "signal_freshness": round(signal_freshness, 3),
            "combined_score": round(float(plan.get("signal_score", 0.0) or 0.0), 3),
            "signal_score": round(float(plan.get("signal_score", 0.0) or 0.0), 3),
            "micro_score": round(float(plan.get("micro_score", 0.0) or 0.0), 3),
            "orderbook_score": round(float(plan.get("orderbook_score", 0.0) or 0.0), 3),
            "orderbook_bid_ask_ratio": round(float(plan.get("orderbook_bid_ask_ratio", 0.0) or 0.0), 3),
            "pullback_score": round(float(plan.get("pullback_score", 0.0) or 0.0), 3),
            "stream_score": round(float(plan.get("stream_score", 0.0) or 0.0), 3),
            "bias": str(plan.get("desk_bias", plan.get("bias", "")) or ""),
            "entry_path": action,
            "status": "planned"
            if action in actionable_entries
            and notional_pct > 0
            and not existing_open
            and not cooldown_loss
            and not repeated_loss_block
            and not extended_symbol_block
            and not desk_loss_pressure_blocks
            and not desk_chronic_drawdown
            and not desk_performance_lock
            and not desk_offense_block
            and not symbol_edge_block
            and not blocked_by_stop_pressure
            and not blocked_by_risk
            and not desk_position_cap_hit
            and not desk_notional_cap_hit
            and not gross_notional_cap_hit
            and not high_corr_cap_hit
            and not stale_signal_block
            else exit_status,
            "pnl_estimate_pct": pnl_estimate_pct,
        }
        notes = list(plan.get("notes", [])) + rotation_notes + downgrade_notes
        if action in actionable_entries and base_size != size:
            if atr_multiplier != 1.0:
                notes.append(
                    f"ATR volatility sizing adjusted base {offense_scaled_base:.2f}x -> {volatility_scaled_base:.2f}x "
                    f"({plan.get('volatility_tier', 'unknown')} / ATR {float(plan.get('atr_pct', 0.0) or 0.0):.2f}%)"
                )
            elif offense_scaled_base != base_notional:
                notes.append(
                    f"{desk} desk offense {desk_offense.get('tone', 'balanced')} adjusted size from {base_size} to {size}"
                )
            elif stop_pressure_scale < 1.0:
                notes.append(f"risk and stop-pressure scaled size from {base_size} to {size}")
            else:
                notes.append(f"risk budget scaled size from {base_size} to {size}")
        if action in actionable_exits and existing_open and symbol:
            notes.append(f"exit requested for live/open position in {symbol}")
        elif existing_open and symbol:
            notes.append(f"existing open paper position in {symbol}, skip duplicate entry")
        if action in actionable_exits and not existing_open:
            notes.append(f"no open position found for {desk} / {symbol or 'desk'}, exit kept idle")
        if cooldown_loss and symbol:
            notes.append(f"recent losing exit in {symbol}, cooldown blocks immediate re-entry")
        if repeated_loss_block and symbol:
            notes.append(f"repeated losses in {symbol}, extended block stays active")
        if extended_symbol_block and symbol:
            notes.append(f"{symbol} remains under extended Korea block after repeated failed attempts")
        if desk_loss_pressure:
            if crypto_recovery_mode:
                notes.append(f"{desk} desk loss pressure active, recovery mode keeps only throttled entries")
            else:
                notes.append(f"{desk} desk loss pressure active, new entries paused")
        if desk_chronic_drawdown:
            notes.append(f"{desk} desk under chronic drawdown lock, new entries require manual recovery")
        if desk_performance_lock:
            notes.append(f"{desk} desk blocked by poor desk-level performance snapshot")
        if desk_offense_block:
            notes.append(f"{desk} desk offense cooldown active, skip new entries this cycle")
        if symbol and symbol_edge.get("tone") in {"hot", "cool", "cold"}:
            notes.append(f"{symbol} symbol edge {symbol_edge.get('tone')} / score {symbol_edge.get('score')}")
        if symbol_edge_block:
            notes.append(f"{symbol} symbol edge is cold, skip re-entry this cycle")
        if desk_recovery_ready:
            notes.append(f"{desk} desk recovery conditions met, selective entries can resume")
        if desk_stop_pressure == "medium":
            notes.append(f"{desk} desk stop pressure elevated, size throttled")
        if symbol_stop_pressure == "medium":
            notes.append(f"{symbol} symbol stop pressure elevated, caution on entry")
        if symbol_stop_pressure == "high":
            notes.append(f"{symbol} symbol stop pressure high, new entry paused")
        if blocked_by_stop_pressure:
            notes.append(f"{desk} desk stop pressure high, new entries paused")
        if blocked_by_risk:
            notes.append("risk gate blocks new entries this cycle")
        if blocked_by_desk_drawdown:
            notes.append(f"{desk} desk blocked after repeated failed attempts and negative expectancy")
        if desk_position_cap_hit and action in actionable_entries:
            notes.append(f"{desk} desk already has {desk_open_count} open position(s), cap {max_positions}")
        if desk_notional_cap_hit and action in actionable_entries:
            notes.append(
                f"{desk} desk exposure cap hit: open {desk_open_notional:.2f}x + new {notional_pct:.2f}x > {max_desk_notional:.2f}x"
            )
        if gross_notional_cap_hit and action in actionable_entries:
            notes.append(
                f"gross exposure cap hit: open {gross_open_notional:.2f}x + new {notional_pct:.2f}x > {total_notional_cap:.2f}x"
            )
        if high_corr_cap_hit:
            scope = "open/planned" if bool(plan.get("force_high_corr_cap", False)) else "open"
            notes.append(
                f"BTC correlation cap hit: corr {btc_corr_15m:.2f} >= {settings.crypto_high_corr_threshold:.2f}, "
                f"{scope} high-beta crypto positions limit {settings.crypto_high_corr_max_positions}"
            )
        if stale_signal_block:
            notes.append(
                f"stale signal blocked entry: freshness {signal_freshness:.2f} "
                f"({plan.get('freshness_reason', 'no freshness detail')})"
            )
        rationale = [meta, *notes]
        return PaperOrder(
            desk=desk,
            action=action,
            focus=str(plan.get("focus", "")),
            size=size,
            symbol=symbol,
            reference_price=reference_price,
            notional_pct=notional_pct,
            status=meta["status"],
            pnl_estimate_pct=pnl_estimate_pct,
            rationale=rationale,
        )

    @staticmethod
    def _crypto_candidate_entry_ok(meta: dict) -> tuple[bool, str]:
        if not meta:
            return False, "missing candidate-specific signal"
        score = float(meta.get("combined_score", meta.get("signal_score", 0.0)) or 0.0)
        trend_allowed = bool(meta.get("trend_entry_allowed", False))
        trend_score = float(meta.get("trend_follow_score", 0.0) or 0.0)
        orderbook_bid_ask = float(meta.get("orderbook_bid_ask_ratio", 0.0) or 0.0)
        freshness = float(meta.get("signal_freshness", 1.0) or 1.0)
        recent_change = float(meta.get("recent_change_pct", 0.0) or 0.0)
        burst_change = float(meta.get("burst_change_pct", 0.0) or 0.0)
        ema_gap = float(meta.get("ema_gap_pct", 0.0) or 0.0)
        rsi_value = meta.get("rsi")
        try:
            rsi_float = float(rsi_value) if rsi_value is not None else 0.0
        except (TypeError, ValueError):
            rsi_float = 0.0
        hard_overheat = recent_change >= 12.0 or burst_change >= 10.0 or ema_gap >= 8.0 or rsi_float >= 92.0
        if score < 0.58:
            return False, f"combined score too low ({score:.2f})"
        if not trend_allowed or trend_score < 0.44:
            return False, f"trend gate failed ({meta.get('trend_alignment', 'unknown')} {trend_score:.2f})"
        if orderbook_bid_ask < 0.96:
            return False, f"orderbook not supportive ({orderbook_bid_ask:.2f}x)"
        if bool(meta.get("rsi_bearish_divergence", False)):
            return False, "bearish RSI divergence"
        if freshness <= 0.55:
            return False, f"stale signal ({freshness:.2f})"
        if hard_overheat:
            return False, "hard overheat"
        return True, f"eligible combined={score:.2f} trend={trend_score:.2f} ob={orderbook_bid_ask:.2f}x"

    @staticmethod
    def _apply_crypto_candidate_meta(plan: dict, meta: dict) -> dict:
        if not meta:
            return plan
        mapped = dict(plan)
        mapped["symbol"] = str(meta.get("market", mapped.get("symbol", "")) or mapped.get("symbol", ""))
        mapped["signal_score"] = float(meta.get("combined_score", meta.get("signal_score", mapped.get("signal_score", 0.0))) or 0.0)
        mapped["desk_bias"] = str(meta.get("bias", mapped.get("desk_bias", "balanced")) or "balanced")
        mapped["focus"] = (
            f"{mapped['symbol']} candidate-specific multi-coin entry "
            f"(combined {mapped['signal_score']:.2f})"
        )
        passthrough_keys = (
            "discovery_score", "change_rate", "volume_24h_krw",
            "recent_change_pct", "burst_change_pct", "ema_gap_pct", "pullback_gap_pct", "range_4_pct", "rsi",
            "micro_score", "micro_ready", "micro_bias", "micro_reasons", "micro_vol_ratio",
            "micro_move_3_pct", "micro_move_10_pct", "micro_vwap_gap_pct", "micro_range_5_pct", "micro_exhausted",
            "stream_fresh", "stream_score", "stream_ignition", "stream_reversal", "stream_age_seconds",
            "stream_move_5s_pct", "stream_move_15s_pct", "stream_move_60s_pct", "stream_ticks_15s",
            "stream_buy_ratio_15s", "stream_reasons",
            "orderbook_score", "orderbook_ready", "orderbook_bid_ask_ratio", "orderbook_spread_pct",
            "orderbook_imbalance", "orderbook_reasons",
            "atr_size_multiplier", "atr_pct", "volatility_tier", "atr_sizing_reason",
            "btc_corr_15m", "signal_freshness", "signal_age_minutes", "freshness_reason",
            "breakout_confirmed", "breakout_partial", "breakout_count", "vol_ratio", "breakout_score",
            "trend_follow_score", "trend_alignment", "trend_entry_allowed", "trend_slope_pct",
            "trend_extension_pct", "trend_reasons",
            "rsi_quality_ok", "rsi_reset_confirmed", "rsi_bearish_divergence", "rsi_extreme",
            "ict_score", "kill_zone_active", "kill_zone_name", "ssl_sweep_confirmed",
            "choch_bullish", "choch_bearish", "bos_bullish", "bos_bearish", "ict_bullish_count",
            "ict_structure", "pullback_detected", "pullback_score", "spike_pct_15m",
            "retrace_from_high_pct", "vol_contracted_on_pullback",
        )
        for key in passthrough_keys:
            if key in meta:
                mapped[key] = meta[key]
        notes = list(mapped.get("notes", []) or [])
        notes.append(
            f"candidate-specific signal: {mapped['symbol']} combined={mapped.get('signal_score', 0.0):.2f} "
            f"trend={mapped.get('trend_follow_score', 0.0):.2f} "
            f"ob={mapped.get('orderbook_bid_ask_ratio', 0.0):.2f}x"
        )
        mapped["notes"] = notes
        return mapped

    def _multi_orders(self, desk: str, plan: dict) -> list[dict]:
        """Generate up to max_positions concurrent orders per desk from ranked candidates."""
        action = str(plan.get("action", ""))
        if action not in {"probe_longs", "attack_opening_drive", "selective_probe"}:
            return [self._plan_to_order(desk, plan).model_dump()]

        max_positions, _ = self._desk_limits(desk)
        already_open = self._desk_open_count(desk)
        slots = max_positions - already_open

        if slots <= 1:
            return [self._plan_to_order(desk, plan).model_dump()]

        primary = str(plan.get("symbol", "")).strip()
        all_candidates: list[str] = [primary] if primary else []
        for item in plan.get("candidate_symbols", []) or []:
            s = str(item).strip()
            if s and s not in all_candidates:
                all_candidates.append(s)

        if len(all_candidates) <= 1:
            return [self._plan_to_order(desk, plan).model_dump()]

        candidate_meta = {
            str(item.get("market", "")).strip(): item
            for item in (plan.get("candidate_markets") or [])
            if str(item.get("market", "")).strip()
        }
        skipped_candidates: list[str] = []
        if desk == "crypto" and candidate_meta:
            eligible_candidates = []
            for candidate in all_candidates:
                ok, reason = self._crypto_candidate_entry_ok(candidate_meta.get(candidate, {}))
                if ok:
                    eligible_candidates.append(candidate)
                else:
                    skipped_candidates.append(f"{candidate}: {reason}")
            all_candidates = eligible_candidates
            if not all_candidates:
                blocked_plan = dict(plan)
                blocked_plan["action"] = "watchlist_only"
                blocked_plan["size"] = "0.00x"
                blocked_plan["focus"] = "Crypto candidates failed per-symbol growth-mode eligibility."
                blocked_plan["notes"] = list(plan.get("notes", []) or []) + skipped_candidates[:6]
                return [self._plan_to_order(desk, blocked_plan).model_dump()]

        # Divide base size evenly across eligible concurrent slots.
        base_notional = self._size_to_notional(str(plan.get("size", "0.00x")))
        n_intended = min(slots, len(all_candidates))
        if desk == "crypto" and n_intended > 1:
            per_order_notional = round(max(base_notional / min(n_intended, 3), 0.18), 2)
        else:
            per_order_notional = round(base_notional / n_intended, 2) if n_intended > 1 else base_notional
        per_order_size = f"{per_order_notional:.2f}x"

        orders: list[dict] = []
        planned_count = 0
        planned_high_corr_count = 0
        for candidate in all_candidates:
            if planned_count >= slots:
                break
            if self._has_open_position(desk, candidate):
                continue
            single_plan = dict(plan)
            single_plan["symbol"] = candidate
            single_plan["candidate_symbols"] = []
            single_plan["size"] = per_order_size
            meta = candidate_meta.get(candidate, {})
            if desk == "crypto":
                single_plan = self._apply_crypto_candidate_meta(single_plan, meta)
                single_plan["candidate_symbols"] = []
                single_plan["size"] = per_order_size
                if skipped_candidates:
                    single_plan["notes"] = list(single_plan.get("notes", []) or []) + [
                        f"skipped weaker candidates: {'; '.join(skipped_candidates[:3])}"
                    ]
            for key in (
                "atr_size_multiplier",
                "atr_pct",
                "volatility_tier",
                "btc_corr_15m",
                "signal_freshness",
                "signal_age_minutes",
                "freshness_reason",
            ):
                if key in meta:
                    single_plan[key] = meta[key]
            try:
                candidate_corr = float(single_plan.get("btc_corr_15m", 1.0) or 1.0)
            except (TypeError, ValueError):
                candidate_corr = 1.0
            high_corr_open_count = self._crypto_high_corr_open_count() if desk == "crypto" else 0
            if (
                desk == "crypto"
                and candidate_corr >= float(settings.crypto_high_corr_threshold)
                and high_corr_open_count + planned_high_corr_count >= int(settings.crypto_high_corr_max_positions)
            ):
                single_plan["force_high_corr_cap"] = True
            order = self._plan_to_order(desk, single_plan)
            orders.append(order.model_dump())
            if order.status == "planned":
                planned_count += 1
                if desk == "crypto" and candidate_corr >= float(settings.crypto_high_corr_threshold):
                    planned_high_corr_count += 1

        return orders if orders else [self._plan_to_order(desk, plan).model_dump()]

    def run(self) -> AgentResult:
        active_desks = settings.active_desk_set
        crypto_plan = self.strategy_book.get("crypto_plan", {})
        korea_plan = self.strategy_book.get("korea_plan", {})
        us_plan = self.strategy_book.get("us_plan", {})
        orders: list[dict] = []
        if "crypto" in active_desks:
            orders += self._multi_orders("crypto", crypto_plan)
        if "korea" in active_desks:
            orders += self._multi_orders("korea", korea_plan)
        if "us" in active_desks:
            orders += self._multi_orders("us", us_plan)
        active_orders = [item for item in orders if item["status"] == "planned"]
        return AgentResult(
            name=self.name,
            score=1.0,
            reason="paper execution ledger active, real broker routing intentionally disabled",
            payload={
                "mode": "paper",
                "orders_sent": len(active_orders),
                "broker_live": False,
                "orders": orders,
            },
        )

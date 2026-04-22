from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult, PaperOrder


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

    def _desk_open_notional(self, desk: str) -> float:
        return round(
            sum(self._size_to_notional(str(item.get("size", "0.00x"))) for item in self.open_positions if item.get("desk") == desk),
            2,
        )

    def _gross_open_notional(self) -> float:
        return round(sum(self._size_to_notional(str(item.get("size", "0.00x"))) for item in self.open_positions), 2)

    def _desk_open_count(self, desk: str) -> int:
        return sum(1 for item in self.open_positions if item.get("desk") == desk)

    def _has_open_position(self, desk: str, symbol: str) -> bool:
        if symbol:
            return any(item.get("desk") == desk and item.get("symbol") == symbol for item in self.open_positions)
        return any(item.get("desk") == desk for item in self.open_positions)

    @staticmethod
    def _desk_limits(desk: str) -> tuple[int, float]:
        if desk == "crypto":
            return 1, 0.6
        if desk == "us":
            return 2, 0.55
        return 2, 0.5

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
            if float(item.get("pnl_pct", 0.0) or 0.0) <= 0:
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
        return last_trade_positive and last_two_realized >= 0.35 and last_three_losses <= 1

    def _repeated_loss_block(self, desk: str, symbol: str) -> bool:
        if not symbol:
            return False
        losses = 0
        for item in self.closed_positions[:8]:
            if item.get("desk") != desk or item.get("symbol") != symbol:
                continue
            if float(item.get("pnl_pct", 0.0) or 0.0) <= 0:
                losses += 1
            if losses >= 2:
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
        stop_like = sum(
            1
            for item in recent[:5]
            if str(item.get("closed_reason", "") or "") in {"stop_hit", "early_failure"}
        )
        pnl_total = sum(float(item.get("pnl_pct", 0.0) or 0.0) for item in recent[:5])
        return stop_like >= 2 or pnl_total <= -2.0

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
        if desk == "us":
            return losses >= 3 or realized <= -2.0
        if desk == "crypto":
            return losses >= 3 or realized <= -1.0
        return losses >= 3 or realized <= -1.2

    def _desk_chronic_drawdown(self, desk: str) -> bool:
        recent = self._desk_recent_trades(desk, limit=5)
        if len(recent) < 4:
            return False
        realized = sum(float(item.get("pnl_pct", 0.0) or 0.0) for item in recent)
        wins = sum(1 for item in recent if float(item.get("pnl_pct", 0.0) or 0.0) > 0)
        losses = sum(1 for item in recent if float(item.get("pnl_pct", 0.0) or 0.0) < 0)
        stop_like = sum(
            1
            for item in recent
            if str(item.get("closed_reason", "") or "") in {"stop_hit", "early_failure"}
        )
        if desk == "us":
            return wins == 0 and losses >= 4 and realized <= -2.0
        if desk == "crypto":
            return wins <= 1 and losses >= 4 and realized <= -1.6
        return wins <= 1 and losses >= 4 and (realized <= -2.4 or stop_like >= 3)

    def _desk_performance_lock(self, desk: str) -> bool:
        desk_stats = (self.daily_summary.get("desk_stats", {}) or {}).get(desk, {}) or {}
        closed_positions = int(desk_stats.get("closed_positions", 0) or 0)
        wins = int(desk_stats.get("wins", 0) or 0)
        losses = int(desk_stats.get("losses", 0) or 0)
        realized = float(desk_stats.get("realized_pnl_pct", 0.0) or 0.0)
        win_rate = float(desk_stats.get("win_rate", 0.0) or 0.0)
        if closed_positions < 4:
            return False
        if desk == "us":
            return wins == 0 and losses >= 4 and realized <= -2.0
        if desk == "crypto":
            return win_rate < 25.0 and losses >= 4 and realized <= -1.5
        return win_rate < 25.0 and losses >= 5 and realized <= -2.5

    def _desk_offense_state(self, desk: str) -> dict:
        desk_stats = (self.daily_summary.get("desk_stats", {}) or {}).get(desk, {}) or {}
        capital_profile = (self.strategy_book.get("capital_profile", {}) or {}) if self.strategy_book else {}
        desk_multiplier = float((capital_profile.get("desk_multipliers", {}) or {}).get(desk, 1.0) or 1.0)
        realized = float(desk_stats.get("realized_pnl_pct", 0.0) or 0.0)
        win_rate = float(desk_stats.get("win_rate", 0.0) or 0.0)
        closed_positions = int(desk_stats.get("closed_positions", 0) or 0)
        open_notional = float(desk_stats.get("open_notional_pct", 0.0) or 0.0)

        score = 50.0
        score += max(min(realized * 7.5, 18.0), -22.0)
        score += max(min((win_rate - 50.0) * 0.35, 12.0), -14.0)
        score += min(closed_positions * 1.8, 8.0)
        score += (desk_multiplier - 1.0) * 50.0
        score -= max(open_notional - 0.55, 0.0) * 18.0
        score = round(max(min(score, 100.0), 0.0), 1)

        if score >= 67:
            return {"score": score, "tone": "press", "size_multiplier": 1.1, "entry_allowed": True}
        if score >= 52:
            return {"score": score, "tone": "balanced", "size_multiplier": 1.0, "entry_allowed": True}
        return {"score": score, "tone": "cooldown", "size_multiplier": 0.75, "entry_allowed": desk == "crypto" and realized > 0}

    def _desk_stop_pressure(self, desk: str) -> str:
        recent = self._desk_recent_trades(desk, limit=6)
        if len(recent) < 2:
            return "none"
        stop_like_count = 0
        stop_like_pnl = 0.0
        for item in recent:
            reason = str(item.get("closed_reason", "") or "")
            if reason in {"stop_hit", "early_failure"}:
                stop_like_count += 1
                stop_like_pnl += float(item.get("pnl_pct", 0.0) or 0.0)
        if stop_like_count >= 3 or stop_like_pnl <= -3.0:
            return "high"
        if stop_like_count >= 2 or stop_like_pnl <= -1.5:
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
            reason = str(item.get("closed_reason", "") or "")
            if reason in {"stop_hit", "early_failure"}:
                stop_like_count += 1
                stop_like_pnl += float(item.get("pnl_pct", 0.0) or 0.0)
        if stop_like_count >= 2 or stop_like_pnl <= -1.8:
            return "high"
        if stop_like_count >= 1 or stop_like_pnl <= -0.8:
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
            if closed_reason in {"stop_hit", "early_failure"}:
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
        base_notional = self._size_to_notional(base_size)
        offense_scaled_base = round(base_notional * float(desk_offense.get("size_multiplier", 1.0) or 1.0), 2)
        risk_scaled_notional = round(offense_scaled_base * max(min(self.risk_budget, 1.0), 0.0), 2)
        desk_stop_pressure = self._desk_stop_pressure(desk)
        symbol_stop_pressure = self._symbol_stop_pressure(desk, symbol)
        downgrade_notes: list[str] = []
        if original_action == "attack_opening_drive" and (desk_stop_pressure != "none" or symbol_stop_pressure != "none"):
            action = "selective_probe"
            downgrade_notes.append(f"{desk} desk risk pattern downgraded attack_opening_drive to selective_probe")
        if action in {"probe_longs", "selective_probe"} and symbol_stop_pressure == "high":
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
        pnl_map = {
            "probe_longs": 0.45,
            "attack_opening_drive": 0.6,
            "selective_probe": 0.2,
            "watchlist_only": 0.0,
            "reduce_risk": 0.0,
            "stand_by": 0.0,
            "capital_preservation": 0.0,
            "pre_market_watch": 0.0,
        }
        pnl_estimate_pct = pnl_map.get(action, 0.0)
        actionable_entries = {"probe_longs", "attack_opening_drive", "selective_probe"}
        actionable_exits = {"reduce_risk", "capital_preservation"}
        existing_open = self._has_open_position(desk, symbol)
        cooldown_loss = self._recent_loss_cooldown(desk, symbol)
        repeated_loss_block = self._repeated_loss_block(desk, symbol)
        extended_symbol_block = self._extended_symbol_block(desk, symbol)
        desk_loss_pressure = self._desk_loss_pressure(desk)
        desk_chronic_drawdown = self._desk_chronic_drawdown(desk)
        desk_performance_lock = self._desk_performance_lock(desk)
        desk_recovery_ready = self._desk_recovery_ready(desk)
        desk_offense_block = not bool(desk_offense.get("entry_allowed", True)) and action in actionable_entries
        blocked_by_stop_pressure = desk_stop_pressure == "high" and action in actionable_entries
        blocked_by_risk = not self.allow_new_entries and action in actionable_entries
        blocked_by_desk_drawdown = (desk_chronic_drawdown or desk_performance_lock) and action in actionable_entries
        desk_open_count = self._desk_open_count(desk)
        desk_open_notional = self._desk_open_notional(desk)
        gross_open_notional = self._gross_open_notional()
        max_positions, max_desk_notional = self._desk_limits(desk)
        total_notional_cap = 1.05 if self.risk_budget >= 0.4 else 0.8 if self.risk_budget >= 0.25 else 0.55
        desk_position_cap_hit = desk_open_count >= max_positions
        desk_notional_cap_hit = (desk_open_notional + notional_pct) > max_desk_notional and action in actionable_entries
        gross_notional_cap_hit = (gross_open_notional + notional_pct) > total_notional_cap and action in actionable_entries
        exit_status = "planned" if action in actionable_exits and existing_open else "idle"
        meta = {
            "symbol": symbol,
            "reference_price": reference_price,
            "notional_pct": notional_pct,
            "status": "planned"
            if action in actionable_entries
            and notional_pct > 0
            and not existing_open
            and not cooldown_loss
            and not repeated_loss_block
            and not extended_symbol_block
            and not desk_loss_pressure
            and not desk_chronic_drawdown
            and not desk_performance_lock
            and not desk_offense_block
            and not blocked_by_stop_pressure
            and not blocked_by_risk
            and not desk_position_cap_hit
            and not desk_notional_cap_hit
            and not gross_notional_cap_hit
            else exit_status,
            "pnl_estimate_pct": pnl_estimate_pct,
        }
        notes = list(plan.get("notes", [])) + rotation_notes + downgrade_notes
        if action in actionable_entries and base_size != size:
            if offense_scaled_base != base_notional:
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
            notes.append(f"{desk} desk loss pressure active, new entries paused")
        if desk_chronic_drawdown:
            notes.append(f"{desk} desk under chronic drawdown lock, new entries require manual recovery")
        if desk_performance_lock:
            notes.append(f"{desk} desk blocked by poor desk-level performance snapshot")
        if desk_offense_block:
            notes.append(f"{desk} desk offense cooldown active, skip new entries this cycle")
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

    def run(self) -> AgentResult:
        crypto_plan = self.strategy_book.get("crypto_plan", {})
        korea_plan = self.strategy_book.get("korea_plan", {})
        us_plan = self.strategy_book.get("us_plan", {})
        orders = [
            self._plan_to_order("crypto", crypto_plan).model_dump(),
            self._plan_to_order("korea", korea_plan).model_dump(),
            self._plan_to_order("us", us_plan).model_dump(),
        ]
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

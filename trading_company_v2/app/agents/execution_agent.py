from __future__ import annotations

from app.agents.base import BaseAgent
from app.config import settings
from app.core.models import AgentResult, PaperOrder
from app.core.state_store import save_shadow_signal
from app.services.upbit_stream_cache import summarize_stream_momentum


STOP_LIKE_EXIT_REASONS = {
    # True hard stops: position hit the max-loss threshold immediately / near-immediately.
    # These count toward stop_pressure and candidate penalties.
    # Time-gated managed exits (trend_invalid, downtrend, no_lift, failed_ignition, etc.) are NOT
    # included here — they represent controlled risk management after holding a valid duration,
    # not immediate-entry failures. Including them caused cascading throttling after any losing run.
    "stop_hit",
    "rapid_stop_hit",
    "early_failure",
    "rapid_tick_failed_start",
    "rapid_obvious_trend_fail",
    "rapid_range_impulse_fail",
    "rapid_failed_start",      # 4 min + peak ≤ 0.05% + pnl ≤ -0.75%  (never showed life)
    "rapid_repeat_symbol_failure",  # repeated failure on same symbol
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
    def _infer_strategy_id(action: str = "", focus: str = "", entry_profile: str = "", desk: str = "crypto") -> str:
        text = f"{entry_profile} {action} {focus}".lower()
        ns = desk if desk in {"crypto", "korea", "us"} else "crypto"
        # Korea-specific patterns
        if ns == "korea":
            if "inst_foreign_catalyst" in text:
                return "korea.inst_foreign_catalyst"
            if "inst_foreign_breakout" in text:
                return "korea.inst_foreign_breakout"
            if "inst_foreign_gap" in text:
                return "korea.inst_foreign_gap"
            if "catalyst_gap" in text:
                return "korea.catalyst_gap"
            if "new_high_breakout" in text:
                return "korea.new_high_breakout"
            if "mongtata_airborne" in text:
                return "korea.mongtata_airborne"
            if "rsi2_mean_reversion" in text:
                return "korea.rsi2_mean_reversion"
            if "nday_pullback" in text:
                return "korea.nday_pullback"
            if "gap_momentum" in text:
                return "korea.gap_momentum"
            if "breakout" in text or "돌파" in text:
                return "korea.breakout"
            if "gap" in text or "갭" in text:
                return "korea.gap_up"
            return f"korea.{entry_profile or action or 'unknown'}"
        # US patterns
        if ns == "us":
            return f"us.{entry_profile or action or 'unknown'}"
        # Crypto patterns
        if "eth_4h_breakout" in text:
            return "crypto.eth_4h_breakout"
        if "mongtata_airborne" in text:
            return "crypto.mongtata_airborne"
        if "rsi2_mean_reversion" in text:
            return "crypto.rsi2_mean_reversion"
        if "nday_pullback" in text:
            return "crypto.nday_pullback"
        if "smart_money_flow" in text:
            return "crypto.smart_money_flow"
        if "ranging_strength_follow" in text:
            return "crypto.ranging_strength_follow"
        if "ranging_momentum_leader" in text:
            return "crypto.ranging_momentum_leader"
        if "bb_squeeze_breakout" in text or "bb 스퀴즈" in text:
            return "crypto.bb_squeeze_breakout"
        if "range_scalp" in text:
            return "crypto.range_scalp"
        if "range_impulse" in text:
            return "crypto.range_impulse"
        if "obvious_trend" in text:
            return "crypto.obvious_trend"
        if "trend_ignition" in text or "tick ignition" in text or "tick entry" in text:
            return "crypto.tick_ignition"
        if "pullback entry" in text or "retracement near ema" in text:
            return "crypto.pullback_entry"
        if "trend pullback" in text:
            return "crypto.trend_pullback"
        if "direct entry" in text:
            return "crypto.direct_entry"
        if "composite signal" in text:
            return "crypto.composite_entry"
        if "stream ignition" in text:
            return "crypto.stream_entry"
        if "candidate-specific" in text or "multi-coin entry" in text:
            return "crypto.candidate_rotation"
        if "balanced" in text or "단타" in text:
            return "crypto.balanced_swing"
        if "offense" in text or "공격적" in text:
            return "crypto.offense_probe"
        return f"crypto.{entry_profile or action or 'unknown'}"

    # 영구 차단 전략: health window 밖으로 벗어나도 재활성화 불가
    # candidate_rotation: cycle-path 구조적 실패 (0%win, 100%peak0, hot-path 전용 아키텍처와 충돌)
    _PERMANENTLY_DISABLED: frozenset[str] = frozenset({
        "crypto.candidate_rotation",
        # 2026-05-14: quarantined after live paper peak=0 loss streak.
        "crypto.ranging_momentum_leader",
        "crypto.ema_bounce",
        # korea.pyramid: re-enabled 2026-05-19 with profit-floor lock mechanism
        # (peak_pnl_pct raised before pyramid entry → base profit protected)
    })

    _RETIRED_STRATEGY_IDS: frozenset[str] = frozenset({
        "crypto.candidate_rotation",
        "crypto.ranging_momentum_leader",
        "crypto.ema_bounce",
        # korea.pyramid: re-enabled 2026-05-19
    })

    def _is_retired_strategy_trade(self, item: dict) -> bool:
        strategy_id = str(item.get("strategy_id", "") or "")
        entry_profile = str(item.get("entry_profile", "") or "").lower()
        focus = str(item.get("focus", "") or "").lower()
        if strategy_id in self._RETIRED_STRATEGY_IDS:
            return True
        return False

    def _strategy_disabled(self, strategy_id: str) -> dict | None:
        if strategy_id in self._PERMANENTLY_DISABLED:
            return {"strategy_id": strategy_id, "win_rate": 0.0, "capital_pnl_pct": -99.0,
                    "peak0_pct": 100.0, "health": "disabled_candidate", "permanent": True}
        for item in self.daily_summary.get("strategy_performance_stats", []) or []:
            if str(item.get("strategy_id", "") or "") == strategy_id and item.get("health") == "disabled_candidate":
                return item
        return None

    def _strategy_stats(self, strategy_id: str) -> dict:
        for item in self.daily_summary.get("strategy_performance_stats", []) or []:
            if str(item.get("strategy_id", "") or "") == strategy_id:
                return item
        return {}

    def _strategy_recovery_allowed(self, desk: str, strategy_id: str, action: str) -> bool:
        """Let proven strategies keep trading small while weak strategies stay quarantined."""
        if action not in {"probe_longs", "attack_opening_drive", "selective_probe"}:
            return False
        if self._strategy_disabled(strategy_id):
            return False
        stats = self._strategy_stats(strategy_id)
        if not stats:
            return False
        health = str(stats.get("health", "") or "")
        count = int(stats.get("count", stats.get("closed_positions", 0)) or 0)
        win_rate = float(stats.get("win_rate", 0.0) or 0.0)
        capital_pnl = float(stats.get("capital_pnl_pct", 0.0) or 0.0)
        raw_pnl = float(stats.get("raw_pnl_pct", stats.get("realized_pnl_pct", 0.0)) or 0.0)
        peak0_pct = float(stats.get("peak0_pct", 0.0) or 0.0)
        if health == "disabled_candidate":
            return False
        # Candidate strategies that are already net positive should not be
        # suffocated by desk-level losses from unrelated experimental routes.
        if health == "candidate" and count >= 2 and (capital_pnl > 0 or raw_pnl > 0) and win_rate >= 45.0:
            return True
        # 2026-06-01: stale_exit(pnl=0) 이 loss로 집계되어 WR이 실제보다 낮아지는 문제 보정
        # raw_pnl > 0 (손실보다 수익이 큰 전략)이고 WR >= 30%면 회복 허용
        # Korea 기준: avg_win > avg_loss 이면 33% WR도 기대값 양수 (avg_win 2.5% / avg_loss 1.0% → PF=2.5)
        if health == "candidate" and count >= 4 and raw_pnl > 0 and win_rate >= 30.0:
            return True
        # 버그성 청산(rapid guard false exit 등)으로 P&L이 일시 음수가 됐지만
        # WR·거래수 기준으로 검증된 전략: stop_pressure 데드락 방지
        # 조건: n≥4, WR≥45%, raw_pnl≥-2.5% (전략 파탄 수준 아님)
        if health == "candidate" and count >= 4 and win_rate >= 45.0 and raw_pnl >= -2.5:
            return True
        if desk == "korea" and strategy_id in {"korea.selective_probe", "korea.attack_opening_drive"}:
            # selective_probe: 38% WR이지만 이익 크기>손실 크기 패턴, 회복 허용
            # attack_opening_drive: 67% WR, 검증됨
            return count >= 2 and win_rate >= 35.0 and raw_pnl >= -1.5
        if desk == "crypto" and strategy_id in {"crypto.selective_probe", "crypto.range_scalp"}:
            return count >= 2 and win_rate >= 50.0 and peak0_pct <= 50.0 and raw_pnl >= 0
        return False

    @staticmethod
    def _is_stop_like_exit(item: dict) -> bool:
        reason = str(item.get("closed_reason", "") or "")
        pnl = float(item.get("pnl_pct", 0.0) or 0.0)
        # Some Korea paper exits are tagged as stop_hit after a price refresh while
        # ending flat/slightly positive. Do not let those poison desk stop-pressure.
        if pnl >= -0.05:
            return False
        if reason in STOP_LIKE_EXIT_REASONS:
            return True
        return reason == "stale_exit" and pnl <= -0.5

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
        if desk == "korea":
            # Max 3 concurrent — supports: 1 open_reversal + 1 breakout + 1 close_drive
            # Pyramid positions are separate and use a 4th slot tracked independently.
            return 3, 1.8
        return 2, 1.2

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
        # gap_candidates = KOSDAQ 갭업 + korea stock desk watchlist 브레이크아웃 포함
        for item in (
            self.market_snapshot.get("gap_candidates", [])
            + self.market_snapshot.get("stock_leaders", [])
            + self.market_snapshot.get("close_drive_candidates", [])
            + self.market_snapshot.get("gap_fill_candidates", [])
            + self.market_snapshot.get("pullback_ma_candidates", [])
        ):
            if str(item.get("ticker", "")).strip() == symbol:
                price = float(item.get("current_price") or 0.0)
                if price > 0:
                    return price
        return 0.0

    def _candidate_snapshot(self, desk: str, symbol: str) -> dict:
        """Return the latest market snapshot row for a symbol.

        Korea plans often arrive as a basket of candidate symbols. The original
        plan focus names only the top candidate, so each expanded order needs
        its own display metadata to avoid misleading dashboard labels.
        """
        if not symbol:
            return {}
        if desk == "crypto":
            pools = (self.market_snapshot.get("crypto_leaders", []) or [])
            key = "market"
        elif desk == "us":
            pools = (self.market_snapshot.get("us_leaders", []) or [])
            key = "ticker"
        else:
            pools = (
                (self.market_snapshot.get("gap_candidates", []) or [])
                + (self.market_snapshot.get("stock_leaders", []) or [])
                + (self.market_snapshot.get("close_drive_candidates", []) or [])
                + (self.market_snapshot.get("gap_fill_candidates", []) or [])
                + (self.market_snapshot.get("pullback_ma_candidates", []) or [])
                + (self.market_snapshot.get("gap_momentum_candidates", []) or [])
                + (self.market_snapshot.get("inst_foreign_candidates", []) or [])
                + (self.market_snapshot.get("catalyst_gap_candidates", []) or [])
            )
            key = "ticker"
        for item in pools:
            if str(item.get(key, "")).strip() == symbol:
                return dict(item)
        return {}

    def _apply_korea_candidate_snapshot(self, plan: dict, symbol: str) -> dict:
        """Rewrite a multi-candidate Korea plan into a symbol-specific order plan."""
        snapshot = self._candidate_snapshot("korea", symbol)
        if not snapshot:
            return plan

        mapped = dict(plan)
        name = str(snapshot.get("name", "") or symbol)
        signal = float(snapshot.get("signal_score", mapped.get("signal_score", 0.0)) or 0.0)
        candidate_score = float(snapshot.get("candidate_score", mapped.get("quality_score", 0.0)) or 0.0)
        gap_pct = float(snapshot.get("gap_pct", 0.0) or 0.0)
        vol_ratio = float(snapshot.get("vol_ratio", snapshot.get("volume_ratio", 0.0)) or 0.0)
        base_focus = str(mapped.get("focus", "") or "").lower()

        if "gap_fill" in base_focus:
            focus = f"gap_fill: {name} ({symbol}) gap {gap_pct:.1f}% mean-reversion entry"
            entry_profile = "gap_fill"
            strategy_id = "korea.gap_fill"
        elif "open_reversal" in base_focus:
            focus = f"open_reversal: {name} ({symbol}) opening exhaustion reversal"
            entry_profile = "open_reversal"
            strategy_id = "korea.open_reversal"
        elif "close_drive" in base_focus:
            focus = f"close_drive: {name} ({symbol}) close-drive strength"
            entry_profile = "close_drive"
            strategy_id = "korea.close_drive"
        elif "pullback_ma" in base_focus:
            focus = f"pullback_ma: {name} ({symbol}) MA pullback continuation"
            entry_profile = "pullback_ma"
            strategy_id = "korea.pullback_ma"
        elif "inst_foreign_breakout" in base_focus:
            _fnet = float(snapshot.get("foreign_net_bn", 0.0) or 0.0)
            focus = (
                f"inst_foreign_breakout: {name} ({symbol}) "
                f"기관+외국인 신고점돌파 foreign_net={_fnet:+.1f}억"
            )
            entry_profile = "inst_foreign_breakout"
            strategy_id = "korea.inst_foreign_breakout"
        elif "inst_foreign_gap" in base_focus:
            _fnet = float(snapshot.get("foreign_net_bn", 0.0) or 0.0)
            focus = (
                f"inst_foreign_gap: {name} ({symbol}) "
                f"기관+외국인 갭모멘텀 foreign_net={_fnet:+.1f}억"
            )
            entry_profile = "inst_foreign_gap"
            strategy_id = "korea.inst_foreign_gap"
        elif "catalyst_gap" in base_focus or "catalyst_gap" in str(mapped.get("focus_tag", "") or ""):
            _cg_gap = float(snapshot.get("gap_pct", 0.0) or 0.0)
            _cg_chg = float(snapshot.get("chg1d", 0.0) or 0.0)
            focus = (
                f"catalyst_gap: {name} ({symbol}) "
                f"강한 갭업 gap={_cg_gap:.1f}% chg={_cg_chg:.1f}%"
            )
            entry_profile = "catalyst_gap"
            strategy_id = "korea.catalyst_gap"
        elif "gap_momentum" in base_focus:
            focus = f"gap_momentum: {name} ({symbol}) S15 gap momentum breakout"
            entry_profile = "gap_momentum"
            strategy_id = "korea.gap_momentum"
        elif "breakout" in base_focus:
            # plan.focus_tag가 최우선, 없으면 snapshot.focus_tag, 없으면 base_focus 텍스트로 판단
            _focus_tag = str(
                mapped.get("focus_tag", "") or snapshot.get("focus_tag", "") or ""
            ).lower()
            if _focus_tag == "new_high_breakout" or "new_high_breakout" in base_focus:
                # 2026-05-19: 신고점 돌파 전략 B — 60-day high + vol surge + RSI 55-80
                # 백테스트 검증: WR 84.6%, Sharpe 6.16, 연 +89.5%, MDD -4.0% (152종목, 3년)
                focus = f"new_high_breakout: {name} ({symbol}) 60-day new high breakout"
                entry_profile = "new_high_breakout"
                strategy_id = "korea.new_high_breakout"
            else:
                # 백테스트 미검증 일반 breakout — 진입 차단
                return plan
        elif "opening drive" in base_focus or "attack_opening_drive" in str(mapped.get("entry_profile", "")).lower():
            focus = f"opening_drive: {name} ({symbol}) opening drive follow-through"
            entry_profile = "attack_opening_drive"
            strategy_id = "korea.attack_opening_drive"
        else:
            entry_profile = str(mapped.get("entry_profile", "") or "selective_probe")
            strategy_id = str(mapped.get("strategy_id", "") or "korea.selective_probe")
            if not strategy_id.startswith("korea."):
                strategy_id = f"korea.{entry_profile or 'selective_probe'}"
            if entry_profile in {"quality_follow_probe", "mid_session_quality_probe"}:
                focus = f"{entry_profile}: {name} ({symbol}) selective probe while confirmation improves"
            else:
                focus = f"{name} ({symbol}) selective probe while confirmation improves"

        mapped["focus"] = focus
        mapped["entry_profile"] = entry_profile
        mapped["strategy_id"] = strategy_id
        mapped["signal_score"] = signal
        mapped["candidate_score"] = candidate_score
        mapped["candidate_symbols"] = []
        # ATR from candidate snapshot — used for dynamic SL tightening at exit
        _snap_atr = float(snapshot.get("atr_pct", 0.0) or 0.0)
        if _snap_atr > 0:
            mapped["atr_pct"] = _snap_atr
        notes = list(mapped.get("notes", []) or [])
        notes.append(
            f"korea candidate-specific: {name} {symbol} signal={signal:.2f} "
            f"candidate={candidate_score:.2f} gap={gap_pct:.2f}% vol={vol_ratio:.1f}x"
        )
        mapped["notes"] = notes
        return mapped

    def _recent_loss_cooldown(self, desk: str, symbol: str) -> bool:
        """동일 종목 최근 손실 쿨다운.

        2026-05-26: 체크 범위 4→10건 확장.
        근거: 039030(이오테크닉스) 1차 손실(5/24) 후 2차 진입(5/26)이 쿨다운 4건 창 밖으로
        벗어나 재진입 → -1.92% 추가 손실. 10건 체크로 확장해 동일 종목 연속 손실 방지.
        """
        if not symbol:
            return False
        recent = [item for item in self.closed_positions if not self._is_retired_strategy_trade(item)][:10]
        for item in recent:
            if item.get("desk") != desk or item.get("symbol") != symbol:
                continue
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            if desk == "crypto":
                if self._is_stop_like_exit(item) and pnl <= -1.0:
                    return True
                continue
            # 2026-06-01: pnl <= 0 → pnl < -0.3 변경
            # 근거: 진입 후 trailing stop이 breakeven으로 당겨져 0% 청산된 경우
            # (peak +1% 후 79000 진입/79000 청산)도 손실로 간주해 쿨다운 발동.
            # 이는 실제로 자본을 잃지 않은 정상적인 신호에 대해 재진입을 무기한 차단.
            # 한국 주식은 -0.3% 이상 실손이 있을 때만 쿨다운 적용.
            if pnl < -0.3:
                return True
        return False

    def _quality_reentry_override(self, desk: str, symbol: str, plan: dict) -> bool:
        """Let high-quality Korea continuation setups re-enter after one small shakeout.

        A same-symbol loss should not permanently suppress a fresh quality follow
        signal. Repeated/catastrophic losses are still handled by the regular
        repeated-loss and extended-block gates.
        """
        if desk != "korea" or not symbol:
            return False
        focus = str(plan.get("focus", "") or "").lower()
        profile = str(plan.get("entry_profile", plan.get("entry_path", "")) or "").lower()
        strategy_id = str(plan.get("strategy_id", "") or "").lower()
        is_quality_follow = any(
            key in f"{focus} {profile} {strategy_id}"
            for key in ("quality_follow_probe", "mid_session_quality_probe", "attack_opening_drive", "opening_drive")
        )
        if not is_quality_follow:
            return False
        signal_score = float(plan.get("signal_score", 0.0) or 0.0)
        quality_score = float(plan.get("quality_score", 0.0) or 0.0)
        candidate_score = float(plan.get("candidate_score", 0.0) or 0.0)
        strong_current_signal = (
            signal_score >= 0.68
            or quality_score >= 0.82
            or candidate_score >= 0.58
        )
        if not strong_current_signal:
            return False

        for item in self.closed_positions[:8]:
            if item.get("desk") != desk or item.get("symbol") != symbol:
                continue
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            if pnl <= -1.6:
                return False
            return True
        return False

    def _desk_recent_trades(self, desk: str, limit: int = 6) -> list[dict]:
        recent: list[dict] = []
        for item in self.closed_positions:
            if item.get("desk") != desk or self._is_retired_strategy_trade(item):
                continue
            recent.append(item)
            if len(recent) >= limit:
                break
        return recent

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
        recent = [item for item in self.closed_positions if not self._is_retired_strategy_trade(item)]
        for item in recent[:8]:
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
            if item.get("desk") == desk and item.get("symbol") == symbol and not self._is_retired_strategy_trade(item)
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
        if desk == "us":
            return realized <= -4.0 or (losses >= 3 and realized <= -2.0)
        if desk == "crypto":
            return realized <= -4.0 or (losses >= 3 and realized <= -2.0)
        return realized <= -5.0 or (losses >= 3 and realized <= -3.0)
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
        # Korea: stop_like >= 4 (기존 3에서 완화, 2026-06-01)
        # stop_hit + stale_loss + early_failure 3개 조합으로 데드락 발생 → 진짜 연속 손절(4회) 기준으로 상향
        return wins <= 1 and losses >= 4 and (realized <= -7.5 or stop_like >= 4)  # 4 × -2.5%

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

        eligible_recent = self._desk_recent_trades(desk, limit=12)
        if eligible_recent:
            closed_positions = len(eligible_recent)
            wins = sum(1 for item in eligible_recent if float(item.get("pnl_pct", 0.0) or 0.0) > 0)
            realized = sum(
                float(item.get("capital_pnl_pct", item.get("pnl_pct", 0.0)) or 0.0)
                for item in eligible_recent
            )
            win_rate = (wins / closed_positions) * 100.0 if closed_positions else 0.0

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
            if item.get("desk") == desk and item.get("symbol") == symbol and not self._is_retired_strategy_trade(item)
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
            if item.get("desk") == desk and item.get("symbol") == symbol and not self._is_retired_strategy_trade(item)
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
            if item.get("desk") == desk and item.get("symbol") == symbol and not self._is_retired_strategy_trade(item)
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
            reentry_override = self._quality_reentry_override(desk, symbol, plan)
            cooldown_loss = self._recent_loss_cooldown(desk, symbol) and not reentry_override
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
        effective_risk_budget = max(min(self.risk_budget, 1.0), 0.0)
        if desk == "korea":
            # Korea has a separate edge profile from crypto. Do not let crypto-driven
            # global caution shrink a profitable/high-win Korea desk into dust size.
            if desk_offense.get("tone") == "press":
                effective_risk_budget = max(effective_risk_budget, 0.65)
            elif desk_offense.get("tone") == "balanced":
                effective_risk_budget = max(effective_risk_budget, 0.50)
        risk_scaled_notional = round(volatility_scaled_base * effective_risk_budget, 2)
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
        if (
            desk == "korea"
            and action in {"probe_longs", "attack_opening_drive", "selective_probe"}
            and desk_offense.get("tone") == "press"
            and desk_stop_pressure == "none"
        ):
            # Recent Korea entries have high hit rate but low capital contribution
            # because multi-candidate orders were scaled to ~0.04-0.07x.
            floor = 0.16 if action == "attack_opening_drive" else 0.12
            scaled_notional_pct = max(scaled_notional_pct, floor)
        if (
            desk == "korea"
            and action in {"probe_longs", "attack_opening_drive", "selective_probe"}
            and desk_stop_pressure != "high"
            and any(key in str(plan.get("focus", "") or "") for key in ("quality_follow_probe", "mid_session_quality_probe"))
        ):
            scaled_notional_pct = max(scaled_notional_pct, 0.06)
        # Recovery 전략 최소 size 보장: 연패 후 검증된 전략이 너무 작은 size로 차단되지 않도록
        # 근거: risk_budget=0.18 시 scaled_notional_pct=0.04~0.06x → 진입해도 회복 불가
        # strategy_recovery_allowed인 경우 최소 0.10x 보장 (stop-pressure 높을 때 제외)
        # NOTE: strategy_recovery_allowed는 아래(887줄)에서 최종 계산되지만, size floor에도 필요하므로
        # entry_profile/strategy_id를 미리 계산해 사전 결정. 아래 887줄에서 동일 값으로 재계산됨.
        _ep_early = str(plan.get("entry_profile", plan.get("entry_path", "")) or "")
        _sid_early = str(plan.get("strategy_id", "") or self._infer_strategy_id(action, str(plan.get("focus", "")), _ep_early, desk))
        if not _ep_early:
            _ep_early = _sid_early.split(".", 1)[-1] if "." in _sid_early else _sid_early
        strategy_recovery_allowed = self._strategy_recovery_allowed(desk, _sid_early, action)
        if strategy_recovery_allowed and scaled_notional_pct < 0.10 and desk_stop_pressure != "high":
            scaled_notional_pct = 0.10
        size = f"{scaled_notional_pct:.2f}x"
        notional_pct = scaled_notional_pct
        reference_price = self._reference_price(desk, symbol)
        # Korea: market_snapshot에 없는 종목(신고점 돌파 중소형주 등)은 plan의 가격 사용
        # 버그 방지: symbol rotation 시 primary 종목 가격을 다른 종목에 잘못 적용하지 않도록
        # candidate_prices 맵 우선 조회 → 없으면 primary 종목에 한해 reference_price fallback
        if reference_price <= 0 and desk == "korea":
            _cand_prices = plan.get("candidate_prices", {}) or {}
            if symbol in _cand_prices:
                # rotation된 종목의 실제 가격 사용 (잘못된 primary 가격 방지)
                reference_price = float(_cand_prices[symbol] or 0.0)
            elif symbol == str(plan.get("symbol", "")):
                # primary 종목 그대로 진입 시에만 plan reference_price 허용
                reference_price = float(plan.get("reference_price", 0.0) or 0.0)
        pnl_estimate_pct = self._expected_pnl_pct(desk, action)
        actionable_entries = {"probe_longs", "attack_opening_drive", "selective_probe"}
        actionable_exits = {"reduce_risk", "capital_preservation"}
        existing_open = self._has_open_position(desk, symbol)
        reentry_override = self._quality_reentry_override(desk, symbol, plan)
        cooldown_loss = self._recent_loss_cooldown(desk, symbol) and not reentry_override
        repeated_loss_block = self._repeated_loss_block(desk, symbol)
        extended_symbol_block = self._extended_symbol_block(desk, symbol)
        desk_loss_pressure = self._desk_loss_pressure(desk)
        # crypto_recovery_mode: Korea 병행 여부와 무관하게 crypto는 항상 loss_pressure 비차단
        # (Korea 활성 시 active_desk_set != {"crypto"} 조건으로 crypto도 블록되는 문제 수정)
        crypto_recovery_mode = desk == "crypto" and "crypto" in settings.active_desk_set
        desk_loss_pressure_blocks = desk_loss_pressure and not crypto_recovery_mode
        desk_chronic_drawdown = self._desk_chronic_drawdown(desk)
        desk_performance_lock = self._desk_performance_lock(desk)
        desk_recovery_ready = self._desk_recovery_ready(desk)
        desk_offense_block = not bool(desk_offense.get("entry_allowed", True)) and action in actionable_entries
        symbol_edge_block = (
            not bool(symbol_edge.get("entry_allowed", True))
            and action in actionable_entries
            and not reentry_override
        )
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
        # Pyramid entries don't count against regular desk position cap
        _is_pyramid_entry = "pyramid" in str(plan.get("entry_profile", "") or "").lower() or "pyramid" in str(plan.get("focus", "") or "").lower()
        if _is_pyramid_entry:
            # Pyramid slot: max 1 pyramid per desk (they're tiny 0.20x follow-ons)
            _pyramid_open = sum(1 for p in self.open_positions if p.get("desk") == desk and "pyramid" in (p.get("entry_profile", "") or "").lower())
            desk_position_cap_hit = _pyramid_open >= 1
        else:
            # Regular slot: exclude pyramid positions from count
            _non_pyramid_open = sum(1 for p in self.open_positions if p.get("desk") == desk and "pyramid" not in (p.get("entry_profile", "") or "").lower())
            desk_position_cap_hit = _non_pyramid_open >= max_positions
        # Per-strategy Korea slot limits (prevent duplicate open_reversal / close_drive)
        if desk == "korea" and action in actionable_entries and not _is_pyramid_entry:
            _focus_lower = str(plan.get("focus", "") or "").lower()
            def _korea_strategy_open(key: str) -> int:
                return sum(1 for p in self.open_positions if p.get("desk") == "korea" and key in (p.get("focus", "") or "").lower())
            if "open_reversal" in _focus_lower and _korea_strategy_open("open_reversal") >= 1:
                desk_position_cap_hit = True
            if "close_drive" in _focus_lower and _korea_strategy_open("close_drive") >= 1:
                desk_position_cap_hit = True
            # 동일 종목 중복 진입 차단 — 064760/131290 같은 같은 심볼 반복 진입 방지
            if symbol:
                _same_symbol_open = sum(
                    1 for p in self.open_positions
                    if p.get("desk") == "korea" and p.get("symbol", "") == symbol and p.get("status") == "open"
                )
                if _same_symbol_open >= 1:
                    desk_position_cap_hit = True
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
        entry_profile = str(plan.get("entry_profile", plan.get("entry_path", "")) or "")
        strategy_id = str(plan.get("strategy_id", "") or self._infer_strategy_id(action, str(plan.get("focus", "")), entry_profile, desk))
        if not entry_profile:
            entry_profile = strategy_id.split(".", 1)[-1] if "." in strategy_id else strategy_id
        strategy_disabled = self._strategy_disabled(strategy_id) if action in actionable_entries else None
        strategy_recovery_allowed = self._strategy_recovery_allowed(desk, strategy_id, action)
        if strategy_recovery_allowed:
            # 검증된 전략: loss_pressure + chronic_drawdown + performance_lock + stop_pressure 모두 해제
            # chronic/performance_lock이 loss_pressure만 풀고 chronic은 블록 → 회복 기회 자체가 없는 데드락 방지
            # blocked_by_stop_pressure: 버그성 청산이 stop_like를 오염시킨 경우 데드락 차단
            desk_loss_pressure_blocks = False
            desk_chronic_drawdown = False
            desk_performance_lock = False
            blocked_by_stop_pressure = False  # stop_pressure 데드락 해제 (검증된 전략만)
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
            "stream_reversal": bool(plan.get("stream_reversal", False)),
            "trend_follow_score": round(float(plan.get("trend_follow_score", 0.0) or 0.0), 3),
            "trend_alignment": str(plan.get("trend_alignment", "") or ""),
            "trend_entry_allowed": bool(plan.get("trend_entry_allowed", False)),
            "trend_slope_pct": round(float(plan.get("trend_slope_pct", 0.0) or 0.0), 3),
            "trend_extension_pct": round(float(plan.get("trend_extension_pct", 0.0) or 0.0), 3),
            "choch_bearish": bool(plan.get("choch_bearish", False)),
            "bos_bearish": bool(plan.get("bos_bearish", False)),
            "rsi_bearish_divergence": bool(plan.get("rsi_bearish_divergence", False)),
            "bias": str(plan.get("desk_bias", plan.get("bias", "")) or ""),
            "entry_path": action,
            "strategy_id": strategy_id,
            "entry_profile": entry_profile,
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
            and not strategy_disabled
            # bear_oversold: 상관 코인 동시 복수 진입 방지 — 1개 포지션만 허용
            and not (
                strategy_id == "crypto.bear_oversold_bounce"
                and any(
                    p.get("desk") == "crypto"
                    and str(p.get("entry_profile", "") or "") == "bear_oversold_bounce"
                    for p in self.open_positions
                )
            )
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
        if reentry_override and symbol:
            notes.append(f"{symbol} quality re-entry override active: fresh Korea signal can retry after one small shakeout")
        if cooldown_loss and symbol:
            notes.append(f"recent losing exit in {symbol}, cooldown blocks immediate re-entry")
        if repeated_loss_block and symbol:
            notes.append(f"repeated losses in {symbol}, extended block stays active")
        if extended_symbol_block and symbol:
            notes.append(f"{symbol} remains under extended Korea block after repeated failed attempts")
        if desk_loss_pressure and action in actionable_entries:
            if crypto_recovery_mode:
                notes.append(f"{desk} desk loss pressure active, recovery mode keeps only throttled entries")
            elif strategy_recovery_allowed:
                notes.append(f"{desk} desk loss pressure active, but proven strategy {strategy_id} remains allowed at throttled size")
            else:
                notes.append(f"{desk} desk loss pressure active, new entries paused")
        elif desk_loss_pressure:
            notes.append(f"{desk} desk loss pressure noted; waiting for a valid entry setup")
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
        if strategy_disabled:
            notes.append(
                f"{strategy_id} disabled by strategy performance: "
                f"win {float(strategy_disabled.get('win_rate', 0.0) or 0.0):.1f}%, "
                f"capital pnl {float(strategy_disabled.get('capital_pnl_pct', 0.0) or 0.0):+.2f}%, "
                f"peak0 {float(strategy_disabled.get('peak0_pct', 0.0) or 0.0):.1f}%"
            )
        rationale = [meta, *notes]
        if strategy_disabled and action in actionable_entries:
            save_shadow_signal(
                desk=desk,
                symbol=symbol,
                strategy_id=strategy_id,
                entry_profile=entry_profile,
                source="cycle",
                action=action,
                focus=str(plan.get("focus", "")),
                reason="strategy_disabled",
                score=float(plan.get("signal_score", 0.0) or 0.0),
                stream_score=float(plan.get("stream_score", 0.0) or 0.0),
                notional_pct=notional_pct,
                payload={"meta": meta, "notes": notes[-6:], "strategy_stats": strategy_disabled},
            )
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
            strategy_id=strategy_id,
            entry_profile=entry_profile,
            rationale=rationale,
        )

    def _recent_crypto_symbol_failure(self, symbol: str) -> dict | None:
        for item in self.closed_positions[:20]:
            if item.get("desk") != "crypto" or item.get("symbol") != symbol:
                continue
            pnl = float(item.get("pnl_pct", 0.0) or 0.0)
            if self._is_stop_like_exit(item) and pnl <= -0.30:
                return item
        return None

    def _crypto_cycle_entry_override_ok(self, meta: dict, plan: dict) -> tuple[bool, str]:
        """Allow high-structure entries to bypass hot-path-only mode.

        RANGING: smart_money_flow / ranging_strength_follow 기존 경로 유지.
        TRENDING + RANGING: ADX 강세 + CHoCH 불리시 추세 코인 — selective_probe 허용.
        근거: stream_ticks=0 (저거래량) 시간대에도 강한 추세는 사이클 진입 허용.
        """
        if not meta:
            return False, "no candidate meta"
        regime_ok = self.regime in {"RANGING", "TRENDING"}
        if not regime_ok:
            return False, f"regime={self.regime} not eligible for cycle override"

        # ── ADX 강세 + CHoCH 불리시 추세 경로 (RANGING/TRENDING 공통) ─────────
        adx_strong = bool(meta.get("adx_trend_strong", False))
        meta_adx_val = float(meta.get("adx_val", 0.0) or 0.0)
        choch_bull = bool(meta.get("choch_bullish", False))
        meta_trend_align = str(meta.get("trend_alignment", "") or "")
        meta_combined = float(meta.get("combined_score", meta.get("signal_score", 0.0)) or 0.0)
        meta_signal = float(meta.get("signal_score", meta_combined) or meta_combined)
        meta_ob = float(meta.get("orderbook_bid_ask_ratio", 0.0) or 0.0)
        meta_micro3 = float(meta.get("micro_move_3_pct", 0.0) or 0.0)
        meta_ema_gap = float(meta.get("ema_gap_pct", 0.0) or 0.0)
        meta_stream = float(meta.get("stream_score", 0.0) or 0.0)
        meta_rsi = meta.get("rsi")
        try:
            meta_rsi_f = float(meta_rsi) if meta_rsi is not None else 50.0
        except (TypeError, ValueError):
            meta_rsi_f = 50.0
        meta_rsi_bearish_div = bool(meta.get("rsi_bearish_divergence", False))
        meta_choch_bearish = bool(meta.get("choch_bearish", False))
        # stream=0.00 진입 차단: 실전 데이터에서 stream=0 사이클 진입은 peak_pnl=0 → 100% 손실
        # WebSocket 틱이 전혀 없으면 진입 모멘텀 확인 불가 → cycle override 비허용
        if meta_stream <= 0.0:
            return False, f"stream=0 cycle override blocked (no tick activity, all stream=0 entries historically lose)"
        # ADX 조건 완화: adx_trend_strong(DI+>DI-)가 False여도 ADX≥35+CHoCH+추세구조면 허용
        # 이유: 강한 추세 구간에서 단기 DI-우위(pullback 중)로 인해 adx_trend_strong=False가 되지만
        #      ADX 수치 자체가 높고 CHoCH+trend 구조가 확인되면 사이클 진입 허용
        _adx_ok = adx_strong or (
            meta_adx_val >= 35.0
            and choch_bull
            and meta_trend_align in ("trend_long", "pullback_long")
        )
        if (
            _adx_ok
            and choch_bull
            and meta_trend_align in ("trend_long", "pullback_long")  # pullback_long도 추세 구조
            and meta_combined >= 0.62
            and meta_signal >= 0.62
            and meta_ema_gap <= 5.5           # 과이격 차단
            and meta_ob >= 0.85
            and meta_micro3 >= -0.30
            and meta_rsi_f <= 86.0
            and not meta_rsi_bearish_div
            and not meta_choch_bearish
        ):
            return True, (
                f"adx_trend cycle ok combined={meta_combined:.2f} "
                f"ema_gap={meta_ema_gap:.1f}% ob={meta_ob:.2f}x adx={meta_adx_val:.0f} stream={meta_stream:.2f} regime={self.regime}"
            )

        # TRENDING regime에서는 adx_trend 경로만 허용 (아래 RANGING 전용 경로 차단)
        if self.regime != "RANGING":
            return False, f"trending regime: adx_trend conditions not met combined={meta_combined:.2f}"
        strategy_id = str(plan.get("strategy_id", "") or "")
        profile = str(plan.get("entry_profile", "") or "")
        focus = str(plan.get("focus", "") or "").lower()
        if not strategy_id:
            strategy_id = self._infer_strategy_id(str(plan.get("action", "")), focus, profile, "crypto")
        disabled_stats = self._strategy_disabled(strategy_id)
        if disabled_stats:
            return False, f"{strategy_id} disabled by live strategy stats"
        symbol = str(meta.get("market", "") or "")
        combined = float(meta.get("combined_score", meta.get("signal_score", 0.0)) or 0.0)
        signal = float(meta.get("signal_score", combined) or combined)
        micro = float(meta.get("micro_score", 0.0) or 0.0)
        micro_move_3 = float(meta.get("micro_move_3_pct", 0.0) or 0.0)
        micro_vwap_gap = float(meta.get("micro_vwap_gap_pct", 0.0) or 0.0)
        orderbook_bid_ask = float(meta.get("orderbook_bid_ask_ratio", 0.0) or 0.0)
        orderbook_score = float(meta.get("orderbook_score", 0.0) or 0.0)
        rsi_value = meta.get("rsi")
        try:
            rsi_float = float(rsi_value) if rsi_value is not None else 50.0
        except (TypeError, ValueError):
            rsi_float = 50.0
        recent_change = float(meta.get("recent_change_pct", 0.0) or 0.0)
        burst_change = float(meta.get("burst_change_pct", 0.0) or 0.0)
        trend_extension = float(meta.get("trend_extension_pct", 0.0) or 0.0)
        trend_alignment = str(meta.get("trend_alignment", "") or "")
        stream_reversal = bool(meta.get("stream_reversal", False))
        rsi_bearish_div = bool(meta.get("rsi_bearish_divergence", False))
        bearish_structure = bool(meta.get("choch_bearish", False)) or bool(meta.get("bos_bearish", False))
        too_late = (
            rsi_float >= 82.0
            or trend_extension >= 4.2
            or recent_change >= 10.0
            or burst_change >= 9.0
            or micro_vwap_gap >= 4.0
            or trend_alignment in {"downtrend", "late_extension"}
        )
        if stream_reversal or rsi_bearish_div or bearish_structure or too_late:
            return False, (
                f"late/bearish risk rsi={rsi_float:.0f} ext={trend_extension:.2f}% "
                f"recent={recent_change:.2f}% burst={burst_change:.2f}%"
            )
        recent_failure = self._recent_crypto_symbol_failure(symbol)
        if recent_failure:
            return False, "recent failed symbol still cooling down"

        smart_route = (
            strategy_id == "crypto.smart_money_flow"
            or profile == "smart_money_flow"
            or "smart_money_flow" in focus
        )
        strength_route = (
            strategy_id == "crypto.ranging_strength_follow"
            or profile == "ranging_strength_follow"
            or "ranging_strength_follow" in focus
        )
        smart_signal = bool(meta.get("smart_money_flow_long", False)) or (
            bool(meta.get("capital_flow_long", False))
            and (bool(meta.get("flow_box_breakout_long", False)) or bool(meta.get("auto_trendline_breakout_long", False)))
        )
        capital_flow_score = float(meta.get("capital_flow_score", 0.0) or 0.0)
        smart_ok = (
            smart_route
            and smart_signal
            and combined >= 0.62
            and capital_flow_score >= 0.65
            and (orderbook_bid_ask >= 1.05 or orderbook_score >= 0.55)
            and micro >= 0.50
            and micro_move_3 >= 0.0
        )
        if smart_ok:
            return True, (
                f"smart_money cycle ok combined={combined:.2f} flow={capital_flow_score:.2f} "
                f"ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}%"
            )

        strength_ok = (
            strength_route
            and combined >= 0.78
            and signal >= 0.70
            and (orderbook_bid_ask >= 1.20 or orderbook_score >= 0.60)
            and micro >= 0.55
            and micro_move_3 >= 0.0
            and trend_extension <= 3.2
            and rsi_float <= 78.0
        )
        if strength_ok:
            return True, (
                f"strength cycle ok combined={combined:.2f} signal={signal:.2f} "
                f"ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}%"
            )
        return False, (
            f"cycle override failed route={strategy_id or profile} combined={combined:.2f} "
            f"flow={capital_flow_score:.2f} ob={orderbook_bid_ask:.2f}x micro={micro:.2f}"
        )

    def _crypto_candidate_entry_ok(self, meta: dict) -> tuple[bool, str]:
        if not meta:
            return False, "missing candidate-specific signal"
        symbol = str(meta.get("market", "") or "")
        score = float(meta.get("combined_score", meta.get("signal_score", 0.0)) or 0.0)
        trend_allowed = bool(meta.get("trend_entry_allowed", False))
        trend_score = float(meta.get("trend_follow_score", 0.0) or 0.0)
        micro_score = float(meta.get("micro_score", 0.0) or 0.0)
        stream_score = float(meta.get("stream_score", 0.0) or 0.0)
        stream_fresh = bool(meta.get("stream_fresh", False))
        stream_ignition = bool(meta.get("stream_ignition", False))
        stream_reversal = bool(meta.get("stream_reversal", False))
        stream_age = float(meta.get("stream_age_seconds", 999.0) or 999.0)
        stream_move_15 = float(meta.get("stream_move_15s_pct", 0.0) or 0.0)
        stream_buy_ratio = float(meta.get("stream_buy_ratio_15s", 0.0) or 0.0)
        micro_vol_ratio = float(meta.get("micro_vol_ratio", 0.0) or 0.0)
        micro_move_3 = float(meta.get("micro_move_3_pct", 0.0) or 0.0)
        micro_vwap_gap = float(meta.get("micro_vwap_gap_pct", 0.0) or 0.0)
        micro_exhausted = bool(meta.get("micro_exhausted", False))
        breakout_count = int(meta.get("breakout_count", 0) or 0)
        vol_ratio = float(meta.get("vol_ratio", 0.0) or 0.0)
        pullback_score = float(meta.get("pullback_score", 0.0) or 0.0)
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
        launch_confirmed = (
            (micro_score >= 0.55 and micro_vol_ratio >= 1.1)
            or stream_score >= 0.55
            or stream_ignition
            or (breakout_count >= 2 and vol_ratio >= 1.4)
            or (pullback_score >= 0.75 and micro_score >= 0.42)
        )
        trend_alignment = str(meta.get("trend_alignment", "") or "")
        rsi_bearish_div = bool(meta.get("rsi_bearish_divergence", False))
        stream_timing_ok = (
            stream_fresh
            and stream_age <= 3.5
            and not stream_reversal
            and (
                (stream_ignition and stream_move_15 >= -0.05)
                or (stream_score >= 0.58 and stream_move_15 >= 0.05)
                or (stream_score >= 0.55 and stream_buy_ratio >= 0.52 and stream_move_15 >= -0.03)
            )
        )
        micro_timing_ok = (
            micro_score >= 0.72
            and micro_vol_ratio >= 1.15
            and micro_move_3 >= 0.05
            and micro_vwap_gap <= 1.6
            and not micro_exhausted
        )
        breakout_timing_ok = breakout_count >= 2 and vol_ratio >= 1.6 and micro_move_3 >= 0.0
        trend_pullback_timing_ok = stream_timing_ok or micro_timing_ok or breakout_timing_ok
        # --- Trend-pullback fast-path ---
        # Strong chart trend is only the setup. Require fresh 1m/tick timing
        # before bypassing the normal composite threshold.
        trend_pullback_eligible = (
            trend_alignment in ("pullback_long", "trend_long")
            and trend_allowed
            and trend_score >= 0.80
            and orderbook_bid_ask >= 1.60
            and trend_pullback_timing_ok
            and score >= 0.65
            and not rsi_bearish_div
            and not hard_overheat
            and freshness > 0.55
        )
        if trend_pullback_eligible:
            recent_failure = self._recent_crypto_symbol_failure(symbol)
            if not recent_failure:
                return (
                    True,
                    f"trend_pullback eligible combined={score:.2f} trend={trend_score:.2f} "
                    f"ob={orderbook_bid_ask:.2f}x timing stream={stream_timing_ok} micro={micro_timing_ok}",
                )
        # --- Standard path ---
        # 진단: combined 0.76-0.84 cycle-level 전건 손실 → 문턱 상향
        # 단, 0.82 완전 차단시 거래 89% 감소 → 0.79로 절충 + micro OR stream 보강
        micro_entry_ok = micro_score >= 0.55 and micro_vol_ratio >= 1.1
        stream_entry_ok = stream_score >= 0.55 and stream_fresh and not stream_reversal
        micro_or_stream_ok = micro_entry_ok or stream_entry_ok
        if score < 0.79:                                         # 0.76→0.82→0.79 절충
            return False, f"combined score too low ({score:.2f})"
        if not trend_allowed or trend_score < 0.62:              # 0.58 → 0.62 유지
            return False, f"trend gate failed ({trend_alignment} {trend_score:.2f})"
        if orderbook_bid_ask < 1.10:                             # 1.08→1.12→1.10 절충
            return False, f"orderbook not supportive ({orderbook_bid_ask:.2f}x)"
        if not micro_or_stream_ok:                               # micro 단독필수→OR stream
            return False, f"micro/stream not ready (micro={micro_score:.2f} vol={micro_vol_ratio:.2f} stream={stream_score:.2f})"
        if not launch_confirmed:
            return (
                False,
                "launch not confirmed "
                f"(micro {micro_score:.2f}, stream {stream_score:.2f}, breakout {breakout_count}, vol {vol_ratio:.1f}x)",
            )
        if rsi_bearish_div:
            return False, "bearish RSI divergence"
        if freshness <= 0.55:
            return False, f"stale signal ({freshness:.2f})"
        if hard_overheat:
            return False, "hard overheat"
        recent_failure = self._recent_crypto_symbol_failure(symbol)
        if recent_failure and (score < 0.82 or trend_score < 0.65 or orderbook_bid_ask < 1.15):
            return (
                False,
                "recent failed symbol requires stronger re-entry "
                f"(score {score:.2f}, trend {trend_score:.2f}, ob {orderbook_bid_ask:.2f}x)",
            )
        return True, f"eligible combined={score:.2f} trend={trend_score:.2f} ob={orderbook_bid_ask:.2f}x"

    @staticmethod
    def _crypto_obvious_trend_entry_ok(meta: dict) -> tuple[bool, str]:
        if not meta:
            return False, "missing metadata"
        chart_score = float(meta.get("signal_score", 0.0) or 0.0)
        combined = float(meta.get("combined_score", 0.0) or 0.0)
        trend_score = float(meta.get("trend_follow_score", 0.0) or 0.0)
        trend_alignment = str(meta.get("trend_alignment", "") or "")
        trend_allowed = bool(meta.get("trend_entry_allowed", False))
        trend_early = bool(meta.get("trend_early_entry", False))
        recent_change = float(meta.get("recent_change_pct", 0.0) or 0.0)
        burst_change = float(meta.get("burst_change_pct", 0.0) or 0.0)
        change_rate = float(meta.get("change_rate", 0.0) or 0.0)
        freshness = float(meta.get("signal_freshness", 1.0) or 1.0)
        ema_gap = float(meta.get("ema_gap_pct", 0.0) or 0.0)
        micro_vwap_gap = float(meta.get("micro_vwap_gap_pct", 0.0) or 0.0)
        trend_extension = float(meta.get("trend_extension_pct", 0.0) or 0.0)
        rsi_value = meta.get("rsi")
        try:
            rsi_float = float(rsi_value) if rsi_value is not None else 0.0
        except (TypeError, ValueError):
            rsi_float = 0.0
        stream_reversal = bool(meta.get("stream_reversal", False))
        stream_fresh = bool(meta.get("stream_fresh", False))
        top_risk = ema_gap >= 10.0 or rsi_float >= 88.0 or bool(meta.get("rsi_bearish_divergence", False))
        stream_ignition = bool(meta.get("stream_ignition", False))
        # obvious_trend: hot_path_guard와 동일 2-path 구조
        # "range"/pullback_long 제거 (78건 99% peak=0 사례)
        # 경로 A: stream_ignition + 표준 임계값
        _path_a = (
            trend_alignment == "trend_long"
            and trend_allowed
            and trend_score >= 0.88
            and chart_score >= 0.82
            and combined >= 0.78
            and stream_ignition
            and freshness >= 0.58
            and trend_extension <= 5.0
            and micro_vwap_gap <= 3.5
            and not top_risk
            and not (stream_fresh and stream_reversal)
        )
        _ot_stream_score_ea = float(meta.get("stream_score", 0.0) or 0.0)
        # 경로 B: 초고점수 + stream 강활성 (ignition 없어도 허용, hot_path_guard와 동기화)
        _path_b = (
            trend_alignment == "trend_long"
            and trend_allowed
            and trend_score >= 0.91
            and chart_score >= 0.86
            and combined >= 0.84
            and _ot_stream_score_ea >= 0.64
            and not stream_ignition
            and freshness >= 0.58
            and trend_extension <= 5.0
            and micro_vwap_gap <= 3.5
            and not top_risk
            and not (stream_fresh and stream_reversal)
        )
        ok = _path_a or _path_b
        path_label = "pathA" if _path_a else ("pathB" if _path_b else "none")
        return (
            ok,
            f"obvious 15m trend ride [{path_label}] chart={chart_score:.2f} combined={combined:.2f} "
            f"trend={trend_score:.2f} stream={_ot_stream_score_ea:.2f} ignition={stream_ignition} "
            f"move={max(recent_change, burst_change, change_rate):.2f}% "
            f"ext={trend_extension:.2f}% rsi={rsi_float:.0f}",
        )

    @staticmethod
    def _crypto_range_impulse_armed(meta: dict) -> tuple[bool, str]:
        if not meta:
            return False, "missing metadata"
        chart_score = float(meta.get("signal_score", 0.0) or 0.0)
        combined = float(meta.get("combined_score", 0.0) or 0.0)
        trend_alignment = str(meta.get("trend_alignment", "") or "")
        recent_change = float(meta.get("recent_change_pct", 0.0) or 0.0)
        change_rate = float(meta.get("change_rate", 0.0) or 0.0)
        freshness = float(meta.get("signal_freshness", 1.0) or 1.0)
        micro_vwap_gap = float(meta.get("micro_vwap_gap_pct", 0.0) or 0.0)
        trend_extension = float(meta.get("trend_extension_pct", 0.0) or 0.0)
        rsi_value = meta.get("rsi")
        try:
            rsi_float = float(rsi_value) if rsi_value is not None else 0.0
        except (TypeError, ValueError):
            rsi_float = 0.0
        ok = (
            trend_alignment in {"trend_long", "pullback_long", "range"}
            and chart_score >= 0.74
            and combined >= 0.38
            and max(recent_change, change_rate) >= 3.0
            and freshness >= 0.55
            and trend_extension <= 7.0
            and rsi_float <= 82.0
            and micro_vwap_gap <= 4.2
            and not bool(meta.get("rsi_bearish_divergence", False))
        )
        return (
            ok,
            f"range impulse armed chart={chart_score:.2f} combined={combined:.2f} "
            f"move={max(recent_change, change_rate):.2f}% ext={trend_extension:.2f}% rsi={rsi_float:.0f}",
        )

    @staticmethod
    def _apply_crypto_candidate_meta(plan: dict, meta: dict) -> dict:
        if not meta:
            return plan
        mapped = dict(plan)
        mapped["symbol"] = str(meta.get("market", mapped.get("symbol", "")) or mapped.get("symbol", ""))
        mapped["signal_score"] = float(meta.get("combined_score", meta.get("signal_score", mapped.get("signal_score", 0.0))) or 0.0)
        mapped["desk_bias"] = str(meta.get("bias", mapped.get("desk_bias", "balanced")) or "balanced")
        trend_alignment = str(meta.get("trend_alignment", "") or "")
        micro_score = float(meta.get("micro_score", 0.0) or 0.0)
        stream_score = float(meta.get("stream_score", 0.0) or 0.0)
        orderbook_bid_ask = float(meta.get("orderbook_bid_ask_ratio", 0.0) or 0.0)
        obvious_ok, obvious_reason = ExecutionAgent._crypto_obvious_trend_entry_ok(meta)
        if obvious_ok:
            mapped["entry_profile"] = "obvious_trend"
            mapped["focus"] = (
                f"{mapped['symbol']} obvious_trend 15m trend ride "
                f"(combined {mapped['signal_score']:.2f}, {trend_alignment}, "
                f"micro {micro_score:.2f}, stream {stream_score:.2f}, ob {orderbook_bid_ask:.2f}x)"
            )
        else:
            mapped["focus"] = (
                f"{mapped['symbol']} candidate-specific multi-coin entry "
                f"(combined {mapped['signal_score']:.2f}, {trend_alignment}, "
                f"micro {micro_score:.2f}, stream {stream_score:.2f}, ob {orderbook_bid_ask:.2f}x)"
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
            "trend_extension_pct", "trend_early_entry", "entry_profile", "trend_reasons",
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
        if obvious_ok:
            notes.append(obvious_reason)
        mapped["notes"] = notes
        return mapped

    def _single_crypto_candidate_order(
        self,
        desk: str,
        plan: dict,
        candidates: list[str],
        candidate_meta: dict[str, dict],
        skipped_candidates: list[str],
    ) -> dict:
        single_plan = dict(plan)
        if desk == "crypto":
            symbol = next(
                (candidate for candidate in candidates if not self._has_open_position(desk, candidate)),
                candidates[0] if candidates else str(plan.get("symbol", "")).strip(),
            )
            single_plan["symbol"] = symbol
            single_plan["candidate_symbols"] = []
            if candidate_meta and symbol in candidate_meta:
                single_plan = self._apply_crypto_candidate_meta(single_plan, candidate_meta[symbol])
            elif plan.get("candidate_symbols"):
                notes = list(single_plan.get("notes", []) or [])
                notes.append("candidate rotation disabled: missing candidate-specific metadata")
                single_plan["notes"] = notes
                single_plan["focus"] = f"{symbol} candidate rotation entry (metadata missing)"
            if skipped_candidates:
                single_plan["notes"] = list(single_plan.get("notes", []) or []) + [
                    f"skipped weaker candidates: {'; '.join(skipped_candidates[:3])}"
                ]
        return self._plan_to_order(desk, single_plan).model_dump()

    def _multi_orders(self, desk: str, plan: dict) -> list[dict]:
        """Generate up to max_positions concurrent orders per desk from ranked candidates."""
        action = str(plan.get("action", ""))
        if action not in {"probe_longs", "attack_opening_drive", "selective_probe"}:
            return [self._plan_to_order(desk, plan).model_dump()]

        max_positions, _ = self._desk_limits(desk)
        already_open = self._desk_open_count(desk)
        slots = max_positions - already_open

        primary = str(plan.get("symbol", "")).strip()
        all_candidates: list[str] = [primary] if primary else []
        for item in plan.get("candidate_symbols", []) or []:
            s = str(item).strip()
            if s and s not in all_candidates:
                all_candidates.append(s)

        opening_drive_focus = desk == "korea" and "opening drive" in str(plan.get("focus", "") or "").lower()
        if desk == "korea" and (action == "attack_opening_drive" or opening_drive_focus) and len(all_candidates) > 1:
            all_candidates = all_candidates[:1]

        candidate_meta = {
            str(item.get("market", "")).strip(): item
            for item in (plan.get("candidate_markets") or [])
            if str(item.get("market", "")).strip()
        }
        skipped_candidates: list[str] = []
        if desk == "crypto":
            if candidate_meta:
                eligible_candidates = []
                range_armed_candidates = []
                range_armed_notes = []
                for candidate in all_candidates:
                    meta = candidate_meta.get(candidate, {})
                    cycle_ok, cycle_reason = self._crypto_cycle_entry_override_ok(meta, plan)
                    if cycle_ok:
                        eligible_candidates.append(candidate)
                        skipped_candidates.append(f"{candidate}: cycle override allowed ({cycle_reason})")
                        continue
                    # cycle-level entries are low-precision in ALL regimes; hot-path only
                    # 사이클 계산→실행 지연 사이 모멘텀 소진 → 전 regime hot-path 전용
                    skipped_candidates.append(
                        f"{candidate}: cycle-entry blocked (hot-path only, regime={self.regime})"
                    )
                    armed_ok, armed_reason = self._crypto_range_impulse_armed(meta)
                    if armed_ok:
                        range_armed_candidates.append(candidate)
                        range_armed_notes.append(f"{candidate}: {armed_reason}")
                if len(eligible_candidates) > 1:
                    skipped_candidates.append(
                        f"cycle override capped: {len(eligible_candidates)} eligible, opening best 1 only"
                    )
                all_candidates = eligible_candidates[:1]
                if all_candidates:
                    # cycle override 통과 → strategy_id를 명시적으로 설정해 candidate_rotation 오판 방지
                    # 이유: _apply_crypto_candidate_meta가 focus에 "candidate-specific multi-coin entry"를 설정하고
                    #       _infer_strategy_id가 이를 crypto.candidate_rotation으로 매핑 (영구 차단됨)
                    plan = dict(plan)
                    plan["strategy_id"] = "crypto.selective_probe"
                    plan["entry_profile"] = "selective_probe"
                if not all_candidates:
                    blocked_plan = dict(plan)
                    blocked_plan["action"] = "watchlist_only"
                    blocked_plan["size"] = "0.00x"
                    if range_armed_candidates:
                        blocked_plan["focus"] = "RANGING impulse candidates armed for tick confirmation."
                        blocked_plan["candidate_symbols"] = range_armed_candidates[:6]
                        blocked_plan["notes"] = (
                            list(plan.get("notes", []) or [])
                            + range_armed_notes[:6]
                            + ["No cycle entry: waiting for websocket trade ignition before opening."]
                        )
                    else:
                        blocked_plan["focus"] = "Crypto candidates failed per-symbol growth-mode eligibility."
                        blocked_plan["candidate_symbols"] = []
                        blocked_plan["notes"] = list(plan.get("notes", []) or []) + skipped_candidates[:6]
                    return [self._plan_to_order(desk, blocked_plan).model_dump()]
            elif plan.get("candidate_symbols"):
                # cycle-level candidate entry: hot-path 전용 아키텍처에서 전 regime 차단
                # RANGING: 기존 차단, TRENDING: 추가 차단 (2026-05-07 candidate_rotation 8건 발화 원인)
                # 사이클 계산→실행 지연(수초~수십초) 사이 모멘텀 소진 → 무조건 hot-path 진입만 허용
                blocked_plan = dict(plan)
                blocked_plan["action"] = "watchlist_only"
                blocked_plan["size"] = "0.00x"
                blocked_plan["focus"] = f"Cycle-level candidate entry blocked (hot-path only, regime={self.regime})."
                return [self._plan_to_order(desk, blocked_plan).model_dump()]

        if slots <= 1 or len(all_candidates) <= 1:
            if desk == "korea" and all_candidates:
                single_plan = dict(plan)
                single_plan["symbol"] = all_candidates[0]
                single_plan = self._apply_korea_candidate_snapshot(single_plan, all_candidates[0])
                return [self._plan_to_order(desk, single_plan).model_dump()]
            return [self._single_crypto_candidate_order(desk, plan, all_candidates, candidate_meta, skipped_candidates)]

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
            elif desk == "korea":
                single_plan = self._apply_korea_candidate_snapshot(single_plan, candidate)
                single_plan["size"] = per_order_size
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

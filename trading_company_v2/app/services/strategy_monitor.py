"""
Strategy live win-rate monitoring and auto-pause.

Queries PaperPositionRecord (status=closed) grouped by strategy_id,
compares live WR against backtest thresholds, and:
  - Sends a Telegram alert if live WR drops >20pp below backtest
  - Marks the strategy as paused (in-memory) so recommendation_engine skips it

Thresholds are derived from fee-adjusted backtest results (2026-05-20).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import NamedTuple

_log = logging.getLogger(__name__)

# ── Backtest WR thresholds (fee-adjusted) ────────────────────────────────────
# WR values are fractions (0–1). Strategies with WR=0 are excluded from checks.
_STRATEGY_WR_THRESHOLDS: dict[str, float] = {
    "crypto.eth_4h_breakout":     0.0,    # breakout: hard to backtest WR meaningfully
    "crypto.mongtata_airborne":   0.56,   # S2 — ⚠️ under re-validation, soft threshold
    "crypto.rsi2_mean_reversion": 0.481,  # S9 (fee-adjusted)
    "crypto.nday_pullback":       0.558,  # S10 (fee-adjusted)
    "korea.new_high_breakout":    0.846,  # B breakout
    "korea.mongtata_airborne":    0.509,  # S2 Korea (soft, under review)
    "korea.rsi2_mean_reversion":  0.581,  # S9 Korea (fee-adjusted)
    "korea.nday_pullback":        0.510,  # S10 Korea (fee-adjusted)
}

# Auto-pause triggers when live WR drops this many percentage points below backtest
_WR_AUTO_PAUSE_GAP: float = 0.20   # 20pp gap
# Minimum live trades required before triggering pause/alert
_MIN_TRADES_FOR_ALERT: int = 20
_MIN_TRADES_FOR_AUTOPAUSE: int = 30

# ── In-memory pause state (reset on process restart) ─────────────────────────
_paused_strategies: set[str] = set()
_last_check_ts: float = 0.0
_CHECK_INTERVAL_SECONDS: float = 30 * 60  # re-check every 30 minutes


class StrategyWrStat(NamedTuple):
    strategy_id: str
    total: int
    wins: int
    live_wr: float
    backtest_wr: float
    gap: float           # live_wr - backtest_wr (negative = underperforming)
    paused: bool


def compute_strategy_wr_stats() -> list[StrategyWrStat]:
    """Query DB and return per-strategy WR stats for all tracked strategies."""
    from app.core.state_store import SessionLocal, PaperPositionRecord
    from sqlalchemy import select, func

    stats: list[StrategyWrStat] = []
    try:
        with SessionLocal() as db:
            # GROUP BY strategy_id — count total and wins
            rows = (
                db.execute(
                    select(
                        PaperPositionRecord.strategy_id,
                        func.count(PaperPositionRecord.id).label("total"),
                        func.sum(
                            func.cast(PaperPositionRecord.pnl_pct > 0, type_=None)
                        ).label("wins"),
                    )
                    .where(
                        PaperPositionRecord.status == "closed",
                        PaperPositionRecord.strategy_id != "",
                    )
                    .group_by(PaperPositionRecord.strategy_id)
                )
                .all()
            )
        for row in rows:
            sid = str(row.strategy_id or "")
            total = int(row.total or 0)
            wins = int(row.wins or 0)
            if total == 0 or sid not in _STRATEGY_WR_THRESHOLDS:
                continue
            backtest_wr = _STRATEGY_WR_THRESHOLDS[sid]
            if backtest_wr <= 0:
                continue  # strategy excluded from WR checks
            live_wr = wins / total
            gap = live_wr - backtest_wr
            stats.append(
                StrategyWrStat(
                    strategy_id=sid,
                    total=total,
                    wins=wins,
                    live_wr=round(live_wr, 4),
                    backtest_wr=backtest_wr,
                    gap=round(gap, 4),
                    paused=sid in _paused_strategies,
                )
            )
    except Exception as exc:
        _log.warning("strategy_monitor: compute_strategy_wr_stats failed: %s", exc)
    return stats


def check_strategy_wr_and_alert(force: bool = False) -> list[StrategyWrStat]:
    """
    Run WR monitoring check. Call periodically from the main loop.

    - Skips if called more recently than _CHECK_INTERVAL_SECONDS (unless force=True)
    - Sends Telegram alert for strategies that underperform >20pp after 20+ trades
    - Auto-pauses strategies that underperform >20pp after 30+ trades
    - Returns list of StrategyWrStat for ALL tracked strategies (for dashboard)
    """
    global _last_check_ts

    now = datetime.now(timezone.utc).timestamp()
    if not force and (now - _last_check_ts) < _CHECK_INTERVAL_SECONDS:
        return []
    _last_check_ts = now

    stats = compute_strategy_wr_stats()
    alert_lines: list[str] = []
    newly_paused: list[str] = []
    newly_resumed: list[str] = []

    for s in stats:
        gap_pp = s.gap * 100  # in percentage points
        underperforming = gap_pp <= -(_WR_AUTO_PAUSE_GAP * 100)

        # Resume: if previously paused but now recovering (gap < 15pp)
        if s.strategy_id in _paused_strategies and gap_pp > -15.0:
            _paused_strategies.discard(s.strategy_id)
            newly_resumed.append(s.strategy_id)
            _log.info("strategy_monitor: %s RESUMED (gap=%.1fpp, n=%d)", s.strategy_id, gap_pp, s.total)

        if underperforming:
            if s.total >= _MIN_TRADES_FOR_AUTOPAUSE and s.strategy_id not in _paused_strategies:
                _paused_strategies.add(s.strategy_id)
                newly_paused.append(s.strategy_id)
                _log.warning(
                    "strategy_monitor: AUTO-PAUSING %s | live WR %.1f%% vs backtest %.1f%% (gap %.1fpp, n=%d)",
                    s.strategy_id, s.live_wr * 100, s.backtest_wr * 100, gap_pp, s.total,
                )
            if s.total >= _MIN_TRADES_FOR_ALERT:
                alert_lines.append(
                    f"{'⏸️ AUTO-PAUSED' if s.strategy_id in _paused_strategies else '⚠️'} {s.strategy_id}: "
                    f"live {s.live_wr*100:.1f}% vs BT {s.backtest_wr*100:.1f}% "
                    f"(gap {gap_pp:+.1f}pp, n={s.total})"
                )

    # Send Telegram alert if any strategies are underperforming
    if alert_lines or newly_paused or newly_resumed:
        try:
            from app.notifier import notifier
            if newly_paused:
                notifier.send_ops_alert(
                    "전략 WR 자동정지",
                    [
                        f"다음 전략이 백테스트 WR 대비 {int(_WR_AUTO_PAUSE_GAP*100)}pp 이상 하락하여 자동 정지됩니다:",
                        *[f"  - {sid}" for sid in newly_paused],
                        "수동 검토 후 재개 여부를 결정하세요.",
                    ],
                )
            if newly_resumed:
                notifier.send_ops_alert(
                    "전략 WR 정지 해제",
                    [f"  - {sid}" for sid in newly_resumed],
                )
            if alert_lines and not newly_paused:
                notifier.send_ops_alert(
                    "전략 WR 경고",
                    ["백테스트 대비 WR 저조 전략 감지:", *alert_lines],
                )
        except Exception as exc:
            _log.warning("strategy_monitor: notifier failed: %s", exc)

    return stats


def is_strategy_paused(strategy_id: str) -> bool:
    """Return True if this strategy has been auto-paused due to poor live WR."""
    return strategy_id in _paused_strategies


def manually_resume_strategy(strategy_id: str) -> None:
    """Manually clear a paused strategy (e.g., after investigation/reset)."""
    _paused_strategies.discard(strategy_id)
    _log.info("strategy_monitor: %s manually resumed", strategy_id)

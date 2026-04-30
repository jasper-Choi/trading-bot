from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz
import requests
from sqlalchemy import JSON, Boolean, Float, Integer, String, create_engine, event, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, object_session, sessionmaker

from app.config import settings
from app.core.models import AgentSnapshot, ClosedPosition, CompanyState, CycleJournalEntry, PaperOrder, Position, utcnow_iso


class Base(DeclarativeBase):
    pass


class StateRecord(Base):
    __tablename__ = "company_state"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    stance: Mapped[str] = mapped_column(String(20), default="BALANCED")
    regime: Mapped[str] = mapped_column(String(20), default="RANGING")
    risk_budget: Mapped[float] = mapped_column(Float, default=0.5)
    allow_new_entries: Mapped[bool] = mapped_column(Boolean, default=True)
    execution_mode: Mapped[str] = mapped_column(String(20), default="paper")
    notes: Mapped[list] = mapped_column(JSON, default=list)
    trader_principles: Mapped[list] = mapped_column(JSON, default=list)
    latest_signals: Mapped[list] = mapped_column(JSON, default=list)
    market_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    session_state: Mapped[dict] = mapped_column(JSON, default=dict)
    desk_views: Mapped[dict] = mapped_column(JSON, default=dict)
    strategy_book: Mapped[dict] = mapped_column(JSON, default=dict)
    agent_runs: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class PaperOrderRecord(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    desk: Mapped[str] = mapped_column(String(50), default="")
    action: Mapped[str] = mapped_column(String(50), default="")
    focus: Mapped[str] = mapped_column(String(200), default="")
    size: Mapped[str] = mapped_column(String(20), default="")
    rationale: Mapped[list] = mapped_column(JSON, default=list)


class CycleJournalRecord(Base):
    __tablename__ = "cycle_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_at: Mapped[str] = mapped_column(String(40), default="")
    stance: Mapped[str] = mapped_column(String(20), default="")
    regime: Mapped[str] = mapped_column(String(20), default="")
    company_focus: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[list] = mapped_column(JSON, default=list)
    orders: Mapped[list] = mapped_column(JSON, default=list)


class PaperPositionRecord(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk: Mapped[str] = mapped_column(String(50), default="")
    symbol: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    action: Mapped[str] = mapped_column(String(50), default="")
    size: Mapped[str] = mapped_column(String(20), default="0.00x")
    opened_at: Mapped[str] = mapped_column(String(40), default="")
    closed_at: Mapped[str] = mapped_column(String(40), default="")
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_price: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    peak_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cycles_open: Mapped[int] = mapped_column(Integer, default=0)
    closed_reason: Mapped[str] = mapped_column(String(100), default="")
    focus: Mapped[str] = mapped_column(String(200), default="")


class PositionRecord(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk: Mapped[str] = mapped_column(String(50), default="")
    symbol: Mapped[str] = mapped_column(String(100), default="")
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    notional_pct: Mapped[float] = mapped_column(Float, default=0.0)
    action: Mapped[str] = mapped_column(String(50), default="")
    unrealized_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[str] = mapped_column(String(40), default="")


class ClosedPositionRecord(Base):
    __tablename__ = "closed_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk: Mapped[str] = mapped_column(String(50), default="")
    symbol: Mapped[str] = mapped_column(String(100), default="")
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_price: Mapped[float] = mapped_column(Float, default=0.0)
    notional_pct: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    won: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[str] = mapped_column(String(40), default="")
    closed_at: Mapped[str] = mapped_column(String(40), default="")
    closed_reason: Mapped[str] = mapped_column(String(100), default="")


class LiveOrderRecord(Base):
    __tablename__ = "live_order_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    desk: Mapped[str] = mapped_column(String(50), default="")
    symbol: Mapped[str] = mapped_column(String(100), default="")
    action: Mapped[str] = mapped_column(String(50), default="")
    size: Mapped[str] = mapped_column(String(20), default="")
    requested_mode: Mapped[str] = mapped_column(String(20), default="paper")
    applied_mode: Mapped[str] = mapped_column(String(20), default="paper")
    broker_live: Mapped[bool] = mapped_column(Boolean, default=False)
    request_status: Mapped[str] = mapped_column(String(20), default="skipped")
    broker_order_id: Mapped[str] = mapped_column(String(100), default="")
    broker_state: Mapped[str] = mapped_column(String(50), default="")
    reason: Mapped[str] = mapped_column(String(100), default="")
    message: Mapped[str] = mapped_column(String(300), default="")
    effect_status: Mapped[str] = mapped_column(String(30), default="pending")
    linked_position_symbol: Mapped[str] = mapped_column(String(100), default="")
    linked_closed_symbol: Mapped[str] = mapped_column(String(100), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


db_path = Path(settings.db_path)
db_path.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_size=8,
    max_overflow=4,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
ACTIONABLE_ENTRY_ACTIONS = {"probe_longs", "attack_opening_drive", "selective_probe"}
ACTIONABLE_EXIT_ACTIONS = {"reduce_risk", "capital_preservation"}
ACTIVE_LIVE_EFFECT_STATUSES = {
    "pending",
    "awaiting_balance_sync",
    "partial_balance_sync",
    "linked_partial_open",
    "partial_close_pending",
}


def _size_to_notional(size: str) -> float:
    try:
        return float(str(size).replace("x", ""))
    except ValueError:
        return 0.0


def _paper_slippage_bps(symbol: str, side: str, salt: str = "") -> float:
    min_bps = float(settings.paper_slippage_min_bps)
    max_bps = max(float(settings.paper_slippage_max_bps), min_bps)
    if max_bps == min_bps:
        return min_bps
    digest = hashlib.blake2b(f"{symbol}:{side}:{salt}".encode("utf-8"), digest_size=2).digest()
    bucket = int.from_bytes(digest, "big") / 65535
    return round(min_bps + (max_bps - min_bps) * bucket, 4)


def _paper_entry_price(raw_price: float, symbol: str, salt: str = "") -> float:
    slip = _paper_slippage_bps(symbol, "entry", salt) / 10_000
    return round(raw_price * (1 + slip), 8)


def _paper_exit_price(raw_price: float, symbol: str, salt: str = "") -> float:
    slip = _paper_slippage_bps(symbol, "exit", salt) / 10_000
    return round(raw_price * (1 - slip), 8)


def _paper_net_pnl_pct(entry_price: float, raw_current_price: float, symbol: str, salt: str = "") -> float:
    if entry_price <= 0 or raw_current_price <= 0:
        return 0.0
    exit_price = _paper_exit_price(raw_current_price, symbol, salt)
    gross_pct = ((exit_price - entry_price) / entry_price) * 100
    fee_pct = float(settings.paper_fee_bps) * 2 / 100
    return round(gross_pct - fee_pct, 2)


def _local_timezone():
    try:
        return ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        return pytz.timezone(settings.timezone)


def _local_date_from_iso(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(_local_timezone()).date().isoformat()
    except ValueError:
        return value[:10]


def _local_datetime_from_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_local_timezone())


def _today_local_date() -> str:
    return datetime.now(_local_timezone()).date().isoformat()


def _local_day_utc_bounds_iso(day: str) -> tuple[str, str]:
    tz = _local_timezone()
    local_start = datetime.combine(datetime.fromisoformat(day).date(), time.min)
    if hasattr(tz, "localize"):
        local_start = tz.localize(local_start)
    else:
        local_start = local_start.replace(tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).isoformat(),
        local_end.astimezone(timezone.utc).isoformat(),
    )


def _extract_order_meta(action: str, rationale: list) -> dict:
    meta = rationale[0] if rationale and isinstance(rationale[0], dict) else {}
    normalized = {
        "symbol": str(meta.get("symbol", "") or ""),
        "reference_price": float(meta.get("reference_price", 0.0) or 0.0),
        "notional_pct": float(meta.get("notional_pct", 0.0) or 0.0),
        "combined_score": float(meta.get("combined_score", meta.get("signal_score", 0.0)) or 0.0),
        "signal_score": float(meta.get("signal_score", 0.0) or 0.0),
        "micro_score": float(meta.get("micro_score", 0.0) or 0.0),
        "orderbook_score": float(meta.get("orderbook_score", 0.0) or 0.0),
        "orderbook_bid_ask_ratio": float(meta.get("orderbook_bid_ask_ratio", 0.0) or 0.0),
        "pullback_score": float(meta.get("pullback_score", 0.0) or 0.0),
        "stream_score": float(meta.get("stream_score", 0.0) or 0.0),
        "bias": str(meta.get("bias", "") or ""),
        "entry_path": str(meta.get("entry_path", action) or action),
        "status": str(meta.get("status", "idle") or "idle"),
        "pnl_estimate_pct": float(meta.get("pnl_estimate_pct", 0.0) or 0.0),
    }
    if action not in ACTIONABLE_ENTRY_ACTIONS:
        normalized["status"] = "idle"
        normalized["pnl_estimate_pct"] = 0.0
    return normalized


def _paper_trade_payload(position: PaperPositionRecord, meta: dict | None = None) -> dict:
    meta = meta or {}
    notional_pct = float(meta.get("notional_pct", 0.0) or _size_to_notional(position.size))
    return {
        "desk": position.desk,
        "symbol": position.symbol,
        "action": position.action,
        "size": position.size,
        "opened_at": position.opened_at,
        "closed_at": position.closed_at,
        "entry_price": position.entry_price,
        "current_price": position.current_price,
        "exit_price": position.exit_price,
        "pnl_pct": position.pnl_pct,
        "peak_pnl_pct": position.peak_pnl_pct,
        "closed_reason": position.closed_reason,
        "focus": position.focus,
        "notional_pct": notional_pct,
        "capital_krw": settings.paper_capital_krw,
        "combined_score": float(meta.get("combined_score", meta.get("signal_score", 0.0)) or 0.0),
        "signal_score": float(meta.get("signal_score", 0.0) or 0.0),
        "micro_score": float(meta.get("micro_score", 0.0) or 0.0),
        "orderbook_score": float(meta.get("orderbook_score", meta.get("orderbook_bid_ask_ratio", 0.0)) or 0.0),
        "pullback_score": float(meta.get("pullback_score", 0.0) or 0.0),
        "stream_score": float(meta.get("stream_score", 0.0) or 0.0),
        "bias": str(meta.get("bias", "") or ""),
        "entry_path": str(meta.get("entry_path", position.action) or position.action),
    }


def _notify_trade_entry(payload: dict) -> None:
    def _send() -> None:
        try:
            from app.notifier import notifier
            notifier.send_trade_entry(payload)
        except Exception as exc:
            print(f"[notifier] trade entry alert failed: {exc}")

    import threading
    threading.Thread(target=_send, name="telegram-trade-entry", daemon=True).start()


def _notify_trade_exit(payload: dict, reason: str) -> None:
    def _send() -> None:
        try:
            from app.notifier import notifier
            notifier.send_trade_exit(payload, reason)
        except Exception as exc:
            print(f"[notifier] trade exit alert failed: {exc}")

    import threading
    threading.Thread(target=_send, name="telegram-trade-exit", daemon=True).start()


def _build_price_lookup(market_snapshot: dict) -> dict[tuple[str, str], float]:
    lookup: dict[tuple[str, str], float] = {}
    for item in market_snapshot.get("crypto_leaders", []):
        symbol = str(item.get("market", "")).strip()
        if symbol:
            lookup[("crypto", symbol)] = float(item.get("trade_price") or 0.0)
    for item in market_snapshot.get("us_leaders", []):
        symbol = str(item.get("ticker", "")).strip()
        if symbol:
            lookup[("us", symbol)] = float(item.get("current_price") or 0.0)
    for item in market_snapshot.get("gap_candidates", []) + market_snapshot.get("stock_leaders", []):
        symbol = str(item.get("ticker", "")).strip()
        if symbol:
            lookup[("korea", symbol)] = float(item.get("current_price") or 0.0)
    return lookup


def _position_thresholds(desk: str, action: str) -> tuple[float, float, int]:
    # Returns (target_pct, stop_pct, max_cycles)
    # Backtest-validated (2025-04):
    #   coin_backtest_v5  → +4% TP / -2.0% stop / ≤48h  (60-min vol-breakout)
    #   stock_backtest_v3 → +4% TP / -2.5% stop / ≤5 days (daily momentum breakout)
    # @ 2 min/cycle: 720 = 24h, 360 = 12h, 195 = 6.5h (1 KRX session)
    if desk == "crypto":
        # Trend mode: cut failed ignitions fast, let winners run with trailing.
        return 10.0, -2.0, 180
    if desk == "us":
        if action == "probe_longs":
            return 6.0, -3.0, 200
        if action == "selective_probe":
            return 4.0, -2.0, 150
        return 3.0, -1.5, 120
    # Korea recovery swing: reachable +3.8% win target, max 1 KRX session.
    if action in {"attack_opening_drive", "probe_longs", "selective_probe"}:
        return 3.8, -2.0, 195
    return 4.0, -2.0, 150


def _crypto_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """Return (giveback_pct, floor_pct) for crypto profit protection.

    Tiers (partial-profit-capture style):
      peak >= 5.0%  → floor 2.20%  (let big winners breathe)
      peak >= 3.0%  → floor 1.20%
      peak >= 1.8%  → floor 0.70%
      peak >= 1.0%  → floor 0.35%  (lock in ≥0.35% once +1% seen)
      peak >= 0.80% → floor 0.12%  (near breakeven lock once +0.8% seen)
    """
    if peak_pnl >= 5.0:
        return 1.20, 2.20
    if peak_pnl >= 3.0:
        return 0.90, 1.20
    if peak_pnl >= 1.8:
        return 0.65, 0.70
    if peak_pnl >= 1.0:
        return 0.45, 0.35
    if peak_pnl >= 0.80:
        return 0.40, 0.12  # once +0.8% seen: floor at breakeven+, exit around +0.40%
    return 0.0, 0.0


def _crypto_no_lift_exit_reason(minutes_open: float, peak_pnl: float, pnl_pct: float, rapid: bool = False) -> str | None:
    """Close crypto entries that never prove momentum."""
    if minutes_open >= 10.0 and peak_pnl <= 0.05 and pnl_pct <= -0.30:
        return "rapid_no_lift" if rapid else "no_lift_exit"
    if minutes_open >= 10.0 and 0.15 <= peak_pnl < 0.80 and pnl_pct <= -0.35:
        return "rapid_reversal_loss" if rapid else "reversal_loss_exit"
    if minutes_open >= 18.0 and peak_pnl <= 0.10 and pnl_pct <= 0.05:
        return "rapid_flat_timeout" if rapid else "flat_no_lift_exit"
    return None


_STOP_LIKE_PAPER_REASONS = {
    "stop_hit",
    "rapid_stop_hit",
    "early_failure",
    "rapid_failed_start",
    "rapid_repeat_symbol_failure",
}


def _has_recent_failed_paper_symbol(db: Session, position: PaperPositionRecord) -> bool:
    prior = db.execute(
        select(PaperPositionRecord)
        .where(
            PaperPositionRecord.desk == position.desk,
            PaperPositionRecord.symbol == position.symbol,
            PaperPositionRecord.status == "closed",
        )
        .order_by(PaperPositionRecord.id.desc())
        .limit(3)
    ).scalars().all()
    for row in prior:
        if row.closed_reason in _STOP_LIKE_PAPER_REASONS and float(row.pnl_pct or 0.0) <= -0.30:
            return True
    return False


def _build_cycle_signal_meta(paper_orders: list[PaperOrder]) -> dict[str, dict]:
    meta_by_symbol: dict[str, dict] = {}
    for order in paper_orders:
        meta = _extract_order_meta(order.action, order.rationale)
        symbol = str(meta.get("symbol", "") or order.symbol or "").strip()
        if symbol:
            meta_by_symbol[symbol] = meta
    return meta_by_symbol


def _crypto_trend_exit_reason(meta: dict, pnl_pct: float, minutes_open: float = 0.0) -> str | None:
    """Translate bearish trend triggers into exits for open crypto positions.

    Minimum hold times prevent noise exits from 8-second cycle oscillation around EMA
    boundaries.  A position that just entered should not be closed by the same trend
    signal that fired the entry — the trend gate is for ENTRIES, not for exits.

    Hold-time ladder:
      CHoCH/BOS structural reversal : 2 min  (strong market-structure signal)
      Stream reversal                : 3 min  (15-second window — very noisy)
      Confirmed downtrend            : 3 min  (15m EMA-based, lags near boundary)
      RSI bearish divergence         : 3 min  (chart-based, not price-action)
      Trend invalid (weak + no-allow): 4 min  AND pnl <= -0.20% (give trade time,
                                               exclude pure fee/slippage exits)
    """
    if not meta:
        return None
    trend_alignment = str(meta.get("trend_alignment", "") or "")
    trend_score = float(meta.get("trend_follow_score", 0.0) or 0.0)
    trend_allowed = bool(meta.get("trend_entry_allowed", False))
    # Strong structural reversal — meaningful market-structure signal; 2 min minimum
    if minutes_open >= 2.0 and (bool(meta.get("choch_bearish", False)) or bool(meta.get("bos_bearish", False))):
        return "trend_reversal_exit"
    # Stream reversal is a 15-second metric — very noisy; 3 min + not well in profit
    if minutes_open >= 3.0 and bool(meta.get("stream_reversal", False)) and pnl_pct <= 0.20:
        return "trend_reversal_exit"
    # Confirmed 15m downtrend: 3 min minimum so the EMA can't flip back in noise
    if minutes_open >= 3.0 and trend_alignment == "downtrend":
        return "downtrend_exit"
    # RSI bearish divergence: 3 min minimum
    if minutes_open >= 3.0 and bool(meta.get("rsi_bearish_divergence", False)) and pnl_pct <= 0.25:
        return "bearish_divergence_exit"
    # Trend invalid: 4 min minimum AND position clearly failing (not just fee/slippage)
    # pnl <= -0.20% filters out -0.10~-0.15% fee-level exits that were 65% of all losses
    if minutes_open >= 4.0 and not trend_allowed and trend_score < 0.35 and pnl_pct <= -0.20:
        return "trend_invalid_exit"
    return None


def _ensure_schema() -> None:
    inspector = inspect(engine)
    try:
        closed_columns = {column["name"] for column in inspector.get_columns("closed_positions")}
    except Exception:
        closed_columns = set()
    if "closed_reason" not in closed_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE closed_positions ADD COLUMN closed_reason VARCHAR(100) DEFAULT ''"))
    try:
        live_columns = {column["name"] for column in inspector.get_columns("live_order_log")}
    except Exception:
        live_columns = set()
    live_column_defs = {
        "effect_status": "ALTER TABLE live_order_log ADD COLUMN effect_status VARCHAR(30) DEFAULT 'pending'",
        "linked_position_symbol": "ALTER TABLE live_order_log ADD COLUMN linked_position_symbol VARCHAR(100) DEFAULT ''",
        "linked_closed_symbol": "ALTER TABLE live_order_log ADD COLUMN linked_closed_symbol VARCHAR(100) DEFAULT ''",
    }
    missing_live = [ddl for column, ddl in live_column_defs.items() if column not in live_columns]
    if missing_live:
        with engine.begin() as connection:
            for ddl in missing_live:
                connection.execute(text(ddl))
    try:
        paper_position_columns = {column["name"] for column in inspector.get_columns("paper_positions")}
    except Exception:
        paper_position_columns = set()
    if "peak_pnl_pct" not in paper_position_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE paper_positions ADD COLUMN peak_pnl_pct FLOAT DEFAULT 0.0"))
    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_orders_created_at ON paper_orders(created_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_cycle_journal_run_at ON cycle_journal(run_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_positions_status ON paper_positions(status)"))


def _build_desk_stats(positions: list[PaperPositionRecord]) -> dict[str, dict]:
    desks = {"crypto", "korea", "us"}
    stats: dict[str, dict] = {}
    for desk in desks:
        closed = [row for row in positions if row.desk == desk and row.status == "closed"]
        open_rows = [row for row in positions if row.desk == desk and row.status == "open"]
        wins = sum(1 for row in closed if row.pnl_pct > 0)
        losses = sum(1 for row in closed if row.pnl_pct <= 0)
        stats[desk] = {
            "open_positions": len(open_rows),
            "closed_positions": len(closed),
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / len(closed)) * 100, 1) if closed else 0.0,
            "realized_pnl_pct": round(sum(row.pnl_pct for row in closed), 2),
            "unrealized_pnl_pct": round(sum(row.pnl_pct for row in open_rows), 2),
            "open_notional_pct": round(sum(_size_to_notional(row.size) for row in open_rows), 2),
        }
    return stats


def _close_reason_stats(closed_rows: list[PaperPositionRecord]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for row in closed_rows:
        reason = row.closed_reason or "unknown"
        bucket = stats.setdefault(reason, {"count": 0, "wins": 0, "losses": 0, "pnl_pct": 0.0})
        bucket["count"] += 1
        if row.pnl_pct > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        bucket["pnl_pct"] = round(float(bucket["pnl_pct"]) + row.pnl_pct, 2)
    return stats


def _desk_close_reason_stats(closed_rows: list[PaperPositionRecord]) -> dict[str, dict]:
    by_desk: dict[str, list[PaperPositionRecord]] = {}
    for row in closed_rows:
        by_desk.setdefault(row.desk, []).append(row)
    return {desk: _close_reason_stats(rows) for desk, rows in by_desk.items()}


def _symbol_performance_stats(positions: list[PaperPositionRecord]) -> list[dict]:
    buckets: dict[tuple[str, str], dict] = {}
    for row in positions:
        if row.status != "closed":
            continue
        key = (row.desk, row.symbol)
        bucket = buckets.setdefault(
            key,
            {
                "desk": row.desk,
                "symbol": row.symbol,
                "count": 0,
                "wins": 0,
                "losses": 0,
                "pnl_pct": 0.0,
                "stop_like_count": 0,
            },
        )
        bucket["count"] += 1
        bucket["pnl_pct"] = round(float(bucket["pnl_pct"]) + row.pnl_pct, 2)
        if row.pnl_pct > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        if row.closed_reason in {"stop_hit", "early_failure"}:
            bucket["stop_like_count"] += 1

    ranked = sorted(
        buckets.values(),
        key=lambda item: (item["stop_like_count"], item["losses"], -item["pnl_pct"]),
        reverse=True,
    )
    return ranked[:6]


def _close_position(position: PaperPositionRecord, reason: str) -> None:
    if position.status == "closed":
        return
    position.status = "closed"
    position.closed_at = utcnow_iso()
    if position.desk == "crypto":
        position.exit_price = _paper_exit_price(position.current_price, position.symbol, position.closed_at)
        position.pnl_pct = _paper_net_pnl_pct(position.entry_price, position.current_price, position.symbol, position.closed_at)
    else:
        position.exit_price = position.current_price
    position.closed_reason = reason
    session = object_session(position)
    if session is not None:
        shadow = session.execute(
            select(PositionRecord).where(
                PositionRecord.desk == position.desk,
                PositionRecord.symbol == position.symbol,
            )
        ).scalar_one_or_none()
        if shadow is not None:
            session.delete(shadow)
    _notify_trade_exit(_paper_trade_payload(position), reason)


_db_initialized = False


def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    Base.metadata.create_all(bind=engine)
    _ensure_schema()
    _db_initialized = True


def rebuild_db() -> None:
    engine.dispose()
    if db_path.exists():
        backup_path = db_path.with_suffix(".backup.db")
        if backup_path.exists():
            backup_path.unlink()
        db_path.replace(backup_path)
    Base.metadata.create_all(bind=engine)


def load_company_state() -> CompanyState:
    init_db()
    try:
        # Read StateRecord and extract all scalar data FIRST, then close session
        # before calling nested load_* functions. Prevents 6 concurrent sessions
        # from stacking up inside the same with-block (Python evaluates all
        # constructor args before the with-block exits).
        with SessionLocal() as db:
            rec = db.get(StateRecord, "primary")
            if rec is None:
                return CompanyState()
            _rec = {
                "stance": rec.stance,
                "regime": rec.regime,
                "risk_budget": rec.risk_budget,
                "allow_new_entries": rec.allow_new_entries,
                "execution_mode": rec.execution_mode,
                "notes": list(rec.notes or []),
                "trader_principles": list(rec.trader_principles or []),
                "latest_signals": list(rec.latest_signals or []),
                "market_snapshot": dict(rec.market_snapshot or {}),
                "session_state": dict(rec.session_state or {}),
                "desk_views": dict(rec.desk_views or {}),
                "strategy_book": dict(rec.strategy_book or {}),
                "agent_runs": [AgentSnapshot.model_validate(item) for item in (rec.agent_runs or [])],
                "updated_at": rec.updated_at or utcnow_iso(),
            }
        # Session is now closed — nested sessions open one at a time
        return CompanyState(
            **_rec,
            daily_summary=load_daily_summary(),
            performance_stats=load_performance_quick_stats(),
            execution_log=load_recent_execution_log(limit=10),
            open_positions=load_paper_open_positions(),
            recent_journal=load_recent_journal(limit=8),
        )
    except OperationalError:
        rebuild_db()
        return CompanyState()


def save_company_state(state: CompanyState) -> CompanyState:
    init_db()
    with SessionLocal() as db:
        rec = db.get(StateRecord, "primary")
        if rec is None:
            rec = StateRecord(key="primary")
            db.add(rec)
        rec.stance = state.stance
        rec.regime = state.regime
        rec.risk_budget = state.risk_budget
        rec.allow_new_entries = state.allow_new_entries
        rec.execution_mode = state.execution_mode
        rec.notes = state.notes
        rec.trader_principles = state.trader_principles
        rec.latest_signals = state.latest_signals
        rec.market_snapshot = state.market_snapshot
        rec.session_state = state.session_state
        rec.desk_views = state.desk_views
        rec.strategy_book = state.strategy_book
        rec.agent_runs = [item.model_dump() for item in state.agent_runs]
        rec.updated_at = utcnow_iso()
        state.updated_at = rec.updated_at
        db.commit()
    return state


def save_paper_orders(orders: list[PaperOrder]) -> None:
    if not orders:
        return
    init_db()
    with SessionLocal() as db:
        for order in orders:
            db.add(
                PaperOrderRecord(
                    created_at=order.created_at,
                    desk=order.desk,
                    action=order.action,
                    focus=order.focus,
                    size=order.size,
                    rationale=order.rationale,
                )
            )
        db.commit()


def save_cycle_journal(entry: CycleJournalEntry) -> None:
    init_db()
    with SessionLocal() as db:
        db.add(
            CycleJournalRecord(
                run_at=entry.run_at,
                stance=entry.stance,
                regime=entry.regime,
                company_focus=entry.company_focus,
                summary=entry.summary,
                orders=[order.model_dump() for order in entry.orders],
            )
        )
        db.commit()


def _fetch_zombie_prices(pos_pairs: list[tuple[str, str]], price_lookup: dict[tuple[str, str], float]) -> None:
    """Fetch live prices for (desk, symbol) pairs missing from market_snapshot.
    Called OUTSIDE any DB session to avoid holding write locks during HTTP calls."""
    from app.services.market_gateway import UPBIT_TICKER_URL, get_naver_daily_prices

    zombie_korea = [sym for desk, sym in pos_pairs if desk == "korea" and ("korea", sym) not in price_lookup]
    zombie_crypto = [sym for desk, sym in pos_pairs if desk == "crypto" and ("crypto", sym) not in price_lookup]

    for sym in zombie_korea:
        try:
            candles = get_naver_daily_prices(sym, count=2)
            if candles:
                price = float(candles[-1].get("close") or 0)
                if price > 0:
                    price_lookup[("korea", sym)] = price
        except Exception:
            pass

    if zombie_crypto:
        try:
            resp = requests.get(UPBIT_TICKER_URL, params={"markets": ",".join(zombie_crypto)}, timeout=8)
            resp.raise_for_status()
            for item in resp.json():
                market = str(item.get("market") or "")
                price = float(item.get("trade_price") or 0)
                if market and price > 0:
                    price_lookup[("crypto", market)] = price
        except Exception:
            pass


def sync_paper_positions(paper_orders: list[PaperOrder], market_snapshot: dict) -> None:
    init_db()
    price_lookup = _build_price_lookup(market_snapshot)
    cycle_signal_meta = _build_cycle_signal_meta(paper_orders)
    opened_alerts: list[dict] = []

    # Read (desk, symbol) pairs first, close session, THEN do HTTP calls outside any DB lock
    with SessionLocal() as _rdb:
        _pairs = [(p.desk, p.symbol) for p in _rdb.execute(
            select(PaperPositionRecord).where(PaperPositionRecord.status == "open")
        ).scalars().all()]

    _fetch_zombie_prices(_pairs, price_lookup)

    with SessionLocal() as db:
        open_positions = db.execute(
            select(PaperPositionRecord).where(PaperPositionRecord.status == "open").order_by(PaperPositionRecord.id.asc())
        ).scalars().all()

        for position in open_positions:
            current_price = price_lookup.get((position.desk, position.symbol), position.current_price)
            if current_price and position.entry_price > 0:
                position.current_price = current_price
                if position.desk == "crypto":
                    position.pnl_pct = _paper_net_pnl_pct(position.entry_price, current_price, position.symbol, str(position.cycles_open))
                else:
                    position.pnl_pct = round(((current_price - position.entry_price) / position.entry_price) * 100, 2)
                position.peak_pnl_pct = max(float(position.peak_pnl_pct or 0.0), position.pnl_pct)
            position.cycles_open += 1
            target_pct, stop_pct, max_cycles = _position_thresholds(position.desk, position.action)
            # early_failure: exit if still deeply losing after fast_fail_cycle cycles
            # stale_floor:   exit near max_cycles if barely profitable
            early_failure_pct = round(stop_pct * 0.7, 2)   # 70% of full stop (e.g. -1.4% at -2% stop)
            stale_floor_pct = round(max(target_pct * 0.15, 0.20), 2)   # 15% of target (e.g. +0.60% at +4% target)
            # Time-based fast_fail (cycle-length agnostic). With fast cycle (8s) + rapid guard,
            # cycle counts are no longer a reliable proxy for elapsed time. Use opened_at directly.
            minutes_open = 0.0
            try:
                opened_dt = datetime.fromisoformat(str(position.opened_at).replace("Z", "+00:00"))
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                minutes_open = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
            except (ValueError, TypeError):
                minutes_open = float(position.cycles_open) * 0.75  # fallback ~45s/cycle assumption
            if position.desk == "crypto":
                fast_fail_minutes = 16.0  # 24→16: cut failed ignitions faster; trail rules protect winners
            elif position.desk == "korea":
                fast_fail_minutes = 30.0 if position.action == "attack_opening_drive" else 45.0
            else:
                fast_fail_minutes = 30.0
            # Cycles fallback for non-crypto desks (korea/us still use cycle-based check below)
            if position.desk == "crypto":
                fast_fail_cycle = 12
            elif position.desk == "korea":
                fast_fail_cycle = 20 if position.action == "attack_opening_drive" else 30
            else:
                fast_fail_cycle = 20
            if position.desk == "crypto":
                peak_pnl = float(position.peak_pnl_pct or position.pnl_pct or 0.0)
                trail_giveback, profit_floor = _crypto_trail_rules(peak_pnl)
                protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
                if position.pnl_pct >= target_pct:
                    _close_position(position, "target_hit")
                elif position.pnl_pct <= stop_pct:
                    _close_position(position, "stop_hit")
                # Time-based failed_ignition: only fire after the intended 24-min window AND
                # only when the position never showed life (peak <= 0.10%). This protects
                # against fast-cycle noise and ensures "failed ignition" really means failed.
                elif (no_lift_reason := _crypto_no_lift_exit_reason(minutes_open, peak_pnl, position.pnl_pct)):
                    _close_position(position, no_lift_reason)
                elif trend_exit_reason := _crypto_trend_exit_reason(cycle_signal_meta.get(position.symbol, {}), position.pnl_pct, minutes_open):
                    _close_position(position, trend_exit_reason)
                elif minutes_open >= fast_fail_minutes and position.pnl_pct <= -0.60 and peak_pnl <= 0.10:
                    _close_position(position, "failed_ignition")
                elif trail_giveback and position.pnl_pct <= protect_level:
                    _close_position(position, "profit_protect" if peak_pnl < 1.8 else "trend_trail")
                elif peak_pnl >= 0.40 and minutes_open >= 3.0 and position.pnl_pct <= max(-0.50, peak_pnl - 1.20):
                    # Wider failed_followthrough: covers smaller peaks (0.40%+) and fires earlier (3 min).
                    # Catches reversals before they reach the hard -2% stop.
                    _close_position(position, "failed_followthrough")
                elif position.cycles_open >= max_cycles and position.pnl_pct < 0.8:
                    _close_position(position, "time_exit")
                continue
            if position.pnl_pct >= target_pct:
                _close_position(position, "target_hit")
            elif position.pnl_pct <= stop_pct:
                _close_position(position, "stop_hit")
            elif position.cycles_open >= fast_fail_cycle and position.pnl_pct <= early_failure_pct:
                _close_position(position, "early_failure")
            elif position.cycles_open >= max(2, max_cycles - 1) and position.pnl_pct < stale_floor_pct:
                _close_position(position, "stale_exit")
            elif position.cycles_open >= max_cycles:
                _close_position(position, "time_exit")

        existing_open_keys = {(item.desk, item.symbol) for item in open_positions if item.status == "open"}
        for order in paper_orders:
            meta = _extract_order_meta(order.action, order.rationale)
            symbol = str(meta.get("symbol", "") or order.symbol or "").strip()
            reference_price = float(meta.get("reference_price", 0.0) or order.reference_price or 0.0)
            if meta.get("status") != "planned" or not symbol or reference_price <= 0:
                continue
            if (order.desk, symbol) in existing_open_keys:
                continue
            entry_price = _paper_entry_price(reference_price, symbol, order.created_at) if order.desk == "crypto" else reference_price
            position = PaperPositionRecord(
                desk=order.desk,
                symbol=symbol,
                status="open",
                action=order.action,
                size=order.size,
                opened_at=order.created_at,
                entry_price=entry_price,
                current_price=reference_price,
                pnl_pct=0.0,
                peak_pnl_pct=0.0,
                cycles_open=0,
                focus=order.focus,
            )
            db.add(
                position
            )
            opened_alerts.append(_paper_trade_payload(position, meta))
        db.commit()
    for payload in opened_alerts:
        _notify_trade_entry(payload)


def load_crypto_rapid_guard_symbols() -> list[str]:
    init_db()
    with SessionLocal() as db:
        paper_symbols = [
            str(row.symbol)
            for row in db.execute(
                select(PaperPositionRecord).where(
                    PaperPositionRecord.status == "open",
                    PaperPositionRecord.desk == "crypto",
                )
            ).scalars().all()
            if str(row.symbol or "").strip()
        ]
        live_symbols = [
            str(row.symbol)
            for row in db.execute(
                select(PositionRecord).where(PositionRecord.desk == "crypto")
            ).scalars().all()
            if str(row.symbol or "").strip()
        ]
    return list(dict.fromkeys(paper_symbols + live_symbols))


def rapid_guard_crypto_positions(prices: dict[str, float]) -> dict:
    """Fast price-only guard for open crypto positions between full strategy cycles."""
    if not prices:
        return {"checked": 0, "paper_closed": 0, "live_closed": 0}
    init_db()
    checked = 0
    paper_closed = 0
    closed_symbols: list[tuple[str, str]] = []
    with SessionLocal() as db:
        rows = db.execute(
            select(PaperPositionRecord).where(
                PaperPositionRecord.status == "open",
                PaperPositionRecord.desk == "crypto",
            )
        ).scalars().all()
        for position in rows:
            current_price = float(prices.get(position.symbol, 0.0) or 0.0)
            if current_price <= 0 or position.entry_price <= 0:
                continue
            checked += 1
            position.current_price = current_price
            position.pnl_pct = _paper_net_pnl_pct(position.entry_price, current_price, position.symbol, "rapid")
            position.peak_pnl_pct = max(float(position.peak_pnl_pct or 0.0), position.pnl_pct)
            target_pct, stop_pct, _ = _position_thresholds(position.desk, position.action)
            peak_pnl = float(position.peak_pnl_pct or position.pnl_pct or 0.0)
            trail_giveback, profit_floor = _crypto_trail_rules(peak_pnl)
            protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
            minutes_open = 0.0
            try:
                opened_dt = datetime.fromisoformat(str(position.opened_at).replace("Z", "+00:00"))
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                minutes_open = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
            except (ValueError, TypeError):
                minutes_open = 0.0
            if position.pnl_pct >= target_pct:
                closed_symbols.append((position.symbol, "rapid_target_hit"))
                _close_position(position, "rapid_target_hit")
                paper_closed += 1
            elif (
                0.40 <= peak_pnl < 0.80
                and minutes_open >= 1.0
                and position.pnl_pct <= max(-0.55, peak_pnl - 1.10)
            ):
                # Mid-range failed breakout: saw initial upside (0.40-0.80%) but fully reversed.
                # Cuts well before the hard -2% stop — saves ~1-1.5% on API3-style reversals.
                # Not added to STOP_LIKE_EXIT_REASONS so it doesn't cascade into risk throttling.
                closed_symbols.append((position.symbol, "failed_breakout_exit"))
                _close_position(position, "failed_breakout_exit")
                paper_closed += 1
            elif position.pnl_pct <= stop_pct:
                closed_symbols.append((position.symbol, "rapid_stop_hit"))
                _close_position(position, "rapid_stop_hit")
                paper_closed += 1
            elif minutes_open >= 4.0 and peak_pnl <= 0.05 and position.pnl_pct <= -0.75:
                closed_symbols.append((position.symbol, "rapid_failed_start"))
                _close_position(position, "rapid_failed_start")
                paper_closed += 1
            elif (
                minutes_open >= 4.0
                and peak_pnl <= 0.05
                and position.pnl_pct <= -0.10
                and _has_recent_failed_paper_symbol(db, position)
            ):
                closed_symbols.append((position.symbol, "rapid_repeat_symbol_failure"))
                _close_position(position, "rapid_repeat_symbol_failure")
                paper_closed += 1
            elif no_lift_reason := _crypto_no_lift_exit_reason(minutes_open, peak_pnl, position.pnl_pct, rapid=True):
                closed_symbols.append((position.symbol, no_lift_reason))
                _close_position(position, no_lift_reason)
                paper_closed += 1
            elif trail_giveback and position.pnl_pct <= protect_level:
                reason = "rapid_profit_protect" if peak_pnl < 1.8 else "rapid_trend_trail"
                closed_symbols.append((position.symbol, reason))
                _close_position(position, reason)
                paper_closed += 1
        db.commit()
    for symbol, reason in closed_symbols:
        close_position_by_symbol("crypto", symbol, prices, reason=reason)
    update_positions_unrealized(prices)
    live_closed = len(auto_exit_positions(prices, skip_desks=set()))
    return {"checked": checked, "paper_closed": paper_closed, "live_closed": live_closed}


def load_recent_orders(limit: int = 10) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(select(PaperOrderRecord).order_by(PaperOrderRecord.id.desc()).limit(limit)).scalars().all()
            return [
                {
                    **(lambda meta: {
                        "created_at": row.created_at,
                        "desk": row.desk,
                        "action": row.action,
                        "focus": row.focus,
                        "size": row.size,
                        "notional_pct": meta["notional_pct"],
                        "status": meta["status"],
                        "pnl_estimate_pct": meta["pnl_estimate_pct"],
                        "rationale": row.rationale or [],
                    })(_extract_order_meta(row.action, row.rationale or []))
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_open_positions(limit: int = 10) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord).where(PaperPositionRecord.status == "open").order_by(PaperPositionRecord.id.desc()).limit(limit)
            ).scalars().all()
            return [
                {
                    "desk": row.desk,
                    "symbol": row.symbol,
                    "action": row.action,
                    "size": row.size,
                    "notional_pct": _size_to_notional(row.size),
                    "opened_at": row.opened_at,
                    "entry_price": row.entry_price,
                    "current_price": row.current_price,
                    "pnl_pct": row.pnl_pct,
                    "peak_pnl_pct": row.peak_pnl_pct,
                    "cycles_open": row.cycles_open,
                    "focus": row.focus,
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_closed_positions(limit: int = 10) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord).where(PaperPositionRecord.status == "closed").order_by(PaperPositionRecord.id.desc()).limit(limit)
            ).scalars().all()
            return [
                {
                    "desk": row.desk,
                    "symbol": row.symbol,
                    "action": row.action,
                    "size": row.size,
                    "notional_pct": _size_to_notional(row.size),
                    "opened_at": row.opened_at,
                    "closed_at": row.closed_at,
                    "entry_price": row.entry_price,
                    "exit_price": row.exit_price,
                    "pnl_pct": row.pnl_pct,
                    "cycles_open": row.cycles_open,
                    "closed_reason": row.closed_reason,
                    "focus": row.focus,
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_daily_summary() -> dict:
    init_db()
    today = _today_local_date()
    start_iso, end_iso = _local_day_utc_bounds_iso(today)
    try:
        with SessionLocal() as db:
            orders = db.execute(
                select(PaperOrderRecord).where(
                    PaperOrderRecord.created_at >= start_iso,
                    PaperOrderRecord.created_at < end_iso,
                )
            ).scalars().all()
            journal = db.execute(
                select(CycleJournalRecord).where(
                    CycleJournalRecord.run_at >= start_iso,
                    CycleJournalRecord.run_at < end_iso,
                )
            ).scalars().all()
            positions = db.execute(select(PaperPositionRecord)).scalars().all()
            opened_today = [row for row in positions if _local_date_from_iso(row.opened_at) == today]
            closed_today = [row for row in positions if row.closed_at and _local_date_from_iso(row.closed_at) == today]
            open_positions = [row for row in positions if row.status == "open"]
            order_dicts = [
                {
                    "desk": row.desk,
                    "action": row.action,
                    "size": row.size,
                    "rationale": row.rationale or [],
                }
                for row in orders
            ]
            planned_orders = 0
            active_desks: set[str] = set()
            estimated_pnl = 0.0
            current_cycle_planned_orders = 0
            current_cycle_active_desks: set[str] = set()
            current_cycle_estimated_pnl = 0.0
            latest_order_timestamp = max((row.created_at for row in orders), default="")
            for row in order_dicts:
                meta = _extract_order_meta(row["action"], row["rationale"])
                if meta.get("status") == "planned":
                    planned_orders += 1
                    active_desks.add(row["desk"])
                    estimated_pnl += float(meta.get("pnl_estimate_pct", 0.0) or 0.0)
            for source_row, row in zip(orders, order_dicts):
                if source_row.created_at != latest_order_timestamp:
                    continue
                meta = _extract_order_meta(row["action"], row["rationale"])
                if meta.get("status") == "planned":
                    current_cycle_planned_orders += 1
                    current_cycle_active_desks.add(row["desk"])
                    current_cycle_estimated_pnl += float(meta.get("pnl_estimate_pct", 0.0) or 0.0)
            all_closed = [row for row in positions if row.status == "closed"]
            wins = sum(1 for row in closed_today if row.pnl_pct > 0)
            losses = sum(1 for row in closed_today if row.pnl_pct <= 0)
            closed_count = len(closed_today)
            win_rate = round((wins / closed_count) * 100, 1) if closed_count else 0.0
            realized_pnl = round(sum(row.pnl_pct for row in closed_today), 2)
            unrealized_pnl = round(sum(row.pnl_pct for row in open_positions), 2)
            expectancy_pct = round(realized_pnl / closed_count, 2) if closed_count else 0.0
            # Cumulative (all-time) — compounding base
            cumulative_realized_pnl = round(sum(row.pnl_pct for row in all_closed), 2)
            cumulative_wins = sum(1 for row in all_closed if row.pnl_pct > 0)
            cumulative_losses = sum(1 for row in all_closed if row.pnl_pct <= 0)
            cumulative_closed = len(all_closed)
            cumulative_win_rate = round((cumulative_wins / cumulative_closed) * 100, 1) if cumulative_closed else 0.0
            # desk_stats must use TODAY's closed + ALL currently-open positions.
            # Bug: passing all-time `positions` caused cumulative realized_pnl to
            # exceed the -6% drawdown floor, permanently blocking new entries.
            desk_stats = _build_desk_stats(closed_today + open_positions)
            gross_open_notional = round(sum(_size_to_notional(row.size) for row in open_positions), 2)
            base_capital = float(settings.paper_capital_krw)
            # Effective capital grows with cumulative P&L (compounding)
            effective_capital = round(base_capital * (1 + cumulative_realized_pnl / 100))
            realized_pnl_krw = round(effective_capital * realized_pnl / 100)
            unrealized_pnl_krw = round(effective_capital * unrealized_pnl / 100)
            expectancy_krw = round(effective_capital * expectancy_pct / 100)
            return {
                "date": today,
                "cycles_run": len(journal),
                "orders_logged": len(order_dicts),
                "planned_orders": planned_orders,
                "current_cycle_planned_orders": current_cycle_planned_orders,
                "active_desks": sorted(active_desks),
                "current_cycle_active_desks": sorted(current_cycle_active_desks),
                "estimated_pnl_pct": round(estimated_pnl, 2),
                "current_cycle_estimated_pnl_pct": round(current_cycle_estimated_pnl, 2),
                "open_positions": len(open_positions),
                "opened_positions": len(opened_today),
                "closed_positions": closed_count,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "expectancy_pct": expectancy_pct,
                "realized_pnl_pct": realized_pnl,
                "unrealized_pnl_pct": unrealized_pnl,
                "realized_pnl_krw": realized_pnl_krw,
                "unrealized_pnl_krw": unrealized_pnl_krw,
                "expectancy_krw": expectancy_krw,
                "gross_open_notional_pct": gross_open_notional,
                "close_reason_stats": _close_reason_stats(closed_today),
                "desk_close_reason_stats": _desk_close_reason_stats(closed_today),
                "symbol_performance_stats": _symbol_performance_stats(positions),
                "desk_stats": desk_stats,
                "cumulative_realized_pnl_pct": cumulative_realized_pnl,
                "cumulative_closed_positions": cumulative_closed,
                "cumulative_wins": cumulative_wins,
                "cumulative_losses": cumulative_losses,
                "cumulative_win_rate": cumulative_win_rate,
                "effective_capital_krw": effective_capital,
            }
    except OperationalError:
        rebuild_db()
        return {
            "date": today,
            "cycles_run": 0,
            "orders_logged": 0,
            "planned_orders": 0,
            "current_cycle_planned_orders": 0,
            "active_desks": [],
            "current_cycle_active_desks": [],
            "estimated_pnl_pct": 0.0,
            "current_cycle_estimated_pnl_pct": 0.0,
            "open_positions": 0,
            "opened_positions": 0,
            "closed_positions": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "expectancy_pct": 0.0,
            "realized_pnl_pct": 0.0,
            "unrealized_pnl_pct": 0.0,
            "realized_pnl_krw": 0,
            "unrealized_pnl_krw": 0,
            "expectancy_krw": 0,
            "gross_open_notional_pct": 0.0,
            "close_reason_stats": {},
            "desk_close_reason_stats": {},
            "symbol_performance_stats": [],
            "desk_stats": {
                "crypto": {"open_positions": 0, "closed_positions": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "realized_pnl_pct": 0.0, "unrealized_pnl_pct": 0.0, "open_notional_pct": 0.0},
                "korea": {"open_positions": 0, "closed_positions": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "realized_pnl_pct": 0.0, "unrealized_pnl_pct": 0.0, "open_notional_pct": 0.0},
                "us": {"open_positions": 0, "closed_positions": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "realized_pnl_pct": 0.0, "unrealized_pnl_pct": 0.0, "open_notional_pct": 0.0},
            },
        }


def load_recent_journal(limit: int = 8) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(select(CycleJournalRecord).order_by(CycleJournalRecord.id.desc()).limit(limit)).scalars().all()
            return [
                {
                    "run_at": row.run_at,
                    "stance": row.stance,
                    "regime": row.regime,
                    "company_focus": row.company_focus,
                    "summary": row.summary or [],
                    "orders": [
                        {
                            **order,
                            **_extract_order_meta(str(order.get("action", "")), list(order.get("rationale", []) or [])),
                        }
                        for order in (row.orders or [])
                    ],
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


# ── Position management ────────────────────────────────────────────────────────

def open_or_skip_position(desk: str, symbol: str, entry_price: float, notional_pct: float, action: str) -> bool:
    """Open a new position for desk+symbol. Skips if one already exists. Returns True if opened."""
    init_db()
    with SessionLocal() as db:
        existing = db.execute(
            select(PositionRecord).where(
                PositionRecord.desk == desk,
                PositionRecord.symbol == symbol,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False
        opened_at = utcnow_iso()
        stored_entry_price = _paper_entry_price(entry_price, symbol, opened_at) if desk == "crypto" else entry_price
        db.add(PositionRecord(
            desk=desk,
            symbol=symbol,
            entry_price=stored_entry_price,
            current_price=entry_price,
            notional_pct=notional_pct,
            action=action,
            unrealized_pnl_pct=0.0,
            opened_at=opened_at,
        ))
        db.commit()
        return True


def close_positions_for_desk(desk: str, prices: dict[str, float]) -> list[ClosedPosition]:
    """Close all open positions for a desk at current prices. Records realized P&L."""
    init_db()
    closed: list[ClosedPosition] = []
    with SessionLocal() as db:
        positions = db.execute(
            select(PositionRecord).where(PositionRecord.desk == desk)
        ).scalars().all()
        for pos in positions:
            raw_exit_price = prices.get(pos.symbol, pos.current_price) or pos.current_price
            exit_price = _paper_exit_price(raw_exit_price, pos.symbol, "desk_exit") if pos.desk == "crypto" else raw_exit_price
            realized_pnl_pct = (
                _paper_net_pnl_pct(pos.entry_price, raw_exit_price, pos.symbol, "desk_exit")
                if pos.desk == "crypto"
                else round(((exit_price - pos.entry_price) / pos.entry_price) * 100, 4) if pos.entry_price > 0 else 0.0
            )
            db.add(ClosedPositionRecord(
                desk=pos.desk,
                symbol=pos.symbol,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                notional_pct=pos.notional_pct,
                realized_pnl_pct=realized_pnl_pct,
                won=realized_pnl_pct > 0,
                opened_at=pos.opened_at,
                closed_at=utcnow_iso(),
                closed_reason="desk_exit",
            ))
            closed.append(ClosedPosition(
                desk=pos.desk,
                symbol=pos.symbol,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                notional_pct=pos.notional_pct,
                realized_pnl_pct=realized_pnl_pct,
                won=realized_pnl_pct > 0,
                opened_at=pos.opened_at,
                closed_reason="desk_exit",
            ))
            db.delete(pos)
        db.commit()
    return closed


def close_position_by_symbol(desk: str, symbol: str, prices: dict[str, float], reason: str) -> ClosedPosition | None:
    init_db()
    with SessionLocal() as db:
        pos = db.execute(
            select(PositionRecord).where(
                PositionRecord.desk == desk,
                PositionRecord.symbol == symbol,
            )
        ).scalar_one_or_none()
        if pos is None:
            return None
        raw_exit_price = prices.get(pos.symbol, pos.current_price) or pos.current_price
        exit_price = _paper_exit_price(raw_exit_price, pos.symbol, reason) if pos.desk == "crypto" else raw_exit_price
        realized_pnl_pct = (
            _paper_net_pnl_pct(pos.entry_price, raw_exit_price, pos.symbol, reason)
            if pos.desk == "crypto"
            else round(((exit_price - pos.entry_price) / pos.entry_price) * 100, 4) if pos.entry_price > 0 else 0.0
        )
        record = ClosedPositionRecord(
            desk=pos.desk,
            symbol=pos.symbol,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            notional_pct=pos.notional_pct,
            realized_pnl_pct=realized_pnl_pct,
            won=realized_pnl_pct > 0,
            opened_at=pos.opened_at,
            closed_at=utcnow_iso(),
            closed_reason=reason,
        )
        db.add(record)
        closed = ClosedPosition(
            desk=pos.desk,
            symbol=pos.symbol,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            notional_pct=pos.notional_pct,
            realized_pnl_pct=realized_pnl_pct,
            won=realized_pnl_pct > 0,
            opened_at=pos.opened_at,
            closed_reason=reason,
        )
        db.delete(pos)
        db.commit()
        return closed


def sync_live_crypto_positions(account_positions: list[dict], prices: dict[str, float]) -> dict:
    """Reconcile crypto positions against live Upbit balances."""
    return sync_live_positions("crypto", account_positions, prices, default_action="live_sync")


def sync_live_positions(desk: str, account_positions: list[dict], prices: dict[str, float], default_action: str = "live_sync") -> dict:
    """Reconcile broker-reported positions against persisted positions for one desk."""
    init_db()
    broker_markets = {str(item.get("market", "")).strip() for item in account_positions if str(item.get("market", "")).strip()}
    opened = 0
    updated = 0
    closed = 0
    with SessionLocal() as db:
        current_rows = db.execute(select(PositionRecord).where(PositionRecord.desk == desk)).scalars().all()
        current_by_symbol = {row.symbol: row for row in current_rows}

        for row in current_rows:
            if row.symbol in broker_markets:
                continue
            exit_price = prices.get(row.symbol, row.current_price) or row.current_price
            realized_pnl_pct = (
                round(((exit_price - row.entry_price) / row.entry_price) * 100, 4)
                if row.entry_price > 0 and exit_price > 0 else 0.0
            )
            db.add(
                ClosedPositionRecord(
                    desk=row.desk,
                    symbol=row.symbol,
                    entry_price=row.entry_price,
                    exit_price=exit_price,
                    notional_pct=row.notional_pct,
                    realized_pnl_pct=realized_pnl_pct,
                    won=realized_pnl_pct > 0,
                    opened_at=row.opened_at,
                    closed_at=utcnow_iso(),
                    closed_reason="broker_sync_exit",
                )
            )
            db.delete(row)
            closed += 1

        capital_base = float(settings.live_capital_krw or settings.paper_capital_krw or 0.0)
        for item in account_positions:
            market = str(item.get("market", "")).strip()
            if not market:
                continue
            current_price = float(prices.get(market) or item.get("avg_buy_price") or 0.0)
            entry_price = float(item.get("avg_buy_price") or current_price or 0.0)
            total_volume = float(item.get("total_volume") or 0.0)
            market_value = current_price * total_volume
            notional_pct = round((market_value / capital_base), 4) if capital_base > 0 and market_value > 0 else 0.0
            unrealized_pnl_pct = (
                round(((current_price - entry_price) / entry_price) * 100, 4)
                if current_price > 0 and entry_price > 0 else 0.0
            )
            existing = current_by_symbol.get(market)
            if existing is None:
                db.add(
                    PositionRecord(
                        desk=desk,
                        symbol=market,
                        entry_price=entry_price,
                        current_price=current_price,
                        notional_pct=notional_pct,
                        action=default_action,
                        unrealized_pnl_pct=unrealized_pnl_pct,
                        opened_at=utcnow_iso(),
                    )
                )
                opened += 1
                continue
            existing.entry_price = entry_price or existing.entry_price
            existing.current_price = current_price or existing.current_price
            existing.notional_pct = notional_pct
            existing.action = existing.action or default_action
            existing.unrealized_pnl_pct = unrealized_pnl_pct
            updated += 1
        db.commit()
    return {
        "desk": desk,
        "broker_positions": len(account_positions),
        "opened": opened,
        "updated": updated,
        "closed": closed,
    }


def update_positions_unrealized(prices: dict[str, float]) -> None:
    """Refresh unrealized P&L for all open positions using latest market prices."""
    if not prices:
        return
    init_db()
    with SessionLocal() as db:
        positions = db.execute(select(PositionRecord)).scalars().all()
        for pos in positions:
            current_price = prices.get(pos.symbol)
            if current_price and current_price > 0 and pos.entry_price > 0:
                pos.current_price = current_price
                pos.unrealized_pnl_pct = (
                    _paper_net_pnl_pct(pos.entry_price, current_price, pos.symbol, "mark")
                    if pos.desk == "crypto"
                    else round(((current_price - pos.entry_price) / pos.entry_price) * 100, 4)
                )
        db.commit()


def auto_exit_positions(prices: dict[str, float], skip_desks: set[str] | None = None) -> list[ClosedPosition]:
    """Close all-time positions using the same desk/action thresholds as paper tracking."""
    from datetime import datetime, timezone
    init_db()
    skip_desks = skip_desks or set()
    closed: list[ClosedPosition] = []
    with SessionLocal() as db:
        positions = db.execute(select(PositionRecord)).scalars().all()
        for pos in positions:
            if pos.desk in skip_desks:
                continue
            current_price = prices.get(pos.symbol, pos.current_price) or pos.current_price
            if not current_price or pos.entry_price <= 0:
                continue
            unrealized = (
                _paper_net_pnl_pct(pos.entry_price, current_price, pos.symbol, "auto_exit")
                if pos.desk == "crypto"
                else round(((current_price - pos.entry_price) / pos.entry_price) * 100, 4)
            )
            try:
                opened = datetime.fromisoformat(pos.opened_at.replace("Z", "+00:00"))
                elapsed_minutes = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            except Exception:
                elapsed_minutes = 0
            target_pct, stop_pct, max_cycles = _position_thresholds(pos.desk, pos.action)
            max_open_minutes = max_cycles * settings.cycle_interval_minutes
            reason = None
            if unrealized >= target_pct:
                reason = "target_hit"
            elif unrealized <= stop_pct:
                reason = "stop_hit"
            elif elapsed_minutes >= max_open_minutes:
                reason = "time_exit"
            if reason:
                exit_price = _paper_exit_price(current_price, pos.symbol, reason) if pos.desk == "crypto" else current_price
                realized_pnl_pct = unrealized
                db.add(ClosedPositionRecord(
                    desk=pos.desk,
                    symbol=pos.symbol,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    notional_pct=pos.notional_pct,
                    realized_pnl_pct=realized_pnl_pct,
                    won=realized_pnl_pct > 0,
                    opened_at=pos.opened_at,
                    closed_at=utcnow_iso(),
                    closed_reason=reason,
                ))
                closed.append(ClosedPosition(
                    desk=pos.desk,
                    symbol=pos.symbol,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    notional_pct=pos.notional_pct,
                    realized_pnl_pct=realized_pnl_pct,
                    won=realized_pnl_pct > 0,
                    opened_at=pos.opened_at,
                    closed_reason=reason,
                ))
                db.delete(pos)
        db.commit()
    return closed


def load_open_positions() -> list[Position]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(select(PositionRecord)).scalars().all()
            return [
                Position(
                    id=row.id,
                    desk=row.desk,
                    symbol=row.symbol,
                    entry_price=row.entry_price,
                    current_price=row.current_price,
                    notional_pct=row.notional_pct,
                    action=row.action,
                    unrealized_pnl_pct=row.unrealized_pnl_pct,
                    opened_at=row.opened_at,
                )
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_paper_open_positions(limit: int = 20) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .where(PaperPositionRecord.status == "open")
                .order_by(PaperPositionRecord.id.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "desk": row.desk,
                    "symbol": row.symbol,
                    "entry_price": row.entry_price,
                    "current_price": row.current_price,
                    "notional_pct": _size_to_notional(row.size),
                    "size": row.size,
                    "action": row.action,
                    "pnl_pct": row.pnl_pct,
                    "unrealized_pnl_pct": row.pnl_pct,
                    "peak_pnl_pct": row.peak_pnl_pct,
                    "cycles_open": row.cycles_open,
                    "opened_at": row.opened_at,
                    "focus": row.focus,
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_paper_closed_positions(limit: int = 50) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .where(PaperPositionRecord.status == "closed")
                .order_by(PaperPositionRecord.id.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "desk": row.desk,
                    "symbol": row.symbol,
                    "entry_price": row.entry_price,
                    "exit_price": row.exit_price,
                    "notional_pct": _size_to_notional(row.size),
                    "size": row.size,
                    "action": row.action,
                    "pnl_pct": row.pnl_pct,
                    "realized_pnl_pct": row.pnl_pct,
                    "won": row.pnl_pct > 0,
                    "opened_at": row.opened_at,
                    "closed_at": row.closed_at,
                    "closed_reason": row.closed_reason or "",
                    "focus": row.focus,
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_closed_positions(limit: int = 50) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(ClosedPositionRecord)
                .order_by(ClosedPositionRecord.id.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "desk": row.desk,
                    "symbol": row.symbol,
                    "entry_price": row.entry_price,
                    "exit_price": row.exit_price,
                    "notional_pct": row.notional_pct,
                    "pnl_pct": row.realized_pnl_pct,
                    "realized_pnl_pct": row.realized_pnl_pct,
                    "won": row.won,
                    "opened_at": row.opened_at,
                    "closed_at": row.closed_at,
                    "closed_reason": row.closed_reason or "",
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def save_live_order_attempts(route_summary: dict, paper_orders: list[PaperOrder]) -> None:
    details = list(route_summary.get("details", []) or [])
    if not details:
        return
    init_db()
    order_lookup = {
        (order.desk, order.symbol, order.action): order
        for order in paper_orders
        if order.status == "planned"
    }
    requested_mode = str(route_summary.get("requested_mode") or "paper")
    applied_mode = str(route_summary.get("applied_mode") or "paper")
    broker_live = bool(route_summary.get("broker_live"))
    with SessionLocal() as db:
        for detail in details:
            desk = str(detail.get("desk", "") or "")
            symbol = str(detail.get("symbol", "") or "")
            action = str(detail.get("action", "") or "")
            order = order_lookup.get((desk, symbol, action))
            broker_order_id = str(detail.get("broker_order_id") or detail.get("uuid") or detail.get("odno") or "")
            broker_state = str(detail.get("state") or detail.get("broker_state") or "")
            request_status = "submitted" if broker_order_id else "fallback"
            effect_status = "pending" if broker_order_id else "noop"
            db.add(
                LiveOrderRecord(
                    created_at=utcnow_iso(),
                    desk=desk,
                    symbol=symbol,
                    action=action,
                    size=str(detail.get("size") or (order.size if order else "")),
                    requested_mode=requested_mode,
                    applied_mode=applied_mode,
                    broker_live=broker_live,
                    request_status=request_status,
                    broker_order_id=broker_order_id,
                    broker_state=broker_state,
                    reason=str(detail.get("reason", "") or ""),
                    message=str(detail.get("message", "") or ""),
                    effect_status=effect_status,
                    payload=dict(detail),
                )
            )
        db.commit()


def load_recent_live_orders(limit: int = 10) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(select(LiveOrderRecord).order_by(LiveOrderRecord.id.desc()).limit(limit)).scalars().all()
            return [
                {
                    "created_at": row.created_at,
                    "source": "live",
                    "desk": row.desk,
                    "symbol": row.symbol,
                    "action": row.action,
                    "focus": "",
                    "size": row.size,
                    "notional_pct": 0.0,
                    "status": row.request_status,
                    "pnl_estimate_pct": 0.0,
                    "rationale": [],
                    "requested_mode": row.requested_mode,
                    "applied_mode": row.applied_mode,
                    "broker_live": row.broker_live,
                    "broker_order_id": row.broker_order_id,
                    "broker_state": row.broker_state,
                    "reason": row.reason,
                    "message": row.message,
                    "effect_status": row.effect_status,
                    "linked_position_symbol": row.linked_position_symbol,
                    "linked_closed_symbol": row.linked_closed_symbol,
                    "payload": row.payload or {},
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_active_live_order_locks() -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(LiveOrderRecord)
                .where(
                    LiveOrderRecord.broker_live.is_(True),
                    LiveOrderRecord.effect_status.in_(list(ACTIVE_LIVE_EFFECT_STATUSES)),
                )
                .order_by(LiveOrderRecord.id.desc())
                .limit(50)
            ).scalars().all()
            locks: list[dict] = []
            for row in rows:
                action = str(row.action or "")
                if action in ACTIONABLE_ENTRY_ACTIONS:
                    intent = "entry"
                elif action in ACTIONABLE_EXIT_ACTIONS:
                    intent = "exit"
                else:
                    intent = "other"
                locks.append(
                    {
                        "desk": row.desk,
                        "symbol": row.symbol,
                        "action": action,
                        "intent": intent,
                        "request_status": row.request_status,
                        "effect_status": row.effect_status,
                        "broker_order_id": row.broker_order_id,
                    }
                )
            return locks
    except OperationalError:
        rebuild_db()
        return []


def load_recent_execution_log(limit: int = 10) -> list[dict]:
    paper_rows = load_recent_orders(limit=limit)
    live_rows = load_recent_live_orders(limit=limit)
    combined = [
        {
            **row,
            "source": "paper",
            "requested_mode": "paper",
            "applied_mode": "paper",
            "broker_live": False,
        }
        for row in paper_rows
    ] + live_rows
    combined.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return combined[:limit]


def refresh_live_order_statuses(fetch_order_details) -> dict:
    """Refresh submitted live orders from broker state."""
    init_db()
    checked = 0
    updated = 0
    failed = 0
    with SessionLocal() as db:
        rows = db.execute(
            select(LiveOrderRecord)
            .where(LiveOrderRecord.request_status.in_(["submitted", "partial"]))
            .order_by(LiveOrderRecord.id.desc())
            .limit(20)
        ).scalars().all()
        for row in rows:
            if not row.broker_order_id:
                continue
            checked += 1
            try:
                payload = fetch_order_details(
                    {
                        "broker_order_id": row.broker_order_id,
                        "desk": row.desk,
                        "symbol": row.symbol,
                        "action": row.action,
                        "broker_state": row.broker_state,
                        "payload": row.payload or {},
                    }
                )
                request_status = str(payload.get("request_status") or row.request_status)
                broker_state = str(payload.get("broker_state") or row.broker_state)
                row.request_status = request_status
                row.broker_state = broker_state
                merged_payload = dict(row.payload or {})
                merged_payload.update(payload)
                row.payload = merged_payload
                row.message = str(payload.get("message", "") or row.message or "")
                row.reason = str(payload.get("reason", "") or row.reason or "")
                updated += 1
            except Exception as exc:
                row.message = str(exc)
                failed += 1
        db.commit()
    return {"checked": checked, "updated": updated, "failed": failed}


def reconcile_live_order_effects(prices: dict[str, float]) -> dict:
    """Link finalized live order outcomes to positions/closed_positions once."""
    init_db()
    checked = 0
    updated = 0
    with SessionLocal() as db:
        rows = db.execute(
            select(LiveOrderRecord)
            .where(
                LiveOrderRecord.broker_live.is_(True),
                LiveOrderRecord.effect_status.in_(["pending", "awaiting_balance_sync"]),
            )
            .order_by(LiveOrderRecord.id.desc())
            .limit(30)
        ).scalars().all()
        for row in rows:
            checked += 1
            payload = dict(row.payload or {})
            executed_volume = _safe_float(payload.get("executed_volume"))
            remaining_volume = _safe_float(payload.get("remaining_volume"))
            if row.request_status == "cancelled":
                row.effect_status = "cancelled_partial_fill" if executed_volume > 0 else "cancelled_no_fill"
                updated += 1
                continue
            if row.request_status not in {"filled", "partial"}:
                continue
            if row.action in {"probe_longs", "attack_opening_drive", "selective_probe"}:
                open_position = db.execute(
                    select(PositionRecord).where(
                        PositionRecord.desk == row.desk,
                        PositionRecord.symbol == row.symbol,
                    )
                ).scalar_one_or_none()
                if open_position is None:
                    row.effect_status = "partial_balance_sync" if row.request_status == "partial" else "awaiting_balance_sync"
                    continue
                row.effect_status = "linked_partial_open" if row.request_status == "partial" else "linked_open"
                row.linked_position_symbol = open_position.symbol
                updated += 1
                continue
            if row.action in {"reduce_risk", "capital_preservation"}:
                if row.request_status == "partial" or (executed_volume > 0 and remaining_volume > 0):
                    row.effect_status = "partial_close_pending"
                    updated += 1
                    continue
                open_position = db.execute(
                    select(PositionRecord).where(
                        PositionRecord.desk == row.desk,
                        PositionRecord.symbol == row.symbol,
                    )
                ).scalar_one_or_none()
                if open_position is None:
                    row.effect_status = "already_reconciled"
                    row.linked_closed_symbol = row.symbol
                    updated += 1
                    continue
                exit_price = prices.get(open_position.symbol, open_position.current_price) or open_position.current_price
                realized_pnl_pct = (
                    round(((exit_price - open_position.entry_price) / open_position.entry_price) * 100, 4)
                    if open_position.entry_price > 0 else 0.0
                )
                db.add(
                    ClosedPositionRecord(
                        desk=open_position.desk,
                        symbol=open_position.symbol,
                        entry_price=open_position.entry_price,
                        exit_price=exit_price,
                        notional_pct=open_position.notional_pct,
                        realized_pnl_pct=realized_pnl_pct,
                        won=realized_pnl_pct > 0,
                        opened_at=open_position.opened_at,
                        closed_at=utcnow_iso(),
                        closed_reason="broker_order_fill",
                    )
                )
                row.effect_status = "linked_close"
                row.linked_closed_symbol = open_position.symbol
                db.delete(open_position)
                updated += 1
        db.commit()
    return {"checked": checked, "updated": updated}


def load_performance_quick_stats() -> dict:
    """All-time compounded performance stats. Never resets."""
    init_db()
    try:
        with SessionLocal() as db:
            closed = db.execute(
                select(ClosedPositionRecord).order_by(ClosedPositionRecord.id)
            ).scalars().all()
            open_pos = db.execute(select(PositionRecord)).scalars().all()

        total_trades = len(closed)
        winning_trades = sum(1 for row in closed if row.won)
        win_rate_pct = round(winning_trades / total_trades * 100, 1) if total_trades > 0 else 0.0

        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for row in closed:
            equity *= 1 + row.realized_pnl_pct / 100
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak * 100
            if dd < max_drawdown:
                max_drawdown = dd

        cumulative_realized_pnl_pct = round((equity - 1.0) * 100, 2)
        total_unrealized_pnl_pct = round(sum(p.unrealized_pnl_pct for p in open_pos), 2)

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "win_rate_pct": win_rate_pct,
            "cumulative_realized_pnl_pct": cumulative_realized_pnl_pct,
            "max_drawdown_pct": round(max_drawdown, 2),
            "open_positions": len(open_pos),
            "total_unrealized_pnl_pct": total_unrealized_pnl_pct,
        }
    except OperationalError:
        rebuild_db()
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "win_rate_pct": 0.0,
            "cumulative_realized_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "open_positions": 0,
            "total_unrealized_pnl_pct": 0.0,
        }


def load_symbol_score_adjustments(window: int = 60) -> dict[str, float]:
    """Per-symbol combined_score penalty based on recent closed crypto trades.

    Penalty accumulates for:
    - Negative avg PnL          → +0.04
    - Win rate < 30% (≥3 trades)→ +0.04
    - 2+ consecutive recent losses → +0.04
    - 3+ consecutive recent losses → +0.04 (stacks with above)
    Max penalty per symbol: 0.12  (caps combined_score reduction)
    """
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .where(PaperPositionRecord.status == "closed", PaperPositionRecord.desk == "crypto")
                .order_by(PaperPositionRecord.id.desc())
                .limit(window)
            ).scalars().all()
    except OperationalError:
        return {}

    symbol_groups: dict[str, list[float]] = {}
    for row in rows:
        sym = str(row.symbol or "").strip()
        if sym:
            symbol_groups.setdefault(sym, []).append(float(row.pnl_pct or 0.0))

    adjustments: dict[str, float] = {}
    for symbol, pnl_list in symbol_groups.items():
        if len(pnl_list) < 2:
            continue  # not enough data to judge
        avg_pnl = sum(pnl_list) / len(pnl_list)
        wins = sum(1 for p in pnl_list if p > 0)
        win_rate = wins / len(pnl_list)
        # pnl_list is newest-first (ORDER BY id DESC)
        consecutive_losses = 0
        for p in pnl_list:
            if p <= 0:
                consecutive_losses += 1
            else:
                break
        penalty = 0.0
        if avg_pnl < -0.5:
            penalty += 0.04
        if win_rate < 0.30 and len(pnl_list) >= 3:
            penalty += 0.04
        if consecutive_losses >= 2:
            penalty += 0.04
        if consecutive_losses >= 3:
            penalty += 0.04
        if penalty > 0:
            adjustments[symbol] = min(round(penalty, 3), 0.12)
    return adjustments


def load_current_loss_streak(desk: str = "crypto", lookback: int = 15) -> int:
    """Returns current consecutive loss streak for a desk (0 = last trade was a win or no trades)."""
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .where(PaperPositionRecord.status == "closed", PaperPositionRecord.desk == desk)
                .order_by(PaperPositionRecord.id.desc())
                .limit(lookback)
            ).scalars().all()
    except OperationalError:
        return 0
    streak = 0
    for row in rows:  # newest-first
        if float(row.pnl_pct or 0.0) <= 0:
            streak += 1
        else:
            break
    return streak


def load_hourly_win_rates(desk: str = "crypto", days: int = 30) -> dict[int, dict]:
    """Returns per-hour stats {hour: {win_rate, trades}} from the last `days` days.
    Only hours with trades >= 5 are returned (insufficient sample otherwise).
    """
    init_db()
    cutoff_date = (datetime.now(_local_timezone()) - timedelta(days=days)).date()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .where(PaperPositionRecord.status == "closed", PaperPositionRecord.desk == desk)
                .order_by(PaperPositionRecord.id.desc())
                .limit(500)
            ).scalars().all()
    except OperationalError:
        return {}

    hour_groups: dict[int, list[float]] = {}
    for row in rows:
        opened = _local_datetime_from_iso(row.opened_at)
        if not opened:
            continue
        row_date = opened.date()
        if row_date < cutoff_date:
            continue
        hour_groups.setdefault(opened.hour, []).append(float(row.pnl_pct or 0.0))

    result: dict[int, dict] = {}
    for hour, pnl_list in hour_groups.items():
        if len(pnl_list) < 5:
            continue  # too few trades — don't make assumptions
        wins = sum(1 for p in pnl_list if p > 0)
        result[hour] = {
            "trades": len(pnl_list),
            "win_rate": round(wins / len(pnl_list), 3),
            "avg_pnl": round(sum(pnl_list) / len(pnl_list), 3),
        }
    return result


def load_performance_analytics(limit: int = 500) -> dict:
    """Paper-position analytics for the operator performance page."""
    init_db()

    def _row_pnl_krw(row: PaperPositionRecord) -> int:
        capital = float(settings.paper_capital_krw)
        return round(capital * _size_to_notional(row.size) * float(row.pnl_pct or 0.0) / 100)

    def _holding_minutes(row: PaperPositionRecord) -> int:
        opened = _local_datetime_from_iso(row.opened_at)
        closed = _local_datetime_from_iso(row.closed_at)
        if not opened:
            return 0
        end = closed or datetime.now(_local_timezone())
        return max(0, round((end - opened).total_seconds() / 60))

    def _group_stats(rows: list[PaperPositionRecord]) -> dict:
        total = len(rows)
        wins = sum(1 for row in rows if float(row.pnl_pct or 0.0) > 0)
        pnl_values = [float(row.pnl_pct or 0.0) for row in rows]
        total_pnl = round(sum(pnl_values), 2)
        total_krw = sum(_row_pnl_krw(row) for row in rows)
        return {
            "trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
            "total_pnl_pct": total_pnl,
            "total_pnl_krw": total_krw,
            "avg_pnl_pct": round(total_pnl / total, 2) if total else 0.0,
            "avg_hold_min": round(sum(_holding_minutes(row) for row in rows) / total, 1) if total else 0.0,
            "best_pnl_pct": round(max(pnl_values), 2) if pnl_values else 0.0,
            "worst_pnl_pct": round(min(pnl_values), 2) if pnl_values else 0.0,
        }

    def _stats_by(rows: list[PaperPositionRecord], key_fn) -> list[dict]:
        grouped: dict[str, list[PaperPositionRecord]] = {}
        for row in rows:
            key = str(key_fn(row) or "unknown").strip() or "unknown"
            grouped.setdefault(key, []).append(row)
        return [
            {"label": key, **_group_stats(items)}
            for key, items in sorted(
                grouped.items(),
                key=lambda item: (len(item[1]), sum(float(row.pnl_pct or 0.0) for row in item[1])),
                reverse=True,
            )
        ]

    def _max_drawdown(rows: list[PaperPositionRecord]) -> float:
        equity = 100.0
        peak = 100.0
        max_dd = 0.0
        for row in rows:
            equity += float(row.pnl_pct or 0.0)
            peak = max(peak, equity)
            if peak > 0:
                max_dd = min(max_dd, (equity - peak) / peak * 100)
        return round(max_dd, 2)

    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .order_by(PaperPositionRecord.id.desc())
                .limit(limit)
            ).scalars().all()
    except OperationalError:
        rebuild_db()
        rows = []

    ordered = list(reversed(rows))
    closed = [row for row in ordered if row.status == "closed"]
    open_rows = [row for row in ordered if row.status == "open"]
    today = _today_local_date()
    closed_today = [row for row in closed if _local_date_from_iso(row.closed_at) == today]

    hourly_groups: dict[int, list[PaperPositionRecord]] = {hour: [] for hour in range(24)}
    for row in closed:
        opened = _local_datetime_from_iso(row.opened_at)
        if opened:
            hourly_groups[opened.hour].append(row)

    hourly_heatmap = [
        {
            "hour": hour,
            "label": f"{hour:02d}:00",
            **_group_stats(items),
        }
        for hour, items in hourly_groups.items()
    ]

    daily_performance = _stats_by(closed, lambda row: _local_date_from_iso(row.closed_at))[:30]

    buckets = [
        ("<= -2%", None, -2.0),
        ("-2% ~ -1%", -2.0, -1.0),
        ("-1% ~ 0%", -1.0, 0.0),
        ("0% ~ 1%", 0.0, 1.0),
        ("1% ~ 2%", 1.0, 2.0),
        (">= 2%", 2.0, None),
    ]
    pnl_distribution = []
    for label, lower, upper in buckets:
        bucket_rows = [
            row for row in closed
            if (lower is None or float(row.pnl_pct or 0.0) >= lower)
            and (upper is None or float(row.pnl_pct or 0.0) < upper)
        ]
        pnl_distribution.append({"label": label, **_group_stats(bucket_rows)})

    recent_closed = [
        {
            "id": row.id,
            "symbol": row.symbol,
            "action": row.action,
            "size": row.size,
            "opened_at": row.opened_at,
            "closed_at": row.closed_at,
            "holding_minutes": _holding_minutes(row),
            "entry_price": row.entry_price,
            "exit_price": row.exit_price,
            "pnl_pct": round(float(row.pnl_pct or 0.0), 2),
            "pnl_krw": _row_pnl_krw(row),
            "peak_pnl_pct": round(float(row.peak_pnl_pct or 0.0), 2),
            "closed_reason": row.closed_reason or "unknown",
            "focus": row.focus,
        }
        for row in reversed(closed[-50:])
    ]
    open_positions = [
        {
            "id": row.id,
            "symbol": row.symbol,
            "action": row.action,
            "size": row.size,
            "opened_at": row.opened_at,
            "holding_minutes": _holding_minutes(row),
            "entry_price": row.entry_price,
            "current_price": row.current_price,
            "pnl_pct": round(float(row.pnl_pct or 0.0), 2),
            "pnl_krw": _row_pnl_krw(row),
            "peak_pnl_pct": round(float(row.peak_pnl_pct or 0.0), 2),
            "focus": row.focus,
        }
        for row in reversed(open_rows[-20:])
    ]

    # Equity curve: date-sorted cumulative PnL (up to 60 trading days)
    daily_by_date = sorted(
        _stats_by(closed, lambda row: _local_date_from_iso(row.closed_at)),
        key=lambda x: str(x.get("label", "")),
    )
    equity_curve: list[dict] = []
    cumulative = 0.0
    for day in daily_by_date[-60:]:
        cumulative += float(day.get("total_pnl_pct", 0.0))
        equity_curve.append(
            {
                "date": str(day.get("label", "")),
                "cumulative_pnl_pct": round(cumulative, 2),
                "daily_pnl_pct": day.get("total_pnl_pct", 0.0),
                "trades": day.get("trades", 0),
            }
        )

    # Win/loss streak analysis from time-ordered closed rows
    tmp_streak = 0
    tmp_type = None  # "win" | "loss" | None
    longest_win = 0
    longest_loss = 0
    for row in closed:
        rt = "win" if float(row.pnl_pct or 0.0) > 0 else "loss"
        if rt == tmp_type:
            tmp_streak += 1
        else:
            tmp_type = rt
            tmp_streak = 1
        if rt == "win":
            longest_win = max(longest_win, tmp_streak)
        else:
            longest_loss = max(longest_loss, tmp_streak)
    streak_info = {
        "current_streak": tmp_streak,
        "current_type": tmp_type or "none",
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
    }

    return {
        "updated_at": utcnow_iso(),
        "timezone": settings.timezone,
        "summary": {
            **_group_stats(closed),
            "today": _group_stats(closed_today),
            "open_positions": len(open_rows),
            "max_drawdown_pct": _max_drawdown(closed),
            "sample_size": len(closed),
        },
        "hourly_heatmap": hourly_heatmap,
        "daily_performance": daily_performance,
        "entry_reason_stats": _stats_by(closed, lambda row: row.action),
        "exit_reason_stats": _stats_by(closed, lambda row: row.closed_reason),
        "symbol_stats": _stats_by(closed, lambda row: row.symbol)[:20],
        "pnl_distribution": pnl_distribution,
        "open_positions": open_positions,
        "recent_closed": recent_closed,
        "equity_curve": equity_curve,
        "streak_info": streak_info,
    }

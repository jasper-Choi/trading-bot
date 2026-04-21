from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz
from sqlalchemy import JSON, Boolean, Float, Integer, String, create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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


db_path = Path(settings.db_path)
db_path.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
ACTIONABLE_ENTRY_ACTIONS = {"probe_longs", "attack_opening_drive", "selective_probe"}


def _size_to_notional(size: str) -> float:
    try:
        return float(str(size).replace("x", ""))
    except ValueError:
        return 0.0


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


def _today_local_date() -> str:
    return datetime.now(_local_timezone()).date().isoformat()


def _extract_order_meta(action: str, rationale: list) -> dict:
    meta = rationale[0] if rationale and isinstance(rationale[0], dict) else {}
    normalized = {
        "notional_pct": float(meta.get("notional_pct", 0.0) or 0.0),
        "status": str(meta.get("status", "idle") or "idle"),
        "pnl_estimate_pct": float(meta.get("pnl_estimate_pct", 0.0) or 0.0),
    }
    if action not in ACTIONABLE_ENTRY_ACTIONS:
        normalized["status"] = "idle"
        normalized["pnl_estimate_pct"] = 0.0
    return normalized


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
    if desk == "crypto":
        if action == "probe_longs":
            return 0.9, -0.55, 5
        return 0.65, -0.4, 4
    if desk == "us":
        if action == "probe_longs":
            return 1.25, -0.7, 5
        return 0.8, -0.45, 4
    if action == "attack_opening_drive":
        return 1.3, -0.65, 3
    if action == "probe_longs":
        return 1.0, -0.55, 4
    return 0.65, -0.4, 3


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
    position.status = "closed"
    position.closed_at = utcnow_iso()
    position.exit_price = position.current_price
    position.closed_reason = reason


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


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
        with SessionLocal() as db:
            rec = db.get(StateRecord, "primary")
            if rec is None:
                return CompanyState()
            return CompanyState(
                stance=rec.stance,
                regime=rec.regime,
                risk_budget=rec.risk_budget,
                allow_new_entries=rec.allow_new_entries,
                execution_mode=rec.execution_mode,
                notes=rec.notes or [],
                trader_principles=rec.trader_principles or [],
                latest_signals=rec.latest_signals or [],
                market_snapshot=rec.market_snapshot or {},
                session_state=rec.session_state or {},
                desk_views=rec.desk_views or {},
                strategy_book=rec.strategy_book or {},
                daily_summary=load_daily_summary(),
                performance_stats=load_performance_quick_stats(),
                execution_log=load_recent_orders(limit=10),
                open_positions=[p.model_dump() for p in load_open_positions()],
                recent_journal=load_recent_journal(limit=8),
                agent_runs=[AgentSnapshot.model_validate(item) for item in (rec.agent_runs or [])],
                updated_at=rec.updated_at or utcnow_iso(),
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


def sync_paper_positions(paper_orders: list[PaperOrder], market_snapshot: dict) -> None:
    init_db()
    price_lookup = _build_price_lookup(market_snapshot)
    with SessionLocal() as db:
        open_positions = db.execute(
            select(PaperPositionRecord).where(PaperPositionRecord.status == "open").order_by(PaperPositionRecord.id.asc())
        ).scalars().all()

        for position in open_positions:
            current_price = price_lookup.get((position.desk, position.symbol), position.current_price)
            if current_price and position.entry_price > 0:
                position.current_price = current_price
                position.pnl_pct = round(((current_price - position.entry_price) / position.entry_price) * 100, 2)
            position.cycles_open += 1
            target_pct, stop_pct, max_cycles = _position_thresholds(position.desk, position.action)
            early_failure_pct = round(stop_pct * 0.6, 2)
            stale_floor_pct = round(max(target_pct * 0.25, 0.2), 2)
            fast_fail_cycle = 1 if position.action in {"attack_opening_drive", "selective_probe"} else 2
            quick_win_floor = round(max(target_pct * 0.45, 0.35), 2)
            if position.pnl_pct >= target_pct:
                _close_position(position, "target_hit")
            elif position.pnl_pct <= stop_pct:
                _close_position(position, "stop_hit")
            elif position.cycles_open >= fast_fail_cycle and position.pnl_pct <= early_failure_pct:
                _close_position(position, "early_failure")
            elif position.cycles_open >= 2 and position.pnl_pct >= quick_win_floor:
                _close_position(position, "momentum_take")
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
            db.add(
                PaperPositionRecord(
                    desk=order.desk,
                    symbol=symbol,
                    status="open",
                    action=order.action,
                    size=order.size,
                    opened_at=order.created_at,
                    entry_price=reference_price,
                    current_price=reference_price,
                    pnl_pct=0.0,
                    cycles_open=0,
                    focus=order.focus,
                )
            )
        db.commit()


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
    try:
        with SessionLocal() as db:
            orders = db.execute(select(PaperOrderRecord)).scalars().all()
            journal = db.execute(select(CycleJournalRecord)).scalars().all()
            positions = db.execute(select(PaperPositionRecord)).scalars().all()
            orders = [row for row in orders if _local_date_from_iso(row.created_at) == today]
            journal = [row for row in journal if _local_date_from_iso(row.run_at) == today]
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
            wins = sum(1 for row in closed_today if row.pnl_pct > 0)
            losses = sum(1 for row in closed_today if row.pnl_pct <= 0)
            closed_count = len(closed_today)
            win_rate = round((wins / closed_count) * 100, 1) if closed_count else 0.0
            realized_pnl = round(sum(row.pnl_pct for row in closed_today), 2)
            unrealized_pnl = round(sum(row.pnl_pct for row in open_positions), 2)
            expectancy_pct = round(realized_pnl / closed_count, 2) if closed_count else 0.0
            desk_stats = _build_desk_stats(positions)
            gross_open_notional = round(sum(_size_to_notional(row.size) for row in open_positions), 2)
            base_capital = float(settings.paper_capital_krw)
            realized_pnl_krw = round(base_capital * realized_pnl / 100)
            unrealized_pnl_krw = round(base_capital * unrealized_pnl / 100)
            expectancy_krw = round(base_capital * expectancy_pct / 100)
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
        db.add(PositionRecord(
            desk=desk,
            symbol=symbol,
            entry_price=entry_price,
            current_price=entry_price,
            notional_pct=notional_pct,
            action=action,
            unrealized_pnl_pct=0.0,
            opened_at=utcnow_iso(),
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
            exit_price = prices.get(pos.symbol, pos.current_price) or pos.current_price
            realized_pnl_pct = (
                round(((exit_price - pos.entry_price) / pos.entry_price) * 100, 4)
                if pos.entry_price > 0 else 0.0
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
            ))
            db.delete(pos)
        db.commit()
    return closed


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
                pos.unrealized_pnl_pct = round(
                    ((current_price - pos.entry_price) / pos.entry_price) * 100, 4
                )
        db.commit()


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


def load_closed_positions(limit: int = 50) -> list[ClosedPosition]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(ClosedPositionRecord)
                .order_by(ClosedPositionRecord.id.desc())
                .limit(limit)
            ).scalars().all()
            return [
                ClosedPosition(
                    id=row.id,
                    desk=row.desk,
                    symbol=row.symbol,
                    entry_price=row.entry_price,
                    exit_price=row.exit_price,
                    notional_pct=row.notional_pct,
                    realized_pnl_pct=row.realized_pnl_pct,
                    won=row.won,
                    opened_at=row.opened_at,
                    closed_at=row.closed_at,
                )
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


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

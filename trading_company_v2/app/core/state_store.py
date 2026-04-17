from __future__ import annotations

from pathlib import Path

from sqlalchemy import JSON, Boolean, Float, Integer, String, create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings
from app.core.models import AgentSnapshot, CompanyState, CycleJournalEntry, PaperOrder, utcnow_iso


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


db_path = Path(settings.db_path)
db_path.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


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
                execution_log=load_recent_orders(limit=10),
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


def load_recent_orders(limit: int = 10) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(select(PaperOrderRecord).order_by(PaperOrderRecord.id.desc()).limit(limit)).scalars().all()
            return [
                {
                    "created_at": row.created_at,
                    "desk": row.desk,
                    "action": row.action,
                    "focus": row.focus,
                    "size": row.size,
                    "notional_pct": row.rationale[0].get("notional_pct", 0.0) if row.rationale and isinstance(row.rationale[0], dict) else 0.0,
                    "status": row.rationale[0].get("status", "planned") if row.rationale and isinstance(row.rationale[0], dict) else "planned",
                    "pnl_estimate_pct": row.rationale[0].get("pnl_estimate_pct", 0.0) if row.rationale and isinstance(row.rationale[0], dict) else 0.0,
                    "rationale": row.rationale or [],
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_daily_summary() -> dict:
    init_db()
    today = utcnow_iso()[:10]
    try:
        with SessionLocal() as db:
            orders = db.execute(select(PaperOrderRecord).where(PaperOrderRecord.created_at.startswith(today))).scalars().all()
            journal = db.execute(select(CycleJournalRecord).where(CycleJournalRecord.run_at.startswith(today))).scalars().all()
            order_dicts = [
                {
                    "desk": row.desk,
                    "action": row.action,
                    "size": row.size,
                    "rationale": row.rationale or [],
                }
                for row in orders
            ]
            planned_orders = sum(1 for row in order_dicts if row["action"] not in {"stand_by", "pre_market_watch"})
            active_desks = sorted({row["desk"] for row in order_dicts})
            estimated_pnl = 0.0
            for row in order_dicts:
                meta = row["rationale"][0] if row["rationale"] and isinstance(row["rationale"][0], dict) else {}
                estimated_pnl += float(meta.get("pnl_estimate_pct", 0.0) or 0.0)
            return {
                "date": today,
                "cycles_run": len(journal),
                "orders_logged": len(order_dicts),
                "planned_orders": planned_orders,
                "active_desks": active_desks,
                "estimated_pnl_pct": round(estimated_pnl, 2),
            }
    except OperationalError:
        rebuild_db()
        return {
            "date": today,
            "cycles_run": 0,
            "orders_logged": 0,
            "planned_orders": 0,
            "active_desks": [],
            "estimated_pnl_pct": 0.0,
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
                    "orders": row.orders or [],
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []

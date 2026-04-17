from __future__ import annotations

from pathlib import Path

from sqlalchemy import JSON, Boolean, Float, String, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings
from app.core.models import AgentSnapshot, CompanyState, utcnow_iso


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
    agent_runs: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[str] = mapped_column(String(40), default="")


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
        rec.agent_runs = [item.model_dump() for item in state.agent_runs]
        rec.updated_at = utcnow_iso()
        state.updated_at = rec.updated_at
        db.commit()
    return state

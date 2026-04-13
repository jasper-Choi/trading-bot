"""
데이터베이스 레이어 — SQLite(로컬) / PostgreSQL(Railway) 자동 감지.

환경변수:
  DATABASE_URL: PostgreSQL 연결 문자열 (미설정 시 SQLite 사용)

테이블:
  trades       - 청산 완료된 거래 이력 (코인 + 주식)
  positions    - 현재/과거 포지션 상태 (coin key 기준 upsert)
  logs         - 실행 로그 (최신 2000줄 유지)
  daily_stats  - 일별 통계 스냅샷
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    Column, Integer, Float, Boolean, String, Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


# ── 엔진 설정 ──────────────────────────────────────────────────────────────────

_RAW_URL = os.environ.get("DATABASE_URL", "")

# Railway / Heroku: postgres:// → postgresql://
if _RAW_URL.startswith("postgres://"):
    _RAW_URL = _RAW_URL.replace("postgres://", "postgresql://", 1)

if _RAW_URL:
    # PostgreSQL (Railway)
    DATABASE_URL = _RAW_URL
    _engine_kw   = {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}
    IS_POSTGRES  = True
else:
    # SQLite (로컬)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    DATABASE_URL = f"sqlite:///{config.DATA_DIR}/trading.db"
    _engine_kw   = {"connect_args": {"check_same_thread": False}}
    IS_POSTGRES  = False

engine       = create_engine(DATABASE_URL, **_engine_kw)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base         = declarative_base()


# ── ORM 모델 ────────────────────────────────────────────────────────────────────

class TradeRecord(Base):
    """청산 완료된 거래 이력 (코인 + 주식 통합)."""
    __tablename__ = "trades"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    coin          = Column(String(30), nullable=False, index=True)  # KRW-BTC or ticker
    market        = Column(String(10), default="coin")              # coin | stock
    name          = Column(String(60))                              # 종목명 (주식)
    entry_price   = Column(Float, nullable=False)
    entry_date    = Column(String(25), nullable=False)
    exit_price    = Column(Float, nullable=False)
    exit_date     = Column(String(25), nullable=False, index=True)
    exit_reason   = Column(String(60))
    pnl           = Column(Float, default=0.0)
    pnl_pct       = Column(Float, default=0.0)
    quantity      = Column(Float, default=0.0)
    capital       = Column(Float, default=0.0)
    # 코인 전용
    atr_at_entry  = Column(Float)
    candles_held  = Column(Integer)
    pyramid_count = Column(Integer, default=0)


class PositionRecord(Base):
    """포지션 상태 (open / closed, coin key 기준 upsert)."""
    __tablename__    = "positions"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    coin             = Column(String(30), nullable=False, unique=True, index=True)
    market           = Column(String(10), default="coin")
    name             = Column(String(60))
    status           = Column(String(10), default="open")
    entry_price      = Column(Float)
    entry_date       = Column(String(25))
    stop_loss        = Column(Float)
    atr_at_entry     = Column(Float)
    peak_price       = Column(Float)
    capital          = Column(Float)
    quantity         = Column(Float)
    pyramid_count    = Column(Integer, default=0)
    max_hold_candles = Column(Integer)
    reason           = Column(String(100))      # 진입 사유 (주식용)
    # 청산 시 채워짐
    exit_price       = Column(Float)
    exit_date        = Column(String(25))
    exit_reason      = Column(String(60))
    pnl              = Column(Float)
    pnl_pct          = Column(Float)
    candles_held     = Column(Integer)
    # 주식 전용
    half_sold        = Column(Boolean, default=False)
    tp1              = Column(Float)
    tp2              = Column(Float)
    updated_at       = Column(String(25))


class LogRecord(Base):
    """실행 로그 (최신 2000줄 유지)."""
    __tablename__ = "logs"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(String(25))
    message   = Column(Text)


class DailyStat(Base):
    """일별 통계 스냅샷."""
    __tablename__ = "daily_stats"

    date       = Column(String(10), primary_key=True)   # "2024-01-01"
    total_pnl  = Column(Float, default=0.0)
    win_count  = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    sharpe     = Column(Float)
    updated_at = Column(String(25))


# ── 초기화 (멱등) ──────────────────────────────────────────────────────────────

_initialized = False

def init_db() -> None:
    """테이블 생성 + 기존 파일 마이그레이션 (멱등 — 여러 번 호출 안전)."""
    global _initialized
    if _initialized:
        return
    Base.metadata.create_all(bind=engine)
    _migrate_from_files()
    _initialized = True


# ── 세션 컨텍스트 ───────────────────────────────────────────────────────────────

@contextmanager
def _db():
    """자동 commit / rollback / close 세션 컨텍스트."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _now() -> str:
    return datetime.now(config.KST).strftime("%Y-%m-%d %H:%M:%S")


# ── Positions CRUD ─────────────────────────────────────────────────────────────

def db_upsert_position(pos: dict) -> None:
    """포지션 dict를 DB에 저장 / 갱신 (coin 기준 upsert)."""
    key = pos.get("coin") or pos.get("ticker", "")
    if not key:
        return
    try:
        with _db() as s:
            rec = s.query(PositionRecord).filter_by(coin=key).first()
            if rec is None:
                rec = PositionRecord(coin=key)
                s.add(rec)
            rec.market           = pos.get("market", "coin")
            rec.name             = pos.get("name")
            rec.status           = pos.get("status", "open")
            rec.entry_price      = pos.get("entry_price")
            rec.entry_date       = pos.get("entry_date")
            rec.stop_loss        = pos.get("stop_loss")
            rec.atr_at_entry     = pos.get("atr_at_entry")
            rec.peak_price       = pos.get("peak_price")
            rec.capital          = pos.get("capital")
            rec.quantity         = pos.get("quantity")
            rec.pyramid_count    = pos.get("pyramid_count", 0)
            rec.max_hold_candles = pos.get("max_hold_candles")
            rec.reason           = pos.get("reason")
            rec.exit_price       = pos.get("exit_price")
            rec.exit_date        = pos.get("exit_date")
            rec.exit_reason      = pos.get("exit_reason")
            rec.pnl              = pos.get("pnl")
            rec.pnl_pct          = pos.get("pnl_pct")
            rec.candles_held     = pos.get("candles_held")
            rec.half_sold        = pos.get("half_sold", False)
            rec.tp1              = pos.get("tp1")
            rec.tp2              = pos.get("tp2")
            rec.updated_at       = _now()
    except Exception as exc:
        print(f"[DB] upsert_position 오류: {exc}")


def db_load_positions(market: str | None = None) -> dict:
    """DB에서 포지션 {coin: dict} 로드."""
    try:
        with _db() as s:
            q = s.query(PositionRecord)
            if market:
                q = q.filter_by(market=market)
            return {r.coin: _pos_to_dict(r) for r in q.all()}
    except Exception as exc:
        print(f"[DB] load_positions 오류: {exc}")
        return {}


def _pos_to_dict(r: PositionRecord) -> dict:
    return {
        col.name: getattr(r, col.name)
        for col in PositionRecord.__table__.columns
        if col.name not in ("id", "updated_at")
    }


# ── Trades CRUD ────────────────────────────────────────────────────────────────

def db_insert_trade(trade: dict) -> None:
    """거래 이력 추가."""
    try:
        with _db() as s:
            s.add(TradeRecord(
                coin          = trade.get("coin") or trade.get("ticker", ""),
                market        = trade.get("market", "coin"),
                name          = trade.get("name"),
                entry_price   = trade.get("entry_price", 0),
                entry_date    = trade.get("entry_date", ""),
                exit_price    = trade.get("exit_price", 0),
                exit_date     = trade.get("exit_date", ""),
                exit_reason   = trade.get("exit_reason", ""),
                pnl           = trade.get("pnl", 0),
                pnl_pct       = trade.get("pnl_pct", 0),
                quantity      = trade.get("quantity", 0),
                capital       = trade.get("capital", 0),
                atr_at_entry  = trade.get("atr_at_entry"),
                candles_held  = trade.get("candles_held"),
                pyramid_count = trade.get("pyramid_count", 0),
            ))
    except Exception as exc:
        print(f"[DB] insert_trade 오류: {exc}")


def db_load_trades(market: str | None = None, limit: int = 1000) -> list[dict]:
    """DB에서 거래 이력 로드 (최신순)."""
    try:
        with _db() as s:
            q = s.query(TradeRecord).order_by(TradeRecord.exit_date.desc())
            if market:
                q = q.filter_by(market=market)
            return [_trade_to_dict(r) for r in q.limit(limit).all()]
    except Exception as exc:
        print(f"[DB] load_trades 오류: {exc}")
        return []


def _trade_to_dict(r: TradeRecord) -> dict:
    return {
        col.name: getattr(r, col.name)
        for col in TradeRecord.__table__.columns
        if col.name != "id"
    }


# ── Logs CRUD ──────────────────────────────────────────────────────────────────

_LOG_KEEP          = 2000   # DB 유지 최대 줄 수
_log_cleanup_count = 0
_LOG_CLEANUP_EVERY = 50     # 50회 쓰기마다 정리


def db_insert_log(message: str) -> None:
    """로그를 DB에 저장. 50번마다 오래된 로그 정리."""
    global _log_cleanup_count
    try:
        ts = _now()
        with _db() as s:
            s.add(LogRecord(timestamp=ts, message=message[:2000]))
            _log_cleanup_count += 1
            if _log_cleanup_count % _LOG_CLEANUP_EVERY == 0:
                cutoff = (
                    s.query(LogRecord.id)
                     .order_by(LogRecord.id.desc())
                     .offset(_LOG_KEEP)
                     .first()
                )
                if cutoff:
                    s.query(LogRecord).filter(
                        LogRecord.id <= cutoff[0]
                    ).delete(synchronize_session=False)
    except Exception:
        pass  # 로그 저장 실패는 무시


def db_load_logs(n: int = 200) -> list[str]:
    """DB에서 최근 N줄 로그 (오래된 것 먼저)."""
    try:
        with _db() as s:
            recs = (
                s.query(LogRecord)
                 .order_by(LogRecord.id.desc())
                 .limit(n)
                 .all()
            )
            return [
                f"{r.timestamp}  {r.message}" if r.timestamp else r.message
                for r in reversed(recs)
            ]
    except Exception:
        return []


# ── Daily Stats CRUD ───────────────────────────────────────────────────────────

def db_upsert_daily_stats(
    date: str,
    total_pnl: float,
    win_count: int,
    loss_count: int,
    sharpe: float | None = None,
) -> None:
    try:
        with _db() as s:
            rec = s.query(DailyStat).filter_by(date=date).first()
            if rec is None:
                rec = DailyStat(date=date)
                s.add(rec)
            rec.total_pnl  = total_pnl
            rec.win_count  = win_count
            rec.loss_count = loss_count
            rec.sharpe     = sharpe
            rec.updated_at = _now()
    except Exception as exc:
        print(f"[DB] upsert_daily_stats 오류: {exc}")


def db_load_daily_stats() -> list[dict]:
    try:
        with _db() as s:
            return [
                {
                    "date": r.date, "total_pnl": r.total_pnl,
                    "win_count": r.win_count, "loss_count": r.loss_count,
                    "sharpe": r.sharpe,
                }
                for r in s.query(DailyStat).order_by(DailyStat.date.desc()).all()
            ]
    except Exception:
        return []


# ── 기존 파일 → DB 마이그레이션 ────────────────────────────────────────────────

def _migrate_from_files() -> None:
    """기존 파일 데이터를 DB로 1회 마이그레이션.

    이미 trades / positions 테이블에 데이터가 있으면 건너뜁니다.
    """
    try:
        with _db() as s:
            has_trades    = s.query(TradeRecord).first()    is not None
            has_positions = s.query(PositionRecord).first() is not None
        if has_trades and has_positions:
            return
    except Exception:
        return

    # ── trade_history.jsonl → trades ──────────────────────────────────────────
    history_path = os.path.join(config.LOG_DIR, "trade_history.jsonl")
    if not has_trades and os.path.exists(history_path):
        count = 0
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        db_insert_trade(json.loads(line))
                        count += 1
                    except Exception:
                        pass
            print(f"[DB 마이그레이션] trade_history.jsonl → trades: {count}건")
        except OSError as exc:
            print(f"[DB 마이그레이션] trade_history.jsonl 읽기 실패: {exc}")

    # ── positions.json → positions ────────────────────────────────────────────
    pos_path = os.path.join(config.DATA_DIR, "positions.json")
    if not has_positions and os.path.exists(pos_path):
        count = 0
        try:
            with open(pos_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for coin, pos in data.items():
                    p = dict(pos)
                    p["coin"]   = coin
                    p["market"] = "coin"
                    db_upsert_position(p)
                    count += 1
            print(f"[DB 마이그레이션] positions.json → positions: {count}개")
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[DB 마이그레이션] positions.json 읽기 실패: {exc}")

    # ── trading.log → logs (최근 500줄) ───────────────────────────────────────
    log_path = os.path.join(config.LOG_DIR, "trading.log")
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = [ln.rstrip("\n") for ln in f if ln.strip()]
            recent = lines[-500:]
            try:
                with _db() as s:
                    for line in recent:
                        s.add(LogRecord(timestamp="", message=line[:2000]))
                print(f"[DB 마이그레이션] trading.log → logs: {len(recent)}줄")
            except Exception:
                pass
        except OSError as exc:
            print(f"[DB 마이그레이션] trading.log 읽기 실패: {exc}")

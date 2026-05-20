from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import hashlib
import logging
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz
import requests
from sqlalchemy import JSON, Boolean, Float, Integer, String, create_engine, event, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, object_session, sessionmaker

from app.config import settings
from app.core.models import AgentSnapshot, ClosedPosition, CompanyState, CycleJournalEntry, PaperOrder, Position, utcnow_iso
from app.services.upbit_stream_cache import summarize_stream_momentum


_log = logging.getLogger(__name__)


def _safe_float(value: object) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


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
    strategy_id: Mapped[str] = mapped_column(String(80), default="")
    entry_profile: Mapped[str] = mapped_column(String(80), default="")
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
    strategy_id: Mapped[str] = mapped_column(String(80), default="")
    entry_profile: Mapped[str] = mapped_column(String(80), default="")
    is_pyramided: Mapped[bool] = mapped_column(Boolean, default=False)
    strategy_type: Mapped[str] = mapped_column(String(50), default="")


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


class ShadowSignalRecord(Base):
    __tablename__ = "shadow_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    desk: Mapped[str] = mapped_column(String(50), default="")
    symbol: Mapped[str] = mapped_column(String(100), default="")
    strategy_id: Mapped[str] = mapped_column(String(80), default="")
    entry_profile: Mapped[str] = mapped_column(String(80), default="")
    source: Mapped[str] = mapped_column(String(50), default="")
    action: Mapped[str] = mapped_column(String(50), default="")
    focus: Mapped[str] = mapped_column(String(240), default="")
    reason: Mapped[str] = mapped_column(String(120), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    stream_score: Mapped[float] = mapped_column(Float, default=0.0)
    notional_pct: Mapped[float] = mapped_column(Float, default=0.0)
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
        "strategy_id": str(meta.get("strategy_id", "") or ""),
        "entry_profile": str(meta.get("entry_profile", meta.get("entry_path", "")) or ""),
        "status": str(meta.get("status", "idle") or "idle"),
        "pnl_estimate_pct": float(meta.get("pnl_estimate_pct", 0.0) or 0.0),
    }
    if action not in ACTIONABLE_ENTRY_ACTIONS:
        normalized["status"] = "idle"
        normalized["pnl_estimate_pct"] = 0.0
    return normalized


def infer_strategy_id(action: str = "", focus: str = "", meta: dict | None = None) -> str:
    """Stable strategy label used for performance attribution and kill switches."""
    meta = meta or {}
    explicit = str(meta.get("strategy_id", "") or "").strip()
    if explicit:
        return explicit
    entry_profile = str(meta.get("entry_profile", meta.get("entry_path", "")) or "").lower()
    text = f"{entry_profile} {action} {focus}".lower()
    if "range_scalp" in text:
        return "crypto.range_scalp"
    if "smart_money_flow" in text:
        return "crypto.smart_money_flow"
    if "ranging_strength_follow" in text:
        return "crypto.ranging_strength_follow"
    if "range_breakout" in text:
        return "crypto.range_breakout"
    if "high_tight_flag" in text:
        return "crypto.high_tight_flag"
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
    if "composite signal" in text or "combined_score_ok" in text:
        return "crypto.composite_entry"
    if "stream ignition" in text:
        return "crypto.stream_entry"
    if "candidate-specific" in text or "multi-coin entry" in text:
        return "crypto.candidate_rotation"
    if "balanced" in text or "단타" in text:
        return "crypto.balanced_swing"
    if "공격적" in text or "offense" in text:
        return "crypto.offense_probe"
    return f"crypto.{entry_profile or action or 'unknown'}" if "crypto" not in text else (entry_profile or action or "crypto.unknown")


def _entry_profile(action: str = "", focus: str = "", meta: dict | None = None) -> str:
    meta = meta or {}
    explicit = str(meta.get("entry_profile", meta.get("entry_path", "")) or "").strip()
    if explicit:
        return explicit
    strategy_id = infer_strategy_id(action, focus, meta)
    return strategy_id.split(".", 1)[-1] if "." in strategy_id else strategy_id


def _paper_trade_payload(position: PaperPositionRecord, meta: dict | None = None) -> dict:
    meta = meta or {}
    notional_pct = float(meta.get("notional_pct", 0.0) or _size_to_notional(position.size))
    strategy_id = str(getattr(position, "strategy_id", "") or infer_strategy_id(position.action, position.focus, meta))
    entry_profile = str(getattr(position, "entry_profile", "") or _entry_profile(position.action, position.focus, meta))
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
        "strategy_id": strategy_id,
        "entry_profile": entry_profile,
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


def _position_thresholds(desk: str, action: str, focus: str = "") -> tuple[float, float, int]:
    # Returns (target_pct, stop_pct, max_cycles)
    # Backtest-validated (2025-04):
    #   coin_backtest_v5  → +4% TP / -2.0% stop / ≤48h  (60-min vol-breakout)
    #   stock_backtest_v3 → +4% TP / -2.5% stop / ≤5 days (daily momentum breakout)
    # @ ~8s/cycle: 450=1h, 225=30min, 75=10min, 25=3min
    if desk == "crypto" and "vol_breakout" in focus:
        # coin_backtest_v5 검증 전략: 60분봉 거래량급등+신고점돌파+RSI+EMA
        # Backtest: 승률~48%, 손익비~2.0, Sharpe~1.2, MDD<-20%
        # target +4.0%: 1차 익절 (실전 partial exit는 미지원 → 단일 목표 사용)
        # stop -2.0%: 백테스트 최적값
        # max_cycles 1620: 약 3.6시간 (60분봉 특성상 수시간 보유 허용)
        return 4.0, -2.0, 1620
    if desk == "crypto" and "range_scalp" in focus:
        # 평균회귀(에어본) 전략: 작은 목표/타이트한 손절/빠른 만기
        # stop -0.50%→-0.30%→-0.22%: 소형코인 갭점프 -0.74% 패턴 + 유동성 필터 추가로 더 타이트
        return 1.20, -0.22, 75
    if desk == "crypto" and "ranging_b36" in focus:
        # RANGING Batch 3-6 전략: 평균회귀/구조개선/압축돌파
        # target=1.80% (RANGING에서 10% 목표는 불가능), stop=-0.40% (rapid_tick_failed_start가 더 빠름)
        # range_scalp_trail_rules 적용 → trail/floor도 빠르게 관리
        return 1.80, -0.40, 90
    if desk == "crypto" and "range_breakout" in focus:
        return 2.20, -1.00, 90
    if desk == "crypto" and "high_tight_flag" in focus:
        return 1.80, -0.90, 90
    if desk == "crypto" and "ranging_momentum_leader" in focus:
        # Broad tape is flat, but the symbol is an individual momentum leader.
        # Use a reachable target and a tighter stop than normal trend mode.
        return 2.00, -0.70, 90
    if desk == "crypto" and "ranging_strength_follow" in focus:
        # Broad tape is flat, but a single symbol has flipped long with controlled strength.
        # Keep target/stop tighter than trend mode until live expectancy is proven.
        return 1.60, -0.55, 75
    if desk == "crypto" and "smart_money_flow" in focus:
        # Capital-flow + trendline/box breakout: let winners breathe more than
        # range scalps, but cap downside until live expectancy is proven.
        return 2.40, -0.80, 120
    if desk == "crypto" and "emma_scalp" in focus:
        # Emma-style scalp: Keltner + Supertrend + MACD confluence.
        # Keep it quick: small target, tight stop, short max hold.
        return 1.40, -0.45, 60
    if desk == "crypto" and "neo_micro_scalp" in focus:
        # Small-size micro-compound path. It should prove itself quickly.
        return 0.90, -0.35, 45
    if desk == "crypto" and "eth_4h_breakout" in focus:
        # ETH 4H 신고점 돌파 전략 백테스트 검증값 (2026-05-19)
        # 코인 전략 v4: Sharpe 2.33, WR 61.1%, MDD -7.08%, n=18
        # TP +7.0%, SL -3.0%, trail_trigger +4% / max 20봉 (80h)
        # @ ~8s/cycle: 80h × 3600s / 8s = 36000 cycles
        return 7.0, -3.0, 36000
    if desk == "crypto":
        # Trend mode: cut failed ignitions fast, let winners run with trailing.
        return 10.0, -2.0, 180
    if desk == "us":
        if action == "probe_longs":
            return 6.0, -3.0, 200
        if action == "selective_probe":
            return 4.0, -2.0, 150
        return 3.0, -1.5, 120
    # Korea stock:
    # stop -1.5%: 손실 크기 축소 (-2.5% → -1.5%), early_failure도 자동으로 타이트해짐
    # trail +1.5%부터 발동 — 작은 수익도 보호
    # max 2700 cycles ≈ 2.3 trading days (20s/cycle)
    if desk == "korea" and "open_reversal" in focus:
        # 오픈 리버설: 갭다운 소진 → 반전 — 빠른 진입/빠른 이탈
        # target 3.0%, stop -0.8%, max 2h
        return 3.0, -0.8, 360
    if desk == "korea" and ("opening_drive" in focus or action == "attack_opening_drive"):
        # Opening drive is an intraday momentum trade, not a multi-day swing.
        # Keep the stop tight and force quick proof of follow-through.
        return 3.5, -1.0, 360
    if desk == "korea" and "close_drive" in focus:
        # 종가 추격: 오버나이트 홀딩 허용 — 더 넓은 stop, 더 긴 보유
        # target 3.0%, stop -1.5%, max ~30h (5400 cycles @ 20s)
        return 3.0, -1.5, 5400
    if desk == "korea" and "gap_fill" in focus:
        # 갭 메꾸기: 당일 갭다운 → 갭 메꾸기 — 빠른 만기
        # target 2.0%, stop -0.8%, max 1h (180 cycles @ 20s)
        return 2.0, -0.8, 180
    if desk == "korea" and "pullback_ma" in focus:
        # 눌림목 매수: 상승 추세 내 MA20 눌림 — 표준 Korea와 동일 파라미터
        return 25.0, -1.5, 2700
    if desk == "korea" and ("quality_follow_probe" in focus or "mid_session_quality_probe" in focus):
        # General intraday quality probes need room for a first shakeout, but
        # must not become loose swing positions. SFA 036540 showed the previous
        # -0.8% stop can cut a valid +2% continuation after only a few minutes.
        return 2.8, -1.3, 540
    if desk == "korea" and action == "selective_probe":
        # 탐색 진입은 아직 확정 추세가 아니므로 손실을 작게 제한한다.
        # 기존 기본값(+25%/-1.5%)은 exploratory trade에 과도하게 넓었다.
        return 3.0, -0.8, 360
    if desk == "crypto" and "rsi2_mean_reversion" in focus:
        # S9 RSI(2) Connors (fee-adjusted 2026-05-20):
        # Crypto: Sh 2.76, WR 48.1%, PnL 1.58, MDD -6.3% | Stocks: Sh 5.52, WR 58.1%, PnL 1.62, MDD -8.0%
        # stop -1.4% (tightened from -1.5% to maintain P&L≥1.5 after 0.10% round-trip fee)
        return 10.0, -1.4, 2700
    if desk == "crypto" and "nday_pullback" in focus:
        # S10 N-Day Pullback (fee-adjusted 2026-05-20):
        # Crypto: Sh 5.21, WR 55.8%, PnL 1.91, MDD -6.7% | Stocks: Sh 3.52, WR 51.0%, PnL 1.64, MDD -13.6%
        # stop -1.2% (tightened from -1.5% to maintain P&L≥1.5 after 0.10% round-trip fee)
        return 10.0, -1.2, 2700
    if desk == "korea" and "rsi2_mean_reversion" in focus:
        # S9 Korea (fee-adjusted 2026-05-20): Sh 5.52, WR 58.1%, PnL 1.62, MDD -8.0%
        # stop -1.4% (tightened from -1.5% to maintain P&L≥1.5 after 0.25% round-trip fee)
        return 10.0, -1.4, 2700
    if desk == "korea" and "nday_pullback" in focus:
        # S10 Korea (fee-adjusted 2026-05-20): Sh 3.52, WR 51.0%, PnL 1.64, MDD -13.6%
        # stop -1.2% (tightened from -1.5% to maintain P&L≥1.5 after 0.25% round-trip fee)
        return 10.0, -1.2, 2700
    if desk == "korea" and "mongtata_airborne" in focus:
        # S2 MONGTATA ⚠️ 재검증 필요 (2026-05-20):
        # 원본 백테스트는 4H 크립토 + EMA200 없음 + 수수료 없음 → 결과 무효
        # 올바른 조건(daily+EMA200+수수료)으로 재백테스트: WR 50.9% 통과, MDD -26.1% FAIL
        # 임시: size 축소 + 손절 -2% (production에서 보수적 운용)
        return 10.0, -2.0, 2700
    if desk == "korea" and "new_high_breakout" in focus:
        # 2026-05-19: 백테스트 검증 전략 B (신고점 돌파)
        # 3년 152종목: 승률 84.6%, 연 +89.5%, Sharpe 6.16, MDD -4.0%
        # target 10% (trail이 실제 청산 담당, target은 상한선)
        # stop -4.0%: 백테스트 최적값 (stop=-2.5%도 유효하나 보수적으로 -4% 유지)
        # max 2700 cycles ≈ 20 거래일 (20s/cycle)
        return 10.0, -4.0, 2700
    if desk == "korea" and action == "probe_longs":
        # 2026-05-19: stop -1.5% → -1.0% (avgLoss 축소 → 손익비 개선)
        return 25.0, -1.0, 2700
    if desk == "crypto" and "dip_bounce" in focus:
        # BTC 급락 반등: 빠른 진입/빠른 이탈 — target 1.2%, stop -0.7%, max 20min
        return 1.2, -0.7, 60
    if action in {"attack_opening_drive", "probe_longs", "selective_probe"}:
        return 25.0, -1.5, 2700
    return 25.0, -1.5, 2700


def _crypto_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """Return (giveback_pct, floor_pct) for crypto profit protection.

    Tiers (partial-profit-capture style):
      peak >= 5.0%  → floor 2.20%
      peak >= 3.0%  → floor 1.20%
      peak >= 1.8%  → floor 0.70%
      peak >= 1.0%  → floor 0.35%
      peak >= 0.80% → floor 0.18%
      peak >= 0.55% → floor 0.08% (giveback 0.20, was 0.30: 수익반납 -0.28% 사례 방지)
      peak >= 0.40% → floor 0.05% (giveback 0.20, was 0.30)
      peak >= 0.25% → floor 0.00% (new tier: 조기 반전 감지)
    """
    if peak_pnl >= 5.0:
        return 1.20, 2.20
    if peak_pnl >= 3.0:
        return 0.90, 1.20
    if peak_pnl >= 1.8:
        return 0.65, 0.70
    if peak_pnl >= 1.2:
        return 0.45, 0.45
    if peak_pnl >= 1.0:
        return 0.45, 0.35
    if peak_pnl >= 0.80:
        return 0.35, 0.18
    if peak_pnl >= 0.55:
        return 0.20, 0.08  # 0.30→0.20 giveback: peak=0.55% → floor 0.35% (was 0.25%)
    if peak_pnl >= 0.40:
        return 0.20, 0.05  # 0.30→0.20 giveback: peak=0.40% → floor 0.20% (was 0.10%)
    if peak_pnl >= 0.25:
        return 0.15, 0.00  # 신규 tier: 소폭 수익이라도 원금 보호 시작
    return 0.0, 0.0


def _crypto_eth4h_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """ETH 4H 신고점 돌파 전략 전용 트레일 — 백테스트 검증값 (2026-05-19).

    Strategy D: TP +7%, SL -3%, trail_trigger +4%, giveback -3.5%
    protect_level = max(floor, peak - giveback)

    peak >= 7.0% → giveback 4.0%, floor 4.0%  (TP 구간: 수익 최대 보호)
    peak >= 4.0% → giveback 3.5%, floor 1.5%  (trail_trigger 구간: 핵심 보호)
    peak >= 2.0% → giveback 2.0%, floor 0.0%  (초기 수익 원금 보호)
    peak <  2.0% → 트레일 없음 (SL -3.0%만 작동)
    """
    if peak_pnl >= 7.0:
        return 4.0, 4.0
    if peak_pnl >= 4.0:
        return 3.5, 1.5
    if peak_pnl >= 2.0:
        return 2.0, 0.0
    return 0.0, 0.0


def _korea_newhi_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """신고점 돌파 전략 전용 트레일 — 백테스트 검증값 (2026-05-19).

    Strategy B: target 10%, stop -4%, trail_trigger 5%, trail_pct 4%
    protect_level = max(floor, peak - giveback)

    peak >= 10% → giveback 6%, floor  6%   (대형 추세 보호)
    peak >=  5% → giveback 4%, floor  2%   (백테스트 핵심 구간)
    peak <   5% → 트레일 없음 (hard stop -4%만 작동)
    """
    if peak_pnl >= 10.0:
        return 6.0, 6.0
    if peak_pnl >= 5.0:
        return 4.0, 2.0
    return 0.0, 0.0


def _mean_reversion_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """S9/S10 평균회귀 공용 트레일 — 백테스트 검증 (2026-05-20).

    S9 RSI(2): stop -1.5%, TP 10%, 최대 10 거래일
    S10 NDayPullback: stop -1.5%, TP 10%, 최대 10 거래일

    peak >= 6% → giveback 3%, floor 4%
    peak >= 3% → giveback 2%, floor 2%
    peak >= 1.5% → giveback 1%, floor 0%
    """
    if peak_pnl >= 6.0:
        return 3.0, 4.0
    if peak_pnl >= 3.0:
        return 2.0, 2.0
    if peak_pnl >= 1.5:
        return 1.0, 0.0
    return 0.0, 0.0


def _mongtata_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """MONGTATA 에어본(평균회귀) 전용 트레일 — 백테스트 검증값 (2026-05-20).

    Strategy S2: TP +10%, SL -3%, 최대 10 거래일
    protect_level = max(floor, peak - giveback)

    peak >= 8% → giveback 4%, floor 5%  (대형 반등 수익 보호)
    peak >= 5% → giveback 3%, floor 3%  (핵심 수익 보호)
    peak >= 2% → giveback 1.5%, floor 0% (원금 보호 시작)
    peak <  2% → 트레일 없음 (SL -3.0%만 작동)
    """
    if peak_pnl >= 8.0:
        return 4.0, 5.0
    if peak_pnl >= 5.0:
        return 3.0, 3.0
    if peak_pnl >= 2.0:
        return 1.5, 0.0
    return 0.0, 0.0


def _korea_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """Korea 주식 트레일링 스탑 (기존 전략용).

    hard target 없이 트레일링만으로 청산 — 상승 추세 최대한 탑승.
    protect_level = max(floor, peak - giveback)

    peak >= 15% → giveback 3.5%, floor 10%   (대세 상승)
    peak >=  8% → giveback 2.5%, floor  5.5%
    peak >=  4% → giveback 1.5%, floor  3.0%  (2026-05-19: 2.5→3.0 상향)
    peak >=  2% → giveback 0.9%, floor  1.5%  (2026-05-19: 1.2→1.5 상향, 수익보호 강화)
    peak >= 1.5% → giveback 0.6%, floor  0.7%  (작은 수익도 보호)
    peak <  1.5% → 트레일 없음
    """
    if peak_pnl >= 15.0:
        return 3.5, 10.0
    if peak_pnl >= 8.0:
        return 2.5, 5.5
    if peak_pnl >= 4.0:
        return 1.5, 3.0  # floor 2.5 → 3.0: peak=4% 도달 시 3.0% 확보
    if peak_pnl >= 2.0:
        return 0.9, 1.5  # floor 1.2 → 1.5: peak=2% 도달 시 1.5% 확보 (기존 1.2%)
    if peak_pnl >= 1.5:
        return 0.6, 0.7
    return 0.0, 0.0


def _range_scalp_trail_rules(peak_pnl: float) -> tuple[float, float]:
    """Range scalp(평균회귀) 포지션 트레일 규칙.

    목표가 작기 때문에 수익 보호를 더 빠르게:
      peak >= 1.0%  → floor 0.55%  (목표 1.2% 근접, 절반 이상 확보)
      peak >= 0.70% → floor 0.30%  (의미있는 수익 발생, 절반 확보)
      peak >= 0.40% → floor 0.10%  (소폭 수익이라도 확보)
      peak >= 0.20% → floor 0.00%  (원금 부근에서 되돌아오면 즉시 청산)
    """
    if peak_pnl >= 1.0:
        return 0.30, 0.55
    if peak_pnl >= 0.70:
        return 0.25, 0.30
    if peak_pnl >= 0.40:
        return 0.20, 0.10
    if peak_pnl >= 0.20:
        return 0.15, 0.00
    return 0.0, 0.0


def _range_scalp_no_lift_exit(minutes_open: float, peak_pnl: float, pnl_pct: float) -> str | None:
    """Range scalp 전용 no-lift 청산 (추세추종보다 훨씬 빠름).

    평균회귀는 단기에 반등이 오지 않으면 신호 자체가 틀린 것 → 빠른 청산.
    """
    if minutes_open >= 4.0 and peak_pnl <= 0.05 and pnl_pct <= -0.25:
        return "range_scalp_no_lift"
    if minutes_open >= 7.0 and peak_pnl <= 0.15 and pnl_pct <= 0.00:
        return "range_scalp_timeout"
    if minutes_open >= 12.0:
        return "range_scalp_timeout"  # 최대 12분 → 타임아웃
    return None


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
    "rapid_tick_failed_start",
    "rapid_obvious_trend_fail",
    "rapid_range_impulse_fail",
    "rapid_range_breakout_fail",
    "rapid_high_tight_flag_fail",
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
        .limit(4)                              # 3 → 4: 더 넓은 이력 확인
    ).scalars().all()
    fail_count = 0
    for row in prior:
        pnl = float(row.pnl_pct or 0.0)
        if row.closed_reason in _STOP_LIKE_PAPER_REASONS:
            if pnl <= -0.15:                   # -0.30% → -0.15%: 작은 손실도 실패로 인식
                return True
            if pnl < 0.0:
                fail_count += 1
    # 소손실 실패 2건 이상이면 반복 패턴으로 판단
    return fail_count >= 2


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
    paper_position_defs = {
        "strategy_id": "ALTER TABLE paper_positions ADD COLUMN strategy_id VARCHAR(80) DEFAULT ''",
        "entry_profile": "ALTER TABLE paper_positions ADD COLUMN entry_profile VARCHAR(80) DEFAULT ''",
        "is_pyramided": "ALTER TABLE paper_positions ADD COLUMN is_pyramided BOOLEAN DEFAULT 0",
        "strategy_type": "ALTER TABLE paper_positions ADD COLUMN strategy_type VARCHAR(50) DEFAULT ''",
    }
    missing_paper_position = [ddl for column, ddl in paper_position_defs.items() if column not in paper_position_columns]
    if missing_paper_position:
        with engine.begin() as connection:
            for ddl in missing_paper_position:
                connection.execute(text(ddl))
    try:
        paper_order_columns = {column["name"] for column in inspector.get_columns("paper_orders")}
    except Exception:
        paper_order_columns = set()
    paper_order_defs = {
        "strategy_id": "ALTER TABLE paper_orders ADD COLUMN strategy_id VARCHAR(80) DEFAULT ''",
        "entry_profile": "ALTER TABLE paper_orders ADD COLUMN entry_profile VARCHAR(80) DEFAULT ''",
    }
    missing_paper_order = [ddl for column, ddl in paper_order_defs.items() if column not in paper_order_columns]
    if missing_paper_order:
        with engine.begin() as connection:
            for ddl in missing_paper_order:
                connection.execute(text(ddl))
    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_orders_created_at ON paper_orders(created_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_cycle_journal_run_at ON cycle_journal(run_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_positions_status ON paper_positions(status)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_positions_strategy ON paper_positions(strategy_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_shadow_signals_created_at ON shadow_signals(created_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_shadow_signals_strategy ON shadow_signals(strategy_id)"))


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
            "realized_pnl_pct": round(_weighted_paper_pnl_pct(closed), 2),
            "unrealized_pnl_pct": round(_weighted_paper_pnl_pct(open_rows), 2),
            "open_notional_pct": round(sum(_size_to_notional(row.size) for row in open_rows), 2),
        }
    return stats


def _paper_row_notional(row: PaperPositionRecord) -> float:
    notional = float(getattr(row, "notional_pct", 0.0) or 0.0)
    return notional if notional > 0 else _size_to_notional(row.size)


def _weighted_paper_pnl_pct(rows: list[PaperPositionRecord]) -> float:
    """Capital P&L contribution, not raw trade-return summation."""
    return sum(float(row.pnl_pct or 0.0) * _paper_row_notional(row) for row in rows)


def _close_reason_stats(closed_rows: list[PaperPositionRecord]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for row in closed_rows:
        reason = row.closed_reason or "unknown"
        bucket = stats.setdefault(reason, {"count": 0, "wins": 0, "losses": 0, "pnl_pct": 0.0, "raw_pnl_pct": 0.0})
        bucket["count"] += 1
        if row.pnl_pct > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        bucket["pnl_pct"] = round(float(bucket["pnl_pct"]) + row.pnl_pct * _paper_row_notional(row), 2)
        bucket["raw_pnl_pct"] = round(float(bucket["raw_pnl_pct"]) + row.pnl_pct, 2)
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
                "raw_pnl_pct": 0.0,
                "stop_like_count": 0,
            },
        )
        bucket["count"] += 1
        bucket["pnl_pct"] = round(float(bucket["pnl_pct"]) + row.pnl_pct * _paper_row_notional(row), 2)
        bucket["raw_pnl_pct"] = round(float(bucket["raw_pnl_pct"]) + row.pnl_pct, 2)
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


def _strategy_performance_stats(positions: list[PaperPositionRecord], limit: int = 20) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in positions:
        if row.status != "closed":
            continue
        # Only count explicitly-tagged positions. Focus-text inference caused old RANGING-era
        # positions to poison strategy stats (e.g. obvious_trend disabled by pre-gate failures).
        strategy_id = str(row.strategy_id or "unknown")
        bucket = buckets.setdefault(
            strategy_id,
            {
                "strategy_id": strategy_id,
                "count": 0,
                "wins": 0,
                "losses": 0,
                "raw_pnl_pct": 0.0,
                "capital_pnl_pct": 0.0,
                "peak0_count": 0,
                "stop_like_count": 0,
                "avg_size": 0.0,
                "_size_sum": 0.0,
            },
        )
        pnl = float(row.pnl_pct or 0.0)
        notional = _paper_row_notional(row)
        bucket["count"] += 1
        bucket["_size_sum"] += notional
        bucket["raw_pnl_pct"] = round(float(bucket["raw_pnl_pct"]) + pnl, 4)
        bucket["capital_pnl_pct"] = round(float(bucket["capital_pnl_pct"]) + pnl * notional, 4)
        if pnl > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        if float(row.peak_pnl_pct or 0.0) <= 0.0001:
            bucket["peak0_count"] += 1
        if row.closed_reason in _STOP_LIKE_PAPER_REASONS or str(row.closed_reason or "").startswith("rapid_"):
            bucket["stop_like_count"] += 1

    results: list[dict] = []
    for bucket in buckets.values():
        count = int(bucket["count"] or 0)
        if count <= 0:
            continue
        wins = int(bucket["wins"] or 0)
        bucket["win_rate"] = round(wins / count * 100, 1)
        bucket["avg_raw_pnl_pct"] = round(float(bucket["raw_pnl_pct"]) / count, 4)
        bucket["avg_capital_pnl_pct"] = round(float(bucket["capital_pnl_pct"]) / count, 5)
        bucket["peak0_pct"] = round(float(bucket["peak0_count"]) / count * 100, 1)
        bucket["stop_like_pct"] = round(float(bucket["stop_like_count"]) / count * 100, 1)
        bucket["avg_size"] = round(float(bucket.pop("_size_sum", 0.0)) / count, 4)
        strategy_id = str(bucket["strategy_id"])
        is_crypto_strategy = strategy_id.startswith("crypto.")
        is_retired_strategy = strategy_id in {"crypto.candidate_rotation", "crypto.ranging_momentum_leader", "crypto.ema_bounce"}
        catastrophic_peak0 = (
            is_crypto_strategy
            and count >= 5          # 2→5: 2~4건은 통계 의미 없음, 재진입 기회 부여
            and wins == 0
            and bucket["peak0_pct"] >= 80.0
            and bucket["raw_pnl_pct"] < 0
        )
        repeated_stop_like = (
            is_crypto_strategy
            and count >= 6          # 3→6: 스탑류 연속이 진짜 구조 실패인지 더 확인 후 차단
            and bucket["stop_like_pct"] >= 80.0
            and bucket["raw_pnl_pct"] < 0
        )
        bucket["health"] = (
            "disabled_candidate"
            if is_retired_strategy or catastrophic_peak0 or repeated_stop_like
            else "disabled_candidate"
            # 최극단 케이스: 7건+ 이고 peak0 100% (단 한 번도 긍정적 모멘텀 없음) → 즉시 차단
            if count >= 7 and bucket["peak0_pct"] >= 100.0
            else "disabled_candidate"
            # 극단 케이스: 10건 이상이고 peak0 90%+ (전혀 긍정적 모멘텀 없음) → 조기 차단
            if count >= 10 and bucket["peak0_pct"] >= 90.0
            else "disabled_candidate"
            # 최근 15건 이상이고 명백히 실패: 승률<20% OR peak0>75% OR 자본손실>-2%
            if count >= 15 and (bucket["win_rate"] < 20.0 or bucket["peak0_pct"] >= 75.0 or bucket["capital_pnl_pct"] < -2.0)
            else "watch"
            if count >= 8 and bucket["capital_pnl_pct"] < 0
            else "candidate"
        )
        results.append(bucket)

    return sorted(
        results,
        key=lambda item: (item["health"] == "disabled_candidate", abs(float(item["capital_pnl_pct"]))),
        reverse=True,
    )[:limit]


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
            meta = _extract_order_meta(order.action, order.rationale)
            strategy_id = order.strategy_id or infer_strategy_id(order.action, order.focus, meta)
            entry_profile = order.entry_profile or _entry_profile(order.action, order.focus, meta)
            db.add(
                PaperOrderRecord(
                    created_at=order.created_at,
                    desk=order.desk,
                    action=order.action,
                    focus=order.focus,
                    size=order.size,
                    strategy_id=strategy_id,
                    entry_profile=entry_profile,
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
            pos_focus = " ".join(
                str(part or "")
                for part in (position.focus, position.entry_profile, position.strategy_id)
            )
            target_pct, stop_pct, max_cycles = _position_thresholds(position.desk, position.action, pos_focus)
            is_range_scalp = "range_scalp" in pos_focus or "ranging_b36" in pos_focus
            # early_failure: exit if still deeply losing after fast_fail_cycle cycles
            # stale_floor:   exit near max_cycles if barely profitable
            early_failure_pct = round(stop_pct * 0.7, 2)
            stale_floor_pct = round(max(target_pct * 0.15, 0.20), 2)
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
                fast_fail_minutes = 30.0 if position.action == "attack_opening_drive" else 12.0
            else:
                fast_fail_minutes = 30.0
            # Cycles fallback for non-crypto desks (korea/us still use cycle-based check below)
            if position.desk == "crypto":
                fast_fail_cycle = 12
            elif position.desk == "korea":
                fast_fail_cycle = 20 if position.action == "attack_opening_drive" else 8
            else:
                fast_fail_cycle = 20
            if position.desk == "crypto":
                peak_pnl = float(position.peak_pnl_pct or position.pnl_pct or 0.0)
                if is_range_scalp:
                    # ── Range Scalp(평균회귀) 청산 로직 ──
                    trail_giveback, profit_floor = _range_scalp_trail_rules(peak_pnl)
                    protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
                    if position.pnl_pct >= target_pct:
                        _close_position(position, "range_scalp_target")
                    elif position.pnl_pct <= stop_pct:
                        _close_position(position, "range_scalp_stop")
                    elif (rs_no_lift := _range_scalp_no_lift_exit(minutes_open, peak_pnl, position.pnl_pct)):
                        _close_position(position, rs_no_lift)
                    elif trail_giveback and position.pnl_pct <= protect_level:
                        _close_position(position, "range_scalp_trail")
                    elif position.cycles_open >= max_cycles:
                        _close_position(position, "range_scalp_timeout")
                    continue
                elif "eth_4h_breakout" in pos_focus:
                    # ── ETH 4H 신고점 돌파 전략 청산 (Strategy D, 2026-05-19) ──
                    # 4H 타임프레임 포지션 — 15m 노이즈 기반 조기청산 비적용
                    # TP +7%, SL -3%, trail_trigger +4% / max 20봉 (80h)
                    trail_giveback, profit_floor = _crypto_eth4h_trail_rules(peak_pnl)
                    protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
                    if position.pnl_pct >= target_pct:
                        _close_position(position, "target_hit")
                    elif position.pnl_pct <= stop_pct:
                        _close_position(position, "stop_hit")
                    elif trail_giveback and position.pnl_pct <= protect_level:
                        _close_position(position, "eth4h_trail")
                    elif position.cycles_open >= max_cycles:
                        _close_position(position, "time_exit")
                    continue
                # ── 추세추종 청산 로직 (기존) ──
                trail_giveback, profit_floor = _crypto_trail_rules(peak_pnl)
                protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
                if position.pnl_pct >= target_pct:
                    _close_position(position, "target_hit")
                elif position.pnl_pct <= stop_pct:
                    _close_position(position, "stop_hit")
                elif (no_lift_reason := _crypto_no_lift_exit_reason(minutes_open, peak_pnl, position.pnl_pct)):
                    _close_position(position, no_lift_reason)
                elif trend_exit_reason := _crypto_trend_exit_reason(cycle_signal_meta.get(position.symbol, {}), position.pnl_pct, minutes_open):
                    _close_position(position, trend_exit_reason)
                elif minutes_open >= fast_fail_minutes and position.pnl_pct <= -0.60 and peak_pnl <= 0.10:
                    _close_position(position, "failed_ignition")
                elif trail_giveback and position.pnl_pct <= protect_level:
                    _close_position(position, "profit_protect" if peak_pnl < 1.8 else "trend_trail")
                elif peak_pnl >= 0.40 and minutes_open >= 3.0 and position.pnl_pct <= max(-0.50, peak_pnl - 1.20):
                    _close_position(position, "failed_followthrough")
                elif position.cycles_open >= max_cycles and position.pnl_pct < 0.8:
                    _close_position(position, "time_exit")
                continue
            # ── Korea 주식 청산 (stock_backtest_v3: 트레일링 스탑 포함) ──
            if position.desk == "korea":
                peak_pnl = float(position.peak_pnl_pct or position.pnl_pct or 0.0)
                # 전략별 전용 trail 규칙 사용 (백테스트 검증값)
                if "new_high_breakout" in pos_focus:
                    trail_giveback, profit_floor = _korea_newhi_trail_rules(peak_pnl)
                elif "mongtata_airborne" in pos_focus:
                    trail_giveback, profit_floor = _mongtata_trail_rules(peak_pnl)
                elif "rsi2_mean_reversion" in pos_focus or "nday_pullback" in pos_focus:
                    trail_giveback, profit_floor = _mean_reversion_trail_rules(peak_pnl)
                else:
                    trail_giveback, profit_floor = _korea_trail_rules(peak_pnl)
                protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
                if position.pnl_pct >= target_pct:
                    _close_position(position, "target_hit")
                elif position.pnl_pct <= stop_pct:
                    _close_position(position, "stop_hit")
                elif trail_giveback and position.pnl_pct <= protect_level:
                    _close_position(position, "korea_trail")
                elif position.cycles_open >= fast_fail_cycle and position.pnl_pct <= early_failure_pct:
                    _close_position(position, "early_failure")
                elif position.cycles_open >= max_cycles:
                    _close_position(position, "stale_exit")
                else:
                    # ── 피라미딩 트리거 ──────────────────────────────────────
                    # 브레이크아웃/갭업 종목이 peak +3% 이상 도달 시 0.10x 추가 진입
                    # 2026-05-19: profit-floor lock 적용
                    #   - 피라미드 진입 전 기존 포지션의 peak_pnl_pct를 강제 상향
                    #   - 목적: 피라미드 실패시에도 기존 수익이 손실로 전환되지 않도록
                    #   - 방법: peak = max(current_peak, current_pnl + trail_giveback - 0.10)
                    #     → protect_level = max(floor, new_peak - giveback) = current_pnl - 0.10%
                    #     → trail fires on 0.10% drop; combined = (pnl-0.10%) - pyramid_stop(1.5%) > 0 when pnl>=1.6%
                    _pyramid_ok = (
                        not getattr(position, "is_pyramided", False)
                        and "open_reversal" not in pos_focus
                        and "opening_drive" not in pos_focus
                        and "close_drive" not in pos_focus
                        and "pyramid" not in pos_focus
                        and position.action != "attack_opening_drive"
                        and peak_pnl >= 3.0
                        and position.pnl_pct >= 2.0
                    )
                    if _pyramid_ok and position.current_price > 0:
                        # Korea desk 내 총 포지션(피라미드 포함) 4개 미만일 때만 허용
                        _total_korea = sum(
                            1 for p in open_positions
                            if p.desk == "korea" and p.status == "open"
                        )
                        if _total_korea < 4:
                            # ── 수익 하한선 잠금 (profit-floor lock) ────────────
                            # 피라미드 진입 전에 기존 포지션의 trail stop을 현재 수익 위로 올림
                            # trail_giveback at current peak (via _korea_trail_rules)
                            _pf_giveback, _pf_floor = _korea_trail_rules(peak_pnl)
                            # 새 peak_pnl = max(현재peak, 현재pnl + giveback - 0.10%)
                            # 이렇게 하면: protect_level = max(floor, new_peak - giveback)
                            #             = current_pnl - 0.10%  → 0.10% 하락시 trail 발동
                            #             합산 = (pnl-0.10%) - pyramid_stop(1.5%) > 0 ✓
                            _required_peak = position.pnl_pct + _pf_giveback - 0.10
                            if position.peak_pnl_pct < _required_peak:
                                _log.info(
                                    "Korea pyramid profit-floor lock: %s peak %.2f%% → %.2f%% (pnl=%.2f%%)",
                                    position.symbol, position.peak_pnl_pct, _required_peak, position.pnl_pct,
                                )
                                position.peak_pnl_pct = _required_peak
                            position.is_pyramided = True
                            pyr = PaperPositionRecord(
                                desk="korea",
                                symbol=position.symbol,
                                status="open",
                                action="probe_longs",
                                size="0.10x",
                                opened_at=utcnow_iso(),
                                entry_price=position.current_price,
                                current_price=position.current_price,
                                pnl_pct=0.0,
                                peak_pnl_pct=0.0,
                                cycles_open=0,
                                focus=f"pyramid: {pos_focus or position.action}",
                                strategy_id="korea.pyramid",
                                entry_profile="pyramid",
                                is_pyramided=False,
                                strategy_type="pyramid",
                            )
                            db.add(pyr)
                            _log.info(
                                "Korea pyramid: %s peak=%.1f%% → +0.10x @ %.0f (profit-floor locked at %.2f%%)",
                                position.symbol, peak_pnl, position.current_price,
                                position.pnl_pct,
                            )
                continue
            # ── US / 기타 데스크 청산 ──
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

        # 평균회귀 전략 상관관계 리스크 제한: 코인 동시 최대 2포지션
        _CRYPTO_MR_STRATEGY_IDS = frozenset({
            "crypto.rsi2_mean_reversion",
            "crypto.nday_pullback",
            "crypto.mongtata_airborne",
        })
        _open_crypto_mr_count = sum(
            1 for p in open_positions
            if p.desk == "crypto" and p.status == "open"
            and (p.strategy_id or "") in _CRYPTO_MR_STRATEGY_IDS
        )

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
            strategy_id = order.strategy_id or infer_strategy_id(order.action, order.focus, meta)
            entry_profile = order.entry_profile or _entry_profile(order.action, order.focus, meta)

            # 코인 평균회귀 상관관계 리스크 가드 (max 2 동시 포지션)
            if order.desk == "crypto" and strategy_id in _CRYPTO_MR_STRATEGY_IDS:
                if _open_crypto_mr_count >= 2:
                    _log.info(
                        "correlation_limit: skip %s strategy=%s (already %d crypto MR positions open)",
                        symbol, strategy_id, _open_crypto_mr_count,
                    )
                    continue
                _open_crypto_mr_count += 1  # optimistic count for this batch

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
                strategy_id=strategy_id,
                entry_profile=entry_profile,
                strategy_type=_derive_strategy_type(order.focus or "", order.action or "", order.desk or ""),
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
    """Fast tick/price guard for open crypto positions between full strategy cycles."""
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
            pos_focus_rapid = " ".join(
                str(part or "")
                for part in (position.focus, position.entry_profile, position.strategy_id)
            )
            target_pct, stop_pct, _ = _position_thresholds(position.desk, position.action, pos_focus_rapid)
            peak_pnl = float(position.peak_pnl_pct or position.pnl_pct or 0.0)
            is_range_scalp_rapid = "range_scalp" in pos_focus_rapid or "ranging_b36" in pos_focus_rapid
            if is_range_scalp_rapid:
                trail_giveback, profit_floor = _range_scalp_trail_rules(peak_pnl)
            else:
                trail_giveback, profit_floor = _crypto_trail_rules(peak_pnl)
            protect_level = max(profit_floor, peak_pnl - trail_giveback) if trail_giveback else 0.0
            is_range_impulse = "range_impulse" in pos_focus_rapid
            is_range_breakout = "range_breakout" in pos_focus_rapid
            is_high_tight_flag = "high_tight_flag" in pos_focus_rapid
            is_obvious_trend = "obvious_trend" in pos_focus_rapid
            minutes_open = 0.0
            try:
                opened_dt = datetime.fromisoformat(str(position.opened_at).replace("Z", "+00:00"))
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                minutes_open = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
            except (ValueError, TypeError):
                minutes_open = 0.0
            if is_range_scalp_rapid:
                # ── Range Scalp rapid guard: 빠른 손절/트레일 ──
                if position.pnl_pct >= target_pct:
                    closed_symbols.append((position.symbol, "rapid_range_scalp_target"))
                    _close_position(position, "rapid_range_scalp_target")
                    paper_closed += 1
                elif peak_pnl <= 0.0 and minutes_open >= 0.15 and position.pnl_pct <= -0.12:
                    # 진입 후 반등 없이 낙하: 9s(0.15min) 후 -0.12%에서 즉시 청산 (12s→9s, -0.15%→-0.12%)
                    # ANKR/FIL peak=0% → -0.59% 패턴: 더 빠른 탈출
                    closed_symbols.append((position.symbol, "rapid_range_scalp_no_lift"))
                    _close_position(position, "rapid_range_scalp_no_lift")
                    paper_closed += 1
                elif position.pnl_pct <= stop_pct:
                    closed_symbols.append((position.symbol, "rapid_range_scalp_stop"))
                    _close_position(position, "rapid_range_scalp_stop")
                    paper_closed += 1
                elif trail_giveback and position.pnl_pct <= protect_level:
                    closed_symbols.append((position.symbol, "rapid_range_scalp_trail"))
                    _close_position(position, "rapid_range_scalp_trail")
                    paper_closed += 1
                elif peak_pnl <= 0.05 and minutes_open >= 1.0 and position.pnl_pct <= -0.18:
                    # 소폭 반등 후 다시 낙하 → 1.0min 후 -0.18%에서 청산 (1.5min/-0.22에서 단축)
                    closed_symbols.append((position.symbol, "rapid_range_scalp_no_lift"))
                    _close_position(position, "rapid_range_scalp_no_lift")
                    paper_closed += 1
                continue
            if position.pnl_pct >= target_pct:
                closed_symbols.append((position.symbol, "rapid_target_hit"))
                _close_position(position, "rapid_target_hit")
                paper_closed += 1
            elif is_obvious_trend and minutes_open >= 1.5 and peak_pnl <= 0.05 and position.pnl_pct <= -0.22:
                # obvious_trend 실패 빠른 청산: 0.25min → 1.5min (슬리피지 흡수 시간 확보)
                # 근거: 진입 15초 내 -0.22% 조건 발동 = 슬리피지 + 정상 조정을 실패로 오판
                # 1.5분(~11사이클)은 진짜 방향 확인에 충분한 시간
                closed_symbols.append((position.symbol, "rapid_obvious_trend_fail"))
                _close_position(position, "rapid_obvious_trend_fail")
                paper_closed += 1
            elif is_obvious_trend and position.pnl_pct <= -0.42:
                # obvious_trend 최대 손실 -0.38% → -0.42%: 슬리피지 감안 (진입비용 ~0.1%)
                closed_symbols.append((position.symbol, "rapid_obvious_trend_fail"))
                _close_position(position, "rapid_obvious_trend_fail")
                paper_closed += 1
            elif is_range_impulse and minutes_open >= 0.25 and peak_pnl <= 0.05 and position.pnl_pct <= -0.25:
                closed_symbols.append((position.symbol, "rapid_range_impulse_fail"))
                _close_position(position, "rapid_range_impulse_fail")
                paper_closed += 1
            elif is_range_impulse and position.pnl_pct <= -0.40:
                closed_symbols.append((position.symbol, "rapid_range_impulse_fail"))
                _close_position(position, "rapid_range_impulse_fail")
                paper_closed += 1
            elif is_range_impulse and peak_pnl >= 0.28 and position.pnl_pct <= max(0.02, peak_pnl - 0.35):
                closed_symbols.append((position.symbol, "rapid_range_impulse_protect"))
                _close_position(position, "rapid_range_impulse_protect")
                paper_closed += 1
            elif is_range_breakout and minutes_open >= 0.25 and peak_pnl <= 0.05 and position.pnl_pct <= -0.35:
                closed_symbols.append((position.symbol, "rapid_range_breakout_fail"))
                _close_position(position, "rapid_range_breakout_fail")
                paper_closed += 1
            elif is_range_breakout and peak_pnl >= 0.45 and position.pnl_pct <= max(0.08, peak_pnl - 0.45):
                closed_symbols.append((position.symbol, "rapid_range_breakout_protect"))
                _close_position(position, "rapid_range_breakout_protect")
                paper_closed += 1
            elif is_high_tight_flag and minutes_open >= 0.25 and peak_pnl <= 0.05 and position.pnl_pct <= -0.30:
                closed_symbols.append((position.symbol, "rapid_high_tight_flag_fail"))
                _close_position(position, "rapid_high_tight_flag_fail")
                paper_closed += 1
            elif is_high_tight_flag and peak_pnl >= 0.35 and position.pnl_pct <= max(0.05, peak_pnl - 0.38):
                closed_symbols.append((position.symbol, "rapid_high_tight_flag_protect"))
                _close_position(position, "rapid_high_tight_flag_protect")
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
            elif (
                # fallback: stream 없어도 peak=0 AND 빠른 역행 → 즉시 청산
                # 0.5min/−0.25% → 0.33min/−0.22%: avg −0.616% → −0.22% 손실 대폭 절감 목표
                not is_range_scalp_rapid
                and peak_pnl <= 0.05
                and minutes_open >= 0.33
                and position.pnl_pct <= -0.22
            ):
                closed_symbols.append((position.symbol, "rapid_tick_failed_start"))
                _close_position(position, "rapid_tick_failed_start")
                paper_closed += 1
            elif (
                (stream := summarize_stream_momentum(position.symbol, max_age_seconds=3.5))
                and bool(stream.get("stream_reversal", False))
                and minutes_open >= 0.5
                and position.pnl_pct <= 0.15
                and (
                    (peak_pnl <= 0.15 and position.pnl_pct <= -0.12)
                    or (peak_pnl >= 0.20 and position.pnl_pct <= max(-0.15, peak_pnl - 0.55))
                )
            ):
                reason = "rapid_tick_failed_start" if peak_pnl <= 0.15 else "rapid_tick_reversal"
                closed_symbols.append((position.symbol, reason))
                _close_position(position, reason)
                paper_closed += 1
            elif position.pnl_pct <= stop_pct:
                closed_symbols.append((position.symbol, "rapid_stop_hit"))
                _close_position(position, "rapid_stop_hit")
                paper_closed += 1
            elif (
                # 3분 타임컷: 진입 후 3분 경과 + peak 수익 없음 + 소폭 손실 → 기회비용 즉시 절감
                # Gemini 제안: peak < +0.2% at 3min → 모멘텀 없음, 74% peak=0 패턴 대응
                # rapid_tick_failed_start(0.33min/-0.22%)와 rapid_no_lift(10min/-0.30%) 사이 공백 메움
                not is_range_scalp_rapid       # range_scalp은 별도 타임아웃 규칙 적용
                and minutes_open >= 3.0        # 3분 경과 (정상 조정 확인 충분한 시간)
                and peak_pnl <= 0.05           # 한 번도 의미있는 수익 없음 (peak=0% 패턴)
                and position.pnl_pct <= -0.10  # 현재 소폭 손실 (flat/소폭+ 포지션은 유지)
            ):
                closed_symbols.append((position.symbol, "time_cut_3min"))
                _close_position(position, "time_cut_3min")
                paper_closed += 1
            elif minutes_open >= 4.0 and peak_pnl <= 0.05 and position.pnl_pct <= -0.75:
                closed_symbols.append((position.symbol, "rapid_failed_start"))
                _close_position(position, "rapid_failed_start")
                paper_closed += 1
            elif (
                minutes_open >= 3.0                # 4.0 → 3.0: 더 빨리 감지
                and peak_pnl <= 0.08               # 0.05 → 0.08: 살짝 여유
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
                        "strategy_id": row.strategy_id or infer_strategy_id(row.action, row.focus, meta),
                        "entry_profile": row.entry_profile or _entry_profile(row.action, row.focus, meta),
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
                    "strategy_id": row.strategy_id or infer_strategy_id(row.action, row.focus),
                    "entry_profile": row.entry_profile or _entry_profile(row.action, row.focus),
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
                    "strategy_id": row.strategy_id or infer_strategy_id(row.action, row.focus),
                    "entry_profile": row.entry_profile or _entry_profile(row.action, row.focus),
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
            realized_pnl = round(_weighted_paper_pnl_pct(closed_today), 2)
            unrealized_pnl = round(_weighted_paper_pnl_pct(open_positions), 2)
            expectancy_pct = round(realized_pnl / closed_count, 2) if closed_count else 0.0
            # Cumulative (all-time) — compounding base
            # Capital-weighted P&L keeps the dashboard aligned with actual exposure.
            cumulative_realized_pnl = round(_weighted_paper_pnl_pct(all_closed), 2)
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
                # 최근 80건 — strategy_id가 명시된 포지션만 평가.
                # "unknown" (태깅 이전 레거시) 제외 → 실제 전략 데이터로만 health 판단.
                # 이전: 80건 중 60건이 unknown → 전략 20건만 평가 가능.
                # 이후: 태깅된 포지션만, 최대 80건 → 전략 건강도가 실제 성과를 반영.
                "strategy_performance_stats": _strategy_performance_stats(
                    sorted(
                        [
                            r for r in positions
                            if r.status == "closed"
                            and (r.strategy_id or "").strip() not in ("", "unknown")
                        ],
                        key=lambda r: r.id or 0,
                        reverse=True,
                    )[:80]
                ),
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
            "strategy_performance_stats": [],
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
                    "strategy_id": row.strategy_id or infer_strategy_id(row.action, row.focus),
                    "entry_profile": row.entry_profile or _entry_profile(row.action, row.focus),
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
                    "strategy_id": row.strategy_id or infer_strategy_id(row.action, row.focus),
                    "entry_profile": row.entry_profile or _entry_profile(row.action, row.focus),
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_strategy_performance_stats(window: int = 300) -> list[dict]:
    """Recent paper strategy attribution for gating and dashboard diagnostics.
    Note: "unknown" strategy_id rows (pre-tagging legacy) are excluded from evaluation.
    """
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .where(
                    PaperPositionRecord.status == "closed",
                    PaperPositionRecord.strategy_id.isnot(None),
                    PaperPositionRecord.strategy_id != "",
                    PaperPositionRecord.strategy_id != "unknown",
                )
                .order_by(PaperPositionRecord.id.desc())
                .limit(window)
            ).scalars().all()
            return _strategy_performance_stats(list(rows), limit=50)
    except OperationalError:
        rebuild_db()
        return []


def save_shadow_signal(
    *,
    desk: str,
    symbol: str,
    strategy_id: str,
    entry_profile: str = "",
    source: str = "",
    action: str = "",
    focus: str = "",
    reason: str = "",
    score: float = 0.0,
    stream_score: float = 0.0,
    notional_pct: float = 0.0,
    payload: dict | None = None,
    dedupe_seconds: int = 60,
) -> bool:
    """Record a blocked strategy signal without opening a position.

    Dedupe prevents websocket ticks from writing the same blocked signal repeatedly.
    """
    init_db()
    created_at = utcnow_iso()
    payload = payload or {}
    try:
        with SessionLocal() as db:
            latest = db.execute(
                select(ShadowSignalRecord)
                .where(
                    ShadowSignalRecord.desk == desk,
                    ShadowSignalRecord.symbol == symbol,
                    ShadowSignalRecord.strategy_id == strategy_id,
                    ShadowSignalRecord.source == source,
                    ShadowSignalRecord.reason == reason,
                )
                .order_by(ShadowSignalRecord.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            latest_dt = _local_datetime_from_iso(latest.created_at) if latest else None
            if latest_dt is not None:
                age = (datetime.now(_local_timezone()) - latest_dt).total_seconds()
                if age < dedupe_seconds:
                    return False
            db.add(
                ShadowSignalRecord(
                    created_at=created_at,
                    desk=desk,
                    symbol=symbol,
                    strategy_id=strategy_id,
                    entry_profile=entry_profile,
                    source=source,
                    action=action,
                    focus=focus[:240],
                    reason=reason,
                    score=round(float(score or 0.0), 4),
                    stream_score=round(float(stream_score or 0.0), 4),
                    notional_pct=round(float(notional_pct or 0.0), 4),
                    payload=payload,
                )
            )
            db.commit()
            return True
    except OperationalError:
        rebuild_db()
        return False


def load_recent_shadow_signals(limit: int = 50) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(ShadowSignalRecord)
                .order_by(ShadowSignalRecord.id.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "created_at": row.created_at,
                    "desk": row.desk,
                    "symbol": row.symbol,
                    "strategy_id": row.strategy_id,
                    "entry_profile": row.entry_profile,
                    "source": row.source,
                    "action": row.action,
                    "focus": row.focus,
                    "reason": row.reason,
                    "score": row.score,
                    "stream_score": row.stream_score,
                    "notional_pct": row.notional_pct,
                    "payload": row.payload or {},
                }
                for row in rows
            ]
    except OperationalError:
        rebuild_db()
        return []


def load_shadow_signal_stats(window: int = 300) -> list[dict]:
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(ShadowSignalRecord)
                .order_by(ShadowSignalRecord.id.desc())
                .limit(window)
            ).scalars().all()
    except OperationalError:
        rebuild_db()
        return []
    buckets: dict[str, dict] = {}
    for row in rows:
        key = row.strategy_id or "unknown"
        bucket = buckets.setdefault(
            key,
            {
                "strategy_id": key,
                "count": 0,
                "symbols": set(),
                "sources": set(),
                "reasons": {},
                "score_sum": 0.0,
                "stream_sum": 0.0,
                "latest_at": "",
            },
        )
        bucket["count"] += 1
        bucket["symbols"].add(row.symbol)
        bucket["sources"].add(row.source)
        bucket["reasons"][row.reason] = int(bucket["reasons"].get(row.reason, 0)) + 1
        bucket["score_sum"] += float(row.score or 0.0)
        bucket["stream_sum"] += float(row.stream_score or 0.0)
        if not bucket["latest_at"] or row.created_at > bucket["latest_at"]:
            bucket["latest_at"] = row.created_at
    result = []
    for bucket in buckets.values():
        count = max(int(bucket["count"]), 1)
        reasons = sorted(bucket["reasons"].items(), key=lambda item: item[1], reverse=True)
        result.append(
            {
                "strategy_id": bucket["strategy_id"],
                "count": count,
                "symbols": sorted(bucket["symbols"])[:8],
                "sources": sorted(bucket["sources"]),
                "top_reason": reasons[0][0] if reasons else "",
                "avg_score": round(float(bucket["score_sum"]) / count, 4),
                "avg_stream_score": round(float(bucket["stream_sum"]) / count, 4),
                "latest_at": bucket["latest_at"],
            }
        )
    return sorted(result, key=lambda item: item["count"], reverse=True)


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
    summary_applied_mode = str(route_summary.get("applied_mode") or "paper")
    summary_broker_live = bool(route_summary.get("broker_live"))
    with SessionLocal() as db:
        for detail in details:
            desk = str(detail.get("desk", "") or "")
            symbol = str(detail.get("symbol", "") or "")
            action = str(detail.get("action", "") or "")
            order = order_lookup.get((desk, symbol, action))
            applied_mode = str(detail.get("applied_mode") or summary_applied_mode or "paper")
            broker_live = bool(detail.get("broker_live", summary_broker_live))
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
                        "applied_mode": row.applied_mode,
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
                    select(PaperPositionRecord).where(
                        PaperPositionRecord.desk == row.desk,
                        PaperPositionRecord.symbol == row.symbol,
                        PaperPositionRecord.status == "open",
                    )
                ).scalar_one_or_none()
                if open_position is None:
                    closed_position = db.execute(
                        select(PaperPositionRecord)
                        .where(
                            PaperPositionRecord.desk == row.desk,
                            PaperPositionRecord.symbol == row.symbol,
                            PaperPositionRecord.status == "closed",
                        )
                        .order_by(PaperPositionRecord.id.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    if closed_position is not None:
                        row.effect_status = "already_reconciled"
                        row.linked_closed_symbol = closed_position.symbol
                        updated += 1
                        continue
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
            notional = float(row.notional_pct or 1.0)
            equity *= 1 + (row.realized_pnl_pct * notional) / 100
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak * 100
            if dd < max_drawdown:
                max_drawdown = dd

        cumulative_realized_pnl_pct = round((equity - 1.0) * 100, 2)
        total_unrealized_pnl_pct = round(sum(p.unrealized_pnl_pct * float(p.notional_pct or 1.0) for p in open_pos), 2)

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


def _derive_strategy_type(focus: str, action: str, desk: str) -> str:
    f = (focus or "").lower()
    a = (action or "").lower()
    if "gap_fill" in f:        return "gap_fill"
    if "pullback_ma" in f:     return "pullback_ma"
    if "open_reversal" in f:   return "open_reversal"
    if "opening_drive" in f:   return "opening_drive"
    if "close_drive" in f:     return "close_drive"
    if "dip_bounce" in f:      return "dip_bounce"
    if "emma_scalp" in f:      return "emma_scalp"
    if "neo_micro_scalp" in f: return "neo_micro_scalp"
    if "smart_money_flow" in f: return "smart_money_flow"
    if "ranging_strength_follow" in f: return "ranging_strength_follow"
    if "ranging_momentum_leader" in f: return "ranging_momentum_leader"
    if "pyramid" in f:         return "pyramid"
    if "breakout" in f:        return "breakout"
    if a == "attack_opening_drive": return "opening_drive"
    if a in {"probe_longs", "selective_probe"}:
        return f"{desk}_probe"
    return "other"


def get_strategy_stats() -> list[dict]:
    """전략별 성과 통계 — 전략 유형별 승률/평균 수익 집계."""
    init_db()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(PaperPositionRecord)
                .where(PaperPositionRecord.status == "closed",
                       PaperPositionRecord.pnl_pct.isnot(None))
                .order_by(PaperPositionRecord.id.desc())
                .limit(500)
            ).scalars().all()
    except Exception:
        return []
    by_type: dict[str, dict] = {}
    for r in rows:
        st = r.strategy_type or _derive_strategy_type(r.focus or "", r.action or "", r.desk or "")
        if st not in by_type:
            by_type[st] = {"n": 0, "wins": 0, "pnl_sum": 0.0, "desk": r.desk or ""}
        by_type[st]["n"] += 1
        if (r.pnl_pct or 0.0) > 0:
            by_type[st]["wins"] += 1
        by_type[st]["pnl_sum"] += float(r.pnl_pct or 0.0)
    result = []
    for st, d in sorted(by_type.items(), key=lambda x: -x[1]["n"]):
        n = d["n"]
        wins = int(d["wins"])
        losses = max(n - wins, 0)
        avg_pnl = round(d["pnl_sum"] / n, 2) if n else 0.0
        total_pnl = round(d["pnl_sum"], 2)
        result.append({
            "strategy_type": st,
            "desk": d["desk"],
            "n_trades": n,
            "total_trades": n,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "avg_pnl": avg_pnl,
            "avg_pnl_pct": avg_pnl,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl,
        })
    return result


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


def load_hours_since_last_loss(desk: str = "crypto") -> float:
    """Hours elapsed since the most recent losing trade for a desk.

    Returns 999.0 if no losses found (treated as "streak is very old").
    Used to apply time-based streak decay: stale streaks from crashes or
    non-trading periods should not permanently suppress the risk budget.
    """
    init_db()
    try:
        with SessionLocal() as db:
            last_loss = db.execute(
                select(PaperPositionRecord)
                .where(
                    PaperPositionRecord.status == "closed",
                    PaperPositionRecord.desk == desk,
                    PaperPositionRecord.pnl_pct <= 0,
                )
                .order_by(PaperPositionRecord.id.desc())
                .limit(1)
            ).scalars().first()
        if not last_loss or not last_loss.closed_at:
            return 999.0
        closed_at_str = str(last_loss.closed_at)
        if closed_at_str.endswith("Z"):
            closed_at_str = closed_at_str[:-1] + "+00:00"
        if "+" not in closed_at_str and closed_at_str[-6] != "+":
            closed_at_str += "+00:00"
        closed_dt = datetime.fromisoformat(closed_at_str)
        if closed_dt.tzinfo is None:
            closed_dt = closed_dt.replace(tzinfo=timezone.utc)
        hours_ago = (datetime.now(timezone.utc) - closed_dt).total_seconds() / 3600
        return round(max(0.0, hours_ago), 1)
    except Exception:
        return 0.0


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

    snapshot_name_lookup: dict[tuple[str, str], str] = {}

    def _position_display_name(row: PaperPositionRecord) -> str:
        symbol = str(row.symbol or "").strip()
        if not symbol:
            return "unknown"
        focus = str(row.focus or "")
        if row.desk == "crypto":
            return symbol.replace("KRW-", "")
        if row.desk in {"korea", "us"}:
            snapshot_name = snapshot_name_lookup.get((row.desk, symbol), "")
            if snapshot_name:
                return f"{snapshot_name}({symbol})"
            marker = f"({symbol})"
            if marker in focus:
                name = focus.split(marker, 1)[0].strip()
                if name:
                    return f"{name}({symbol})"
            if focus:
                for sep in (" selective ", " selective_probe", " Opening ", " momentum ", " - "):
                    if sep in focus:
                        name = focus.split(sep, 1)[0].strip()
                        if name and symbol not in name and len(name) <= 40:
                            return f"{name}({symbol})"
            return symbol
        return symbol

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

    def _symbol_stats_by_desk(rows: list[PaperPositionRecord]) -> dict[str, list[dict]]:
        desks = ("crypto", "korea", "us")
        result: dict[str, list[dict]] = {}
        for desk in desks:
            desk_rows = [row for row in rows if row.desk == desk]
            result[desk] = _stats_by(desk_rows, _position_display_name)[:20]
        return result

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
            rec = db.get(StateRecord, "primary")
            snapshot = dict(rec.market_snapshot or {}) if rec else {}
            for key, desk in (
                ("gap_candidates", "korea"),
                ("stock_leaders", "korea"),
                ("close_drive_candidates", "korea"),
                ("gap_fill_candidates", "korea"),
                ("pullback_ma_candidates", "korea"),
                ("us_leaders", "us"),
            ):
                for item in snapshot.get(key, []) or []:
                    symbol = str(item.get("ticker", "")).strip()
                    name = str(item.get("name", "") or "").strip()
                    if symbol and name:
                        snapshot_name_lookup[(desk, symbol)] = name
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
        "symbol_stats": _stats_by(closed, _position_display_name)[:20],
        "symbol_stats_by_desk": _symbol_stats_by_desk(closed),
        "pnl_distribution": pnl_distribution,
        "open_positions": open_positions,
        "recent_closed": recent_closed,
        "equity_curve": equity_curve,
        "streak_info": streak_info,
    }

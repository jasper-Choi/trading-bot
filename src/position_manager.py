"""
가상 포지션 관리 (15분봉 기준) — 인메모리 + DB 영속화.

주요 제약:
  - 동시 최대 포지션: 시장 국면별 REGIME_CONFIG["max_positions"]
  - 일일 손실 한도:   -3% → BEAR, -5% → VOLATILE + 신규 차단
  - 연속 손실 3회   → 포지션 크기 50% 축소
  - 연속 손실 5회   → 당일 거래 중단
  - VOLATILE       → 신규 진입 차단
  - 피라미딩        → BULL 모드, +2% 수익 시 최대 3회 추가
"""

import os
import threading
from datetime import datetime

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

_DT_FMT = "%Y-%m-%d %H:%M:%S"

# ── 인메모리 저장소 ───────────────────────────────────────────────────────────
_lock:      threading.Lock = threading.Lock()
_positions: dict           = {}
_history:   list           = []


def _now_str() -> str:
    return datetime.now(config.KST).strftime(_DT_FMT)

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s[:19].replace("T", " "), _DT_FMT)


# ── DB 영속화 헬퍼 ────────────────────────────────────────────────────────────

def _save_position(pos: dict, coin: str | None = None) -> None:
    """포지션을 DB에 upsert."""
    try:
        from src.database import db_upsert_position
        p = dict(pos)
        if coin:
            p["coin"] = coin
        p.setdefault("market", "coin")
        db_upsert_position(p)
    except Exception as exc:
        print(f"[PM] DB 포지션 저장 실패: {exc}")


def _save_trade(record: dict) -> None:
    """거래 이력을 DB에 삽입."""
    try:
        from src.database import db_insert_trade
        r = dict(record)
        r.setdefault("market", "coin")
        db_insert_trade(r)
    except Exception as exc:
        print(f"[PM] DB 거래 저장 실패: {exc}")


# ── DB에서 인메모리 복원 (앱 시작 시) ────────────────────────────────────────

def _init_from_db() -> None:
    """앱 시작 시 DB에서 인메모리 저장소를 복원합니다.

    로컬 SQLite, Railway PostgreSQL 모두 동일한 경로를 사용합니다.
    """
    try:
        from src.database import init_db, db_load_positions, db_load_trades
        init_db()  # 테이블 생성 + 파일 마이그레이션 (멱등)
        db_pos = db_load_positions(market="coin")
        _positions.update(db_pos)
        # 최근 200건만 in-memory로 (일일 PnL, 연속 손실 계산에 충분)
        db_hist = db_load_trades(market="coin", limit=200)
        _history.extend(reversed(db_hist))  # 오래된 순으로
        print(f"[PM] DB 복원: 포지션 {len(db_pos)}개, 거래 이력 {len(db_hist)}건")
    except Exception as exc:
        print(f"[PM] DB 복원 실패 (빈 메모리로 시작): {exc}")


_init_from_db()


# ── 조회 ─────────────────────────────────────────────────────────────────────

def load_positions() -> dict:
    with _lock:
        return dict(_positions)

def load_history() -> list[dict]:
    with _lock:
        return list(_history)

def get_position(coin: str) -> dict | None:
    with _lock:
        pos = _positions.get(coin)
    return pos if pos and pos.get("status") == "open" else None

def get_open_positions() -> list[dict]:
    with _lock:
        return [p for p in _positions.values() if p.get("status") == "open"]

def count_open_positions() -> int:
    return len(get_open_positions())


# ── 일일 손익 / 연속 손실 ─────────────────────────────────────────────────────

def get_daily_pnl() -> float:
    """오늘 청산된 거래들의 누적 손익(원)."""
    today = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        return sum(
            r.get("pnl", 0)
            for r in _history
            if r.get("exit_date", "").startswith(today)
        )

def get_consecutive_losses() -> int:
    """최근 연속 손실 횟수."""
    with _lock:
        history = list(_history)
    count = 0
    for trade in reversed(history):
        if trade.get("pnl", 0) < 0:
            count += 1
        else:
            break
    return count


# ── 방어 트리거 ───────────────────────────────────────────────────────────────

def _check_defense_triggers():
    """일간 손실 / 연속 손실에 따라 시장 국면을 강제 전환합니다."""
    try:
        from src.market_regime import market_regime, BEAR, VOLATILE
    except ImportError:
        return

    daily_pnl     = get_daily_pnl()
    total_capital = config.INITIAL_CAPITAL_PER_COIN * config.MAX_POSITIONS
    if total_capital <= 0:
        return

    loss_pct = -daily_pnl / total_capital

    if loss_pct >= 0.05:
        market_regime.set_regime(VOLATILE, f"일간손실{loss_pct*100:.1f}%≥5%")
    elif loss_pct >= 0.03:
        market_regime.set_regime(BEAR, f"일간손실{loss_pct*100:.1f}%≥3%")

    consec = get_consecutive_losses()
    if consec >= 5:
        market_regime.set_regime(VOLATILE, f"연속손실{consec}회≥5")


# ── 포지션 크기 결정 ──────────────────────────────────────────────────────────

def _get_position_capital() -> float:
    base   = config.INITIAL_CAPITAL_PER_COIN
    consec = get_consecutive_losses()
    return base * 0.5 if consec >= 3 else base


# ── 진입 가능 여부 ────────────────────────────────────────────────────────────

def can_open_new_position() -> tuple[bool, str]:
    _check_defense_triggers()

    consec = get_consecutive_losses()
    if consec >= 5:
        return False, f"연속 손실 {consec}회 — 당일 거래 중단"

    try:
        from src.market_regime import market_regime
        regime_cfg  = market_regime.get_config()
        max_pos     = regime_cfg["max_positions"]
        regime_name = market_regime.regime
    except ImportError:
        max_pos     = config.MAX_POSITIONS
        regime_name = "N/A"

    if max_pos == 0:
        return False, f"시장 국면 {regime_name} — 신규 진입 차단"

    open_cnt = count_open_positions()
    if open_cnt >= max_pos:
        return False, f"최대 포지션 도달 ({open_cnt}/{max_pos}, 국면:{regime_name})"

    total_capital  = config.INITIAL_CAPITAL_PER_COIN * config.MAX_POSITIONS
    loss_threshold = -(total_capital * config.DAILY_LOSS_LIMIT_PCT)
    daily_pnl      = get_daily_pnl()
    if daily_pnl <= loss_threshold:
        return False, (
            f"일일 손실 한도 도달 "
            f"({daily_pnl:+,.0f}원 / 한도 {loss_threshold:,.0f}원)"
        )

    return True, "OK"


# ── 시간 초과 판정 ────────────────────────────────────────────────────────────

def get_candles_held(pos: dict) -> int:
    try:
        entry   = _parse_dt(pos["entry_date"])
        elapsed = (datetime.now() - entry).total_seconds()
        return int(elapsed / (config.CANDLE_MINUTES * 60))
    except (KeyError, ValueError):
        return 0

def is_time_exit(pos: dict) -> bool:
    return get_candles_held(pos) >= config.MAX_HOLD_CANDLES


# ── 포지션 생성 / 갱신 / 청산 ────────────────────────────────────────────────

def open_position(
    coin: str, entry_price: float, stop_loss: float, atr: float
) -> dict:
    capital  = _get_position_capital()
    quantity = capital / entry_price
    pos = {
        "coin":             coin,
        "status":           "open",
        "entry_price":      entry_price,
        "entry_date":       _now_str(),
        "stop_loss":        stop_loss,
        "atr_at_entry":     atr,
        "peak_price":       entry_price,
        "capital":          capital,
        "quantity":         quantity,
        "max_hold_candles": config.MAX_HOLD_CANDLES,
        "pyramid_count":    0,
    }
    with _lock:
        _positions[coin] = pos
    _save_position(pos, coin)
    return pos


def update_peak(coin: str, current_price: float) -> None:
    with _lock:
        pos = _positions.get(coin)
        if pos and pos["status"] == "open" and current_price > pos["peak_price"]:
            pos["peak_price"] = current_price
            snapshot = dict(pos)
        else:
            snapshot = None
    if snapshot:
        _save_position(snapshot, coin)


def close_position(coin: str, exit_price: float, reason: str) -> dict:
    with _lock:
        pos = _positions.get(coin)
        if not pos or pos["status"] != "open":
            raise ValueError(f"열린 포지션 없음: {coin}")

        pnl     = (exit_price - pos["entry_price"]) * pos["quantity"]
        pnl_pct = (exit_price / pos["entry_price"] - 1) * 100

        pos.update({
            "status":       "closed",
            "exit_price":   exit_price,
            "exit_date":    _now_str(),
            "exit_reason":  reason,
            "candles_held": get_candles_held(pos),
            "pnl":          round(pnl, 2),
            "pnl_pct":      round(pnl_pct, 4),
        })
        _positions[coin] = pos
        _history.append(pos)
        snapshot = dict(pos)

    _save_position(snapshot, coin)
    _save_trade(snapshot)
    return pos


def pyramid_position(coin: str, current_price: float, atr: float) -> dict | None:
    """BULL 모드에서 수익 중인 포지션에 추가 진입 (피라미딩)."""
    try:
        from src.market_regime import market_regime, BULL
        if market_regime.regime != BULL:
            return None
    except ImportError:
        return None

    with _lock:
        pos = _positions.get(coin)
    if not pos or pos["status"] != "open":
        return None

    pnl_pct       = (current_price / pos["entry_price"] - 1) * 100
    pyramid_count = pos.get("pyramid_count", 0)

    if pnl_pct < 2.0 or pyramid_count >= 3:
        return None

    add_capital  = pos["capital"] * 0.5
    add_quantity = add_capital / current_price
    new_stop     = current_price - atr * config.ATR_STOP_MULT

    with _lock:
        pos["quantity"]      += add_quantity
        pos["capital"]       += add_capital
        pos["pyramid_count"]  = pyramid_count + 1
        pos["stop_loss"]      = max(pos["stop_loss"], new_stop)
        snapshot = dict(pos)

    _save_position(snapshot, coin)
    return {
        "added_quantity": add_quantity,
        "pyramid_count":  pyramid_count + 1,
        "new_capital":    pos["capital"],
    }

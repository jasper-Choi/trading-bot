"""
가상 포지션 관리 (15분봉 기준) — 인메모리 저장소 + 파일 선택적 fallback.

주요 제약:
  - 동시 최대 포지션: MAX_POSITIONS 개
  - 일일 손실 한도:   총자본의 DAILY_LOSS_LIMIT_PCT 도달 시 신규 진입 중단
"""

import json
import os
import threading
from datetime import datetime

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

POSITIONS_FILE = os.path.join(config.DATA_DIR, "positions.json")
HISTORY_FILE   = os.path.join(config.LOG_DIR,  "trade_history.jsonl")

_DT_FMT = "%Y-%m-%d %H:%M:%S"

# ── 인메모리 저장소 ───────────────────────────────────────────────────────────
_lock: threading.Lock = threading.Lock()
_positions: dict = {}
_history: list = []


def _now_str() -> str:
    return datetime.now().strftime(_DT_FMT)


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s[:19].replace("T", " "), _DT_FMT)


# ── 파일 I/O (선택적) ─────────────────────────────────────────────────────────

def _try_write_positions(positions: dict):
    """파일 쓰기 가능한 환경에서만 positions.json 저장."""
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _try_append_history(record: dict):
    """파일 쓰기 가능한 환경에서만 trade_history.jsonl 추가."""
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── 조회 ──────────────────────────────────────────────────────────────────────

def load_positions() -> dict:
    """현재 포지션 딕셔너리 복사본을 반환합니다."""
    with _lock:
        return dict(_positions)


def load_history() -> list[dict]:
    """전체 거래 이력 리스트 복사본을 반환합니다."""
    with _lock:
        return list(_history)


def get_position(coin: str) -> dict | None:
    """오픈 포지션 반환 (없으면 None)."""
    with _lock:
        pos = _positions.get(coin)
    return pos if pos and pos.get("status") == "open" else None


def get_open_positions() -> list[dict]:
    """현재 오픈된 모든 포지션 리스트."""
    with _lock:
        return [p for p in _positions.values() if p.get("status") == "open"]


def count_open_positions() -> int:
    return len(get_open_positions())


# ── 일일 손익 / 진입 가능 여부 ────────────────────────────────────────────────

def get_daily_pnl() -> float:
    """오늘 청산된 거래들의 누적 손익(원)을 반환합니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        return sum(
            r.get("pnl", 0)
            for r in _history
            if r.get("exit_date", "").startswith(today)
        )


def can_open_new_position() -> tuple[bool, str]:
    """
    신규 포지션 진입 가능 여부를 반환합니다.

    Returns:
        (True, "OK")               진입 가능
        (False, "사유 설명")        진입 불가
    """
    open_cnt = count_open_positions()
    if open_cnt >= config.MAX_POSITIONS:
        return False, f"최대 포지션 도달 ({open_cnt}/{config.MAX_POSITIONS})"

    total_capital  = config.INITIAL_CAPITAL_PER_COIN * config.MAX_POSITIONS
    loss_threshold = -(total_capital * config.DAILY_LOSS_LIMIT_PCT)
    daily_pnl      = get_daily_pnl()

    if daily_pnl <= loss_threshold:
        return False, (
            f"일일 손실 한도 도달 "
            f"({daily_pnl:+,.0f}원 / 한도 {loss_threshold:,.0f}원)"
        )

    return True, "OK"


# ── 시간 초과 판정 ─────────────────────────────────────────────────────────────

def get_candles_held(pos: dict) -> int:
    """진입 후 경과한 15분봉 캔들 수를 반환합니다."""
    try:
        entry   = _parse_dt(pos["entry_date"])
        elapsed = (datetime.now() - entry).total_seconds()
        return int(elapsed / (config.CANDLE_MINUTES * 60))
    except (KeyError, ValueError):
        return 0


def is_time_exit(pos: dict) -> bool:
    """MAX_HOLD_CANDLES(6시간) 초과 여부를 반환합니다."""
    return get_candles_held(pos) >= config.MAX_HOLD_CANDLES


# ── 포지션 생성 / 갱신 / 청산 ────────────────────────────────────────────────

def open_position(
    coin: str, entry_price: float, stop_loss: float, atr: float
) -> dict:
    """가상 매수 포지션을 기록합니다."""
    quantity = config.INITIAL_CAPITAL_PER_COIN / entry_price
    pos = {
        "coin":             coin,
        "status":           "open",
        "entry_price":      entry_price,
        "entry_date":       _now_str(),
        "stop_loss":        stop_loss,
        "atr_at_entry":     atr,
        "peak_price":       entry_price,
        "capital":          config.INITIAL_CAPITAL_PER_COIN,
        "quantity":         quantity,
        "max_hold_candles": config.MAX_HOLD_CANDLES,
    }
    with _lock:
        _positions[coin] = pos
        snapshot = dict(_positions)
    _try_write_positions(snapshot)
    return pos


def update_peak(coin: str, current_price: float):
    """고점 가격을 갱신합니다 (트레일링 스탑 계산용)."""
    with _lock:
        pos = _positions.get(coin)
        if pos and pos["status"] == "open" and current_price > pos["peak_price"]:
            pos["peak_price"] = current_price
            snapshot = dict(_positions)
        else:
            snapshot = None
    if snapshot:
        _try_write_positions(snapshot)


def close_position(coin: str, exit_price: float, reason: str) -> dict:
    """가상 포지션을 청산하고 손익을 기록합니다."""
    with _lock:
        pos = _positions.get(coin)
        if not pos or pos["status"] != "open":
            raise ValueError(f"[15M] 열린 포지션 없음: {coin}")

        pnl     = (exit_price - pos["entry_price"]) * pos["quantity"]
        pnl_pct = (exit_price / pos["entry_price"] - 1) * 100

        pos.update(
            {
                "status":       "closed",
                "exit_price":   exit_price,
                "exit_date":    _now_str(),
                "exit_reason":  reason,
                "candles_held": get_candles_held(pos),
                "pnl":          round(pnl, 2),
                "pnl_pct":      round(pnl_pct, 4),
            }
        )
        _positions[coin] = pos
        _history.append(pos)
        snapshot = dict(_positions)
        record   = dict(pos)

    _try_write_positions(snapshot)
    _try_append_history(record)
    return pos

"""
가상 포지션 관리 (15분봉 기준) — 인메모리 저장소 + 파일 선택적 fallback.

주요 제약:
  - 동시 최대 포지션: 시장 국면별 REGIME_CONFIG["max_positions"]
  - 일일 손실 한도:   -3% → BEAR, -5% → VOLATILE + 신규 차단
  - 연속 손실 3회   → 포지션 크기 50% 축소
  - 연속 손실 5회   → 당일 거래 중단
  - VOLATILE       → 신규 진입 차단
  - 피라미딩        → BULL 모드, +2% 수익 시 최대 3회 추가
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
_DT_FMT        = "%Y-%m-%d %H:%M:%S"

# ── 인메모리 저장소 ───────────────────────────────────────────────────────────
_lock:      threading.Lock = threading.Lock()
_positions: dict           = {}
_history:   list           = []


def _is_railway() -> bool:
    """Railway 환경 여부를 감지합니다."""
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )


def _init_from_files():
    """앱 시작 시 파일에서 인메모리 저장소를 복원합니다 (로컬 전용).

    Railway는 에페머럴 파일시스템이라 파일이 없으므로 건너뜁니다.
    """
    if _is_railway():
        return

    # positions.json → _positions
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _positions.update(data)
    except (OSError, json.JSONDecodeError):
        pass

    # trade_history.jsonl → _history
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            _history.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
    except OSError:
        pass


_init_from_files()


def _now_str() -> str:
    return datetime.now().strftime(_DT_FMT)

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s[:19].replace("T", " "), _DT_FMT)


# ── 파일 I/O ─────────────────────────────────────────────────────────────────

def _try_write_positions(positions: dict):
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

def _try_append_history(record: dict):
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


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

    loss_pct = -daily_pnl / total_capital   # 양수 = 손실 %

    if loss_pct >= 0.05:
        market_regime.set_regime(VOLATILE, f"일간손실{loss_pct*100:.1f}%≥5%")
    elif loss_pct >= 0.03:
        market_regime.set_regime(BEAR, f"일간손실{loss_pct*100:.1f}%≥3%")

    consec = get_consecutive_losses()
    if consec >= 5:
        market_regime.set_regime(VOLATILE, f"연속손실{consec}회≥5")


# ── 포지션 크기 결정 ──────────────────────────────────────────────────────────

def _get_position_capital() -> float:
    """연속 손실에 따라 포지션 크기(원)를 조정합니다."""
    base   = config.INITIAL_CAPITAL_PER_COIN
    consec = get_consecutive_losses()
    return base * 0.5 if consec >= 3 else base


# ── 진입 가능 여부 ────────────────────────────────────────────────────────────

def can_open_new_position() -> tuple[bool, str]:
    """신규 포지션 진입 가능 여부를 반환합니다."""
    # 방어 트리거 먼저 체크
    _check_defense_triggers()

    # 연속 손실 5회 → 당일 거래 중단
    consec = get_consecutive_losses()
    if consec >= 5:
        return False, f"연속 손실 {consec}회 — 당일 거래 중단"

    # 시장 국면 체크
    try:
        from src.market_regime import market_regime
        regime_cfg = market_regime.get_config()
        max_pos    = regime_cfg["max_positions"]
        regime_name = market_regime.regime
    except ImportError:
        max_pos     = config.MAX_POSITIONS
        regime_name = "N/A"

    if max_pos == 0:
        return False, f"시장 국면 {regime_name} — 신규 진입 차단"

    open_cnt = count_open_positions()
    if open_cnt >= max_pos:
        return False, f"최대 포지션 도달 ({open_cnt}/{max_pos}, 국면:{regime_name})"

    # 일일 손실 한도
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
    """가상 매수 포지션을 기록합니다."""
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
        snapshot = dict(_positions)
        record   = dict(pos)

    _try_write_positions(snapshot)
    _try_append_history(record)
    return pos


def pyramid_position(coin: str, current_price: float, atr: float) -> dict | None:
    """
    BULL 모드에서 수익 중인 포지션에 추가 진입 (피라미딩).
    - +2% 수익 시 50% 크기로 추가
    - 최대 3회
    """
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
        snapshot = dict(_positions)

    _try_write_positions(snapshot)
    return {
        "added_quantity": add_quantity,
        "pyramid_count":  pyramid_count + 1,
        "new_capital":    pos["capital"],
    }

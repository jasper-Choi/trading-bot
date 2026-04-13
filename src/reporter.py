"""
포지션 현황 출력 및 로그 기록.

로그 저장: 인메모리 deque (빠른 읽기) + DB 영속화 (재시작 복원).
"""

import logging
import os
from collections import deque
from datetime import date, datetime

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

# ── 인메모리 로그 버퍼 (최대 200줄) ──────────────────────────────────────────
_log_buffer: deque = deque(maxlen=200)


# ── KST 로그 포맷터 ───────────────────────────────────────────────────────────
class _KSTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=config.KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── 파일 핸들러 (옵션) ────────────────────────────────────────────────────────
_logger = logging.getLogger("trading_bot")
_logger.setLevel(logging.INFO)

if not _logger.handlers:
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        _fh = logging.FileHandler(
            os.path.join(config.LOG_DIR, "trading.log"),
            encoding="utf-8",
        )
        _fh.setFormatter(_KSTFormatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(_fh)
    except OSError:
        pass


# ── DB에서 로그 버퍼 복원 (앱 시작 시) ───────────────────────────────────────

def _init_log_from_db() -> None:
    """앱 시작 시 DB에서 최근 200줄을 in-memory 버퍼에 복원합니다."""
    try:
        from src.database import init_db, db_load_logs
        init_db()
        lines = db_load_logs(n=200)
        for line in lines:
            _log_buffer.append(line)
    except Exception as exc:
        print(f"[Reporter] DB 로그 복원 실패: {exc}")


_init_log_from_db()


# ── 로그 함수 ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    """콘솔 + 인메모리 버퍼 + 파일(가능 시) + DB 동시 출력."""
    print(msg)
    _log_buffer.append(msg)
    _logger.info(msg)
    try:
        from src.database import db_insert_log
        db_insert_log(msg)
    except Exception:
        pass


def get_log_lines(n: int = 50) -> list[str]:
    """인메모리 버퍼에서 최근 N줄을 반환합니다."""
    lines = list(_log_buffer)
    return lines[-n:] if n < len(lines) else lines


# ── 현황 출력 ─────────────────────────────────────────────────────────────────

def print_status(positions: dict, current_prices: dict) -> None:
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  모의투자 현황  ({date.today()})")
    print(sep)

    open_positions   = {k: v for k, v in positions.items() if v.get("status") == "open"}
    closed_positions = {k: v for k, v in positions.items() if v.get("status") == "closed"}

    if open_positions:
        print("\n[보유 포지션]")
        for coin, pos in open_positions.items():
            price = current_prices.get(coin, pos["entry_price"])
            pnl   = (price - pos["entry_price"]) * pos["quantity"]
            pnl_pct = (price / pos["entry_price"] - 1) * 100
            print(f"  {coin}")
            print(f"    진입가: {pos['entry_price']:>14,.2f}  ({pos['entry_date']})")
            print(f"    현재가: {price:>14,.2f}  |  고점: {pos['peak_price']:,.2f}")
            print(f"    손절가: {pos['stop_loss']:>14,.2f}")
            print(f"    수량:   {pos['quantity']:>14.4f}")
            sign = "+" if pnl >= 0 else ""
            print(f"    평가손익: {sign}{pnl:,.0f}원  ({sign}{pnl_pct:.2f}%)")
    else:
        print("\n[보유 포지션]  없음")

    if closed_positions:
        print("\n[금일 청산 내역]")
        for coin, pos in closed_positions.items():
            pnl     = pos.get("pnl", 0)
            pnl_pct = pos.get("pnl_pct", 0)
            sign    = "+" if pnl >= 0 else ""
            print(
                f"  {coin}  {pos['entry_date']} → {pos.get('exit_date', '-')}"
                f"  사유: {pos.get('exit_reason', '-')}"
                f"  손익: {sign}{pnl:,.0f}원 ({sign}{pnl_pct:.2f}%)"
            )

    print(f"{sep}\n")


def print_history(records: list[dict]) -> None:
    sep = "=" * 62
    print(f"\n{sep}")
    print("  전체 거래 이력")
    print(sep)

    if not records:
        print("  (기록 없음)")
    else:
        total_pnl = 0.0
        for r in records:
            pnl     = r.get("pnl", 0)
            pnl_pct = r.get("pnl_pct", 0)
            total_pnl += pnl
            sign = "+" if pnl >= 0 else ""
            print(
                f"  {r['coin']:<12}  {r['entry_date']} → {r.get('exit_date', '-')}"
                f"  [{r.get('exit_reason', '-')}]"
                f"  {sign}{pnl:,.0f}원 ({sign}{pnl_pct:.2f}%)"
            )
        sign = "+" if total_pnl >= 0 else ""
        print(f"\n  누적 손익: {sign}{total_pnl:,.0f}원")

    print(f"{sep}\n")

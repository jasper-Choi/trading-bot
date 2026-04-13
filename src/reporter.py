"""
포지션 현황 출력 및 로그 기록 (인메모리 deque + 파일 선택적 fallback).
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
    """한국 시간(KST, UTC+9)으로 로그 시각을 출력하는 포맷터."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=config.KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── 파일 핸들러 (옵션 — 쓰기 가능한 환경에서만 활성화) ──────────────────────────
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
        pass  # Railway 등 읽기 전용 파일시스템에서는 파일 로그 생략


def _is_railway() -> bool:
    """Railway 환경 여부를 감지합니다."""
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )


def _init_log_from_file():
    """앱 시작 시 trading.log 파일에서 로그 버퍼를 복원합니다.

    로컬: 영속 파일에서 복원.
    Railway: 동일 배포 내 재시작 시 파일이 남아 있으면 복원, 없으면 빈 상태로 시작.
    """
    log_file = os.path.join(config.LOG_DIR, "trading.log")
    try:
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines:
                stripped = line.rstrip("\n")
                if stripped:
                    _log_buffer.append(stripped)
    except OSError:
        pass


_init_log_from_file()


def log(msg: str):
    """콘솔 + 인메모리 버퍼 + 파일(가능한 경우) 동시 출력."""
    print(msg)
    _log_buffer.append(msg)
    _logger.info(msg)


def get_log_lines(n: int = 50) -> list[str]:
    """인메모리 버퍼에서 최근 N줄을 반환합니다."""
    lines = list(_log_buffer)
    return lines[-n:] if n < len(lines) else lines


def print_status(positions: dict, current_prices: dict):
    """현재 포지션 현황을 출력합니다."""
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  모의투자 현황  ({date.today()})")
    print(sep)

    open_positions = {k: v for k, v in positions.items() if v.get("status") == "open"}
    closed_positions = {k: v for k, v in positions.items() if v.get("status") == "closed"}

    # --- 보유 포지션 ---
    if open_positions:
        print("\n[보유 포지션]")
        for coin, pos in open_positions.items():
            price = current_prices.get(coin, pos["entry_price"])
            pnl = (price - pos["entry_price"]) * pos["quantity"]
            pnl_pct = (price / pos["entry_price"] - 1) * 100
            print(f"  {coin}")
            print(f"    진입가: {pos['entry_price']:>14,.2f}  ({pos['entry_date']})")
            print(f"    현재가: {price:>14,.2f}  |  고점: {pos['peak_price']:,.2f}")
            print(f"    손절가: {pos['stop_loss']:>14,.2f}")
            print(f"    수량:   {pos['quantity']:>14.4f}")
            pnl_sign = "+" if pnl >= 0 else ""
            print(f"    평가손익: {pnl_sign}{pnl:,.0f}원  ({pnl_sign}{pnl_pct:.2f}%)")
    else:
        print("\n[보유 포지션]  없음")

    # --- 청산 내역 ---
    if closed_positions:
        print("\n[금일 청산 내역]")
        for coin, pos in closed_positions.items():
            pnl = pos.get("pnl", 0)
            pnl_pct = pos.get("pnl_pct", 0)
            pnl_sign = "+" if pnl >= 0 else ""
            print(
                f"  {coin}  {pos['entry_date']} → {pos.get('exit_date', '-')}"
                f"  사유: {pos.get('exit_reason', '-')}"
                f"  손익: {pnl_sign}{pnl:,.0f}원 ({pnl_sign}{pnl_pct:.2f}%)"
            )

    print(f"{sep}\n")


def print_history(records: list[dict]):
    """전체 거래 이력을 출력합니다."""
    sep = "=" * 62
    print(f"\n{sep}")
    print("  전체 거래 이력")
    print(sep)

    if not records:
        print("  (기록 없음)")
    else:
        total_pnl = 0.0
        for r in records:
            pnl = r.get("pnl", 0)
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

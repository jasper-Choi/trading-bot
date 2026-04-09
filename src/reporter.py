"""
포지션 현황 출력 및 로그 파일 기록.
"""

import logging
import os
from datetime import date

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

os.makedirs(config.LOG_DIR, exist_ok=True)

_logger = logging.getLogger("trading_bot")
_logger.setLevel(logging.INFO)

if not _logger.handlers:
    _fh = logging.FileHandler(
        os.path.join(config.LOG_DIR, "trading.log"),
        encoding="utf-8",
    )
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_fh)


def log(msg: str):
    """콘솔 + 로그 파일 동시 출력."""
    print(msg)
    _logger.info(msg)


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

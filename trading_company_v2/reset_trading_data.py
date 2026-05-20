"""
Trading Bot 거래 데이터 초기화 스크립트
========================================
company_state (execution_mode 등 설정)는 보존.
나머지 모든 거래 이력 초기화.

사용법:
  python reset_trading_data.py           # 확인 후 실행
  python reset_trading_data.py --yes     # 확인 없이 즉시 실행
  python reset_trading_data.py --dry-run # 실제 삭제 없이 카운트만 출력
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 없이 실행")
    parser.add_argument("--dry-run", action="store_true", help="실제 삭제 없이 현황만 출력")
    args = parser.parse_args()

    from app.core.state_store import SessionLocal

    # ── 현재 행 수 확인 ──────────────────────────────────────────────────────
    TABLES_TO_CLEAR = [
        "paper_positions",
        "paper_orders",
        "cycle_journal",
        "shadow_signals",
        "live_order_log",
        "closed_positions",
        "positions",
    ]
    PRESERVE_TABLES = ["company_state"]

    print("=" * 55)
    print("  Trading Bot 거래 데이터 초기화")
    print("=" * 55)
    print()

    with SessionLocal() as db:
        total = 0
        for table in TABLES_TO_CLEAR:
            count = db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
            print(f"  삭제 예정  {table:30s}  {count:>6,} rows")
            total += count
        print()
        for table in PRESERVE_TABLES:
            count = db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
            print(f"  보존       {table:30s}  {count:>6,} rows")
        print()
        print(f"  총 삭제 예정: {total:,} rows")
        print()

    if args.dry_run:
        print("[dry-run] 실제 삭제 없이 종료.")
        return

    if not args.yes:
        ans = input("정말 삭제하시겠습니까? (yes/no): ").strip().lower()
        if ans != "yes":
            print("취소됨.")
            sys.exit(0)

    # ── 삭제 실행 ────────────────────────────────────────────────────────────
    with SessionLocal() as db:
        for table in TABLES_TO_CLEAR:
            deleted = db.execute(text(f'DELETE FROM "{table}"')).rowcount
            print(f"  ✓ {table}: {deleted:,} rows 삭제")
        db.commit()

    print()
    print("✅ 초기화 완료. company_state(설정)는 보존됨.")
    print()

    # ── 결과 확인 ────────────────────────────────────────────────────────────
    with SessionLocal() as db:
        for table in TABLES_TO_CLEAR:
            count = db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
            status = "✅" if count == 0 else "⚠️"
            print(f"  {status} {table}: {count} rows")


if __name__ == "__main__":
    main()

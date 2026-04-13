"""
거래 이력 라우터.

logs/trade_history.jsonl 을 읽어 반환합니다.
- 로컬: 파일에서 직접 읽기 (최신 반영)
- Railway: 파일 없음 → 인메모리 fallback
"""

import json
import os

from fastapi import APIRouter, Query

import config
from src.position_manager import load_history
from api.models import TradeOut

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _load_trades() -> list[dict]:
    """trade_history.jsonl 파일 우선 읽기, 없으면 인메모리 fallback."""
    path = os.path.join(config.LOG_DIR, "trade_history.jsonl")
    try:
        if os.path.exists(path):
            records = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            return records
    except OSError:
        pass
    return load_history()


@router.get("", response_model=list[TradeOut], summary="전체 거래 이력")
def get_trades(
    coin: str | None = Query(None, description="코인 필터 (예: KRW-XRP)"),
    limit: int = Query(100, ge=1, le=1000, description="최대 반환 건수"),
):
    """청산 완료된 거래 이력을 최신순으로 반환합니다."""
    records = _load_trades()

    if coin:
        records = [r for r in records if r.get("coin", "").upper() == coin.upper()]

    # 최신순 정렬 (exit_date 기준)
    records.sort(key=lambda r: r.get("exit_date", ""), reverse=True)

    return [TradeOut(**r) for r in records[:limit]]


@router.get("/coins", response_model=list[str], summary="거래된 코인 목록")
def get_traded_coins():
    """거래 이력에 등장한 코인 목록을 반환합니다."""
    records = _load_trades()
    coins = sorted({r["coin"] for r in records if "coin" in r})
    return coins

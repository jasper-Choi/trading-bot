"""
거래 이력 라우터.

logs/trade_history.jsonl 을 읽어 반환합니다.
"""

from fastapi import APIRouter, Query

from src.position_manager import load_history
from api.models import TradeOut

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("", response_model=list[TradeOut], summary="전체 거래 이력")
def get_trades(
    coin: str | None = Query(None, description="코인 필터 (예: KRW-XRP)"),
    limit: int = Query(100, ge=1, le=1000, description="최대 반환 건수"),
):
    """청산 완료된 거래 이력을 최신순으로 반환합니다."""
    records = load_history()

    if coin:
        records = [r for r in records if r.get("coin", "").upper() == coin.upper()]

    # 최신순 정렬 (exit_date 기준)
    records.sort(key=lambda r: r.get("exit_date", ""), reverse=True)

    return [TradeOut(**r) for r in records[:limit]]


@router.get("/coins", response_model=list[str], summary="거래된 코인 목록")
def get_traded_coins():
    """거래 이력에 등장한 코인 목록을 반환합니다."""
    records = load_history()
    coins = sorted({r["coin"] for r in records if "coin" in r})
    return coins

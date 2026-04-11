"""
주식 포지션 / 이력 라우터.
"""

from fastapi import APIRouter, Query
from api.models import StockPositionOut, StockTradeOut

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get("/positions", response_model=list[StockPositionOut], summary="주식 오픈 포지션")
def get_stock_positions():
    """현재 보유 중인 주식 포지션을 반환합니다."""
    from src.stock_strategy import get_stock_positions
    return [StockPositionOut(**p) for p in get_stock_positions()]


@router.get("/history", response_model=list[StockTradeOut], summary="주식 거래 이력")
def get_stock_history(
    limit: int = Query(100, ge=1, le=500, description="최대 반환 건수"),
):
    """주식 청산 거래 이력을 최신순으로 반환합니다."""
    from src.stock_strategy import get_stock_history
    records = get_stock_history()
    records.sort(key=lambda r: r.get("exit_date", ""), reverse=True)
    return [StockTradeOut(**r) for r in records[:limit]]

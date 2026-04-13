"""
거래 이력 라우터.

DB (또는 인메모리 fallback) 에서 코인 거래 이력을 반환합니다.
"""

from fastapi import APIRouter, Query

from src.position_manager import load_history
from api.models import TradeOut

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _load_trades() -> list[dict]:
    """DB에서 거래 이력 로드, 실패 시 인메모리 fallback."""
    try:
        from src.database import db_load_trades
        records = db_load_trades(market="coin", limit=1000)
        if records:
            return records
    except Exception:
        pass
    return load_history()


@router.get("", response_model=list[TradeOut], summary="전체 거래 이력")
def get_trades(
    coin:  str | None = Query(None, description="코인 필터 (예: KRW-XRP)"),
    limit: int        = Query(100, ge=1, le=1000, description="최대 반환 건수"),
):
    """청산 완료된 거래 이력을 최신순으로 반환합니다."""
    records = _load_trades()

    if coin:
        records = [r for r in records if r.get("coin", "").upper() == coin.upper()]

    records.sort(key=lambda r: r.get("exit_date", ""), reverse=True)
    return [TradeOut(**r) for r in records[:limit]]


@router.get("/coins", response_model=list[str], summary="거래된 코인 목록")
def get_traded_coins():
    """거래 이력에 등장한 코인 목록을 반환합니다."""
    records = _load_trades()
    coins   = sorted({r["coin"] for r in records if "coin" in r})
    return coins

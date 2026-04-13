"""
포지션 현황 라우터.

data/positions.json 을 읽어 현재가 및 미실현 손익을 계산해서 반환합니다.
- 로컬: 파일에서 직접 읽기 (최신 반영)
- Railway: 파일 없음 → 인메모리 fallback
"""

import json
import os

from fastapi import APIRouter, HTTPException

import config
from src.position_manager import load_positions
from src.data_fetcher import fetch_current_price
from src.strategy import compute_indicators, effective_stop
from src.data_fetcher import fetch_daily_candles
from api.models import PositionOut

router = APIRouter(prefix="/api/positions", tags=["positions"])


def _load_positions_data() -> dict:
    """positions.json 파일 우선 읽기, 없으면 인메모리 fallback."""
    path = os.path.join(config.DATA_DIR, "positions.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return load_positions()


def _enrich_open(pos: dict, coin: str) -> PositionOut:
    """오픈 포지션에 현재가 / 미실현 손익 / 트레일링 스탑을 추가합니다."""
    try:
        df = fetch_daily_candles(coin, count=config.CANDLE_COUNT)
        df = compute_indicators(df)
        current_price = float(df.iloc[-1]["close"])
        atr = float(df.iloc[-1]["atr"])
    except Exception:
        current_price = pos["entry_price"]
        atr = pos["atr_at_entry"]

    unrealized_pnl = (current_price - pos["entry_price"]) * pos["quantity"]
    unrealized_pnl_pct = (current_price / pos["entry_price"] - 1) * 100
    trail_stop = effective_stop(pos, atr)

    return PositionOut(
        **pos,
        current_price=current_price,
        unrealized_pnl=round(unrealized_pnl, 2),
        unrealized_pnl_pct=round(unrealized_pnl_pct, 4),
        trailing_stop=round(trail_stop, 2),
    )


@router.get("", response_model=list[PositionOut], summary="전체 포지션 조회")
def get_positions():
    """현재 열려 있는 포지션과 오늘 청산된 포지션을 모두 반환합니다."""
    positions = _load_positions_data()
    result = []

    for coin, pos in positions.items():
        if pos.get("status") == "open":
            result.append(_enrich_open(pos, coin))
        else:
            result.append(PositionOut(**pos))

    return result


@router.get("/open", response_model=list[PositionOut], summary="오픈 포지션만 조회")
def get_open_positions():
    """현재 보유 중인 포지션만 반환합니다."""
    positions = _load_positions_data()
    return [
        _enrich_open(pos, coin)
        for coin, pos in positions.items()
        if pos.get("status") == "open"
    ]


@router.get("/{coin}", response_model=PositionOut, summary="코인별 포지션 조회")
def get_position_by_coin(coin: str):
    """특정 코인의 포지션을 반환합니다."""
    coin = coin.upper()
    positions = _load_positions_data()
    pos = positions.get(coin)
    if not pos:
        raise HTTPException(status_code=404, detail=f"포지션 없음: {coin}")
    if pos.get("status") == "open":
        return _enrich_open(pos, coin)
    return PositionOut(**pos)

"""Pydantic 응답 모델 정의."""

from typing import Optional
from pydantic import BaseModel


class PositionOut(BaseModel):
    """보유 또는 청산된 포지션 하나."""
    coin: str
    status: str                         # "open" | "closed"
    entry_price: float
    entry_date: str
    stop_loss: float
    atr_at_entry: float
    peak_price: float
    capital: float
    quantity: float
    # open 포지션 전용
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    trailing_stop: Optional[float] = None
    # closed 포지션 전용
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None


class TradeOut(BaseModel):
    """청산 완료된 거래 하나 (이력용)."""
    coin: str
    entry_price: float
    entry_date: str
    exit_price: float
    exit_date: str
    exit_reason: str
    pnl: float
    pnl_pct: float
    quantity: float
    capital: float


class StatsOut(BaseModel):
    """전략 통계."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float                     # 0.0 ~ 1.0
    total_pnl: float                    # 누적 손익 (원)
    avg_pnl: float                      # 평균 거래 손익
    avg_win: float                      # 평균 수익 거래
    avg_loss: float                     # 평균 손실 거래
    profit_factor: float                # 총수익 / 총손실
    sharpe_ratio: Optional[float]       # 거래 수익률 기반 샤프
    max_drawdown: float                 # 최대 낙폭 (원)
    max_drawdown_pct: float             # 최대 낙폭 (%)


class BotStatusOut(BaseModel):
    """봇 실행 상태."""
    running: bool
    last_run: Optional[str]             # 마지막 실행 시각
    next_run: Optional[str]             # 다음 예정 실행 시각
    top_coins_count: int                # 스크리너 상위 코인 수
    max_positions: int                  # 최대 동시 포지션 수


class BotControlOut(BaseModel):
    """봇 시작/중지 결과."""
    success: bool
    message: str
    status: BotStatusOut


class LogsOut(BaseModel):
    """최근 로그 라인."""
    lines: list[str]
    total_lines: int

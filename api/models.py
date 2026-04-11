"""Pydantic 응답 모델 정의."""

from typing import Optional
from pydantic import BaseModel


class PositionOut(BaseModel):
    """보유 또는 청산된 코인 포지션 하나."""
    coin:         str
    status:       str               # "open" | "closed"
    entry_price:  float
    entry_date:   str
    stop_loss:    float
    atr_at_entry: float
    peak_price:   float
    capital:      float
    quantity:     float
    pyramid_count: Optional[int]   = 0
    # open 포지션 전용
    current_price:       Optional[float] = None
    unrealized_pnl:      Optional[float] = None
    unrealized_pnl_pct:  Optional[float] = None
    trailing_stop:       Optional[float] = None
    # closed 포지션 전용
    exit_price:   Optional[float] = None
    exit_date:    Optional[str]   = None
    exit_reason:  Optional[str]   = None
    pnl:          Optional[float] = None
    pnl_pct:      Optional[float] = None


class TradeOut(BaseModel):
    """청산 완료된 코인 거래 하나."""
    coin:         str
    entry_price:  float
    entry_date:   str
    exit_price:   float
    exit_date:    str
    exit_reason:  str
    pnl:          float
    pnl_pct:      float
    quantity:     float
    capital:      float


class StockPositionOut(BaseModel):
    """보유 중인 주식 포지션 하나."""
    ticker:      str
    name:        str
    status:      str
    entry_price: float
    entry_date:  str
    stop_loss:   float
    peak_price:  float
    capital:     float
    quantity:    float
    half_sold:   bool
    reason:      str
    tp1:         float
    tp2:         float


class StockTradeOut(BaseModel):
    """청산 완료된 주식 거래 하나."""
    ticker:      str
    name:        str
    entry_price: float
    entry_date:  str
    exit_price:  float
    exit_date:   str
    exit_reason: str
    quantity:    float
    capital:     float
    pnl:         float
    pnl_pct:     float


class StatsOut(BaseModel):
    """전략 통계."""
    total_trades:    int
    winning_trades:  int
    losing_trades:   int
    win_rate:        float
    total_pnl:       float
    avg_pnl:         float
    avg_win:         float
    avg_loss:        float
    profit_factor:   float
    sharpe_ratio:    Optional[float]
    max_drawdown:    float
    max_drawdown_pct: float


class BotStatusOut(BaseModel):
    """봇 실행 상태."""
    running:          bool
    last_run:         Optional[str]
    next_run:         Optional[str]
    top_coins_count:  int
    max_positions:    int


class BotControlOut(BaseModel):
    """봇 시작/중지 결과."""
    success: bool
    message: str
    status:  BotStatusOut


class MarketRegimeOut(BaseModel):
    """시장 국면 정보."""
    regime:            str     # BULL | NEUTRAL | BEAR | VOLATILE
    positions_allowed: int
    risk_pct:          float
    pyramiding:        bool
    last_changed:      str


class LogsOut(BaseModel):
    """최근 로그 라인."""
    lines:       list[str]
    total_lines: int

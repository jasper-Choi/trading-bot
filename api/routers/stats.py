"""
통계 라우터 — 승률 / 샤프 비율 / 누적 손익 / 최대 드로우다운.
"""

import math
from fastapi import APIRouter

from src.position_manager import load_history
from api.models import StatsOut

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _compute_stats(records: list[dict]) -> StatsOut:
    """청산 거래 목록으로 통계를 계산합니다."""
    total = len(records)

    if total == 0:
        return StatsOut(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            total_pnl=0.0,
            avg_pnl=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profit_factor=0.0,
            sharpe_ratio=None,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
        )

    pnl_list = [r["pnl"] for r in records]
    pnl_pct_list = [r["pnl_pct"] for r in records]

    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    win_count = len(wins)
    loss_count = len(losses)
    total_pnl = sum(pnl_list)
    avg_pnl = total_pnl / total

    avg_win = sum(wins) / win_count if wins else 0.0
    avg_loss = sum(losses) / loss_count if losses else 0.0

    # 손익비 (Profit Factor): 총수익 / 총손실 절댓값
    total_gain = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_gain / total_loss if total_loss > 0 else float("inf")

    # 샤프 비율 — 거래 수익률(%) 기반 근사
    # 거래 2개 미만이면 계산 불가
    sharpe: float | None = None
    if total >= 2:
        mean_r = sum(pnl_pct_list) / total
        variance = sum((r - mean_r) ** 2 for r in pnl_pct_list) / (total - 1)
        std_r = math.sqrt(variance)
        if std_r > 0:
            # 연간화: 연 252 거래일 가정, 거래 단위이므로 sqrt(252) 로 스케일
            sharpe = round((mean_r / std_r) * math.sqrt(252), 4)

    # 최대 드로우다운 — 누적 손익 기준 피크-투-트로프
    max_dd = 0.0
    max_dd_pct = 0.0
    peak_cumulative = 0.0
    cumulative = 0.0

    # 거래일 순서대로 정렬 (exit_date 오름차순)
    sorted_records = sorted(records, key=lambda r: r.get("exit_date", ""))
    initial_capital = sum(r["capital"] for r in sorted_records[:1]) or 500_000

    cumulative_pct = 0.0
    peak_pct = 0.0

    for r in sorted_records:
        cumulative += r["pnl"]
        cumulative_pct += r["pnl_pct"]
        if cumulative > peak_cumulative:
            peak_cumulative = cumulative
        if cumulative_pct > peak_pct:
            peak_pct = cumulative_pct

        dd = peak_cumulative - cumulative
        dd_pct = peak_pct - cumulative_pct
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return StatsOut(
        total_trades=total,
        winning_trades=win_count,
        losing_trades=loss_count,
        win_rate=round(win_count / total, 4),
        total_pnl=round(total_pnl, 2),
        avg_pnl=round(avg_pnl, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        profit_factor=round(profit_factor, 4),
        sharpe_ratio=sharpe,
        max_drawdown=round(max_dd, 2),
        max_drawdown_pct=round(max_dd_pct, 4),
    )


def _load_all_trades() -> list[dict]:
    """DB에서 전체 거래 이력 로드, 실패 시 인메모리 fallback."""
    try:
        from src.database import db_load_trades
        records = db_load_trades(market="coin", limit=9999)
        if records:
            return records
    except Exception:
        pass
    return load_history()


@router.get("", response_model=StatsOut, summary="전체 전략 통계")
def get_stats():
    """전체 청산 거래를 기반으로 전략 성과 통계를 반환합니다."""
    records = _load_all_trades()
    return _compute_stats(records)


@router.get("/{coin}", response_model=StatsOut, summary="코인별 통계")
def get_stats_by_coin(coin: str):
    """특정 코인의 거래만 필터링하여 통계를 반환합니다."""
    records  = _load_all_trades()
    filtered = [r for r in records if r.get("coin", "").upper() == coin.upper()]
    return _compute_stats(filtered)

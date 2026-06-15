"""정량 리스크/성과 메트릭 라이브러리 (2026-06-15).

자동매매 검증 19개 테스트 중 정량 지표를 한 곳에서 계산.
입력은 거래 단위 수익률 리스트(pnl_pct, % 단위) 또는 (pnl_pct, notional_pct) 페어.
표준 라이브러리만 사용 — numpy 불필요(의존성 최소화), VM/백테스트 양쪽에서 import 가능.

용어:
  pnls       : 거래별 수익률(%) 리스트. 예: [1.5, -0.8, 2.3, ...]
  weighted   : 자본 가중 수익률 — pnl_pct × (notional_pct / 평균 notional)
  equity     : 누적 자본 곡선 (시작 1.0 기준 복리)

핵심 메트릭:
  sharpe / sortino / calmar / recovery_factor / profit_factor
  max_drawdown / monte_carlo / risk_of_ruin / kelly_fraction
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float], ddof: int = 1) -> float:
    n = len(xs)
    if n <= ddof:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def equity_curve(pnls: list[float], start: float = 1.0) -> list[float]:
    """거래별 수익률(%)을 복리로 누적한 자본 곡선."""
    eq = [start]
    for p in pnls:
        eq.append(eq[-1] * (1.0 + p / 100.0))
    return eq


def max_drawdown_pct(equity: list[float]) -> float:
    """자본 곡선의 최대 낙폭(%) — 음수 반환 (예: -8.3)."""
    if len(equity) < 2:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100.0
            if dd < mdd:
                mdd = dd
    return round(mdd, 2)


def sharpe(pnls: list[float], periods_per_year: int = 252) -> float:
    """거래 단위 샤프(연율화). 무위험수익률 0 가정."""
    if len(pnls) < 2:
        return 0.0
    sd = _std(pnls)
    if sd <= 0:
        return 0.0
    return round(_mean(pnls) / sd * math.sqrt(periods_per_year), 2)


def sortino(pnls: list[float], periods_per_year: int = 252, target: float = 0.0) -> float:
    """소르티노 — 하방 변동성만으로 위험 조정 (기관 선호).

    상승 변동성은 위험이 아니라는 관점. 하방편차(downside deviation)만 분모.
    """
    if len(pnls) < 2:
        return 0.0
    downside = [min(p - target, 0.0) ** 2 for p in pnls]
    dd = math.sqrt(sum(downside) / len(pnls))
    if dd <= 0:
        return 99.99 if _mean(pnls) > 0 else 0.0
    return round((_mean(pnls) - target) / dd * math.sqrt(periods_per_year), 2)


def profit_factor(pnls: list[float]) -> float:
    """수익팩터 = 총이익 / |총손실| (1.5+ 양호, 2+ 우수)."""
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    if gross_loss <= 0:
        return 99.99 if gross_win > 0 else 0.0
    return round(gross_win / gross_loss, 2)


def calmar(pnls: list[float], periods_per_year: int = 252) -> float:
    """Calmar = 연율화 수익 / |최대 낙폭|.

    거래 단위라 연율화는 근사 — 평균수익 × 연간 거래수 추정.
    """
    if len(pnls) < 2:
        return 0.0
    eq = equity_curve(pnls)
    mdd = abs(max_drawdown_pct(eq))
    if mdd <= 0:
        return 99.99 if _mean(pnls) > 0 else 0.0
    ann_return = _mean(pnls) * periods_per_year
    return round(ann_return / mdd, 2)


def recovery_factor(pnls: list[float]) -> float:
    """Recovery Factor = 총 순수익(%) / |최대 낙폭(%)|.

    손실을 얼마나 빨리/효율적으로 회복하는가. 높을수록 견고.
    """
    eq = equity_curve(pnls)
    net_return = (eq[-1] / eq[0] - 1.0) * 100.0
    mdd = abs(max_drawdown_pct(eq))
    if mdd <= 0:
        return 99.99 if net_return > 0 else 0.0
    return round(net_return / mdd, 2)


def kelly_fraction(pnls: list[float]) -> dict:
    """켈리 공식 — 최적 베팅 비율.

    f* = W - (1 - W) / R
      W = 승률, R = 평균이익 / |평균손실| (페이오프 비율)
    f* > 0 이면 양의 기대값. 실전은 half-kelly(0.5×) 권장 — 추정오차/분산 완충.

    반환: {full_kelly, half_kelly, win_rate, payoff_ratio, edge}
    """
    n = len(pnls)
    if n < 5:
        return {"full_kelly": 0.0, "half_kelly": 0.0, "win_rate": 0.0,
                "payoff_ratio": 0.0, "edge": 0.0, "samples": n, "note": "표본 부족(<5)"}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    w = len(wins) / n
    avg_win = _mean(wins) if wins else 0.0
    avg_loss = abs(_mean(losses)) if losses else 0.0
    if avg_loss <= 0:
        # 손실이 없으면 켈리 정의 불가 — 보수적으로 0.25 캡
        return {"full_kelly": 0.25, "half_kelly": 0.25, "win_rate": round(w, 3),
                "payoff_ratio": 99.99, "edge": round(avg_win * w, 3), "samples": n,
                "note": "무손실 — 보수적 캡 적용"}
    r = avg_win / avg_loss
    f = w - (1.0 - w) / r
    f = max(-1.0, min(1.0, f))  # 클램프
    return {
        "full_kelly": round(f, 3),
        "half_kelly": round(f * 0.5, 3),
        "win_rate": round(w, 3),
        "payoff_ratio": round(r, 2),
        "edge": round(w * avg_win - (1 - w) * avg_loss, 3),  # 기대 수익률(%)/거래
        "samples": n,
        "note": "양의 기대값" if f > 0 else "음의 기대값 — 진입 자제",
    }


@dataclass
class MonteCarloResult:
    runs: int
    ruin_threshold_pct: float
    risk_of_ruin: float          # 파산확률 (0~1)
    median_return_pct: float
    p5_return_pct: float         # 하위 5% (나쁜 시나리오)
    p95_return_pct: float        # 상위 5%
    median_max_dd_pct: float
    worst_max_dd_pct: float      # 최악 낙폭 (p95 of |dd|)
    prob_profit: float           # 최종 수익 > 0 확률
    raw: dict = field(default_factory=dict)


def monte_carlo(
    pnls: list[float],
    runs: int = 2000,
    ruin_threshold_pct: float = 25.0,
    seed: int | None = 42,
) -> MonteCarloResult:
    """몬테카를로 시뮬레이션 + 파산확률.

    거래 순서를 무작위로 섞어(부트스트랩, 복원추출) runs회 자본곡선을 생성.
    "수익이 거래 순서 운에 의존하는가"를 검증하고, 최악의 자본 경로를 정량화.

    파산(ruin): 자본이 시작 대비 -ruin_threshold_pct% 아래로 떨어지는 경로.
    risk_of_ruin = 그런 경로 비율.
    """
    if len(pnls) < 5:
        return MonteCarloResult(runs=0, ruin_threshold_pct=ruin_threshold_pct,
                                risk_of_ruin=0.0, median_return_pct=0.0,
                                p5_return_pct=0.0, p95_return_pct=0.0,
                                median_max_dd_pct=0.0, worst_max_dd_pct=0.0,
                                prob_profit=0.0, raw={"note": "표본 부족(<5)"})
    rng = random.Random(seed)
    n = len(pnls)
    ruin_level = 1.0 - ruin_threshold_pct / 100.0
    finals: list[float] = []
    mdds: list[float] = []
    ruins = 0
    for _ in range(runs):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]  # 복원추출 부트스트랩
        eq = equity_curve(sample)
        finals.append((eq[-1] / eq[0] - 1.0) * 100.0)
        mdds.append(abs(max_drawdown_pct(eq)))
        if min(eq) <= ruin_level:
            ruins += 1

    def _pct(xs: list[float], q: float) -> float:
        s = sorted(xs)
        i = min(len(s) - 1, max(0, int(q * len(s))))
        return round(s[i], 2)

    return MonteCarloResult(
        runs=runs,
        ruin_threshold_pct=ruin_threshold_pct,
        risk_of_ruin=round(ruins / runs, 4),
        median_return_pct=_pct(finals, 0.50),
        p5_return_pct=_pct(finals, 0.05),
        p95_return_pct=_pct(finals, 0.95),
        median_max_dd_pct=_pct(mdds, 0.50),
        worst_max_dd_pct=_pct(mdds, 0.95),
        prob_profit=round(sum(1 for f in finals if f > 0) / runs, 3),
        raw={"final_mean": round(_mean(finals), 2)},
    )


def capacity_estimate(
    avg_daily_value_krw: float,
    max_participation: float = 0.01,
    position_pct_of_seed: float = 0.15,
) -> dict:
    """용량 분석 — 전략이 수용 가능한 최대 시드 추정.

    시장충격(market impact)을 낮게 유지하려면 한 포지션이 종목 일평균
    거래대금의 일정 비율(max_participation, 기본 1%)을 넘지 않아야 함.
    그 한도에서 역산해 전체 운용 시드 상한을 추정.

    예: 일거래대금 100억, participation 1% → 포지션당 1억,
        포지션이 시드의 15% → 시드 상한 ≈ 6.7억.
    이를 넘기면 슬리피지가 수익을 잠식(용량 초과).
    """
    if avg_daily_value_krw <= 0:
        return {"max_position_krw": 0, "max_seed_krw": 0, "note": "거래대금 데이터 없음"}
    per_position = avg_daily_value_krw * max_participation
    max_seed = per_position / position_pct_of_seed if position_pct_of_seed > 0 else 0
    return {
        "max_position_krw": round(per_position),
        "max_seed_krw": round(max_seed),
        "max_participation": max_participation,
        "position_pct_of_seed": position_pct_of_seed,
        "note": "이 시드 초과 시 시장충격(슬리피지)이 수익 잠식",
    }


def factor_beta(strategy_returns: list[float], market_returns: list[float]) -> dict:
    """팩터 익스포저 — 전략 수익의 시장 팩터 노출도 (단순 OLS).

    beta = cov(strategy, market) / var(market)
    alpha = mean(strategy) - beta * mean(market)  (시장 중립 초과수익)
    r2 = 설명력 (전략 수익 중 시장으로 설명되는 비율)

    beta≈0 → 시장 중립(독립 알파), beta≈1 → 시장 추종(지수 베팅과 유사).
    기관은 낮은 beta + 양의 alpha를 선호(분산 효과 + 순수 엣지).
    """
    n = min(len(strategy_returns), len(market_returns))
    if n < 10:
        return {"beta": None, "alpha": None, "r2": None, "samples": n, "note": "표본 부족(<10)"}
    s = strategy_returns[:n]
    m = market_returns[:n]
    ms, mm = _mean(s), _mean(m)
    var_m = sum((x - mm) ** 2 for x in m) / n
    if var_m <= 0:
        return {"beta": None, "alpha": None, "r2": None, "samples": n, "note": "시장 분산 0"}
    cov = sum((s[i] - ms) * (m[i] - mm) for i in range(n)) / n
    beta = cov / var_m
    alpha = ms - beta * mm
    # R^2
    ss_tot = sum((x - ms) ** 2 for x in s)
    ss_res = sum((s[i] - (alpha + beta * m[i])) ** 2 for i in range(n))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        "beta": round(beta, 3),
        "alpha": round(alpha, 4),
        "r2": round(r2, 3),
        "samples": n,
        "note": ("시장 중립(독립 알파)" if abs(beta) < 0.3
                 else "시장 추종 성향" if beta > 0.7 else "부분 시장 노출"),
    }


def full_report(pnls: list[float], label: str = "") -> dict:
    """전체 메트릭 한 번에 — 리포트/대시보드용."""
    if not pnls:
        return {"label": label, "trades": 0, "note": "거래 없음"}
    eq = equity_curve(pnls)
    mc = monte_carlo(pnls)
    k = kelly_fraction(pnls)
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "label": label,
        "trades": n,
        "win_rate": round(wins / n, 3),
        "net_return_pct": round((eq[-1] / eq[0] - 1.0) * 100.0, 2),
        "sharpe": sharpe(pnls),
        "sortino": sortino(pnls),
        "calmar": calmar(pnls),
        "profit_factor": profit_factor(pnls),
        "recovery_factor": recovery_factor(pnls),
        "max_drawdown_pct": max_drawdown_pct(eq),
        "kelly": k,
        "monte_carlo": {
            "risk_of_ruin_25pct": mc.risk_of_ruin,
            "median_return_pct": mc.median_return_pct,
            "p5_return_pct": mc.p5_return_pct,
            "worst_max_dd_pct": mc.worst_max_dd_pct,
            "prob_profit": mc.prob_profit,
        },
    }

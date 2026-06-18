"""19개 자동매매 검증 테스트 통합 리포트 (2026-06-15).

실거래 DB(paper_positions)의 청산 트레이드에 quant_metrics 전체를 적용해
정량 지표를 한 번에 산출. VM에서 실행:

    cd ~/trading-bot/trading_company_v2 && .venv/bin/python tools/quant_report.py

전략별 + 전체(korea) 메트릭, 켈리 권고, 몬테카를로/파산확률, 용량/팩터까지.
kis_hold/manual 청산은 봇 전략 성과가 아니므로 제외.
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import quant_metrics as qm  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trading_company_v2.db")


def _load_trades(desk: str = "korea") -> list[dict]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT symbol, entry_profile, strategy_id, pnl_pct, closed_reason,
                  opened_at, closed_at
           FROM paper_positions
           WHERE status='closed' AND desk=?
             AND entry_profile NOT LIKE '%kis_hold%'
             AND entry_profile NOT LIKE '%manual%'
             AND closed_reason NOT LIKE '%manual%'
             AND closed_reason NOT LIKE 'shadow_%'
           ORDER BY closed_at""",
        (desk,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def main() -> None:
    trades = _load_trades("korea")
    pnls = [float(t["pnl_pct"] or 0) for t in trades]
    print("=" * 60)
    print(" 19개 자동매매 검증 테스트 — 실거래 리포트 (korea)")
    print("=" * 60)
    print(f"청산 트레이드: {len(pnls)}건  (kis_hold/manual 제외)")
    if len(pnls) < 5:
        print("표본 부족(<5) — 더 많은 거래 필요. 메트릭 신뢰도 낮음.")
        if not pnls:
            return

    rep = qm.full_report(pnls, "korea 전체")
    print("\n[ 위험조정 성과 ]")
    print(f"  11. 샤프(Sharpe)        : {_fmt(rep['sharpe'])}")
    print(f"  12. 소르티노(Sortino)    : {_fmt(rep['sortino'])}  (하방위험 기준)")
    print(f"  13. Calmar             : {_fmt(rep['calmar'])}  (연수익/MDD)")
    print(f"  14. 수익팩터(PF)        : {_fmt(rep['profit_factor'])}  (1.5+양호 2+우수)")
    print(f"  15. Recovery Factor     : {_fmt(rep['recovery_factor'])}  (순수익/MDD)")
    print(f"  10. 최대낙폭(MDD)        : {_fmt(rep['max_drawdown_pct'])}%")
    print(f"      승률 / 순수익        : {rep['win_rate']*100:.1f}% / {_fmt(rep['net_return_pct'])}%")

    k = rep["kelly"]
    print("\n[ 9. 켈리공식 ]")
    print(f"  full-kelly={_fmt(k['full_kelly'])}  half-kelly={_fmt(k['half_kelly'])}  "
          f"payoff={_fmt(k['payoff_ratio'])}  edge={_fmt(k['edge'])}%/거래")
    print(f"  → {k['note']}")

    mc = rep["monte_carlo"]
    print("\n[ 5/16. 몬테카를로 + 파산확률 (2000회 부트스트랩) ]")
    print(f"  파산확률(-25%)         : {mc['risk_of_ruin_25pct']*100:.2f}%")
    print(f"  수익확률               : {mc['prob_profit']*100:.1f}%")
    print(f"  중앙값 수익            : {_fmt(mc['median_return_pct'])}%")
    print(f"  하위5% 시나리오        : {_fmt(mc['p5_return_pct'])}%")
    print(f"  최악 낙폭(p95)         : {_fmt(mc['worst_max_dd_pct'])}%")

    # 전략별 분해 (17번 regime/전략 분석에 준함)
    print("\n[ 전략별 분해 (n>=5만) ]")
    by_strat: dict[str, list[float]] = {}
    for t in trades:
        ep = str(t.get("entry_profile") or "?")
        by_strat.setdefault(ep, []).append(float(t["pnl_pct"] or 0))
    print(f"  {'전략':<22} {'n':>3} {'승률':>5} {'순익%':>7} {'PF':>5} {'half-K':>7}")
    for ep, ps in sorted(by_strat.items(), key=lambda kv: -sum(kv[1])):
        if len(ps) < 5:
            continue
        r = qm.full_report(ps, ep)
        print(f"  {ep:<22} {r['trades']:>3} {r['win_rate']*100:>4.0f}% "
              f"{_fmt(r['net_return_pct']):>7} {_fmt(r['profit_factor']):>5} "
              f"{_fmt(r['kelly']['half_kelly']):>7}")

    # 용량 분석 (8번) — 한국 중형주 일거래대금 ~100억 가정 예시
    print("\n[ 8. 용량 분석 (예시: 일거래대금 100억 종목) ]")
    cap = qm.capacity_estimate(10_000_000_000, max_participation=0.01, position_pct_of_seed=0.15)
    print(f"  포지션당 상한 ≈ {cap['max_position_krw']/1e8:.2f}억  "
          f"→ 시드 상한 ≈ {cap['max_seed_krw']/1e8:.2f}억")
    print(f"  {cap['note']}")

    print("\n" + "=" * 60)
    print(" 미적용: 19.팩터익스포저(코스피 일간수익 회귀 — 데이터 누적 후)")
    print("=" * 60)


if __name__ == "__main__":
    main()

"""
한국 주식 전략 v2 — 갭 + 오프닝 드라이브 (일봉 기반)
목적: 오토트레이딩 수익 극대화를 위한 전략 검증

전략 정의:
  진입 조건 (AND):
    1. 갭 상승: 시가 > 전일 종가 × 1.02 (최소 +2% 갭업)
    2. 거래량 급증: 당일 거래량 > 20일 평균의 3배
    3. 상승 추세: 5일선 > 20일선 (역추세 진입 금지)
    4. 드라이브 확인: 당일 고가 > 시가 × 1.01 (장 초반 추가 상승 확인)
    5. 시총 필터: 코스닥/코스피 상위 유동성 종목 한정

  청산 조건:
    목표 1: +3.0% → 포지션 50% 청산
    목표 2: +5.0% → 나머지 청산
    손절:   -1.5% (빠르고 기계적으로)
    시간 강제 청산: 당일 14:00 이후 (오후 반전 회피)

  비용:
    수수료: 0.015% × 2 = 0.03% (증권사 실제 수준)
    슬리피지: 0.05% × 2 = 0.10%
    총 왕복 비용: 0.13%

  통과 기준:
    승률 ≥ 45%
    손익비 ≥ 2.0
    샤프비율 ≥ 1.0
    최대 낙폭 ≥ -15%
    총수익률 > 0%
    거래 수 ≥ 20

참고:
  pykrx로 일봉 데이터 수집. 일봉이므로 장중 시뮬레이션은
  당일 OHLCV로 근사. 실제 장중 데이터가 있으면 더 정확.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import sys
from dataclasses import dataclass
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from pykrx import stock as krx
    PYKRX_OK = True
except ImportError:
    PYKRX_OK = False
    print("[안내] pykrx 없음. pip install pykrx 후 재실행하세요.")
    print("      현재는 시뮬레이션 데이터로 진행합니다.\n")


# ─────────────────────────────────────────────────────────────────
#  설정값
# ─────────────────────────────────────────────────────────────────
CONFIG = {
    "capital":          5_000_000,

    # 전략 파라미터
    "gap_min_pct":      0.020,          # 갭업 최소 +2%
    "gap_max_pct":      0.150,          # 갭업 최대 +15% (상한선 초과 = 추격 금지)
    "vol_mult":         3.0,            # 거래량 급증 배수
    "vol_ma_period":    20,
    "drive_min_pct":    0.010,          # 오프닝 드라이브 확인 최소 +1%
    "trend_fast":       5,              # 단기 이평
    "trend_slow":       20,             # 장기 이평

    # 청산
    "tp1_pct":          0.030,          # 1차 익절 +3%
    "tp2_pct":          0.050,          # 2차 익절 +5%
    "tp1_ratio":        0.50,           # 1차 익절 비율 50%
    "stop_pct":         0.015,          # 손절 -1.5%

    # 비용
    "commission":       0.00015,        # 수수료 0.015%
    "slippage":         0.0005,         # 슬리피지 0.05%

    # 백테스트
    "backtest_days":    180,            # 6개월

    # 테스트 유니버스 (코스닥 유동성 상위 + 코스피 주요 종목)
    # 실전에서는 동적으로 스크리닝 — 여기서는 대표 종목으로 검증
    "tickers": {
        # 코스닥 성장주
        "에코프로비엠":     "247540",
        "알테오젠":         "196170",
        "HLB":              "028300",
        "리가켐바이오":     "141080",
        "삼천당제약":       "000250",
        # 코스피 주요 대형
        "삼성전자":         "005930",
        "SK하이닉스":       "000660",
        "현대차":           "005380",
        "카카오":           "035720",
        "POSCO홀딩스":      "005490",
    },
}

ROUND_TRIP_COST = (CONFIG["commission"] + CONFIG["slippage"]) * 2


# ─────────────────────────────────────────────────────────────────
#  데이터 수집
# ─────────────────────────────────────────────────────────────────
def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    if not PYKRX_OK:
        return _sample_data(ticker, start, end)
    try:
        df = krx.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        df.columns = ["date", "open", "high", "low", "close", "volume", "value"]
        df["date"] = pd.to_datetime(df["date"])
        df["ticker"] = ticker
        return df[["date", "open", "high", "low", "close", "volume", "ticker"]]
    except Exception as e:
        print(f"  [pykrx 오류] {ticker}: {e}")
        return _sample_data(ticker, start, end)


def _sample_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    """pykrx 없을 때 시뮬레이션용 샘플 데이터"""
    np.random.seed(abs(hash(ticker)) % 10000)
    dates = pd.bdate_range(start=start, end=end)
    n = len(dates)
    # 종목 특성 반영 (바이오 = 변동성 크게)
    vol_factor = 0.03 if any(k in ticker for k in ["바이오", "제약", "HLB"]) else 0.02
    base = np.random.choice([20000, 50000, 100000, 300000])
    ret = np.random.normal(0.0003, vol_factor, n)
    close = base * np.cumprod(1 + ret)
    # 갭 이벤트 삽입 (약 5% 확률)
    gap_days = np.random.choice(n, size=max(1, n // 20), replace=False)
    open_arr = close * (1 + np.random.normal(0, 0.008, n))
    for d in gap_days:
        open_arr[d] = close[max(0, d-1)] * (1 + np.random.uniform(0.02, 0.08))
    high = np.maximum(open_arr, close) * (1 + np.abs(np.random.normal(0, 0.01, n)))
    low = np.minimum(open_arr, close) * (1 - np.abs(np.random.normal(0, 0.01, n)))
    vol = np.random.lognormal(14, 0.8, n).astype(int)
    for d in gap_days:
        vol[d] = vol[max(0, d-1)] * np.random.uniform(3, 7)

    return pd.DataFrame({
        "date":   dates, "ticker": ticker,
        "open":   open_arr.astype(int), "high": high.astype(int),
        "low":    low.astype(int),      "close": close.astype(int),
        "volume": vol,
    })


# ─────────────────────────────────────────────────────────────────
#  지표 계산
# ─────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy().sort_values("date").reset_index(drop=True)
    df["prev_close"]  = df["close"].shift(1)
    df["prev_high"]   = df["high"].shift(1)
    df["gap_pct"]     = (df["open"] - df["prev_close"]) / df["prev_close"]
    df["vol_ma"]      = df["volume"].rolling(cfg["vol_ma_period"]).mean()
    df["vol_ratio"]   = df["volume"] / df["vol_ma"]
    df["ma_fast"]     = df["close"].rolling(cfg["trend_fast"]).mean()
    df["ma_slow"]     = df["close"].rolling(cfg["trend_slow"]).mean()
    df["trend_ok"]    = df["ma_fast"] > df["ma_slow"]
    # 오프닝 드라이브 확인 (고가가 시가 대비 일정 % 이상 상승)
    df["drive_pct"]   = (df["high"] - df["open"]) / df["open"]
    return df.dropna().reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────
#  진입 조건
# ─────────────────────────────────────────────────────────────────
def check_entry(row: pd.Series, cfg: dict) -> bool:
    # 1. 갭업 범위
    if not (cfg["gap_min_pct"] <= row["gap_pct"] <= cfg["gap_max_pct"]):
        return False
    # 2. 거래량 급증
    if row["vol_ratio"] < cfg["vol_mult"]:
        return False
    # 3. 상승 추세
    if not row["trend_ok"]:
        return False
    # 4. 오프닝 드라이브 확인
    if row["drive_pct"] < cfg["drive_min_pct"]:
        return False
    return True


# ─────────────────────────────────────────────────────────────────
#  트레이드 시뮬레이션 (일봉 기반)
# ─────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    ticker:       str
    name:         str
    date:         str
    entry_price:  float
    exit_price:   float
    size_krw:     float
    pnl_pct:      float
    pnl_krw:      float
    exit_reason:  str
    gap_pct_entry: float


def simulate_trades(ticker: str, name: str, df: pd.DataFrame, cfg: dict) -> list[Trade]:
    """
    일봉 기반 시뮬레이션.
    진입: 갭업 시가 (슬리피지 적용)
    청산: 당일 OHLCV로 근사
      - 손절 → low가 손절선 이하 시 손절가
      - 1차 익절 → high가 +3% 이상 시
      - 2차 익절 → high가 +5% 이상 시
      - 시간 청산 → 종가 (오후 반전 가정)
    """
    trades = []

    for _, row in df.iterrows():
        if not check_entry(row, cfg):
            continue

        entry = row["open"] * (1 + cfg["slippage"])
        stop  = entry * (1 - cfg["stop_pct"])
        tp1   = entry * (1 + cfg["tp1_pct"])
        tp2   = entry * (1 + cfg["tp2_pct"])

        # 포지션 사이즈 (리스크 기반)
        risk_krw = cfg["capital"] * 0.02   # 1회 자본의 2% 리스크
        size_krw = min(risk_krw / cfg["stop_pct"],
                       cfg["capital"] * 0.25)     # 최대 25%

        low, high, close = row["low"], row["high"], row["close"]

        # 청산 우선순위: 손절 > 2차 익절 > 1차 익절 > 시간 청산
        if low <= stop:
            # 손절 (시초가 갭다운 포함)
            exit_p  = max(stop, row["open"]) * (1 - cfg["slippage"])
            reason  = "손절"
            _add(trades, ticker, name, row, entry, exit_p, size_krw, reason, cfg)

        elif high >= tp2:
            # 1차 익절 (50%) + 2차 익절 (50%)
            exit_p1 = tp1 * (1 - cfg["slippage"])
            pnl1    = _pnl(entry, exit_p1, size_krw * cfg["tp1_ratio"], cfg)
            _add_split(trades, ticker, name, row, entry, exit_p1,
                       size_krw * cfg["tp1_ratio"], "1차익절", cfg)

            exit_p2 = tp2 * (1 - cfg["slippage"])
            _add_split(trades, ticker, name, row, entry, exit_p2,
                       size_krw * (1 - cfg["tp1_ratio"]), "2차익절", cfg)

        elif high >= tp1:
            # 1차 익절 (50%) + 종가 청산 (50%)
            exit_p1 = tp1 * (1 - cfg["slippage"])
            _add_split(trades, ticker, name, row, entry, exit_p1,
                       size_krw * cfg["tp1_ratio"], "1차익절", cfg)

            exit_p2 = close * (1 - cfg["slippage"])
            _add_split(trades, ticker, name, row, entry, exit_p2,
                       size_krw * (1 - cfg["tp1_ratio"]), "시간청산", cfg)

        else:
            # 시간 청산 (종가)
            exit_p = close * (1 - cfg["slippage"])
            reason = "시간청산"
            _add(trades, ticker, name, row, entry, exit_p, size_krw, reason, cfg)

    return trades


def _pnl(entry, exit_p, size, cfg):
    raw = (exit_p - entry) / entry
    return (raw - ROUND_TRIP_COST) * size


def _add(trades, ticker, name, row, entry, exit_p, size, reason, cfg):
    raw_pct = (exit_p - entry) / entry
    pnl_pct = raw_pct - ROUND_TRIP_COST
    trades.append(Trade(
        ticker=ticker, name=name,
        date=str(row["date"].date()),
        entry_price=round(entry), exit_price=round(exit_p),
        size_krw=round(size),
        pnl_pct=round(pnl_pct * 100, 3),
        pnl_krw=round(size * pnl_pct),
        exit_reason=reason,
        gap_pct_entry=round(row["gap_pct"] * 100, 2),
    ))


def _add_split(trades, ticker, name, row, entry, exit_p, size, reason, cfg):
    _add(trades, ticker, name, row, entry, exit_p, size, reason, cfg)


# ─────────────────────────────────────────────────────────────────
#  성과 분석
# ─────────────────────────────────────────────────────────────────
def analyze(trades: list[Trade], capital: float) -> dict:
    if len(trades) < 5:
        return {"error": f"거래 {len(trades)}회 — 분석 불가"}

    df = pd.DataFrame([t.__dict__ for t in trades])
    wins   = df[df["pnl_krw"] > 0]
    losses = df[df["pnl_krw"] <= 0]

    wr  = len(wins) / len(df) * 100
    aw  = wins["pnl_pct"].mean()   if len(wins)   > 0 else 0
    al  = losses["pnl_pct"].mean() if len(losses) > 0 else 0
    rr  = abs(aw / al)             if al != 0      else 0
    pf  = (wins["pnl_krw"].sum() / abs(losses["pnl_krw"].sum())
           if len(losses) > 0 and losses["pnl_krw"].sum() != 0 else 99)

    equity = capital + df["pnl_krw"].cumsum()
    max_dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

    r = df["pnl_pct"] / 100
    sharpe = (r.mean() - 0.03 / 252) / r.std() * np.sqrt(252) if r.std() > 0 else 0

    by_reason = {}
    for reason in df["exit_reason"].unique():
        sub = df[df["exit_reason"] == reason]
        by_reason[reason] = {
            "횟수":     len(sub),
            "승률":     round(len(sub[sub["pnl_krw"] > 0]) / len(sub) * 100, 1),
            "평균수익": round(sub["pnl_pct"].mean(), 2),
            "총손익":   round(sub["pnl_krw"].sum()),
        }

    # 갭 크기별 성과
    df["gap_band"] = pd.cut(df["gap_pct_entry"],
                            bins=[0, 3, 5, 8, 15, 100],
                            labels=["2-3%", "3-5%", "5-8%", "8-15%", "15%+"])
    by_gap = {}
    for band, grp in df.groupby("gap_band", observed=True):
        if len(grp) > 0:
            by_gap[str(band)] = {
                "횟수":   len(grp),
                "승률":   round(len(grp[grp["pnl_krw"] > 0]) / len(grp) * 100, 1),
                "평균":   round(grp["pnl_pct"].mean(), 2),
            }

    return {
        "총거래수":   len(df),
        "승률(%)":    round(wr, 1),
        "손익비":     round(rr, 2),
        "샤프비율":   round(sharpe, 2),
        "최대DD(%)":  round(max_dd, 2),
        "프로핏팩터": round(pf, 2),
        "평균수익(%)": round(aw, 2),
        "평균손실(%)": round(al, 2),
        "총손익(원)": round(df["pnl_krw"].sum()),
        "총수익률(%)": round(df["pnl_krw"].sum() / capital * 100, 2),
        "청산사유별": by_reason,
        "갭대역별":   by_gap,
    }


def check_pass(r: dict) -> bool:
    return (
        r.get("승률(%)",     0)   >= 45
        and r.get("손익비",  0)   >= 2.0
        and r.get("샤프비율", 0)  >= 1.0
        and r.get("최대DD(%)", -999) >= -15
        and r.get("총수익률(%)", 0) > 0
        and r.get("총거래수", 0)  >= 20
    )


def print_report(name: str, ticker: str, r: dict):
    if "error" in r:
        print(f"\n  [{name}({ticker})]: {r['error']}")
        return
    ok = check_pass(r)
    tag = "✅ 통과" if ok else "❌ 미달"
    print(f"\n{'='*55}")
    print(f"  {name}({ticker})  {tag}")
    print(f"{'='*55}")
    print(f"  총 거래수    : {r['총거래수']}회")
    print(f"  승률         : {r['승률(%)']}%     (기준 ≥45%)")
    print(f"  손익비       : {r['손익비']}      (기준 ≥2.0)")
    print(f"  샤프비율     : {r['샤프비율']}      (기준 ≥1.0)")
    print(f"  최대 낙폭    : {r['최대DD(%)']}%  (기준 ≥-15%)")
    print(f"  총 손익      : {r['총손익(원)']:,}원  (수익률 {r['총수익률(%)']}%)")
    if r.get("갭대역별"):
        print(f"\n  갭 크기별 성과:")
        for band, v in r["갭대역별"].items():
            print(f"    갭 {band:6s} | {v['횟수']}회 | 승률 {v['승률']}% | 평균 {v['평균']}%")
    if r.get("청산사유별"):
        print(f"\n  청산 사유별:")
        for reason, v in r["청산사유별"].items():
            tag2 = "✓" if v["총손익"] > 0 else "✗"
            print(f"    {tag2} {reason:8s} | {v['횟수']}회 | "
                  f"승률 {v['승률']}% | 평균 {v['평균수익']}%")


# ─────────────────────────────────────────────────────────────────
#  메인
# ─────────────────────────────────────────────────────────────────
def main():
    cfg = CONFIG
    end   = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=cfg["backtest_days"])).strftime("%Y%m%d")

    print("=" * 55)
    print("  한국 주식 단기스윙 백테스트 v2")
    print(f"  전략: 갭업 + 오프닝 드라이브 모멘텀")
    print(f"  기간: {start} ~ {end}  ({cfg['backtest_days']}일)")
    print(f"  초기자본: {cfg['capital']:,}원")
    print(f"  비용: 왕복 {ROUND_TRIP_COST*100:.2f}% (수수료+슬리피지)")
    print(f"  진입: 갭업 {cfg['gap_min_pct']*100:.0f}~{cfg['gap_max_pct']*100:.0f}% "
          f"+ 거래량 {cfg['vol_mult']}x + 드라이브 {cfg['drive_min_pct']*100:.0f}%+ 확인")
    print(f"  청산: 1차 +{cfg['tp1_pct']*100:.0f}%(50%) "
          f"/ 2차 +{cfg['tp2_pct']*100:.0f}% "
          f"/ 손절 -{cfg['stop_pct']*100:.1f}%")
    print("=" * 55)

    all_results = {}
    all_trades  = []
    passed = []

    for name, ticker in cfg["tickers"].items():
        print(f"\n[{name}({ticker})] 데이터 수집 중...")
        df = fetch_ohlcv(ticker, start, end)

        if df.empty or len(df) < 30:
            print(f"  데이터 부족")
            continue

        print(f"  {len(df)}일 | "
              f"{df['date'].iloc[0].strftime('%Y-%m-%d')} ~ "
              f"{df['date'].iloc[-1].strftime('%Y-%m-%d')}")

        df = add_indicators(df, cfg)
        trades = simulate_trades(ticker, name, df, cfg)
        result = analyze(trades, cfg["capital"])
        print_report(name, ticker, result)
        all_results[ticker] = result
        all_trades.extend(trades)

        if "error" not in result and check_pass(result):
            passed.append({"name": name, "ticker": ticker, **result})

    # ── 전체 합산 ──
    print(f"\n\n{'='*55}")
    print("  전체 합산 성과")
    print(f"{'='*55}")

    if all_trades:
        total_df  = pd.DataFrame([t.__dict__ for t in all_trades])
        total_pnl = total_df["pnl_krw"].sum()
        total_wr  = len(total_df[total_df["pnl_krw"] > 0]) / len(total_df) * 100
        print(f"  총 거래수: {len(total_df)}회")
        print(f"  통합 승률: {total_wr:.1f}%")
        print(f"  총 손익:   {total_pnl:,}원")

    if passed:
        print(f"\n  ✅ 실전 투입 가능 종목 ({len(passed)}개):")
        for p in sorted(passed, key=lambda x: x.get("손익비", 0), reverse=True):
            print(f"    {p['name']}({p['ticker']}) | "
                  f"승률 {p['승률(%)']}% | 손익비 {p['손익비']} | "
                  f"수익률 {p['총수익률(%)']}%")

        print("\n  → 실전 투입 시 갭 스캐너가 매일 장 시작 전 후보 종목 선별")
        print("  → 갭 크기 3~8% 구간이 최적 (상/하한 모두 성과 저조 예상)")
    else:
        print("\n  통과 종목 없음 — 파라미터 재검토 필요")
        valid = {k: v for k, v in all_results.items() if "error" not in v}
        if valid:
            best = max(valid, key=lambda k: valid[k].get("손익비", 0))
            r = valid[best]
            print(f"  최고 손익비: {best} | 손익비 {r['손익비']} | 승률 {r['승률(%)']}%")

    # JSON 저장
    out = "stock_result_v2.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  결과 저장: {out}")
    print("\n  다음 단계:")
    print("  1. 통과 종목 갭 패턴 → 실전 스크리닝 로직으로 이식")
    print("  2. recommendation_engine.py korea_plan 재작성")
    print("  3. 코인 백테스트 결과와 조합해 포트폴리오 배분 결정")


if __name__ == "__main__":
    main()

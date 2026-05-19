"""
한국 주식 전략 v3 — 모멘텀 브레이크아웃 (일봉 멀티데이 홀딩)
목적: 오토트레이딩 수익 극대화를 위한 전략 검증

전략 정의 (코인 v5와 동일 구조, 주식에 맞게 파라미터 조정):
  진입 조건 (AND):
    1. 20일 신고가 돌파: 종가 > 20일 최고가
    2. 거래량 급증: 당일 거래량 > 20일 평균의 2.5배
    3. RSI(14) 55~78 (모멘텀 확인, 과매수 회피)
    4. 가격 > EMA20 (추세 방향 필터)
    5. 진입: 다음 날 시가 (미래 데이터 참조 방지)

  청산 조건 (우선순위):
    1. 손절: -2.5%
    2. 1차 익절: +4% (50% 청산) → 남은 50%는 트레일링
    3. 2차 익절: +8%
    4. 트레일링 스탑: +4% 도달 후 고점 대비 -3.5%
    5. 최대 보유: 5 거래일 강제 청산

  비용:
    수수료: 0.015% x 2 = 0.03% (증권사 실제 수준)
    슬리피지: 0.05% x 2 = 0.10%
    총 왕복: 0.13%

  통과 기준:
    승률 ≥ 45%
    손익비 ≥ 2.0
    샤프비율 ≥ 1.0
    최대 낙폭 ≥ -15%
    총수익률 > 0%
    거래 수 ≥ 20

주의:
  일봉 데이터로 장중 청산 근사. 실제 장중 데이터 사용 시 더 정확.
  손절/익절 여부는 당일 low/high로 판정.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
from dataclasses import dataclass, field
from typing import Optional

try:
    from pykrx import stock as krx
    PYKRX_OK = True
except ImportError:
    PYKRX_OK = False
    print("[안내] pykrx 없음. pip install pykrx 후 재실행하세요.")


# ─────────────────────────────────────────────────────────────────
#  설정값
# ─────────────────────────────────────────────────────────────────
CONFIG = {
    "capital":            5_000_000,
    "risk_per_trade":     0.02,          # 1회 자본의 2% 리스크
    "max_position_pct":   0.30,          # 최대 30% 집중

    # 진입 조건
    "breakout_period":    20,            # N일 신고가 돌파
    "vol_surge_mult":     2.5,           # 거래량 급증 배수
    "vol_ma_period":      20,
    "rsi_period":         14,
    "rsi_min":            55,
    "rsi_max":            78,
    "ema_period":         20,

    # 청산
    "tp1_pct":            0.040,         # 1차 익절 +4% (50% 청산)
    "tp2_pct":            0.080,         # 2차 익절 +8%
    "tp1_exit_ratio":     0.50,
    "stop_pct":           0.025,         # 손절 -2.5%
    "trail_trigger":      0.040,         # 트레일링 발동 기준 +4%
    "trail_pct":          0.035,         # 고점 대비 -3.5% (트레일)
    "max_hold_days":      5,             # 최대 보유 5 거래일

    # 비용
    "commission":         0.00015,       # 0.015%
    "slippage":           0.0005,        # 0.05%

    # 백테스트 기간
    "backtest_days":      365,           # 1년

    # 종목 유니버스 (코스닥 변동성 상위 + 코스피 모멘텀)
    # 실전에서는 매일 동적 스크리닝 — 여기서는 대표 종목 20개로 검증
    "tickers": {
        # 코스닥 바이오/성장주 (변동성 크고 모멘텀 강함)
        "에코프로비엠":     "247540",
        "알테오젠":         "196170",
        "HLB":              "028300",
        "리가켐바이오":     "141080",
        "삼천당제약":       "000250",
        "클래시스":         "214150",
        "레인보우로보틱스": "277810",
        "에코프로":         "086520",
        "셀트리온":         "068270",
        "카카오게임즈":     "293490",
        # 코스피 모멘텀 (대형주 + 테마)
        "삼성전자":         "005930",
        "SK하이닉스":       "000660",
        "현대차":           "005380",
        "카카오":           "035720",
        "POSCO홀딩스":      "005490",
        "LG에너지솔루션":   "373220",
        "삼성SDI":          "006400",
        "크래프톤":         "259960",
        "네이버":           "035420",
        "두산에너빌리티":   "034020",
    },
}

ROUND_TRIP_COST = (CONFIG["commission"] + CONFIG["slippage"]) * 2  # = 0.0013


# ─────────────────────────────────────────────────────────────────
#  데이터 수집
# ─────────────────────────────────────────────────────────────────
def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    if not PYKRX_OK:
        print("  [pykrx 없음] pip install pykrx 실행 필요")
        return pd.DataFrame()
    try:
        df = krx.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        # pykrx 컬럼명 정규화
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if "date" in cl or cl == "날짜":
                col_map[c] = "date"
            elif "open" in cl or "시가" in cl:
                col_map[c] = "open"
            elif "high" in cl or "고가" in cl:
                col_map[c] = "high"
            elif "low" in cl or "저가" in cl:
                col_map[c] = "low"
            elif "close" in cl or "종가" in cl:
                col_map[c] = "close"
            elif "volume" in cl or "거래량" in cl:
                col_map[c] = "volume"
        df = df.rename(columns=col_map)
        needed = ["date", "open", "high", "low", "close", "volume"]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            print(f"  [컬럼 없음] {missing} / 실제: {list(df.columns)}")
            return pd.DataFrame()
        df = df[needed].copy()
        df["date"] = pd.to_datetime(df["date"])
        df["ticker"] = ticker
        df = df.dropna().sort_values("date").reset_index(drop=True)
        # 0값 제거
        df = df[(df["open"] > 0) & (df["close"] > 0) & (df["volume"] > 0)]
        return df
    except Exception as e:
        print(f"  [pykrx 오류] {ticker}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────
#  지표 계산
# ─────────────────────────────────────────────────────────────────
def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta  = series.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_g  = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l  = loss.ewm(com=period - 1, min_periods=period).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy().sort_values("date").reset_index(drop=True)

    # 볼륨 이평
    df["vol_ma"]       = df["volume"].rolling(cfg["vol_ma_period"]).mean()
    df["vol_ratio"]    = df["volume"] / df["vol_ma"]

    # 신고가 돌파 (현재 종가 > 이전 N일 최고가)
    df["prev_high_n"]  = df["high"].shift(1).rolling(cfg["breakout_period"]).max()
    df["breakout"]     = df["close"] > df["prev_high_n"]

    # RSI
    df["rsi"]          = _rsi(df["close"], cfg["rsi_period"])

    # EMA 추세 필터
    df["ema"]          = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"]    = df["close"] > df["ema"]

    # ATR (손절/트레일에 활용)
    hl  = df["high"]  - df["low"]
    hpc = (df["high"]  - df["close"].shift(1)).abs()
    lpc = (df["low"]   - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    return df.dropna().reset_index(drop=True)


def check_entry(row: pd.Series, cfg: dict) -> bool:
    if not row["breakout"]:
        return False
    if row["vol_ratio"] < cfg["vol_surge_mult"]:
        return False
    if not (cfg["rsi_min"] <= row["rsi"] <= cfg["rsi_max"]):
        return False
    if not row["above_ema"]:
        return False
    return True


# ─────────────────────────────────────────────────────────────────
#  멀티데이 트레이드 시뮬레이션
# ─────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    ticker:       str
    name:         str
    entry_date:   str
    exit_date:    str
    entry_price:  float
    exit_price:   float
    size_krw:     float
    pnl_pct:      float
    pnl_krw:      float
    exit_reason:  str
    hold_days:    int
    entry_rsi:    float


def simulate_trades(ticker: str, name: str, df: pd.DataFrame, cfg: dict) -> list[Trade]:
    """
    다음날 시가 진입 → 최대 max_hold_days 일 보유.
    - 부분 익절 시뮬레이션: 1차 익절 50%는 별도 Trade로 기록
    - 나머지 50%: 트레일링 스탑 or 2차 익절 or 강제 청산
    """
    trades  = []
    n       = len(df)
    in_pos  = False
    skip_until = -1  # 현재 포지션 종료일 인덱스

    for i in range(len(df) - 1):
        if i < skip_until:
            continue
        if in_pos:
            continue

        row = df.iloc[i]
        if not check_entry(row, cfg):
            continue

        # 다음날 시가 진입
        entry_idx  = i + 1
        entry_row  = df.iloc[entry_idx]
        entry_p    = entry_row["open"] * (1 + cfg["slippage"])

        # 사이즈 계산
        risk_per_unit = cfg["stop_pct"]
        size_krw      = min(
            cfg["capital"] * cfg["risk_per_trade"] / risk_per_unit,
            cfg["capital"] * cfg["max_position_pct"]
        )

        stop_p     = entry_p * (1 - cfg["stop_pct"])
        tp1_p      = entry_p * (1 + cfg["tp1_pct"])
        tp2_p      = entry_p * (1 + cfg["tp2_pct"])

        partial_done  = False
        high_since    = entry_p
        trail_active  = False

        for j in range(entry_idx, min(entry_idx + cfg["max_hold_days"], n)):
            day  = df.iloc[j]
            low  = day["low"]
            high = day["high"]
            close= day["close"]
            hold = j - entry_idx

            high_since = max(high_since, high)

            # 트레일링 발동 여부 (1차 익절 이후)
            if partial_done and (high_since / entry_p - 1) >= cfg["trail_trigger"]:
                trail_active = True

            # ── 청산 판단 ──
            # 1) 손절
            if low <= stop_p:
                ep = max(stop_p, day["open"]) * (1 - cfg["slippage"])
                # 1차 익절 미실현 포지션 100% 손절
                ratio = (1 - cfg["tp1_exit_ratio"]) if partial_done else 1.0
                _add_trade(trades, ticker, name, entry_row, day, entry_p, ep,
                           size_krw * ratio, "손절", hold, row["rsi"])
                in_pos = False
                skip_until = j + 1
                break

            # 2) 트레일링 스탑 (1차 익절 후)
            if trail_active:
                trail_stop = high_since * (1 - cfg["trail_pct"])
                if low <= trail_stop:
                    ep = max(trail_stop, day["open"]) * (1 - cfg["slippage"])
                    _add_trade(trades, ticker, name, entry_row, day, entry_p, ep,
                               size_krw * (1 - cfg["tp1_exit_ratio"]), "트레일", hold, row["rsi"])
                    in_pos = False
                    skip_until = j + 1
                    break

            # 3) 1차 익절 (아직 미실현)
            if not partial_done and high >= tp1_p:
                ep1 = tp1_p * (1 - cfg["slippage"])
                _add_trade(trades, ticker, name, entry_row, day, entry_p, ep1,
                           size_krw * cfg["tp1_exit_ratio"], "1차익절", hold, row["rsi"])
                partial_done = True
                # 남은 포지션은 계속 진행

            # 4) 2차 익절
            if partial_done and high >= tp2_p:
                ep2 = tp2_p * (1 - cfg["slippage"])
                _add_trade(trades, ticker, name, entry_row, day, entry_p, ep2,
                           size_krw * (1 - cfg["tp1_exit_ratio"]), "2차익절", hold, row["rsi"])
                in_pos = False
                skip_until = j + 1
                break

        else:
            # 최대 보유 기간 도달 → 강제 청산 (종가)
            last = df.iloc[min(entry_idx + cfg["max_hold_days"] - 1, n - 1)]
            ep   = last["close"] * (1 - cfg["slippage"])
            hold = min(cfg["max_hold_days"] - 1, n - entry_idx - 1)

            if partial_done:
                _add_trade(trades, ticker, name, entry_row, last, entry_p, ep,
                           size_krw * (1 - cfg["tp1_exit_ratio"]), "기간청산", hold, row["rsi"])
            else:
                _add_trade(trades, ticker, name, entry_row, last, entry_p, ep,
                           size_krw, "기간청산", hold, row["rsi"])
            skip_until = entry_idx + cfg["max_hold_days"]

    return trades


def _add_trade(trades, ticker, name, entry_row, exit_row, entry_p, exit_p,
               size_krw, reason, hold, rsi):
    raw_pct = (exit_p - entry_p) / entry_p
    pnl_pct = raw_pct - ROUND_TRIP_COST
    pnl_krw = size_krw * pnl_pct
    trades.append(Trade(
        ticker=ticker, name=name,
        entry_date=str(entry_row["date"].date()),
        exit_date=str(exit_row["date"].date()),
        entry_price=round(entry_p), exit_price=round(exit_p),
        size_krw=round(size_krw),
        pnl_pct=round(pnl_pct * 100, 3),
        pnl_krw=round(pnl_krw),
        exit_reason=reason,
        hold_days=hold,
        entry_rsi=round(rsi, 1),
    ))


# ─────────────────────────────────────────────────────────────────
#  성과 분석
# ─────────────────────────────────────────────────────────────────
def analyze(trades: list[Trade], capital: float) -> dict:
    if len(trades) < 5:
        return {"error": f"거래 {len(trades)}회 — 분석 불가"}

    df    = pd.DataFrame([t.__dict__ for t in trades])
    wins  = df[df["pnl_krw"] > 0]
    losses= df[df["pnl_krw"] <= 0]

    wr  = len(wins) / len(df) * 100
    aw  = wins["pnl_pct"].mean()    if len(wins)   > 0 else 0
    al  = losses["pnl_pct"].mean()  if len(losses) > 0 else 0
    rr  = abs(aw / al)              if al != 0 else 0

    equity = capital + df["pnl_krw"].cumsum()
    max_dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

    r = df["pnl_pct"] / 100
    sharpe = (r.mean() - 0.03/252) / r.std() * np.sqrt(252) if r.std() > 0 else 0

    by_reason = {}
    for reason in df["exit_reason"].unique():
        sub = df[df["exit_reason"] == reason]
        by_reason[reason] = {
            "횟수":      len(sub),
            "승률":      round(len(sub[sub["pnl_krw"] > 0]) / len(sub) * 100, 1),
            "평균수익":  round(sub["pnl_pct"].mean(), 2),
            "총손익":    round(sub["pnl_krw"].sum()),
        }

    return {
        "총거래수":    len(df),
        "승률(%)":     round(wr, 1),
        "손익비":      round(rr, 2),
        "샤프비율":    round(sharpe, 2),
        "최대DD(%)":   round(max_dd, 2),
        "평균수익(%)": round(aw, 2),
        "평균손실(%)": round(al, 2),
        "총손익(원)":  round(df["pnl_krw"].sum()),
        "총수익률(%)": round(df["pnl_krw"].sum() / capital * 100, 2),
        "평균보유일":  round(df["hold_days"].mean(), 1),
        "청산사유별":  by_reason,
    }


def check_pass(r: dict) -> bool:
    return (
        r.get("승률(%)",      0)   >= 45
        and r.get("손익비",   0)   >= 2.0
        and r.get("샤프비율", 0)   >= 1.0
        and r.get("최대DD(%)", -999) >= -15
        and r.get("총수익률(%)", 0) > 0
        and r.get("총거래수", 0)   >= 20
    )


def print_report(name: str, ticker: str, r: dict):
    if "error" in r:
        print(f"\n  [{name}({ticker})]: {r['error']}")
        return
    ok  = check_pass(r)
    tag = "✅ 통과" if ok else "❌ 미달"
    print(f"\n{'='*55}")
    print(f"  {name}({ticker})  {tag}")
    print(f"{'='*55}")
    print(f"  총 거래수    : {r['총거래수']}회")
    print(f"  승률         : {r['승률(%)']}%     (기준 >=45%)")
    print(f"  손익비       : {r['손익비']}      (기준 >=2.0)")
    print(f"  샤프비율     : {r['샤프비율']}      (기준 >=1.0)")
    print(f"  최대 낙폭    : {r['최대DD(%)']}%  (기준 >=-15%)")
    print(f"  평균 보유일  : {r['평균보유일']}일")
    print(f"  총 손익      : {r['총손익(원)']:,}원  (수익률 {r['총수익률(%)']}%)")
    if r.get("청산사유별"):
        print(f"\n  청산 사유별:")
        for reason, v in r["청산사유별"].items():
            tag2 = "+" if v["총손익"] > 0 else "-"
            print(f"    [{tag2}] {reason:6s} | {v['횟수']:3}회 | "
                  f"승률 {v['승률']:5.1f}% | 평균 {v['평균수익']:+.2f}%")


# ─────────────────────────────────────────────────────────────────
#  메인
# ─────────────────────────────────────────────────────────────────
def main():
    cfg  = CONFIG
    end  = datetime.today().strftime("%Y%m%d")
    # 지표 계산용 버퍼 50일 추가
    start_buf = (datetime.today() - timedelta(days=cfg["backtest_days"] + 60)).strftime("%Y%m%d")
    start_bt  = (datetime.today() - timedelta(days=cfg["backtest_days"])).strftime("%Y%m%d")

    print("=" * 55)
    print("  한국 주식 모멘텀 브레이크아웃 v3")
    print(f"  전략: {cfg['breakout_period']}일 신고가 + 거래량 {cfg['vol_surge_mult']}x + RSI {cfg['rsi_min']}-{cfg['rsi_max']}")
    print(f"  기간: {start_bt} ~ {end}  ({cfg['backtest_days']}일)")
    print(f"  초기자본: {cfg['capital']:,}원")
    print(f"  비용: 왕복 {ROUND_TRIP_COST*100:.2f}% (수수료+슬리피지)")
    print(f"  진입: 다음날 시가")
    print(f"  청산: 1차 +{cfg['tp1_pct']*100:.0f}%(50%) / 2차 +{cfg['tp2_pct']*100:.0f}% / 손절 -{cfg['stop_pct']*100:.1f}% / 트레일 / 최대 {cfg['max_hold_days']}일")
    print("=" * 55)

    all_results = {}
    all_trades  = []
    passed      = []

    for name, ticker in cfg["tickers"].items():
        print(f"\n[{name}({ticker})] 데이터 수집 중...", end=" ", flush=True)
        df = fetch_ohlcv(ticker, start_buf, end)

        if df.empty or len(df) < 40:
            print(f"데이터 부족 ({len(df)}일)")
            continue

        print(f"{len(df)}일 수집")

        df      = add_signals(df, cfg)
        # 백테스트 기간만 신호 스캔 (지표 버퍼 제외)
        df_bt   = df[df["date"] >= pd.Timestamp(start_bt)].reset_index(drop=True)

        if len(df_bt) < 20:
            print(f"  백테스트 기간 데이터 부족 ({len(df_bt)}일)")
            continue

        trades  = simulate_trades(ticker, name, df_bt, cfg)
        result  = analyze(trades, cfg["capital"])
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
        r = total_df["pnl_pct"] / 100
        total_sharpe = (r.mean() - 0.03/252) / r.std() * np.sqrt(252) if r.std() > 0 else 0
        print(f"  총 거래수  : {len(total_df)}회")
        print(f"  통합 승률  : {total_wr:.1f}%")
        print(f"  통합 샤프  : {total_sharpe:.2f}")
        print(f"  총 손익    : {total_pnl:,}원")
        print(f"  총 수익률  : {total_pnl / cfg['capital'] * 100:.2f}%")
    else:
        print("  거래 없음")

    if passed:
        print(f"\n  ✅ 기준 통과 종목 ({len(passed)}개):")
        for p in sorted(passed, key=lambda x: x.get("손익비", 0), reverse=True):
            print(f"    {p['name']}({p['ticker']}) | "
                  f"승률 {p['승률(%)']}% | 손익비 {p['손익비']} | "
                  f"수익률 {p['총수익률(%)']}%")
        print("\n  → 실전: 매일 장 마감 후 신호 스캔 → 다음날 시가 진입")
        print("  → 포트폴리오: 동시 최대 3종목 / 종목당 최대 30%")
    else:
        print("\n  통과 종목 없음 — 파라미터 재검토 필요")
        valid = {k: v for k, v in all_results.items() if "error" not in v}
        if valid:
            best = max(valid, key=lambda k: (valid[k].get("손익비", 0) * valid[k].get("승률(%)", 0)))
            r = valid[best]
            name_map = {v: k for k, v in cfg["tickers"].items()}
            print(f"  최고 종합: {name_map.get(best, best)} | "
                  f"승률 {r['승률(%)']}% | 손익비 {r['손익비']} | 샤프 {r['샤프비율']}")

    # JSON 저장
    out = "stock_result_v3.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  결과 저장: {out}")


if __name__ == "__main__":
    main()

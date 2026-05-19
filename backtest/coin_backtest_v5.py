"""
코인 전략 v5 — 단기 스윙 변동성 돌파 (60분봉 기반)
목적: 오토트레이딩 수익 극대화를 위한 전략 검증

전략 정의:
  진입 조건 (AND):
    1. 거래량 급등: 현재 봉 거래량 > 24봉 평균의 3배
    2. 가격 돌파: 현재 종가 > 최근 20봉 최고가 (신고점 돌파)
    3. RSI 모멘텀: RSI(14) 55~78 사이 (추세 확인, 과열 전)
    4. 추세 필터: 종가 > EMA(20) (하락 추세 진입 금지)

  청산 조건:
    목표 1: +4.0% → 포지션 50% 청산
    목표 2: +7.0% → 나머지 청산
    손절: -2.0% (ATR 기반 동적 손절 병행)
    트레일링: +4% 돌파 후 → 고점 대비 -3% 하락 시 청산
    시간 손절: 최대 48시간 보유 후 강제 청산

  비용:
    수수료: 0.05% × 2 = 0.10%
    슬리피지: 0.05% × 2 = 0.10%
    총 왕복 비용: 0.20%

  통과 기준:
    승률 ≥ 45%
    손익비 ≥ 2.0
    샤프비율 ≥ 1.0
    최대 낙폭 ≥ -20%
    총수익률 > 0%
    거래 수 ≥ 20
"""

import requests
import pandas as pd
import numpy as np
import time
import json
import sys
from dataclasses import dataclass, field
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────
#  설정값
# ─────────────────────────────────────────────────────────────────
CONFIG = {
    # 테스트 코인 (업비트 KRW 기준 유동성 상위)
    "markets": [
        "KRW-BTC", "KRW-ETH", "KRW-XRP",
        "KRW-SOL", "KRW-DOGE", "KRW-ADA",
    ],

    # 자본
    "capital":          5_000_000,      # 초기 자본
    "risk_per_trade":   0.02,           # 1회 리스크 2% (ATR 손절 기준)
    "max_position_pct": 0.30,           # 단일 포지션 최대 자본의 30%

    # 시그널 파라미터
    "vol_surge_mult":   3.0,            # 거래량 급등 배수 (24봉 평균 대비)
    "breakout_period":  20,             # 신고점 돌파 기준 봉 수
    "rsi_period":       14,
    "rsi_min":          55,             # RSI 하한 (모멘텀 확인)
    "rsi_max":          78,             # RSI 상한 (과열 방지)
    "ema_period":       20,             # 추세 필터 EMA

    # 청산 파라미터
    "tp1_pct":          0.040,          # 1차 익절 +4.0%
    "tp2_pct":          0.070,          # 2차 익절 +7.0%
    "tp1_exit_ratio":   0.50,           # 1차 익절 시 청산 비율
    "stop_pct":         0.020,          # 기본 손절 -2.0%
    "atr_stop_mult":    2.0,            # ATR 손절 배수 (기본 손절과 더 엄격한 쪽 사용)
    "trail_trigger":    0.040,          # 트레일링 발동 기준 +4%
    "trail_atr_mult":   3.0,            # 트레일링 ATR 배수
    "max_hold_hours":   48,             # 최대 보유 시간

    # 비용
    "commission":       0.0005,         # 수수료 0.05%
    "slippage":         0.0005,         # 슬리피지 0.05%

    # 데이터
    "candle_minutes":   60,             # 60분봉
    "fetch_count":      4400,           # 봉 수 (약 6개월치 = 24h*183일)
    "atr_period":       14,
}

ROUND_TRIP_COST = (CONFIG["commission"] + CONFIG["slippage"]) * 2


# ─────────────────────────────────────────────────────────────────
#  데이터 수집
# ─────────────────────────────────────────────────────────────────
def fetch_ohlcv(market: str, minutes: int = 60, count: int = 500) -> pd.DataFrame:
    """업비트 분봉 데이터 수집 (최대 500봉)"""
    url = f"https://api.upbit.com/v1/candles/minutes/{minutes}"
    all_candles = []
    to = None

    while len(all_candles) < count:
        need = min(200, count - len(all_candles))
        params = {"market": market, "count": need}
        if to:
            params["to"] = to

        for attempt in range(3):
            try:
                res = requests.get(url, params=params, timeout=15)
                candles = res.json()
                if not candles or not isinstance(candles, list):
                    return _to_df(all_candles)
                all_candles.extend(candles)
                to = candles[-1]["candle_date_time_utc"]
                time.sleep(0.15)
                break
            except Exception as e:
                print(f"  [재시도 {attempt+1}] {market}: {e}")
                time.sleep(1)
        else:
            break

    return _to_df(all_candles)


def _to_df(candles: list) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles).rename(columns={
        "candle_date_time_kst": "dt",
        "opening_price":        "open",
        "high_price":           "high",
        "low_price":            "low",
        "trade_price":          "close",
        "candle_acc_trade_volume": "volume",
    })
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.sort_values("dt")[["dt", "open", "high", "low", "close", "volume"]]
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────
#  지표 계산
# ─────────────────────────────────────────────────────────────────
def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def add_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()

    # 거래량 이동평균 (24봉 = 24시간)
    df["vol_ma24"]      = df["volume"].rolling(24).mean()
    df["vol_surge"]     = df["volume"] > df["vol_ma24"] * cfg["vol_surge_mult"]

    # 신고점 돌파
    df["high_max"]      = df["close"].shift(1).rolling(cfg["breakout_period"]).max()
    df["breakout"]      = df["close"] > df["high_max"]

    # RSI
    df["rsi"]           = calc_rsi(df["close"], cfg["rsi_period"])
    df["rsi_ok"]        = (df["rsi"] >= cfg["rsi_min"]) & (df["rsi"] <= cfg["rsi_max"])

    # EMA 추세 필터
    df["ema"]           = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"]     = df["close"] > df["ema"]

    # ATR
    df["atr"]           = calc_atr(df, cfg["atr_period"])

    # 최종 진입 시그널 (AND)
    df["signal"] = (
        df["vol_surge"] &
        df["breakout"] &
        df["rsi_ok"] &
        df["above_ema"]
    )

    return df.dropna().reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────
#  트레이드 시뮬레이션
# ─────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    market:         str
    entry_dt:       str
    exit_dt:        str
    entry_price:    float
    exit_price:     float
    size_krw:       float
    pnl_pct:        float           # 비용 포함
    pnl_krw:        float
    exit_reason:    str
    hold_hours:     float
    rsi_at_entry:   float = 0.0
    tp1_taken:      bool  = False


def simulate_trades(df: pd.DataFrame, market: str, cfg: dict) -> list[Trade]:
    """
    시그널 발생 시점부터 포지션 시뮬레이션.
    중첩 포지션 없음 (한 번에 1 포지션).
    """
    trades = []
    n = len(df)
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    size_krw = 0.0
    remaining_ratio = 1.0        # 남은 포지션 비율
    tp1_taken = False
    trail_active = False
    trail_stop = 0.0
    atr_stop = 0.0
    basic_stop = 0.0

    for i in range(1, n - 1):
        row = df.iloc[i]

        # ── 포지션 없음: 진입 탐색 ──
        if not in_position:
            if not row["signal"]:
                continue

            # 다음 봉 시가에 진입
            next_row = df.iloc[i + 1]
            entry_price = next_row["open"] * (1 + cfg["slippage"])
            atr = row["atr"]

            # 포지션 사이즈 (ATR 손절 기준 리스크)
            atr_stop_dist = atr * cfg["atr_stop_mult"]
            fixed_stop_dist = entry_price * cfg["stop_pct"]
            stop_dist = max(atr_stop_dist, fixed_stop_dist)
            stop_dist = min(stop_dist, entry_price * 0.03)   # 최대 손절 -3%

            risk_krw = cfg["capital"] * cfg["risk_per_trade"]
            raw_size = risk_krw / (stop_dist / entry_price)
            size_krw = min(raw_size, cfg["capital"] * cfg["max_position_pct"])

            basic_stop = entry_price - fixed_stop_dist
            atr_stop   = entry_price - atr_stop_dist
            # 더 높은 손절 기준 사용 (보수적)
            actual_stop = max(basic_stop, atr_stop)

            in_position  = True
            entry_idx    = i + 1
            remaining_ratio = 1.0
            tp1_taken    = False
            trail_active = False
            trail_stop   = actual_stop
            continue

        # ── 포지션 있음: 청산 탐색 ──
        row = df.iloc[i]
        elapsed_hours = (i - entry_idx)  # 60분봉이므로 봉 수 = 시간 수
        curr_pct = (row["close"] - entry_price) / entry_price

        # 1. 손절
        if row["low"] <= trail_stop:
            exit_p = trail_stop * (1 - cfg["slippage"])
            _close(trades, market, df, entry_idx, i, entry_price, exit_p,
                   size_krw, remaining_ratio, "손절", elapsed_hours,
                   df.iloc[entry_idx]["rsi"], tp1_taken, cfg)
            in_position = False
            continue

        # 2. 1차 익절 (+4%)
        if not tp1_taken and row["high"] >= entry_price * (1 + cfg["tp1_pct"]):
            exit_p = entry_price * (1 + cfg["tp1_pct"]) * (1 - cfg["slippage"])
            partial_pnl = (exit_p - entry_price) / entry_price - ROUND_TRIP_COST
            partial_krw = size_krw * cfg["tp1_exit_ratio"] * partial_pnl
            tp1_taken    = True
            remaining_ratio = 1.0 - cfg["tp1_exit_ratio"]
            trail_active = True
            trail_stop   = max(trail_stop, row["high"] - row["atr"] * cfg["trail_atr_mult"])
            # 1차 익절 트레이드 기록
            trades.append(Trade(
                market=market,
                entry_dt=str(df.iloc[entry_idx]["dt"]),
                exit_dt=str(row["dt"]),
                entry_price=round(entry_price, 4),
                exit_price=round(exit_p, 4),
                size_krw=round(size_krw * cfg["tp1_exit_ratio"]),
                pnl_pct=round(partial_pnl * 100, 3),
                pnl_krw=round(partial_krw),
                exit_reason="1차익절",
                hold_hours=elapsed_hours,
                rsi_at_entry=round(df.iloc[entry_idx]["rsi"], 1),
                tp1_taken=True,
            ))

        # 3. 2차 익절 (+7%)
        if tp1_taken and row["high"] >= entry_price * (1 + cfg["tp2_pct"]):
            exit_p = entry_price * (1 + cfg["tp2_pct"]) * (1 - cfg["slippage"])
            _close(trades, market, df, entry_idx, i, entry_price, exit_p,
                   size_krw, remaining_ratio, "2차익절", elapsed_hours,
                   df.iloc[entry_idx]["rsi"], tp1_taken, cfg)
            in_position = False
            continue

        # 4. 트레일링 스탑 갱신
        if trail_active:
            new_trail = row["high"] - row["atr"] * cfg["trail_atr_mult"]
            trail_stop = max(trail_stop, new_trail)

        # 5. 시간 손절 (48시간)
        if elapsed_hours >= cfg["max_hold_hours"]:
            exit_p = row["close"] * (1 - cfg["slippage"])
            _close(trades, market, df, entry_idx, i, entry_price, exit_p,
                   size_krw, remaining_ratio, "시간청산", elapsed_hours,
                   df.iloc[entry_idx]["rsi"], tp1_taken, cfg)
            in_position = False
            continue

    return trades


def _close(trades, market, df, entry_idx, exit_idx,
           entry_price, exit_p, size_krw, remaining_ratio,
           reason, hold_hours, rsi_entry, tp1_taken, cfg):
    rem_size = size_krw * remaining_ratio
    pnl_pct_raw = (exit_p - entry_price) / entry_price
    pnl_pct = pnl_pct_raw - ROUND_TRIP_COST
    pnl_krw = rem_size * pnl_pct
    trades.append(Trade(
        market=market,
        entry_dt=str(df.iloc[entry_idx]["dt"]),
        exit_dt=str(df.iloc[exit_idx]["dt"]),
        entry_price=round(entry_price, 4),
        exit_price=round(exit_p, 4),
        size_krw=round(rem_size),
        pnl_pct=round(pnl_pct * 100, 3),
        pnl_krw=round(pnl_krw),
        exit_reason=reason,
        hold_hours=hold_hours,
        rsi_at_entry=round(rsi_entry, 1),
        tp1_taken=tp1_taken,
    ))


# ─────────────────────────────────────────────────────────────────
#  성과 분석
# ─────────────────────────────────────────────────────────────────
def analyze(trades: list[Trade], capital: float) -> dict:
    if len(trades) < 10:
        return {"error": f"거래 {len(trades)}회 — 분석 불가 (최소 10회 필요)"}

    df = pd.DataFrame([t.__dict__ for t in trades])

    wins   = df[df["pnl_krw"] > 0]
    losses = df[df["pnl_krw"] <= 0]

    wr      = len(wins) / len(df) * 100
    avg_w   = wins["pnl_pct"].mean()   if len(wins)   > 0 else 0
    avg_l   = losses["pnl_pct"].mean() if len(losses) > 0 else 0
    rr      = abs(avg_w / avg_l)       if avg_l != 0  else 0
    pf      = (wins["pnl_krw"].sum() / abs(losses["pnl_krw"].sum())
               if len(losses) > 0 and losses["pnl_krw"].sum() != 0 else 99)

    equity  = capital + df["pnl_krw"].cumsum()
    max_dd  = ((equity - equity.cummax()) / equity.cummax() * 100).min()

    # 샤프비율 (거래 기준)
    r       = df["pnl_pct"] / 100
    sharpe  = ((r.mean() - 0.03 / 252) / r.std() * np.sqrt(252)
               if r.std() > 0 else 0)

    # 청산 사유별 분석
    by_reason = {}
    for reason in df["exit_reason"].unique():
        sub = df[df["exit_reason"] == reason]
        by_reason[reason] = {
            "횟수":   len(sub),
            "승률":   round(len(sub[sub["pnl_krw"] > 0]) / len(sub) * 100, 1),
            "평균수익": round(sub["pnl_pct"].mean(), 2),
            "총손익":  round(sub["pnl_krw"].sum()),
        }

    return {
        "총거래수":      len(df),
        "승률(%)":       round(wr, 1),
        "손익비":        round(rr, 2),
        "샤프비율":      round(sharpe, 2),
        "최대DD(%)":     round(max_dd, 2),
        "프로핏팩터":    round(pf, 2),
        "평균수익(%)":   round(avg_w, 2),
        "평균손실(%)":   round(avg_l, 2),
        "평균보유(h)":   round(df["hold_hours"].mean(), 1),
        "총손익(원)":    round(df["pnl_krw"].sum()),
        "총수익률(%)":   round(df["pnl_krw"].sum() / capital * 100, 2),
        "청산사유별":    by_reason,
    }


def check_pass(r: dict) -> bool:
    return (
        r.get("승률(%)",    0)    >= 45
        and r.get("손익비",  0)   >= 2.0
        and r.get("샤프비율", 0)  >= 1.0
        and r.get("최대DD(%)", -999) >= -20
        and r.get("총수익률(%)", 0) > 0
        and r.get("총거래수", 0)  >= 20
    )


def print_report(market: str, r: dict, capital: float):
    if "error" in r:
        print(f"\n  {market}: {r['error']}")
        return

    ok = check_pass(r)
    status = "✅ 실전 투입 가능" if ok else "❌ 기준 미달"
    line = "=" * 58

    print(f"\n{line}")
    print(f"  {market}  {status}")
    print(line)
    print(f"  총 거래수      : {r['총거래수']}회")
    print(f"  승률           : {r['승률(%)']}%     (기준 ≥45%)")
    print(f"  손익비         : {r['손익비']}      (기준 ≥2.0)")
    print(f"  샤프비율       : {r['샤프비율']}      (기준 ≥1.0)")
    print(f"  최대 낙폭      : {r['최대DD(%)']}%  (기준 ≥-20%)")
    print(f"  프로핏팩터     : {r['프로핏팩터']}")
    print(f"  평균 수익      : {r['평균수익(%)']}%  |  평균 손실: {r['평균손실(%)']}%")
    print(f"  평균 보유시간  : {r['평균보유(h)']}시간")
    print(f"  총 손익        : {r['총손익(원)']:,}원")
    print(f"  총 수익률      : {r['총수익률(%)']}%  ({capital:,}원 기준)")
    print(f"\n  청산 사유별:")
    for reason, v in r.get("청산사유별", {}).items():
        tag = "✓" if v["총손익"] > 0 else "✗"
        print(f"    {tag} {reason:8s} | {v['횟수']}회 | "
              f"승률 {v['승률']}% | 평균 {v['평균수익']}% | "
              f"손익 {v['총손익']:,}원")


# ─────────────────────────────────────────────────────────────────
#  메인
# ─────────────────────────────────────────────────────────────────
def main():
    cfg = CONFIG

    print("=" * 58)
    print("  코인 단기스윙 백테스트 v5")
    print(f"  전략: 변동성 돌파 (60분봉 기반)")
    print(f"  기간: 최근 {cfg['fetch_count']}봉 (~{cfg['fetch_count']//24}일)")
    print(f"  초기자본: {cfg['capital']:,}원")
    print(f"  비용: 왕복 {ROUND_TRIP_COST*100:.2f}% (수수료+슬리피지)")
    print(f"  진입조건: 거래량 {cfg['vol_surge_mult']}x 급등 + "
          f"{cfg['breakout_period']}봉 신고점 돌파 + RSI {cfg['rsi_min']}~{cfg['rsi_max']}")
    print(f"  청산: 1차 +{cfg['tp1_pct']*100:.0f}%(50%) "
          f"/ 2차 +{cfg['tp2_pct']*100:.0f}% "
          f"/ 손절 -{cfg['stop_pct']*100:.0f}%")
    print("=" * 58)

    all_results = {}
    passed = []

    for market in cfg["markets"]:
        print(f"\n[{market}] 60분봉 데이터 수집 중...")
        df = fetch_ohlcv(market, cfg["candle_minutes"], cfg["fetch_count"])

        if df.empty or len(df) < 100:
            print(f"  데이터 부족 ({len(df)}봉)")
            continue

        print(f"  수집 완료: {len(df)}봉 | "
              f"{df['dt'].iloc[0].strftime('%Y-%m-%d')} ~ "
              f"{df['dt'].iloc[-1].strftime('%Y-%m-%d')}")

        df = add_signals(df, cfg)
        signal_count = df["signal"].sum()
        print(f"  시그널 발생: {signal_count}회")

        trades = simulate_trades(df, market, cfg)
        result = analyze(trades, cfg["capital"])
        print_report(market, result, cfg["capital"])
        all_results[market] = result

        if "error" not in result and check_pass(result):
            passed.append({
                "market":    market,
                "승률":      result["승률(%)"],
                "손익비":    result["손익비"],
                "샤프":      result["샤프비율"],
                "수익률":    result["총수익률(%)"],
                "총손익(원)": result["총손익(원)"],
            })

    # ── 최종 요약 ──
    print(f"\n\n{'=' * 58}")
    print("  최종 결과 요약")
    print(f"{'=' * 58}")

    if passed:
        passed.sort(key=lambda x: x["손익비"] * x["샤프"], reverse=True)
        print(f"\n  ✅ 실전 투입 가능 코인 ({len(passed)}개):")
        for c in passed:
            print(f"    {c['market']:12s} | "
                  f"승률 {c['승률']}% | "
                  f"손익비 {c['손익비']} | "
                  f"샤프 {c['샤프']} | "
                  f"수익률 {c['수익률']}%")
        print(f"\n  → 가장 강한 코인: {passed[0]['market']}")
        print(f"  → 실전 투입 순서: {' > '.join(c['market'] for c in passed)}")
    else:
        print("\n  통과 코인 없음 — 파라미터 최적화 필요")
        valid = {k: v for k, v in all_results.items() if "error" not in v}
        if valid:
            best = max(valid, key=lambda k: valid[k].get("손익비", 0))
            r = valid[best]
            print(f"  현재 최고 손익비: {best} | "
                  f"손익비 {r['손익비']} | 승률 {r['승률(%)']}%")
            print("\n  조정 방향:")
            for k, v in valid.items():
                rr = v.get("손익비", 0)
                wr = v.get("승률(%)", 0)
                dd = v.get("최대DD(%)", 0)
                issues = []
                if wr < 45:   issues.append(f"승률 {wr}% (↑45% 필요)")
                if rr < 2.0:  issues.append(f"손익비 {rr} (↑2.0 필요)")
                if dd < -20:  issues.append(f"낙폭 {dd}% (↑-20% 필요)")
                if issues:
                    print(f"    {k}: {' / '.join(issues)}")

    # JSON 저장
    out_path = "coin_result_v5.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  결과 저장: {out_path}")
    print("\n  다음 단계:")
    print("  1. 통과 코인 → recommendation_engine.py 신호 엔진 교체")
    print("  2. 미달 코인 → 파라미터 조정 후 재검증")
    print("  3. 양쪽 완료 → 페이퍼 2주 실전 배포")


if __name__ == "__main__":
    main()

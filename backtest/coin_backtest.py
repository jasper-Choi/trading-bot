"""
코인 전략 v4 — 변동성 돌파 + 추세 추종 조합
전략 1: 변동성 돌파 (승률 담당)
전략 2: EMA + ADX 추세 추종 (손익비 담당)
조합: 두 전략 동시 신호 시 포지션 1.5배
"""

import requests
import pandas as pd
import numpy as np
import time
import json
from dataclasses import dataclass


CONFIG = {
    "markets": ["KRW-XRP", "KRW-ETH", "KRW-BTC", "KRW-SOL"],
    "capital":          5_000_000,
    "risk_per_trade":   0.015,
    "atr_stop_mult":    1.5,
    "trailing_mult":    3.0,
    # 변동성 돌파 K값
    "k":                0.6,
    # 추세 추종 파라미터
    "ema_fast":         10,
    "ema_slow":         30,
    "adx_period":       14,
    "adx_threshold":    20,       # ADX 20 이상 = 추세 강함
    # 조합 가중치
    "combo_size_mult":  1.5,      # 두 전략 동시 신호 시 포지션 배수
    "backtest_days":    365,
}


# ─────────────────────────────────────────
#  데이터 수집
# ─────────────────────────────────────────
def fetch_daily(market: str, count: int = 420) -> pd.DataFrame:
    url = "https://api.upbit.com/v1/candles/days"
    all_candles, to = [], None

    while len(all_candles) < count:
        params = {"market": market, "count": min(200, count - len(all_candles))}
        if to:
            params["to"] = to
        for attempt in range(3):
            try:
                res = requests.get(url, params=params, timeout=15)
                candles = res.json()
                if not candles:
                    break
                all_candles.extend(candles)
                to = candles[-1]["candle_date_time_utc"]
                time.sleep(0.2)
                break
            except Exception as e:
                print(f"  [재시도 {attempt+1}] {e}")
                time.sleep(1)
        else:
            break
        if not candles:
            break

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles).rename(columns={
        "candle_date_time_kst": "datetime",
        "opening_price":        "open",
        "high_price":           "high",
        "low_price":            "low",
        "trade_price":          "close",
        "candle_acc_trade_volume": "volume",
    })
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.sort_values("datetime")[
        ["datetime","open","high","low","close","volume"]
    ].reset_index(drop=True)


# ─────────────────────────────────────────
#  지표 계산
# ─────────────────────────────────────────
def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX — 추세 강도 지표"""
    high, low, close = df["high"], df["low"], df["close"]
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up   = high - prev_high
    down = prev_low - low
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)

    atr14  = tr.ewm(span=period, adjust=False).mean()
    pdi    = 100 * pd.Series(pdm, index=df.index).ewm(span=period, adjust=False).mean() / atr14
    ndi    = 100 * pd.Series(ndm, index=df.index).ewm(span=period, adjust=False).mean() / atr14
    dx     = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan))
    adx    = dx.ewm(span=period, adjust=False).mean()
    return adx, pdi, ndi


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()

    # 전일 값
    df["prev_high"]  = df["high"].shift(1)
    df["prev_low"]   = df["low"].shift(1)
    df["prev_close"] = df["close"].shift(1)
    df["prev_range"] = df["prev_high"] - df["prev_low"]

    # ── 전략 1: 변동성 돌파 ──
    df["vb_target"]  = df["open"] + df["prev_range"] * cfg["k"]
    df["vb_signal"]  = df["high"] >= df["vb_target"]   # 당일 돌파 여부

    # ── 전략 2: 추세 추종 ──
    df["ema_fast"]   = df["close"].ewm(span=cfg["ema_fast"],  adjust=False).mean()
    df["ema_slow"]   = df["close"].ewm(span=cfg["ema_slow"],  adjust=False).mean()
    df["trend_bull"] = df["ema_fast"] > df["ema_slow"]        # 상승 추세
    df["trend_cross"]= (df["ema_fast"] > df["ema_slow"]) & \
                       (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))  # 골든크로스

    adx, pdi, ndi    = calc_adx(df, cfg["adx_period"])
    df["adx"]        = adx
    df["pdi"]        = pdi
    df["ndi"]        = ndi
    df["adx_strong"] = df["adx"] >= cfg["adx_threshold"]
    df["trend_signal"] = df["trend_cross"] & df["adx_strong"] & df["trend_bull"]

    # ── 공통 ──
    # ATR 손절
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["prev_close"]).abs()
    lc = (df["low"]  - df["prev_close"]).abs()
    df["atr"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()

    # 익일 OHLC
    df["next_open"]  = df["open"].shift(-1)
    df["next_high"]  = df["high"].shift(-1)
    df["next_low"]   = df["low"].shift(-1)
    df["next_close"] = df["close"].shift(-1)

    return df.dropna().reset_index(drop=True)


# ─────────────────────────────────────────
#  트레이드 시뮬레이션
# ─────────────────────────────────────────
@dataclass
class Trade:
    market:     str
    date:       str
    strategy:   str       # "VB" / "TREND" / "COMBO"
    entry:      float
    exit_price: float
    size:       float
    pnl_pct:    float
    pnl:        float
    exit_reason:str


def sim_vb_trade(row: pd.Series, cfg: dict, size_mult: float = 1.0) -> Trade | None:
    """변동성 돌파 트레이드 시뮬레이션"""
    if not row["vb_signal"] or pd.isna(row["next_open"]):
        return None

    entry    = row["vb_target"]
    atr      = row["atr"]
    stop     = entry - atr * cfg["atr_stop_mult"]
    stop_pct = (entry - stop) / entry
    min_tp   = entry + (entry - stop) * 1.5

    risk_amt = CONFIG["capital"] * cfg["risk_per_trade"] * size_mult
    size     = min(risk_amt / stop_pct, CONFIG["capital"] * 0.4)

    # 익일 트레일링 스탑
    n_open, n_high, n_low, n_close = (
        row["next_open"], row["next_high"],
        row["next_low"],  row["next_close"],
    )
    if n_open <= stop:
        exit_p, reason = n_open, "갭손절"
    else:
        trail = max(n_open, n_high) - atr * cfg["trailing_mult"]
        if n_low <= trail:
            exit_p, reason = max(trail, stop), "트레일링"
        elif n_high >= min_tp:
            exit_p, reason = min_tp, "목표익절"
        else:
            exit_p, reason = n_close, "익일종가"

    pnl_pct = (exit_p - entry) / entry
    return Trade(
        market=row["market"], date=str(row["datetime"].date()),
        strategy="VB", entry=round(entry, 4),
        exit_price=round(exit_p, 4), size=round(size),
        pnl_pct=round(pnl_pct * 100, 3),
        pnl=round(size * pnl_pct), exit_reason=reason,
    )


def sim_trend_trade(df: pd.DataFrame, idx: int, cfg: dict,
                    size_mult: float = 1.0) -> Trade | None:
    """추세 추종 트레이드 — 골든크로스 진입, EMA 역전 청산"""
    row = df.iloc[idx]
    if not row["trend_signal"] or pd.isna(row["next_open"]):
        return None

    entry    = row["next_open"]   # 다음날 시가 진입
    atr      = row["atr"]
    stop     = entry - atr * cfg["atr_stop_mult"] * 1.5   # 추세 전략은 손절 여유 더 줌
    stop_pct = (entry - stop) / entry

    risk_amt = CONFIG["capital"] * cfg["risk_per_trade"] * size_mult
    size     = min(risk_amt / stop_pct, CONFIG["capital"] * 0.35)

    # 추세가 꺾이거나 손절까지 보유
    exit_p, reason = entry, "미청산"
    for j in range(idx + 2, min(idx + 20, len(df))):  # 최대 20일 보유
        r = df.iloc[j]
        # 손절
        if r["low"] <= stop:
            exit_p, reason = stop, "손절"
            break
        # 추세 역전 (데드크로스)
        if not r["trend_bull"]:
            exit_p, reason = r["open"], "추세역전"
            break
        # 트레일링 스탑 갱신
        new_stop = r["close"] - atr * cfg["trailing_mult"] * 1.5
        stop = max(stop, new_stop)
    else:
        exit_p = df.iloc[min(idx + 20, len(df) - 1)]["close"]
        reason = "기간종료"

    pnl_pct = (exit_p - entry) / entry
    return Trade(
        market=row["market"], date=str(row["datetime"].date()),
        strategy="TREND", entry=round(entry, 4),
        exit_price=round(exit_p, 4), size=round(size),
        pnl_pct=round(pnl_pct * 100, 3),
        pnl=round(size * pnl_pct), exit_reason=reason,
    )


# ─────────────────────────────────────────
#  백테스트 엔진
# ─────────────────────────────────────────
def run_backtest(market: str, df: pd.DataFrame, cfg: dict) -> list[Trade]:
    df = df.copy()
    df["market"] = market
    trades = []

    for i, row in df.iterrows():
        vb_sig    = row["vb_signal"]
        tr_sig    = row["trend_signal"]
        combo     = vb_sig and tr_sig

        # 변동성 돌파
        if vb_sig:
            mult = cfg["combo_size_mult"] if combo else 1.0
            t = sim_vb_trade(row, cfg, mult)
            if t:
                t.strategy = "COMBO" if combo else "VB"
                trades.append(t)

        # 추세 추종 (콤보가 아닐 때만 — 중복 방지)
        if tr_sig and not combo:
            t = sim_trend_trade(df, i, cfg, 1.0)
            if t:
                trades.append(t)

    return trades


# ─────────────────────────────────────────
#  성과 분석
# ─────────────────────────────────────────
def analyze(trades: list[Trade], capital: float) -> dict:
    if len(trades) < 5:
        return {"error": f"거래 {len(trades)}회 — 부족"}

    df = pd.DataFrame([t.__dict__ for t in trades])
    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    wr     = len(wins) / len(df) * 100
    aw     = wins["pnl_pct"].mean()   if len(wins)   > 0 else 0
    al     = losses["pnl_pct"].mean() if len(losses) > 0 else 0
    rr     = abs(aw / al) if al != 0 else 0
    pf     = wins["pnl"].sum() / abs(losses["pnl"].sum()) \
             if losses["pnl"].sum() != 0 else 99

    equity = capital + df["pnl"].cumsum()
    max_dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

    daily  = df["pnl_pct"] / 100
    sharpe = (daily.mean() - 0.03/252) / daily.std() * np.sqrt(252) \
             if daily.std() > 0 else 0

    # 전략별 분석
    by_strat = {}
    for s in ["VB", "TREND", "COMBO"]:
        sub = df[df["strategy"] == s]
        if len(sub) > 0:
            by_strat[s] = {
                "거래수": len(sub),
                "승률":   round(len(sub[sub["pnl"] > 0]) / len(sub) * 100, 1),
                "손익비": round(abs(sub[sub["pnl"]>0]["pnl_pct"].mean() /
                               sub[sub["pnl"]<=0]["pnl_pct"].mean())
                               if sub[sub["pnl"]<=0]["pnl_pct"].mean() != 0 else 0, 2),
                "총손익": round(sub["pnl"].sum()),
            }

    return {
        "총거래수":    len(df),
        "승률(%)":     round(wr, 1),
        "손익비":      round(rr, 2),
        "샤프비율":    round(sharpe, 2),
        "최대DD(%)":   round(max_dd, 2),
        "프로핏팩터":  round(pf, 2),
        "평균수익(%)": round(aw, 2),
        "평균손실(%)": round(al, 2),
        "총손익(원)":  round(df["pnl"].sum()),
        "총수익률(%)": round(df["pnl"].sum() / capital * 100, 2),
        "전략별":      by_strat,
        "청산사유":    df["exit_reason"].value_counts().to_dict(),
    }


def check_pass(r: dict) -> bool:
    return (
        r.get("손익비", 0)        >= 1.5
        and r.get("승률(%)", 0)   >= 40
        and r.get("샤프비율", 0)  >= 0.8
        and r.get("최대DD(%)", -999) >= -20
        and r.get("총수익률(%)", 0)  >  0
        and r.get("총거래수", 0)  >= 15
    )


def print_report(market: str, r: dict):
    if "error" in r:
        print(f"\n  {market}: {r['error']}")
        return
    status = "✅ 통과" if check_pass(r) else "❌ 미달"
    print(f"\n{'='*55}")
    print(f"  {market}  {status}")
    print(f"{'='*55}")
    print(f"  총 거래수    : {r['총거래수']}회")
    print(f"  승률         : {r['승률(%)']}%   (기준 ≥40%)")
    print(f"  손익비       : {r['손익비']}   (기준 ≥1.5)")
    print(f"  샤프비율     : {r['샤프비율']}   (기준 ≥0.8)")
    print(f"  최대DD       : {r['최대DD(%)']}%  (기준 ≥-20%)")
    print(f"  총수익률     : {r['총수익률(%)']}%")
    print(f"  총손익       : {r['총손익(원)']:,}원")
    print(f"  프로핏팩터   : {r['프로핏팩터']}")
    if r.get("전략별"):
        print(f"\n  전략별 성과:")
        for s, v in r["전략별"].items():
            print(f"    [{s}] 거래{v['거래수']}회 | 승률{v['승률']}% | "
                  f"손익비{v['손익비']} | 손익{v['총손익']:,}원")


# ─────────────────────────────────────────
#  메인
# ─────────────────────────────────────────
def main():
    cfg   = CONFIG
    count = cfg["backtest_days"] + 60

    print("=" * 55)
    print("  코인 전략 v4 — 변동성돌파 + 추세추종 조합")
    print(f"  기간: {cfg['backtest_days']}일 / 일봉")
    print(f"  VB K={cfg['k']} | EMA {cfg['ema_fast']}/{cfg['ema_slow']} | ADX≥{cfg['adx_threshold']}")
    print(f"  초기 자본: {cfg['capital']:,}원")
    print("=" * 55)

    all_results = {}
    passed_list = []

    for market in cfg["markets"]:
        print(f"\n[{market}] 데이터 수집 중...")
        df = fetch_daily(market, count)
        if df.empty:
            print("  데이터 없음")
            continue
        print(f"  {len(df)}개 / "
              f"{df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[-1].date()}")

        df = add_indicators(df, cfg)
        trades = run_backtest(market, df, cfg)
        result = analyze(trades, cfg["capital"])
        print_report(market, result)
        all_results[market] = result

        if "error" not in result and check_pass(result):
            passed_list.append({
                "market":  market,
                "손익비":  result["손익비"],
                "샤프":    result["샤프비율"],
                "승률":    result["승률(%)"],
                "수익률":  result["총수익률(%)"],
            })

    # 최종 요약
    print(f"\n\n{'='*55}")
    print("  최종 결과")
    print(f"{'='*55}")
    if passed_list:
        passed_list.sort(key=lambda x: x["손익비"] * x["샤프"], reverse=True)
        print("\n  ✅ 실전 투입 가능 코인:")
        for c in passed_list:
            print(f"    {c['market']} | 손익비 {c['손익비']} | "
                  f"샤프 {c['샤프']} | 승률 {c['승률']}% | 수익률 {c['수익률']}%")
        print("\n  → 모의투자 단계로 진입 준비 완료")
    else:
        print("\n  통과 코인 없음 — 전 코인 최고 손익비:")
        valid = {k: v for k, v in all_results.items() if "error" not in v}
        if valid:
            best_m = max(valid, key=lambda k: valid[k].get("손익비", 0))
            r = valid[best_m]
            print(f"    {best_m} | 손익비 {r['손익비']} | "
                  f"샤프 {r['샤프비율']} | 수익률 {r['총수익률(%)']}%")

    with open("coin_result_v4.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print("\n  결과 저장: coin_result_v4.json")


if __name__ == "__main__":
    main()
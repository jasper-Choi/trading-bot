"""
코인 전략 v1 — 3개 전략 동시 비교 백테스트
목적: 현행 시스템 완전 재설계를 위한 전략 방향 결정

[Strategy A] 60분봉 신고점 돌파
  진입: close > 20봉 최고가 + 거래량 3x + RSI 55-78 + close > EMA20
  청산: TP +5% / SL -2% / trail(3%발동, -2.5%) / max 36h

[Strategy B] 일봉 신고점 돌파 (Korea B 코인 버전)
  진입: close > 30일 최고가 + 거래량 2x + RSI 55-80 + close > EMA20
  청산: TP +8% / SL -3% / trail(5%발동, -3.5%) / max 10일

[Strategy C] 60분봉 추세 눌림목
  진입: close > EMA60 (상승 추세) + RSI 35-50 (눌림목)
  청산: TP +4% / SL -2% / trail(2.5%발동, -2%) / max 24h

유니버스: 업비트 KRW 유동성 상위 13개 코인
기간: 가능한 최대 (Upbit API 분봉 최대 4400봉 = 약 6개월/60분봉, 일봉은 500일)
비용: 수수료 0.05% + 슬리피지 0.05% (왕복 0.20%)
포지션: 고정 100만원 (컴파운딩 없음)
"""

import requests
import pandas as pd
import numpy as np
import time
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ──────────────────────────────────────────────────────────────────
#  유니버스 & 공통 설정
# ──────────────────────────────────────────────────────────────────
MARKETS = [
    "KRW-BTC",  "KRW-ETH",  "KRW-XRP",  "KRW-SOL",
    "KRW-DOGE", "KRW-ADA",  "KRW-AVAX", "KRW-LINK",
    "KRW-DOT",  "KRW-ATOM", "KRW-NEAR", "KRW-UNI",
    "KRW-SAND",
]

COMMISSION  = 0.0005
SLIPPAGE    = 0.0005
ROUND_TRIP  = (COMMISSION + SLIPPAGE) * 2
FIXED_POS   = 1_000_000   # 고정 포지션 크기 (원)

# 전략 설정
CFG_A = {
    "name":            "A_60m_breakout",
    "candle_min":      60,
    "fetch_count":     4400,       # ~6개월
    "breakout_period": 20,         # 20봉(20시간) 최고가
    "vol_period":      24,         # 거래량 기준봉 수
    "vol_mult":        3.0,        # 거래량 배수
    "rsi_period":      14,
    "rsi_min":         55.0,
    "rsi_max":         78.0,
    "ema_period":      20,
    "tp_pct":          0.050,      # +5%
    "sl_pct":          0.020,      # -2%
    "trail_trigger":   0.030,      # +3% 이상에서 트레일 발동
    "trail_giveback":  0.025,      # 고점 대비 -2.5%
    "max_hold_bars":   36,         # 36봉 = 36시간
}

CFG_B = {
    "name":            "B_daily_breakout",
    "candle_min":      "day",
    "fetch_count":     500,        # ~500일
    "breakout_period": 30,         # 30일 최고가
    "vol_period":      20,         # 거래량 기준봉 수
    "vol_mult":        2.0,        # 거래량 배수
    "rsi_period":      14,
    "rsi_min":         55.0,
    "rsi_max":         80.0,
    "ema_period":      20,
    "tp_pct":          0.080,      # +8%
    "sl_pct":          0.030,      # -3%
    "trail_trigger":   0.050,      # +5% 이상에서 트레일 발동
    "trail_giveback":  0.035,      # 고점 대비 -3.5%
    "max_hold_bars":   10,         # 10일
}

CFG_C = {
    "name":            "C_60m_pullback",
    "candle_min":      60,
    "fetch_count":     4400,       # ~6개월
    "breakout_period": None,       # 미사용
    "trend_period":    60,         # 추세 판단 EMA 기간
    "rsi_period":      14,
    "rsi_min":         35.0,       # 눌림목 RSI 하한
    "rsi_max":         50.0,       # 눌림목 RSI 상한
    "ema_period":      60,
    "tp_pct":          0.040,      # +4%
    "sl_pct":          0.020,      # -2%
    "trail_trigger":   0.025,      # +2.5% 이상에서 트레일 발동
    "trail_giveback":  0.020,      # 고점 대비 -2%
    "max_hold_bars":   24,         # 24봉 = 24시간
}


# ──────────────────────────────────────────────────────────────────
#  데이터 수집
# ──────────────────────────────────────────────────────────────────
def fetch_ohlcv(market: str, cfg: dict) -> pd.DataFrame:
    """업비트 OHLCV 수집"""
    candle_min = cfg["candle_min"]
    count = cfg["fetch_count"]

    if candle_min == "day":
        url = "https://api.upbit.com/v1/candles/days"
    else:
        url = f"https://api.upbit.com/v1/candles/minutes/{candle_min}"

    all_candles: list = []
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
                    break
                all_candles.extend(candles)
                to = candles[-1]["candle_date_time_utc"]
                time.sleep(0.12)
                break
            except Exception as e:
                time.sleep(1.0)
        else:
            break
        if len(candles) < need:
            break

    if not all_candles:
        return pd.DataFrame()

    key_dt = "candle_date_time_kst"
    df = pd.DataFrame(all_candles).rename(columns={
        key_dt:                      "dt",
        "opening_price":             "open",
        "high_price":                "high",
        "low_price":                 "low",
        "trade_price":               "close",
        "candle_acc_trade_volume":   "volume",
    })
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.sort_values("dt")[["dt", "open", "high", "low", "close", "volume"]]
    return df.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────
#  지표
# ──────────────────────────────────────────────────────────────────
def calc_rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_signals_A(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Strategy A: 60분봉 신고점 돌파"""
    df = df.copy()
    df["vol_ma"] = df["volume"].rolling(cfg["vol_period"]).mean()
    df["vol_surge"] = df["volume"] > df["vol_ma"] * cfg["vol_mult"]
    df["high_max"] = df["close"].shift(1).rolling(cfg["breakout_period"]).max()
    df["breakout"] = df["close"] > df["high_max"]
    df["rsi"] = calc_rsi(df["close"], cfg["rsi_period"])
    df["rsi_ok"] = (df["rsi"] >= cfg["rsi_min"]) & (df["rsi"] <= cfg["rsi_max"])
    df["ema"] = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"] = df["close"] > df["ema"]
    df["signal"] = df["vol_surge"] & df["breakout"] & df["rsi_ok"] & df["above_ema"]
    return df.dropna().reset_index(drop=True)


def add_signals_B(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Strategy B: 일봉 신고점 돌파"""
    df = df.copy()
    df["vol_ma"] = df["volume"].rolling(cfg["vol_period"]).mean()
    df["vol_surge"] = df["volume"] > df["vol_ma"] * cfg["vol_mult"]
    df["high_max"] = df["close"].shift(1).rolling(cfg["breakout_period"]).max()
    df["breakout"] = df["close"] > df["high_max"]
    df["rsi"] = calc_rsi(df["close"], cfg["rsi_period"])
    df["rsi_ok"] = (df["rsi"] >= cfg["rsi_min"]) & (df["rsi"] <= cfg["rsi_max"])
    df["ema"] = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"] = df["close"] > df["ema"]
    df["signal"] = df["vol_surge"] & df["breakout"] & df["rsi_ok"] & df["above_ema"]
    return df.dropna().reset_index(drop=True)


def add_signals_C(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Strategy C: 60분봉 추세 눌림목"""
    df = df.copy()
    df["ema_long"] = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["uptrend"] = df["close"] > df["ema_long"]
    df["rsi"] = calc_rsi(df["close"], cfg["rsi_period"])
    df["rsi_pullback"] = (df["rsi"] >= cfg["rsi_min"]) & (df["rsi"] <= cfg["rsi_max"])
    # 이전봉도 눌림 구간에 있어야 (단순 반등이 아닌 지속 눌림)
    df["rsi_prev"] = df["rsi"].shift(1)
    df["signal"] = df["uptrend"] & df["rsi_pullback"] & (df["rsi_prev"] <= cfg["rsi_max"] + 5)
    return df.dropna().reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────
#  백테스트 시뮬레이션
# ──────────────────────────────────────────────────────────────────
def simulate(df: pd.DataFrame, cfg: dict) -> dict:
    """단일 코인 단일 전략 시뮬레이션"""
    trades = []
    pos = None   # dict: entry_price, peak_price, bars_held, entry_idx

    tp   = cfg["tp_pct"]
    sl   = cfg["sl_pct"]
    trig = cfg["trail_trigger"]
    give = cfg["trail_giveback"]
    maxb = cfg["max_hold_bars"]

    for i in range(len(df)):
        row = df.iloc[i]
        close = float(row["close"])

        if pos is None:
            if row.get("signal", False):
                pos = {
                    "entry_price": close * (1 + SLIPPAGE),
                    "peak_price":  close,
                    "bars_held":   0,
                    "entry_idx":   i,
                }
            continue

        # 포지션 보유 중
        ep = pos["entry_price"]
        peak = pos["peak_price"]
        pnl_pct = (close - ep) / ep

        # 피크 업데이트
        if close > peak:
            pos["peak_price"] = close
            peak = close

        peak_pnl = (peak - ep) / ep
        pos["bars_held"] += 1

        exit_reason = None
        exit_price  = close

        # 1) TP
        if pnl_pct >= tp:
            exit_reason = "tp"
        # 2) SL
        elif pnl_pct <= -sl:
            exit_reason = "sl"
        # 3) 트레일링
        elif peak_pnl >= trig:
            trail_floor = peak * (1 - give)
            if close <= trail_floor:
                exit_reason = "trail"
        # 4) 시간 손절
        elif pos["bars_held"] >= maxb:
            exit_reason = "time"

        if exit_reason:
            actual_exit = exit_price * (1 - SLIPPAGE)
            net_pnl_pct = (actual_exit - ep) / ep - COMMISSION * 2  # 진입+청산 수수료
            pnl_krw = FIXED_POS * net_pnl_pct
            trades.append({
                "pnl_pct":    net_pnl_pct,
                "pnl_krw":    pnl_krw,
                "won":        net_pnl_pct > 0,
                "reason":     exit_reason,
                "bars_held":  pos["bars_held"],
                "entry_idx":  pos["entry_idx"],
                "exit_idx":   i,
            })
            pos = None

    return _summarize(trades)


def _summarize(trades: list) -> dict:
    if not trades:
        return {"trades": 0, "wr": 0.0, "avg_pnl": 0.0, "sharpe": 0.0,
                "mdd": 0.0, "total_pnl_pct": 0.0, "total_krw": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "reasons": {}}

    n      = len(trades)
    pnls   = np.array([t["pnl_pct"] for t in trades])
    wins   = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr     = float(np.mean(pnls > 0)) * 100
    avg    = float(np.mean(pnls))
    total  = float(np.sum(pnls))
    total_krw = float(sum(t["pnl_krw"] for t in trades))

    # 샤프 (일별 환산 — 60분봉은 24봉/일)
    sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(n)) if np.std(pnls) > 0 else 0.0

    # MDD (누적 자산 기준)
    equity = np.cumsum([t["pnl_krw"] for t in trades])
    peak_e = np.maximum.accumulate(equity)
    dd = (equity - peak_e) / (FIXED_POS + peak_e) * 100
    mdd = float(np.min(dd)) if len(dd) > 0 else 0.0

    # 이유별
    reasons: dict = {}
    for t in trades:
        r = t["reason"]
        reasons.setdefault(r, {"cnt": 0, "pnl": 0.0})
        reasons[r]["cnt"] += 1
        reasons[r]["pnl"] += t["pnl_pct"]

    return {
        "trades":        n,
        "wr":            round(wr, 1),
        "avg_pnl":       round(avg * 100, 3),
        "total_pnl_pct": round(total * 100, 2),
        "total_krw":     round(total_krw),
        "sharpe":        round(sharpe, 2),
        "mdd":           round(mdd, 2),
        "avg_win":       round(float(np.mean(wins) * 100), 3) if len(wins) else 0.0,
        "avg_loss":      round(float(np.mean(losses) * 100), 3) if len(losses) else 0.0,
        "reasons":       reasons,
    }


# ──────────────────────────────────────────────────────────────────
#  메인 실행
# ──────────────────────────────────────────────────────────────────
def run_strategy(cfg: dict, label: str) -> dict:
    all_trades_pnl = []
    all_trades_krw = []
    results = {}

    print(f"\n{'='*60}")
    print(f"  Strategy {label}: {cfg['name']}")
    print(f"{'='*60}")

    for market in MARKETS:
        sys.stdout.write(f"  {market}... ")
        sys.stdout.flush()

        df = fetch_ohlcv(market, cfg)
        if df.empty or len(df) < 100:
            print("skip (데이터 부족)")
            continue

        # 신호 추가
        if label == "A":
            df = add_signals_A(df, cfg)
        elif label == "B":
            df = add_signals_B(df, cfg)
        else:
            df = add_signals_C(df, cfg)

        if df.empty:
            print("skip (신호 계산 실패)")
            continue

        res = simulate(df, cfg)
        results[market] = res
        all_trades_pnl.extend([res["total_pnl_pct"]] if res["trades"] > 0 else [])
        all_trades_krw.append(res["total_krw"])

        # 코인별 요약
        t = res["trades"]
        wr = res["wr"]
        avg = res["avg_pnl"]
        sh = res["sharpe"]
        mdd = res["mdd"]
        flag = "[PASS]" if (sh >= 1.0 and wr >= 45 and mdd >= -15) else "[FAIL]"
        print(f"{flag} n={t:>3} WR={wr:>5}% avg={avg:>+5}% Sharpe={sh:>5} MDD={mdd:>+6}%")

    # 전략 전체 집계
    if results:
        all_pnl = [v["avg_pnl"] for v in results.values() if v["trades"] > 0]
        all_wr  = [v["wr"] for v in results.values() if v["trades"] > 0]
        all_sh  = [v["sharpe"] for v in results.values() if v["trades"] > 0]
        all_mdd = [v["mdd"] for v in results.values() if v["trades"] > 0]

        print(f"\n  --- {label} 전체 집계 ({len(all_wr)}개 코인) ---")
        print(f"  평균 WR:     {np.mean(all_wr):>5.1f}%")
        print(f"  평균 avg_pnl:{np.mean(all_pnl):>+6.3f}%/trade")
        print(f"  평균 Sharpe: {np.mean(all_sh):>5.2f}")
        print(f"  평균 MDD:    {np.mean(all_mdd):>+6.2f}%")
        print(f"  총 P&L:      {sum(all_trades_krw):>+,.0f}원")

    return results


def compare_strategies(results_A: dict, results_B: dict, results_C: dict) -> None:
    print(f"\n{'='*60}")
    print("  최종 비교표")
    print(f"{'='*60}")
    print(f"{'코인':<12} {'A_WR':>6} {'A_SH':>6} {'B_WR':>6} {'B_SH':>6} {'C_WR':>6} {'C_SH':>6}")
    print("-" * 60)

    all_markets = set(list(results_A) + list(results_B) + list(results_C))
    summary = {"A": [], "B": [], "C": []}

    for m in sorted(all_markets):
        a = results_A.get(m, {})
        b = results_B.get(m, {})
        c = results_C.get(m, {})
        a_wr = a.get("wr", 0); a_sh = a.get("sharpe", 0)
        b_wr = b.get("wr", 0); b_sh = b.get("sharpe", 0)
        c_wr = c.get("wr", 0); c_sh = c.get("sharpe", 0)
        print(f"{m:<12} {a_wr:>5.1f}% {a_sh:>5.2f}  {b_wr:>5.1f}% {b_sh:>5.2f}  {c_wr:>5.1f}% {c_sh:>5.2f}")
        if a.get("trades", 0) > 0:
            summary["A"].append((a.get("sharpe", 0), a.get("wr", 0), a.get("avg_pnl", 0)))
        if b.get("trades", 0) > 0:
            summary["B"].append((b.get("sharpe", 0), b.get("wr", 0), b.get("avg_pnl", 0)))
        if c.get("trades", 0) > 0:
            summary["C"].append((c.get("sharpe", 0), c.get("wr", 0), c.get("avg_pnl", 0)))

    print(f"\n{'='*60}")
    print("  전략 종합 평가")
    print(f"{'='*60}")
    best = None
    best_score = -999

    for tag, data in summary.items():
        if not data:
            continue
        avg_sharpe = np.mean([d[0] for d in data])
        avg_wr     = np.mean([d[1] for d in data])
        avg_pnl    = np.mean([d[2] for d in data])
        pass_cnt   = sum(1 for d in data if d[0] >= 1.0 and d[1] >= 45)
        score      = avg_sharpe * 0.5 + avg_wr * 0.01 + pass_cnt * 0.3
        print(f"  Strategy {tag}: avg_WR={avg_wr:.1f}%  avg_Sharpe={avg_sharpe:.2f}"
              f"  avg_pnl={avg_pnl:+.3f}%/trade  PASS코인={pass_cnt}/{len(data)}")
        if score > best_score:
            best_score = score
            best = tag

    print(f"\n  [BEST] Strategy {best} 채택 추천")


if __name__ == "__main__":
    print("코인 전략 v1 — 3개 전략 동시 백테스트")
    print(f"유니버스: {len(MARKETS)}개 코인")
    print(f"고정 포지션: {FIXED_POS:,}원 / 왕복 비용: {ROUND_TRIP*100:.2f}%")
    print()

    results_A = run_strategy(CFG_A, "A")
    results_B = run_strategy(CFG_B, "B")
    results_C = run_strategy(CFG_C, "C")

    compare_strategies(results_A, results_B, results_C)

    # 결과 저장
    out = {
        "strategy_A": {k: {kk: float(vv) if isinstance(vv, (np.floating, np.integer)) else vv
                            for kk, vv in v.items()} for k, v in results_A.items()},
        "strategy_B": {k: {kk: float(vv) if isinstance(vv, (np.floating, np.integer)) else vv
                            for kk, vv in v.items()} for k, v in results_B.items()},
        "strategy_C": {k: {kk: float(vv) if isinstance(vv, (np.floating, np.integer)) else vv
                            for kk, vv in v.items()} for k, v in results_C.items()},
    }
    with open("coin_strategy_v1_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n[저장] coin_strategy_v1_result.json")

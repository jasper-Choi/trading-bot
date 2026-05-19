"""
코인 전략 v2 — BTC 레짐 필터 + 4H봉 신고점 돌파
목적: v1 실패 원인(레짐 의존성) 해결

핵심 개선:
1. BTC 레짐 필터: BTC close > EMA200 이면 상승장 → 진입 허용
                  BTC close <= EMA200 이면 하락장 → 전체 진입 차단
2. Universe 축소: BTC, ETH, DOGE, SOL (v1에서 검증된 코인만)
3. 4H봉 (240분봉) 사용: 일봉보다 거래 기회 많고, 60분봉보다 노이즈 적음
4. 파라미터 최적화: lookback 20봉(80H), vol 2.0x, RSI 50-75

전략 D (최종안):
  BTC 레짐 필터:  BTC close > EMA(200봉 기준 단기 = EMA50 on 4H)
  진입 조건 (AND):
    1. BTC 레짐 상승 (BTC 4H close > EMA50)
    2. 4H close > 20봉 최고가 (신고점)
    3. 거래량 > 20봉 평균 × 2.0배
    4. RSI(14) 50 ~ 75 사이
    5. close > EMA20 (단기 추세 확인)
  청산:
    목표: +7% (trail 보호 역할, 실제 청산은 trail이 담당)
    손절: -3%
    트레일: +4% 돌파 후 고점 대비 -3.5% 하락
    시간: 최대 20봉 (80시간)

비용: 왕복 0.20% (수수료 0.05% × 2 + 슬리피지 0.05% × 2)
포지션: 고정 100만원
"""

import requests
import pandas as pd
import numpy as np
import time
import json
import sys
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ──────────────────────────────────────────────────────────────────
#  설정
# ──────────────────────────────────────────────────────────────────
UNIVERSE = ["KRW-BTC", "KRW-ETH", "KRW-DOGE", "KRW-SOL"]

COMMISSION  = 0.0005
SLIPPAGE    = 0.0005
ROUND_TRIP  = (COMMISSION + SLIPPAGE) * 2
FIXED_POS   = 1_000_000

# 4H봉 기준 (240분)
CFG_D = {
    "name":             "D_4H_regime_breakout",
    "candle_min":       240,        # 4시간봉
    "fetch_count":      2200,       # ~365일 (4H × 6봉/일 × 365일)
    "btc_ema_period":   50,         # BTC 레짐 판단 EMA (4H 50봉 = 약 8.3일 = 단기 추세)
    "breakout_period":  20,         # 20봉(80H) 최고가
    "vol_period":       20,
    "vol_mult":         2.0,
    "rsi_period":       14,
    "rsi_min":          50.0,
    "rsi_max":          75.0,
    "ema_period":       20,
    "tp_pct":           0.070,      # +7%
    "sl_pct":           0.030,      # -3%
    "trail_trigger":    0.040,      # +4% 이상에서 트레일 발동
    "trail_giveback":   0.035,      # 고점 대비 -3.5%
    "max_hold_bars":    20,         # 20봉 = 80시간
}

# 파라미터 민감도 테스트용 변형들
PARAM_VARIANTS = [
    {"tag": "D_base",     "vol_mult": 2.0, "rsi_min": 50.0, "rsi_max": 75.0, "sl_pct": 0.030, "trail_trigger": 0.040},
    {"tag": "D_tight_sl", "vol_mult": 2.0, "rsi_min": 50.0, "rsi_max": 75.0, "sl_pct": 0.025, "trail_trigger": 0.040},
    {"tag": "D_wide_rsi", "vol_mult": 2.0, "rsi_min": 45.0, "rsi_max": 78.0, "sl_pct": 0.030, "trail_trigger": 0.040},
    {"tag": "D_hi_vol",   "vol_mult": 2.5, "rsi_min": 50.0, "rsi_max": 75.0, "sl_pct": 0.030, "trail_trigger": 0.040},
    {"tag": "D_lo_vol",   "vol_mult": 1.5, "rsi_min": 50.0, "rsi_max": 75.0, "sl_pct": 0.030, "trail_trigger": 0.040},
]


# ──────────────────────────────────────────────────────────────────
#  데이터 수집
# ──────────────────────────────────────────────────────────────────
_cache: dict = {}

def fetch_ohlcv(market: str, candle_min: int, count: int) -> pd.DataFrame:
    cache_key = f"{market}_{candle_min}_{count}"
    if cache_key in _cache:
        return _cache[cache_key]

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
            except Exception:
                time.sleep(1.0)
        else:
            break
        if len(candles) < need:
            break

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles).rename(columns={
        "candle_date_time_kst":      "dt",
        "opening_price":             "open",
        "high_price":                "high",
        "low_price":                 "low",
        "trade_price":               "close",
        "candle_acc_trade_volume":   "volume",
    })
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.sort_values("dt")[["dt", "open", "high", "low", "close", "volume"]]
    df = df.reset_index(drop=True)
    _cache[cache_key] = df
    return df


# ──────────────────────────────────────────────────────────────────
#  지표
# ──────────────────────────────────────────────────────────────────
def calc_rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def build_df(df_coin: pd.DataFrame, df_btc: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """코인 OHLCV에 BTC 레짐 정보 합산하여 신호 생성"""
    df = df_coin.copy()

    # 거래량 급등
    df["vol_ma"] = df["volume"].rolling(cfg["vol_period"]).mean()
    df["vol_surge"] = df["volume"] > df["vol_ma"] * cfg["vol_mult"]

    # 신고점 돌파
    df["high_max"] = df["close"].shift(1).rolling(cfg["breakout_period"]).max()
    df["breakout"] = df["close"] > df["high_max"]

    # RSI
    df["rsi"] = calc_rsi(df["close"], cfg["rsi_period"])
    df["rsi_ok"] = (df["rsi"] >= cfg["rsi_min"]) & (df["rsi"] <= cfg["rsi_max"])

    # EMA 추세
    df["ema"] = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"] = df["close"] > df["ema"]

    # BTC 레짐 — dt 기준으로 머지
    if df_btc is not None and not df_btc.empty:
        btc = df_btc.copy()
        btc["btc_ema"] = btc["close"].ewm(span=cfg["btc_ema_period"], adjust=False).mean()
        btc["btc_regime"] = btc["close"] > btc["btc_ema"]
        btc_sub = btc[["dt", "btc_regime"]].rename(columns={"dt": "dt"})
        df = pd.merge_asof(
            df.sort_values("dt"),
            btc_sub.sort_values("dt"),
            on="dt",
            direction="backward",
        )
    else:
        df["btc_regime"] = True  # BTC 자신은 레짐 필터 없이

    df["signal"] = (
        df["vol_surge"] &
        df["breakout"] &
        df["rsi_ok"] &
        df["above_ema"] &
        df["btc_regime"]
    )
    return df.dropna(subset=["signal"]).reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────
#  시뮬레이션
# ──────────────────────────────────────────────────────────────────
def simulate(df: pd.DataFrame, cfg: dict) -> dict:
    trades = []
    pos = None

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
                }
            continue

        ep   = pos["entry_price"]
        peak = pos["peak_price"]
        pnl  = (close - ep) / ep
        if close > peak:
            pos["peak_price"] = close
            peak = close
        peak_pnl = (peak - ep) / ep
        pos["bars_held"] += 1

        exit_reason = None
        if pnl >= tp:
            exit_reason = "tp"
        elif pnl <= -sl:
            exit_reason = "sl"
        elif peak_pnl >= trig and close <= peak * (1 - give):
            exit_reason = "trail"
        elif pos["bars_held"] >= maxb:
            exit_reason = "time"

        if exit_reason:
            net = (close * (1 - SLIPPAGE) - ep) / ep - COMMISSION * 2
            trades.append({
                "pnl_pct": net,
                "pnl_krw": FIXED_POS * net,
                "won":     net > 0,
                "reason":  exit_reason,
            })
            pos = None

    return _summarize(trades)


def _summarize(trades: list) -> dict:
    if not trades:
        return {"trades": 0, "wr": 0.0, "avg_pnl": 0.0, "sharpe": 0.0,
                "mdd": 0.0, "total_krw": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    pnls  = np.array([t["pnl_pct"] for t in trades])
    wins  = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    n     = len(trades)
    wr    = float(np.mean(pnls > 0)) * 100
    avg   = float(np.mean(pnls))
    sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(n)) if np.std(pnls) > 0 else 0.0
    equity = np.cumsum([t["pnl_krw"] for t in trades])
    peak_e = np.maximum.accumulate(equity)
    dd = (equity - peak_e) / (FIXED_POS + np.maximum(peak_e, 0)) * 100
    mdd = float(np.min(dd)) if len(dd) else 0.0
    return {
        "trades":    n,
        "wr":        round(wr, 1),
        "avg_pnl":   round(avg * 100, 3),
        "total_krw": round(sum(t["pnl_krw"] for t in trades)),
        "sharpe":    round(sharpe, 2),
        "mdd":       round(mdd, 2),
        "avg_win":   round(float(np.mean(wins) * 100), 3) if len(wins) else 0.0,
        "avg_loss":  round(float(np.mean(losses) * 100), 3) if len(losses) else 0.0,
    }


# ──────────────────────────────────────────────────────────────────
#  레짐 효과 분석 — 레짐 ON/OFF 비교
# ──────────────────────────────────────────────────────────────────
def analyze_regime_effect(market: str, df_btc: pd.DataFrame, cfg: dict) -> None:
    df_raw = fetch_ohlcv(market, cfg["candle_min"], cfg["fetch_count"])
    if df_raw.empty or len(df_raw) < 100:
        return

    # 레짐 필터 ON
    df_on = build_df(df_raw, df_btc, cfg)
    res_on = simulate(df_on, cfg)

    # 레짐 필터 OFF (BTC 레짐 무시)
    cfg_noregime = dict(cfg)
    df_off = build_df(df_raw, None, cfg_noregime)
    res_off = simulate(df_off, cfg_noregime)

    print(f"  {market}")
    print(f"    레짐 OFF: n={res_off['trades']:>3} WR={res_off['wr']:>5}% Sharpe={res_off['sharpe']:>6} MDD={res_off['mdd']:>+7}%")
    print(f"    레짐 ON:  n={res_on['trades']:>3} WR={res_on['wr']:>5}% Sharpe={res_on['sharpe']:>6} MDD={res_on['mdd']:>+7}%")
    filtered = res_off["trades"] - res_on["trades"]
    print(f"    레짐 필터로 제외된 진입: {filtered}건")


# ──────────────────────────────────────────────────────────────────
#  파라미터 민감도 테스트
# ──────────────────────────────────────────────────────────────────
def param_sensitivity(market: str, df_btc: pd.DataFrame, base_cfg: dict) -> None:
    df_raw = fetch_ohlcv(market, base_cfg["candle_min"], base_cfg["fetch_count"])
    if df_raw.empty or len(df_raw) < 100:
        return

    print(f"  {market} 파라미터 민감도:")
    for v in PARAM_VARIANTS:
        cfg = dict(base_cfg)
        cfg.update(v)
        df = build_df(df_raw, df_btc, cfg)
        res = simulate(df, cfg)
        flag = "[OK]" if res["sharpe"] >= 1.0 and res["wr"] >= 45 else "    "
        print(f"    {flag} {v['tag']:<15} n={res['trades']:>3} WR={res['wr']:>5}% "
              f"Sharpe={res['sharpe']:>6} avg={res['avg_pnl']:>+6}% MDD={res['mdd']:>+7}%")


# ──────────────────────────────────────────────────────────────────
#  연도별 분석 (월별 집계)
# ──────────────────────────────────────────────────────────────────
def yearly_breakdown(market: str, df_btc: pd.DataFrame, cfg: dict) -> None:
    df_raw = fetch_ohlcv(market, cfg["candle_min"], cfg["fetch_count"])
    if df_raw.empty or len(df_raw) < 100:
        return

    df = build_df(df_raw, df_btc, cfg)

    # 월별 신호 수 & BTC 레짐 비율
    df["month"] = df["dt"].dt.to_period("M")
    monthly_signals = df.groupby("month")["signal"].sum()
    monthly_regime  = df.groupby("month")["btc_regime"].mean()

    print(f"  {market} 월별 신호/레짐:")
    for m in monthly_signals.index[-12:]:  # 최근 12개월
        sig = monthly_signals.get(m, 0)
        reg = monthly_regime.get(m, 0)
        bar = "#" * int(sig)
        print(f"    {m}: 신호={sig:>3}건  레짐={reg:.0%}  {bar}")


# ──────────────────────────────────────────────────────────────────
#  전체 실행
# ──────────────────────────────────────────────────────────────────
def main():
    print("코인 전략 v2 — BTC 레짐 필터 + 4H 신고점 돌파")
    print(f"유니버스: {UNIVERSE}")
    print(f"왕복 비용: {ROUND_TRIP*100:.2f}%")

    cfg = CFG_D

    # BTC 데이터 수집 (레짐 계산용)
    print("\n[1] BTC 데이터 수집...")
    df_btc = fetch_ohlcv("KRW-BTC", cfg["candle_min"], cfg["fetch_count"])
    if df_btc.empty:
        print("ERROR: BTC 데이터 없음")
        return

    # BTC 레짐 통계
    df_btc["btc_ema"] = df_btc["close"].ewm(span=cfg["btc_ema_period"], adjust=False).mean()
    btc_bull_pct = (df_btc["close"] > df_btc["btc_ema"]).mean() * 100
    print(f"    BTC EMA{cfg['btc_ema_period']} 상승장 비율: {btc_bull_pct:.1f}%")

    # ── 레짐 효과 비교 ──
    print(f"\n{'='*60}")
    print("  [2] 레짐 필터 효과 분석 (ON vs OFF)")
    print(f"{'='*60}")
    for market in UNIVERSE:
        analyze_regime_effect(market, df_btc, cfg)

    # ── 메인 백테스트 ──
    print(f"\n{'='*60}")
    print("  [3] Strategy D 기본 백테스트")
    print(f"{'='*60}")
    results = {}
    total_krw = 0
    for market in UNIVERSE:
        sys.stdout.write(f"  {market}... ")
        sys.stdout.flush()
        df_raw = fetch_ohlcv(market, cfg["candle_min"], cfg["fetch_count"])
        if df_raw.empty or len(df_raw) < 100:
            print("skip")
            continue
        df = build_df(df_raw, df_btc if market != "KRW-BTC" else None, cfg)
        res = simulate(df, cfg)
        results[market] = res
        total_krw += res["total_krw"]
        flag = "[PASS]" if (res["sharpe"] >= 1.0 and res["wr"] >= 45 and res["mdd"] >= -15) else "[FAIL]"
        print(f"{flag} n={res['trades']:>3} WR={res['wr']:>5}% avg={res['avg_pnl']:>+6}%"
              f" Sharpe={res['sharpe']:>6} MDD={res['mdd']:>+7}%"
              f" win={res['avg_win']:>+5}% loss={res['avg_loss']:>+6}%")

    # 집계
    valid = [v for v in results.values() if v["trades"] >= 5]
    if valid:
        avg_wr  = np.mean([v["wr"] for v in valid])
        avg_sh  = np.mean([v["sharpe"] for v in valid])
        avg_pnl = np.mean([v["avg_pnl"] for v in valid])
        avg_mdd = np.mean([v["mdd"] for v in valid])
        pass_cnt = sum(1 for v in valid if v["sharpe"] >= 1.0 and v["wr"] >= 45)
        print(f"\n  집계 ({len(valid)}개 코인, n>=5):")
        print(f"  평균 WR={avg_wr:.1f}%  Sharpe={avg_sh:.2f}  avg_pnl={avg_pnl:+.3f}%  MDD={avg_mdd:.2f}%")
        print(f"  PASS 코인: {pass_cnt}/{len(valid)}  |  총 P&L: {total_krw:+,.0f}원")

    # ── 파라미터 민감도 ──
    print(f"\n{'='*60}")
    print("  [4] 파라미터 민감도 테스트")
    print(f"{'='*60}")
    for market in UNIVERSE:
        param_sensitivity(market, df_btc if market != "KRW-BTC" else None, cfg)

    # ── 월별 분포 ──
    print(f"\n{'='*60}")
    print("  [5] 월별 신호 분포 (최근 12개월)")
    print(f"{'='*60}")
    for market in UNIVERSE[:2]:  # BTC, ETH만
        yearly_breakdown(market, df_btc if market != "KRW-BTC" else None, cfg)

    # ── 슬리피지 스트레스 테스트 ──
    print(f"\n{'='*60}")
    print("  [6] 슬리피지 스트레스 테스트 (Strategy D, 전 코인 합산)")
    print(f"{'='*60}")
    for slip in [0.0005, 0.0010, 0.0020, 0.0030]:
        global SLIPPAGE
        SLIPPAGE = slip
        _cache.clear()  # 슬리피지 변경 시 재계산
        slip_results = []
        for market in UNIVERSE:
            df_raw = fetch_ohlcv(market, cfg["candle_min"], cfg["fetch_count"])
            if df_raw.empty:
                continue
            df = build_df(df_raw, df_btc if market != "KRW-BTC" else None, cfg)
            r = simulate(df, cfg)
            if r["trades"] >= 3:
                slip_results.append(r)
        if slip_results:
            avg_sh_s = np.mean([r["sharpe"] for r in slip_results])
            avg_pnl_s = np.mean([r["avg_pnl"] for r in slip_results])
            flag = "[OK]" if avg_sh_s >= 0.8 else "[NG]"
            print(f"  {flag} 슬리피지 {slip*100:.2f}%:  avg_Sharpe={avg_sh_s:.2f}  avg_pnl={avg_pnl_s:+.3f}%")
    SLIPPAGE = 0.0005  # 원복

    # 결과 저장
    out = {
        "strategy_D": {
            k: {kk: float(vv) if isinstance(vv, (np.floating, np.integer)) else vv
                for kk, vv in v.items()}
            for k, v in results.items()
        }
    }
    with open("coin_strategy_v2_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n[저장] coin_strategy_v2_result.json")

    # 최종 판정
    print(f"\n{'='*60}")
    print("  최종 판정")
    print(f"{'='*60}")
    if valid:
        overall_sharpe = np.mean([v["sharpe"] for v in valid])
        overall_wr     = np.mean([v["wr"] for v in valid])
        overall_pnl    = np.mean([v["avg_pnl"] for v in valid])
        if overall_sharpe >= 1.0 and overall_wr >= 45 and pass_cnt >= 2:
            verdict = "[VALID] Strategy D 실전 적용 가능"
        elif overall_pnl > 0 and pass_cnt >= 1:
            verdict = "[PARTIAL] 조건부 적용 가능 — 파라미터 추가 최적화 필요"
        else:
            verdict = "[REJECT] 재설계 필요"
        print(f"  {verdict}")
        print(f"  PASS 기준: Sharpe>=1.0, WR>=45%, PASS코인>=2개")
        print(f"  실제값:    Sharpe={overall_sharpe:.2f}, WR={overall_wr:.1f}%, PASS={pass_cnt}/{len(valid)}")


if __name__ == "__main__":
    main()

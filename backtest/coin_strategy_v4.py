"""
코인 전략 v4 — 2026Q2 부진 원인 정밀 분석

v3 우려:
  2026Q2: 5건 WR 20% (avg -1.878%, Sharpe -2.03) ← 최근 7주 부진

분석 목표:
  1. Q2 5개 거래 상세 해부 (RSI, vol비율, 결과)
  2. ETH 자체 레짐 필터 효과 (ETH > own EMA100)
  3. BTC/ETH 상대강도 필터 (ETH/BTC 비율 상승 중일 때만)
  4. 최근 3개월 전용 파라미터 최적화
  5. 레짐 조합별 Q2 필터링 효과 비교
"""

import requests
import pandas as pd
import numpy as np
import time
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COMMISSION = 0.0005
SLIPPAGE   = 0.0005
FIXED_POS  = 1_000_000

BASE_CFG = {
    "candle_min":       240,
    "fetch_count":      2200,
    "btc_ema_period":   200,
    "breakout_period":  20,
    "vol_period":       20,
    "vol_mult":         2.5,
    "rsi_period":       14,
    "rsi_min":          50.0,
    "rsi_max":          75.0,
    "ema_period":       20,
    "tp_pct":           0.070,
    "sl_pct":           0.030,
    "trail_trigger":    0.040,
    "trail_giveback":   0.035,
    "max_hold_bars":    20,
}

_cache: dict = {}

def fetch_ohlcv(market: str, candle_min: int, count: int) -> pd.DataFrame:
    key = f"{market}_{candle_min}_{count}"
    if key in _cache:
        return _cache[key]
    url = f"https://api.upbit.com/v1/candles/minutes/{candle_min}"
    all_candles: list = []
    to = None
    while len(all_candles) < count:
        need = min(200, count - len(all_candles))
        params = {"market": market, "count": need}
        if to:
            params["to"] = to
        for _ in range(3):
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
        "candle_date_time_kst":    "dt",
        "opening_price":           "open",
        "high_price":              "high",
        "low_price":               "low",
        "trade_price":             "close",
        "candle_acc_trade_volume": "volume",
    })
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.sort_values("dt")[["dt","open","high","low","close","volume"]].reset_index(drop=True)
    _cache[key] = df
    return df


def calc_rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def build_full_signals(df_coin: pd.DataFrame, df_btc: pd.DataFrame,
                       cfg: dict, regime_mode: str = "btc_ema200") -> pd.DataFrame:
    """
    regime_mode:
      'none'        — 레짐 필터 없음
      'btc_ema200'  — BTC > EMA200 (v3 기본)
      'eth_ema100'  — ETH > 자체 EMA100
      'combined'    — BTC EMA200 AND ETH EMA100
      'eth_btc_ratio' — ETH/BTC 비율 상승 중 (ETH/BTC > EMA20)
    """
    df = df_coin.copy()
    vol_mult = cfg.get("vol_mult", 2.5)

    df["vol_ma"]    = df["volume"].rolling(cfg["vol_period"]).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, np.nan)
    df["vol_surge"] = df["volume"] > df["vol_ma"] * vol_mult
    df["high_max"]  = df["close"].shift(1).rolling(cfg["breakout_period"]).max()
    df["breakout"]  = df["close"] > df["high_max"]
    df["rsi"]       = calc_rsi(df["close"], cfg["rsi_period"])
    df["rsi_ok"]    = (df["rsi"] >= cfg["rsi_min"]) & (df["rsi"] <= cfg["rsi_max"])
    df["ema20"]     = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"] = df["close"] > df["ema20"]
    df["ema100"]    = df["close"].ewm(span=100, adjust=False).mean()
    df["eth_regime"]= df["close"] > df["ema100"]

    # BTC 레짐
    if df_btc is not None and not df_btc.empty:
        btc = df_btc.copy()
        btc["btc_ema200"]  = btc["close"].ewm(span=200, adjust=False).mean()
        btc["btc_r"]       = btc["close"] > btc["btc_ema200"]
        # ETH/BTC 비율
        df_merged = pd.merge_asof(
            df.sort_values("dt"), btc[["dt","close","btc_r"]].rename(columns={"close":"btc_close"}).sort_values("dt"),
            on="dt", direction="backward"
        )
        df_merged["eth_btc_ratio"] = df_merged["close"] / df_merged["btc_close"].replace(0, np.nan)
        df_merged["ratio_ema20"]   = df_merged["eth_btc_ratio"].ewm(span=20, adjust=False).mean()
        df_merged["ratio_rising"]  = df_merged["eth_btc_ratio"] > df_merged["ratio_ema20"]
        df = df_merged.copy()
    else:
        df["btc_r"]       = True
        df["ratio_rising"]= True

    # 레짐 선택
    if regime_mode == "none":
        df["regime"] = True
    elif regime_mode == "btc_ema200":
        df["regime"] = df["btc_r"]
    elif regime_mode == "eth_ema100":
        df["regime"] = df["eth_regime"]
    elif regime_mode == "combined":
        df["regime"] = df["btc_r"] & df["eth_regime"]
    elif regime_mode == "eth_btc_ratio":
        df["regime"] = df["btc_r"] & df["ratio_rising"]
    else:
        df["regime"] = True

    df["signal"] = (
        df["vol_surge"] & df["breakout"] & df["rsi_ok"] &
        df["above_ema"] & df["regime"]
    )
    return df.dropna(subset=["signal"]).reset_index(drop=True)


def simulate_with_detail(df: pd.DataFrame, cfg: dict) -> tuple[dict, list]:
    """상세 거래 기록 포함 시뮬레이션"""
    trades = []
    pos = None

    for i in range(len(df)):
        row   = df.iloc[i]
        close = float(row["close"])
        if pos is None:
            if row.get("signal", False):
                pos = {
                    "ep": close*(1+SLIPPAGE),
                    "peak": close, "bars": 0,
                    "entry_dt": str(row["dt"]),
                    "entry_rsi": float(row.get("rsi", 0)),
                    "vol_ratio": float(row.get("vol_ratio", 0)),
                    "regime": bool(row.get("regime", True)),
                }
            continue

        ep = pos["ep"]; peak = pos["peak"]
        pnl = (close - ep)/ep
        if close > peak:
            pos["peak"] = close; peak = close
        pk_pnl = (peak - ep)/ep
        pos["bars"] += 1

        exit_reason = None
        if pnl >= cfg["tp_pct"]:                              exit_reason = "tp"
        elif pnl <= -cfg["sl_pct"]:                           exit_reason = "sl"
        elif pk_pnl >= cfg["trail_trigger"] and close <= peak*(1-cfg["trail_giveback"]): exit_reason = "trail"
        elif pos["bars"] >= cfg["max_hold_bars"]:             exit_reason = "time"

        if exit_reason:
            net = (close*(1-SLIPPAGE)-ep)/ep - COMMISSION*2
            trades.append({
                "entry_dt":  pos["entry_dt"],
                "exit_dt":   str(row["dt"]),
                "pnl_pct":   round(net*100, 3),
                "pnl_krw":   FIXED_POS*net,
                "won":       net > 0,
                "reason":    exit_reason,
                "entry_rsi": round(pos["entry_rsi"], 1),
                "vol_ratio": round(pos["vol_ratio"], 2),
                "bars_held": pos["bars"],
            })
            pos = None

    return _summarize_detail(trades), trades


def _summarize_detail(trades: list) -> dict:
    if not trades:
        return {"trades":0,"wr":0.0,"avg_pnl":0.0,"sharpe":0.0,"mdd":0.0}
    pnls   = np.array([t["pnl_pct"] for t in trades])
    n      = len(trades)
    sharpe = float(np.mean(pnls)/np.std(pnls)*np.sqrt(n)) if np.std(pnls) > 0 else 0.0
    equity = np.cumsum([t["pnl_krw"] for t in trades])
    pk     = np.maximum.accumulate(equity)
    dd     = (equity - pk)/(FIXED_POS + np.maximum(pk, 0))*100
    return {
        "trades": n,
        "wr":     round(float(np.mean(pnls > 0))*100, 1),
        "avg_pnl":round(float(np.mean(pnls)), 3),
        "sharpe": round(sharpe, 2),
        "mdd":    round(float(np.min(dd)) if len(dd) else 0.0, 2),
    }


def main():
    print("코인 전략 v4 — 2026Q2 부진 원인 분석")
    print()

    print("[데이터 수집]")
    df_btc  = fetch_ohlcv("KRW-BTC",  BASE_CFG["candle_min"], BASE_CFG["fetch_count"])
    df_eth  = fetch_ohlcv("KRW-ETH",  BASE_CFG["candle_min"], BASE_CFG["fetch_count"])

    # ── [1] Q2 5개 거래 상세 해부 ──
    print(f"\n{'='*65}")
    print("  [1] 2026Q2 5개 거래 상세 (v3 기본: BTC EMA200, vol=2.5x)")
    print(f"{'='*65}")
    df_s = build_full_signals(df_eth, df_btc, BASE_CFG, regime_mode="btc_ema200")
    _, all_trades = simulate_with_detail(df_s, BASE_CFG)

    q2_trades = [t for t in all_trades if "2026-0" in t["entry_dt"]
                 and int(t["entry_dt"][5:7]) in (4,5,6)]  # Apr-Jun 2026
    print(f"  2026Q2 거래: {len(q2_trades)}건")
    print(f"  {'진입일':<22} {'PnL':>7} {'결과':<6} {'이유':<7} {'RSI':>5} {'vol배':>6} {'보유봉':>5}")
    print(f"  {'-'*62}")
    for t in q2_trades:
        flag = "WIN" if t["won"] else "LOSS"
        print(f"  {t['entry_dt'][:19]:<22} {t['pnl_pct']:>+6.2f}% {flag:<6} {t['reason']:<7}"
              f" {t['entry_rsi']:>5.1f} {t['vol_ratio']:>6.2f}x {t['bars_held']:>5}봉")

    # 비Q2 거래 비교
    other_trades = [t for t in all_trades if t not in q2_trades]
    q2_wr   = np.mean([t["won"] for t in q2_trades])*100 if q2_trades else 0
    q2_avg  = np.mean([t["pnl_pct"] for t in q2_trades]) if q2_trades else 0
    oth_wr  = np.mean([t["won"] for t in other_trades])*100 if other_trades else 0
    oth_avg = np.mean([t["pnl_pct"] for t in other_trades]) if other_trades else 0
    print(f"\n  비교:")
    print(f"  2026Q2:  WR={q2_wr:.0f}%  avg={q2_avg:+.2f}%  n={len(q2_trades)}")
    print(f"  나머지:  WR={oth_wr:.0f}%  avg={oth_avg:+.2f}%  n={len(other_trades)}")
    print(f"  Q2 vol_ratio 평균: {np.mean([t['vol_ratio'] for t in q2_trades]):.2f}x"
          f"  (나머지: {np.mean([t['vol_ratio'] for t in other_trades]):.2f}x)")
    print(f"  Q2 entry_rsi 평균: {np.mean([t['entry_rsi'] for t in q2_trades]):.1f}"
          f"  (나머지: {np.mean([t['entry_rsi'] for t in other_trades]):.1f})")

    # ── [2] 레짐 조합별 Q2 필터링 효과 ──
    print(f"\n{'='*65}")
    print("  [2] 레짐 필터 조합별 성과 비교")
    print(f"{'='*65}")
    print(f"  {'모드':<20} {'전체':>3}건 {'Q2':>3}건 {'전체WR':>6} {'전체SH':>7} {'Q2WR':>6} {'전체avg':>8}")
    print(f"  {'-'*65}")

    for mode in ["none", "btc_ema200", "eth_ema100", "combined", "eth_btc_ratio"]:
        df_s2 = build_full_signals(df_eth, df_btc, BASE_CFG, regime_mode=mode)
        res, trade_list = simulate_with_detail(df_s2, BASE_CFG)
        q2 = [t for t in trade_list if "2026-0" in t["entry_dt"]
              and int(t["entry_dt"][5:7]) in (4,5,6)]
        q2_wr2 = np.mean([t["won"] for t in q2])*100 if q2 else float("nan")
        print(f"  {mode:<20} {res['trades']:>3}건 {len(q2):>3}건"
              f" {res['wr']:>5.1f}% {res['sharpe']:>7.2f}"
              f" {q2_wr2:>5.1f}%  avg={res['avg_pnl']:>+6.2f}%")

    # ── [3] Q2에서 살아남는 vol_mult 임계값 ──
    print(f"\n{'='*65}")
    print("  [3] vol_mult 레벨별 Q2 필터링 효과 (BTC EMA200 기준)")
    print(f"{'='*65}")
    for vm in [2.0, 2.5, 3.0, 3.5]:
        cfg = dict(BASE_CFG); cfg["vol_mult"] = vm
        df_s3 = build_full_signals(df_eth, df_btc, cfg, regime_mode="btc_ema200")
        res, trade_list = simulate_with_detail(df_s3, cfg)
        q2 = [t for t in trade_list if "2026-0" in t["entry_dt"]
              and int(t["entry_dt"][5:7]) in (4,5,6)]
        q2_wr2 = np.mean([t["won"] for t in q2])*100 if q2 else float("nan")
        flag = "[OK]" if res["sharpe"] >= 1.5 else "    "
        print(f"  {flag} vol={vm:.1f}x: 전체={res['trades']:>3}건 Sharpe={res['sharpe']:>5} WR={res['wr']:>5}% "
              f"| Q2={len(q2):>2}건 WR={q2_wr2:>5.1f}%")

    # ── [4] 최근 6개월 vs 전체 기간 안정성 ──
    print(f"\n{'='*65}")
    print("  [4] 시기별 월별 WR 트렌드 (BTC EMA200, vol=2.5x)")
    print(f"{'='*65}")
    df_s4 = build_full_signals(df_eth, df_btc, BASE_CFG, regime_mode="btc_ema200")
    _, trade_list4 = simulate_with_detail(df_s4, BASE_CFG)

    by_month: dict = {}
    for t in trade_list4:
        m = t["entry_dt"][:7]
        by_month.setdefault(m, []).append(t)

    print(f"  {'월':<8} {'건':>3} {'WR':>6} {'avg':>7} {'누적승'}")
    cumulative_wins = 0; cumulative_n = 0
    for m in sorted(by_month):
        trades_m = by_month[m]
        n_m   = len(trades_m)
        wr_m  = np.mean([t["won"] for t in trades_m])*100
        avg_m = np.mean([t["pnl_pct"] for t in trades_m])
        cumulative_wins += sum(1 for t in trades_m if t["won"])
        cumulative_n    += n_m
        cum_wr = cumulative_wins / cumulative_n * 100
        bar = "#" * sum(1 for t in trades_m if t["won"]) + "." * sum(1 for t in trades_m if not t["won"])
        print(f"  {m:<8} {n_m:>3}건 {wr_m:>5.0f}% {avg_m:>+6.2f}%  {bar}  (누적WR {cum_wr:.0f}%)")

    # ── [5] 2026Q2 부진 원인 결론 + 최종 추천 파라미터 ──
    print(f"\n{'='*65}")
    print("  [5] 분석 결론 및 최종 추천")
    print(f"{'='*65}")

    # combined 모드로 전체 + Q2 성과 계산
    cfg_best = dict(BASE_CFG); cfg_best["vol_mult"] = 2.5
    df_comb  = build_full_signals(df_eth, df_btc, cfg_best, regime_mode="combined")
    res_comb, trades_comb = simulate_with_detail(df_comb, cfg_best)
    q2_comb = [t for t in trades_comb if "2026-0" in t["entry_dt"]
               and int(t["entry_dt"][5:7]) in (4,5,6)]
    q2_wr_comb = np.mean([t["won"] for t in q2_comb])*100 if q2_comb else float("nan")

    print(f"  combined (BTC EMA200 + ETH EMA100):")
    print(f"    전체: n={res_comb['trades']} WR={res_comb['wr']}% Sharpe={res_comb['sharpe']} MDD={res_comb['mdd']}%")
    print(f"    2026Q2: n={len(q2_comb)} WR={q2_wr_comb:.0f}%")

    # 슬리피지 최종 확인 (combined)
    print()
    for slip in [0.0005, 0.0010, 0.0020, 0.0030]:
        from copy import deepcopy
        net_list = []
        pos = None
        for i in range(len(df_comb)):
            row = df_comb.iloc[i]
            close = float(row["close"])
            if pos is None:
                if row.get("signal", False):
                    pos = {"ep": close*(1+slip), "peak": close, "bars": 0}
                continue
            ep = pos["ep"]; peak = pos["peak"]
            pnl = (close - ep)/ep
            if close > peak:
                pos["peak"] = close; peak = close
            pk_pnl = (peak - ep)/ep
            pos["bars"] += 1
            exit_reason = None
            if pnl >= cfg_best["tp_pct"]:   exit_reason = "tp"
            elif pnl <= -cfg_best["sl_pct"]: exit_reason = "sl"
            elif pk_pnl >= cfg_best["trail_trigger"] and close <= peak*(1-cfg_best["trail_giveback"]): exit_reason = "trail"
            elif pos["bars"] >= cfg_best["max_hold_bars"]: exit_reason = "time"
            if exit_reason:
                net_list.append((close*(1-slip)-ep)/ep - COMMISSION*2)
                pos = None
        if net_list:
            arr = np.array(net_list)*100
            sh  = float(np.mean(arr)/np.std(arr)*np.sqrt(len(arr))) if np.std(arr) > 0 else 0
            wr  = float(np.mean(arr > 0)*100)
            flag = "[OK]" if sh >= 1.0 else "    "
            print(f"  {flag} combined + slip={slip*100:.2f}%: n={len(net_list)} WR={wr:.1f}% Sharpe={sh:.2f}")

    # 저장
    summary = {
        "v3_eth_base":    {"sharpe": 2.33, "wr": 61.1, "mdd": -7.08, "q2_wr": 20.0},
        "v4_combined":    {"sharpe": res_comb["sharpe"], "wr": res_comb["wr"],
                           "mdd": res_comb["mdd"], "q2_wr": round(q2_wr_comb, 1)},
        "recommendation": "combined" if res_comb["sharpe"] >= 2.0 else "btc_ema200",
    }
    with open("coin_strategy_v4_result.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n[저장] coin_strategy_v4_result.json")


if __name__ == "__main__":
    main()

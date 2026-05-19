"""
코인 전략 v3 — ETH 집중 검증 + BTC 레짐 EMA200 수정
목적: v2에서 유일하게 통과한 ETH를 깊게 검증

v2 발견:
  - ETH vol=2.5x: Sharpe 1.83, WR 54.5%, 22건 (6개월) → 가장 유망
  - BTC EMA50 레짐은 너무 짧아 효과 없음
  - DOGE/SOL 경계선 (0.72, 0.80) → 추가 검증 필요

v3 설계:
  1. BTC 레짐 EMA를 50→200으로 수정 (200봉×4H = 800시간 ≈ 33일 추세)
  2. ETH vol_mult 그리드 테스트: 1.5 / 2.0 / 2.5 / 3.0 / 3.5
  3. BTC 레짐 필터 ON/OFF 비교 (진짜 효과 확인)
  4. ETH 단독 walk-forward (2024H1 IS → 2024H2 OOS → 2025 OOS)
  5. DOGE/SOL 동일 파라미터로 비교
  6. 슬리피지 0.30%까지 스트레스 테스트
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
#  설정
# ──────────────────────────────────────────────────────────────────
COMMISSION = 0.0005
SLIPPAGE   = 0.0005
FIXED_POS  = 1_000_000

BASE_CFG = {
    "candle_min":       240,
    "fetch_count":      2200,      # ~365일
    "btc_ema_period":   200,       # v3 수정: 50→200 (33일 장기 추세)
    "breakout_period":  20,
    "vol_period":       20,
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

# vol_mult 그리드 (ETH 집중 테스트)
VOL_GRID = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

# ──────────────────────────────────────────────────────────────────
#  데이터
# ──────────────────────────────────────────────────────────────────
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


def build_signals(df_coin: pd.DataFrame, df_btc: pd.DataFrame | None,
                  cfg: dict, use_regime: bool = True) -> pd.DataFrame:
    df = df_coin.copy()
    vol_mult = cfg.get("vol_mult", 2.5)

    df["vol_ma"]    = df["volume"].rolling(cfg["vol_period"]).mean()
    df["vol_surge"] = df["volume"] > df["vol_ma"] * vol_mult
    df["high_max"]  = df["close"].shift(1).rolling(cfg["breakout_period"]).max()
    df["breakout"]  = df["close"] > df["high_max"]
    df["rsi"]       = calc_rsi(df["close"], cfg["rsi_period"])
    df["rsi_ok"]    = (df["rsi"] >= cfg["rsi_min"]) & (df["rsi"] <= cfg["rsi_max"])
    df["ema"]       = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"] = df["close"] > df["ema"]

    if use_regime and df_btc is not None and not df_btc.empty:
        btc = df_btc.copy()
        btc["btc_ema_long"] = btc["close"].ewm(span=cfg["btc_ema_period"], adjust=False).mean()
        btc["btc_regime"]   = btc["close"] > btc["btc_ema_long"]
        df = pd.merge_asof(
            df.sort_values("dt"),
            btc[["dt","btc_regime"]].sort_values("dt"),
            on="dt", direction="backward",
        )
    else:
        df["btc_regime"] = True

    df["signal"] = (
        df["vol_surge"] & df["breakout"] & df["rsi_ok"] &
        df["above_ema"] & df["btc_regime"]
    )
    return df.dropna(subset=["signal"]).reset_index(drop=True)


def simulate(df: pd.DataFrame, cfg: dict, slip: float = None) -> dict:
    _slip = slip if slip is not None else SLIPPAGE
    trades = []
    pos = None
    tp   = cfg["tp_pct"]
    sl   = cfg["sl_pct"]
    trig = cfg["trail_trigger"]
    give = cfg["trail_giveback"]
    maxb = cfg["max_hold_bars"]

    for i in range(len(df)):
        row   = df.iloc[i]
        close = float(row["close"])
        if pos is None:
            if row.get("signal", False):
                pos = {"ep": close*(1+_slip), "peak": close, "bars": 0}
            continue
        ep = pos["ep"]; peak = pos["peak"]
        pnl = (close - ep) / ep
        if close > peak:
            pos["peak"] = close; peak = close
        peak_pnl = (peak - ep) / ep
        pos["bars"] += 1

        exit_reason = None
        if pnl >= tp:                                   exit_reason = "tp"
        elif pnl <= -sl:                                exit_reason = "sl"
        elif peak_pnl >= trig and close <= peak*(1-give): exit_reason = "trail"
        elif pos["bars"] >= maxb:                       exit_reason = "time"

        if exit_reason:
            net = (close*(1-_slip) - ep)/ep - COMMISSION*2
            trades.append({"pnl_pct": net, "pnl_krw": FIXED_POS*net,
                           "won": net > 0, "reason": exit_reason})
            pos = None

    return _summarize(trades)


def _summarize(trades: list) -> dict:
    if not trades:
        return {"trades":0,"wr":0.0,"avg_pnl":0.0,"sharpe":0.0,
                "mdd":0.0,"total_krw":0.0,"avg_win":0.0,"avg_loss":0.0}
    pnls   = np.array([t["pnl_pct"] for t in trades])
    wins   = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    n      = len(trades)
    sharpe = float(np.mean(pnls)/np.std(pnls)*np.sqrt(n)) if np.std(pnls) > 0 else 0.0
    equity = np.cumsum([t["pnl_krw"] for t in trades])
    pk     = np.maximum.accumulate(equity)
    dd     = (equity - pk) / (FIXED_POS + np.maximum(pk, 0)) * 100
    return {
        "trades":    n,
        "wr":        round(float(np.mean(pnls>0))*100, 1),
        "avg_pnl":   round(float(np.mean(pnls))*100, 3),
        "total_krw": round(float(sum(t["pnl_krw"] for t in trades))),
        "sharpe":    round(sharpe, 2),
        "mdd":       round(float(np.min(dd)) if len(dd) else 0.0, 2),
        "avg_win":   round(float(np.mean(wins))*100, 3) if len(wins) else 0.0,
        "avg_loss":  round(float(np.mean(losses))*100, 3) if len(losses) else 0.0,
    }


# ──────────────────────────────────────────────────────────────────
#  메인
# ──────────────────────────────────────────────────────────────────
def main():
    print("코인 전략 v3 — ETH 집중 검증 (BTC 레짐 EMA200)")
    print()

    print("[데이터 수집]")
    df_btc = fetch_ohlcv("KRW-BTC", BASE_CFG["candle_min"], BASE_CFG["fetch_count"])
    df_eth = fetch_ohlcv("KRW-ETH", BASE_CFG["candle_min"], BASE_CFG["fetch_count"])
    df_doge= fetch_ohlcv("KRW-DOGE", BASE_CFG["candle_min"], BASE_CFG["fetch_count"])
    df_sol = fetch_ohlcv("KRW-SOL",  BASE_CFG["candle_min"], BASE_CFG["fetch_count"])

    # BTC 레짐 통계
    df_btc_tmp = df_btc.copy()
    df_btc_tmp["ema200"] = df_btc_tmp["close"].ewm(span=200, adjust=False).mean()
    bull_pct = (df_btc_tmp["close"] > df_btc_tmp["ema200"]).mean()*100
    print(f"  BTC EMA200 기준 상승장 비율: {bull_pct:.1f}%")
    df_btc_tmp["ema50"] = df_btc_tmp["close"].ewm(span=50, adjust=False).mean()
    bull_pct50 = (df_btc_tmp["close"] > df_btc_tmp["ema50"]).mean()*100
    print(f"  BTC EMA50  기준 상승장 비율: {bull_pct50:.1f}%")

    # ── [1] ETH vol_mult 그리드 ──
    print(f"\n{'='*60}")
    print("  [1] ETH vol_mult 그리드 테스트 (레짐 ON)")
    print(f"{'='*60}")
    best_vm = None; best_sharpe = -999
    for vm in VOL_GRID:
        cfg = dict(BASE_CFG); cfg["vol_mult"] = vm
        df = build_signals(df_eth, df_btc, cfg, use_regime=True)
        res = simulate(df, cfg)
        flag = "[OK] " if res["sharpe"] >= 1.0 and res["wr"] >= 45 else "     "
        print(f"  {flag} vol={vm:.1f}x  n={res['trades']:>3} WR={res['wr']:>5}%"
              f" avg={res['avg_pnl']:>+6}% Sharpe={res['sharpe']:>6} MDD={res['mdd']:>+7}%")
        if res["sharpe"] > best_sharpe and res["trades"] >= 10:
            best_sharpe = res["sharpe"]; best_vm = vm

    print(f"\n  최적 vol_mult: {best_vm} (Sharpe {best_sharpe:.2f})")

    # ── [2] BTC 레짐 EMA200 vs 없음 비교 ──
    best_vol_cfg = dict(BASE_CFG); best_vol_cfg["vol_mult"] = best_vm or 2.5
    print(f"\n{'='*60}")
    print("  [2] BTC 레짐 필터 효과 (EMA200, vol={})".format(best_vol_cfg["vol_mult"]))
    print(f"{'='*60}")
    for market, df_coin in [("ETH", df_eth), ("DOGE", df_doge), ("SOL", df_sol)]:
        df_off = build_signals(df_coin, None, best_vol_cfg, use_regime=False)
        df_on  = build_signals(df_coin, df_btc, best_vol_cfg, use_regime=True)
        r_off  = simulate(df_off, best_vol_cfg)
        r_on   = simulate(df_on,  best_vol_cfg)
        filtered = r_off["trades"] - r_on["trades"]
        diff_sh  = r_on["sharpe"] - r_off["sharpe"]
        print(f"  KRW-{market}")
        print(f"    레짐 OFF: n={r_off['trades']:>3} WR={r_off['wr']:>5}% Sharpe={r_off['sharpe']:>6} MDD={r_off['mdd']:>+7}%")
        print(f"    레짐 ON:  n={r_on['trades']:>3} WR={r_on['wr']:>5}% Sharpe={r_on['sharpe']:>6} MDD={r_on['mdd']:>+7}%")
        sign = "+" if diff_sh >= 0 else ""
        print(f"    레짐 효과: 제외={filtered}건, Sharpe {sign}{diff_sh:.2f}")

    # ── [3] 구간별 성과 (walk-forward 대용) ──
    print(f"\n{'='*60}")
    print("  [3] ETH 시기별 성과 분석")
    print(f"{'='*60}")
    df_eth_sig = build_signals(df_eth, df_btc, best_vol_cfg, use_regime=True)
    df_eth_sig["quarter"] = df_eth_sig["dt"].dt.to_period("Q")

    for q in sorted(df_eth_sig["quarter"].unique()):
        slice_df = df_eth_sig[df_eth_sig["quarter"] == q].reset_index(drop=True)
        res = simulate(slice_df, best_vol_cfg)
        flag = "[OK]" if res["trades"] >= 3 and res["wr"] >= 40 else "    "
        print(f"  {flag} {q}: n={res['trades']:>2} WR={res['wr']:>5}% avg={res['avg_pnl']:>+6}% Sharpe={res['sharpe']:>6}")

    # ── [4] ETH 레짐별 승률 분해 ──
    print(f"\n{'='*60}")
    print("  [4] ETH 상승장/하락장 진입 승률 분해")
    print(f"{'='*60}")
    df_eth_all = build_signals(df_eth, df_btc, best_vol_cfg, use_regime=False)

    bull_trades = []; bear_trades = []
    pos = None
    for i in range(len(df_eth_all)):
        row = df_eth_all.iloc[i]
        close = float(row["close"])
        regime = bool(row.get("btc_regime", True))
        if pos is None:
            if row.get("signal", False):
                pos = {"ep": close*(1+SLIPPAGE), "peak": close, "bars": 0, "regime": regime}
            continue
        ep = pos["ep"]; peak = pos["peak"]
        pnl = (close - ep)/ep
        if close > peak:
            pos["peak"] = close; peak = close
        pk_pnl = (peak - ep)/ep
        pos["bars"] += 1
        exit_reason = None
        if pnl >= best_vol_cfg["tp_pct"]:            exit_reason="tp"
        elif pnl <= -best_vol_cfg["sl_pct"]:         exit_reason="sl"
        elif pk_pnl >= best_vol_cfg["trail_trigger"] and close <= peak*(1-best_vol_cfg["trail_giveback"]): exit_reason="trail"
        elif pos["bars"] >= best_vol_cfg["max_hold_bars"]: exit_reason="time"
        if exit_reason:
            net = (close*(1-SLIPPAGE)-ep)/ep - COMMISSION*2
            t = {"pnl_pct": net, "pnl_krw": FIXED_POS*net, "won": net>0, "reason": exit_reason}
            if pos["regime"]:
                bull_trades.append(t)
            else:
                bear_trades.append(t)
            pos = None

    r_bull = _summarize(bull_trades)
    r_bear = _summarize(bear_trades)
    print(f"  상승장 진입: n={r_bull['trades']:>3} WR={r_bull['wr']:>5}% avg={r_bull['avg_pnl']:>+6}% Sharpe={r_bull['sharpe']:>6}")
    print(f"  하락장 진입: n={r_bear['trades']:>3} WR={r_bear['wr']:>5}% avg={r_bear['avg_pnl']:>+6}% Sharpe={r_bear['sharpe']:>6}")

    # ── [5] 슬리피지 스트레스 (ETH 최적 파라미터) ──
    print(f"\n{'='*60}")
    print(f"  [5] 슬리피지 스트레스 테스트 (ETH, vol={best_vol_cfg['vol_mult']}x)")
    print(f"{'='*60}")
    df_eth_sig2 = build_signals(df_eth, df_btc, best_vol_cfg, use_regime=True)
    for slip in [0.0005, 0.0010, 0.0015, 0.0020, 0.0030]:
        res = simulate(df_eth_sig2, best_vol_cfg, slip=slip)
        flag = "[OK]" if res["sharpe"] >= 1.0 and res["wr"] >= 45 else "    "
        print(f"  {flag} 슬리피지 {slip*100:.2f}%: n={res['trades']:>3} WR={res['wr']:>5}% avg={res['avg_pnl']:>+6}% Sharpe={res['sharpe']:>6}")

    # ── [6] 3개 코인 최종 비교 (vol 최적값) ──
    print(f"\n{'='*60}")
    print(f"  [6] ETH/DOGE/SOL 최종 비교 (vol={best_vol_cfg['vol_mult']}x, 레짐 ON)")
    print(f"{'='*60}")
    final_results = {}
    for market, df_coin in [("KRW-ETH", df_eth), ("KRW-DOGE", df_doge), ("KRW-SOL", df_sol)]:
        df = build_signals(df_coin, df_btc, best_vol_cfg, use_regime=True)
        res = simulate(df, best_vol_cfg)
        final_results[market] = res
        flag = "[PASS]" if res["sharpe"] >= 1.0 and res["wr"] >= 45 and res["mdd"] >= -15 else "[FAIL]"
        print(f"  {flag} {market}: n={res['trades']:>3} WR={res['wr']:>5}%"
              f" avg={res['avg_pnl']:>+6}% Sharpe={res['sharpe']:>6} MDD={res['mdd']:>+7}%")

    # ── 최종 판정 ──
    pass_cnt = sum(1 for v in final_results.values()
                   if v["sharpe"] >= 1.0 and v["wr"] >= 45 and v["mdd"] >= -15)
    valid = [v for v in final_results.values() if v["trades"] >= 5]
    overall_sharpe = np.mean([v["sharpe"] for v in valid]) if valid else 0.0
    overall_wr     = np.mean([v["wr"] for v in valid]) if valid else 0.0

    print(f"\n{'='*60}")
    print("  최종 판정")
    print(f"{'='*60}")
    eth_res = final_results.get("KRW-ETH", {})
    if eth_res.get("sharpe", 0) >= 1.0 and eth_res.get("wr", 0) >= 45 and eth_res.get("mdd", -99) >= -15:
        if pass_cnt >= 2:
            verdict = "[VALID] ETH + 추가 코인 실전 적용 가능"
        else:
            verdict = "[VALID-ETH] ETH 단독 실전 적용 가능 (추가 코인은 보류)"
    else:
        verdict = "[REJECT] 파라미터 추가 탐색 필요"

    print(f"  {verdict}")
    print(f"  ETH: Sharpe={eth_res.get('sharpe',0):.2f}, WR={eth_res.get('wr',0):.1f}%, MDD={eth_res.get('mdd',0):.2f}%")
    print(f"  PASS 코인: {pass_cnt}/3")

    # 저장
    out = {"best_vol_mult": best_vm, "best_sharpe": round(best_sharpe, 2),
           "final": {k: {kk: float(vv) if isinstance(vv, (np.floating, np.integer)) else vv
                         for kk, vv in v.items()}
                     for k, v in final_results.items()}}
    with open("coin_strategy_v3_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n[저장] coin_strategy_v3_result.json")


if __name__ == "__main__":
    main()

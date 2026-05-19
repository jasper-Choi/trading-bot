"""
워크포워드 백테스트 (Walk-Forward Analysis)
목적: coin_backtest_v5.py 파라미터가 오버핏인지 검증
      향후 Claude/Codex의 파라미터 변경 시 객관적 검증 도구

구조:
  - 학습 윈도우: 3개월 (2160시간)
  - 테스트 윈도우: 1주 (168시간)
  - 슬라이드: 1주 단위 이동
  - 총 데이터: ~6개월 (약 4320봉)

워크포워드 흐름:
  1. 학습 구간에서 그리드서치 → 최적 파라미터 탐색
  2. 테스트 구간에서 최적 파라미터로 OOS(아웃오브샘플) 성과 측정
  3. 오버핏 감지: train_sharpe >> test_sharpe 여부 확인
  4. 파라미터 안정성: 윈도우 간 최적 파라미터 분포 분석

현재 프로덕션 파라미터 (coin_backtest_v5.py CONFIG 기준):
  vol_surge_mult=3.0, breakout_period=20, rsi_min=55, rsi_max=78

오버핏 판단 기준:
  - train_sharpe / test_sharpe > 2.5 → 위험 (강한 오버핏)
  - train_sharpe / test_sharpe > 1.5 → 주의
  - OOS 통과율 < 40% → 불안정한 전략
"""

import requests
import pandas as pd
import numpy as np
import time
import json
import sys
import itertools
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────
#  설정
# ─────────────────────────────────────────────────────────────────
WF_CONFIG = {
    # 테스트 코인
    "markets": [
        "KRW-BTC", "KRW-ETH", "KRW-XRP",
        "KRW-SOL", "KRW-DOGE", "KRW-ADA",
    ],

    # 자본 / 비용
    "capital":          5_000_000,
    "commission":       0.0005,
    "slippage":         0.0005,
    "risk_per_trade":   0.02,
    "max_position_pct": 0.30,

    # 데이터 수집 (약 6개월 = 4320봉)
    "candle_minutes":   60,
    "fetch_count":      4400,

    # 윈도우 구조
    "train_hours":  90 * 24,   # 3개월 = 2160봉
    "test_hours":    7 * 24,   # 1주   = 168봉
    "step_hours":    7 * 24,   # 1주 슬라이드

    # 청산 파라미터 (고정 — 최적화 대상 아님)
    "tp1_pct":          0.040,
    "tp2_pct":          0.070,
    "tp1_exit_ratio":   0.50,
    "stop_pct":         0.020,
    "atr_stop_mult":    2.0,
    "trail_trigger":    0.040,
    "trail_atr_mult":   3.0,
    "max_hold_hours":   48,
    "atr_period":       14,
    "ema_period":       20,
}

# 현재 프로덕션 파라미터 (coin_backtest_v5.py 기준)
PRODUCTION_PARAMS = {
    "vol_surge_mult":  3.0,
    "breakout_period": 20,
    "rsi_min":         55,
    "rsi_max":         78,
    "rsi_period":      14,
}

# 그리드서치 탐색 공간
PARAM_GRID = {
    "vol_surge_mult":  [2.0, 2.5, 3.0, 3.5, 4.0],
    "breakout_period": [12, 15, 20, 25],
    "rsi_min":         [48, 50, 52, 55, 58],
    "rsi_max":         [72, 75, 78, 80],
    "rsi_period":      [14],   # 고정
}

ROUND_TRIP_COST = (WF_CONFIG["commission"] + WF_CONFIG["slippage"]) * 2

# 최소 거래 수 (분석 의미를 갖기 위한 하한)
MIN_TRADES_TRAIN = 8
MIN_TRADES_TEST  = 3

# OOS 통과 기준 (약한 기준: 테스트 구간이 짧아 엄격 기준 적용 어려움)
OOS_PASS_CRITERIA = {
    "sharpe":   0.3,    # OOS 샤프 ≥ 0.3
    "pnl":      0.0,    # OOS 총손익 > 0
    "max_dd": -25.0,    # OOS 최대 낙폭 > -25%
}


# ─────────────────────────────────────────────────────────────────
#  데이터 수집
# ─────────────────────────────────────────────────────────────────
def fetch_ohlcv(market: str, minutes: int = 60, count: int = 500) -> pd.DataFrame:
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


def add_signals(df: pd.DataFrame, params: dict, cfg: dict) -> pd.DataFrame:
    """파라미터화된 시그널 계산 (train/test 슬라이스에 사용)"""
    df = df.copy()

    df["vol_ma24"]  = df["volume"].rolling(24).mean()
    df["vol_surge"] = df["volume"] > df["vol_ma24"] * params["vol_surge_mult"]

    df["high_max"]  = df["close"].shift(1).rolling(params["breakout_period"]).max()
    df["breakout"]  = df["close"] > df["high_max"]

    df["rsi"]       = calc_rsi(df["close"], params["rsi_period"])
    df["rsi_ok"]    = (df["rsi"] >= params["rsi_min"]) & (df["rsi"] <= params["rsi_max"])

    df["ema"]       = df["close"].ewm(span=cfg["ema_period"], adjust=False).mean()
    df["above_ema"] = df["close"] > df["ema"]

    df["atr"]       = calc_atr(df, cfg["atr_period"])

    df["signal"] = (
        df["vol_surge"] &
        df["breakout"] &
        df["rsi_ok"] &
        df["above_ema"]
    )

    return df.dropna().reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────
#  트레이드 시뮬레이션 (v5와 동일 로직)
# ─────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    market:       str
    entry_dt:     pd.Timestamp
    exit_dt:      pd.Timestamp
    entry_price:  float
    exit_price:   float
    size_krw:     float
    pnl_pct:      float
    pnl_krw:      float
    exit_reason:  str
    hold_hours:   float
    rsi_at_entry: float = 0.0
    tp1_taken:    bool  = False


def simulate_trades(df: pd.DataFrame, market: str, cfg: dict, params: dict) -> list:
    trades = []
    n = len(df)
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    size_krw = 0.0
    remaining_ratio = 1.0
    tp1_taken = False
    trail_active = False
    trail_stop = 0.0

    for i in range(1, n - 1):
        row = df.iloc[i]

        if not in_position:
            if not row["signal"]:
                continue

            next_row = df.iloc[i + 1]
            entry_price = next_row["open"] * (1 + cfg["slippage"])
            atr = row["atr"]

            atr_stop_dist   = atr * cfg["atr_stop_mult"]
            fixed_stop_dist = entry_price * cfg["stop_pct"]
            stop_dist = max(atr_stop_dist, fixed_stop_dist)
            stop_dist = min(stop_dist, entry_price * 0.03)

            risk_krw = cfg["capital"] * cfg["risk_per_trade"]
            raw_size = risk_krw / (stop_dist / entry_price)
            size_krw = min(raw_size, cfg["capital"] * cfg["max_position_pct"])

            basic_stop = entry_price - fixed_stop_dist
            atr_stop   = entry_price - atr_stop_dist
            actual_stop = max(basic_stop, atr_stop)

            in_position     = True
            entry_idx       = i + 1
            remaining_ratio = 1.0
            tp1_taken       = False
            trail_active    = False
            trail_stop      = actual_stop
            continue

        row = df.iloc[i]
        elapsed_hours = i - entry_idx

        # 손절
        if row["low"] <= trail_stop:
            exit_p = trail_stop * (1 - cfg["slippage"])
            _close(trades, market, df, entry_idx, i, entry_price, exit_p,
                   size_krw, remaining_ratio, "손절", elapsed_hours,
                   df.iloc[entry_idx]["rsi"], tp1_taken, cfg)
            in_position = False
            continue

        # 1차 익절
        if not tp1_taken and row["high"] >= entry_price * (1 + cfg["tp1_pct"]):
            exit_p = entry_price * (1 + cfg["tp1_pct"]) * (1 - cfg["slippage"])
            partial_pnl = (exit_p - entry_price) / entry_price - ROUND_TRIP_COST
            partial_krw = size_krw * cfg["tp1_exit_ratio"] * partial_pnl
            tp1_taken    = True
            remaining_ratio = 1.0 - cfg["tp1_exit_ratio"]
            trail_active = True
            trail_stop   = max(trail_stop, row["high"] - row["atr"] * cfg["trail_atr_mult"])
            trades.append(Trade(
                market=market,
                entry_dt=df.iloc[entry_idx]["dt"],
                exit_dt=row["dt"],
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

        # 2차 익절
        if tp1_taken and row["high"] >= entry_price * (1 + cfg["tp2_pct"]):
            exit_p = entry_price * (1 + cfg["tp2_pct"]) * (1 - cfg["slippage"])
            _close(trades, market, df, entry_idx, i, entry_price, exit_p,
                   size_krw, remaining_ratio, "2차익절", elapsed_hours,
                   df.iloc[entry_idx]["rsi"], tp1_taken, cfg)
            in_position = False
            continue

        # 트레일링 스탑 갱신
        if trail_active:
            new_trail = row["high"] - row["atr"] * cfg["trail_atr_mult"]
            trail_stop = max(trail_stop, new_trail)

        # 시간 손절
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
        entry_dt=df.iloc[entry_idx]["dt"],
        exit_dt=df.iloc[exit_idx]["dt"],
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
def analyze_trades(trades: list, capital: float, min_trades: int = 3) -> dict:
    """trades 리스트를 분석하여 성과 dict 반환. 거래 수 부족 시 None."""
    if len(trades) < min_trades:
        return None

    df = pd.DataFrame([t.__dict__ for t in trades])

    wins   = df[df["pnl_krw"] > 0]
    losses = df[df["pnl_krw"] <= 0]

    wr    = len(wins) / len(df) * 100
    avg_w = wins["pnl_pct"].mean()   if len(wins)   > 0 else 0.0
    avg_l = losses["pnl_pct"].mean() if len(losses) > 0 else 0.0
    rr    = abs(avg_w / avg_l)       if avg_l != 0  else 0.0

    equity = capital + df["pnl_krw"].cumsum()
    max_dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

    r = df["pnl_pct"] / 100
    sharpe = ((r.mean() - 0.03 / 252) / r.std() * np.sqrt(252)
              if r.std() > 0 else 0.0)

    return {
        "n_trades":  len(df),
        "win_rate":  round(wr, 1),
        "rr":        round(rr, 2),
        "sharpe":    round(sharpe, 2),
        "max_dd":    round(max_dd, 2),
        "pnl_pct":   round(df["pnl_krw"].sum() / capital * 100, 2),
        "pnl_krw":   round(df["pnl_krw"].sum()),
    }


# ─────────────────────────────────────────────────────────────────
#  워크포워드 핵심 로직
# ─────────────────────────────────────────────────────────────────
def run_walk_forward(market: str, raw_df: pd.DataFrame, cfg: dict) -> dict:
    """
    단일 코인에 대해 Walk-Forward Analysis 수행.
    Returns dict with per-window results and summary stats.
    """
    train_h = cfg["train_hours"]
    test_h  = cfg["test_hours"]
    step_h  = cfg["step_hours"]
    capital = cfg["capital"]

    n = len(raw_df)
    if n < train_h + test_h:
        return {"error": f"데이터 부족 ({n}봉, 필요 {train_h + test_h}봉)"}

    # 그리드서치 모든 파라미터 조합 생성
    param_keys   = list(PARAM_GRID.keys())
    param_values = list(PARAM_GRID.values())
    all_combos   = list(itertools.product(*param_values))
    total_combos = len(all_combos)

    print(f"  그리드서치 조합: {total_combos}개")

    # 윈도우 인덱스 계산
    windows = []
    start = 0
    while start + train_h + test_h <= n:
        windows.append({
            "train_start": start,
            "train_end":   start + train_h,
            "test_start":  start + train_h,
            "test_end":    min(start + train_h + test_h, n),
        })
        start += step_h

    if not windows:
        return {"error": "윈도우 생성 실패"}

    print(f"  총 윈도우: {len(windows)}개")

    window_results = []
    best_params_history = []

    for wi, win in enumerate(windows):
        train_df_raw = raw_df.iloc[win["train_start"]:win["train_end"]].reset_index(drop=True)
        test_df_raw  = raw_df.iloc[win["test_start"]:win["test_end"]].reset_index(drop=True)

        train_start_dt = train_df_raw["dt"].iloc[0].strftime("%Y-%m-%d")
        train_end_dt   = train_df_raw["dt"].iloc[-1].strftime("%Y-%m-%d")
        test_start_dt  = test_df_raw["dt"].iloc[0].strftime("%Y-%m-%d")
        test_end_dt    = test_df_raw["dt"].iloc[-1].strftime("%Y-%m-%d")

        # ── 그리드서치 on TRAIN ──
        best_sharpe = -999.0
        best_params = None
        best_train_result = None

        for combo in all_combos:
            params = dict(zip(param_keys, combo))

            # RSI min < max 가드
            if params["rsi_min"] >= params["rsi_max"] - 5:
                continue

            try:
                df_sig = add_signals(train_df_raw, params, cfg)
                trades = simulate_trades(df_sig, market, cfg, params)
                result = analyze_trades(trades, capital, MIN_TRADES_TRAIN)
            except Exception:
                continue

            if result is None:
                continue

            if result["sharpe"] > best_sharpe:
                best_sharpe = result["sharpe"]
                best_params = params.copy()
                best_train_result = result

        # ── OOS 테스트 ──
        oos_result = None
        if best_params is not None:
            try:
                df_test_sig = add_signals(test_df_raw, best_params, cfg)
                test_trades = simulate_trades(df_test_sig, market, cfg, best_params)
                oos_result  = analyze_trades(test_trades, capital, MIN_TRADES_TEST)
            except Exception:
                oos_result = None

        # ── 프로덕션 파라미터로 테스트 구간 성과 ──
        prod_oos_result = None
        try:
            df_prod = add_signals(test_df_raw, PRODUCTION_PARAMS, cfg)
            prod_trades = simulate_trades(df_prod, market, cfg, PRODUCTION_PARAMS)
            prod_oos_result = analyze_trades(prod_trades, capital, 1)
        except Exception:
            prod_oos_result = None

        win_record = {
            "window":          wi + 1,
            "train_period":    f"{train_start_dt} ~ {train_end_dt}",
            "test_period":     f"{test_start_dt} ~ {test_end_dt}",
            "best_params":     best_params,
            "train_result":    best_train_result,
            "oos_result":      oos_result,
            "prod_oos_result": prod_oos_result,
        }
        window_results.append(win_record)

        if best_params:
            best_params_history.append(best_params)

        # 진행 상황 출력
        oos_s = f"{oos_result['sharpe']:+.2f}" if oos_result else "N/A"
        tr_s  = f"{best_train_result['sharpe']:+.2f}" if best_train_result else "N/A"
        oos_pass = "✅" if _oos_passes(oos_result) else ("⚠️" if oos_result else "—")
        print(f"  Win {wi+1:2d}/{len(windows)} | "
              f"Train {train_start_dt}~{train_end_dt} | "
              f"Test {test_start_dt}~{test_end_dt} | "
              f"Train Sharpe={tr_s} | OOS Sharpe={oos_s} {oos_pass}")

    # ── 집계 분석 ──
    summary = _summarize_wf(window_results, best_params_history, capital)

    return {
        "market":         market,
        "total_windows":  len(windows),
        "windows":        window_results,
        "summary":        summary,
    }


def _oos_passes(result: Optional[dict]) -> bool:
    if result is None:
        return False
    return (
        result.get("sharpe", -99)  >= OOS_PASS_CRITERIA["sharpe"]
        and result.get("pnl_krw", -1) > 0
        and result.get("max_dd", -999) >= OOS_PASS_CRITERIA["max_dd"]
    )


def _summarize_wf(window_results: list, params_history: list, capital: float) -> dict:
    """전체 윈도우 결과 집계 및 오버핏 분석"""
    valid_windows = [w for w in window_results
                     if w["train_result"] and w["oos_result"]]

    oos_pass_count = sum(1 for w in valid_windows if _oos_passes(w["oos_result"]))
    total_valid    = len(valid_windows)

    oos_sharpes  = [w["oos_result"]["sharpe"]  for w in valid_windows if w["oos_result"]]
    train_sharpes= [w["train_result"]["sharpe"] for w in valid_windows if w["train_result"]]
    oos_pnls     = [w["oos_result"]["pnl_krw"]  for w in valid_windows if w["oos_result"]]

    avg_train_sharpe = np.mean(train_sharpes) if train_sharpes else 0.0
    avg_oos_sharpe   = np.mean(oos_sharpes)   if oos_sharpes   else 0.0
    avg_oos_pnl      = np.mean(oos_pnls)      if oos_pnls      else 0.0

    # 오버핏 감지
    if avg_oos_sharpe > 0 and avg_train_sharpe > 0:
        overfit_ratio = avg_train_sharpe / avg_oos_sharpe
    elif avg_train_sharpe > 0 and avg_oos_sharpe <= 0:
        overfit_ratio = 99.0
    else:
        overfit_ratio = 1.0

    if overfit_ratio >= 2.5:
        overfit_label = "🔴 강한 오버핏 (파라미터 재검토 필요)"
    elif overfit_ratio >= 1.5:
        overfit_label = "🟡 주의 (경미한 오버핏 가능성)"
    else:
        overfit_label = "🟢 안전 (오버핏 없음)"

    oos_pass_rate = oos_pass_count / total_valid * 100 if total_valid > 0 else 0.0
    if oos_pass_rate >= 60:
        stability_label = "🟢 안정적 전략"
    elif oos_pass_rate >= 40:
        stability_label = "🟡 보통 (조건부 사용 가능)"
    else:
        stability_label = "🔴 불안정 (실전 투입 비권장)"

    # 프로덕션 파라미터 OOS 성과
    prod_sharpes = [w["prod_oos_result"]["sharpe"]
                    for w in window_results if w.get("prod_oos_result")]
    avg_prod_sharpe = np.mean(prod_sharpes) if prod_sharpes else None

    # 파라미터 안정성 분석
    param_stability = _analyze_param_stability(params_history)

    return {
        "total_windows":       len(window_results),
        "valid_windows":       total_valid,
        "oos_pass_count":      oos_pass_count,
        "oos_pass_rate_pct":   round(oos_pass_rate, 1),
        "avg_train_sharpe":    round(avg_train_sharpe, 3),
        "avg_oos_sharpe":      round(avg_oos_sharpe, 3),
        "avg_oos_pnl_krw":     round(avg_oos_pnl),
        "overfit_ratio":       round(overfit_ratio, 2),
        "overfit_label":       overfit_label,
        "stability_label":     stability_label,
        "avg_prod_oos_sharpe": round(avg_prod_sharpe, 3) if avg_prod_sharpe is not None else None,
        "param_stability":     param_stability,
    }


def _analyze_param_stability(params_history: list) -> dict:
    """윈도우별 최적 파라미터 분포 — 안정적으로 선택된 값 파악"""
    if not params_history:
        return {}

    stability = {}
    for key in PARAM_GRID:
        counts = Counter(p[key] for p in params_history if key in p)
        total  = sum(counts.values())
        ranked = sorted(counts.items(), key=lambda x: -x[1])
        top_val, top_cnt = ranked[0] if ranked else (None, 0)
        prod_val = PRODUCTION_PARAMS.get(key)

        stability[key] = {
            "distribution": {str(v): cnt for v, cnt in ranked},
            "most_common":  top_val,
            "most_common_rate_pct": round(top_cnt / total * 100, 1) if total > 0 else 0,
            "production_value": prod_val,
            "production_in_top3": prod_val in [v for v, _ in ranked[:3]],
        }

    return stability


# ─────────────────────────────────────────────────────────────────
#  프로덕션 파라미터 전체 구간 백테스트 비교
# ─────────────────────────────────────────────────────────────────
def run_production_backtest(market: str, raw_df: pd.DataFrame, cfg: dict) -> dict:
    """프로덕션 파라미터로 전체 구간 백테스트 (참조 기준점)"""
    try:
        df_sig = add_signals(raw_df, PRODUCTION_PARAMS, cfg)
        trades = simulate_trades(df_sig, market, cfg, PRODUCTION_PARAMS)
        result = analyze_trades(trades, cfg["capital"], 1)
        return result or {"error": "거래 없음"}
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────
#  출력 포맷
# ─────────────────────────────────────────────────────────────────
def print_market_report(market: str, wf: dict):
    line = "=" * 68
    print(f"\n{line}")
    print(f"  {market}  — Walk-Forward 결과")
    print(line)

    if "error" in wf:
        print(f"  오류: {wf['error']}")
        return

    s = wf["summary"]
    print(f"  총 윈도우     : {wf['total_windows']}개  (유효: {s['valid_windows']}개)")
    print(f"  OOS 통과율    : {s['oos_pass_count']}/{s['valid_windows']} = {s['oos_pass_rate_pct']}%")
    print(f"  Train 샤프    : {s['avg_train_sharpe']:+.3f}  →  OOS 샤프: {s['avg_oos_sharpe']:+.3f}")
    print(f"  오버핏 비율   : {s['overfit_ratio']:.2f}  {s['overfit_label']}")
    print(f"  전략 안정성   : {s['stability_label']}")
    print(f"  OOS 평균 손익 : {s['avg_oos_pnl_krw']:,}원")
    if s.get("avg_prod_oos_sharpe") is not None:
        prod_ok = s['avg_prod_oos_sharpe'] >= 0.3
        tag = "✅" if prod_ok else "⚠️"
        print(f"  프로덕션 파라  OOS 샤프: {s['avg_prod_oos_sharpe']:+.3f} {tag}")

    # 파라미터 안정성
    print(f"\n  ─── 파라미터 안정성 ───")
    for key, info in s.get("param_stability", {}).items():
        dist_str = "  ".join(f"{v}:{c}회" for v, c in list(info["distribution"].items())[:4])
        prod_tag = "✅" if info["production_in_top3"] else "⚠️"
        print(f"  {key:18s} | 최다선택: {str(info['most_common']):4s} "
              f"({info['most_common_rate_pct']}%) | "
              f"현프로덕션: {info['production_value']} {prod_tag}")
        print(f"    분포: {dist_str}")

    # 윈도우별 요약 (최근 5개)
    recent_wins = wf["windows"][-5:]
    print(f"\n  ─── 최근 {len(recent_wins)}개 윈도우 ───")
    for w in recent_wins:
        if not w["oos_result"]:
            oos_str = "  (OOS 거래 없음)"
        else:
            r = w["oos_result"]
            oos_pass = "✅" if _oos_passes(w["oos_result"]) else "❌"
            oos_str = (f"  OOS: n={r['n_trades']:2d} | "
                       f"Sharpe={r['sharpe']:+.2f} | "
                       f"PnL={r['pnl_pct']:+.2f}% | "
                       f"DD={r['max_dd']:.1f}% {oos_pass}")
        bp = w.get("best_params") or {}
        p_str = (f"vsm={bp.get('vol_surge_mult','?')} "
                 f"bp={bp.get('breakout_period','?')} "
                 f"rsi={bp.get('rsi_min','?')}~{bp.get('rsi_max','?')}")
        print(f"  Win {w['window']:2d} {w['test_period']} | {p_str} |{oos_str}")


def print_overall_summary(all_results: dict, prod_full: dict):
    line = "=" * 68
    print(f"\n\n{line}")
    print("  📊  전체 코인 워크포워드 종합 요약")
    print(line)

    print(f"\n  {'코인':12s} | {'OOS통과율':8s} | {'Train샤프':9s} | {'OOS샤프':8s} | "
          f"{'오버핏비율':9s} | {'판정'}")
    print(f"  {'-'*12}-+-{'-'*8}-+-{'-'*9}-+-{'-'*8}-+-{'-'*9}-+-{'-'*20}")

    stable_markets = []
    for market, wf in all_results.items():
        if "error" in wf:
            print(f"  {market:12s} | ERROR: {wf['error']}")
            continue
        s = wf["summary"]
        overfit_icon = (
            "🔴" if s["overfit_ratio"] >= 2.5
            else "🟡" if s["overfit_ratio"] >= 1.5
            else "🟢"
        )
        print(f"  {market:12s} | {s['oos_pass_rate_pct']:7.1f}% | "
              f"{s['avg_train_sharpe']:+8.3f} | "
              f"{s['avg_oos_sharpe']:+7.3f} | "
              f"{s['overfit_ratio']:9.2f}x | "
              f"{overfit_icon} {s['stability_label'][:10]}")

        if s["oos_pass_rate_pct"] >= 50 and s["overfit_ratio"] < 2.0:
            stable_markets.append(market)

    # 프로덕션 파라미터 전체 구간 성과
    print(f"\n  ─── 프로덕션 파라미터 (v5 CONFIG) 전체 구간 성과 ───")
    print(f"  {'코인':12s} | {'거래수':6s} | {'승률':6s} | {'손익비':6s} | "
          f"{'샤프':6s} | {'수익률':8s} | {'최대DD':8s}")
    print(f"  {'-'*80}")
    for market, r in prod_full.items():
        if "error" in r:
            print(f"  {market:12s} | ERROR: {r['error']}")
            continue
        ok = "✅" if (r.get("sharpe", 0) >= 1.0 and r.get("win_rate", 0) >= 45) else "❌"
        print(f"  {market:12s} | {r.get('n_trades',0):6d} | "
              f"{r.get('win_rate',0):5.1f}% | "
              f"{r.get('rr',0):6.2f} | "
              f"{r.get('sharpe',0):+5.2f} | "
              f"{r.get('pnl_pct',0):+7.2f}% | "
              f"{r.get('max_dd',0):+7.2f}% {ok}")

    # 종합 권고
    print(f"\n  ─── 종합 권고 ───")
    print(f"  OOS 안정적 코인 (통과율≥50%, 오버핏비율<2.0): "
          f"{', '.join(stable_markets) if stable_markets else '없음'}")

    # 파라미터 추천
    _print_param_recommendation(all_results)


def _print_param_recommendation(all_results: dict):
    """모든 코인의 파라미터 안정성을 합산하여 추천값 도출"""
    combined_counts = {key: Counter() for key in PARAM_GRID}

    for wf in all_results.values():
        if "error" in wf:
            continue
        for key, info in wf["summary"].get("param_stability", {}).items():
            for val_str, cnt in info["distribution"].items():
                try:
                    val = type(PRODUCTION_PARAMS.get(key, 0))(float(val_str))
                except (ValueError, TypeError):
                    val = val_str
                combined_counts[key][val] += cnt

    print(f"\n  ─── 파라미터 추천 (전 코인 통합) ───")
    print(f"  {'파라미터':20s} | {'현재(프로덕션)':16s} | {'권장값':10s} | {'일치여부':8s}")
    print(f"  {'-'*65}")
    for key, counter in combined_counts.items():
        if not counter:
            continue
        recommended = counter.most_common(1)[0][0]
        prod_val    = PRODUCTION_PARAMS.get(key, "?")
        match       = "✅ 일치" if recommended == prod_val else f"⚠️  추천: {recommended}"
        print(f"  {key:20s} | {str(prod_val):16s} | {str(recommended):10s} | {match}")


# ─────────────────────────────────────────────────────────────────
#  메인
# ─────────────────────────────────────────────────────────────────
def main():
    cfg = WF_CONFIG

    print("=" * 68)
    print("  코인 전략 Walk-Forward Analysis")
    print(f"  학습: {cfg['train_hours']//24}일  /  테스트: {cfg['test_hours']//24}일  /  슬라이드: {cfg['step_hours']//24}일")
    total_combos = 1
    for v in PARAM_GRID.values():
        total_combos *= len(v)
    print(f"  그리드 조합: {total_combos}개  (rsi 범위 검증 후 실제는 조금 적음)")
    print(f"  현재 프로덕션 파라미터: {PRODUCTION_PARAMS}")
    print("=" * 68)

    all_wf_results  = {}
    all_prod_full   = {}

    for market in cfg["markets"]:
        print(f"\n\n{'─'*68}")
        print(f"[{market}] 데이터 수집 중...")
        raw_df = fetch_ohlcv(market, cfg["candle_minutes"], cfg["fetch_count"])

        if raw_df.empty or len(raw_df) < cfg["train_hours"] + cfg["test_hours"]:
            print(f"  데이터 부족 ({len(raw_df)}봉)")
            all_wf_results[market] = {"error": f"데이터 부족 ({len(raw_df)}봉)"}
            continue

        dt_from = raw_df["dt"].iloc[0].strftime("%Y-%m-%d")
        dt_to   = raw_df["dt"].iloc[-1].strftime("%Y-%m-%d")
        print(f"  수집 완료: {len(raw_df)}봉  ({dt_from} ~ {dt_to})")

        # 프로덕션 파라미터 전체 구간 성과
        prod_r = run_production_backtest(market, raw_df, cfg)
        all_prod_full[market] = prod_r

        # Walk-Forward
        print(f"\n  Walk-Forward 분석 시작...")
        wf_result = run_walk_forward(market, raw_df, cfg)
        all_wf_results[market] = wf_result

        # 코인별 리포트
        print_market_report(market, wf_result)

    # 전체 요약
    print_overall_summary(all_wf_results, all_prod_full)

    # JSON 저장
    save_path = "walk_forward_result.json"
    save_data = {
        "production_params": PRODUCTION_PARAMS,
        "param_grid":        {k: list(v) for k, v in PARAM_GRID.items()},
        "wf_config": {
            "train_days": cfg["train_hours"] // 24,
            "test_days":  cfg["test_hours"] // 24,
            "step_days":  cfg["step_hours"] // 24,
        },
        "results": {}
    }
    for market, wf in all_wf_results.items():
        if "error" in wf:
            save_data["results"][market] = {"error": wf["error"]}
            continue
        # windows의 pd.Timestamp를 str로 변환
        windows_serializable = []
        for w in wf["windows"]:
            wc = {k: v for k, v in w.items()}
            windows_serializable.append(wc)
        save_data["results"][market] = {
            "summary":  wf["summary"],
            "windows":  windows_serializable,
            "prod_full": all_prod_full.get(market, {}),
        }

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n\n  💾 결과 저장: {save_path}")
    print("\n  ─── 다음 단계 ───")
    print("  1. OOS 통과율 ≥ 60%  → 전략 실전 투입 가능")
    print("  2. 오버핏 비율 ≥ 2.5x → 파라미터 범위 좁히기 (단순화)")
    print("  3. 권장값 ≠ 프로덕션 → coin_backtest_v5.py CONFIG 업데이트 후 재검증")
    print("  4. 불안정 코인 (OOS < 40%) → 유니버스에서 제외 고려")


if __name__ == "__main__":
    main()

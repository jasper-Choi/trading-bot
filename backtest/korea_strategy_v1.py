"""
Korea Stock Multi-Strategy Backtest v1
=======================================
전략 A: 갭업 모멘텀  (Gap Up Momentum)
전략 B: 신고점 돌파  (New High Breakout)
전략 C: 눌림목 매수  (MA20 Pullback)

테스트 기간: 2022-01-03 ~ 2025-12-31 (약 3년, 약세장 포함)
유니버스:    KOSPI + KOSDAQ 대형/중형주 150개 (시가총액 상위)
자본:        10,000,000 KRW
비용:        수수료 0.015% × 2 + 슬리피지 0.10% × 2 = 0.23% 왕복

통과 기준 (연 30~50% 목표):
  샤프비율 ≥ 1.5  |  승률 ≥ 48%  |  손익비 ≥ 1.8  |  MDD ≤ -20%  |  거래수 ≥ 50

사용법:
  pip install pykrx pandas numpy
  python korea_strategy_v1.py
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

try:
    from pykrx import stock as krx
    PYKRX_OK = True
except ImportError:
    PYKRX_OK = False
    print("[오류] pykrx 없음 → pip install pykrx")

# ----------------------------------------------------------------------
#  설정
# ----------------------------------------------------------------------
CONFIG = {
    "start_date":      "20220103",
    "end_date":        "20251231",
    "initial_capital": 10_000_000,
    "max_positions":   3,           # 동시 최대 포지션
    "position_size":   0.30,        # 포지션당 자본 비율
    "commission":      0.00015,     # 편도 0.015%
    "slippage":        0.0005,      # 편도 0.05%
    "cache_file":      "korea_universe_cache.json",
}

ROUND_TRIP_COST = (CONFIG["commission"] + CONFIG["slippage"]) * 2  # 0.0013

# 전략별 파라미터
STRATEGIES = {
    "A_gap_momentum": {
        "gap_min_pct":   3.0,   # 전일 대비 갭업 최소 %
        "vol_mult":      3.0,   # 전일 대비 거래량 배수
        "vol_ma_period": 20,
        "target_pct":    5.0,   # 익절
        "stop_pct":     -2.5,   # 손절
        "trail_trigger": 3.0,   # 트레일 발동 peak %
        "trail_pct":     2.0,   # 고점 대비 되돌림
        "max_hold_days": 3,     # 갭 모멘텀은 빠른 청산
    },
    "B_new_high": {
        "lookback":      60,    # N일 신고점 (52주=252는 진입 기회 너무 희귀)
        "vol_mult":      2.0,
        "vol_ma_period": 20,
        "rsi_min":       55,    # 모멘텀 확인
        "rsi_max":       80,    # 과매수 회피
        "rsi_period":    14,
        "target_pct":    10.0,
        "stop_pct":      -4.0,
        "trail_trigger": 5.0,
        "trail_pct":     4.0,
        "max_hold_days": 20,
    },
    "C_ma20_pullback": {
        "trend_ma":      60,    # 추세 필터: close > MA60
        "pullback_days": 2,     # 연속 하락 후 반등
        "vol_ma_period": 20,
        "rsi_max":       60,    # 과매수 아닐 때 진입
        "rsi_period":    14,
        "target_pct":    5.0,
        "stop_pct":      -2.5,
        "trail_trigger": 3.0,
        "trail_pct":     2.0,
        "max_hold_days": 15,
    },
}

# 대표 유니버스 (KOSPI + KOSDAQ 주요 종목)
# 시가총액 상위 + 모멘텀 전략에 적합한 중소형 혼합
UNIVERSE = {
    # -- KOSPI 대형주 --
    "삼성전자":        "005930",
    "SK하이닉스":      "000660",
    "LG에너지솔루션":  "373220",
    "삼성바이오로직스":"207940",
    "현대차":          "005380",
    "기아":            "000270",
    "POSCO홀딩스":     "005490",
    "셀트리온":        "068270",
    "KB금융":          "105560",
    "신한지주":        "055550",
    "하나금융지주":    "086790",
    "삼성SDI":         "006400",
    "LG화학":          "051910",
    "SK이노베이션":    "096770",
    "카카오":          "035720",
    "네이버":          "035420",
    "현대모비스":      "012330",
    "삼성물산":        "028260",
    "한국전력":        "015760",
    "두산에너빌리티":  "034020",
    "HD현대일렉트릭":  "267260",
    "LS ELECTRIC":     "010120",
    "한화에어로스페이스": "012450",
    "HD현대중공업":    "329180",
    "삼성중공업":      "010140",
    "한화오션":        "042660",
    "크래프톤":        "259960",
    "넷마블":          "251270",
    "CJ제일제당":      "097950",
    "롯데케미칼":      "011170",
    # -- KOSDAQ 중형 성장주 --
    "에코프로비엠":    "247540",
    "에코프로":        "086520",
    "알테오젠":        "196170",
    "HLB":             "028300",
    "리가켐바이오":    "141080",
    "삼천당제약":      "000250",
    "클래시스":        "214150",
    "레인보우로보틱스":"277810",
    "카카오게임즈":    "293490",
    "펄어비스":        "263750",
    "엔씨소프트":      "036570",
    "셀트리온헬스케어":"091990",
    "메디톡스":        "086900",
    "파마리서치":      "214450",
    "코스맥스":        "044820",
    "한미약품":        "128940",
    "동국제약":        "086450",
    "보령":            "003850",
    "유한양행":        "000100",
    "종근당":          "185750",
    "OCI홀딩스":       "010060",
    "씨에스윈드":      "112610",
    "솔라엣지":        "007810",
    "SK바이오팜":      "326030",
    "카카오뱅크":      "323410",
    "케이카":          "381970",
    "두산퓨얼셀":      "336260",
    "현대로템":        "064350",
    "한화시스템":      "272210",
    "LIG넥스원":       "079550",
    "이오테크닉스":    "039030",
    "HPSP":            "403870",
    "피에스케이":      "031980",
    "원익IPS":         "240810",
    "테크윙":          "089030",
    "파크시스템스":    "140860",
    "저스템":          "085660",
    "솔브레인":        "357780",
    "SK아이이테크놀로지": "361610",
    "인텔리안테크":    "189300",
    "에스티팜":        "237690",
}


# ----------------------------------------------------------------------
#  데이터 수집 / 캐시
# ----------------------------------------------------------------------
def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """pykrx로 일봉 OHLCV 다운로드."""
    if not PYKRX_OK:
        return pd.DataFrame()
    try:
        df = krx.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if "date" in cl or "날짜" in cl:
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
        if any(c not in df.columns for c in needed):
            return pd.DataFrame()
        df = df[needed].copy()
        df["date"] = pd.to_datetime(df["date"])
        df["ticker"] = ticker
        df = df[(df["open"] > 0) & (df["close"] > 0) & (df["volume"] > 0)]
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"  [오류] {ticker}: {e}")
        return pd.DataFrame()


def load_universe(universe: dict, start: str, end: str, cache_file: str) -> dict[str, pd.DataFrame]:
    """유니버스 전체 데이터 로드 (캐시 우선)."""
    cache_path = os.path.join(os.path.dirname(__file__), cache_file)

    # 캐시 존재 시 로드
    if os.path.exists(cache_path):
        print(f"[캐시 로드] {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = {}
        for ticker, records in raw.items():
            df = pd.DataFrame(records)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                result[ticker] = df
        print(f"  → {len(result)}개 종목 로드 완료")
        return result

    # 신규 다운로드
    print(f"[데이터 다운로드] {len(universe)}개 종목 × {start}~{end}")
    result = {}
    cache_data = {}
    for i, (name, ticker) in enumerate(universe.items(), 1):
        print(f"  [{i:2d}/{len(universe)}] {name} ({ticker})", end=" ", flush=True)
        df = fetch_ohlcv(ticker, start, end)
        if not df.empty:
            result[ticker] = df
            cache_data[ticker] = df.assign(date=df["date"].astype(str)).to_dict("records")
            print(f"OK {len(df)}일")
        else:
            print("SKIP 데이터 없음")
        time.sleep(0.3)  # API rate limit

    # 캐시 저장
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False)
    print(f"\n[캐시 저장] {cache_path} ({len(result)}개 종목)")
    return result


# ----------------------------------------------------------------------
#  기술 지표
# ----------------------------------------------------------------------
def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """전략에 필요한 지표 계산."""
    df = df.copy()
    vol_period = cfg.get("vol_ma_period", 20)
    df["vol_ma"] = df["volume"].rolling(vol_period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"]

    # 갭 (전일 종가 대비 시가)
    df["gap_pct"] = (df["open"] / df["close"].shift(1) - 1) * 100

    # 전일 대비 변동
    df["chg_pct"] = (df["close"] / df["close"].shift(1) - 1) * 100

    # MA
    for p in [20, 60]:
        df[f"ma{p}"] = df["close"].rolling(p).mean()

    # RSI
    rsi_period = cfg.get("rsi_period", 14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(rsi_period).mean()
    loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    # 신고점 (lookback)
    lookback = cfg.get("lookback", 60)
    df[f"high_{lookback}d"] = df["close"].rolling(lookback).max().shift(1)

    # 연속 하락 일수 (전날까지의 연속 하락 — 오늘 반등 신호와 분리)
    df["is_down"] = (df["close"] < df["close"].shift(1)).astype(int)
    _consec = df["is_down"].groupby(
        (df["is_down"] != df["is_down"].shift()).cumsum()
    ).cumsum() * df["is_down"]
    # prev_consec_down: 어제까지 연속 하락이었으면 오늘은 반등 체크 가능
    df["prev_consec_down"] = _consec.shift(1).fillna(0)

    return df


# ----------------------------------------------------------------------
#  진입 신호
# ----------------------------------------------------------------------
def signal_A(row: pd.Series, cfg: dict) -> bool:
    """갭업 모멘텀: 갭업 + 거래량 폭발."""
    if pd.isna(row["vol_ma"]) or row["vol_ma"] == 0:
        return False
    return (
        row["gap_pct"] >= cfg["gap_min_pct"]
        and row["vol_ratio"] >= cfg["vol_mult"]
    )


def signal_B(row: pd.Series, cfg: dict) -> bool:
    """신고점 돌파: N일 최고가 돌파 + 거래량 + RSI 모멘텀."""
    lookback = cfg["lookback"]
    high_col = f"high_{lookback}d"
    if pd.isna(row.get(high_col)) or pd.isna(row.get("rsi")):
        return False
    return (
        row["close"] > row[high_col]
        and row["vol_ratio"] >= cfg["vol_mult"]
        and cfg["rsi_min"] <= row["rsi"] <= cfg["rsi_max"]
    )


def signal_C(row: pd.Series, cfg: dict) -> bool:
    """눌림목 매수: 상승 추세 내 MA20 위에서 반등.
    조건: 전날까지 N일 연속 하락 + 오늘 양봉 반등 + MA60 위 추세 유지.
    """
    if pd.isna(row.get("ma60")) or pd.isna(row.get("ma20")) or pd.isna(row.get("rsi")):
        return False
    return (
        row["close"] > row["ma60"]                # 상승 추세
        and row["close"] > row["ma20"]            # MA20 위 (지지 확인)
        and row["prev_consec_down"] >= cfg["pullback_days"]  # 전날까지 N일 눌림
        and row["chg_pct"] > 0                    # 오늘 반등
        and row["rsi"] <= cfg["rsi_max"]          # 과매수 아님
    )


SIGNAL_FUNCS = {
    "A_gap_momentum":  signal_A,
    "B_new_high":      signal_B,
    "C_ma20_pullback": signal_C,
}


# ----------------------------------------------------------------------
#  거래 시뮬레이션
# ----------------------------------------------------------------------
@dataclass
class Position:
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    size: float          # 주수
    strategy: str
    peak_price: float = field(init=False)
    days_held: int = 0

    def __post_init__(self):
        self.peak_price = self.entry_price


@dataclass
class Trade:
    ticker: str
    strategy: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    size: float
    reason: str

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1) * 100 - ROUND_TRIP_COST * 100

    @property
    def pnl_krw(self) -> float:
        gross = (self.exit_price - self.entry_price) * self.size
        cost = self.entry_price * self.size * ROUND_TRIP_COST
        return gross - cost


def simulate_strategy(strategy_name: str, cfg: dict, data: dict[str, pd.DataFrame],
                      dates: list) -> tuple[list[Trade], list[float]]:
    """단일 전략 포트폴리오 시뮬레이션."""
    capital = CONFIG["initial_capital"]
    max_pos = CONFIG["max_positions"]
    pos_size_pct = CONFIG["position_size"]
    # 포지션당 최대 KRW 고정 (자본 성장에 따른 비현실적 복리 방지)
    max_pos_krw = CONFIG["initial_capital"] * pos_size_pct

    positions: list[Position] = []
    trades: list[Trade] = []
    equity_curve: list[float] = [capital]

    signal_fn = SIGNAL_FUNCS[strategy_name]

    # 날짜별 인덱스 미리 계산
    ticker_idx: dict[str, dict] = {}
    for ticker, df in data.items():
        ticker_idx[ticker] = {row["date"]: i for i, row in df.iterrows()}

    for date_idx, date in enumerate(dates):
        date_ts = pd.Timestamp(date)

        # -- 기존 포지션 청산 체크 --
        closed = []
        for pos in positions:
            if pos.ticker not in data:
                continue
            df = data[pos.ticker]
            idx = ticker_idx[pos.ticker].get(date_ts)
            if idx is None:
                pos.days_held += 1
                continue

            row = df.iloc[idx]
            pos.days_held += 1
            pos.peak_price = max(pos.peak_price, row["high"])

            exit_price = None
            reason = None

            entry = pos.entry_price
            high, low, close = row["high"], row["low"], row["close"]
            peak = pos.peak_price

            stop_price  = entry * (1 + cfg["stop_pct"] / 100)
            target_price = entry * (1 + cfg["target_pct"] / 100)
            trail_trigger = entry * (1 + cfg["trail_trigger"] / 100)
            trail_price = peak * (1 - cfg["trail_pct"] / 100)

            # 우선순위: 손절 > 익절 > 트레일 > 만기
            if low <= stop_price:
                exit_price = stop_price
                reason = "stop"
            elif high >= target_price:
                exit_price = target_price
                reason = "target"
            elif peak >= trail_trigger and low <= trail_price:
                exit_price = max(trail_price, low)
                reason = "trail"
            elif pos.days_held >= cfg["max_hold_days"]:
                exit_price = close
                reason = "timeout"

            if exit_price:
                trade = Trade(
                    ticker=pos.ticker,
                    strategy=strategy_name,
                    entry_date=pos.entry_date,
                    exit_date=date_ts,
                    entry_price=entry,
                    exit_price=exit_price,
                    size=pos.size,
                    reason=reason,
                )
                capital += trade.pnl_krw
                trades.append(trade)
                closed.append(pos)

        for p in closed:
            positions.remove(p)

        # -- 신규 진입 신호 스캔 --
        if len(positions) < max_pos:
            candidates = []
            for ticker, df in data.items():
                # 이미 보유 중인 종목 제외
                if any(p.ticker == ticker for p in positions):
                    continue
                idx = ticker_idx[ticker].get(date_ts)
                if idx is None or idx < 1:
                    continue
                row = df.iloc[idx]
                if signal_fn(row, cfg):
                    # 거래량 많은 순으로 정렬
                    candidates.append((ticker, row, float(row.get("vol_ratio", 0))))

            # 거래량 폭발 순으로 최대 (max_pos - 현재) 개 진입
            candidates.sort(key=lambda x: x[2], reverse=True)
            slots = max_pos - len(positions)
            for ticker, row, _ in candidates[:slots]:
                entry_price = float(row["open"]) * (1 + CONFIG["slippage"])
                if entry_price <= 0:
                    continue
                alloc = min(capital * pos_size_pct, max_pos_krw)
                size = alloc / entry_price
                if size < 1:
                    continue
                positions.append(Position(
                    ticker=ticker,
                    entry_date=date_ts,
                    entry_price=entry_price,
                    size=size,
                    strategy=strategy_name,
                ))

        # 미실현 평가
        unrealized = 0.0
        for pos in positions:
            df = data[pos.ticker]
            idx = ticker_idx[pos.ticker].get(date_ts)
            if idx is not None:
                unrealized += (df.iloc[idx]["close"] - pos.entry_price) * pos.size
        equity_curve.append(capital + unrealized)

    # 잔여 포지션 강제청산 (마지막 날)
    if dates and positions:
        last_date = pd.Timestamp(dates[-1])
        for pos in positions:
            df = data[pos.ticker]
            idx = ticker_idx[pos.ticker].get(last_date)
            if idx is not None:
                exit_price = float(df.iloc[idx]["close"])
                trade = Trade(
                    ticker=pos.ticker,
                    strategy=strategy_name,
                    entry_date=pos.entry_date,
                    exit_date=last_date,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    size=pos.size,
                    reason="end_of_test",
                )
                capital += trade.pnl_krw
                trades.append(trade)

    return trades, equity_curve


# ----------------------------------------------------------------------
#  통계 계산
# ----------------------------------------------------------------------
def calc_stats(trades: list[Trade], equity_curve: list[float], strategy_name: str) -> dict:
    if not trades:
        return {"strategy": strategy_name, "error": "거래 없음"}

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / len(pnls) * 100
    avg_win = np.mean(wins) if wins else 0.0
    avg_loss = np.mean(losses) if losses else 0.0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

    # MDD
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak * 100
    mdd = float(np.min(dd))

    # 연간 수익률
    initial = CONFIG["initial_capital"]
    final = equity_curve[-1]
    years = (pd.Timestamp(CONFIG["end_date"]) - pd.Timestamp(CONFIG["start_date"])).days / 365.25
    annual_return = ((final / initial) ** (1 / years) - 1) * 100

    # 샤프비율 (일간 수익률 기반)
    daily_returns = np.diff(eq) / eq[:-1]
    sharpe = (np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)) if np.std(daily_returns) > 0 else 0.0

    # 기대값
    ev = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss

    # 통과 기준
    PASS_CRITERIA = {
        "sharpe":      (sharpe,       1.5,  "≥ 1.5"),
        "win_rate":    (win_rate,     48.0, "≥ 48%"),
        "pl_ratio":    (pl_ratio,     1.8,  "≥ 1.8"),
        "mdd":         (mdd,          -20.0,"≥ -20%"),
        "trade_count": (len(trades),  50,   "≥ 50건"),
    }
    passed = {k: v[0] >= v[1] for k, v in PASS_CRITERIA.items()}
    passed["mdd"] = mdd >= -20.0

    return {
        "strategy":      strategy_name,
        "trade_count":   len(trades),
        "win_rate":      round(win_rate, 1),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "pl_ratio":      round(pl_ratio, 2),
        "ev_per_trade":  round(ev, 3),
        "annual_return": round(annual_return, 1),
        "total_return":  round((final / initial - 1) * 100, 1),
        "mdd":           round(mdd, 1),
        "sharpe":        round(sharpe, 2),
        "final_capital": round(final),
        "pass":          passed,
        "overall_pass":  all(passed.values()),
    }


def print_stats(stats: dict):
    if "error" in stats:
        print(f"\n{'='*55}")
        print(f"  전략 {stats['strategy']}: {stats['error']}")
        return

    name = stats["strategy"]
    passed = stats["overall_pass"]
    status = "[PASS]" if passed else "[FAIL]"

    print(f"\n{'='*55}")
    print(f"  전략: {name}  {status}")
    print(f"{'-'*55}")
    print(f"  거래 수:      {stats['trade_count']:>6}건")
    wr_ok  = "OK" if stats["pass"]["win_rate"]  else "--"
    pl_ok  = "OK" if stats["pass"]["pl_ratio"]  else "--"
    mdd_ok = "OK" if stats["pass"]["mdd"]       else "--"
    sh_ok  = "OK" if stats["pass"]["sharpe"]    else "--"
    print(f"  승률:         {stats['win_rate']:>6.1f}%   {wr_ok} (기준 >=48%)")
    print(f"  평균 수익:   {stats['avg_win']:>+7.2f}%")
    print(f"  평균 손실:   {stats['avg_loss']:>+7.2f}%")
    print(f"  손익비:       {stats['pl_ratio']:>6.2f}x   {pl_ok} (기준 >=1.8)")
    print(f"  기대값/거래: {stats['ev_per_trade']:>+7.3f}%")
    print(f"  연 수익률:   {stats['annual_return']:>+7.1f}%")
    print(f"  전체 수익률: {stats['total_return']:>+7.1f}%")
    print(f"  MDD:         {stats['mdd']:>+7.1f}%   {mdd_ok} (기준 >=-20%)")
    print(f"  샤프비율:     {stats['sharpe']:>6.2f}   {sh_ok} (기준 >=1.5)")
    print(f"  최종 자본:   {stats['final_capital']:>10,}원")

    # 청산 이유 분포
    pass


def print_exit_reasons(trades: list[Trade]):
    reasons = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    total = len(trades)
    print(f"  청산 사유:    ", end="")
    print(" / ".join(f"{r}={c}({c/total*100:.0f}%)" for r, c in sorted(reasons.items())))


# ----------------------------------------------------------------------
#  메인
# ----------------------------------------------------------------------
def main():
    print("=" * 55)
    print("  Korea Multi-Strategy Backtest v1")
    print(f"  기간: {CONFIG['start_date']} ~ {CONFIG['end_date']}")
    print(f"  자본: {CONFIG['initial_capital']:,}원")
    print(f"  유니버스: {len(UNIVERSE)}개 종목")
    print("=" * 55)

    # 1. 데이터 로드
    data = load_universe(UNIVERSE, CONFIG["start_date"], CONFIG["end_date"],
                         CONFIG["cache_file"])
    if not data:
        print("[오류] 데이터 없음. pykrx 설치 확인.")
        return

    # 2. 지표 계산 (각 전략 파라미터 포함하도록 넉넉하게)
    combined_cfg = {
        "vol_ma_period": 20, "rsi_period": 14,
        "lookback": 60, "trend_ma": 60,
    }
    print("\n[지표 계산 중...]")
    processed = {}
    for ticker, df in data.items():
        processed[ticker] = add_indicators(df, combined_cfg)

    # 3. 공통 거래일 목록
    all_dates: set = set()
    for df in processed.values():
        all_dates.update(df["date"].tolist())
    dates = sorted(all_dates)
    start_ts = pd.Timestamp(CONFIG["start_date"])
    end_ts = pd.Timestamp(CONFIG["end_date"])
    dates = [d for d in dates if start_ts <= d <= end_ts]
    print(f"  → 거래일: {len(dates)}일")

    # 4. 전략별 시뮬레이션
    all_stats = []
    all_trades = {}

    for strategy_name, cfg in STRATEGIES.items():
        print(f"\n[시뮬레이션] {strategy_name} ...")
        trades, equity = simulate_strategy(strategy_name, cfg, processed, dates)
        stats = calc_stats(trades, equity, strategy_name)
        print_stats(stats)
        if trades:
            print_exit_reasons(trades)
        all_stats.append(stats)
        all_trades[strategy_name] = trades

    # 5. 최종 비교 요약
    print(f"\n{'='*55}")
    print("  [SUMMARY] 전략 비교 요약")
    print(f"{'-'*55}")
    print(f"  {'전략':<22} {'연수익':>7} {'MDD':>7} {'샤프':>6} {'승률':>6} {'손익비':>6} {'결과':>6}")
    print(f"  {'-'*22} {'-'*7} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
    for s in all_stats:
        if "error" in s:
            continue
        flag = "PASS" if s["overall_pass"] else "FAIL"
        print(f"  {s['strategy']:<22} {s['annual_return']:>+6.1f}% {s['mdd']:>+6.1f}% "
              f"{s['sharpe']:>6.2f} {s['win_rate']:>5.1f}% {s['pl_ratio']:>6.2f}x {flag}")

    # 6. 결과 JSON 저장
    result_path = os.path.join(os.path.dirname(__file__), "korea_backtest_result.json")
    def _json_safe(obj):
        """numpy/bool 타입 → JSON 직렬화 가능하게 변환."""
        if isinstance(obj, dict):
            return {k: _json_safe(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_json_safe(v) for v in obj]
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        return obj
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(_json_safe(all_stats), f, ensure_ascii=False, indent=2)
    print(f"\n[결과 저장] {result_path}")

    # 7. 추천
    print(f"\n{'='*55}")
    passed = [s for s in all_stats if s.get("overall_pass")]
    if passed:
        best = max(passed, key=lambda s: s.get("sharpe", 0))
        print(f"  [BEST] 추천 전략: {best['strategy']}")
        print(f"     연 수익률 {best['annual_return']:+.1f}% / 샤프 {best['sharpe']:.2f} / MDD {best['mdd']:.1f}%")
    else:
        best = max(all_stats, key=lambda s: s.get("sharpe", -99) if "error" not in s else -99)
        print(f"  [!] 통과 전략 없음. 파라미터 조정 필요.")
        print(f"     가장 근접: {best.get('strategy')} (샤프 {best.get('sharpe', 0):.2f})")
    print("=" * 55)


if __name__ == "__main__":
    main()

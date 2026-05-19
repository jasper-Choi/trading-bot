"""
주식 1호 전략 백테스트
전략: 갭 모멘텀 + 뉴스 감성 필터 (감성 분석은 룰 기반 시뮬레이션)
시장: 코스닥 (pykrx 라이브러리 사용 — 무료 한국 주식 데이터)

설치: pip install pykrx pandas numpy
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
from dataclasses import dataclass
from typing import Optional

try:
    from pykrx import stock as krx
    PYKRX_OK = True
except ImportError:
    PYKRX_OK = False
    print("[안내] pykrx 미설치. 샘플 데이터로 시뮬레이션합니다.")
    print("      실제 백테스트: pip install pykrx 후 재실행\n")


# ─────────────────────────────────────────
#  설정값
# ─────────────────────────────────────────
CONFIG = {
    "capital": 5_000_000,
    "risk_per_trade": 0.01,         # 1회 리스크 1%
    "stop_loss_pct": 0.02,          # 손절 -2%
    "take_profit_1": 0.02,          # 1차 익절 +2% (절반)
    "take_profit_2": 0.04,          # 2차 익절 +4%
    "gap_min_pct": 0.03,            # 최소 갭 상승 +3%
    "volume_mult": 2.0,             # 거래량 배수
    "max_hold_minutes": 90,         # 최대 보유 90분 (10:30까지)
    "entry_window_end": "09:30",    # 진입 마감 시각
    "max_daily_trades": 3,          # 하루 최대 종목 수
    "backtest_days": 120,           # 백테스트 기간
    # 코스닥 시총 필터 (실전에서는 스크리닝 API로 자동화)
    "target_tickers": [
        "247540",  # 에코프로비엠
        "086520",  # 에코프로
        "373220",  # LG에너지솔루션
        "006400",  # 삼성SDI
        "096770",  # SK이노베이션
    ],
}


# ─────────────────────────────────────────
#  데이터 수집 (pykrx)
# ─────────────────────────────────────────
def fetch_stock_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    """pykrx로 일봉 데이터 가져오기"""
    if not PYKRX_OK:
        return generate_sample_data(ticker, start, end)

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
        print(f"  [오류] {ticker} 데이터 수집 실패: {e}")
        return generate_sample_data(ticker, start, end)


def generate_sample_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    """pykrx 없을 때 시뮬레이션용 샘플 데이터 생성"""
    np.random.seed(hash(ticker) % 1000)
    dates = pd.bdate_range(start=start, end=end, freq="B")
    n = len(dates)
    base = 50000
    returns = np.random.normal(0.0005, 0.025, n)
    closes = base * np.cumprod(1 + returns)
    opens = closes * (1 + np.random.normal(0, 0.008, n))
    highs = np.maximum(opens, closes) * (1 + np.abs(np.random.normal(0, 0.008, n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(np.random.normal(0, 0.008, n)))
    volumes = np.random.lognormal(15, 0.8, n).astype(int)

    return pd.DataFrame({
        "date": dates, "open": opens.astype(int), "high": highs.astype(int),
        "low": lows.astype(int), "close": closes.astype(int),
        "volume": volumes, "ticker": ticker,
    })


# ─────────────────────────────────────────
#  지표 & 필터
# ─────────────────────────────────────────
def add_stock_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values("date").reset_index(drop=True)
    df["prev_close"] = df["close"].shift(1)
    df["gap_pct"] = (df["open"] - df["prev_close"]) / df["prev_close"]
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["vol_avg20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg20"]
    # 5일선 > 20일선 → 상승 바이어스
    df["trend_ok"] = df["ma5"] > df["ma20"]
    return df.dropna().reset_index(drop=True)


def simulate_news_sentiment(row: pd.Series) -> str:
    """
    실제 환경에서는 Claude API로 뉴스 감성 분석.
    백테스트에서는 갭 크기 + 거래량으로 룰 기반 시뮬레이션.
    갭 크기가 크고 거래량이 많을수록 POSITIVE 확률 높음.
    """
    gap = row["gap_pct"]
    vol_ratio = row["vol_ratio"]

    # 갭이 10% 이상이면 뉴스 이슈 가능성 높음
    if gap >= 0.10 and vol_ratio >= 3.0:
        return "POSITIVE"
    elif gap >= 0.05 and vol_ratio >= 2.0:
        # 70% 확률로 POSITIVE (불확실성 반영)
        np.random.seed(int(row.name))
        return "POSITIVE" if np.random.random() < 0.70 else "NEUTRAL"
    elif gap >= 0.03 and vol_ratio >= 1.5:
        np.random.seed(int(row.name) + 100)
        return "POSITIVE" if np.random.random() < 0.55 else "NEUTRAL"
    elif gap < 0:
        return "NEGATIVE"
    else:
        return "NEUTRAL"


# ─────────────────────────────────────────
#  진입 / 청산 조건
# ─────────────────────────────────────────
def check_stock_entry(row: pd.Series, cfg: dict) -> bool:
    # 추세 필터
    if not row["trend_ok"]:
        return False
    # 갭 상승 최소
    if row["gap_pct"] < cfg["gap_min_pct"]:
        return False
    # 거래량 급증
    if row["vol_ratio"] < cfg["volume_mult"]:
        return False
    # 뉴스 감성 (실전: Claude API, 백테스트: 룰 기반)
    sentiment = simulate_news_sentiment(row)
    if sentiment != "POSITIVE":
        return False
    # 시초가가 전일 고가 이상 (강한 갭업 확인)
    if "prev_high" in row and row["open"] < row.get("prev_high", 0):
        return False
    return True


# ─────────────────────────────────────────
#  백테스트 엔진 (일봉 기반)
# ─────────────────────────────────────────
@dataclass
class StockTrade:
    ticker: str
    date: datetime
    entry_price: float
    size: float
    qty: int
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""


def run_stock_backtest(ticker: str, df: pd.DataFrame, cfg: dict) -> list[StockTrade]:
    trades = []

    for _, row in df.iterrows():
        if not check_stock_entry(row, cfg):
            continue

        entry = row["open"]  # 시초가 진입 가정
        risk_amt = cfg["capital"] * cfg["risk_per_trade"]
        size = risk_amt / cfg["stop_loss_pct"]
        qty = max(1, int(size / entry))
        actual_size = qty * entry

        # 당일 내 시뮬레이션 (일봉이므로 고가/저가/종가로 결과 추정)
        stop = entry * (1 - cfg["stop_loss_pct"])
        tp1 = entry * (1 + cfg["take_profit_1"])
        tp2 = entry * (1 + cfg["take_profit_2"])

        # 당일 손절 먼저 확인
        if row["low"] <= stop:
            exit_price = stop
            reason = "손절"
        # 2차 익절 달성
        elif row["high"] >= tp2:
            exit_price = tp2
            reason = "2차익절"
        # 1차 익절 (절반은 여기서, 나머지는 종가)
        elif row["high"] >= tp1:
            # 단순화: 평균 (tp1 + close) / 2
            exit_price = (tp1 + row["close"]) / 2
            reason = "1차익절"
        # 시간 청산 (10:30 = 종가로 근사)
        else:
            exit_price = row["close"]
            reason = "시간청산"

        pnl_pct = (exit_price - entry) / entry
        pnl = actual_size * pnl_pct

        trades.append(StockTrade(
            ticker=ticker, date=row["date"],
            entry_price=entry, size=actual_size, qty=qty,
            exit_price=exit_price, pnl=pnl,
            pnl_pct=pnl_pct * 100, exit_reason=reason,
        ))

    return trades


# ─────────────────────────────────────────
#  성과 분석 (코인과 동일 구조)
# ─────────────────────────────────────────
def analyze_stock_results(trades: list[StockTrade], capital: float) -> dict:
    if not trades:
        return {"error": "거래 없음"}

    df = pd.DataFrame([{
        "ticker": t.ticker, "date": t.date,
        "entry": t.entry_price, "exit": t.exit_price,
        "pnl": t.pnl, "pnl_pct": t.pnl_pct,
        "reason": t.exit_reason,
    } for t in trades])

    # 슬리피지 + 수수료 차감 (0.1% 왕복)
    df["pnl"] = df["pnl"] - (df["entry"] * 0.001 * df["pnl"] / df["pnl"].abs().clip(1))

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    win_rate = len(wins) / len(df) * 100
    avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0
    profit_factor = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 and losses["pnl"].sum() != 0 else 9.99
    rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 9.99

    equity = capital + df["pnl"].cumsum()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak * 100
    max_dd = drawdown.min()

    daily_ret = df.set_index("date")["pnl_pct"].resample("D").sum() / 100
    sharpe = 0
    if daily_ret.std() > 0:
        sharpe = (daily_ret.mean() - 0.03 / 252) / daily_ret.std() * np.sqrt(252)

    return {
        "총거래수": len(df),
        "승률(%)": round(win_rate, 1),
        "총손익(원)": round(df["pnl"].sum()),
        "총수익률(%)": round(df["pnl"].sum() / capital * 100, 2),
        "평균수익(%)": round(avg_win, 2),
        "평균손실(%)": round(avg_loss, 2),
        "손익비": round(rr_ratio, 2),
        "프로핏팩터": round(profit_factor, 2),
        "최대드로우다운(%)": round(max_dd, 2),
        "샤프비율": round(sharpe, 2),
        "청산사유별": df["reason"].value_counts().to_dict(),
    }


def print_stock_report(result: dict, ticker: str):
    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    passed = (
        result["샤프비율"] >= 1.0
        and result["승률(%)"] >= 45
        and result["손익비"] >= 1.5
        and result["최대드로우다운(%)"] >= -10
        and result["총거래수"] >= 20
    )
    status = "✅ 통과" if passed else "❌ 미달"

    print(f"\n{'='*50}")
    print(f"  {ticker} 백테스트 결과  {status}")
    print(f"{'='*50}")
    print(f"  총 거래수         : {result['총거래수']}회")
    print(f"  승률              : {result['승률(%)']}%  (기준: ≥45%)")
    print(f"  손익비            : {result['손익비']}  (기준: ≥1.5)")
    print(f"  샤프 비율         : {result['샤프비율']}  (기준: ≥1.0)")
    print(f"  최대 드로우다운   : {result['최대드로우다운(%)']}%  (기준: ≥-10%)")
    print(f"  총 손익           : {result['총손익(원)']:,}원")
    print(f"  총 수익률         : {result['총수익률(%)']}%")
    print(f"  청산 사유:")
    for r, cnt in result["청산사유별"].items():
        print(f"    {r}: {cnt}회")


# ─────────────────────────────────────────
#  메인
# ─────────────────────────────────────────
def main():
    cfg = CONFIG
    end_date = datetime.today().strftime("%Y%m%d")
    start_date = (datetime.today() - timedelta(days=cfg["backtest_days"])).strftime("%Y%m%d")

    print("=" * 50)
    print("  주식 1호 전략 — 백테스트 시작")
    print(f"  기간: {start_date} ~ {end_date} ({cfg['backtest_days']}일)")
    print(f"  초기 자본: {cfg['capital']:,}원")
    if not PYKRX_OK:
        print("  [주의] pykrx 없음 → 샘플 데이터로 시뮬레이션")
    print("=" * 50)

    all_results = {}

    for ticker in cfg["target_tickers"]:
        print(f"\n[{ticker}] 데이터 수집 중...")
        df = fetch_stock_data(ticker, start_date, end_date)
        if df.empty:
            print(f"  [{ticker}] 데이터 없음, 건너뜀")
            continue

        df = add_stock_indicators(df)
        trades = run_stock_backtest(ticker, df, cfg)
        result = analyze_stock_results(trades, cfg["capital"])
        print_stock_report(result, ticker)
        all_results[ticker] = result

    print(f"\n\n{'='*50}")
    print("  전체 주식 전략 요약")
    print(f"{'='*50}")

    valid = {k: v for k, v in all_results.items() if "error" not in v}
    if valid:
        total_pnl = sum(r["총손익(원)"] for r in valid.values())
        avg_sharpe = np.mean([r["샤프비율"] for r in valid.values()])
        passed = [t for t, r in valid.items()
                  if r["샤프비율"] >= 1.0 and r["승률(%)"] >= 45
                  and r["손익비"] >= 1.5 and r["최대드로우다운(%)"] >= -10]

        print(f"  총 손익 (합산): {total_pnl:,}원")
        print(f"  평균 샤프비율: {round(avg_sharpe, 2)}")
        print(f"  백테스트 통과 종목: {passed if passed else '없음'}")

    with open("/home/claude/backtest/stock_result.json", "w", encoding="utf-8") as f:
        summary = {k: {ky: v for ky, v in r.items() if ky != "trades_df"}
                   for k, r in all_results.items() if "error" not in r}
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print("\n  결과 저장 완료: stock_result.json")


if __name__ == "__main__":
    main()

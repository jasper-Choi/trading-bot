from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pykrx import stock as krx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


UNIVERSE: dict[str, str] = {
    "247540": "에코프로비엠",
    "196170": "알테오젠",
    "028300": "HLB",
    "141080": "리가켐바이오",
    "000250": "삼천당제약",
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "035720": "카카오",
    "005490": "POSCO홀딩스",
    "086520": "에코프로",
    "277810": "레인보우로보틱스",
    "454910": "두산로보틱스",
    "012450": "한화에어로스페이스",
    "267260": "HD현대일렉트릭",
    "087010": "펩트론",
    "257720": "실리콘투",
    "214450": "파마리서치",
    "214150": "클래시스",
    "035900": "JYP Ent.",
    "041510": "에스엠",
    "064350": "현대로템",
    "079550": "LIG넥스원",
    "047810": "한국항공우주",
    "058470": "리노공업",
    "091990": "셀트리온헬스케어",
    "298380": "에이비엘바이오",
    "237690": "에스티팜",
    "214370": "케어젠",
    "112040": "위메이드",
}

CAPITAL = 5_000_000
COMMISSION = 0.00015
SLIPPAGE = 0.0005
ROUND_TRIP_COST = (COMMISSION + SLIPPAGE) * 2
BACKTEST_DAYS = 240
MIN_TRADES = 20

PARAM_GRID = {
    "gap_min_pct": [0.012, 0.015, 0.02],
    "gap_max_pct": [0.12, 0.16],
    "vol_mult": [1.6, 2.0, 2.6],
    "drive_min_pct": [0.004, 0.007, 0.01],
    "tp1_pct": [0.025, 0.03],
    "tp2_pct": [0.045, 0.05],
    "stop_pct": [0.012, 0.015],
}


@dataclass
class Trade:
    ticker: str
    name: str
    date: str
    gap_pct: float
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_krw: float
    exit_reason: str


def fetch_daily(ticker: str, start: str, end: str) -> pd.DataFrame:
    df = krx.get_market_ohlcv_by_date(start, end, ticker)
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.reset_index()
    frame.columns = ["date", "open", "high", "low", "close", "volume", "change_pct"]
    frame["date"] = pd.to_datetime(frame["date"])
    frame["ticker"] = ticker
    return frame[["date", "ticker", "open", "high", "low", "close", "volume"]]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy().sort_values("date").reset_index(drop=True)
    frame["prev_close"] = frame["close"].shift(1)
    frame["gap_pct"] = (frame["open"] - frame["prev_close"]) / frame["prev_close"]
    frame["vol_ma20"] = frame["volume"].rolling(20).mean()
    frame["vol_ratio"] = frame["volume"] / frame["vol_ma20"]
    frame["ma_fast"] = frame["close"].rolling(5).mean()
    frame["ma_slow"] = frame["close"].rolling(20).mean()
    frame["trend_ok"] = frame["ma_fast"] > frame["ma_slow"]
    frame["drive_pct"] = (frame["high"] - frame["open"]) / frame["open"]
    return frame.dropna().reset_index(drop=True)


def simulate_symbol(df: pd.DataFrame, ticker: str, name: str, cfg: dict[str, float]) -> list[Trade]:
    trades: list[Trade] = []
    for _, row in df.iterrows():
        if not (cfg["gap_min_pct"] <= row["gap_pct"] <= cfg["gap_max_pct"]):
            continue
        if row["vol_ratio"] < cfg["vol_mult"]:
            continue
        if not bool(row["trend_ok"]):
            continue
        if row["drive_pct"] < cfg["drive_min_pct"]:
            continue

        entry = float(row["open"]) * (1 + SLIPPAGE)
        stop = entry * (1 - cfg["stop_pct"])
        tp1 = entry * (1 + cfg["tp1_pct"])
        tp2 = entry * (1 + cfg["tp2_pct"])
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])

        risk_krw = CAPITAL * 0.02
        size_krw = min(risk_krw / cfg["stop_pct"], CAPITAL * 0.25)

        if low <= stop:
            exit_price = max(stop, float(row["open"])) * (1 - SLIPPAGE)
            exit_reason = "stop"
            allocations = [(1.0, exit_price, exit_reason)]
        elif high >= tp2:
            allocations = [
                (0.5, tp1 * (1 - SLIPPAGE), "tp1"),
                (0.5, tp2 * (1 - SLIPPAGE), "tp2"),
            ]
        elif high >= tp1:
            allocations = [
                (0.5, tp1 * (1 - SLIPPAGE), "tp1"),
                (0.5, close * (1 - SLIPPAGE), "time"),
            ]
        else:
            allocations = [(1.0, close * (1 - SLIPPAGE), "time")]

        for ratio, exit_price, exit_reason in allocations:
            raw_pct = (exit_price - entry) / entry
            pnl_pct = raw_pct - ROUND_TRIP_COST
            notional = size_krw * ratio
            trades.append(
                Trade(
                    ticker=ticker,
                    name=name,
                    date=str(row["date"].date()),
                    gap_pct=round(float(row["gap_pct"]) * 100, 2),
                    entry_price=round(entry, 2),
                    exit_price=round(exit_price, 2),
                    pnl_pct=round(pnl_pct * 100, 3),
                    pnl_krw=round(notional * pnl_pct),
                    exit_reason=exit_reason,
                )
            )
    return trades


def analyze_trades(trades: list[Trade]) -> dict[str, float] | dict[str, str]:
    if len(trades) < MIN_TRADES:
        return {"error": f"not enough trades: {len(trades)}"}

    df = pd.DataFrame([t.__dict__ for t in trades])
    wins = df[df["pnl_krw"] > 0]
    losses = df[df["pnl_krw"] <= 0]
    win_rate = len(wins) / len(df) * 100
    avg_win = wins["pnl_pct"].mean() if len(wins) else 0.0
    avg_loss = losses["pnl_pct"].mean() if len(losses) else 0.0
    rr = abs(avg_win / avg_loss) if avg_loss else 0.0
    returns = df["pnl_pct"] / 100
    sharpe = ((returns.mean() - 0.03 / 252) / returns.std() * math.sqrt(252)) if returns.std() else 0.0
    equity = CAPITAL + df["pnl_krw"].cumsum()
    max_dd = float(((equity - equity.cummax()) / equity.cummax() * 100).min())
    total_return = float(df["pnl_krw"].sum() / CAPITAL * 100)

    return {
        "trades": int(len(df)),
        "win_rate": round(win_rate, 1),
        "rr": round(rr, 2),
        "sharpe": round(sharpe, 2),
        "max_dd": round(max_dd, 2),
        "total_return": round(total_return, 2),
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
    }


def passes(metrics: dict[str, float]) -> bool:
    return (
        metrics.get("trades", 0) >= MIN_TRADES
        and metrics.get("win_rate", 0.0) >= 45.0
        and metrics.get("rr", 0.0) >= 2.0
        and metrics.get("sharpe", 0.0) >= 1.0
        and metrics.get("max_dd", -999.0) >= -15.0
        and metrics.get("total_return", 0.0) > 0.0
    )


def config_grid() -> list[dict[str, float]]:
    configs: list[dict[str, float]] = []
    for gap_min in PARAM_GRID["gap_min_pct"]:
        for gap_max in PARAM_GRID["gap_max_pct"]:
            for vol_mult in PARAM_GRID["vol_mult"]:
                for drive_min in PARAM_GRID["drive_min_pct"]:
                    for tp1 in PARAM_GRID["tp1_pct"]:
                        for tp2 in PARAM_GRID["tp2_pct"]:
                            for stop in PARAM_GRID["stop_pct"]:
                                if tp2 <= tp1:
                                    continue
                                configs.append(
                                    {
                                        "gap_min_pct": gap_min,
                                        "gap_max_pct": gap_max,
                                        "vol_mult": vol_mult,
                                        "drive_min_pct": drive_min,
                                        "tp1_pct": tp1,
                                        "tp2_pct": tp2,
                                        "stop_pct": stop,
                                    }
                                )
    return configs


def score(metrics: dict[str, float]) -> float:
    return (
        metrics["sharpe"] * 0.35
        + metrics["rr"] * 0.25
        + metrics["total_return"] * 0.2
        + (metrics["win_rate"] / 100) * 0.1
        + min(metrics["trades"], 60) / 60 * 0.1
    )


def main() -> None:
    end = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=BACKTEST_DAYS)).strftime("%Y%m%d")
    print("=" * 72)
    print("Korea Opening Drive Research")
    print(f"Period: {start} -> {end} | Universe: {len(UNIVERSE)} names | Capital: {CAPITAL:,} KRW")
    print("=" * 72)

    datasets: dict[str, tuple[str, pd.DataFrame]] = {}
    for ticker, name in UNIVERSE.items():
        df = fetch_daily(ticker, start, end)
        if df.empty or len(df) < 40:
            continue
        datasets[ticker] = (name, add_indicators(df))
    print(f"Loaded datasets: {len(datasets)}")

    results: list[dict[str, object]] = []
    for idx, cfg in enumerate(config_grid(), start=1):
        trades: list[Trade] = []
        per_symbol: dict[str, int] = {}
        for ticker, (name, df) in datasets.items():
            symbol_trades = simulate_symbol(df, ticker, name, cfg)
            trades.extend(symbol_trades)
            if symbol_trades:
                per_symbol[ticker] = len(symbol_trades)
        metrics = analyze_trades(trades)
        if "error" in metrics:
            continue
        result = {
            "config": cfg,
            "metrics": metrics,
            "score": round(score(metrics), 3),
            "passed": passes(metrics),
            "active_symbols": len(per_symbol),
            "top_symbols": sorted(per_symbol.items(), key=lambda item: item[1], reverse=True)[:8],
        }
        results.append(result)
        if idx % 40 == 0:
            print(f"checked {idx} configs...")

    results.sort(key=lambda item: (bool(item["passed"]), float(item["score"])), reverse=True)
    top = results[:10]
    print("\nTop configs:")
    for rank, item in enumerate(top, start=1):
        m = item["metrics"]
        print(
            f"{rank:02d}. pass={item['passed']} score={item['score']} "
            f"trades={m['trades']} win={m['win_rate']} rr={m['rr']} sharpe={m['sharpe']} "
            f"dd={m['max_dd']} return={m['total_return']} active={item['active_symbols']}"
        )
        print(f"    cfg={item['config']}")
        print(f"    top_symbols={item['top_symbols']}")

    out_path = Path(__file__).resolve().parent / "korea_opening_drive_research.json"
    out_path.write_text(json.dumps(results[:50], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()

"""
주식 전략 — 갭 모멘텀 (09:00~09:30) + 뉴스 모멘텀 (09:30~14:30).

진입 조건:
  갭 모멘텀:
    1. 갭 +3% 이상
    2. 뉴스 감성 POSITIVE or NEUTRAL
    3. 거래량 전일 대비 2배 이상
    4. 신호 강도 상위 3개 진입

  뉴스 모멘텀:
    1. 강한 뉴스 (신뢰도 0.8 이상)
    2. 뉴스 발표 후 5분 이내
    3. 거래량 급증 확인

청산 조건:
  - 익절 1차: +2% (절반 청산)
  - 익절 2차: +4% (나머지 청산)
  - 손절:     -2%
  - 트레일링: 고점 대비 -1.5%
  - 15:00 전량 강제 청산

포지션 크기:
  - BULL:    총 자본의 10%
  - NEUTRAL: 총 자본의 5%
  - BEAR:    진입 안 함
"""

import re
import time
import threading
import requests
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from src.stock_screener import get_gap_up_stocks, get_kosdaq_realtime
from src.news_analyzer  import analyze_stock_news
from src.market_regime  import market_regime, BULL, NEUTRAL, BEAR, VOLATILE

# ── 상수 ──────────────────────────────────────────────────────────────────────
STOCK_TOTAL_CAPITAL = config.INITIAL_CAPITAL_PER_COIN * config.MAX_POSITIONS

STOP_LOSS_PCT = 0.02    # 손절 -2%
TP1_PCT       = 0.02    # 익절 1차 +2%
TP2_PCT       = 0.04    # 익절 2차 +4%
TRAIL_PCT     = 0.015   # 트레일링 -1.5%

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer":    "https://finance.naver.com",
}

# ── 인메모리 포지션 ────────────────────────────────────────────────────────────
_stock_lock      = threading.Lock()
_stock_positions: dict[str, dict] = {}
_stock_history:   list[dict]      = []


# ── 포지션 크기 결정 ─────────────────────────────────────────────────────────

def _position_capital() -> float:
    regime = market_regime.regime
    if regime == BULL:
        return STOCK_TOTAL_CAPITAL * 0.10
    if regime == NEUTRAL:
        return STOCK_TOTAL_CAPITAL * 0.05
    return 0.0


def _can_enter() -> bool:
    return _position_capital() > 0


# ── 포지션 생성 / 청산 ────────────────────────────────────────────────────────

def open_stock_position(
    ticker: str, name: str, entry_price: float, reason: str
) -> Optional[dict]:
    """주식 포지션 진입."""
    if not _can_enter() or entry_price <= 0:
        return None
    capital  = _position_capital()
    quantity = capital / entry_price
    pos = {
        "ticker":      ticker,
        "name":        name,
        "status":      "open",
        "entry_price": entry_price,
        "entry_date":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stop_loss":   entry_price * (1 - STOP_LOSS_PCT),
        "peak_price":  entry_price,
        "capital":     capital,
        "quantity":    quantity,
        "half_sold":   False,
        "reason":      reason,
        "tp1":         entry_price * (1 + TP1_PCT),
        "tp2":         entry_price * (1 + TP2_PCT),
    }
    with _stock_lock:
        _stock_positions[ticker] = pos
    return pos


def close_stock_position(
    ticker: str, exit_price: float, reason: str,
    quantity: Optional[float] = None
) -> Optional[dict]:
    """주식 포지션 청산 (부분 청산 지원)."""
    with _stock_lock:
        pos = _stock_positions.get(ticker)
        if not pos or pos["status"] != "open":
            return None
        qty     = quantity if quantity is not None else pos["quantity"]
        pnl     = (exit_price - pos["entry_price"]) * qty
        pnl_pct = (exit_price / pos["entry_price"] - 1) * 100

        record = {
            "ticker":      ticker,
            "name":        pos.get("name", ticker),
            "entry_price": pos["entry_price"],
            "entry_date":  pos["entry_date"],
            "exit_price":  exit_price,
            "exit_date":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "exit_reason": reason,
            "quantity":    qty,
            "capital":     pos["capital"],
            "pnl":         round(pnl, 0),
            "pnl_pct":     round(pnl_pct, 2),
        }
        _stock_history.append(record)

        if quantity is not None and quantity < pos["quantity"]:
            pos["quantity"] -= quantity
            pos["capital"]  -= quantity * pos["entry_price"]
            pos["half_sold"] = True
        else:
            pos.update({
                "status":      "closed",
                "exit_price":  exit_price,
                "exit_date":   record["exit_date"],
                "exit_reason": reason,
                "pnl":         record["pnl"],
                "pnl_pct":     record["pnl_pct"],
            })
    return record


def get_stock_positions() -> list[dict]:
    with _stock_lock:
        return [dict(p) for p in _stock_positions.values() if p["status"] == "open"]


def get_stock_history() -> list[dict]:
    with _stock_lock:
        return list(_stock_history)


# ── 현재가 조회 ───────────────────────────────────────────────────────────────

def _fetch_current_price(ticker: str) -> Optional[float]:
    """네이버 금융에서 현재가 조회."""
    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        m = re.search(r'id="_nowVal"[^>]*>([\d,]+)', resp.text)
        if m:
            return float(m.group(1).replace(",", ""))
    except Exception:
        pass
    return None


# ── 포지션 관리 ───────────────────────────────────────────────────────────────

def manage_stock_positions(log_fn=print) -> None:
    """기존 주식 포지션 손절·익절·트레일링·강제청산."""
    now         = datetime.now()
    force_close = now.hour >= 15

    for ticker in list(_stock_positions.keys()):
        with _stock_lock:
            pos = dict(_stock_positions.get(ticker, {}))
        if not pos or pos.get("status") != "open":
            continue

        price = _fetch_current_price(ticker)
        if price is None:
            continue

        # 고점 갱신
        with _stock_lock:
            p = _stock_positions.get(ticker)
            if p and price > p.get("peak_price", 0):
                p["peak_price"] = price

        entry   = pos["entry_price"]
        peak    = pos.get("peak_price", entry)
        pnl_pct = (price / entry - 1) * 100

        # 15:00 강제 청산
        if force_close:
            r = close_stock_position(ticker, price, "장마감강제청산")
            if r:
                log_fn(
                    f"[주식] {pos['name']}({ticker}) 강제청산 "
                    f"손익={r['pnl']:+,.0f}원 ({r['pnl_pct']:+.2f}%)"
                )
            continue

        trail_stop = peak * (1 - TRAIL_PCT)
        stop_price = pos["stop_loss"]
        effective  = max(stop_price, trail_stop)

        # 손절 / 트레일링
        if price <= effective:
            reason = "손절" if price <= stop_price else "트레일링스탑"
            r = close_stock_position(ticker, price, reason)
            if r:
                log_fn(
                    f"[주식] {pos['name']}({ticker}) {reason} "
                    f"손익={r['pnl']:+,.0f}원 ({r['pnl_pct']:+.2f}%)"
                )
            continue

        # 익절 1차 +2% (절반 청산)
        if not pos.get("half_sold") and pnl_pct >= TP1_PCT * 100:
            half_qty = pos["quantity"] / 2
            r = close_stock_position(ticker, price, "익절1차", quantity=half_qty)
            if r:
                log_fn(
                    f"[주식] {pos['name']}({ticker}) 익절1차(50%) "
                    f"손익={r['pnl']:+,.0f}원 ({r['pnl_pct']:+.2f}%)"
                )
            continue

        # 익절 2차 +4% (전량 청산)
        if pos.get("half_sold") and pnl_pct >= TP2_PCT * 100:
            r = close_stock_position(ticker, price, "익절2차")
            if r:
                log_fn(
                    f"[주식] {pos['name']}({ticker}) 익절2차(전량) "
                    f"손익={r['pnl']:+,.0f}원 ({r['pnl_pct']:+.2f}%)"
                )
            continue

        log_fn(
            f"[주식] {pos['name']}({ticker}) 유지 "
            f"현재가={price:,.0f} 손익={pnl_pct:+.2f}% "
            f"손절={stop_price:,.0f} 트레일={trail_stop:,.0f}"
        )
        time.sleep(0.15)


# ── 갭 모멘텀 (09:00~09:30) ──────────────────────────────────────────────────

def run_gap_momentum(log_fn=print) -> int:
    """갭 상승 종목 스캔 및 진입. 반환: 진입 건수."""
    now = datetime.now()
    if not (now.hour == 9 and now.minute < 30):
        return 0
    if not _can_enter():
        return 0

    gap_stocks = get_gap_up_stocks()
    if not gap_stocks:
        return 0

    candidates = []
    for s in gap_stocks:
        ticker = s["ticker"]

        # 뉴스 감성
        sentiment = analyze_stock_news(ticker, min_confidence=0.7)
        if sentiment["label"] == "NEGATIVE":
            log_fn(f"[주식갭] {s['name']}({ticker}) 뉴스부정 — 스킵")
            continue

        # 거래량 2배 이상
        if s.get("volume", 0) < s.get("prev_volume", 1) * 2:
            log_fn(f"[주식갭] {s['name']}({ticker}) 거래량부족 — 스킵")
            continue

        score = float(s["gap_pct"])
        if sentiment["label"] == "POSITIVE":
            score += sentiment["confidence"] * 2

        candidates.append({**s, "score": score, "sentiment": sentiment})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    entered = 0
    for cand in candidates[:3]:
        ticker = cand["ticker"]
        price  = cand.get("today_open") or cand.get("current_price", 0)
        if price <= 0:
            continue

        pos = open_stock_position(
            ticker  = ticker,
            name    = cand["name"],
            entry_price = price,
            reason  = f"갭모멘텀+{cand['gap_pct']}%",
        )
        if pos:
            entered += 1
            snt = cand["sentiment"]
            log_fn(
                f"[주식갭] {cand['name']}({ticker}) 진입 "
                f"갭={cand['gap_pct']:+.1f}% "
                f"뉴스={snt['label']}({snt['confidence']}) "
                f"진입가={price:,.0f}"
            )

    return entered


# ── 뉴스 모멘텀 (09:30~14:30) ───────────────────────────────────────────────

def run_news_momentum(log_fn=print) -> int:
    """뉴스 모멘텀 스캔 및 진입. 반환: 진입 건수."""
    now  = datetime.now()
    h, m = now.hour, now.minute
    in_window = (h == 9 and m >= 30) or (10 <= h < 14) or (h == 14 and m <= 30)
    if not in_window or not _can_enter():
        return 0

    top_stocks = get_kosdaq_realtime(50)
    entered    = 0

    for s in top_stocks[:20]:
        ticker    = s["ticker"]
        sentiment = analyze_stock_news(ticker, min_confidence=0.8)

        if sentiment["label"] != "POSITIVE" or sentiment["confidence"] < 0.8:
            continue

        # 최신 뉴스 5분 이내 확인
        news_list = sentiment.get("news", [])
        if not news_list:
            continue
        try:
            news_time = datetime.strptime(news_list[0]["pub_time"], "%Y-%m-%d %H:%M")
            if (now - news_time).total_seconds() > 300:
                continue
        except (ValueError, KeyError):
            continue

        price = s.get("current_price", 0)
        if price <= 0 or s.get("volume", 0) < 10_000:
            continue

        pos = open_stock_position(
            ticker  = ticker,
            name    = s["name"],
            entry_price = price,
            reason  = f"뉴스모멘텀:{sentiment['confidence']}",
        )
        if pos:
            entered += 1
            log_fn(
                f"[주식뉴스] {s['name']}({ticker}) 진입 "
                f"뉴스={sentiment['label']}({sentiment['confidence']}) "
                f"진입가={price:,.0f}"
            )
            break

        time.sleep(0.2)

    return entered


# ── 장 전 스크리닝 (08:50) ────────────────────────────────────────────────────

def run_premarket_screening(log_fn=print) -> None:
    """장 전 스크리닝 — 갭 상승 후보 미리 파악."""
    log_fn("[주식] 장 전 스크리닝 시작...")
    top_stocks    = get_kosdaq_realtime(config.STOCK_TOP_N)
    gap_candidates = [
        s for s in top_stocks if s.get("gap_pct", 0) >= config.STOCK_GAP_MIN
    ]
    if gap_candidates:
        log_fn(f"[주식] 갭 후보 {len(gap_candidates)}개:")
        for s in gap_candidates[:10]:
            log_fn(f"  {s['name']}({s['ticker']}) 갭={s['gap_pct']:+.1f}%")
    else:
        log_fn("[주식] 갭 상승 후보 없음")

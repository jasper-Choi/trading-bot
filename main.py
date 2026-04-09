"""
모의투자 봇 — 전체 시장 자동 스캔 + 변동성 돌파 + 추세 추종 (15분봉)

실행 방법:
  python main.py            # 스케줄러 시작 (매 15분 정각 자동 실행)
  python main.py run        # 지금 즉시 1회 실행
  python main.py status     # 현재 포지션 현황 출력
  python main.py history    # 전체 거래 이력 출력

실행 흐름 (매 15분):
  1) screener  — 업비트 전체 KRW 코인 스캔 → 거래대금 상위 30개 추출
  2) fetch     — 각 코인 15분봉 100개 수집
  3) strategy  — 신호 계산 + 강도 점수 (RSI / 거래량급증 / 골든크로스)
  4) select    — 점수 상위 3개 진입 후보 선별
  5) manage    — 기존 포지션 손절·트레일링·시간초과 체크
  6) entry     — 신규 포지션 진입 (최대 3개 동시, 일일 손실 한도 체크)
  7) stock     — 코스닥 갭 상승 스캔 (09:00~09:30만)

로그 형식: [15M] 스캔: X개 → 신호: Y개 → 진입: Z개
"""

import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import schedule
import time
from datetime import datetime

import config
from src.screener       import get_top_krw_coins
from src.stock_screener import get_gap_up_stocks
from src.data_fetcher   import fetch_15m_candles, fetch_current_price
from src.strategy       import compute_indicators, check_entry_signal, effective_stop
from src.position_manager import (
    load_positions,
    get_position,
    get_open_positions,
    count_open_positions,
    open_position,
    close_position,
    update_peak,
    get_candles_held,
    is_time_exit,
    can_open_new_position,
    get_daily_pnl,
    load_history,
)
from src.reporter import log, print_status, print_history

TAG = "[15M]"


def run_15m():
    """매 15분(00·15·30·45분)마다 실행되는 메인 전략 루프."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    log(f"\n{'='*56}")
    log(f"{TAG} 전략 실행 — {now_str}")

    # ── Step 1: 스크리너 ──────────────────────────────────────────────────
    try:
        top_coins  = get_top_krw_coins(config.TOP_COINS_COUNT)
        scan_count = len(top_coins)
        log(f"{TAG} 스크리너: KRW 마켓 스캔 완료 → 상위 {scan_count}개 선정")
    except Exception as exc:
        log(f"{TAG} 스크리너 오류: {exc}")
        top_coins  = []
        scan_count = 0

    top_coin_set = set(top_coins)

    # 오픈 포지션 코인(스크리너 밖으로 나간 코인도 관리 유지)
    open_positions = get_open_positions()
    open_coin_set  = {p["coin"] for p in open_positions}

    # 처리 대상 전체 = 오픈 포지션 코인 + 스크리너 상위 코인
    all_coins = list(open_coin_set | top_coin_set)

    # ── Step 2~4: 데이터 수집 + 전략 계산 ────────────────────────────────
    signal_count   = 0
    entry_count    = 0
    candidates: list[dict] = []     # 진입 후보
    current_prices: dict[str, float] = {}

    for coin in all_coins:
        try:
            df            = fetch_15m_candles(coin, count=config.CANDLE_COUNT)
            df            = compute_indicators(df)
            current_price = float(df.iloc[-1]["close"])
            atr           = float(df.iloc[-1]["atr"])
            current_prices[coin] = current_price

            pos = get_position(coin)

            # ── Step 5: 기존 포지션 관리 ──────────────────────────────
            if pos:
                candles = get_candles_held(pos)

                # 우선순위 1: 시간 초과 청산
                if is_time_exit(pos):
                    closed = close_position(coin, current_price, "시간초과청산")
                    log(
                        f"{TAG}  {coin} 시간초과 청산 "
                        f"({candles}캔들/{config.MAX_HOLD_CANDLES}캔들) "
                        f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                    )
                    continue

                # 우선순위 2: ATR 손절 / 트레일링 스탑
                update_peak(coin, current_price)
                pos  = get_position(coin)
                stop = effective_stop(pos, atr)

                if current_price <= stop:
                    reason = (
                        "ATR손절" if current_price <= pos["stop_loss"]
                        else "트레일링스탑"
                    )
                    closed = close_position(coin, current_price, reason)
                    log(
                        f"{TAG}  {coin} {reason} 청산 "
                        f"({candles}캔들) "
                        f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                    )
                else:
                    pnl       = (current_price - pos["entry_price"]) * pos["quantity"]
                    pnl_pct   = (current_price / pos["entry_price"] - 1) * 100
                    remaining = config.MAX_HOLD_CANDLES - candles
                    log(
                        f"{TAG}  {coin} 유지 "
                        f"({candles}캔들 보유, 잔여 {remaining}캔들) "
                        f"현재가={current_price:,.2f} "
                        f"평가손익={pnl:+,.0f}원 ({pnl_pct:+.2f}%) "
                        f"손절가={stop:,.2f}"
                    )

            # ── Step 3: 신호 계산 (스크리너 상위 코인만) ──────────────
            elif coin in top_coin_set:
                signal = check_entry_signal(df)
                if signal:
                    signal_count += 1
                    candidates.append(
                        {
                            "coin":   coin,
                            "signal": signal,
                            "score":  signal["score"],
                        }
                    )

        except Exception as exc:
            log(f"{TAG}  {coin} 오류: {exc}")

    # ── Step 4: 점수 상위 정렬 → 진입 후보 선별 ─────────────────────────
    candidates.sort(key=lambda x: x["score"], reverse=True)

    if candidates:
        top_label = "  ".join(
            f"{c['coin']}[{c['score']}점]" for c in candidates[:5]
        )
        log(f"{TAG} 진입 후보: {top_label}")

    # ── Step 6: 신규 진입 ─────────────────────────────────────────────────
    for cand in candidates:
        coin   = cand["coin"]
        signal = cand["signal"]

        can_open, reason = can_open_new_position()
        if not can_open:
            log(f"{TAG} 진입 중단 — {reason}")
            break

        # 같은 사이클에서 이미 진입된 코인 재확인
        if get_position(coin):
            continue

        open_position(
            coin        = coin,
            entry_price = signal["entry_price"],
            stop_loss   = signal["stop_loss"],
            atr         = signal["atr"],
        )
        entry_count += 1
        reasons_str = ", ".join(signal["score_reasons"]) or "기본신호"
        log(
            f"{TAG}  {coin} 매수 진입 "
            f"[점수:{signal['score']} — {reasons_str}] "
            f"진입가={signal['entry_price']:,.2f} "
            f"손절가={signal['stop_loss']:,.2f} "
            f"ATR={signal['atr']:,.2f}"
        )

    # ── Step 7: 주식 스크리너 (09:00~09:30만 활성) ────────────────────────
    try:
        gap_stocks = get_gap_up_stocks()
        if gap_stocks:
            log(f"\n[주식스크리너] 코스닥 갭 상승 {len(gap_stocks)}개 종목:")
            for s in gap_stocks[:10]:
                log(
                    f"  {s['name']}({s['ticker']}) "
                    f"+{s['gap_pct']}% "
                    f"시가={s['today_open']:,.0f} "
                    f"전일거래량={s['prev_volume']:,}"
                )
    except Exception as exc:
        log(f"[주식스크리너] 오류: {exc}")

    # ── 요약 로그 ─────────────────────────────────────────────────────────
    daily_pnl  = get_daily_pnl()
    open_cnt   = count_open_positions()
    total_cap  = config.INITIAL_CAPITAL_PER_COIN * config.MAX_POSITIONS
    loss_limit = -(total_cap * config.DAILY_LOSS_LIMIT_PCT)

    log(
        f"\n{TAG} 스캔: {scan_count}개 코인 → "
        f"신호: {signal_count}개 → "
        f"진입: {entry_count}개 | "
        f"보유: {open_cnt}/{config.MAX_POSITIONS} | "
        f"일일손익: {daily_pnl:+,.0f}원 (한도 {loss_limit:,.0f}원)"
    )

    # 현황 출력
    positions = load_positions()
    print_status(positions, current_prices)


def show_status():
    """현재 포지션 현황 + 현재가를 출력합니다."""
    positions      = load_positions()
    current_prices = {}
    for coin in {p["coin"] for p in get_open_positions()}:
        try:
            current_prices[coin] = fetch_current_price(coin)
        except Exception:
            pass
    print_status(positions, current_prices)


def show_history():
    """전체 거래 이력을 출력합니다."""
    print_history(load_history())


def start_scheduler():
    """매 15분 정각(HH:00, HH:15, HH:30, HH:45)에 전략을 실행합니다."""
    log(f"{TAG} 스케줄러 시작 — 매 {config.CANDLE_MINUTES}분(00·15·30·45)마다 실행")
    log(f"{TAG} 스캔: 상위 {config.TOP_COINS_COUNT}개 코인 | "
        f"최대 포지션: {config.MAX_POSITIONS}개 | "
        f"최대 보유: {config.MAX_HOLD_CANDLES}캔들({config.MAX_HOLD_CANDLES * config.CANDLE_MINUTES // 60}시간) | "
        f"일일 손실 한도: -{config.DAILY_LOSS_LIMIT_PCT*100:.0f}%")
    log(f"{TAG} 종료: Ctrl+C\n")

    for minute in ("00", "15", "30", "45"):
        schedule.every().hour.at(f":{minute}").do(run_15m)

    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scheduler"

    if cmd == "run":
        run_15m()
    elif cmd == "status":
        show_status()
    elif cmd == "history":
        show_history()
    else:
        start_scheduler()

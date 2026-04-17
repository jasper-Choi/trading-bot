"""
모의투자 봇 — 멀티 타임프레임 스케줄러 (1분 / 5분 / 15분)

타임프레임 역할:
  1분봉  — 긴급 시장 감지 → 즉시 국면 전환 (BTC 급락/급등)
  5분봉  — 진입/청산 신호 체크 (상위 코인 + 주식)
  15분봉 — 전체 추세 업데이트 + KRW 코인 스캔

실행:
  python main.py            # 스케줄러 시작
  python main.py run        # 즉시 1회 실행 (15분 전략)
  python main.py status     # 포지션 현황
  python main.py history    # 거래 이력
  python main.py stock-test # 주식 스크리너 강제 실행 (시간 제한 무시)
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
from src.screener        import get_top_krw_coins
from src.stock_screener  import get_gap_up_stocks
from src.data_fetcher    import fetch_15m_candles, fetch_5m_candles, fetch_current_price
from src.strategy        import compute_indicators, check_entry_signal, effective_stop
from src.position_manager import (
    load_positions, get_position, get_open_positions,
    count_open_positions, open_position, close_position,
    update_peak, get_candles_held, is_time_exit,
    can_open_new_position, get_daily_pnl, load_history,
    pyramid_position,
)
from src.reporter        import log, print_status, print_history
from src.market_regime   import market_regime
from src.stock_screener  import get_kosdaq_realtime, get_gap_up_stocks
from src.stock_strategy  import (
    get_stock_history,
    manage_stock_positions, run_gap_momentum,
    run_news_momentum, run_premarket_screening,
)
from src.agents.orchestrator import run_agent_cycle
from src.agents.notifier import TelegramNotifier

TAG_1M  = "[1M]"
TAG_5M  = "[5M]"
TAG_15M = "[15M]"

# 스크리너 캐시 (15분마다 갱신, 5분 루프에서 재사용)
_top_coins_cache: list[str] = []


# ── 1분 루프: 긴급 시장 감지 ────────────────────────────────────────────────

def run_1m():
    """BTC 1분봉으로 긴급 국면 체크."""
    TelegramNotifier().send_daily_summary_if_needed(stock_history=get_stock_history())
    new_regime = market_regime.check_1m()
    if new_regime:
        log(
            f"{TAG_1M} 시장 국면 전환 → {new_regime} "
            f"(전환시각: {market_regime.last_changed})"
        )


# ── 5분 루프: 진입/청산 신호 ────────────────────────────────────────────────

def run_5m():
    """5분봉 기반 진입/청산 신호 체크 + 주식 전략."""
    global _top_coins_cache

    now_str  = datetime.now(config.KST).strftime("%Y-%m-%d %H:%M")
    regime   = market_regime.regime
    regime_cfg = market_regime.get_config()
    log(f"\n{TAG_5M} 신호 체크 — {now_str} | 국면:{regime}")

    # ── 코인: 기존 포지션 관리 ─────────────────────────────────────────────
    open_positions = get_open_positions()
    current_prices: dict[str, float] = {}

    for pos in open_positions:
        coin = pos["coin"]
        try:
            df            = fetch_15m_candles(coin, count=config.CANDLE_COUNT)
            df            = compute_indicators(df)
            current_price = float(df.iloc[-1]["close"])
            atr           = float(df.iloc[-1]["atr"])
            current_prices[coin] = current_price

            candles = get_candles_held(pos)

            if is_time_exit(pos):
                closed = close_position(coin, current_price, "시간초과청산")
                log(
                    f"{TAG_5M}  {coin} 시간초과 청산 ({candles}캔들) "
                    f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                )
                continue

            update_peak(coin, current_price)
            pos  = get_position(coin)
            stop = effective_stop(pos, atr)

            if current_price <= stop:
                reason = "ATR손절" if current_price <= pos["stop_loss"] else "트레일링스탑"
                closed = close_position(coin, current_price, reason)
                log(
                    f"{TAG_5M}  {coin} {reason} 청산 ({candles}캔들) "
                    f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                )
            else:
                # 피라미딩 체크 (BULL 모드)
                if regime_cfg.get("pyramiding"):
                    result = pyramid_position(coin, current_price, atr)
                    if result:
                        log(
                            f"{TAG_5M}  {coin} 피라미딩 진입 "
                            f"(+{result['pyramid_count']}회차) "
                            f"추가수량={result['added_quantity']:.4f}"
                        )

                pnl_pct = (current_price / pos["entry_price"] - 1) * 100
                log(
                    f"{TAG_5M}  {coin} 유지 ({candles}캔들) "
                    f"현재가={current_price:,.2f} 손익={pnl_pct:+.2f}%"
                )

        except Exception as exc:
            log(f"{TAG_5M}  {coin} 오류: {exc}")

    # ── 코인: 5분봉 신호로 신규 진입 ──────────────────────────────────────
    top_coins = _top_coins_cache
    candidates: list[dict] = []

    for coin in top_coins:
        if get_position(coin):
            continue
        try:
            df5    = fetch_5m_candles(coin, count=config.CANDLE_COUNT)
            df5    = compute_indicators(df5)
            signal = check_entry_signal(df5)
            if signal:
                candidates.append({"coin": coin, "signal": signal, "score": signal["score"]})
        except Exception:
            pass

    candidates.sort(key=lambda x: x["score"], reverse=True)

    entry_count = 0
    for cand in candidates:
        coin   = cand["coin"]
        signal = cand["signal"]
        can_open, reason = can_open_new_position()
        if not can_open:
            log(f"{TAG_5M} 진입 중단 — {reason}")
            break
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
            f"{TAG_5M}  {coin} 매수 진입 "
            f"[점수:{signal['score']} — {reasons_str}] "
            f"진입가={signal['entry_price']:,.2f}"
        )

    # ── 주식 전략 ──────────────────────────────────────────────────────────
    now = datetime.now(config.KST)
    h, m = now.hour, now.minute

    # 08:50 장 전 스크리닝 (KST)
    if h == 8 and m == 50:
        run_premarket_screening(log)

    try:
        # 09:00~09:30 갭 모멘텀
        gap_entered = run_gap_momentum(log)

        # 09:30~14:30 뉴스 모멘텀
        news_entered = run_news_momentum(log)

        # 주식 포지션 관리
        manage_stock_positions(log)

        if gap_entered or news_entered:
            log(f"{TAG_5M} 주식 진입 — 갭:{gap_entered}건 뉴스:{news_entered}건")
    except Exception as exc:
        import traceback
        log(f"{TAG_5M} 주식 전략 오류: {exc}\n{traceback.format_exc()}")

    # 요약
    daily_pnl  = get_daily_pnl()
    open_cnt   = count_open_positions()
    log(
        f"{TAG_5M} 보유:{open_cnt}/{regime_cfg['max_positions']} | "
        f"일일손익:{daily_pnl:+,.0f}원 | 국면:{regime}"
    )


# ── 15분 루프: 전체 추세 + 코인 스캔 ────────────────────────────────────────

def run_15m():
    """매 15분 전체 KRW 마켓 스캔 + 15분봉 추세 업데이트."""
    global _top_coins_cache

    now_str = datetime.now(config.KST).strftime("%Y-%m-%d %H:%M")
    log(f"\n{'='*56}")
    log(f"{TAG_15M} 전략 실행 — {now_str}")

    try:
        run_agent_cycle(log_fn=log)
    except Exception as exc:
        log(f"{TAG_15M} agent cycle error: {exc}")

    # 15분봉 추세 업데이트
    new_regime = market_regime.check_15m()
    if new_regime:
        log(f"{TAG_15M} 추세 → {new_regime}")

    # 스크리너
    try:
        top_coins = get_top_krw_coins(config.TOP_COINS_COUNT)
        _top_coins_cache = top_coins
        log(f"{TAG_15M} 스크리너: 상위 {len(top_coins)}개 선정")
    except Exception as exc:
        log(f"{TAG_15M} 스크리너 오류: {exc}")
        top_coins = _top_coins_cache

    top_coin_set  = set(top_coins)
    open_coin_set = {p["coin"] for p in get_open_positions()}
    all_coins     = list(open_coin_set | top_coin_set)

    signal_count   = 0
    entry_count    = 0
    candidates: list[dict] = []
    current_prices: dict[str, float] = {}

    for coin in all_coins:
        try:
            df            = fetch_15m_candles(coin, count=config.CANDLE_COUNT)
            df            = compute_indicators(df)
            current_price = float(df.iloc[-1]["close"])
            atr           = float(df.iloc[-1]["atr"])
            current_prices[coin] = current_price

            pos = get_position(coin)

            if pos:
                candles = get_candles_held(pos)
                if is_time_exit(pos):
                    closed = close_position(coin, current_price, "시간초과청산")
                    log(
                        f"{TAG_15M}  {coin} 시간초과 청산 "
                        f"({candles}캔들/{config.MAX_HOLD_CANDLES}캔들) "
                        f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                    )
                    continue

                update_peak(coin, current_price)
                pos  = get_position(coin)
                stop = effective_stop(pos, atr)

                if current_price <= stop:
                    reason = "ATR손절" if current_price <= pos["stop_loss"] else "트레일링스탑"
                    closed = close_position(coin, current_price, reason)
                    log(
                        f"{TAG_15M}  {coin} {reason} 청산 ({candles}캔들) "
                        f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                    )
                else:
                    pnl_pct   = (current_price / pos["entry_price"] - 1) * 100
                    remaining = config.MAX_HOLD_CANDLES - candles
                    log(
                        f"{TAG_15M}  {coin} 유지 ({candles}캔들, 잔여 {remaining}) "
                        f"현재가={current_price:,.2f} 손익={pnl_pct:+.2f}%"
                    )

            elif coin in top_coin_set:
                signal = check_entry_signal(df)
                if signal:
                    signal_count += 1
                    candidates.append({"coin": coin, "signal": signal, "score": signal["score"]})

        except Exception as exc:
            log(f"{TAG_15M}  {coin} 오류: {exc}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    if candidates:
        top_label = "  ".join(f"{c['coin']}[{c['score']}점]" for c in candidates[:5])
        log(f"{TAG_15M} 진입 후보: {top_label}")

    for cand in candidates:
        coin   = cand["coin"]
        signal = cand["signal"]
        can_open, reason = can_open_new_position()
        if not can_open:
            log(f"{TAG_15M} 진입 중단 — {reason}")
            break
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
            f"{TAG_15M}  {coin} 매수 진입 "
            f"[점수:{signal['score']} — {reasons_str}] "
            f"진입가={signal['entry_price']:,.2f} "
            f"손절가={signal['stop_loss']:,.2f}"
        )

    # 주식 스크리너 (09:00~09:30)
    try:
        gap_stocks = get_gap_up_stocks()
        if gap_stocks:
            log(f"\n[주식스크리너] 코스닥 갭 상승 {len(gap_stocks)}개:")
            for s in gap_stocks[:10]:
                log(
                    f"  {s['name']}({s['ticker']}) "
                    f"+{s['gap_pct']}% "
                    f"시가={s['today_open']:,.0f}"
                )
    except Exception as exc:
        log(f"[주식스크리너] 오류: {exc}")

    # 요약
    regime      = market_regime.regime
    regime_cfg  = market_regime.get_config()
    daily_pnl   = get_daily_pnl()
    open_cnt    = count_open_positions()
    total_cap   = config.INITIAL_CAPITAL_PER_COIN * config.MAX_POSITIONS
    loss_limit  = -(total_cap * config.DAILY_LOSS_LIMIT_PCT)

    log(
        f"\n{TAG_15M} 스캔:{len(all_coins)}개 → 신호:{signal_count}개 → 진입:{entry_count}개 | "
        f"보유:{open_cnt}/{regime_cfg['max_positions']} | "
        f"일일손익:{daily_pnl:+,.0f}원 | 국면:{regime}"
    )
    print_status(load_positions(), current_prices)


# ── 현황 / 이력 출력 ─────────────────────────────────────────────────────────

def show_status():
    positions      = load_positions()
    current_prices = {}
    for coin in {p["coin"] for p in get_open_positions()}:
        try:
            current_prices[coin] = fetch_current_price(coin)
        except Exception:
            pass
    print_status(positions, current_prices)

def show_history():
    print_history(load_history())


def run_stock_test():
    """주식 스크리너 강제 실행 — 장 시간/스캔 창 무관하게 테스트."""
    from datetime import datetime
    now_str = datetime.now(config.KST).strftime("%Y-%m-%d %H:%M KST")
    in_market = "장 시간 내" if (9 <= datetime.now(config.KST).hour < 15) else "장 시간 외"
    print(f"\n{'='*60}")
    print(f"[stock-test] 주식 스크리너 강제 테스트")
    print(f"[stock-test] 시각: {now_str} ({in_market})")
    print(f"{'='*60}")

    # 1. 네이버 금융 API 직접 테스트 (verbose=True)
    print("\n[stock-test] ── 1단계: 코스닥 실시간 데이터 조회 (상세 로그) ──")
    from src.stock_screener import get_kosdaq_realtime, get_gap_up_stocks
    all_stocks = get_kosdaq_realtime(config.STOCK_TOP_N, verbose=True)
    print(f"\n[stock-test] 조회 결과: {len(all_stocks)}개 종목")

    if all_stocks:
        print(f"[stock-test] 상위 10개 샘플:")
        for i, s in enumerate(all_stocks[:10], 1):
            print(
                f"  {i:2d}. {s['name']:<12}({s['ticker']}) "
                f"현재가={s['current_price']:>8,.0f} "
                f"갭={s['gap_pct']:+6.2f}% "
                f"거래량={s['volume']:>10,}"
            )
    else:
        print("[stock-test] !! 종목 데이터를 가져오지 못했습니다.")
        print("[stock-test] 가능한 원인:")
        print("  - 네이버 금융 서버 점검 중")
        print("  - 네트워크 연결 문제 (방화벽, 프록시)")
        print("  - HTML 파싱 패턴 변경")
        print("[stock-test] === 종료 ===\n")
        return

    # 2. 갭 상승 필터
    print(f"\n[stock-test] ── 2단계: 갭 +{config.STOCK_GAP_MIN}% 이상 필터 (force=True) ──")
    gap_stocks = get_gap_up_stocks(force=True, verbose=True)
    print(f"[stock-test] 갭 상승 종목: {len(gap_stocks)}개")
    if gap_stocks:
        print("[stock-test] 갭 상승 종목 목록:")
        for s in gap_stocks[:10]:
            print(
                f"  {s['name']:<12}({s['ticker']}) "
                f"갭={s['gap_pct']:+6.2f}% "
                f"현재가={s['current_price']:>8,.0f} "
                f"거래량={s['volume']:>10,}"
            )
    else:
        print(f"[stock-test] 갭 +{config.STOCK_GAP_MIN}% 이상 종목 없음")
        if all_stocks:
            best = all_stocks[0]
            print(f"[stock-test] 현재 최고 갭: {best['name']} ({best['gap_pct']:+.2f}%)")

    # 3. 갭 모멘텀 전략 강제 실행
    print(f"\n[stock-test] ── 3단계: 갭 모멘텀 전략 강제 실행 ──")
    try:
        entered = run_gap_momentum(log_fn=print, force=True)
        print(f"[stock-test] 진입 건수: {entered}건")
    except Exception as exc:
        import traceback
        print(f"[stock-test] 전략 실행 오류: {exc}")
        traceback.print_exc()

    print(f"\n{'='*60}")
    print("[stock-test] === 완료 ===\n")


# ── 스케줄러 시작 ────────────────────────────────────────────────────────────

def start_scheduler():
    log(f"[Scheduler] 시작 — 1분/5분/15분 멀티 타임프레임")
    log(
        f"[Scheduler] 스캔:{config.TOP_COINS_COUNT}개 | "
        f"기본 최대포지션:{config.MAX_POSITIONS}개 | "
        f"일일손실한도:-{config.DAILY_LOSS_LIMIT_PCT*100:.0f}%"
    )
    log("[Scheduler] 종료: Ctrl+C\n")

    # 1분 — 긴급 국면 감지
    schedule.every(1).minutes.do(run_1m)

    # 5분 — 진입/청산 신호
    schedule.every(5).minutes.do(run_5m)

    # 15분 정각 — 전체 스캔
    for minute in ("00", "15", "30", "45"):
        schedule.every().hour.at(f":{minute}").do(run_15m)

    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scheduler"

    # DB 초기화 (테이블 생성 + 파일 마이그레이션)
    from src.database import init_db
    init_db()

    if cmd == "run":
        run_15m()
    elif cmd == "status":
        show_status()
    elif cmd == "history":
        show_history()
    elif cmd == "stock-test":
        run_stock_test()
    else:
        start_scheduler()

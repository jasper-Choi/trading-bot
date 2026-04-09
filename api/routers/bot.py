"""
봇 제어 라우터 — 시작 / 중지 / 상태 조회.

봇은 백그라운드 스레드에서 실행되며 매 15분(00·15·30·45)마다
전체 KRW 마켓을 스캔하고 전략을 수행합니다.
앱 재시작 시 봇은 중지 상태로 초기화됩니다.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter

import config
from src.screener import get_top_krw_coins
from src.data_fetcher import fetch_15m_candles
from src.strategy import compute_indicators, check_entry_signal, effective_stop
from src.position_manager import (
    get_position,
    get_open_positions,
    open_position,
    close_position,
    update_peak,
    get_candles_held,
    is_time_exit,
    can_open_new_position,
)
from src.reporter import log
from api.models import BotStatusOut, BotControlOut

router = APIRouter(prefix="/api/bot", tags=["bot"])

TAG = "[API-Bot]"


# ---------------------------------------------------------------------------
# 봇 상태 싱글턴
# ---------------------------------------------------------------------------

class _BotRunner:
    """백그라운드 스레드에서 15분마다 스캔 전략을 실행하는 클래스."""

    def __init__(self):
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None

    # ── 공개 인터페이스 ──────────────────────────────────────────────────

    def start(self) -> bool:
        if self.running:
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="BotSchedulerThread"
        )
        self._thread.start()
        self.running = True
        return True

    def stop(self) -> bool:
        if not self.running:
            return False
        self._stop_event.set()
        self.running = False
        self.next_run = None
        return True

    def to_status(self) -> BotStatusOut:
        return BotStatusOut(
            running=self.running,
            last_run=self.last_run,
            next_run=self.next_run,
            top_coins_count=config.TOP_COINS_COUNT,
            max_positions=config.MAX_POSITIONS,
        )

    # ── 내부 메서드 ──────────────────────────────────────────────────────

    def _next_scheduled_datetime(self) -> datetime:
        """다음 15분 정각(HH:00·15·30·45) 시각을 계산합니다."""
        now = datetime.now()
        minute = now.minute
        # 다음 15분 배수 찾기
        next_minute = ((minute // 15) + 1) * 15
        if next_minute >= 60:
            next_dt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_dt = now.replace(minute=next_minute, second=0, microsecond=0)
        return next_dt

    def _loop(self):
        """스케줄 루프 — 다음 15분 정각까지 대기 후 전략 실행."""
        while not self._stop_event.is_set():
            next_dt = self._next_scheduled_datetime()
            self.next_run = next_dt.strftime("%Y-%m-%d %H:%M:%S")

            while datetime.now() < next_dt and not self._stop_event.is_set():
                remaining = (next_dt - datetime.now()).total_seconds()
                time.sleep(min(30, max(1, remaining)))

            if self._stop_event.is_set():
                break

            self.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                self._run_strategy()
            except Exception as exc:
                log(f"{TAG} 전략 실행 오류: {exc}")

    def _run_strategy(self):
        """매 15분 실행되는 전략 본체 (main.py run_15m 과 동일 로직)."""
        log(f"{TAG} 전략 실행 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        # Step 1: 스크리너
        try:
            top_coins = get_top_krw_coins(config.TOP_COINS_COUNT)
        except Exception as exc:
            log(f"{TAG} 스크리너 오류: {exc}")
            top_coins = []

        top_coin_set = set(top_coins)
        open_coin_set = {p["coin"] for p in get_open_positions()}
        all_coins = list(open_coin_set | top_coin_set)

        candidates: list[dict] = []

        for coin in all_coins:
            try:
                df = fetch_15m_candles(coin, count=config.CANDLE_COUNT)
                df = compute_indicators(df)
                current_price = float(df.iloc[-1]["close"])
                atr = float(df.iloc[-1]["atr"])

                pos = get_position(coin)

                if pos:
                    candles = get_candles_held(pos)

                    if is_time_exit(pos):
                        closed = close_position(coin, current_price, "시간초과청산")
                        log(
                            f"{TAG} {coin} 시간초과 청산 ({candles}캔들) "
                            f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                        )
                        continue

                    update_peak(coin, current_price)
                    pos = get_position(coin)
                    stop = effective_stop(pos, atr)

                    if current_price <= stop:
                        reason = (
                            "ATR손절" if current_price <= pos["stop_loss"]
                            else "트레일링스탑"
                        )
                        closed = close_position(coin, current_price, reason)
                        log(
                            f"{TAG} {coin} {reason} 청산 ({candles}캔들) "
                            f"손익={closed['pnl']:+,.0f}원 ({closed['pnl_pct']:+.2f}%)"
                        )
                    else:
                        pnl_pct = (current_price / pos["entry_price"] - 1) * 100
                        log(
                            f"{TAG} {coin} 유지 ({candles}캔들) "
                            f"현재가={current_price:,.2f} "
                            f"평가손익={pnl_pct:+.2f}% "
                            f"손절가={stop:,.2f}"
                        )

                elif coin in top_coin_set:
                    signal = check_entry_signal(df)
                    if signal:
                        candidates.append(
                            {"coin": coin, "signal": signal, "score": signal["score"]}
                        )

            except Exception as exc:
                log(f"{TAG} {coin} 오류: {exc}")

        # Step 2: 점수 상위 후보 진입
        candidates.sort(key=lambda x: x["score"], reverse=True)

        for cand in candidates:
            coin = cand["coin"]
            signal = cand["signal"]

            can_open, reason = can_open_new_position()
            if not can_open:
                log(f"{TAG} 진입 중단 — {reason}")
                break

            if get_position(coin):
                continue

            open_position(
                coin=coin,
                entry_price=signal["entry_price"],
                stop_loss=signal["stop_loss"],
                atr=signal["atr"],
            )
            reasons_str = ", ".join(signal["score_reasons"]) or "기본신호"
            log(
                f"{TAG} {coin} 매수 진입 "
                f"[점수:{signal['score']} — {reasons_str}] "
                f"진입가={signal['entry_price']:,.2f} "
                f"손절가={signal['stop_loss']:,.2f}"
            )


# 모듈 수준 싱글턴
bot_runner = _BotRunner()


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

@router.post("/start", response_model=BotControlOut, summary="봇 시작")
def start_bot():
    """백그라운드 15분 스케줄러를 시작합니다."""
    started = bot_runner.start()
    return BotControlOut(
        success=started,
        message="봇을 시작했습니다." if started else "이미 실행 중입니다.",
        status=bot_runner.to_status(),
    )


@router.post("/stop", response_model=BotControlOut, summary="봇 중지")
def stop_bot():
    """백그라운드 스케줄러를 중지합니다."""
    stopped = bot_runner.stop()
    return BotControlOut(
        success=stopped,
        message="봇을 중지했습니다." if stopped else "실행 중이 아닙니다.",
        status=bot_runner.to_status(),
    )

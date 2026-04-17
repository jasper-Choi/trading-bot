"""
봇 제어 라우터 — 시작 / 중지 / 상태 / 시장 국면.

봇은 백그라운드 스레드에서 실행됩니다:
  ・매 1분  — 긴급 시장 국면 감지
  ・매 5분  — 진입/청산 신호 체크
  ・매 15분 — 전체 KRW 마켓 스캔 + 추세 업데이트
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query

import config
from src.screener        import get_top_krw_coins
from src.data_fetcher    import fetch_15m_candles, fetch_5m_candles
from src.strategy        import compute_indicators, check_entry_signal, effective_stop
from src.position_manager import (
    get_position, get_open_positions,
    open_position, close_position,
    update_peak, get_candles_held, is_time_exit,
    can_open_new_position, pyramid_position,
)
from src.market_regime   import market_regime
from src.reporter        import log, get_log_lines
from src.stock_strategy  import (
    get_stock_history,
    manage_stock_positions, run_gap_momentum,
    run_news_momentum, run_premarket_screening,
)
from src.agents.orchestrator import run_agent_cycle
from src.agents.notifier import TelegramNotifier
from api.models          import BotStatusOut, BotControlOut, MarketRegimeOut, LogsOut

router = APIRouter(prefix="/api/bot", tags=["bot"])
TAG    = "[API-Bot]"


# ── 봇 상태 싱글턴 ────────────────────────────────────────────────────────────

class _BotRunner:
    """멀티 타임프레임 백그라운드 봇."""

    def __init__(self):
        self.running:     bool                    = False
        self._thread:     Optional[threading.Thread] = None
        self._stop_event: threading.Event         = threading.Event()
        self.last_run:    Optional[str]           = None
        self.next_run:    Optional[str]           = None
        self._top_coins_cache: list[str]          = []
        self._last_15m:   Optional[datetime]      = None

    # ── 공개 인터페이스 ───────────────────────────────────────────────────

    def start(self) -> bool:
        if self.running:
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="BotThread"
        )
        self._thread.start()
        self.running = True
        return True

    def stop(self) -> bool:
        if not self.running:
            return False
        self._stop_event.set()
        self.running  = False
        self.next_run = None
        return True

    def to_status(self) -> BotStatusOut:
        return BotStatusOut(
            running         = self.running,
            last_run        = self.last_run,
            next_run        = self.next_run,
            top_coins_count = config.TOP_COINS_COUNT,
            max_positions   = config.MAX_POSITIONS,
        )

    # ── 내부 루프 ─────────────────────────────────────────────────────────

    def _next_minute_boundary(self) -> datetime:
        """다음 정각 1분 경계 시각 (KST)."""
        now = datetime.now(config.KST)
        return now.replace(second=0, microsecond=0) + timedelta(minutes=1)

    def _loop(self):
        """1분마다 깨어나 멀티 타임프레임 작업 수행."""
        while not self._stop_event.is_set():
            next_dt = self._next_minute_boundary()
            self.next_run = next_dt.strftime("%Y-%m-%d %H:%M:%S")

            while datetime.now(config.KST) < next_dt and not self._stop_event.is_set():
                remaining = (next_dt - datetime.now(config.KST)).total_seconds()
                time.sleep(min(10, max(1, remaining)))

            if self._stop_event.is_set():
                break

            self.last_run = datetime.now(config.KST).strftime("%Y-%m-%d %H:%M:%S")
            try:
                self._tick()
            except Exception as exc:
                log(f"{TAG} 실행 오류: {exc}")

    def _tick(self):
        """1분 틱 — 1분·5분·15분 작업을 분기합니다."""
        now    = datetime.now(config.KST)
        minute = now.minute
        TelegramNotifier().send_daily_summary_if_needed(stock_history=get_stock_history())

        # 1분마다: 긴급 국면 감지
        new_regime = market_regime.check_1m()
        if new_regime:
            log(f"{TAG} 국면 전환 → {new_regime}")

        # 5분마다: 진입/청산 신호
        if minute % 5 == 0:
            try:
                self._run_5m()
            except Exception as exc:
                log(f"{TAG} 5분 전략 오류: {exc}")

        # 15분마다: 전체 스캔 + 추세 업데이트
        if minute % 15 == 0:
            try:
                self._run_15m()
            except Exception as exc:
                log(f"{TAG} 15분 전략 오류: {exc}")

    def _run_5m(self):
        """5분봉 기반 진입/청산 신호 체크."""
        regime_cfg = market_regime.get_config()
        open_pos   = get_open_positions()

        for pos in open_pos:
            coin = pos["coin"]
            try:
                df    = fetch_15m_candles(coin, count=config.CANDLE_COUNT)
                df    = compute_indicators(df)
                price = float(df.iloc[-1]["close"])
                atr   = float(df.iloc[-1]["atr"])
                candles = get_candles_held(pos)

                if is_time_exit(pos):
                    closed = close_position(coin, price, "시간초과청산")
                    log(f"{TAG} {coin} 시간초과 청산 손익={closed['pnl']:+,.0f}원")
                    continue

                update_peak(coin, price)
                pos  = get_position(coin)
                stop = effective_stop(pos, atr)

                if price <= stop:
                    reason = "ATR손절" if price <= pos["stop_loss"] else "트레일링스탑"
                    closed = close_position(coin, price, reason)
                    log(f"{TAG} {coin} {reason} 손익={closed['pnl']:+,.0f}원")
                elif regime_cfg.get("pyramiding"):
                    result = pyramid_position(coin, price, atr)
                    if result:
                        log(f"{TAG} {coin} 피라미딩 +{result['pyramid_count']}회차")
            except Exception as exc:
                log(f"{TAG} {coin} 5분 오류: {exc}")

        # 5분봉 신호 체크
        candidates: list[dict] = []
        for coin in self._top_coins_cache:
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
        for cand in candidates:
            can_open, reason = can_open_new_position()
            if not can_open:
                break
            coin   = cand["coin"]
            signal = cand["signal"]
            if get_position(coin):
                continue
            open_position(
                coin        = coin,
                entry_price = signal["entry_price"],
                stop_loss   = signal["stop_loss"],
                atr         = signal["atr"],
            )
            reasons_str = ", ".join(signal["score_reasons"]) or "기본신호"
            log(
                f"{TAG} {coin} 매수 진입 "
                f"[점수:{signal['score']} — {reasons_str}] "
                f"진입가={signal['entry_price']:,.2f}"
            )

        # ── 주식 전략 (KST 기준) ───────────────────────────────────────────────
        now_kst = datetime.now(config.KST)
        h, m    = now_kst.hour, now_kst.minute

        # 08:50 장 전 스크리닝
        if h == 8 and m == 50:
            try:
                run_premarket_screening(log)
            except Exception as exc:
                log(f"{TAG} 주식 장전스크리닝 오류: {exc}")

        try:
            gap_entered  = run_gap_momentum(log)
            news_entered = run_news_momentum(log)
            manage_stock_positions(log)
            if gap_entered or news_entered:
                log(f"{TAG} 주식 진입 — 갭:{gap_entered}건 뉴스:{news_entered}건")
        except Exception as exc:
            import traceback
            log(f"{TAG} 주식 전략 오류: {exc}\n{traceback.format_exc()}")

    def _run_15m(self):
        """15분 전체 스캔 + 추세 업데이트."""
        try:
            run_agent_cycle(log_fn=log)
        except Exception as exc:
            log(f"{TAG} agent cycle error: {exc}")

        new_regime = market_regime.check_15m()
        if new_regime:
            log(f"{TAG} 추세 → {new_regime}")

        try:
            top_coins = get_top_krw_coins(config.TOP_COINS_COUNT)
            self._top_coins_cache = top_coins
        except Exception as exc:
            log(f"{TAG} 스크리너 오류: {exc}")
            return

        top_coin_set  = set(top_coins)
        open_coin_set = {p["coin"] for p in get_open_positions()}
        all_coins     = list(open_coin_set | top_coin_set)

        candidates: list[dict] = []
        for coin in all_coins:
            try:
                df    = fetch_15m_candles(coin, count=config.CANDLE_COUNT)
                df    = compute_indicators(df)
                price = float(df.iloc[-1]["close"])
                atr   = float(df.iloc[-1]["atr"])
                pos   = get_position(coin)

                if pos:
                    candles = get_candles_held(pos)
                    if is_time_exit(pos):
                        closed = close_position(coin, price, "시간초과청산")
                        log(f"{TAG} {coin} 시간초과 청산 ({candles}캔들) 손익={closed['pnl']:+,.0f}원")
                        continue
                    update_peak(coin, price)
                    pos  = get_position(coin)
                    stop = effective_stop(pos, atr)
                    if price <= stop:
                        reason = "ATR손절" if price <= pos["stop_loss"] else "트레일링스탑"
                        closed = close_position(coin, price, reason)
                        log(f"{TAG} {coin} {reason} 손익={closed['pnl']:+,.0f}원")
                    else:
                        pnl_pct = (price / pos["entry_price"] - 1) * 100
                        log(f"{TAG} {coin} 유지 ({candles}캔들) 손익={pnl_pct:+.2f}%")

                elif coin in top_coin_set:
                    signal = check_entry_signal(df)
                    if signal:
                        candidates.append({"coin": coin, "signal": signal, "score": signal["score"]})

            except Exception as exc:
                log(f"{TAG} {coin} 15분 오류: {exc}")

        candidates.sort(key=lambda x: x["score"], reverse=True)
        for cand in candidates:
            can_open, reason = can_open_new_position()
            if not can_open:
                log(f"{TAG} 진입 중단 — {reason}")
                break
            coin   = cand["coin"]
            signal = cand["signal"]
            if get_position(coin):
                continue
            open_position(
                coin        = coin,
                entry_price = signal["entry_price"],
                stop_loss   = signal["stop_loss"],
                atr         = signal["atr"],
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


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/start", response_model=BotControlOut, summary="봇 시작")
def start_bot():
    started = bot_runner.start()
    return BotControlOut(
        success = started,
        message = "봇을 시작했습니다." if started else "이미 실행 중입니다.",
        status  = bot_runner.to_status(),
    )


@router.post("/stop", response_model=BotControlOut, summary="봇 중지")
def stop_bot():
    stopped = bot_runner.stop()
    return BotControlOut(
        success = stopped,
        message = "봇을 중지했습니다." if stopped else "실행 중이 아닙니다.",
        status  = bot_runner.to_status(),
    )


@router.get("/logs", response_model=LogsOut, summary="봇 로그 조회")
def get_bot_logs(lines: int = Query(50, ge=1, le=500, description="반환할 로그 줄 수")):
    """DB에서 최근 로그를 반환합니다 (인메모리 버퍼 fallback)."""
    try:
        from src.database import db_load_logs
        recent = db_load_logs(n=lines)
        if recent:
            return LogsOut(lines=recent, total_lines=len(recent))
    except Exception:
        pass
    recent = get_log_lines(lines)
    return LogsOut(lines=recent, total_lines=len(recent))


@router.get("/market-regime", response_model=MarketRegimeOut, summary="현재 시장 국면")
def get_market_regime():
    """현재 시장 국면과 국면별 설정을 반환합니다."""
    regime     = market_regime.regime
    cfg        = market_regime.get_config()
    return MarketRegimeOut(
        regime             = regime,
        positions_allowed  = cfg["max_positions"],
        risk_pct           = cfg["risk_pct"],
        pyramiding         = cfg["pyramiding"],
        last_changed       = market_regime.last_changed,
    )

from __future__ import annotations

import threading
import time
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import DATA_DIR, settings
from app.core.state_store import rapid_guard_crypto_positions, rapid_guard_korea_positions, run_ppp_minute_scanner
from app.notifier import notifier
from app.orchestrator import CompanyOrchestrator
from app.services.hot_path_guard import (
    hot_process_crypto_tick,
    hot_runtime_symbols,
    refresh_hot_crypto_positions,
    refresh_hot_entry_candidates,
)
from app.services.hot_path_metrics import record_hot_path_event, reset_hot_path_metrics
from app.services.kis_stream_cache import (
    ensure_stream_started as _kis_ensure_stream,
    get_latest_prices as _kis_get_latest_prices,
    register_tick_callback as _kis_register_tick_callback,
    subscribe_tickers as _kis_subscribe_tickers,
)
from app.services.market_gateway import get_upbit_ticker_prices
from app.services.session_clock import current_session_snapshot
from app.services.upbit_stream_cache import register_trade_callback, start_upbit_ticker_stream, upbit_stream_status


# ── 장 마감 리포트 중복 방지 ─────────────────────────────────────────────────
_korea_close_reported: str = ""   # 마지막 리포트 보낸 날짜 "YYYY-MM-DD" (KST)
_us_close_reported: str = ""      # 마지막 리포트 보낸 날짜 "YYYY-MM-DD" (ET)

_tick_guard_lock = threading.Lock()
_tick_guard_symbols: set[str] = set()
_tick_guard_symbols_loaded_at = 0.0
_tick_guard_last_by_symbol: dict[str, float] = {}
_runtime_lock_handle = None

# ── Korea KIS tick guard ─────────────────────────────────────────────────────
_korea_tick_guard_lock = threading.Lock()
_korea_tick_last_by_symbol: dict[str, float] = {}
_KOREA_TICK_THROTTLE_S = 0.5   # 종목당 최소 0.5초 간격 (연속 틱 중복 차단)


def _acquire_runtime_singleton_lock() -> bool:
    """Prevent stale/manual runtime processes from trading beside systemd."""
    global _runtime_lock_handle
    lock_path = Path(DATA_DIR) / "trading_runtime.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _runtime_lock_handle = handle
    return True


def _active_crypto_guard_symbols_cached() -> set[str]:
    global _tick_guard_symbols, _tick_guard_symbols_loaded_at
    now = time.monotonic()
    if now - _tick_guard_symbols_loaded_at <= 1.0:
        return _tick_guard_symbols
    try:
        _tick_guard_symbols = hot_runtime_symbols()
        _tick_guard_symbols_loaded_at = now
    except Exception as exc:
        print(f"[runtime] tick guard symbol refresh failed: {exc}")
    return _tick_guard_symbols


def _run_crypto_tick_guard_from_trade(row: dict) -> None:
    """Event-driven guard: react to trade ticks instead of waiting for the next sleep poll."""
    started_perf = time.perf_counter()
    started_epoch = time.time()
    symbol = str(row.get("market") or "").strip()
    price = float(row.get("trade_price") or 0.0)
    if not symbol or price <= 0:
        return
    tick_epoch = float(row.get("received_at") or started_epoch)
    if symbol not in _active_crypto_guard_symbols_cached():
        return
    now = time.monotonic()
    last = _tick_guard_last_by_symbol.get(symbol, 0.0)
    if now - last < 0.45:
        record_hot_path_event(
            {
                "symbol": symbol,
                "reason": "throttled",
                "dispatch_ms": round((started_epoch - tick_epoch) * 1000, 3),
                "guard_ms": None,
                "total_ms": round((time.perf_counter() - started_perf) * 1000, 3),
                "closed": False,
            }
        )
        return
    _tick_guard_last_by_symbol[symbol] = now
    if not _tick_guard_lock.acquire(blocking=False):
        record_hot_path_event(
            {
                "symbol": symbol,
                "reason": "lock_busy",
                "dispatch_ms": round((started_epoch - tick_epoch) * 1000, 3),
                "guard_ms": None,
                "total_ms": round((time.perf_counter() - started_perf) * 1000, 3),
                "closed": False,
            }
        )
        return
    try:
        guard_started = time.perf_counter()
        summary = hot_process_crypto_tick(symbol, price)
        guard_ms = round((time.perf_counter() - guard_started) * 1000, 3)
        closed = bool(summary.get("paper_closed") or summary.get("live_closed"))
        opened = bool(summary.get("entry_opened"))
        guard_reason = str(summary.get("reason") or ("closed" if closed else "checked"))
        record_hot_path_event(
            {
                "symbol": symbol,
                "reason": "closed" if closed else "opened" if opened else guard_reason,
                "dispatch_ms": round((started_epoch - tick_epoch) * 1000, 3),
                "guard_ms": guard_ms,
                "total_ms": round((time.perf_counter() - started_perf) * 1000, 3),
                "closed": closed,
                "opened": opened,
                "guard_reason": guard_reason,
                "paper_closed": int(summary.get("paper_closed", 0) or 0),
                "live_closed": int(summary.get("live_closed", 0) or 0),
                "entry_opened": int(summary.get("entry_opened", 0) or 0),
            }
        )
        if summary.get("paper_closed") or summary.get("live_closed"):
            print(
                "[runtime] crypto tick guard closed "
                f"{symbol} paper={summary.get('paper_closed', 0)} live={summary.get('live_closed', 0)}"
            )
        if summary.get("entry_opened"):
            print(
                "[runtime] crypto tick guard opened "
                f"{symbol} size={summary.get('size', 0)} score={summary.get('combined_score', 0)}"
            )
    except Exception as exc:
        record_hot_path_event(
            {
                "symbol": symbol,
                "reason": "error",
                "dispatch_ms": round((started_epoch - tick_epoch) * 1000, 3),
                "guard_ms": None,
                "total_ms": round((time.perf_counter() - started_perf) * 1000, 3),
                "closed": False,
                "error": str(exc)[:120],
            }
        )
        print(f"[runtime] crypto tick guard failed: {exc}")
    finally:
        _tick_guard_lock.release()


def _run_korea_tick_guard_from_tick(tick: dict) -> None:
    """KIS H0STCNT0 체결 틱 → Korea 포지션 즉시 stop/trail 체크.

    Upbit _run_crypto_tick_guard_from_trade 와 동일 구조:
      - 종목당 0.5s 쓰로틀
      - 비차단 잠금(blocking=False): 기존 처리 중이면 틱 스킵
    """
    ticker = str(tick.get("ticker") or "").strip()
    price = float(tick.get("price") or 0.0)
    if not ticker or price <= 0:
        return
    now = time.monotonic()
    last = _korea_tick_last_by_symbol.get(ticker, 0.0)
    if now - last < _KOREA_TICK_THROTTLE_S:
        return
    _korea_tick_last_by_symbol[ticker] = now
    if not _korea_tick_guard_lock.acquire(blocking=False):
        return
    try:
        summary = rapid_guard_korea_positions({ticker: price})
        if summary.get("paper_closed"):
            print(
                f"[runtime] korea tick guard closed {ticker} "
                f"paper={summary['paper_closed']}"
            )
    except Exception as exc:
        print(f"[runtime] korea tick guard failed: {exc}")
    finally:
        _korea_tick_guard_lock.release()


def _refresh_korea_kis_subscriptions() -> None:
    """오픈 Korea 포지션을 KIS WebSocket에 구독 (신규 진입 종목 자동 추가)."""
    if not settings.kis_app_key or not settings.kis_app_secret:
        return
    try:
        from app.core.state_store import SessionLocal, PaperPositionRecord
        from sqlalchemy import select
        with SessionLocal() as db:
            tickers = [
                row.symbol for row in db.execute(
                    select(PaperPositionRecord).where(
                        PaperPositionRecord.status == "open",
                        PaperPositionRecord.desk == "korea",
                    )
                ).scalars().all()
                if row.symbol
            ]
        if tickers:
            _kis_subscribe_tickers(tickers)
    except Exception as exc:
        print(f"[runtime] KIS subscription refresh failed: {exc}")


def _determine_runtime_interval_seconds(session: dict) -> int:
    if settings.active_desk_set == {"crypto"}:
        return max(5, settings.crypto_fast_cycle_seconds)
    if session.get("korea_opening_window") or session.get("us_regular"):
        return max(10, settings.realtime_active_interval_seconds)
    if session.get("korea_open") or session.get("us_premarket") or session.get("crypto_focus"):
        return max(15, settings.realtime_watch_interval_seconds)
    return max(30, settings.realtime_idle_interval_seconds)


def _run_crypto_rapid_guard() -> dict:
    positions = refresh_hot_crypto_positions()
    symbols = list(positions)
    if not symbols:
        return {"checked": 0, "paper_closed": 0, "live_closed": 0}
    prices = get_upbit_ticker_prices(symbols)
    if not prices:
        return {"checked": 0, "paper_closed": 0, "live_closed": 0}
    return rapid_guard_crypto_positions(prices)


def _sleep_with_rapid_guards(interval_seconds: int) -> None:
    if settings.active_desk_set != {"crypto"}:
        time.sleep(interval_seconds)
        return
    guard_interval = max(2, int(settings.crypto_rapid_guard_seconds))
    deadline = time.monotonic() + max(interval_seconds, 0)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(guard_interval, remaining))
        try:
            summary = _run_crypto_rapid_guard()
            if summary.get("paper_closed") or summary.get("live_closed"):
                print(
                    "[runtime] crypto rapid guard closed "
                    f"paper={summary.get('paper_closed', 0)} live={summary.get('live_closed', 0)}"
                )
        except Exception as exc:
            print(f"[runtime] crypto rapid guard failed: {exc}")


def _check_market_close_reports() -> None:
    """한국/미국 장 마감 직후 당일 거래 종합 요약을 텔레그램으로 발송.

    - 한국: KST 15:35~16:15 사이에 오늘 KST 날짜로 1회
    - 미국: ET  16:05~17:00 사이에 오늘 ET  날짜로 1회
    """
    global _korea_close_reported, _us_close_reported

    try:
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        kst_date = now_kst.strftime("%Y-%m-%d")
        kst_hm = (now_kst.hour, now_kst.minute)

        # 한국 장 마감: 15:30 KST → 15:35~16:15 사이 전송
        if (15, 35) <= kst_hm <= (16, 15):
            if _korea_close_reported != kst_date:
                if "korea" in settings.active_desk_set:
                    sent = notifier.send_market_close_report("korea", kst_date)
                    if sent:
                        print(f"[runtime] korea market close report sent for {kst_date}")
                _korea_close_reported = kst_date

        now_et = datetime.now(ZoneInfo("America/New_York"))
        et_date = now_et.strftime("%Y-%m-%d")
        et_hm = (now_et.hour, now_et.minute)

        # 미국 장 마감: 16:00 ET → 16:05~17:00 사이 전송
        if (16, 5) <= et_hm <= (17, 0):
            if _us_close_reported != et_date:
                if "us" in settings.active_desk_set:
                    sent = notifier.send_market_close_report("us", et_date)
                    if sent:
                        print(f"[runtime] us market close report sent for {et_date}")
                _us_close_reported = et_date

    except Exception as exc:
        print(f"[runtime] market close report check failed: {exc}")


_ppp_last_scan_ts: float = 0.0          # 마지막 분봉 스캔 시각
_PPP_SCAN_INTERVAL: float = 60.0       # 60초마다 분봉 스캔


def run_company_loop() -> None:
    global _ppp_last_scan_ts
    if not _acquire_runtime_singleton_lock():
        print("[runtime] another trading runtime is already active; exiting duplicate process")
        return
    orchestrator = CompanyOrchestrator()

    # ── Upbit WebSocket 틱 스트림 (코인) — active_desk 무관하게 항상 기동 ──────
    if settings.upbit_ws_enabled:
        reset_hot_path_metrics()
        refresh_hot_crypto_positions(force=True)
        refresh_hot_entry_candidates({}, force=True)
        register_trade_callback(_run_crypto_tick_guard_from_trade)
        started = start_upbit_ticker_stream()
        status = upbit_stream_status()
        print(
            "[runtime] upbit websocket cache "
            f"started={started} running={status.get('running')} cached={status.get('cached_count')}"
        )

    # ── KIS WebSocket 틱 스트림 (한국 주식) ────────────────────────────────────
    if settings.kis_app_key and settings.kis_app_secret:
        _kis_register_tick_callback(_run_korea_tick_guard_from_tick)
        _kis_ensure_stream()
        _refresh_korea_kis_subscriptions()
        print("[runtime] KIS tick stream registered (korea rapid guard active)")

    print(
        "[runtime] starting reactive company loop "
        f"(active={settings.realtime_active_interval_seconds}s, "
        f"watch={settings.realtime_watch_interval_seconds}s, "
        f"idle={settings.realtime_idle_interval_seconds}s)"
    )

    while True:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        interval_seconds = settings.realtime_idle_interval_seconds
        try:
            result = orchestrator.run_cycle()
            state = result["state"]
            if settings.upbit_ws_enabled:
                refresh_hot_crypto_positions(force=True)
                refresh_hot_entry_candidates(state, force=True)
            # 신규 Korea 포지션 → KIS WebSocket 구독 갱신
            if settings.kis_app_key and settings.kis_app_secret:
                _refresh_korea_kis_subscriptions()
            session = state.get("session_state", {}) or current_session_snapshot()
            interval_seconds = _determine_runtime_interval_seconds(session)
            print(
                f"[runtime] {started_at} stance={state['stance']} "
                f"regime={state['regime']} risk_budget={state['risk_budget']} "
                f"phase={session.get('market_phase', 'n/a')} next={interval_seconds}s"
            )
            _check_market_close_reports()

            # ── 분봉 PPP 스캐너 (60초마다 실행) ────────────────────────────
            import time as _time_mod
            _now_ts = _time_mod.time()
            if "korea" in settings.active_desk_set and (_now_ts - _ppp_last_scan_ts) >= _PPP_SCAN_INTERVAL:
                _ppp_last_scan_ts = _now_ts
                try:
                    _ms = result.get("state", {}).get("market_snapshot") or {}
                    _ppp_result = run_ppp_minute_scanner(_ms)
                    if _ppp_result.get("orders_created", 0):
                        print(
                            f"[runtime] ppp_scanner: signals={_ppp_result['signals']} "
                            f"orders={_ppp_result['orders_created']}"
                        )
                except Exception as _ppp_exc:
                    print(f"[runtime] ppp_scanner failed: {_ppp_exc}")

        except Exception as exc:
            print(f"[runtime] {started_at} cycle failed: {exc}")
            notifier.send_error(f"{started_at} cycle failed: {exc}")
            interval_seconds = max(30, settings.realtime_idle_interval_seconds)
        _sleep_with_rapid_guards(interval_seconds)


if __name__ == "__main__":
    run_company_loop()

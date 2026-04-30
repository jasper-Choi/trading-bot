from __future__ import annotations

import threading
import time
from datetime import datetime

from app.config import settings
from app.core.state_store import load_crypto_rapid_guard_symbols, rapid_guard_crypto_positions
from app.notifier import notifier
from app.orchestrator import CompanyOrchestrator
from app.services.market_gateway import get_upbit_ticker_prices
from app.services.session_clock import current_session_snapshot
from app.services.upbit_stream_cache import register_trade_callback, start_upbit_ticker_stream, upbit_stream_status


_tick_guard_lock = threading.Lock()
_tick_guard_symbols: set[str] = set()
_tick_guard_symbols_loaded_at = 0.0
_tick_guard_last_by_symbol: dict[str, float] = {}


def _active_crypto_guard_symbols_cached() -> set[str]:
    global _tick_guard_symbols, _tick_guard_symbols_loaded_at
    now = time.monotonic()
    if now - _tick_guard_symbols_loaded_at <= 1.0:
        return _tick_guard_symbols
    try:
        _tick_guard_symbols = set(load_crypto_rapid_guard_symbols())
        _tick_guard_symbols_loaded_at = now
    except Exception as exc:
        print(f"[runtime] tick guard symbol refresh failed: {exc}")
    return _tick_guard_symbols


def _run_crypto_tick_guard_from_trade(row: dict) -> None:
    """Event-driven guard: react to trade ticks instead of waiting for the next sleep poll."""
    symbol = str(row.get("market") or "").strip()
    price = float(row.get("trade_price") or 0.0)
    if not symbol or price <= 0:
        return
    if symbol not in _active_crypto_guard_symbols_cached():
        return
    now = time.monotonic()
    last = _tick_guard_last_by_symbol.get(symbol, 0.0)
    if now - last < 0.45:
        return
    _tick_guard_last_by_symbol[symbol] = now
    if not _tick_guard_lock.acquire(blocking=False):
        return
    try:
        summary = rapid_guard_crypto_positions({symbol: price})
        if summary.get("paper_closed") or summary.get("live_closed"):
            print(
                "[runtime] crypto tick guard closed "
                f"{symbol} paper={summary.get('paper_closed', 0)} live={summary.get('live_closed', 0)}"
            )
    except Exception as exc:
        print(f"[runtime] crypto tick guard failed: {exc}")
    finally:
        _tick_guard_lock.release()


def _determine_runtime_interval_seconds(session: dict) -> int:
    if settings.active_desk_set == {"crypto"}:
        return max(5, settings.crypto_fast_cycle_seconds)
    if session.get("korea_opening_window") or session.get("us_regular"):
        return max(10, settings.realtime_active_interval_seconds)
    if session.get("korea_open") or session.get("us_premarket") or session.get("crypto_focus"):
        return max(15, settings.realtime_watch_interval_seconds)
    return max(30, settings.realtime_idle_interval_seconds)


def _run_crypto_rapid_guard() -> dict:
    symbols = load_crypto_rapid_guard_symbols()
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


def run_company_loop() -> None:
    orchestrator = CompanyOrchestrator()
    if settings.active_desk_set == {"crypto"} and settings.upbit_ws_enabled:
        register_trade_callback(_run_crypto_tick_guard_from_trade)
        started = start_upbit_ticker_stream()
        status = upbit_stream_status()
        print(
            "[runtime] upbit websocket cache "
            f"started={started} running={status.get('running')} cached={status.get('cached_count')}"
        )
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
            session = state.get("session_state", {}) or current_session_snapshot()
            interval_seconds = _determine_runtime_interval_seconds(session)
            print(
                f"[runtime] {started_at} stance={state['stance']} "
                f"regime={state['regime']} risk_budget={state['risk_budget']} "
                f"phase={session.get('market_phase', 'n/a')} next={interval_seconds}s"
            )
        except Exception as exc:
            print(f"[runtime] {started_at} cycle failed: {exc}")
            notifier.send_error(f"{started_at} cycle failed: {exc}")
            interval_seconds = max(30, settings.realtime_idle_interval_seconds)
        _sleep_with_rapid_guards(interval_seconds)


if __name__ == "__main__":
    run_company_loop()

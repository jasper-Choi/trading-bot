from __future__ import annotations

import time
from datetime import datetime

from app.config import settings
from app.notifier import notifier
from app.orchestrator import CompanyOrchestrator
from app.services.session_clock import current_session_snapshot


def _determine_runtime_interval_seconds(session: dict) -> int:
    if session.get("korea_opening_window") or session.get("us_regular"):
        return max(10, settings.realtime_active_interval_seconds)
    if session.get("korea_open") or session.get("us_premarket") or session.get("crypto_focus"):
        return max(15, settings.realtime_watch_interval_seconds)
    return max(30, settings.realtime_idle_interval_seconds)


def run_company_loop() -> None:
    orchestrator = CompanyOrchestrator()
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
        time.sleep(interval_seconds)


if __name__ == "__main__":
    run_company_loop()

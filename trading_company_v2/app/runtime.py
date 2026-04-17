from __future__ import annotations

import time
from datetime import datetime

from app.config import settings
from app.notifier import notifier
from app.orchestrator import CompanyOrchestrator


def run_company_loop() -> None:
    orchestrator = CompanyOrchestrator()
    interval_seconds = max(60, settings.cycle_interval_minutes * 60)
    print(f"[runtime] starting company loop every {settings.cycle_interval_minutes} minutes")

    while True:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            result = orchestrator.run_cycle()
            state = result["state"]
            print(
                f"[runtime] {started_at} stance={state['stance']} "
                f"regime={state['regime']} risk_budget={state['risk_budget']}"
            )
        except Exception as exc:
            print(f"[runtime] {started_at} cycle failed: {exc}")
            notifier.send_error(f"{started_at} cycle failed: {exc}")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    run_company_loop()

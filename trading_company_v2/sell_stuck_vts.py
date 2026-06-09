#!/usr/bin/env python3
"""
One-time script: sell stuck VTS positions (307950, 069500, 035420).
Scheduled at 09:05 KST (00:05 UTC) on 2026-06-10.
Auto-removes cron entry on success.
"""
import sys, json, logging, sqlite3, subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

sys.path.insert(0, "/home/ubuntu/trading-bot/trading_company_v2")

from app.core.models import PaperOrder
from app.services import kis_broker

SYMBOLS = ["307950", "069500", "035420"]  # 307950, KODEX200, NAVER

def sell_one(symbol: str) -> bool:
    order = PaperOrder(
        desk="korea",
        action="reduce_risk",
        focus="manual_vts_clearance",
        size="1.00x",
        symbol=symbol,
        strategy_id="manual",
        entry_profile="manual_vts_sell",
        rationale=["stuck VTS position clearance — scheduled 09:05 KST"],
    )
    result = kis_broker.place_order(order)
    log.info("%s: ok=%s mode=%s detail=%s", symbol, result.ok, result.request_mode,
             json.dumps(result.detail, ensure_ascii=False))
    return result.ok

def close_paper_position(symbol: str) -> None:
    db_path = "/home/ubuntu/trading-bot/trading_company_v2/data/trading_company_v2.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE paper_positions SET status='closed', closed_reason='manual_vts_sell_script', "
        "closed_at=datetime('now') WHERE symbol=? AND status='open' AND entry_profile='kis_hold'",
        (symbol,)
    )
    conn.commit()
    conn.close()
    log.info("%s paper_position closed.", symbol)

def main():
    all_ok = True
    for sym in SYMBOLS:
        ok = sell_one(sym)
        if ok:
            close_paper_position(sym)
        else:
            all_ok = False

    if all_ok:
        log.info("All symbols sold. Removing cron entry.")
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            lines = [l for l in result.stdout.splitlines() if "sell_stuck_vts.py" not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)
        except Exception as e:
            log.warning("Cron removal failed: %s", e)
    else:
        log.error("Some sells failed — cron entry kept for next run.")

if __name__ == "__main__":
    main()

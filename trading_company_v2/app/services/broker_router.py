from __future__ import annotations

from app.config import settings
from app.core.models import PaperOrder
from app.services.kis_broker import place_order as place_kis_order
from app.services.upbit_broker import place_order as place_upbit_order

SUPPORTED_EXECUTION_MODES = {"paper", "upbit_live", "kis_live"}


def normalize_execution_mode(mode: str) -> str:
    normalized = str(mode or "paper").strip().lower()
    return normalized if normalized in SUPPORTED_EXECUTION_MODES else "paper"


def route_orders(orders: list[PaperOrder], requested_mode: str) -> dict:
    mode = normalize_execution_mode(requested_mode)
    active_orders = [order for order in orders if order.status == "planned"]
    warnings: list[str] = []
    routed_orders: list[dict] = []
    skipped_orders: list[dict] = []

    if mode == "paper":
        return {
            "requested_mode": mode,
            "applied_mode": "paper",
            "broker_live": False,
            "routed_orders": 0,
            "skipped_orders": 0,
            "warnings": [],
            "details": [],
        }

    if mode == "upbit_live":
        if not settings.upbit_access_key or not settings.upbit_secret_key:
            warnings.append("Upbit live requested but API credentials are missing; paper fallback applied")
            return _paper_fallback(mode, active_orders, warnings)
        if settings.live_capital_krw <= 0:
            warnings.append("Upbit live requested but LIVE_CAPITAL_KRW is not configured; paper fallback applied")
            return _paper_fallback(mode, active_orders, warnings)
        details: list[dict] = []
        routed_orders = 0
        skipped_orders = 0
        applied_mode = "upbit_live" if settings.upbit_allow_live else "paper"
        unsupported_live_orders = 0
        for order in active_orders:
            result = place_upbit_order(order)
            details.append(result.detail)
            if result.ok:
                routed_orders += 1
            else:
                skipped_orders += 1
                if result.detail.get("reason") == "unsupported_desk_for_upbit":
                    unsupported_live_orders += 1
        if unsupported_live_orders:
            warnings.append(f"{unsupported_live_orders} order(s) target desks unsupported by Upbit and were not routed")
        if applied_mode != "upbit_live":
            warnings.append("Upbit credentials are present, but UPBIT_ALLOW_LIVE is false; paper fallback applied")
        if routed_orders == 0:
            applied_mode = "paper"
        return {
            "requested_mode": mode,
            "applied_mode": applied_mode,
            "broker_live": routed_orders > 0 and settings.upbit_allow_live,
            "routed_orders": routed_orders,
            "skipped_orders": skipped_orders,
            "warnings": warnings,
            "details": details,
        }

    if mode == "kis_live":
        missing = [
            name
            for name, value in (
                ("KIS_APP_KEY", settings.kis_app_key),
                ("KIS_APP_SECRET", settings.kis_app_secret),
                ("KIS_ACCOUNT_NO", settings.kis_account_no),
                ("KIS_PRODUCT_CODE", settings.kis_product_code),
            )
            if not value
        ]
        if missing:
            warnings.append(f"KIS live requested but credentials are missing: {', '.join(missing)}; paper fallback applied")
            return _paper_fallback(mode, active_orders, warnings)
        if settings.live_capital_krw <= 0:
            warnings.append("KIS live requested but LIVE_CAPITAL_KRW is not configured; paper fallback applied")
            return _paper_fallback(mode, active_orders, warnings)
        if not settings.kis_allow_live:
            warnings.append("KIS credentials are present, but KIS_ALLOW_LIVE is false; paper fallback applied")
            return _paper_fallback(mode, active_orders, warnings)
        details: list[dict] = []
        routed_orders = 0
        skipped_orders = 0
        unsupported_live_orders = 0
        for order in active_orders:
            result = place_kis_order(order)
            details.append(result.detail)
            if result.ok:
                routed_orders += 1
            else:
                skipped_orders += 1
                if result.detail.get("reason") == "unsupported_desk_for_kis":
                    unsupported_live_orders += 1
        if unsupported_live_orders:
            warnings.append(f"{unsupported_live_orders} order(s) target desks unsupported by KIS and were not routed")
        applied_mode = "kis_live" if routed_orders > 0 else "paper"
        return {
            "requested_mode": mode,
            "applied_mode": applied_mode,
            "broker_live": routed_orders > 0,
            "routed_orders": routed_orders,
            "skipped_orders": skipped_orders,
            "warnings": warnings,
            "details": details,
        }

    warnings.append(f"Unknown execution mode '{requested_mode}', paper fallback applied")
    return _paper_fallback(mode, active_orders, warnings)


def _paper_fallback(requested_mode: str, active_orders: list[PaperOrder], warnings: list[str]) -> dict:
    details = [
        {
            "desk": order.desk,
            "symbol": order.symbol,
            "action": order.action,
            "size": order.size,
            "reason": "paper_fallback",
        }
        for order in active_orders
    ]
    return {
        "requested_mode": requested_mode,
        "applied_mode": "paper",
        "broker_live": False,
        "routed_orders": 0,
        "skipped_orders": len(active_orders),
        "warnings": warnings,
        "details": details,
    }

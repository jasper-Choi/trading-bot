from __future__ import annotations

from app.config import settings
from app.core.models import PaperOrder
from app.services.kis_broker import place_order as place_kis_order
from app.services.upbit_broker import place_order as place_upbit_order

SUPPORTED_EXECUTION_MODES = {"paper", "upbit_live", "kis_live"}


def normalize_execution_mode(mode: str) -> str:
    normalized = str(mode or "paper").strip().lower()
    return normalized if normalized in SUPPORTED_EXECUTION_MODES else "paper"


def _upbit_ready() -> bool:
    return bool(
        settings.upbit_allow_live
        and settings.upbit_access_key
        and settings.upbit_secret_key
        and settings.live_capital_krw > 0
    )


def _kis_ready() -> bool:
    return bool(
        settings.kis_allow_live
        and settings.kis_app_key
        and settings.kis_app_secret
        and settings.kis_account_no
        and settings.kis_product_code
        and settings.kis_capital_krw > 0
    )


def _tag_detail(detail: dict, *, applied_mode: str, broker_live: bool) -> dict:
    updated = dict(detail)
    updated["applied_mode"] = applied_mode
    updated["broker_live"] = broker_live
    return updated


def route_orders(orders: list[PaperOrder], requested_mode: str) -> dict:
    mode = normalize_execution_mode(requested_mode)
    active_orders = [order for order in orders if order.status == "planned"]
    warnings: list[str] = []

    if mode == "paper":
        return {
            "requested_mode": mode,
            "applied_mode": "paper",
            "broker_live": False,
            "routed_orders": 0,
            "skipped_orders": 0,
            "warnings": [],
            "live_desks": [],
            "details": [],
        }

    if mode == "upbit_live":
        return _route_mixed_upbit_mode(active_orders, requested_mode=mode, warnings=warnings)

    if mode == "kis_live":
        return _route_kis_mode(active_orders, requested_mode=mode, warnings=warnings)

    warnings.append(f"Unknown execution mode '{requested_mode}', paper fallback applied")
    return _paper_fallback(mode, active_orders, warnings)


def _route_mixed_upbit_mode(active_orders: list[PaperOrder], *, requested_mode: str, warnings: list[str]) -> dict:
    """Route crypto to Upbit and Korea stocks to KIS when each broker is ready.

    The production host often runs with EXECUTION_MODE=upbit_live because crypto is
    the primary live lane. Korea mock/live orders still need to reach KIS, so this
    path routes per desk instead of treating the whole cycle as one broker.
    """
    if settings.upbit_pilot_single_order_only:
        crypto_orders = [order for order in active_orders if order.desk == "crypto"]
        if len(crypto_orders) > 1:
            warnings.append("Upbit pilot guard allows only one live crypto order per cycle; extra orders were skipped")
            active_orders = crypto_orders[:1] + [order for order in active_orders if order.desk != "crypto"]

    details: list[dict] = []
    routed_orders = 0
    skipped_orders = 0
    live_modes: set[str] = set()
    live_desks: set[str] = set()
    unsupported_upbit_orders = 0
    unsupported_kis_orders = 0

    for order in active_orders:
        # Shadow period guard: new strategies stay in paper mode until 30 completed trades
        strategy_id = str(order.strategy_id or "")
        if strategy_id:
            try:
                from app.services.strategy_monitor import is_in_shadow_period
                if is_in_shadow_period(strategy_id):
                    details.append(_tag_detail(
                        {"symbol": getattr(order, "focus", "")[:50], "reason": f"shadow_period:{strategy_id}"},
                        applied_mode="paper", broker_live=False,
                    ))
                    skipped_orders += 1
                    continue
            except Exception:
                pass  # fail open — don't block order on monitor error

        if order.desk == "korea" and _kis_ready():
            result = place_kis_order(order)
            target_mode = "kis_live"
        else:
            result = place_upbit_order(order)
            target_mode = "upbit_live"

        is_live = bool(result.ok)
        details.append(_tag_detail(result.detail, applied_mode=target_mode if is_live else "paper", broker_live=is_live))
        if is_live:
            routed_orders += 1
            live_modes.add(target_mode)
            live_desks.add(order.desk)
        else:
            skipped_orders += 1
            reason = str(result.detail.get("reason", "") or "")
            if reason == "unsupported_desk_for_upbit":
                unsupported_upbit_orders += 1
            if reason == "unsupported_desk_for_kis":
                unsupported_kis_orders += 1

    if any(order.desk == "crypto" for order in active_orders) and not _upbit_ready():
        warnings.append("Upbit live is not ready for crypto orders; affected orders used paper fallback")
    if any(order.desk == "korea" for order in active_orders) and not _kis_ready():
        warnings.append("KIS mock/live is not ready for Korea orders; affected orders used paper fallback")
    if unsupported_upbit_orders:
        warnings.append(f"{unsupported_upbit_orders} order(s) target desks unsupported by Upbit and were not routed")
    if unsupported_kis_orders:
        warnings.append(f"{unsupported_kis_orders} order(s) target desks unsupported by KIS and were not routed")

    applied_mode = "paper"
    if len(live_modes) == 1:
        applied_mode = next(iter(live_modes))
    elif len(live_modes) > 1:
        applied_mode = "mixed_live"

    return {
        "requested_mode": requested_mode,
        "applied_mode": applied_mode,
        "broker_live": routed_orders > 0,
        "routed_orders": routed_orders,
        "skipped_orders": skipped_orders,
        "warnings": warnings,
        "live_desks": sorted(live_desks),
        "details": details,
    }


def _route_kis_mode(active_orders: list[PaperOrder], *, requested_mode: str, warnings: list[str]) -> dict:
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
        return _paper_fallback(requested_mode, active_orders, warnings)
    if settings.kis_capital_krw <= 0:
        warnings.append("KIS live requested but KIS_CAPITAL_KRW is not configured; paper fallback applied")
        return _paper_fallback(requested_mode, active_orders, warnings)
    if not settings.kis_allow_live:
        warnings.append("KIS credentials are present, but KIS_ALLOW_LIVE is false; paper fallback applied")
        return _paper_fallback(requested_mode, active_orders, warnings)

    details: list[dict] = []
    routed_orders = 0
    skipped_orders = 0
    live_desks: set[str] = set()
    unsupported_live_orders = 0
    for order in active_orders:
        result = place_kis_order(order)
        is_live = bool(result.ok)
        details.append(_tag_detail(result.detail, applied_mode="kis_live" if is_live else "paper", broker_live=is_live))
        if is_live:
            routed_orders += 1
            live_desks.add(order.desk)
        else:
            skipped_orders += 1
            if result.detail.get("reason") == "unsupported_desk_for_kis":
                unsupported_live_orders += 1
    if unsupported_live_orders:
        warnings.append(f"{unsupported_live_orders} order(s) target desks unsupported by KIS and were not routed")
    applied_mode = "kis_live" if routed_orders > 0 else "paper"
    return {
        "requested_mode": requested_mode,
        "applied_mode": applied_mode,
        "broker_live": routed_orders > 0,
        "routed_orders": routed_orders,
        "skipped_orders": skipped_orders,
        "warnings": warnings,
        "live_desks": sorted(live_desks),
        "details": details,
    }


def _paper_fallback(requested_mode: str, active_orders: list[PaperOrder], warnings: list[str]) -> dict:
    details = [
        {
            "desk": order.desk,
            "symbol": order.symbol,
            "action": order.action,
            "size": order.size,
            "reason": "paper_fallback",
            "applied_mode": "paper",
            "broker_live": False,
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
        "live_desks": [],
        "details": details,
    }

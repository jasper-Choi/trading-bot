from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import requests
from requests import RequestException

from app.config import settings
from app.core.models import PaperOrder


UPBIT_BASE_URL = "https://api.upbit.com"
UPBIT_CREATE_ORDER_URL = f"{UPBIT_BASE_URL}/v1/orders"
UPBIT_GET_ORDER_URL = f"{UPBIT_BASE_URL}/v1/order"
UPBIT_TICKER_URL = f"{UPBIT_BASE_URL}/v1/ticker"
REQUEST_TIMEOUT = 8


@dataclass(slots=True)
class UpbitOrderResult:
    ok: bool
    request_mode: str
    detail: dict[str, Any]


def place_order(order: PaperOrder) -> UpbitOrderResult:
    if order.desk != "crypto":
        return UpbitOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "unsupported_desk_for_upbit",
            },
        )

    if not settings.upbit_allow_live:
        return UpbitOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "upbit_live_not_enabled",
            },
        )

    payload = _build_order_payload(order)
    if payload is None:
        return UpbitOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "unsupported_order_shape",
            },
        )

    try:
        response = _request("POST", "/v1/orders", payload)
        return UpbitOrderResult(
            ok=True,
            request_mode="upbit_live",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "ord_type": payload.get("ord_type"),
                "side": payload.get("side"),
                "uuid": response.get("uuid", ""),
                "state": response.get("state", ""),
                "requested_volume": payload.get("volume", ""),
                "requested_price": payload.get("price", ""),
            },
        )
    except RequestException as exc:
        return UpbitOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "request_exception",
                "message": str(exc),
            },
        )
    except RuntimeError as exc:
        return UpbitOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "upbit_error",
                "message": str(exc),
            },
        )


def get_order(uuid: str) -> dict[str, Any]:
    return _request("GET", "/v1/order", {"uuid": uuid})


def normalize_order_state(payload: dict[str, Any]) -> dict[str, str]:
    state = str(payload.get("state", "") or "").lower()
    remaining_volume = _safe_float(payload.get("remaining_volume"))
    executed_volume = _safe_float(payload.get("executed_volume"))
    paid_fee = _safe_float(payload.get("paid_fee"))
    status = "submitted"
    if state in {"done", "cancel"}:
        status = "filled" if state == "done" else "cancelled"
    elif executed_volume > 0 and remaining_volume > 0:
        status = "partial"
    elif state in {"wait", "watch"}:
        status = "submitted"
    return {
        "request_status": status,
        "broker_state": state or "unknown",
        "executed_volume": _format_decimal(executed_volume) if executed_volume > 0 else "",
        "remaining_volume": _format_decimal(remaining_volume) if remaining_volume > 0 else "",
        "paid_fee": _format_decimal(paid_fee) if paid_fee > 0 else "",
    }


def get_balances() -> list[dict[str, Any]]:
    payload = _request("GET", "/v1/accounts")
    return payload if isinstance(payload, list) else []


def get_account_positions() -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for item in get_balances():
        currency = str(item.get("currency", "") or "").upper()
        unit_currency = str(item.get("unit_currency", "") or "").upper()
        if not currency or not unit_currency or currency == unit_currency:
            continue
        try:
            balance = float(item.get("balance") or 0.0)
            locked = float(item.get("locked") or 0.0)
            avg_buy_price = float(item.get("avg_buy_price") or 0.0)
        except (TypeError, ValueError):
            continue
        total_volume = balance + locked
        if total_volume <= 0:
            continue
        positions.append(
            {
                "market": f"{unit_currency}-{currency}",
                "currency": currency,
                "unit_currency": unit_currency,
                "balance": balance,
                "locked": locked,
                "total_volume": total_volume,
                "avg_buy_price": avg_buy_price,
            }
        )
    return positions


def get_ticker_prices(markets: list[str]) -> dict[str, float]:
    clean_markets = [str(item).strip() for item in markets if str(item).strip()]
    if not clean_markets:
        return {}
    response = requests.get(
        UPBIT_TICKER_URL,
        params={"markets": ",".join(clean_markets)},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    rows = response.json()
    return {
        str(item.get("market", "")).strip(): float(item.get("trade_price") or 0.0)
        for item in rows
        if str(item.get("market", "")).strip()
    }


def _build_order_payload(order: PaperOrder) -> dict[str, str] | None:
    if order.action in {"probe_longs", "attack_opening_drive", "selective_probe"}:
        if not order.symbol or order.reference_price <= 0 or order.notional_pct <= 0:
            return None
        live_budget_krw = round(settings.live_capital_krw * order.notional_pct, 2)
        if live_budget_krw <= 0:
            return None
        return {
            "market": order.symbol,
            "side": "bid",
            "ord_type": "price",
            "price": str(int(round(live_budget_krw))),
        }

    if order.action in {"reduce_risk", "capital_preservation"}:
        if not order.symbol:
            return None
        volume = _get_available_asset_volume(order.symbol)
        if volume <= 0:
            return None
        return {
            "market": order.symbol,
            "side": "ask",
            "ord_type": "market",
            "volume": _format_decimal(volume),
        }

    return None


def _get_available_asset_volume(market: str) -> float:
    _, base_currency = _split_market(market)
    if not base_currency:
        return 0.0
    try:
        balances = get_balances()
    except (RequestException, RuntimeError):
        return 0.0
    for item in balances:
        if str(item.get("currency", "")).upper() != base_currency.upper():
            continue
        try:
            balance = float(item.get("balance") or 0.0)
            locked = float(item.get("locked") or 0.0)
        except (TypeError, ValueError):
            return 0.0
        available = balance - locked
        return available if available > 0 else 0.0
    return 0.0


def _request(method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    token = _create_jwt(params)
    headers = {"Authorization": f"Bearer {token}"}
    if method.upper() == "POST":
        headers["Content-Type"] = "application/json"
        response = requests.post(f"{UPBIT_BASE_URL}{path}", headers=headers, json=params, timeout=REQUEST_TIMEOUT)
    else:
        response = requests.get(f"{UPBIT_BASE_URL}{path}", headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload


def _create_jwt(params: dict[str, Any] | None = None) -> str:
    params = params or {}
    header = {"alg": "HS512", "typ": "JWT"}
    payload: dict[str, Any] = {"access_key": settings.upbit_access_key, "nonce": str(uuid4())}
    query_string = _build_query_string(params)
    if query_string:
        payload["query_hash"] = hashlib.sha512(query_string.encode("utf-8")).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(settings.upbit_secret_key.encode("utf-8"), signing_input, hashlib.sha512).digest()
    encoded_signature = _b64url(signature)
    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def _build_query_string(params: dict[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, list):
            for item in value:
                pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))
    return urlencode(pairs, doseq=True)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _split_market(market: str) -> tuple[str, str]:
    parts = str(market or "").split("-", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _format_decimal(value: float) -> str:
    text = f"{value:.16f}".rstrip("0").rstrip(".")
    return text or "0"


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

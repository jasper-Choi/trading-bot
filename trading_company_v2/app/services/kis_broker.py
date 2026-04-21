from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any

import requests
from requests import RequestException

from app.config import settings
from app.core.models import PaperOrder


KIS_PROD_BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_TOKEN_PATH = "/oauth2/tokenP"
KIS_HASHKEY_PATH = "/uapi/hashkey"
KIS_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
KIS_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
KIS_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
REQUEST_TIMEOUT = 8
_TOKEN_CACHE: dict[str, Any] = {"access_token": "", "expires_at": None}


@dataclass(slots=True)
class KisOrderResult:
    ok: bool
    request_mode: str
    detail: dict[str, Any]


def place_order(order: PaperOrder) -> KisOrderResult:
    if order.desk != "korea":
        return KisOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "unsupported_desk_for_kis",
            },
        )

    payload = _build_order_payload(order)
    if payload is None:
        return KisOrderResult(
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
        response = _request(
            "POST",
            KIS_ORDER_CASH_PATH,
            json_body=payload,
            tr_id=_order_tr_id(order.action),
            include_hashkey=True,
        )
        output = _extract_output(response)
        broker_order_id = str(
            output.get("ODNO")
            or output.get("odno")
            or response.get("ODNO")
            or response.get("odno")
            or ""
        )
        return KisOrderResult(
            ok=True,
            request_mode="kis_live",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "side": "buy" if _is_buy_action(order.action) else "sell",
                "ord_dvsn": payload.get("ORD_DVSN", ""),
                "requested_qty": payload.get("ORD_QTY", ""),
                "requested_price": payload.get("ORD_UNPR", ""),
                "broker_order_id": broker_order_id,
                "ord_gno_brno": str(output.get("KRX_FWDG_ORD_ORGNO") or output.get("ORD_GNO_BRNO") or ""),
                "broker_state": "submitted",
                "rt_cd": str(response.get("rt_cd", "") or ""),
                "msg1": str(response.get("msg1", "") or ""),
            },
        )
    except RequestException as exc:
        return KisOrderResult(
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
        return KisOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "kis_error",
                "message": str(exc),
            },
        )


def get_account_positions() -> list[dict[str, Any]]:
    cano, product_code = _account_parts()
    response = _request(
        "GET",
        KIS_BALANCE_PATH,
        params={
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
        tr_id="TTTC8434R",
    )
    rows = _extract_rows(response, "output1")
    positions: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("pdno") or row.get("PDNO") or "").strip()
        if not symbol:
            continue
        total_qty = _safe_float(
            row.get("hldg_qty")
            or row.get("hold_qty")
            or row.get("ord_psbl_qty")
            or row.get("ord_psbl_qty1")
        )
        if total_qty <= 0:
            continue
        avg_buy_price = _safe_float(
            row.get("pchs_avg_pric")
            or row.get("pchs_avg_price")
            or row.get("avg_prvs")
            or row.get("purchase_avg_price")
        )
        current_price = _safe_float(row.get("prpr") or row.get("now_pric") or row.get("stck_prpr"))
        positions.append(
            {
                "market": symbol,
                "symbol": symbol,
                "currency": "KRW",
                "unit_currency": "KRW",
                "balance": total_qty,
                "locked": 0.0,
                "total_volume": total_qty,
                "avg_buy_price": avg_buy_price or current_price,
            }
        )
    return positions


def get_order(odno: str, symbol: str = "", side_hint: str = "") -> dict[str, Any]:
    cano, product_code = _account_parts()
    today = datetime.now().strftime("%Y%m%d")
    side_code = {"buy": "02", "sell": "01"}.get(str(side_hint or "").strip().lower(), "00")
    response = _request(
        "GET",
        KIS_DAILY_CCLD_PATH,
        params={
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": side_code,
            "PDNO": str(symbol or "").strip(),
            "CCLD_DVSN": "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": str(odno or "").strip(),
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        },
        tr_id="TTTC0081R",
    )
    rows = _extract_rows(response, "output1")
    target = str(odno or "").strip()
    for row in rows:
        row_odno = str(row.get("odno") or row.get("ODNO") or "").strip()
        if row_odno == target:
            return row
    return rows[0] if rows else {}


def normalize_order_state(payload: dict[str, Any]) -> dict[str, str]:
    order_qty = _safe_float(
        payload.get("ord_qty")
        or payload.get("ORD_QTY")
        or payload.get("order_qty")
        or payload.get("tot_ord_qty")
        or payload.get("TOT_ORD_QTY")
    )
    filled_qty = _safe_float(
        payload.get("tot_ccld_qty")
        or payload.get("TOT_CCLD_QTY")
        or payload.get("ccld_qty")
        or payload.get("CCLD_QTY")
        or payload.get("filled_qty")
        or payload.get("exec_qty")
        or payload.get("EXEC_QTY")
    )
    remaining_qty = _safe_float(
        payload.get("rmn_qty")
        or payload.get("RMN_QTY")
        or payload.get("nccs_qty")
        or payload.get("NCCS_QTY")
        or payload.get("ord_psbl_qty")
        or payload.get("ORD_PSBL_QTY")
        or max(order_qty - filled_qty, 0.0)
    )
    raw_status = str(
        payload.get("prcs_stat_name")
        or payload.get("sll_buy_dvsn_cd_name")
        or payload.get("CCLD_DVSN_NAME")
        or payload.get("ccld_dvsn_name")
        or payload.get("order_status")
        or payload.get("ord_dt")
        or payload.get("ORD_DT")
        or ""
    ).strip()
    status = "submitted"
    if filled_qty > 0 and remaining_qty > 0:
        status = "partial"
    elif order_qty > 0 and filled_qty >= order_qty:
        status = "filled"
    elif order_qty > 0 and remaining_qty >= order_qty and _is_cancelled_payload(payload):
        status = "cancelled"
    elif _is_cancelled_payload(payload):
        status = "cancelled"
    avg_price = _safe_float(
        payload.get("avg_prvs")
        or payload.get("AVG_PRVS")
        or payload.get("avg_ccld_unpr")
        or payload.get("AVG_CCLD_UNPR")
        or payload.get("tot_ccld_unpr")
        or payload.get("TOT_CCLD_UNPR")
    )
    if avg_price <= 0 and filled_qty > 0:
        total_filled_amount = _safe_float(
            payload.get("tot_ccld_amt")
            or payload.get("TOT_CCLD_AMT")
            or payload.get("ccld_amt")
            or payload.get("CCLD_AMT")
            or payload.get("tot_ccld_amt1")
        )
        if total_filled_amount > 0:
            avg_price = total_filled_amount / filled_qty
    return {
        "request_status": status,
        "broker_state": raw_status or status,
        "executed_volume": _format_decimal(filled_qty) if filled_qty > 0 else "",
        "remaining_volume": _format_decimal(remaining_qty) if remaining_qty > 0 else "",
        "avg_fill_price": _format_decimal(avg_price) if avg_price > 0 else "",
    }


def _build_order_payload(order: PaperOrder) -> dict[str, str] | None:
    symbol = str(order.symbol or "").strip()
    if not symbol:
        return None
    cano, product_code = _account_parts()
    if _is_buy_action(order.action):
        if order.reference_price <= 0 or order.notional_pct <= 0:
            return None
        live_budget_krw = float(settings.live_capital_krw or 0) * float(order.notional_pct or 0.0)
        qty = math.floor(live_budget_krw / float(order.reference_price or 0.0)) if order.reference_price > 0 else 0
        if qty <= 0:
            return None
        return {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "PDNO": symbol,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
    if _is_sell_action(order.action):
        qty = _get_available_stock_quantity(symbol)
        if qty <= 0:
            return None
        return {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "PDNO": symbol,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
    return None


def _get_available_stock_quantity(symbol: str) -> int:
    try:
        positions = get_account_positions()
    except (RequestException, RuntimeError):
        return 0
    for item in positions:
        if str(item.get("market", "")).strip() != symbol:
            continue
        return int(max(_safe_float(item.get("total_volume")), 0.0))
    return 0


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    tr_id: str = "",
    include_hashkey: bool = False,
) -> dict[str, Any]:
    token = _get_access_token()
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.kis_app_key,
        "appsecret": settings.kis_app_secret,
        "custtype": "P",
    }
    if tr_id:
        headers["tr_id"] = tr_id
    body = json_body or {}
    if include_hashkey:
        headers["hashkey"] = _issue_hashkey(body)
    url = f"{KIS_PROD_BASE_URL}{path}"
    if method.upper() == "POST":
        response = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    else:
        response = requests.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and str(payload.get("rt_cd", "") or "") not in {"", "0"}:
        raise RuntimeError(str(payload.get("msg1") or payload.get("msg_cd") or payload))
    return payload if isinstance(payload, dict) else {}


def _account_parts() -> tuple[str, str]:
    raw_account = str(settings.kis_account_no or "").strip()
    raw_product = str(settings.kis_product_code or "").strip()
    digits = "".join(ch for ch in raw_account if ch.isdigit())
    if len(digits) >= 10 and not raw_product:
        return digits[:8], digits[8:10]
    return digits[:8], raw_product


def _get_access_token() -> str:
    now = datetime.now(timezone.utc)
    cached_token = str(_TOKEN_CACHE.get("access_token") or "")
    expires_at = _TOKEN_CACHE.get("expires_at")
    if cached_token and isinstance(expires_at, datetime) and now < expires_at:
        return cached_token

    response = requests.post(
        f"{KIS_PROD_BASE_URL}{KIS_TOKEN_PATH}",
        headers={"content-type": "application/json; charset=utf-8"},
        json={
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise RuntimeError(str(payload.get("msg1") or "KIS access token missing in response"))
    expires_in = int(payload.get("expires_in") or 3600)
    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = now + timedelta(seconds=max(expires_in - 60, 60))
    return access_token


def _issue_hashkey(payload: dict[str, Any]) -> str:
    response = requests.post(
        f"{KIS_PROD_BASE_URL}{KIS_HASHKEY_PATH}",
        headers={
            "content-type": "application/json; charset=utf-8",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    hashkey = str(body.get("HASH") or body.get("hash") or "")
    if not hashkey:
        raise RuntimeError(str(body.get("msg1") or "KIS hashkey missing in response"))
    return hashkey


def _order_tr_id(action: str) -> str:
    return "TTTC0802U" if _is_buy_action(action) else "TTTC0801U"


def _is_buy_action(action: str) -> bool:
    return action in {"probe_longs", "attack_opening_drive", "selective_probe"}


def _is_sell_action(action: str) -> bool:
    return action in {"reduce_risk", "capital_preservation"}


def _extract_output(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output")
    return output if isinstance(output, dict) else {}


def _extract_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key)
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]
    return []


def _is_cancelled_payload(payload: dict[str, Any]) -> bool:
    text = " ".join(
        str(payload.get(key) or "")
        for key in (
            "rvse_cncl_dvsn_name",
            "RVSE_CNCL_DVSN_NAME",
            "ord_dvsn_name",
            "ORD_DVSN_NAME",
            "prcs_stat_name",
            "PRCS_STAT_NAME",
            "ccld_dvsn_name",
            "CCLD_DVSN_NAME",
        )
    ).lower()
    return "취소" in text or "cancel" in text


def _format_decimal(value: float) -> str:
    text = f"{value:.16f}".rstrip("0").rstrip(".")
    return text or "0"


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

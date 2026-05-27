from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from typing import Any

import requests
from requests import RequestException

from app.config import DATA_DIR, settings
from app.core.models import PaperOrder


KIS_PROD_BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"
KIS_TOKEN_PATH = "/oauth2/tokenP"
KIS_HASHKEY_PATH = "/uapi/hashkey"
KIS_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
KIS_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
KIS_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
KIS_MINUTE_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
# 해외주식 (미국)
KIS_US_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
KIS_US_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"
KIS_US_CCLD_PATH = "/uapi/overseas-stock/v1/trading/inquire-ccnl"

# 미국 주식 거래소 코드 매핑 (KIS 기준)
# 매핑에 없으면 NASD(나스닥) 기본값 사용
_US_EXCHANGE_MAP: dict[str, str] = {
    # NYSE / NYSE Arca ETF
    "SPY": "AMEX", "IVV": "AMEX", "VOO": "AMEX", "VTI": "AMEX",
    "GLD": "AMEX", "SLV": "AMEX", "DIA": "AMEX", "IWM": "AMEX",
    "EEM": "AMEX", "EFA": "AMEX", "HYG": "AMEX", "LQD": "AMEX",
    # NYSE 개별주
    "JPM": "NYSE", "BAC": "NYSE", "V": "NYSE", "MA": "NYSE",
    "JNJ": "NYSE", "WMT": "NYSE", "PG": "NYSE", "HD": "NYSE",
    "XOM": "NYSE", "CVX": "NYSE", "KO": "NYSE", "PFE": "NYSE",
    "MRK": "NYSE", "DIS": "NYSE", "MCD": "NYSE", "IBM": "NYSE",
    "GS": "NYSE", "UNH": "NYSE", "BRK-B": "NYSE", "AXP": "NYSE",
    "CAT": "NYSE", "BA": "NYSE", "MMM": "NYSE", "GE": "NYSE",
    "C": "NYSE", "WFC": "NYSE", "MS": "NYSE", "BLK": "NYSE",
}
REQUEST_TIMEOUT = 8
_TOKEN_CACHE: dict[str, Any] = {"access_token": "", "expires_at": None}
_TOKEN_CACHE_FILE = DATA_DIR / "kis_access_token_cache.json"


@dataclass(slots=True)
class KisOrderResult:
    ok: bool
    request_mode: str
    detail: dict[str, Any]


def place_order(order: PaperOrder) -> KisOrderResult:
    if order.desk == "us":
        return _place_us_order(order)
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
        debug = _order_shape_debug(order)
        return KisOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "unsupported_order_shape",
                "message": debug.get("message", ""),
                **debug,
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
                "message": _request_exception_message(exc),
                "reference_price": order.reference_price,
                "notional_pct": order.notional_pct,
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
        tr_id="VTTC8434R" if settings.kis_mock else "TTTC8434R",
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
        tr_id="VTTC0081R" if settings.kis_mock else "TTTC0081R",
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


# ── 해외주식 (미국) ──────────────────────────────────────────────────────────

def _us_exchange_code(symbol: str) -> str:
    """티커 → KIS 거래소 코드 (기본값 NASD)."""
    return _US_EXCHANGE_MAP.get(symbol.upper().strip(), "NASD")


def _us_order_tr_id(action: str) -> str:
    """해외주식 주문 TR ID.

    KIS 모의투자(VTS)는 해외주식 미지원 → 호출 전에 막아야 함.
    실전: 매수 TTTS0002U / 매도 TTTS0001U (미국 주간)
    """
    return "TTTS0002U" if _is_buy_action(action) else "TTTS0001U"


def _build_us_order_payload(order: PaperOrder) -> dict[str, str] | None:
    """KIS 해외주식 주문 payload 생성 (USD 기준)."""
    symbol = str(order.symbol or "").strip().upper()
    if not symbol:
        return None
    cano, product_code = _account_parts()

    if _is_buy_action(order.action):
        reference_price = _order_reference_price(order)   # USD 가격
        notional_pct = _order_notional_pct(order)
        capital_usd = float(settings.kis_us_capital_usd or 0)
        if reference_price <= 0 or notional_pct <= 0 or capital_usd <= 0:
            return None
        budget_usd = capital_usd * notional_pct
        qty = math.floor(budget_usd / reference_price)
        if qty <= 0 and budget_usd >= reference_price * 0.70:
            qty = 1   # 소수점 불가 → 예산이 1주 가격의 70% 이상이면 1주
        if qty <= 0:
            return None
        return {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "OVRS_EXCG_CD": _us_exchange_code(symbol),
            "PDNO": symbol,
            "ORD_DVSN": "00",               # 지정가
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": f"{reference_price:.2f}",
            "ORD_SVR_DVSN_CD": "0",
        }

    if _is_sell_action(order.action):
        qty = _get_available_us_stock_quantity(symbol)
        if qty <= 0:
            return None
        reference_price = _order_reference_price(order)
        if reference_price <= 0:
            return None
        return {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "OVRS_EXCG_CD": _us_exchange_code(symbol),
            "PDNO": symbol,
            "ORD_DVSN": "00",
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": f"{reference_price:.2f}",
            "SLL_TYPE": "00",
            "ORD_SVR_DVSN_CD": "0",
        }
    return None


def _place_us_order(order: PaperOrder) -> KisOrderResult:
    """해외주식(미국) 주문 실행."""
    # KIS 모의투자는 해외주식 미지원 → paper fallback
    if settings.kis_mock:
        return KisOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "kis_mock_unsupported_for_us",
                "message": "KIS VTS does not support overseas stock orders; paper fallback",
            },
        )

    if float(settings.kis_us_capital_usd or 0) <= 0:
        return KisOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "kis_us_capital_not_configured",
                "message": "KIS_US_CAPITAL_USD is 0; set it in .env to enable US live trading",
            },
        )

    payload = _build_us_order_payload(order)
    if payload is None:
        return KisOrderResult(
            ok=False,
            request_mode="paper_fallback",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "reason": "unsupported_order_shape",
                "message": "Could not build US order payload (missing price/qty/capital)",
            },
        )

    try:
        response = _request(
            "POST",
            KIS_US_ORDER_PATH,
            json_body=payload,
            tr_id=_us_order_tr_id(order.action),
            include_hashkey=True,
        )
        output = _extract_output(response)
        broker_order_id = str(
            output.get("ODNO") or output.get("odno")
            or response.get("ODNO") or response.get("odno") or ""
        )
        return KisOrderResult(
            ok=True,
            request_mode="kis_live",
            detail={
                "desk": order.desk,
                "symbol": order.symbol,
                "action": order.action,
                "side": "buy" if _is_buy_action(order.action) else "sell",
                "exchange": payload.get("OVRS_EXCG_CD", ""),
                "ord_dvsn": payload.get("ORD_DVSN", ""),
                "requested_qty": payload.get("ORD_QTY", ""),
                "requested_price_usd": payload.get("OVRS_ORD_UNPR", ""),
                "broker_order_id": broker_order_id,
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
                "message": _request_exception_message(exc),
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


def get_us_account_positions() -> list[dict[str, Any]]:
    """KIS 해외주식 보유 잔고 조회."""
    cano, product_code = _account_parts()
    try:
        response = _request(
            "GET",
            KIS_US_BALANCE_PATH,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": product_code,
                "OVRS_EXCG_CD": "NASD",   # 전체 조회용 (KIS는 거래소별 조회)
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
            tr_id="TTTS3012R",
        )
    except Exception:
        return []
    rows = _extract_rows(response, "output1")
    positions: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("ovrs_pdno") or row.get("pdno") or "").strip().upper()
        if not symbol:
            continue
        qty = _safe_float(row.get("ovrs_cblc_qty") or row.get("hldg_qty") or row.get("ord_psbl_qty"))
        if qty <= 0:
            continue
        avg_buy_price = _safe_float(row.get("pchs_avg_pric") or row.get("avg_unpr3"))
        current_price = _safe_float(row.get("now_pric2") or row.get("prpr"))
        positions.append({
            "market": symbol,
            "symbol": symbol,
            "currency": "USD",
            "unit_currency": "USD",
            "balance": qty,
            "locked": 0.0,
            "total_volume": qty,
            "avg_buy_price": avg_buy_price or current_price,
        })
    return positions


def _get_available_us_stock_quantity(symbol: str) -> int:
    """해외주식 매도 가능 수량 조회."""
    try:
        positions = get_us_account_positions()
    except Exception:
        return 0
    for item in positions:
        if str(item.get("symbol", "")).strip().upper() == symbol.upper():
            return int(max(_safe_float(item.get("total_volume")), 0.0))
    return 0


# ── 국내주식 ──────────────────────────────────────────────────────────────────

def _build_order_payload(order: PaperOrder) -> dict[str, str] | None:
    symbol = str(order.symbol or "").strip()
    if not symbol:
        return None
    cano, product_code = _account_parts()
    if _is_buy_action(order.action):
        reference_price = _order_reference_price(order)
        notional_pct = _order_notional_pct(order)
        if reference_price <= 0 or notional_pct <= 0:
            return None
        live_budget_krw = float(settings.kis_capital_krw or 0) * float(notional_pct or 0.0)
        qty = math.floor(live_budget_krw / float(reference_price or 0.0)) if reference_price > 0 else 0
        if qty <= 0 and live_budget_krw >= reference_price * 0.70:
            # Domestic stocks have a one-share minimum. If the sizing budget is
            # close to one share, round up so KIS can actually receive the order.
            qty = 1
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


def _order_reference_price(order: PaperOrder) -> float:
    try:
        price = float(order.reference_price or 0.0)
    except (TypeError, ValueError):
        price = 0.0
    if price > 0:
        return price
    if order.rationale and isinstance(order.rationale[0], dict):
        try:
            return float(order.rationale[0].get("reference_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _order_notional_pct(order: PaperOrder) -> float:
    try:
        notional = float(order.notional_pct or 0.0)
    except (TypeError, ValueError):
        notional = 0.0
    if notional > 0:
        return notional
    size = str(order.size or "").strip().lower().replace("x", "")
    try:
        notional = float(size)
    except (TypeError, ValueError):
        notional = 0.0
    if notional > 0:
        return notional
    if order.rationale and isinstance(order.rationale[0], dict):
        try:
            return float(order.rationale[0].get("notional_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _order_shape_debug(order: PaperOrder) -> dict[str, Any]:
    reference_price = _order_reference_price(order)
    notional_pct = _order_notional_pct(order)
    budget = float(settings.kis_capital_krw or 0) * float(notional_pct or 0.0)
    qty = math.floor(budget / reference_price) if reference_price > 0 else 0
    if not str(order.symbol or "").strip():
        message = "missing symbol"
    elif reference_price <= 0:
        message = "missing reference price"
    elif notional_pct <= 0:
        message = "missing notional percentage"
    elif qty <= 0:
        message = f"budget {budget:.0f} KRW is below one-share price {reference_price:.0f} KRW"
    else:
        message = "unsupported action or unavailable sell quantity"
    return {
        "message": message,
        "reference_price": reference_price,
        "notional_pct": notional_pct,
        "kis_capital_krw": settings.kis_capital_krw,
        "estimated_budget_krw": round(budget, 2),
        "estimated_qty": qty,
    }


def _request_exception_message(exc: RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        text = str(getattr(response, "text", "") or "")[:500]
        if text:
            return f"{exc}; body={text}"
    return str(exc)


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
    url = f"{_kis_base_url()}{path}"
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

    disk_token, disk_expires_at = _read_token_cache(now)
    if disk_token and disk_expires_at and now < disk_expires_at:
        _TOKEN_CACHE["access_token"] = disk_token
        _TOKEN_CACHE["expires_at"] = disk_expires_at
        return disk_token

    try:
        response = requests.post(
            f"{_kis_base_url()}{KIS_TOKEN_PATH}",
            headers={"content-type": "application/json; charset=utf-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except RequestException:
        # KIS VTS can reject repeated token issuance across loop/dashboard
        # processes. If a still-valid shared token exists, prefer it.
        disk_token, disk_expires_at = _read_token_cache(now, allow_grace=True)
        if disk_token and disk_expires_at and now < disk_expires_at:
            _TOKEN_CACHE["access_token"] = disk_token
            _TOKEN_CACHE["expires_at"] = disk_expires_at
            return disk_token
        raise
    payload = response.json()
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise RuntimeError(str(payload.get("msg1") or "KIS access token missing in response"))
    expires_in = int(payload.get("expires_in") or 3600)
    expires_at = now + timedelta(seconds=max(expires_in - 60, 60))
    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = expires_at
    _write_token_cache(access_token, expires_at)
    return access_token


def _read_token_cache(now: datetime, *, allow_grace: bool = False) -> tuple[str, datetime | None]:
    try:
        raw = json.loads(_TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "", None
    token = str(raw.get("access_token") or "")
    expires_text = str(raw.get("expires_at") or "")
    try:
        expires_at = datetime.fromisoformat(expires_text)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_at = expires_at.astimezone(timezone.utc)
    except ValueError:
        return "", None
    if allow_grace and token and expires_at <= now:
        # Do not use expired tokens, but keep this branch explicit for future
        # broker-specific grace behavior.
        return "", None
    return token, expires_at


def _write_token_cache(access_token: str, expires_at: datetime) -> None:
    try:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(str(_TOKEN_CACHE_FILE) + ".tmp")
        temp_path.write_text(
            json.dumps(
                {
                    "access_token": access_token,
                    "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
                    "mock": bool(settings.kis_mock),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp_path.replace(_TOKEN_CACHE_FILE)
    except OSError:
        return


def _issue_hashkey(payload: dict[str, Any]) -> str:
    response = requests.post(
        f"{_kis_base_url()}{KIS_HASHKEY_PATH}",
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


def _kis_base_url() -> str:
    """모의투자(KIS_MOCK=true)면 VTS 서버, 실전이면 PROD 서버 반환."""
    return KIS_MOCK_BASE_URL if settings.kis_mock else KIS_PROD_BASE_URL


def _order_tr_id(action: str) -> str:
    # 모의투자: VTTC0802U(매수) / VTTC0801U(매도)
    # 실전투자: TTTC0802U(매수) / TTTC0801U(매도)
    if settings.kis_mock:
        return "VTTC0802U" if _is_buy_action(action) else "VTTC0801U"
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


def get_minute_candles(ticker: str, count: int = 30) -> list[dict[str, Any]]:
    """KIS API 주식 분봉 조회 (FHKST03010200).

    KIS는 최근 30거래일치 분봉을 제공. 실시간 S16 패닉셀 감지에 사용.

    Args:
        ticker : 종목코드 (예: "005930")
        count  : 반환할 최대 봉 수 (기본 30)

    Returns:
        [{
            "time":   "HHMMSS",
            "date":   "YYYYMMDD",
            "open":   float,
            "high":   float,
            "low":    float,
            "close":  float,
            "volume": float,
        }, ...]  — 오래된 것부터 정렬
    """
    try:
        now_kst = datetime.now(tz=__import__("zoneinfo").ZoneInfo("Asia/Seoul"))
        time_str = now_kst.strftime("%H%M%S")
        response = _request(
            "GET",
            KIS_MINUTE_CHART_PATH,
            params={
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_HOUR_1": time_str,
                "FID_PW_DATA_INCU_YN": "Y",
            },
            tr_id="FHKST03010200",
        )
        rows = response.get("output2") or []
        candles: list[dict[str, Any]] = []
        for row in rows:
            try:
                candles.append({
                    "time":   str(row.get("stck_cntg_hour") or ""),
                    "date":   str(row.get("stck_bsop_date") or ""),
                    "open":   _safe_float(row.get("stck_oprc")),
                    "high":   _safe_float(row.get("stck_hgpr")),
                    "low":    _safe_float(row.get("stck_lwpr")),
                    "close":  _safe_float(row.get("stck_prpr")),
                    "volume": _safe_float(row.get("acml_vol")),
                })
            except Exception:
                continue
        # KIS 응답은 최신→과거 순 — 뒤집어서 오래된→최신 순으로 반환
        candles.reverse()
        return candles[-count:]
    except Exception:
        return []

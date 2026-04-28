from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agents.base import BaseAgent
from app.config import settings
from app.core.models import AgentResult
from app.services.atr_sizing import summarize_atr_sizing
from app.services.backtest_advisor import get_crypto_weights
from app.services.market_gateway import (
    get_krw_crypto_candidates,
    get_upbit_15m_candles,
    get_upbit_1m_candles,
    get_upbit_orderbook,
)
from app.services.signal_engine import (
    summarize_crypto_micro_momentum_signal,
    summarize_crypto_signal,
    summarize_orderbook_pressure,
)

_FETCH_WORKERS = 8
_MAX_CYCLE_MARKETS = 18
_KST = ZoneInfo("Asia/Seoul")


def _pct_returns(candles: list[dict], limit: int = 32) -> list[float]:
    closes = [float(item.get("close") or 0.0) for item in candles if float(item.get("close") or 0.0) > 0]
    if len(closes) < 3:
        return []
    values = closes[-(limit + 1):]
    return [((values[idx] - values[idx - 1]) / values[idx - 1]) for idx in range(1, len(values)) if values[idx - 1] > 0]


def _pearson_corr(left: list[float], right: list[float]) -> float:
    n = min(len(left), len(right))
    if n < 8:
        return 1.0
    a = left[-n:]
    b = right[-n:]
    avg_a = sum(a) / n
    avg_b = sum(b) / n
    var_a = sum((value - avg_a) ** 2 for value in a)
    var_b = sum((value - avg_b) ** 2 for value in b)
    if var_a <= 0 or var_b <= 0:
        return 1.0
    cov = sum((a[idx] - avg_a) * (b[idx] - avg_b) for idx in range(n))
    return round(max(min(cov / ((var_a * var_b) ** 0.5), 1.0), -1.0), 3)


def _latest_candle_age_minutes(candles: list[dict]) -> float:
    if not candles:
        return 999.0
    raw = str(candles[-1].get("date") or "").strip()
    if not raw:
        return 999.0
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_KST)
        return round(max((datetime.now(_KST) - parsed.astimezone(_KST)).total_seconds() / 60, 0.0), 2)
    except ValueError:
        return 999.0


def _freshness_factor(age_minutes: float) -> tuple[float, str]:
    stale_limit = max(float(settings.crypto_signal_stale_minutes or 6.0), 1.0)
    if age_minutes <= 2.0:
        return 1.0, f"fresh 1m signal ({age_minutes:.1f}m)"
    if age_minutes <= stale_limit:
        factor = max(0.82, 1.0 - ((age_minutes - 2.0) / stale_limit) * 0.18)
        return round(factor, 3), f"aging 1m signal ({age_minutes:.1f}m)"
    return 0.68, f"stale 1m signal ({age_minutes:.1f}m)"


class CryptoDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("crypto_desk_agent")

    def run(self) -> AgentResult:
        weights = get_crypto_weights()
        direction_symbol = "KRW-BTC"
        direction_candles = get_upbit_15m_candles(direction_symbol, count=40)
        direction_returns = _pct_returns(direction_candles)
        direction_signal = summarize_crypto_signal(direction_candles)

        discovery_candidates = get_krw_crypto_candidates(limit=_MAX_CYCLE_MARKETS)
        market_weights: dict[str, float] = {}
        discovery_map: dict[str, dict] = {}
        for item in discovery_candidates:
            market = str(item.get("market", "")).strip()
            if not market:
                continue
            discovery_map[market] = item
            market_weights[market] = max(float(item.get("discovery_score", 0.0) or 0.0) * 0.35, 0.03)
        for market, weight in weights.items():
            market_weights[market] = max(float(weight or 0.0), market_weights.get(market, 0.0))

        all_markets = sorted(
            market_weights.items(),
            key=lambda item: (
                item[1],
                float((discovery_map.get(item[0], {}) or {}).get("volume_24h_krw", 0.0) or 0.0),
            ),
            reverse=True,
        )[:_MAX_CYCLE_MARKETS]

        def _fetch_market(market_weight: tuple[str, float]) -> tuple[str, float, dict, dict, dict, dict]:
            market, weight = market_weight
            candles_15m = get_upbit_15m_candles(market, count=40)
            candles_1m = get_upbit_1m_candles(market, count=80)
            signal = summarize_crypto_signal(candles_15m)
            micro = summarize_crypto_micro_momentum_signal(candles_1m)
            orderbook = summarize_orderbook_pressure(get_upbit_orderbook(market))
            atr_sizing = summarize_atr_sizing(candles_15m)
            corr = 1.0 if market == direction_symbol else _pearson_corr(_pct_returns(candles_15m), direction_returns)
            age_minutes = _latest_candle_age_minutes(candles_1m)
            freshness_factor, freshness_reason = _freshness_factor(age_minutes)
            signal.update(
                {
                    "btc_corr_15m": corr,
                    "signal_age_minutes": age_minutes,
                    "signal_freshness": freshness_factor,
                    "freshness_reason": freshness_reason,
                }
            )
            return market, weight, signal, micro, orderbook, atr_sizing

        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
            fetch_results = list(executor.map(_fetch_market, all_markets))

        direction_score = float(direction_signal.get("score", 0.5) or 0.5)
        ranked_candidates: list[dict] = []
        for market, weight, signal, micro, orderbook, atr_sizing in fetch_results:
            combined_score = round(
                # Orderbook weight raised: it's the most real-time signal we have
                (float(signal.get("score", 0.5) or 0.5) * 0.38)
                + (float(micro.get("micro_score", 0.0) or 0.0) * 0.26)
                + (float(orderbook.get("orderbook_score", 0.0) or 0.0) * 0.18)
                + (direction_score * 0.12)
                + (float(weight or 0.0) * 0.06),
                3,
            )
            freshness = float(signal.get("signal_freshness", 1.0) or 1.0)
            if freshness < 1.0:
                combined_score = round(combined_score * freshness, 3)
            if bool(micro.get("micro_ready", False)) and bool(orderbook.get("orderbook_ready", False)) and bool(signal.get("rsi_quality_ok", True)):
                combined_score = min(1.0, round(combined_score + 0.06, 3))
            pullback_s = float(signal.get("pullback_score", 0.0) or 0.0)
            if bool(signal.get("pullback_detected", False)) and pullback_s >= 0.60:
                combined_score = min(1.0, round(combined_score + pullback_s * 0.08, 3))
            ranked_candidates.append(
                {
                    "market": market,
                    "weight": round(float(weight or 0.0), 4),
                    "discovery_score": float((discovery_map.get(market, {}) or {}).get("discovery_score", 0.0) or 0.0),
                    "change_rate": float((discovery_map.get(market, {}) or {}).get("change_rate", 0.0) or 0.0),
                    "volume_24h_krw": int(float((discovery_map.get(market, {}) or {}).get("volume_24h_krw", 0.0) or 0.0)),
                    "signal_score": float(signal.get("score", 0.5) or 0.5),
                    "micro_score": float(micro.get("micro_score", 0.0) or 0.0),
                    "micro_ready": bool(micro.get("micro_ready", False)),
                    "micro_bias": str(micro.get("micro_bias", "neutral") or "neutral"),
                    "micro_reasons": list(micro.get("micro_reasons", [])),
                    "micro_vol_ratio": float(micro.get("micro_vol_ratio", 0.0) or 0.0),
                    "micro_move_3_pct": float(micro.get("micro_move_3_pct", 0.0) or 0.0),
                    "micro_vwap_gap_pct": float(micro.get("micro_vwap_gap_pct", 0.0) or 0.0),
                    "orderbook_score": float(orderbook.get("orderbook_score", 0.0) or 0.0),
                    "orderbook_ready": bool(orderbook.get("orderbook_ready", False)),
                    "orderbook_bid_ask_ratio": float(orderbook.get("orderbook_bid_ask_ratio", 0.0) or 0.0),
                    "orderbook_spread_pct": float(orderbook.get("orderbook_spread_pct", 0.0) or 0.0),
                    "orderbook_imbalance": float(orderbook.get("orderbook_imbalance", 0.0) or 0.0),
                    "orderbook_reasons": list(orderbook.get("orderbook_reasons", [])),
                    "atr_pct": float(atr_sizing.get("atr_pct", 0.0) or 0.0),
                    "atr_size_multiplier": float(atr_sizing.get("atr_size_multiplier", 1.0) or 1.0),
                    "volatility_tier": str(atr_sizing.get("volatility_tier", "unknown") or "unknown"),
                    "atr_sizing_reason": str(atr_sizing.get("atr_sizing_reason", "") or ""),
                    "btc_corr_15m": float(signal.get("btc_corr_15m", 1.0) or 1.0),
                    "signal_age_minutes": float(signal.get("signal_age_minutes", 999.0) or 999.0),
                    "signal_freshness": float(signal.get("signal_freshness", 1.0) or 1.0),
                    "freshness_reason": str(signal.get("freshness_reason", "") or ""),
                    "combined_score": combined_score,
                    "bias": str(signal.get("bias", "balanced") or "balanced"),
                    "recent_change_pct": float(signal.get("recent_change_pct", 0.0) or 0.0),
                    "burst_change_pct": float(signal.get("burst_change_pct", 0.0) or 0.0),
                    "ema_gap_pct": float(signal.get("ema_gap_pct", 0.0) or 0.0),
                    "pullback_gap_pct": float(signal.get("pullback_gap_pct", 0.0) or 0.0),
                    "range_4_pct": float(signal.get("range_4_pct", 0.0) or 0.0),
                    "rsi": signal.get("rsi"),
                    "reasons": list(signal.get("reasons", [])),
                    "breakout_confirmed": bool(signal.get("breakout_confirmed", False)),
                    "breakout_partial": bool(signal.get("breakout_partial", False)),
                    "breakout_count": int(signal.get("breakout_count", 0) or 0),
                    "vol_ratio": float(signal.get("vol_ratio", 0.0) or 0.0),
                    "breakout_score": float(signal.get("breakout_score", 0.0) or 0.0),
                    "rsi_quality_ok": bool(signal.get("rsi_quality_ok", True)),
                    "rsi_reset_confirmed": bool(signal.get("rsi_reset_confirmed", False)),
                    "rsi_bearish_divergence": bool(signal.get("rsi_bearish_divergence", False)),
                    "rsi_extreme": bool(signal.get("rsi_extreme", False)),
                    "ict_score": float(signal.get("ict_score", 0.0) or 0.0),
                    "kill_zone_active": bool(signal.get("kill_zone_active", False)),
                    "kill_zone_name": signal.get("kill_zone_name"),
                    "ssl_sweep_confirmed": bool(signal.get("ssl_sweep_confirmed", False)),
                    "choch_bullish": bool(signal.get("choch_bullish", False)),
                    "choch_bearish": bool(signal.get("choch_bearish", False)),
                    "bos_bullish": bool(signal.get("bos_bullish", False)),
                    "bos_bearish": bool(signal.get("bos_bearish", False)),
                    "price_at_bull_ob": bool(signal.get("price_at_bull_ob", False)),
                    "price_in_bull_fvg": bool(signal.get("price_in_bull_fvg", False)),
                    "ict_bullish_count": int(signal.get("ict_bullish_count", 0) or 0),
                    "ict_structure": str(signal.get("ict_structure", "undecided") or "undecided"),
                    "pullback_detected": bool(signal.get("pullback_detected", False)),
                    "pullback_score": float(signal.get("pullback_score", 0.0) or 0.0),
                    "spike_pct_15m": float(signal.get("spike_pct_15m", 0.0) or 0.0),
                    "retrace_from_high_pct": float(signal.get("retrace_from_high_pct", 0.0) or 0.0),
                    "vol_contracted_on_pullback": bool(signal.get("vol_contracted_on_pullback", False)),
                }
            )

        ranked_candidates.sort(key=lambda item: item.get("combined_score", 0.0), reverse=True)
        leader = ranked_candidates[0] if ranked_candidates else {
            "market": next(iter(weights), "KRW-BTC"),
            "weight": 0.0,
            "signal_score": float(direction_signal.get("score", 0.5) or 0.5),
            "combined_score": float(direction_signal.get("score", 0.5) or 0.5),
            "bias": str(direction_signal.get("bias", "balanced") or "balanced"),
            "recent_change_pct": float(direction_signal.get("recent_change_pct", 0.0) or 0.0),
            "burst_change_pct": float(direction_signal.get("burst_change_pct", 0.0) or 0.0),
            "ema_gap_pct": float(direction_signal.get("ema_gap_pct", 0.0) or 0.0),
            "pullback_gap_pct": float(direction_signal.get("pullback_gap_pct", 0.0) or 0.0),
            "range_4_pct": float(direction_signal.get("range_4_pct", 0.0) or 0.0),
            "rsi": direction_signal.get("rsi"),
            "reasons": list(direction_signal.get("reasons", [])),
            "micro_score": 0.0,
            "micro_ready": False,
            "micro_bias": "neutral",
            "micro_reasons": [],
            "orderbook_score": 0.0,
            "orderbook_ready": False,
            "orderbook_reasons": [],
        }

        lead_market = str(leader.get("market", "") or next(iter(weights), "KRW-BTC"))
        candidate_markets = [str(item.get("market", "")).strip() for item in ranked_candidates if str(item.get("market", "")).strip()]
        candidate_summary = [
            f"{item['market']} score {item['combined_score']:.2f} / bias {item['bias']} / weight {item['weight']:.2f}"
            for item in ranked_candidates[:3]
        ]

        return AgentResult(
            name=self.name,
            score=float(leader.get("combined_score", 0.5) or 0.5),
            reason=(
                f"crypto leader {lead_market} selected with score {leader.get('combined_score', 0.0):.2f} "
                f"under BTC backdrop {direction_signal.get('bias', 'balanced')}"
            ),
            payload={
                "lead_market": lead_market,
                "direction_market": direction_symbol,
                "desk_bias": leader.get("bias", "balanced"),
                "reasons": candidate_summary + list(direction_signal.get("reasons", []))[:2] + list(leader.get("reasons", []))[:2],
                "signal_score": float(leader.get("combined_score", 0.5) or 0.5),
                "discovery_score": float(leader.get("discovery_score", 0.0) or 0.0),
                "change_rate": float(leader.get("change_rate", 0.0) or 0.0),
                "volume_24h_krw": int(float(leader.get("volume_24h_krw", 0.0) or 0.0)),
                "recent_change_pct": float(leader.get("recent_change_pct", 0.0) or 0.0),
                "burst_change_pct": float(leader.get("burst_change_pct", 0.0) or 0.0),
                "ema_gap_pct": float(leader.get("ema_gap_pct", 0.0) or 0.0),
                "pullback_gap_pct": float(leader.get("pullback_gap_pct", 0.0) or 0.0),
                "range_4_pct": float(leader.get("range_4_pct", 0.0) or 0.0),
                "rsi": leader.get("rsi"),
                "micro_score": float(leader.get("micro_score", 0.0) or 0.0),
                "micro_ready": bool(leader.get("micro_ready", False)),
                "micro_bias": str(leader.get("micro_bias", "neutral") or "neutral"),
                "micro_reasons": list(leader.get("micro_reasons", [])),
                "micro_vol_ratio": float(leader.get("micro_vol_ratio", 0.0) or 0.0),
                "micro_move_3_pct": float(leader.get("micro_move_3_pct", 0.0) or 0.0),
                "micro_vwap_gap_pct": float(leader.get("micro_vwap_gap_pct", 0.0) or 0.0),
                "orderbook_score": float(leader.get("orderbook_score", 0.0) or 0.0),
                "orderbook_ready": bool(leader.get("orderbook_ready", False)),
                "orderbook_bid_ask_ratio": float(leader.get("orderbook_bid_ask_ratio", 0.0) or 0.0),
                "orderbook_spread_pct": float(leader.get("orderbook_spread_pct", 0.0) or 0.0),
                "orderbook_imbalance": float(leader.get("orderbook_imbalance", 0.0) or 0.0),
                "orderbook_reasons": list(leader.get("orderbook_reasons", [])),
                "atr_pct": float(leader.get("atr_pct", 0.0) or 0.0),
                "atr_size_multiplier": float(leader.get("atr_size_multiplier", 1.0) or 1.0),
                "volatility_tier": str(leader.get("volatility_tier", "unknown") or "unknown"),
                "atr_sizing_reason": str(leader.get("atr_sizing_reason", "") or ""),
                "btc_corr_15m": float(leader.get("btc_corr_15m", 1.0) or 1.0),
                "signal_age_minutes": float(leader.get("signal_age_minutes", 999.0) or 999.0),
                "signal_freshness": float(leader.get("signal_freshness", 1.0) or 1.0),
                "freshness_reason": str(leader.get("freshness_reason", "") or ""),
                "backtest_weights": weights,
                "candidate_symbols": candidate_markets[:6],
                "candidate_markets": ranked_candidates[:6],
                "scanned_market_count": len(all_markets),
                "discovery_universe_count": len(discovery_candidates),
                "direction_bias": direction_signal.get("bias", "balanced"),
                "direction_score": float(direction_signal.get("score", 0.5) or 0.5),
                "breakout_confirmed": bool(leader.get("breakout_confirmed", False)),
                "breakout_partial": bool(leader.get("breakout_partial", False)),
                "breakout_count": int(leader.get("breakout_count", 0) or 0),
                "vol_ratio": float(leader.get("vol_ratio", 0.0) or 0.0),
                "breakout_score": float(leader.get("breakout_score", 0.0) or 0.0),
                "rsi_quality_ok": bool(leader.get("rsi_quality_ok", True)),
                "rsi_reset_confirmed": bool(leader.get("rsi_reset_confirmed", False)),
                "rsi_bearish_divergence": bool(leader.get("rsi_bearish_divergence", False)),
                "rsi_extreme": bool(leader.get("rsi_extreme", False)),
                "ict_score": float(leader.get("ict_score", 0.0) or 0.0),
                "kill_zone_active": bool(leader.get("kill_zone_active", False)),
                "kill_zone_name": leader.get("kill_zone_name"),
                "ssl_sweep_confirmed": bool(leader.get("ssl_sweep_confirmed", False)),
                "choch_bullish": bool(leader.get("choch_bullish", False)),
                "choch_bearish": bool(leader.get("choch_bearish", False)),
                "bos_bullish": bool(leader.get("bos_bullish", False)),
                "bos_bearish": bool(leader.get("bos_bearish", False)),
                "price_at_bull_ob": bool(leader.get("price_at_bull_ob", False)),
                "price_in_bull_fvg": bool(leader.get("price_in_bull_fvg", False)),
                "pullback_detected": bool(leader.get("pullback_detected", False)),
                "pullback_score": float(leader.get("pullback_score", 0.0) or 0.0),
                "spike_pct_15m": float(leader.get("spike_pct_15m", 0.0) or 0.0),
                "retrace_from_high_pct": float(leader.get("retrace_from_high_pct", 0.0) or 0.0),
                "vol_contracted_on_pullback": bool(leader.get("vol_contracted_on_pullback", False)),
                "ict_bullish_count": int(leader.get("ict_bullish_count", 0) or 0),
                "ict_structure": str(leader.get("ict_structure", "undecided") or "undecided"),
            },
        )

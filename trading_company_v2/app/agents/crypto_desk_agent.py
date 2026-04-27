from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.backtest_advisor import get_crypto_weights
from app.services.market_gateway import get_upbit_15m_candles, get_upbit_1m_candles, get_upbit_orderbook
from app.services.signal_engine import (
    summarize_crypto_micro_momentum_signal,
    summarize_crypto_signal,
    summarize_orderbook_pressure,
)


class CryptoDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("crypto_desk_agent")

    def run(self) -> AgentResult:
        weights = get_crypto_weights()
        direction_symbol = "KRW-BTC"
        direction_signal = summarize_crypto_signal(get_upbit_15m_candles(direction_symbol, count=40))

        ranked_candidates: list[dict] = []
        for market, weight in list(weights.items())[:4]:
            signal = summarize_crypto_signal(get_upbit_15m_candles(market, count=40))
            micro = summarize_crypto_micro_momentum_signal(get_upbit_1m_candles(market, count=80))
            orderbook = summarize_orderbook_pressure(get_upbit_orderbook(market))
            combined_score = round(
                (float(signal.get("score", 0.5) or 0.5) * 0.50)
                + (float(micro.get("micro_score", 0.0) or 0.0) * 0.21)
                + (float(orderbook.get("orderbook_score", 0.0) or 0.0) * 0.08)
                + (float(direction_signal.get("score", 0.5) or 0.5) * 0.15)
                + (float(weight or 0.0) * 0.06),
                3,
            )
            if bool(micro.get("micro_ready", False)) and bool(orderbook.get("orderbook_ready", False)) and bool(signal.get("rsi_quality_ok", True)):
                combined_score = min(1.0, round(combined_score + 0.06, 3))
            ranked_candidates.append(
                {
                    "market": market,
                    "weight": round(float(weight or 0.0), 4),
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
                "backtest_weights": weights,
                "candidate_symbols": candidate_markets[:4],
                "candidate_markets": ranked_candidates[:4],
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
                "ict_bullish_count": int(leader.get("ict_bullish_count", 0) or 0),
                "ict_structure": str(leader.get("ict_structure", "undecided") or "undecided"),
            },
        )

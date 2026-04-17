from __future__ import annotations

from contextlib import contextmanager

import pandas as pd

import config
from src.strategy import check_entry_signal

from .base import TradingAgent, utcnow_iso
from .state import CACHE_DIR, SIGNALS_DIR, load_json_artifact, load_state, write_json_artifact


COIN_CACHE_FILE = CACHE_DIR / "coin_data.json"
COIN_SIGNAL_FILE = SIGNALS_DIR / "coin_signals.json"


@contextmanager
def _temporary_coin_params(params: dict):
    original = {
        "K": config.K,
        "ATR_STOP_MULT": config.ATR_STOP_MULT,
        "ATR_TRAIL_MULT": config.ATR_TRAIL_MULT,
        "RSI_PERIOD": config.RSI_PERIOD,
        "RSI_OVERSOLD": config.RSI_OVERSOLD,
    }
    try:
        for key, value in original.items():
            new_value = params.get(key)
            if new_value is not None:
                setattr(config, key, new_value)
        yield
    finally:
        for key, value in original.items():
            setattr(config, key, value)


class CoinSignalAgent(TradingAgent):
    """Generates coin entry signals from cached candle data and tuned params."""

    def __init__(self):
        super().__init__(name="coin_signal_agent")

    def run(self) -> dict:
        state = load_state()
        insight_score = float(state.get("strategy", {}).get("insight_score") or 0.5)
        coin_params = state.get("parameters", {}).get("coin", {})
        cache = load_json_artifact(COIN_CACHE_FILE, default={"candles": {}, "markets": []})

        signals: list[dict] = []
        with _temporary_coin_params(coin_params):
            for market, rows in cache.get("candles", {}).items():
                if not rows:
                    continue
                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["date"])
                signal = check_entry_signal(df)
                if not signal:
                    continue

                base_score = float(signal.get("score", 0))
                confidence = min(1.0, round((base_score / 3.0) * 0.7 + insight_score * 0.3, 4))
                signals.append(
                    {
                        "market": market,
                        "entry_price": signal["entry_price"],
                        "stop_loss": signal["stop_loss"],
                        "atr": signal["atr"],
                        "candle_time": signal["candle_time"],
                        "base_score": base_score,
                        "confidence": confidence,
                        "insight_score": insight_score,
                        "score_reasons": signal.get("score_reasons", []),
                    }
                )

        signals.sort(key=lambda item: item["confidence"], reverse=True)
        payload = {
            "updated_at": utcnow_iso(),
            "insight_score": insight_score,
            "signal_count": len(signals),
            "signals": signals,
        }
        write_json_artifact(COIN_SIGNAL_FILE, payload)

        return {
            "score": 1.0 if signals else 0.5,
            "reason": f"generated {len(signals)} coin signals",
            "raw": {
                "signal_file": str(COIN_SIGNAL_FILE),
                "signal_count": len(signals),
            },
        }

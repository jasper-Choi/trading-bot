from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENTS_DIR = Path(__file__).resolve().parent
CACHE_DIR = AGENTS_DIR / "cache"
SIGNALS_DIR = AGENTS_DIR / "signals"
STATE_FILE = AGENTS_DIR / "state.json"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "strategy": {
        "direction": "NEUTRAL",
        "insight_score": None,
        "market_regime": None,
        "risk_budget": None,
        "notes": [],
        "last_decision_at": None,
    },
    "parameters": {
        "applied_at": None,
        "coin": {
            "K": None,
            "ATR_STOP_MULT": None,
            "ATR_TRAIL_MULT": None,
            "RSI_PERIOD": None,
            "RSI_OVERSOLD": None,
        },
        "stock": {
            "gap_min_pct": None,
            "top_n": None,
        },
    },
    "risk": {
        "allow_new_entries": True,
        "daily_loss_limit_pct": None,
        "current_daily_pnl": None,
        "drawdown_pct": None,
        "position_scale": 1.0,
        "warnings": [],
        "last_checked_at": None,
    },
    "agents": {
        "ceo_agent": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "strategy_agent": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "coin_data_agent": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "stock_data_agent": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "coin_signal_agent": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "stock_signal_agent": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "risk_agent": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "coin_executor": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "stock_executor": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
        "notifier": {
            "status": "idle",
            "last_run_at": None,
            "summary": {},
        },
    },
    "artifacts": {
        "coin_cache_file": "src/agents/cache/coin_data.json",
        "stock_cache_file": "src/agents/cache/stock_data.json",
        "coin_signal_file": "src/agents/signals/coin_signals.json",
        "stock_signal_file": "src/agents/signals/stock_signals.json",
    },
}


def ensure_agent_directories() -> None:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        payload = deepcopy(DEFAULT_STATE)
        payload["updated_at"] = utcnow_iso()
        with STATE_FILE.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=True, indent=2)
            fp.write("\n")


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_state() -> dict[str, Any]:
    ensure_agent_directories()
    with STATE_FILE.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    return _deep_merge(DEFAULT_STATE, data)


def write_state(state: dict[str, Any]) -> dict[str, Any]:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(state)
    payload["updated_at"] = utcnow_iso()
    with STATE_FILE.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=True, indent=2)
        fp.write("\n")
    return payload


def merge_state(updates: dict[str, Any]) -> dict[str, Any]:
    state = load_state()
    merged = _deep_merge(state, updates)
    return write_state(merged)


def write_json_artifact(path: Path | str, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=True, indent=2)
        fp.write("\n")


def load_json_artifact(path: Path | str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def update_agent_status(name: str, status: str, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    state = load_state()
    agents = state.setdefault("agents", {})
    agent_state = agents.setdefault(name, {"status": "idle", "last_run_at": None, "summary": {}})
    agent_state["status"] = status
    agent_state["last_run_at"] = utcnow_iso()
    if summary is not None:
        agent_state["summary"] = summary
    return write_state(state)

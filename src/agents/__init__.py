"""Multi-agent orchestration package for trading bot workflows."""

from .base import TradingAgent
from .ceo_agent import CEOAgent
from .state import (
    AGENTS_DIR,
    CACHE_DIR,
    SIGNALS_DIR,
    STATE_FILE,
    ensure_agent_directories,
    load_state,
    merge_state,
    update_agent_status,
)
from .strategy_agent import StrategyAgent

__all__ = [
    "AGENTS_DIR",
    "CACHE_DIR",
    "CEOAgent",
    "SIGNALS_DIR",
    "STATE_FILE",
    "StrategyAgent",
    "TradingAgent",
    "ensure_agent_directories",
    "load_state",
    "merge_state",
    "update_agent_status",
]

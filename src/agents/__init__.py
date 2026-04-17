"""Multi-agent orchestration package for trading bot workflows."""

from .base import TradingAgent
from .ceo_agent import CEOAgent
from .coin_data_agent import CoinDataAgent
from .coin_signal_agent import CoinSignalAgent
from .state import (
    AGENTS_DIR,
    CACHE_DIR,
    SIGNALS_DIR,
    STATE_FILE,
    ensure_agent_directories,
    load_json_artifact,
    load_state,
    merge_state,
    update_agent_status,
    write_json_artifact,
)
from .stock_data_agent import StockDataAgent
from .stock_signal_agent import StockSignalAgent
from .strategy_agent import StrategyAgent

__all__ = [
    "AGENTS_DIR",
    "CACHE_DIR",
    "CEOAgent",
    "CoinDataAgent",
    "CoinSignalAgent",
    "SIGNALS_DIR",
    "STATE_FILE",
    "StockDataAgent",
    "StockSignalAgent",
    "StrategyAgent",
    "TradingAgent",
    "ensure_agent_directories",
    "load_json_artifact",
    "load_state",
    "merge_state",
    "update_agent_status",
    "write_json_artifact",
]

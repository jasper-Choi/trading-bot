import os
from fastapi import APIRouter
from src.agents.state import STATE_FILE, load_json_artifact, load_state

router = APIRouter(prefix="/insights", tags=["insights"])
agent_router = APIRouter(prefix="/agents", tags=["agents"])

@router.get("/")
def get_insights():
    try:
        from src.insight_agents.orchestrator import OrchestratorAgent
        api_key = os.environ.get("OPENAI_API_KEY", "")
        orchestrator = OrchestratorAgent(openai_api_key=api_key)
        return orchestrator.run()
    except ModuleNotFoundError as exc:
        return {
            "insight_score": 0.5,
            "timestamp": None,
            "agents": {},
            "fallback_reason": f"missing dependency: {exc.name}",
        }

def _build_agents_status():
    state = load_state()
    coin_signals = load_json_artifact(state["artifacts"]["coin_signal_file"], default={"signal_count": 0})
    stock_signals = load_json_artifact(state["artifacts"]["stock_signal_file"], default={"signal_count": 0})
    coin_cache = load_json_artifact(state["artifacts"]["coin_cache_file"], default={"cached_count": 0})
    stock_cache = load_json_artifact(
        state["artifacts"]["stock_cache_file"],
        default={"universe_count": 0, "gap_up_count": 0},
    )

    return {
        "updated_at": state.get("updated_at"),
        "strategy": state.get("strategy", {}),
        "risk": state.get("risk", {}),
        "agents": state.get("agents", {}),
        "artifacts": {
            "coin_cached_count": coin_cache.get("cached_count", 0),
            "stock_universe_count": stock_cache.get("universe_count", 0),
            "stock_gap_up_count": stock_cache.get("gap_up_count", 0),
            "coin_signal_count": coin_signals.get("signal_count", 0),
            "stock_signal_count": stock_signals.get("signal_count", 0),
        },
        "state_file": str(STATE_FILE),
    }


@router.get("/agents/status")
def get_agents_status():
    return _build_agents_status()


@agent_router.get("/status")
def get_agents_status_alias():
    return _build_agents_status()

@router.get("/debug")
def debug():
    api_key = os.environ.get("OPENAI_API_KEY", "")
    all_env = {k: v[:6] + "..." for k, v in os.environ.items() if "KEY" in k or "TOKEN" in k}
    return {
        "key_length": len(api_key),
        "key_preview": api_key[:8] + "..." if len(api_key) > 8 else "TOO_SHORT",
        "env_keys": all_env
    }

import os
from fastapi import APIRouter
from src.insight_agents.orchestrator import OrchestratorAgent

router = APIRouter(prefix="/insights", tags=["insights"])

@router.get("/")
def get_insights():
    api_key = os.environ.get("OPENAI_API_KEY", "")
    orchestrator = OrchestratorAgent(openai_api_key=api_key)
    result = orchestrator.run()
    return result

@router.get("/debug")
def debug():
    api_key = os.environ.get("OPENAI_API_KEY", "")
    all_env = {k: v[:6] + "..." for k, v in os.environ.items() if "KEY" in k or "TOKEN" in k}
    return {
        "key_length": len(api_key),
        "key_preview": api_key[:8] + "..." if len(api_key) > 8 else "TOO_SHORT",
        "env_keys": all_env
    }
import os
from fastapi import APIRouter
from src.insight_agents.orchestrator import OrchestratorAgent

router = APIRouter(prefix="/insights", tags=["insights"])

@router.get("/")
def get_insights():
    api_key = os.getenv("OPENAI_API_KEY", "")
    orchestrator = OrchestratorAgent(openai_api_key=api_key)
    result = orchestrator.run()
    return result

@router.get("/debug")
def debug():
    api_key = os.getenv("OPENAI_API_KEY", "NOT_FOUND")
    return {
        "key_found": bool(api_key and api_key != "NOT_FOUND"),
        "key_preview": api_key[:8] + "..." if api_key else "EMPTY"
    }
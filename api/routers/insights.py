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
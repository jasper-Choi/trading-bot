from datetime import datetime
from .sentiment_agent import SentimentAgent
from .trend_agent import TrendAgent
from .macro_agent import MacroAgent
from .onchain_agent import OnchainAgent

WEIGHTS = {
    "sentiment": 0.25,
    "trend":     0.40,
    "macro":     0.15,
    "onchain":   0.20,
}

class OrchestratorAgent:
    def __init__(self, openai_api_key: str):
        self.agents = {
            "sentiment": SentimentAgent(api_key=openai_api_key),
            "trend":     TrendAgent(),
            "macro":     MacroAgent(),
            "onchain":   OnchainAgent(),
        }

    def run(self) -> dict:
        results = {}
        for name, agent in self.agents.items():
            results[name] = agent.safe_run()

        insight_score = sum(
            results[name]["score"] * weight
            for name, weight in WEIGHTS.items()
        )
        insight_score = round(insight_score, 4)

        return {
            "insight_score": insight_score,
            "timestamp": datetime.utcnow().isoformat(),
            "agents": {
                name: {
                    "score": results[name]["score"],
                    "reason": results[name]["reason"],
                }
                for name in results
            },
        }
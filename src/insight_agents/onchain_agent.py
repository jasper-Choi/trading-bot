import requests
from .base_agent import BaseAgent

FNG_URL = "https://api.alternative.me/fng/"

class OnchainAgent(BaseAgent):
    def __init__(self):
        super().__init__("OnchainAgent")

    def run(self) -> dict:
        resp = requests.get(FNG_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()["data"][0]

        value = int(data["value"])          # 0~100
        classification = data["value_classification"]
        normalized = round(value / 100.0, 4)

        return {
            "score": normalized,
            "reason": f"Fear & Greed: {value} ({classification})",
            "raw": data,
        }
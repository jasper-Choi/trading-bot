import os
import requests
import feedparser
from .base_agent import BaseAgent

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]

HF_API_URL = "https://api-inference.huggingface.co/models/ProsusAI/finbert"

class SentimentAgent(BaseAgent):
    def __init__(self, api_key: str = ""):
        super().__init__("SentimentAgent")
        self.hf_key = os.getenv("HUGGINGFACE_API_KEY", api_key)

    def _fetch_headlines(self) -> list[str]:
        headlines = []
        for url in RSS_FEEDS:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                headlines.append(entry.title)
        return headlines[:6]

    def _analyze(self, text: str) -> float:
        headers = {"Authorization": f"Bearer {self.hf_key}"}
        response = requests.post(HF_API_URL, headers=headers, json={"inputs": text}, timeout=15)
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            scores = result[0]
            score_map = {item["label"]: item["score"] for item in scores}
            positive = score_map.get("positive", 0)
            negative = score_map.get("negative", 0)
            return round(positive / (positive + negative + 1e-9), 4)
        return 0.5

    def run(self) -> dict:
        headlines = self._fetch_headlines()
        if not headlines:
            return {"score": 0.5, "reason": "no headlines fetched", "raw":
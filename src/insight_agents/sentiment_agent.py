import os
import requests
import feedparser
from .base_agent import BaseAgent

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]

HF_API_URL = "https://api-inference.huggingface.co/models/ProsusAI/finbert"

SAMPLE_HEADLINES = [
    "Bitcoin rises amid growing institutional interest",
    "Crypto market shows signs of recovery",
    "Ethereum upgrade boosts investor confidence",
]

class SentimentAgent(BaseAgent):
    def __init__(self, api_key: str = ""):
        super().__init__("SentimentAgent")
        self.hf_key = os.getenv("HUGGINGFACE_API_KEY", api_key)

    def _fetch_headlines(self) -> list[str]:
        headlines = []
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    headlines.append(entry.title)
            except Exception:
                continue
        return headlines[:6] if headlines else SAMPLE_HEADLINES

    def _analyze(self, text: str) -> float:
        headers = {"Authorization": f"Bearer {self.hf_key}"}
        response = requests.post(
            HF_API_URL,
            headers=headers,
            json={"inputs": text},
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text[:100]}")
        result = response.json()
        if isinstance(result, dict) and "error" in result:
            raise Exception(f"HF error: {result['error']}")
        if isinstance(result, list) and len(result) > 0:
            scores = result[0]
            score_map = {item["label"]: item["score"] for item in scores}
            positive = score_map.get("positive", 0)
            negative = score_map.get("negative", 0)
            return round(positive / (positive + negative + 1e-9), 4)
        return 0.5

    def run(self) -> dict:
        headlines = self._fetch_headlines()
        individual_scores = []
        errors = []
        for h in headlines:
            try:
                s = self._analyze(h)
                individual_scores.append(s)
            except Exception as e:
                errors.append(str(e))
                continue

        if not individual_scores:
            return {"score": 0.5, "reason": f"failed: {errors[:1]}", "raw": {}}

        avg = round(sum(individual_scores) / len(individual_scores), 4)
        return {
            "score": avg,
            "reason": f"FinBERT across {len(individual_scores)} headlines",
            "raw": {"headlines": headlines, "scores": individual_scores},
        }

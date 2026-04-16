import os
import feedparser
from huggingface_hub import InferenceClient
from .base_agent import BaseAgent

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]

SAMPLE_HEADLINES = [
    "Bitcoin rises amid growing institutional interest",
    "Crypto market shows signs of recovery",
    "Ethereum upgrade boosts investor confidence",
]

class SentimentAgent(BaseAgent):
    def __init__(self, api_key: str = ""):
        super().__init__("SentimentAgent")
        self.hf_key = os.getenv("HUGGINGFACE_API_KEY", api_key)
        self.client = InferenceClient(
            provider="hf-inference",
            api_key=self.hf_key,
        )

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
        result = self.client.text_classification(
            text,
            model="distilbert-base-uncased-finetuned-sst-2-english",
        )
        score_map = {item.label.upper(): item.score for item in result}
        positive = score_map.get("POSITIVE", 0)
        negative = score_map.get("NEGATIVE", 0)
        return round(positive / (positive + negative + 1e-9), 4)

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
            "reason": f"DistilBERT sentiment across {len(individual_scores)} headlines",
            "raw": {"headlines": headlines, "scores": individual_scores},
        }

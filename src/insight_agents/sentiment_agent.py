import feedparser
from openai import OpenAI
from .base_agent import BaseAgent

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]

PROMPT_TEMPLATE = """
You are a crypto market sentiment analyst.
Below are recent news headlines. Rate the overall market sentiment on a scale from -1.0 (extremely bearish) to 1.0 (extremely bullish).
Respond with JSON only: {{"score": float, "reason": "one sentence"}}

Headlines:
{headlines}
"""

class SentimentAgent(BaseAgent):
    def __init__(self, api_key: str):
        super().__init__("SentimentAgent")
        self.client = OpenAI(api_key=api_key)

    def _fetch_headlines(self) -> list[str]:
        headlines = []
        for url in RSS_FEEDS:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                headlines.append(entry.title)
        return headlines[:10]

    def run(self) -> dict:
        import json
        headlines = self._fetch_headlines()
        if not headlines:
            return {"score": 0.5, "reason": "no headlines fetched", "raw": {}}

        prompt = PROMPT_TEMPLATE.format(headlines="\n".join(f"- {h}" for h in headlines))
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = json.loads(response.choices[0].message.content)
        normalized = (raw["score"] + 1.0) / 2.0  # -1~1 → 0~1
        return {
            "score": round(max(0.0, min(1.0, normalized)), 4),
            "reason": raw["reason"],
            "raw": {"headlines": headlines, "llm_raw": raw},
        }
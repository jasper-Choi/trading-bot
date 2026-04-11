"""
네이버 금융 뉴스 스캔 + Claude API 감성 분석.

감성 레이블: POSITIVE / NEUTRAL / NEGATIVE / SKIP
신뢰도 0.7 이상만 진입에 활용.

환경변수:
  ANTHROPIC_API_KEY — Claude API 키 (없으면 감성 분석 생략)
"""

import os
import re
import time
import requests
from datetime import datetime, timedelta
from typing import Optional

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

NAVER_NEWS_URL = "https://finance.naver.com/item/news_news.naver"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://finance.naver.com",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

SENTIMENT_LABELS = ("POSITIVE", "NEUTRAL", "NEGATIVE", "SKIP")


# ── 뉴스 수집 ─────────────────────────────────────────────────────────────────

def _fetch_news(ticker: str, limit: int = 10) -> list[dict]:
    """네이버 금융 종목 뉴스 — 최근 30분 이내 항목만 반환."""
    params = {"code": ticker, "page": 1}
    try:
        resp = requests.get(
            NAVER_NEWS_URL, params=params, headers=HEADERS, timeout=10
        )
        resp.encoding = "euc-kr"
        html = resp.text
    except Exception:
        return []

    # <a href="...news_read...">제목</a> ... 날짜
    pattern = re.compile(
        r'href="([^"]*news_read[^"]*)"[^>]*>(.*?)</a>.*?'
        r'(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2})',
        re.DOTALL,
    )

    cutoff  = datetime.now() - timedelta(minutes=30)
    results = []

    for m in pattern.finditer(html):
        title_raw = m.group(2)
        time_str  = m.group(3).strip()
        title     = re.sub(r"<[^>]+>", "", title_raw).strip()
        if not title:
            continue
        try:
            pub_time = datetime.strptime(time_str, "%Y.%m.%d %H:%M")
        except ValueError:
            continue
        if pub_time < cutoff:
            continue
        results.append({
            "title":    title,
            "pub_time": pub_time.strftime("%Y-%m-%d %H:%M"),
        })
        if len(results) >= limit:
            break

    return results


# ── Claude API 감성 분석 ─────────────────────────────────────────────────────

def _analyze_sentiment(title: str) -> tuple[str, float]:
    """
    Claude API(claude-haiku-4-5)로 뉴스 제목 감성 분석.
    Returns: (label, confidence)  — POSITIVE / NEUTRAL / NEGATIVE / SKIP
    """
    if not ANTHROPIC_API_KEY:
        return "SKIP", 0.0

    prompt = (
        f'주식 뉴스 제목의 감성을 분석하세요.\n\n'
        f'뉴스 제목: "{title}"\n\n'
        f'다음 형식으로만 응답하세요 (다른 텍스트 없이):\n'
        f'LABEL|CONFIDENCE\n\n'
        f'LABEL은 POSITIVE, NEUTRAL, NEGATIVE, SKIP 중 하나.\n'
        f'CONFIDENCE는 0.0~1.0 소수점 한 자리.\n'
        f'(SKIP은 주식과 무관한 뉴스)\n\n'
        f'예시: POSITIVE|0.8'
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 20,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        text   = resp.json()["content"][0]["text"].strip()
        parts  = text.split("|")
        if len(parts) == 2:
            label = parts[0].strip().upper()
            conf  = float(parts[1].strip())
            if label in SENTIMENT_LABELS:
                return label, round(min(max(conf, 0.0), 1.0), 1)
    except Exception:
        pass

    return "SKIP", 0.0


# ── 공개 API ─────────────────────────────────────────────────────────────────

def analyze_stock_news(ticker: str, min_confidence: float = 0.7) -> dict:
    """
    종목 뉴스를 수집하고 감성을 분석합니다.

    Returns:
        {
            "ticker":     "035720",
            "label":      "POSITIVE",
            "confidence": 0.8,
            "news":       [{"title", "pub_time", "label", "confidence"}, ...]
        }
    """
    news_items = _fetch_news(ticker)
    if not news_items:
        return {"ticker": ticker, "label": "SKIP", "confidence": 0.0, "news": []}

    analyzed       = []
    positive_scores: list[float] = []
    negative_scores: list[float] = []

    for item in news_items[:5]:
        label, conf = _analyze_sentiment(item["title"])
        analyzed.append({**item, "label": label, "confidence": conf})
        if label == "POSITIVE" and conf >= min_confidence:
            positive_scores.append(conf)
        elif label == "NEGATIVE" and conf >= min_confidence:
            negative_scores.append(conf)
        time.sleep(0.3)

    # 대표 레이블 결정
    pos_sum = sum(positive_scores)
    neg_sum = sum(negative_scores)

    if positive_scores and pos_sum > neg_sum:
        rep_label = "POSITIVE"
        rep_conf  = round(pos_sum / len(positive_scores), 1)
    elif negative_scores:
        rep_label = "NEGATIVE"
        rep_conf  = round(neg_sum / len(negative_scores), 1)
    elif analyzed:
        rep_label = "NEUTRAL"
        rep_conf  = 0.6
    else:
        rep_label = "SKIP"
        rep_conf  = 0.0

    return {
        "ticker":     ticker,
        "label":      rep_label,
        "confidence": rep_conf,
        "news":       analyzed,
    }

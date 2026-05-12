"""
Naver 종목토론방 + 뉴스 기반 감성 스코어.
- board_score: 종목토론방 최근 글 키워드 분석 (60% weight)
- news_score: 뉴스 헤드라인 키워드 분석 (40% weight)
- combined_score: 0.0~1.0 (0.5 = neutral)
- attention_boost: True if 최근 게시물 수 급증
Cache TTL: 15분 per ticker.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from html import unescape
from typing import Any

import requests
from requests import RequestException

from app.services.market_gateway import NAVER_HEADERS, REQUEST_TIMEOUT, _strip_html

_log = logging.getLogger(__name__)

_BOARD_URL = "https://finance.naver.com/item/board.naver?code={ticker}"
_NEWS_URL = "https://finance.naver.com/item/news_news.naver?code={ticker}"

_CACHE_TTL = 900.0  # 15 minutes
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}  # ticker → (ts, result)

# ── Keyword tables ──────────────────────────────────────────────────────────
_BULLISH_WORDS = [
    "급등", "상승", "매수", "돌파", "신고", "호재", "기대", "강세", "목표", "상향",
    "긍정", "성장", "수주", "계약", "흑자", "실적", "배당", "매집", "눌림", "반등",
    "저평가", "상장", "테마", "이슈", "주목", "상한가", "대박", "황금", "선점",
]
_BEARISH_WORDS = [
    "급락", "하락", "매도", "손실", "악재", "위험", "공매도", "실망", "부진",
    "적자", "하향", "감소", "우려", "폭락", "주의", "경고", "하한가", "손절",
    "매물", "고점", "거품", "조정",
]


def _score_text(texts: list[str]) -> float:
    """Returns 0.0..1.0 sentiment score for a list of text snippets."""
    bull = 0
    bear = 0
    for text in texts:
        t = text.lower()
        for w in _BULLISH_WORDS:
            if w in t:
                bull += 1
        for w in _BEARISH_WORDS:
            if w in t:
                bear += 1
    total = bull + bear
    if total == 0:
        return 0.5
    return round(min(max(bull / total, 0.0), 1.0), 3)


def _fetch_board(ticker: str) -> tuple[float, int, list[str]]:
    """Returns (board_score, post_count, top_titles)."""
    try:
        resp = requests.get(
            _BOARD_URL.format(ticker=ticker),
            headers=NAVER_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.content.decode("euc-kr", errors="ignore")
    except RequestException as exc:
        _log.debug("board fetch failed %s: %s", ticker, exc)
        return 0.5, 0, []

    # Extract post titles
    titles = re.findall(
        r'class="title"[^>]*>\s*<a[^>]*>([^<]+)</a>',
        html,
        flags=re.S | re.I,
    )
    titles = [_strip_html(t) for t in titles[:30]]

    # Extract total post count (게시글 수)
    count_match = re.search(r'totalCount["\s:]+(\d[\d,]*)', html)
    post_count = int(count_match.group(1).replace(",", "")) if count_match else len(titles)

    score = _score_text(titles)
    return score, post_count, titles[:5]


def _fetch_news(ticker: str) -> tuple[float, list[str]]:
    """Returns (news_score, top_headlines)."""
    try:
        resp = requests.get(
            _NEWS_URL.format(ticker=ticker),
            headers=NAVER_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.content.decode("euc-kr", errors="ignore")
    except RequestException as exc:
        _log.debug("news fetch failed %s: %s", ticker, exc)
        return 0.5, []

    headlines = re.findall(
        r'class="articleSubject"[^>]*>\s*(?:<[^>]+>)*\s*([^<]{5,})',
        html,
        flags=re.S | re.I,
    )
    if not headlines:
        # fallback: any <a> inside dt/dd with newsTitle-like pattern
        headlines = re.findall(r'<a[^>]*title="([^"]{5,})"', html)
    headlines = [_strip_html(h) for h in headlines[:20]]
    score = _score_text(headlines)
    return score, headlines[:5]


def get_combined_sentiment(ticker: str) -> dict[str, Any]:
    """
    Returns:
        combined_score: float 0.0..1.0 (>0.55 bullish, <0.45 bearish)
        attention_boost: bool (많은 게시글 → 주목도 높음)
        board_score: float
        news_score: float
        top_discussion: list[str]
        top_news: list[str]
    """
    with _lock:
        cached = _cache.get(ticker)
        if cached:
            ts, result = cached
            if time.monotonic() - ts < _CACHE_TTL:
                return result

    board_score, post_count, top_discussion = _fetch_board(ticker)
    news_score, top_news = _fetch_news(ticker)

    combined = round(board_score * 0.60 + news_score * 0.40, 3)
    # attention_boost: 종목토론방 최근 게시글 100개 이상
    attention_boost = post_count >= 100

    result: dict[str, Any] = {
        "combined_score": combined,
        "attention_boost": attention_boost,
        "board_score": board_score,
        "news_score": news_score,
        "post_count": post_count,
        "top_discussion": top_discussion,
        "top_news": top_news,
    }
    with _lock:
        _cache[ticker] = (time.monotonic(), result)
    return result

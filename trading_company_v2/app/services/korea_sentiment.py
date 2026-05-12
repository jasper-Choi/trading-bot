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
_theme_cache: tuple[float, dict[str, int]] = (0.0, {})  # (ts, {theme: count})
_HOT_THEME_TTL = 1800.0  # 30분

# ── 테마 키워드 매핑 ─────────────────────────────────────────────────────────
_THEME_KEYWORDS: dict[str, list[str]] = {
    "AI":      ["AI", "인공지능", "LLM", "GPT", "딥러닝", "머신러닝", "챗봇", "생성형", "온디바이스"],
    "반도체":  ["반도체", "HBM", "파운드리", "DRAM", "낸드", "웨이퍼", "칩", "엔비디아", "TSMC"],
    "2차전지": ["배터리", "2차전지", "전기차", "EV", "양극재", "음극재", "전해질", "리튬", "전고체"],
    "바이오":  ["바이오", "신약", "임상", "FDA", "항체", "mRNA", "세포치료", "신약", "의약품"],
    "방산":    ["방산", "무기", "미사일", "탱크", "방위산업", "방위", "수출", "NATO", "K방산"],
    "로봇":    ["로봇", "자동화", "협동로봇", "산업용로봇", "휴머노이드", "RPA"],
    "조선":    ["조선", "LNG", "선박", "수주", "VLCC", "컨테이너선"],
    "원전":    ["원전", "핵발전", "SMR", "우라늄", "원자력", "핵융합"],
}

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


def _detect_themes(texts: list[str]) -> dict[str, int]:
    """텍스트 리스트에서 테마별 언급 횟수를 반환."""
    counts: dict[str, int] = {}
    joined = " ".join(texts)
    for theme, keywords in _THEME_KEYWORDS.items():
        count = sum(kw in joined for kw in keywords)
        if count:
            counts[theme] = count
    return counts


def get_hot_themes(board_titles: list[str]) -> dict[str, int]:
    """종목토론방 제목 리스트에서 오늘의 핫 테마 집계.

    외부에서 여러 종목 board_titles를 모아 넘겨주면 시장 전체 테마 열기를 파악한다.
    반환: {테마명: 언급수}
    """
    return _detect_themes(board_titles)


def get_theme_boost(texts: list[str], hot_themes: dict[str, int] | None = None) -> float:
    """특정 종목의 텍스트에 핫 테마 키워드가 있으면 0.0~0.15 부스트 반환."""
    detected = _detect_themes(texts)
    if not detected:
        return 0.0
    if hot_themes:
        # 시장에서 핫한 테마에 해당하면 추가 부스트
        overlap = sum(1 for t in detected if t in hot_themes and hot_themes[t] >= 3)
        return min(0.05 * len(detected) + 0.05 * overlap, 0.15)
    return min(0.05 * len(detected), 0.10)


# 펀더멘털 갭다운 리스크 키워드 (기술적 갭과 구분)
_GAP_RISK_KEYWORDS = [
    "적자", "영업손실", "순손실", "실적 부진", "실적쇼크", "어닝쇼크",
    "하향", "목표주가 하향", "투자의견 하향",
    "소송", "검찰", "수사", "조사", "고발", "기소",
    "상장폐지", "거래정지", "불성실공시",
    "대표이사 교체", "대표이사 사임", "최대주주 변경",
    "계약 해지", "계약 취소", "수주 취소",
    "횡령", "배임", "사기",
]
_NEWS_RISK_TTL = 600.0  # 10분
_news_risk_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_news_risk_lock = threading.Lock()


def get_stock_news_risk(ticker: str) -> dict[str, Any]:
    """당일 뉴스에서 펀더멘털 갭다운 리스크 키워드를 감지한다.
    Returns: {"has_risk": bool, "risk_keywords": list[str]}
    갭 메꾸기 전략에서 이유 있는 갭다운(실적악화, 소송 등)을 필터링하는 데 사용.
    """
    with _news_risk_lock:
        cached = _news_risk_cache.get(ticker)
        if cached and (time.time() - cached[0]) < _NEWS_RISK_TTL:
            return cached[1]
    try:
        resp = requests.get(
            _NEWS_URL.format(ticker=ticker),
            headers=NAVER_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.content.decode("euc-kr", errors="ignore")
    except RequestException:
        result: dict[str, Any] = {"has_risk": False, "risk_keywords": []}
        with _news_risk_lock:
            _news_risk_cache[ticker] = (time.time(), result)
        return result

    headlines = re.findall(r'class="articleSubject"[^>]*>.*?<a[^>]*>([^<]{5,})', html, flags=re.S | re.I)
    if not headlines:
        headlines = re.findall(r'<a[^>]*title="([^"]{5,})"', html)
    headlines = [_strip_html(h) for h in headlines[:15]]
    joined = " ".join(headlines)

    found = [kw for kw in _GAP_RISK_KEYWORDS if kw in joined]
    result = {"has_risk": bool(found), "risk_keywords": found[:5]}
    with _news_risk_lock:
        _news_risk_cache[ticker] = (time.time(), result)
    return result


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
    attention_boost = post_count >= 100

    all_texts = top_discussion + top_news
    detected_themes = _detect_themes(all_texts)
    theme_boost = get_theme_boost(all_texts)

    result: dict[str, Any] = {
        "combined_score": combined,
        "attention_boost": attention_boost,
        "board_score": board_score,
        "news_score": news_score,
        "post_count": post_count,
        "top_discussion": top_discussion,
        "top_news": top_news,
        "detected_themes": detected_themes,
        "theme_boost": round(theme_boost, 3),
    }
    with _lock:
        _cache[ticker] = (time.monotonic(), result)
    return result

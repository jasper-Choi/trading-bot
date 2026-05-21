"""글로벌 뉴스/SNS 인텔리전스 서비스.

전세계 뉴스·소셜미디어를 실시간 스캔하여 시장에 영향을 미치는
정보를 수집, 감성 분석, 점수화한다.

데이터 출처:
  - Google News RSS (영어 글로벌 + 한국어) — 무료, API 키 불필요
  - Nitter RSS (X/Twitter 핵심 계정) — 무료, best-effort (불안정)
  - Naver 금융 종목 뉴스 — 스크래핑, 한국 종목 전용
  - NewsAPI.org — 선택적 (API 키 있을 경우 강화)

핵심 모니터링 대상:
  - 트럼프 (@realDonaldTrump) : 관세/코인/주식 시장 언급
  - 연준/파월 (@federalreserve, @JeromePowell) : 금리 신호
  - 일론 머스크 (@elonmusk) : 코인/주식 직접 언급
  - 거시경제 뉴스 : 관세, 전쟁, 금리, 경기침체
  - 한국 종목 뉴스 : 공시, 실적, 수주, 테마

출력 (get_market_news_intel):
  impact      : "calm" | "caution" | "panic"
  macro_score : 0.0 (패닉) ~ 1.0 (매우 긍정)
  trump_alert : bool — 트럼프 시장 언급 감지
  tariff_alert: bool — 관세 관련 부정 뉴스
  crypto_boost: bool — 코인 긍정 뉴스 (트럼프 지지 등)
  korea_risk  : bool — 한국 시장 직접 리스크 뉴스
  breaking    : list[str] — 최근 1시간 헤드라인
  cached_at   : UTC timestamp
"""
from __future__ import annotations

import re
import time
import threading
from datetime import datetime, timezone, timedelta
from html import unescape
from typing import Any
from xml.etree import ElementTree

import requests

# ── 캐시 설정 ────────────────────────────────────────────────────────────────
_MARKET_CACHE_TTL   = 5 * 60   # 5분 (시장 급변 감지용)
_STOCK_CACHE_TTL    = 15 * 60  # 15분 (종목별 뉴스)
_NITTER_CACHE_TTL   = 3 * 60   # 3분 (SNS 변동성 빠름)

_market_cache: dict[str, Any] = {}
_stock_cache:  dict[str, Any] = {}
_lock = threading.Lock()

# ── 요청 설정 ─────────────────────────────────────────────────────────────────
_TIMEOUT = 8  # 초
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ── 감성 키워드 사전 ──────────────────────────────────────────────────────────
# 각 카테고리는 (키워드, 가중치) 형태
_PANIC_KW: list[tuple[str, float]] = [
    # 거시 쇼크
    ("tariff", 1.5), ("trade war", 2.0), ("trade ban", 1.8),
    ("관세", 1.5), ("무역전쟁", 2.0), ("수출금지", 1.8),
    ("sanction", 1.5), ("제재", 1.5),
    # 금융 위기
    ("crash", 2.0), ("circuit breaker", 2.5), ("market meltdown", 2.5),
    ("급락", 1.5), ("폭락", 2.0), ("서킷브레이커", 2.5),
    ("black monday", 3.0), ("flash crash", 2.5),
    # 금리 충격
    ("emergency rate hike", 2.5), ("surprise hike", 2.0),
    ("긴급 금리", 2.5), ("금리 충격", 2.0),
    # 지정학
    ("war", 1.0), ("invasion", 1.5), ("nuclear", 2.0), ("전쟁", 1.0), ("침공", 1.5),
    # 트럼프 부정
    ("trump tariff", 2.0), ("trump ban", 1.8), ("trump sanction", 1.8),
    ("trump trade", 1.5),
    # 한국 직접 리스크
    ("kospi", 0.5), ("코스피 급락", 2.0), ("외국인 매도", 1.5),
    ("korea default", 3.0), ("원화 급락", 2.0),
]

_POSITIVE_KW: list[tuple[str, float]] = [
    # 거시 호재
    ("rate cut", 2.0), ("금리 인하", 2.0), ("pivot", 1.5), ("금리 피벗", 1.5),
    ("trade deal", 2.0), ("무역 합의", 2.0), ("관세 완화", 2.0),
    ("tariff reduction", 2.0), ("tariff relief", 2.0),
    # 주식 상승
    ("bull market", 1.5), ("rally", 1.0), ("record high", 1.5),
    ("상승장", 1.5), ("강세장", 1.5), ("신고가", 1.0),
    # 코인 호재
    ("bitcoin etf", 1.5), ("crypto regulation clarity", 1.5),
    ("trump crypto", 1.5), ("trump bitcoin", 2.0),
    # 한국 호재
    ("수주", 1.0), ("실적 개선", 1.5), ("어닝 서프라이즈", 1.5),
    ("흑자 전환", 1.5), ("외국인 매수", 1.0),
]

# 트럼프 직접 언급 키워드 (X 포스트 또는 뉴스에서)
_TRUMP_MARKET_KW: list[str] = [
    "trump", "donald trump", "트럼프",
]

# ── Nitter 인스턴스 목록 (순서대로 시도) ──────────────────────────────────────
_NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://nitter.1d4.us",
    "https://nitter.poast.org",
    "https://bird.trom.tf",
]

# 모니터링 X 계정 목록 (사용자명, 레이블, 기본 가중치)
_KEY_ACCOUNTS: list[tuple[str, str, float]] = [
    ("realDonaldTrump", "트럼프", 2.0),
    ("POTUS",           "백악관", 1.5),
    ("federalreserve",  "연준",   1.8),
    ("elonmusk",        "일론머스크", 1.5),
    ("SecBessent",      "재무장관", 1.5),
    ("GaryGensler",     "SEC",    1.2),
]


# ─────────────────────────────────────────────────────────────────────────────
# 내부 유틸리티
# ─────────────────────────────────────────────────────────────────────────────

def _safe_get(url: str, timeout: int = _TIMEOUT) -> requests.Response | None:
    """요청 실패 시 None 반환 (예외 무시)."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def _parse_rss(xml_text: str, max_items: int = 30) -> list[dict]:
    """RSS XML을 파싱하여 [{title, link, published}] 반환."""
    items: list[dict] = []
    try:
        root = ElementTree.fromstring(xml_text)
        ns = ""
        channel = root.find("channel") or root
        for item in channel.findall(f"{ns}item")[:max_items]:
            title_el = item.find(f"{ns}title")
            link_el  = item.find(f"{ns}link")
            pub_el   = item.find(f"{ns}pubDate")
            title = unescape(title_el.text or "") if title_el is not None else ""
            link  = (link_el.text or "") if link_el is not None else ""
            pub   = (pub_el.text or "") if pub_el is not None else ""
            if title:
                items.append({"title": title.strip(), "link": link.strip(), "published": pub.strip()})
    except Exception:
        pass
    return items


def _score_text(text: str) -> tuple[float, float, list[str]]:
    """텍스트의 공황(-) / 긍정(+) 점수와 매칭 키워드를 반환.

    Returns: (panic_score, positive_score, matched_keywords)
    """
    lower = text.lower()
    panic_score = 0.0
    pos_score   = 0.0
    matched: list[str] = []

    for kw, w in _PANIC_KW:
        if kw in lower:
            panic_score += w
            matched.append(f"-{kw}")

    for kw, w in _POSITIVE_KW:
        if kw in lower:
            pos_score += w
            matched.append(f"+{kw}")

    return panic_score, pos_score, matched


def _headlines_to_impact(headlines: list[str]) -> dict:
    """헤드라인 목록을 종합 분석하여 market impact dict 반환."""
    total_panic  = 0.0
    total_pos    = 0.0
    all_matched: list[str] = []
    trump_alert  = False
    tariff_alert = False
    crypto_boost = False
    korea_risk   = False

    for h in headlines:
        p, pos, matched = _score_text(h)
        total_panic += p
        total_pos   += pos
        all_matched.extend(matched)

        lower = h.lower()
        if any(kw in lower for kw in _TRUMP_MARKET_KW):
            trump_alert = True
        if any(kw in lower for kw in ("tariff", "관세", "trade war", "무역전쟁")):
            tariff_alert = True
        if any(kw in lower for kw in ("bitcoin", "crypto", "비트코인", "암호화폐")):
            if pos > 0:
                crypto_boost = True
        if any(kw in lower for kw in ("kospi", "코스피", "코스닥", "원화", "한국 주식")):
            if p > 0:
                korea_risk = True

    # 점수 정규화 → macro_score (0.0 ~ 1.0)
    # 패닉이 높을수록 낮은 점수, 긍정이 높을수록 높은 점수
    # 기준선: 뉴스 없음 = 0.55 (약간 긍정적 바이어스)
    net = total_pos - total_panic
    raw = 0.55 + net * 0.04          # 스케일 조정
    macro_score = max(0.05, min(0.95, round(raw, 3)))

    if macro_score <= 0.30:
        impact = "panic"
    elif macro_score <= 0.45:
        impact = "caution"
    else:
        impact = "calm"

    return {
        "impact":       impact,
        "macro_score":  macro_score,
        "trump_alert":  trump_alert,
        "tariff_alert": tariff_alert,
        "crypto_boost": crypto_boost,
        "korea_risk":   korea_risk,
        "panic_score":  round(total_panic, 2),
        "pos_score":    round(total_pos, 2),
        "keywords":     list(set(all_matched))[:10],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Google News RSS 스캔
# ─────────────────────────────────────────────────────────────────────────────

def _google_news_rss(query: str, lang: str = "en", gl: str = "US") -> list[str]:
    """Google News RSS에서 헤드라인 목록 반환."""
    from urllib.parse import quote_plus
    encoded = quote_plus(query)
    ceid = f"{gl}:{lang}"
    url = (
        f"https://news.google.com/rss/search"
        f"?q={encoded}&hl={lang}-{gl}&gl={gl}&ceid={ceid}"
    )
    resp = _safe_get(url)
    if not resp:
        return []
    items = _parse_rss(resp.text, max_items=25)
    return [item["title"] for item in items]


def _fetch_global_macro_headlines() -> list[str]:
    """전세계 거시경제 영향 뉴스 수집 (영어 기반)."""
    queries = [
        "Trump tariff trade war stock market",
        "Federal Reserve interest rate cut hike",
        "stock market crash rally S&P 500",
        "crypto bitcoin ETF regulation",
        "global recession GDP inflation",
    ]
    headlines: list[str] = []
    for q in queries:
        headlines += _google_news_rss(q, lang="en", gl="US")
    return headlines


def _fetch_korea_macro_headlines() -> list[str]:
    """한국 시장 거시 뉴스 (한국어)."""
    queries = [
        "코스피 코스닥 외국인 기관 주식",
        "원달러 환율 한국 경제",
        "반도체 삼성 하이닉스 수출",
    ]
    headlines: list[str] = []
    for q in queries:
        headlines += _google_news_rss(q, lang="ko", gl="KR")
    return headlines


# ─────────────────────────────────────────────────────────────────────────────
# Nitter RSS (X/Twitter 핵심 계정)
# ─────────────────────────────────────────────────────────────────────────────

def _try_nitter_rss(username: str) -> list[str]:
    """여러 Nitter 인스턴스를 순서대로 시도하여 최신 포스트 반환."""
    for base in _NITTER_INSTANCES:
        url = f"{base}/{username}/rss"
        resp = _safe_get(url, timeout=5)
        if resp and resp.status_code == 200 and "<item>" in resp.text:
            items = _parse_rss(resp.text, max_items=10)
            titles = [item["title"] for item in items]
            if titles:
                return titles
    return []


def _fetch_influencer_posts() -> list[str]:
    """핵심 계정 X 포스트 수집 (Nitter 통해 — best effort).

    실패 시 빈 리스트 반환 (Google News에서 viral 포스트 커버됨).
    """
    posts: list[str] = []
    for username, label, _ in _KEY_ACCOUNTS:
        try:
            items = _try_nitter_rss(username)
            for text in items[:5]:  # 최신 5개
                posts.append(f"[{label}] {text}")
        except Exception:
            continue
    return posts


# ─────────────────────────────────────────────────────────────────────────────
# Naver 금융 종목 뉴스
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_naver_stock_news(ticker: str, max_items: int = 10) -> list[str]:
    """Naver 금융 종목 뉴스 헤드라인 반환."""
    url = (
        f"https://finance.naver.com/item/news_news.nhn"
        f"?code={ticker}&page=1&sm=title_entity_id.basic"
    )
    resp = _safe_get(url)
    if not resp:
        return []
    # 뉴스 제목 추출 — <a class="tit" ...> 또는 <td class="title">
    headlines: list[str] = []
    for m in re.findall(r'<a[^>]+class="tit"[^>]*>(.*?)</a>', resp.text, re.DOTALL):
        clean = re.sub(r"<[^>]+>", "", m).strip()
        if clean:
            headlines.append(unescape(clean))
    if not headlines:
        # 다른 패턴 fallback
        for m in re.findall(r'<td class="title">(.*?)</td>', resp.text, re.DOTALL):
            clean = re.sub(r"<[^>]+>", "", m).strip()
            if clean:
                headlines.append(unescape(clean))
    return headlines[:max_items]


def _google_stock_news(ticker_name: str) -> list[str]:
    """Google News에서 특정 종목 관련 한국 뉴스."""
    from urllib.parse import quote_plus
    q = quote_plus(f"{ticker_name} 주식 호재 악재")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko-KR&gl=KR&ceid=KR:ko"
    resp = _safe_get(url)
    if not resp:
        return []
    items = _parse_rss(resp.text, max_items=10)
    return [item["title"] for item in items]


# ─────────────────────────────────────────────────────────────────────────────
# 공개 API: 시장 전체 뉴스 인텔 (5분 캐시)
# ─────────────────────────────────────────────────────────────────────────────

def get_market_news_intel(force_refresh: bool = False) -> dict:
    """전체 시장 뉴스 인텔리전스 (5분 캐시).

    Returns:
        {
          impact: "calm" | "caution" | "panic",
          macro_score: float,       # 0.0(공황) ~ 1.0(매우 긍정)
          trump_alert: bool,
          tariff_alert: bool,
          crypto_boost: bool,
          korea_risk: bool,
          panic_score: float,
          pos_score: float,
          keywords: list[str],
          breaking: list[str],      # 최근 헤드라인 샘플
          sources: list[str],       # 수집된 소스 목록
          cached_at: str,
          from_cache: bool,
        }
    """
    now = time.time()
    with _lock:
        cached = _market_cache.get("latest")
        if not force_refresh and cached and (now - cached.get("_ts", 0)) < _MARKET_CACHE_TTL:
            return {**cached, "from_cache": True}

    headlines: list[str] = []
    sources: list[str] = []

    # 1. 글로벌 거시 뉴스 (Google News 영어)
    try:
        global_h = _fetch_global_macro_headlines()
        headlines.extend(global_h)
        if global_h:
            sources.append(f"google_global({len(global_h)})")
    except Exception:
        pass

    # 2. 한국 시장 뉴스 (Google News 한국어)
    try:
        korea_h = _fetch_korea_macro_headlines()
        headlines.extend(korea_h)
        if korea_h:
            sources.append(f"google_korea({len(korea_h)})")
    except Exception:
        pass

    # 3. X/Twitter 핵심 계정 (Nitter — best effort)
    try:
        snsl = _fetch_influencer_posts()
        headlines.extend(snsl)
        if snsl:
            sources.append(f"nitter({len(snsl)})")
    except Exception:
        pass

    result = _headlines_to_impact(headlines)
    result["breaking"]   = headlines[:15]  # 최근 헤드라인 15개
    result["sources"]    = sources
    result["cached_at"]  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["from_cache"] = False
    result["_ts"]        = now

    with _lock:
        _market_cache["latest"] = result

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 공개 API: 종목별 뉴스 catalyst 체크 (15분 캐시)
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_catalyst(ticker: str, name: str) -> dict:
    """특정 종목의 뉴스 catalyst 분석 (15분 캐시).

    Returns:
        {
          positive: bool,       # 호재 감지
          negative: bool,       # 악재 감지
          score_delta: float,   # candidate_score 보정값 (-0.20 ~ +0.15)
          headlines: list[str], # 관련 헤드라인
          reason: str,          # 요약 이유
        }
    """
    cache_key = f"{ticker}_{name}"
    now = time.time()
    with _lock:
        cached = _stock_cache.get(cache_key)
        if cached and (now - cached.get("_ts", 0)) < _STOCK_CACHE_TTL:
            return {k: v for k, v in cached.items() if k != "_ts"}

    headlines: list[str] = []

    # Naver 금융 뉴스
    try:
        naver_h = _fetch_naver_stock_news(ticker)
        headlines.extend(naver_h)
    except Exception:
        pass

    # Google News 한국어
    if name:
        try:
            google_h = _google_stock_news(name)
            headlines.extend(google_h)
        except Exception:
            pass

    total_panic = 0.0
    total_pos   = 0.0
    for h in headlines:
        p, pos, _ = _score_text(h)
        total_panic += p
        total_pos   += pos

    # 종목 특화 추가 키워드
    all_text = " ".join(headlines).lower()
    # 강한 호재 키워드
    strong_pos = any(kw in all_text for kw in (
        "수주", "계약", "특허", "어닝서프라이즈", "흑자 전환", "실적 개선",
        "신제품", "승인", "급등", "상한가"
    ))
    # 강한 악재 키워드
    strong_neg = any(kw in all_text for kw in (
        "적자", "하한가", "감사의견", "횡령", "불성실", "상장폐지",
        "소송", "리콜", "파산", "워크아웃"
    ))

    positive = strong_pos or (total_pos >= 2.0 and total_pos > total_panic * 1.5)
    negative = strong_neg or (total_panic >= 2.0 and total_panic > total_pos * 1.5)

    score_delta = 0.0
    reason = "뉴스 없음 또는 중립"

    if negative:
        score_delta = -0.20
        reason = f"악재 감지 (panic={total_panic:.1f})"
    elif positive:
        score_delta = +0.12
        reason = f"호재 감지 (pos={total_pos:.1f})"
    elif total_pos > 0.5:
        score_delta = +0.05
        reason = f"약한 긍정 신호 (pos={total_pos:.1f})"

    result: dict = {
        "positive":    positive,
        "negative":    negative,
        "score_delta": score_delta,
        "headlines":   headlines[:6],
        "reason":      reason,
        "_ts":         now,
    }

    with _lock:
        _stock_cache[cache_key] = result

    return {k: v for k, v in result.items() if k != "_ts"}


# ─────────────────────────────────────────────────────────────────────────────
# 긴급 차단 판단 유틸리티
# ─────────────────────────────────────────────────────────────────────────────

def is_entry_blocked_by_news() -> tuple[bool, str]:
    """뉴스 패닉으로 신규 진입을 차단해야 하면 (True, 이유) 반환.

    예: 트럼프 관세 발표, 연준 서프라이즈, 시장 붕괴 뉴스
    """
    try:
        intel = get_market_news_intel()
        impact = intel.get("impact", "calm")
        if impact == "panic":
            reason = "글로벌 뉴스 패닉 감지"
            kws = intel.get("keywords", [])
            if kws:
                reason += f" ({', '.join(kws[:3])})"
            return True, reason
        if intel.get("tariff_alert") and intel.get("trump_alert"):
            return True, "트럼프 관세/무역 발표 경보 — 신규 진입 일시 중단"
    except Exception:
        pass
    return False, ""

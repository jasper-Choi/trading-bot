"""글로벌 뉴스/SNS 인텔리전스 서비스 — Phase 3.

전세계 정책 입안자·투자 거물·미디어·커뮤니티를 종합 모니터링.
트럼프는 수백 명의 인사이트 원천 중 하나일 뿐이다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  모니터링 채널 (6개 카테고리, 30+ 계정)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  정책/정부    : 트럼프, 백악관, 미재무부, EU집행위, G20, 한국 금융당국
  중앙은행     : Fed, ECB, BOE, IMF, 세계은행
  글로벌 투자자: 버핏(버크셔), 달리오(브릿지워터), 캐시우드(ARK), 세일러
  코인/블록체인: 비탈릭(ETH), 바이낸스, 코인베이스, Pompliano
  미디어 RSS   : Reuters, Bloomberg, WSJ, FT, Economist, CNBC
                한국경제, 연합뉴스, 이데일리, 블록미디어
  커뮤니티 RSS : Reddit r/investing, r/stocks, r/CryptoCurrency, r/Korea

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  출력 (get_market_news_intel):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  impact          : "calm" | "caution" | "panic"
  macro_score     : 0.0(패닉) ~ 1.0(매우 긍정)
  policy_alert    : bool — 정부/정책 시장 언급 감지 (트럼프 포함)
  cb_alert        : bool — 중앙은행 서프라이즈 감지
  tariff_alert    : bool — 관세/무역 뉴스
  crypto_boost    : bool — 코인 긍정 뉴스
  korea_risk      : bool — 한국 시장 직접 리스크
  breaking        : list[str] — 2시간 이내 헤드라인

  출력 (get_stock_catalyst):
  catalyst_score  : 0.0(강한 악재) ~ 1.0(강한 호재)  ← 연속 점수 (Phase 3)
  catalyst_rating : int 0~10 — 사람이 읽기 쉬운 점수
  score_delta     : float — candidate_score 보정값 (-0.20 ~ +0.18)
  positive / negative : bool
  jongto          : dict — 종토방 활동량 + 감성
"""
from __future__ import annotations

import re
import time
import threading
from datetime import datetime, timezone
from html import unescape
from typing import Any
from xml.etree import ElementTree

import requests

# ── 캐시 설정 ─────────────────────────────────────────────────────────────────
_MARKET_CACHE_TTL = 5 * 60    # 5분 (시장 급변 감지용)
_STOCK_CACHE_TTL  = 15 * 60   # 15분 (종목별 뉴스)

_market_cache: dict[str, Any] = {}
_stock_cache:  dict[str, Any] = {}
_lock = threading.Lock()

# ── 요청 설정 ─────────────────────────────────────────────────────────────────
_TIMEOUT = 8
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ── Nitter 인스턴스 목록 ──────────────────────────────────────────────────────
_NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.net",
    "https://nitter.1d4.us",
    "https://nitter.poast.org",
    "https://bird.trom.tf",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 모니터링 대상 — 6개 카테고리
# (username, 레이블, 가중치, 카테고리)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_KEY_ACCOUNTS: list[tuple[str, str, float, str]] = [
    # ── 정책/정부 ──────────────────────────────────────────────────────────
    ("realDonaldTrump", "트럼프",       2.0, "policy"),
    ("POTUS",           "백악관",       1.5, "policy"),
    ("SecBessent",      "미재무장관",   1.8, "policy"),
    ("WhiteHouse",      "백악관공식",   1.4, "policy"),
    ("USTradeRep",      "USTR무역대표", 1.6, "policy"),  # 관세 직접 담당
    ("EU_Commission",   "EU집행위",     1.3, "policy"),
    ("G20org",          "G20",          1.2, "policy"),
    # ── 중앙은행 / 국제기구 ────────────────────────────────────────────────
    ("federalreserve",  "미연준",       2.0, "central_bank"),
    ("ecb",             "유럽중앙은행", 1.8, "central_bank"),
    ("bankofengland",   "영란은행",     1.6, "central_bank"),
    ("IMFNews",         "IMF",          1.5, "central_bank"),
    ("WorldBank",       "세계은행",     1.3, "central_bank"),
    ("BIS_org",         "BIS국제결제",  1.3, "central_bank"),
    # ── 글로벌 투자 거물 ───────────────────────────────────────────────────
    ("elonmusk",        "일론머스크",   1.8, "investor"),  # 테슬라·코인·X
    ("CathieDWood",     "캐시우드",     1.4, "investor"),  # ARK 기술주
    ("RayDalio",        "레이달리오",   1.5, "investor"),  # 브릿지워터
    ("michael_saylor",  "마이클세일러", 1.6, "investor"),  # 비트코인 최대 보유
    ("Naval",           "Naval",        1.2, "investor"),  # 실리콘밸리 영향력
    # ── 코인/블록체인 ──────────────────────────────────────────────────────
    ("VitalikButerin",  "비탈릭",       1.7, "crypto"),   # 이더리움 창시자
    ("APompliano",      "Pompliano",    1.4, "crypto"),   # 코인 인플루언서
    ("binance",         "바이낸스",     1.5, "crypto"),   # 최대 거래소
    ("coinbase",        "코인베이스",   1.4, "crypto"),   # 미국 상장 거래소
    ("cz_binance",      "CZ",           1.4, "crypto"),   # 바이낸스 창업자
    # ── 금융 미디어 ────────────────────────────────────────────────────────
    ("Reuters",         "로이터",       1.3, "media"),
    ("Bloomberg",       "블룸버그",     1.3, "media"),
    ("WSJ",             "WSJ",          1.3, "media"),
    ("FT",              "파이낸셜타임즈", 1.2, "media"),
    ("TheEconomist",    "이코노미스트", 1.1, "media"),
    ("CNBC",            "CNBC",         1.1, "media"),
    # ── 한국 관련 ──────────────────────────────────────────────────────────
    ("koreaherald",     "코리아헤럴드", 1.2, "korea"),
    ("yonhap_news",     "연합뉴스",     1.2, "korea"),
]

# ── RSS 뉴스 소스 목록 ─────────────────────────────────────────────────────────
# (url, 레이블, 가중치)
_RSS_SOURCES: list[tuple[str, str, float]] = [
    # 한국 금융 뉴스
    ("https://www.hankyung.com/rss/finance.xml",
     "한국경제", 1.3),
    ("https://www.yonhapnews.co.kr/rss/economy.xml",
     "연합뉴스경제", 1.2),
    ("https://rss.edaily.co.kr/economy.xml",
     "이데일리경제", 1.2),
    ("https://www.blockmedia.co.kr/feed/",
     "블록미디어(코인)", 1.3),
    # 글로벌 투자 커뮤니티 (Reddit Atom)
    ("https://www.reddit.com/r/investing/.rss?limit=20",
     "Reddit/investing", 1.1),
    ("https://www.reddit.com/r/stocks/.rss?limit=20",
     "Reddit/stocks", 1.0),
    ("https://www.reddit.com/r/CryptoCurrency/.rss?limit=20",
     "Reddit/crypto", 1.2),
    ("https://www.reddit.com/r/Korea/.rss?limit=15",
     "Reddit/Korea", 1.0),
    # 영어 금융 뉴스 RSS
    ("https://feeds.reuters.com/reuters/businessNews",
     "Reuters business", 1.3),
    ("https://feeds.reuters.com/reuters/financialsNews",
     "Reuters finance", 1.3),
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 감성 키워드 사전 — (키워드, 가중치)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_PANIC_KW: list[tuple[str, float]] = [
    # 거시 쇼크
    ("tariff", 1.5), ("trade war", 2.0), ("trade ban", 1.8),
    ("관세", 1.5), ("무역전쟁", 2.0), ("수출금지", 1.8),
    ("sanction", 1.5), ("제재", 1.5), ("embargo", 1.8),
    # 금융 위기
    ("crash", 2.0), ("circuit breaker", 2.5), ("market meltdown", 2.5),
    ("급락", 1.5), ("폭락", 2.0), ("서킷브레이커", 2.5),
    ("black monday", 3.0), ("flash crash", 2.5), ("bank run", 2.5),
    ("default", 2.0), ("디폴트", 2.0), ("파산", 1.8),
    # 금리/통화 충격
    ("emergency rate hike", 2.5), ("surprise hike", 2.0),
    ("긴급 금리", 2.5), ("금리 충격", 2.0), ("급격한 금리", 1.8),
    ("currency crisis", 2.5), ("환율 위기", 2.5),
    # 지정학/전쟁
    ("war", 1.0), ("invasion", 1.8), ("nuclear", 2.0),
    ("전쟁", 1.0), ("침공", 1.8), ("핵", 2.0),
    ("conflict escalation", 1.8), ("military strike", 2.0),
    # 기업/시장 악재
    ("recession", 1.8), ("경기침체", 1.8), ("stagflation", 2.0),
    ("yield inversion", 1.5), ("장단기금리역전", 1.5),
    # 한국 직접 리스크
    ("코스피 급락", 2.0), ("외국인 매도", 1.5),
    ("원화 급락", 2.0), ("korea default", 3.0),
]

_POSITIVE_KW: list[tuple[str, float]] = [
    # 거시 호재
    ("rate cut", 2.0), ("금리 인하", 2.0), ("pivot", 1.5), ("금리 피벗", 1.5),
    ("trade deal", 2.0), ("무역 합의", 2.0), ("관세 완화", 2.0),
    ("tariff reduction", 2.0), ("tariff relief", 2.0),
    ("ceasefire", 2.0), ("평화협정", 2.0),
    # 주식 상승
    ("bull market", 1.5), ("rally", 1.0), ("record high", 1.5), ("all-time high", 1.5),
    ("상승장", 1.5), ("강세장", 1.5), ("신고가", 1.0), ("역대최고", 1.5),
    # 코인 호재
    ("bitcoin etf", 1.5), ("crypto regulation clarity", 1.5),
    ("crypto reserve", 1.8),  # 국가 코인 비축
    ("bitcoin strategic reserve", 2.0),
    # 한국 호재
    ("수주", 1.0), ("실적 개선", 1.5), ("어닝 서프라이즈", 1.5),
    ("흑자 전환", 1.5), ("외국인 매수", 1.0), ("기관 순매수", 1.0),
    # Fed/중앙은행 완화
    ("dovish", 1.8), ("quantitative easing", 1.5), ("양적완화", 1.5),
    ("stimulus", 1.5), ("경기부양", 1.5),
    # 기업 호재
    ("earnings beat", 1.5), ("guidance raised", 1.5), ("buyback", 1.0),
    ("dividend increase", 1.0), ("merger", 1.0),
]

# 알림 감지 키워드 (카테고리별)
_POLICY_KW    = ["trump", "biden", "potus", "white house", "재무부", "관세청",
                  "트럼프", "백악관", "tariff executive order", "executive order"]
_CB_KW        = ["fed rate", "fomc", "ecb rate", "boe rate", "rate decision",
                  "interest rate decision", "금리 결정", "기준금리", "연준 결정"]
_TARIFF_KW    = ["tariff", "관세", "trade war", "무역전쟁", "import duty", "수입관세"]
_CRYPTO_POS_KW = ["bitcoin strategic reserve", "crypto etf approved", "bitcoin etf",
                   "crypto deregulation", "비트코인 ETF", "코인 규제 완화"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _safe_get(url: str, timeout: int = _TIMEOUT) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def _parse_rss(xml_text: str, max_items: int = 20) -> list[dict]:
    """RSS / Atom XML → [{title, link, published}]."""
    items: list[dict] = []
    try:
        root = ElementTree.fromstring(xml_text)
        # 네임스페이스 처리 (Atom: {http://www.w3.org/2005/Atom})
        ns_map = {
            "atom": "http://www.w3.org/2005/Atom",
            "dc":   "http://purl.org/dc/elements/1.1/",
        }
        # RSS 2.0
        channel = root.find("channel")
        if channel is not None:
            for item in channel.findall("item")[:max_items]:
                t = item.findtext("title", "") or ""
                p = item.findtext("pubDate", "") or item.findtext("dc:date", "", ns_map)
                if t.strip():
                    items.append({"title": unescape(t.strip()), "published": p.strip()})
            return items
        # Atom
        atom_ns = "http://www.w3.org/2005/Atom"
        for entry in root.findall(f"{{{atom_ns}}}entry")[:max_items]:
            t = entry.findtext(f"{{{atom_ns}}}title", "") or ""
            p = entry.findtext(f"{{{atom_ns}}}published", "") or \
                entry.findtext(f"{{{atom_ns}}}updated", "")
            if t.strip():
                items.append({"title": unescape(re.sub(r"<[^>]+>", "", t).strip()), "published": p.strip()})
    except Exception:
        pass
    return items


def _parse_pubdate(pub_str: str) -> datetime | None:
    if not pub_str:
        return None
    fmts = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(pub_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _recency_weight(pub_dt: datetime | None) -> float:
    """발행 시각 가중치: <2hr=1.0 / 2~6hr=0.4 / 6~24hr=0.1 / >24hr=0.0"""
    if pub_dt is None:
        return 0.15
    age_hrs = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
    if age_hrs < 2:
        return 1.0
    if age_hrs < 6:
        return 0.4
    if age_hrs < 24:
        return 0.1
    return 0.0


def _score_text(text: str) -> tuple[float, float, list[str]]:
    """텍스트 → (panic_raw, positive_raw, matched_kw). 기사당 최대 cap(4.0) 적용."""
    lower = text.lower()
    panic = 0.0
    pos   = 0.0
    matched: list[str] = []
    for kw, w in _PANIC_KW:
        if kw in lower:
            panic += w
            matched.append(f"-{kw}")
    for kw, w in _POSITIVE_KW:
        if kw in lower:
            pos += w
            matched.append(f"+{kw}")
    return min(panic, 4.0), min(pos, 4.0), matched


def _items_to_impact(items: list[dict]) -> dict:
    """뉴스 아이템 목록 → 시간 가중 market impact dict."""
    w_panic = 0.0
    w_pos   = 0.0
    all_kw: list[str] = []
    policy_alert = False
    cb_alert     = False
    tariff_alert = False
    crypto_boost = False
    korea_risk   = False
    breaking: list[str] = []

    for item in items:
        title  = str(item.get("title", "") or "")
        pub_dt = _parse_pubdate(str(item.get("published", "") or ""))
        src_w  = float(item.get("weight", 1.0) or 1.0)
        r_w    = _recency_weight(pub_dt)
        eff_w  = r_w * src_w
        if eff_w <= 0.0:
            continue

        p, pos, kws = _score_text(title)
        w_panic += p * eff_w
        w_pos   += pos * eff_w
        all_kw.extend(kws)

        # 알림 플래그 — 6시간 이내만
        if r_w >= 0.4:
            lower = title.lower()
            if any(kw in lower for kw in _POLICY_KW):
                policy_alert = True
            if any(kw in lower for kw in _CB_KW):
                cb_alert = True
            if any(kw in lower for kw in _TARIFF_KW):
                tariff_alert = True
            if any(kw in lower for kw in _CRYPTO_POS_KW):
                crypto_boost = True
            if any(kw in lower for kw in ("kospi", "코스피", "코스닥", "원화", "한국 주식")):
                if p > 0:
                    korea_risk = True

        if r_w >= 1.0:
            breaking.append(title)

    net = w_pos - w_panic
    raw = 0.55 + net * 0.03
    macro_score = max(0.15, min(0.95, round(raw, 3)))

    # 정책+관세 동시 경보(6시간 이내) → caution 보정
    if policy_alert and tariff_alert and macro_score > 0.38:
        macro_score = min(macro_score, 0.38)

    impact = "panic" if macro_score <= 0.30 else "caution" if macro_score <= 0.45 else "calm"

    return {
        "impact":        impact,
        "macro_score":   macro_score,
        "policy_alert":  policy_alert,   # 정책/정부 (트럼프 포함)
        "cb_alert":      cb_alert,       # 중앙은행 서프라이즈
        "tariff_alert":  tariff_alert,
        "crypto_boost":  crypto_boost,
        "korea_risk":    korea_risk,
        "panic_score":   round(w_panic, 2),
        "pos_score":     round(w_pos, 2),
        "keywords":      list(set(all_kw))[:12],
        "breaking_count": len(breaking),
        "breaking":      breaking[:15],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 뉴스 수집 — Google News RSS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _google_news_rss(query: str, lang: str = "en", gl: str = "US",
                     src_weight: float = 1.0, max_items: int = 12) -> list[dict]:
    from urllib.parse import quote_plus
    url = (f"https://news.google.com/rss/search"
           f"?q={quote_plus(query)}&hl={lang}-{gl}&gl={gl}&ceid={gl}:{lang}")
    resp = _safe_get(url)
    if not resp:
        return []
    raw = _parse_rss(resp.text, max_items=max_items)
    return [{"title": r["title"], "published": r["published"], "weight": src_weight}
            for r in raw]


def _fetch_global_headlines() -> list[dict]:
    """전세계 거시 뉴스 — 6개 주제 쿼리."""
    queries_en = [
        ("Federal Reserve interest rate decision FOMC",    1.5),
        ("ECB BOE central bank rate decision",             1.4),
        ("tariff trade war trade deal US China",           1.4),
        ("stock market crash rally S&P 500 Nasdaq",        1.2),
        ("crypto bitcoin ETF regulation institutional",    1.2),
        ("global recession inflation GDP jobs report",     1.1),
        ("geopolitical risk war sanctions nuclear",        1.2),
        ("earnings beat miss guidance Wall Street",        1.0),
    ]
    items: list[dict] = []
    for q, w in queries_en:
        items += _google_news_rss(q, lang="en", gl="US", src_weight=w)
    return items


def _fetch_korea_headlines() -> list[dict]:
    """한국 시장 Google News."""
    queries_ko = [
        ("코스피 코스닥 외국인 기관 매수 매도",  1.3),
        ("원달러 환율 한국 경제",               1.2),
        ("반도체 삼성 SK하이닉스 수출",          1.2),
        ("한국 금리 기준금리 한국은행",          1.3),
        ("코인 비트코인 이더리움 한국",          1.1),
    ]
    items: list[dict] = []
    for q, w in queries_ko:
        items += _google_news_rss(q, lang="ko", gl="KR", src_weight=w)
    return items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 뉴스 수집 — 직접 RSS 소스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_rss_sources() -> list[dict]:
    """한국 미디어 + Reddit + Reuters 직접 RSS 수집."""
    items: list[dict] = []
    for url, label, w in _RSS_SOURCES:
        try:
            resp = _safe_get(url, timeout=6)
            if not resp:
                continue
            raw = _parse_rss(resp.text, max_items=15)
            for r in raw:
                items.append({"title": f"[{label}] {r['title']}",
                               "published": r["published"], "weight": w})
        except Exception:
            continue
    return items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# X/Twitter — Nitter RSS (best-effort, 가중치 높음)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _try_nitter(username: str) -> list[dict]:
    for base in _NITTER_INSTANCES:
        resp = _safe_get(f"{base}/{username}/rss", timeout=5)
        if resp and resp.status_code == 200 and "<item>" in resp.text:
            raw = _parse_rss(resp.text, max_items=8)
            if raw:
                return raw
    return []


def _fetch_influencer_posts() -> list[dict]:
    """30+ 계정 X 포스트 (Nitter — best effort). 계정별 가중치 적용."""
    posts: list[dict] = []
    for username, label, w, cat in _KEY_ACCOUNTS:
        try:
            raw = _try_nitter(username)
            for r in raw[:4]:
                posts.append({
                    "title":     f"[{cat}/{label}] {r['title']}",
                    "published": r.get("published", ""),
                    "weight":    w,
                })
        except Exception:
            continue
    return posts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Naver 종목토론방 (종토방) 스캔
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_jongto(ticker: str, max_posts: int = 30) -> dict:
    """Naver 종목토론방 최근 활동량 + 감성 분석.

    Returns:
        {
          post_count: int,        # 최근 페이지 글 수 (활동량 지표)
          positive_count: int,    # 긍정 글 수
          negative_count: int,    # 부정 글 수
          hot: bool,              # 비정상적 활동량 (평소 대비 급증)
          sentiment_score: float, # 0.0(부정) ~ 1.0(긍정)
          titles: list[str],      # 글 제목 샘플
        }
    """
    url = f"https://finance.naver.com/item/board.nhn?code={ticker}&page=1"
    resp = _safe_get(url)
    if not resp:
        return {"post_count": 0, "positive_count": 0, "negative_count": 0,
                "hot": False, "sentiment_score": 0.5, "titles": []}

    # 글 제목 추출
    titles: list[str] = []
    for m in re.findall(r'<a[^>]+title="([^"]{5,80})"[^>]*>', resp.text):
        clean = m.strip()
        if clean and clean not in titles:
            titles.append(clean)

    # 대안 패턴
    if not titles:
        for m in re.findall(r'class="title"[^>]*>\s*<a[^>]*>([^<]{5,80})</a>', resp.text):
            titles.append(m.strip())

    titles = titles[:max_posts]
    total = len(titles)

    pos_count = 0
    neg_count = 0
    _pos_kw = ("급등", "상한가", "호재", "매수", "올라", "돌파", "수익", "목표가", "추천")
    _neg_kw = ("급락", "하한가", "악재", "매도", "떨어", "손절", "실망", "사기", "주의")

    for t in titles:
        lower = t.lower()
        if any(k in lower for k in _pos_kw):
            pos_count += 1
        if any(k in lower for k in _neg_kw):
            neg_count += 1

    neutral = total - pos_count - neg_count
    denom = pos_count + neg_count
    sentiment_score = 0.5
    if denom > 0:
        sentiment_score = round(pos_count / denom * 0.85 + 0.075, 3)

    # hot: 최근 활동량이 많으면 (임의 기준: 20글 이상 = 관심 집중)
    hot = total >= 20

    return {
        "post_count":      total,
        "positive_count":  pos_count,
        "negative_count":  neg_count,
        "hot":             hot,
        "sentiment_score": sentiment_score,
        "titles":          titles[:6],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Naver 종목 뉴스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_naver_stock_news(ticker: str, max_items: int = 12) -> list[dict]:
    url = (f"https://finance.naver.com/item/news_news.nhn"
           f"?code={ticker}&page=1&sm=title_entity_id.basic")
    resp = _safe_get(url)
    if not resp:
        return []
    headlines: list[str] = []
    for m in re.findall(r'<a[^>]+class="tit"[^>]*>(.*?)</a>', resp.text, re.DOTALL):
        clean = re.sub(r"<[^>]+>", "", m).strip()
        if clean:
            headlines.append(unescape(clean))
    if not headlines:
        for m in re.findall(r'<td class="title">(.*?)</td>', resp.text, re.DOTALL):
            clean = re.sub(r"<[^>]+>", "", m).strip()
            if clean:
                headlines.append(unescape(clean))
    # pubDate 없음 → 오늘 날짜로 추정 (최신 기사 가정)
    now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return [{"title": h, "published": now_str, "weight": 1.3}
            for h in headlines[:max_items]]


def _google_stock_news(name: str) -> list[dict]:
    from urllib.parse import quote_plus
    url = (f"https://news.google.com/rss/search"
           f"?q={quote_plus(name + ' 주식 호재 악재')}&hl=ko-KR&gl=KR&ceid=KR:ko")
    resp = _safe_get(url)
    if not resp:
        return []
    raw = _parse_rss(resp.text, max_items=10)
    return [{"title": r["title"], "published": r["published"], "weight": 1.2}
            for r in raw]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공개 API 1: 시장 전체 뉴스 인텔 (5분 캐시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_market_news_intel(force_refresh: bool = False) -> dict:
    """전체 시장 뉴스 인텔리전스 (5분 캐시).

    하위 호환성: trump_alert = policy_alert (정책/정부 알림으로 통합됨)
    """
    now = time.time()
    with _lock:
        cached = _market_cache.get("latest")
        if not force_refresh and cached and (now - cached.get("_ts", 0)) < _MARKET_CACHE_TTL:
            return {**cached, "from_cache": True}

    all_items: list[dict] = []
    sources: list[str] = []

    # 1. Google News 글로벌 거시
    try:
        g_global = _fetch_global_headlines()
        all_items.extend(g_global)
        if g_global:
            sources.append(f"google_global({len(g_global)})")
    except Exception:
        pass

    # 2. Google News 한국
    try:
        g_korea = _fetch_korea_headlines()
        all_items.extend(g_korea)
        if g_korea:
            sources.append(f"google_korea({len(g_korea)})")
    except Exception:
        pass

    # 3. 직접 RSS (한국 미디어 + Reddit + Reuters)
    try:
        rss = _fetch_rss_sources()
        all_items.extend(rss)
        if rss:
            sources.append(f"rss_direct({len(rss)})")
    except Exception:
        pass

    # 4. X/Twitter Nitter (best effort, 가중치 높음)
    try:
        sns = _fetch_influencer_posts()
        all_items.extend(sns)
        if sns:
            sources.append(f"nitter({len(sns)})")
    except Exception:
        pass

    result = _items_to_impact(all_items)

    # 하위 호환성 필드 (orchestrator 등이 trump_alert 사용)
    result["trump_alert"] = result.get("policy_alert", False)

    # 브레이킹: 2시간 이내 헤드라인
    result["sources"]    = sources
    result["cached_at"]  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["from_cache"] = False
    result["_ts"]        = now

    # breaking 재계산 (이미 _items_to_impact에서 처리됨)
    if not result.get("breaking"):
        result["breaking"] = [item["title"] for item in all_items[:10]]

    with _lock:
        _market_cache["latest"] = result

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공개 API 2: 종목별 catalyst 레이팅 (Phase 3 — 연속 점수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_stock_catalyst(ticker: str, name: str) -> dict:
    """종목별 뉴스 catalyst 분석 — 0~10점 연속 레이팅 (Phase 3).

    Phase 2 대비 개선:
      - binary(positive/negative) → catalyst_score: float(0~1) + catalyst_rating: int(0~10)
      - 종토방(jongto) 활동량 + 감성 통합
      - 뉴스 출처별 가중치 반영

    Returns:
        {
          catalyst_score  : float  # 0.0(강한 악재) ~ 1.0(강한 호재)
          catalyst_rating : int    # 0~10 사람이 읽기 좋은 점수
          score_delta     : float  # candidate_score 보정값 (-0.20 ~ +0.18)
          positive        : bool   # 호재 (catalyst_score >= 0.65)
          negative        : bool   # 악재 (catalyst_score <= 0.35)
          headlines       : list[str]
          reason          : str
          jongto          : dict   # 종토방 데이터
        }
    """
    cache_key = f"{ticker}_{name}"
    now = time.time()
    with _lock:
        cached = _stock_cache.get(cache_key)
        if cached and (now - cached.get("_ts", 0)) < _STOCK_CACHE_TTL:
            return {k: v for k, v in cached.items() if k != "_ts"}

    items: list[dict] = []

    # Naver 금융 뉴스
    try:
        items.extend(_fetch_naver_stock_news(ticker))
    except Exception:
        pass

    # Google News 한국어
    if name:
        try:
            items.extend(_google_stock_news(name))
        except Exception:
            pass

    # 종토방 스캔
    jongto: dict = {}
    try:
        jongto = _fetch_jongto(ticker)
    except Exception:
        jongto = {}

    # ── 뉴스 텍스트 감성 분석 ────────────────────────────────────────────
    total_panic = 0.0
    total_pos   = 0.0
    all_text    = ""
    for item in items:
        t = str(item.get("title", "") or "")
        all_text += " " + t.lower()
        p, pos, _ = _score_text(t)
        r_w = _recency_weight(_parse_pubdate(str(item.get("published", "") or "")))
        w   = float(item.get("weight", 1.0) or 1.0)
        total_panic += p * r_w * w
        total_pos   += pos * r_w * w

    # ── 종목 전용 키워드 ─────────────────────────────────────────────────
    strong_pos_kw = ("수주", "계약", "특허", "어닝서프라이즈", "흑자 전환", "실적 개선",
                     "신제품 승인", "급등", "상한가", "공매도 금지", "외국인 순매수",
                     "earnings beat", "guidance raised", "new contract", "patent")
    strong_neg_kw = ("적자", "하한가", "감사의견", "횡령", "불성실", "상장폐지",
                     "소송", "리콜", "파산", "워크아웃", "주가 조작", "공시 위반",
                     "earnings miss", "guidance cut", "default", "delisting")

    strong_pos = any(kw in all_text for kw in strong_pos_kw)
    strong_neg = any(kw in all_text for kw in strong_neg_kw)

    # ── 종토방 감성 통합 ─────────────────────────────────────────────────
    jongto_score = float(jongto.get("sentiment_score", 0.5) or 0.5)
    jongto_hot   = bool(jongto.get("hot", False))

    # ── catalyst_score 계산 (0.0 ~ 1.0) ────────────────────────────────
    # 기준선: 0.50 (중립)
    # 뉴스 영향: net = pos - panic, 스케일 0.06
    # 종토방 영향: (jongto_score - 0.5) × 0.10
    net = total_pos - total_panic
    raw = 0.50 + net * 0.06 + (jongto_score - 0.5) * 0.10

    if strong_pos:
        raw = max(raw, 0.70)   # 강한 호재 → 최소 0.70 보장
    if strong_neg:
        raw = min(raw, 0.25)   # 강한 악재 → 최대 0.25 제한
    if jongto_hot and jongto_score > 0.6:
        raw += 0.05            # 종토방 뜨겁고 긍정적 → 추가 부스트

    catalyst_score  = max(0.0, min(1.0, round(raw, 3)))
    catalyst_rating = round(catalyst_score * 10)

    # ── score_delta: candidate_score 보정값 ──────────────────────────────
    # 0~2점: -0.20 (강한 악재)
    # 3점:   -0.10 (약한 악재)
    # 4~6점:  0.00 (중립)
    # 7점:   +0.08 (약한 호재)
    # 8~9점: +0.12 (호재)
    # 10점:  +0.18 (강한 호재)
    if catalyst_rating <= 2:
        score_delta = -0.20
    elif catalyst_rating == 3:
        score_delta = -0.10
    elif catalyst_rating <= 6:
        score_delta = 0.00
    elif catalyst_rating == 7:
        score_delta = +0.08
    elif catalyst_rating <= 9:
        score_delta = +0.12
    else:
        score_delta = +0.18

    positive = catalyst_score >= 0.65
    negative = catalyst_score <= 0.35

    # ── reason 문자열 ──────────────────────────────────────────────────
    parts = [f"catalyst {catalyst_rating}/10 (score={catalyst_score:.2f})"]
    if strong_pos:
        parts.append("강한 호재 키워드 감지")
    if strong_neg:
        parts.append("강한 악재 키워드 감지")
    if jongto.get("post_count", 0) > 0:
        parts.append(f"종토방 {jongto['post_count']}글 "
                     f"(긍:{jongto.get('positive_count',0)} 부:{jongto.get('negative_count',0)})"
                     f"{'🔥' if jongto_hot else ''}")
    reason = " | ".join(parts)

    result = {
        "catalyst_score":  catalyst_score,
        "catalyst_rating": catalyst_rating,
        "score_delta":     score_delta,
        "positive":        positive,
        "negative":        negative,
        "headlines":       [i["title"] for i in items[:6]],
        "reason":          reason,
        "jongto":          jongto,
        "_ts":             now,
    }

    with _lock:
        _stock_cache[cache_key] = result

    return {k: v for k, v in result.items() if k != "_ts"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공개 API 3: 긴급 진입 차단 판단
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def is_entry_blocked_by_news() -> tuple[bool, str]:
    """뉴스 패닉으로 신규 진입 차단 여부 반환.

    트리거 조건:
      1. impact == "panic" (macro_score <= 0.30)
      2. policy_alert + tariff_alert 동시 (정책자 관세 언급)
      3. cb_alert + macro_score < 0.40 (중앙은행 서프라이즈)
    """
    try:
        intel = get_market_news_intel()
        impact = intel.get("impact", "calm")
        if impact == "panic":
            kws = intel.get("keywords", [])
            return True, f"글로벌 뉴스 패닉 ({', '.join(kws[:3])})"
        if intel.get("policy_alert") and intel.get("tariff_alert"):
            return True, "정책 입안자 관세/무역 발표 경보 — 신규 진입 일시 중단"
        if intel.get("cb_alert") and float(intel.get("macro_score", 0.5)) < 0.40:
            return True, f"중앙은행 서프라이즈 경보 (score={intel.get('macro_score'):.2f})"
    except Exception:
        pass
    return False, ""

"""매크로 이벤트 → 한국 종목 섹터 매핑 서비스.

트럼프 발언, 머스크 발언, US 시장 밤사이 급등 등의 매크로 신호를
구체적인 한국 종목 코드로 변환하는 매핑 레이어.

예:
  NASDAQ 밤사이 +2%  →  AI/기술주 (네이버, 카카오) uplift
  SOX 반도체 +3%     →  삼성전자, SK하이닉스 uplift
  트럼프 AI 언급     →  NAVER, 카카오 flagged
  머스크 배터리 언급 →  에코프로, LG에너지, 삼성SDI flagged
"""
from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 섹터 → 종목코드 매핑
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTOR_TICKERS: dict[str, list[str]] = {
    # AI / 인터넷 / 플랫폼
    "ai_tech": ["035420", "035720", "259960", "293490"],
    # 035420=네이버, 035720=카카오, 259960=크래프톤, 293490=카카오게임즈

    # 반도체 대형주
    "semiconductor": ["005930", "000660", "091160"],
    # 005930=삼성전자, 000660=SK하이닉스, 091160=KODEX반도체

    # 반도체 장비 / 소재 (2026-06-04 추가 — 섹터 촉매 wave 포착)
    "semiconductor_equip": [
        "084370",  # 유진테크
        "240810",  # 원익IPS
        "095610",  # 테스
        "036930",  # 주성엔지니어링
        "319660",  # 피에스케이
        "031980",  # 피에스케이홀딩스
        "089970",  # 브이엠
        "064760",  # 티씨케이
        "403870",  # HPSP
        "058470",  # 리노공업
        "131290",  # 티에스이
        "420770",  # 기가비스
        "005290",  # 동진쎄미켐
        "039030",  # 이오테크닉스
        "140860",  # 파크시스템스
        "089030",  # 테크윙
        "067310",  # 하나마이크론
        "166090",  # 하나머티리얼즈
        "440110",  # 파두
    ],

    # 2차전지 / EV / 배터리
    "battery_ev": ["373220", "006400", "086520", "003670", "096770"],
    # 373220=LG에너지, 006400=삼성SDI, 086520=에코프로, 003670=포스코퓨처엠, 096770=SK이노베이션

    # 자동차
    "auto": ["005380", "000270", "012330"],
    # 005380=현대차, 000270=기아, 012330=현대모비스

    # 바이오 / 헬스케어
    "bio_pharma": ["207940", "068270", "141080", "196170", "214150"],
    # 207940=삼성바이오, 068270=셀트리온, 141080=리가켐바이오, 196170=알테오젠, 214150=클래시스

    # 금융
    "financial": ["105560", "055550", "086790", "032830"],
    # 105560=KB금융, 055550=신한지주, 086790=하나금융, 032830=삼성생명

    # 철강 / 소재
    "materials": ["005490", "003670"],
    # 005490=POSCO홀딩스, 003670=포스코퓨처엠

    # 방산 / 우주
    "defense": ["012450", "047810", "272210"],
    # 012450=한화에어로, 047810=한국항공우주, 272210=한화시스템

    # 로봇 / 자동화
    "robot": ["108490", "090460", "000150", "064350"],
    # 108490=로보티즈, 090460=나인봇, 000150=두산, 064350=현대로템

    # 화장품 / K-뷰티
    "cosmetics": ["090430", "051900", "123890", "214150"],
    # 090430=아모레퍼시픽, 051900=LG생활건강, 123890=한국콜마, 214150=클래시스
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 키워드 → 섹터 매핑 (트럼프/머스크 발언, 뉴스 헤드라인 파싱용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KEYWORD_SECTOR: list[tuple[list[str], str]] = [
    # AI / 기술
    (["artificial intelligence", "ai ", " ai,", "chatgpt", "llm", "generative ai",
      "인공지능", "ai반도체", "gpu", "openai", "anthropic", "gemini", "grok"],
     "ai_tech"),

    # 반도체 / 칩
    (["semiconductor", "chip", "chips act", "tsmc", "nvidia", "hbm", "nand", "dram",
      "반도체", "칩", "메모리", "파운드리"],
     "semiconductor"),

    # 반도체 장비
    (["semiconductor equipment", "fab", "etch", "deposition", "cvd", "pvd",
      "반도체 장비", "식각", "증착", "노광", "세정", "삼성 투자", "sk 투자", "팹 투자"],
     "semiconductor_equip"),

    # 로봇
    (["robot", "automation", "humanoid", "로봇", "자동화", "휴머노이드"],
     "robot"),

    # 배터리 / EV
    (["battery", "electric vehicle", "ev ", "tesla", "lithium", "cathode",
      "배터리", "전기차", "양극재", "리튬", "2차전지"],
     "battery_ev"),

    # 자동차
    (["automobile", "auto tariff", "car tariff", "vehicle", "hyundai", "kia",
      "자동차", "관세", "현대차", "기아"],
     "auto"),

    # 바이오
    (["fda", "drug approval", "clinical trial", "biotech", "pharma",
      "fda 승인", "임상", "바이오", "제약"],
     "bio_pharma"),

    # 방산
    (["defense", "military", "nato", "weapons", "drone", "missile",
      "방산", "군사", "무기", "드론"],
     "defense"),
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# US 지수 → 섹터 매핑 (밤사이 급등/급락 연동)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# us_ctx 내 섹터 데이터 키 → 한국 섹터
US_SECTOR_KOREA_MAP: dict[str, str] = {
    "Technology":      "ai_tech",
    "Semiconductors":  "semiconductor",
    "Consumer Discr.": "auto",
    "Health Care":     "bio_pharma",
    "Financials":      "financial",
    "Materials":       "materials",
}


def get_boosted_tickers(
    news_intel: dict,
    us_ctx: dict,
    us_overnight_threshold: float = 1.2,
) -> dict[str, float]:
    """매크로 신호로부터 상승 예상 종목 코드 → 부스트 점수 반환.

    Args:
        news_intel: get_market_news_intel() 결과
        us_ctx:     get_us_market_context() 결과
        us_overnight_threshold: US 지수 변화율 임계치 (%)

    Returns:
        {ticker: boost_score} — boost_score 0.0~1.0
        0.0 = 영향 없음, 1.0 = 매우 강한 매크로 호재
    """
    boosted: dict[str, float] = {}

    # ── 1. 글로벌 뉴스/인플루언서 키워드 파싱 ───────────────────────────
    # breaking 헤드라인 + policy/alert 텍스트 수집
    all_text = " ".join([
        " ".join(news_intel.get("breaking", []) or []),
        str(news_intel.get("summary", "") or ""),
    ]).lower()

    for keywords, sector in KEYWORD_SECTOR:
        if any(kw in all_text for kw in keywords):
            for ticker in SECTOR_TICKERS.get(sector, []):
                boosted[ticker] = max(boosted.get(ticker, 0.0), 0.5)

    # ── 2. 트럼프/머스크 특별 가중치 ────────────────────────────────────
    trump_alert = bool(news_intel.get("trump_alert", False))
    if trump_alert:
        # 트럼프 발언이 있으면 관세 영향권 (자동차, 철강) 경계 + AI 정책 주목
        for kw in ["tariff", "trade", "관세"]:
            if kw in all_text:
                for ticker in SECTOR_TICKERS.get("auto", []):
                    boosted[ticker] = max(boosted.get(ticker, 0.0), 0.3)  # 경계 (하방 위험)

        for kw in ["ai", "technology", "chip", "semiconductor"]:
            if kw in all_text:
                for ticker in SECTOR_TICKERS.get("ai_tech", []) + SECTOR_TICKERS.get("semiconductor", []):
                    boosted[ticker] = max(boosted.get(ticker, 0.0), 0.6)  # 호재

    # ── 3. US 밤사이 지수 급등 → 섹터 연동 ──────────────────────────────
    qqq_chg = float(us_ctx.get("qqq_chg", 0.0) or 0.0)
    spy_chg = float(us_ctx.get("spy_chg", 0.0) or 0.0)

    if qqq_chg >= us_overnight_threshold:
        # NASDAQ 급등 → AI/기술주 부스트
        boost = min(1.0, 0.5 + qqq_chg * 0.08)
        for ticker in SECTOR_TICKERS["ai_tech"] + SECTOR_TICKERS["semiconductor"]:
            boosted[ticker] = max(boosted.get(ticker, 0.0), boost)

    if spy_chg >= us_overnight_threshold * 0.8:
        # S&P 전반 상승 → 전 섹터 소폭 부스트
        for ticker_list in SECTOR_TICKERS.values():
            for ticker in ticker_list:
                boosted[ticker] = max(boosted.get(ticker, 0.0), 0.3)

    # ── 4. US 섹터별 등락 → 한국 섹터 연동 ─────────────────────────────
    sectors = us_ctx.get("sectors", {}) or {}
    for us_sector_name, korea_sector in US_SECTOR_KOREA_MAP.items():
        chg = float(sectors.get(us_sector_name, 0.0) or 0.0)
        if chg >= us_overnight_threshold:
            boost = min(1.0, 0.45 + chg * 0.06)
            for ticker in SECTOR_TICKERS.get(korea_sector, []):
                boosted[ticker] = max(boosted.get(ticker, 0.0), boost)

    # ── 5. 코인 crypto_boost → 리스크온 → 기술주 간접 부스트 ────────────
    crypto_boost = bool(news_intel.get("crypto_boost", False))
    if crypto_boost:
        for ticker in SECTOR_TICKERS["ai_tech"]:
            boosted[ticker] = max(boosted.get(ticker, 0.0), 0.35)

    return boosted


# ── 역방향 매핑: ticker → sector ───────────────────────────────────────────────
_TICKER_SECTOR: dict[str, str] = {
    ticker: sector
    for sector, tickers in SECTOR_TICKERS.items()
    for ticker in tickers
}


def get_sector_for_ticker(ticker: str) -> str | None:
    """종목코드 → 섹터명 반환. 매핑 없으면 None."""
    return _TICKER_SECTOR.get(str(ticker).strip())


def detect_sector_wave(
    stock_leaders: list[dict],
    min_gap_pct: float = 10.0,
    min_count: int = 2,
) -> dict | None:
    """stock_leaders에서 섹터 wave 감지.

    같은 섹터에 min_count개 이상 종목이 min_gap_pct% 이상 상승하면
    해당 섹터와 리더 종목 목록을 반환.
    """
    sector_buckets: dict[str, list[dict]] = {}
    for stock in stock_leaders:
        gap = float(stock.get("gap_pct", 0) or 0)
        if gap < min_gap_pct:
            continue
        ticker = str(stock.get("ticker", "")).strip()
        sector = get_sector_for_ticker(ticker)
        if sector:
            sector_buckets.setdefault(sector, []).append(stock)

    best: dict | None = None
    best_score = 0.0
    for sector, members in sector_buckets.items():
        if len(members) < min_count:
            continue
        score = len(members) * sum(float(m.get("gap_pct", 0) or 0) for m in members)
        if score > best_score:
            best_score = score
            best = {"sector": sector, "leaders": members, "score": score}
    return best


def get_sector_laggards(sector: str, leader_tickers: set[str]) -> list[str]:
    """섹터 내에서 아직 상승하지 않은 종목(laggard) 반환."""
    peers = SECTOR_TICKERS.get(sector, [])
    return [t for t in peers if t not in leader_tickers]

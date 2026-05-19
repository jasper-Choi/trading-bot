"""
Korea Strategy B (New High Breakout) - Wide Universe Validation
================================================================
목적: 71개 모멘텀 편향 종목에서 나온 Strategy B 결과가
      더 넓고 다양한 유니버스에서도 유효한지 검증.

유니버스: 200개+ (섹터 균형 배분)
  - 기존 71개 모멘텀 성장주 포함
  + 금융 (은행/보험/증권)
  + 산업재 (철강/화학/건설/조선)
  + 소비재 (유통/식품/엔터)
  + 헬스케어 (제약/바이오 균형)
  + 에너지/유틸리티
  + 소형주 (변동성 높은 소형)
  + 부진 종목 (하락세 종목 — 전략이 회피하는지 확인)

테스트 기간: 2022-01-03 ~ 2025-12-31
자본: 10,000,000 KRW (포지션당 3,000,000 고정)
전략 B 파라미터: 60일 신고점 + 거래량 2배 + RSI 55-80
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

try:
    from pykrx import stock as krx
    PYKRX_OK = True
except ImportError:
    PYKRX_OK = False
    print("[오류] pykrx 없음 -> pip install pykrx")

# ----------------------------------------------------------------------
#  설정
# ----------------------------------------------------------------------
CONFIG = {
    "start_date":      "20220103",
    "end_date":        "20251231",
    "initial_capital": 10_000_000,
    "pos_krw":         3_000_000,  # 포지션당 고정 3백만원
    "max_positions":   3,
    "commission":      0.00015,
    "slippage":        0.0005,
    "cache_file":      "korea_wide_cache.json",
}

ROUND_TRIP_COST = (CONFIG["commission"] + CONFIG["slippage"]) * 2

STRATEGY_B = {
    "lookback":      60,
    "vol_mult":      2.0,
    "vol_ma_period": 20,
    "rsi_min":       55,
    "rsi_max":       80,
    "rsi_period":    14,
    "target_pct":    10.0,
    "stop_pct":      -4.0,
    "trail_trigger": 5.0,
    "trail_pct":     4.0,
    "max_hold_days": 20,
}

# ----------------------------------------------------------------------
#  확장 유니버스 (~215개, 섹터별 균형)
# ----------------------------------------------------------------------
UNIVERSE = {
    # ── [기존] KOSPI 대형주 ───────────────────────────────────────────
    "삼성전자":          "005930",
    "SK하이닉스":        "000660",
    "LG에너지솔루션":    "373220",
    "삼성바이오로직스":  "207940",
    "현대차":            "005380",
    "기아":              "000270",
    "POSCO홀딩스":       "005490",
    "셀트리온":          "068270",
    "현대모비스":        "012330",
    "삼성물산":          "028260",
    "삼성SDI":           "006400",
    "LG화학":            "051910",
    "SK이노베이션":      "096770",
    "카카오":            "035720",
    "네이버":            "035420",
    "두산에너빌리티":    "034020",
    "HD현대일렉트릭":    "267260",
    "한화에어로스페이스":"012450",
    "HD현대중공업":      "329180",
    "삼성중공업":        "010140",
    "한화오션":          "042660",
    "크래프톤":          "259960",
    "넷마블":            "251270",
    "CJ제일제당":        "097950",
    "LS ELECTRIC":       "010120",

    # ── [금융] 은행/보험/증권 ─────────────────────────────────────────
    "KB금융":            "105560",
    "신한지주":          "055550",
    "하나금융지주":      "086790",
    "우리금융지주":      "316140",
    "기업은행":          "024110",
    "BNK금융지주":       "138930",
    "DGB금융지주":       "139130",
    "JB금융지주":        "175330",
    "삼성생명":          "032830",
    "한화생명":          "088350",
    "삼성화재":          "000810",
    "DB손해보험":        "005830",
    "현대해상":          "001450",
    "메리츠금융지주":    "138040",
    "미래에셋증권":      "006800",
    "한국투자증권":      "071050",  # 한국금융지주
    "NH투자증권":        "005940",
    "키움증권":          "039490",
    "대신증권":          "003540",

    # ── [산업재] 철강/화학/건설/기계 ─────────────────────────────────
    "POSCO":             "005490",  # 포스코홀딩스 포함
    "현대제철":          "004020",
    "동국제강":          "001230",
    "세아베스틸지주":    "001430",
    "롯데케미칼":        "011170",
    "금호석유화학":      "011780",
    "한화솔루션":        "009830",
    "OCI홀딩스":         "010060",
    "효성첨단소재":      "298050",
    "SKC":               "011790",
    "GS건설":            "006360",
    "현대건설":          "000720",
    "대우건설":          "047040",
    "HDC현대산업개발":   "294870",
    "삼성엔지니어링":    "028050",
    "현대로템":          "064350",
    "두산밥캣":          "241560",
    "HD현대":            "267250",
    "현대글로비스":      "086280",
    "CJ대한통운":        "000120",

    # ── [소비재] 유통/식품/엔터/여행 ─────────────────────────────────
    "이마트":            "139480",
    "롯데쇼핑":          "023530",
    "현대백화점":        "069960",
    "BGF리테일":         "282330",
    "GS리테일":          "007070",
    "CJ ENM":            "035760",
    "하이브":            "352820",
    "SM엔터테인먼트":    "041510",
    "YG엔터테인먼트":    "122870",
    "JYP Ent":           "035900",
    "호텔신라":          "008770",
    "파라다이스":        "034230",
    "강원랜드":          "035250",
    "오리온":            "271560",
    "농심":              "004370",
    "하이트진로":        "000080",
    "롯데제과":          "280360",
    "CJ제일제당":        "097950",
    "대상":              "001680",

    # ── [헬스케어] 제약/바이오 균형 ──────────────────────────────────
    "알테오젠":          "196170",
    "HLB":               "028300",
    "리가켐바이오":      "141080",
    "삼천당제약":        "000250",
    "클래시스":          "214150",
    "파마리서치":        "214450",
    "한미약품":          "128940",
    "유한양행":          "000100",
    "종근당":            "185750",
    "동국제약":          "086450",
    "보령":              "003850",
    "대웅제약":          "069620",
    "일동제약":          "249420",
    "JW중외제약":        "001060",
    "셀트리온헬스케어":  "091990",
    "메디톡스":          "086900",
    "휴젤":              "145020",
    "바이오엔텍":        "326030",  # SK바이오팜
    "녹십자":            "006280",
    "SK바이오팜":        "326030",

    # ── [IT/반도체/통신] ──────────────────────────────────────────────
    "삼성전기":          "009150",
    "LG이노텍":          "011070",
    "LG디스플레이":      "034220",
    "SK스퀘어":          "402340",
    "카카오뱅크":        "323410",
    "카카오페이":        "377300",
    "KT":                "030200",
    "SKT":               "017670",
    "LG유플러스":        "032640",
    "NAVER":             "035420",
    "이오테크닉스":      "039030",
    "HPSP":              "403870",
    "원익IPS":           "240810",
    "파크시스템스":      "140860",
    "솔브레인":          "357780",
    "테크윙":            "089030",
    "피에스케이":        "031980",
    "코스맥스":          "044820",

    # ── [방산/조선] 2022-2025 주도 섹터 ─────────────────────────────
    "한화시스템":        "272210",
    "LIG넥스원":         "079550",
    "레인보우로보틱스":  "277810",
    "현대로템":          "064350",
    "한국항공우주":      "047810",
    "빅텍":              "065450",

    # ── [에너지/유틸리티] ─────────────────────────────────────────────
    "한국전력":          "015760",
    "한국가스공사":      "036460",
    "GS":                "078930",
    "S-Oil":             "010950",
    "SK이노베이션":      "096770",
    "씨에스윈드":        "112610",
    "두산퓨얼셀":        "336260",
    "한화솔루션":        "009830",

    # ── [KOSDAQ 성장주] ───────────────────────────────────────────────
    "에코프로비엠":      "247540",
    "에코프로":          "086520",
    "펄어비스":          "263750",
    "엔씨소프트":        "036570",
    "카카오게임즈":      "293490",
    "SK아이이테크놀로지":"361610",
    "인텔리안테크":      "189300",
    "에스티팜":          "237690",
    "저스템":            "085660",
    "케이카":            "381970",

    # ── [소형/중형 다양성] ────────────────────────────────────────────
    "락앤락":            "115390",
    "F&F":               "383220",
    "한섬":              "020000",
    "코웰패션":          "033290",
    "자화전자":          "033240",
    "비에이치":          "090460",
    "이녹스첨단소재":    "272290",
    "덕산네오룩스":      "213420",
    "후성":              "093370",
    "솔루스첨단소재":    "336370",

    # ── [부진/하락 섹터] — 전략 필터 테스트 ─────────────────────────
    # (60일 신고점 조건이 이들을 자동 배제하는지 확인)
    "LG전자":            "066570",   # 스마트폰 철수 이후 부진
    "삼성전기":          "009150",   # 변동성 큰 중립
    "롯데케미칼":        "011170",   # 2022-2023 급락
    "SK":                "034730",   # 지주사 디스카운트
    "한국전력":          "015760",   # 적자 지속
    "HMM":               "011200",   # 해운 사이클 하락
    "현대중공업":        "329180",   # 조선 사이클
    "대한항공":          "003490",   # 항공 회복 불확실
    "아시아나항공":      "020560",   # 매각 이슈
    "이마트":            "139480",   # 오프라인 유통 부진
}

# 중복 제거
UNIVERSE = dict(dict.fromkeys(UNIVERSE.values(), None))
# 티커만 unique하게 → 이름을 티커로 대체
_ticker_to_name = {}
for name, ticker in {
    "삼성전자":"005930","SK하이닉스":"000660","LG에너지솔루션":"373220",
    "삼성바이오로직스":"207940","현대차":"005380","기아":"000270",
    "셀트리온":"068270","현대모비스":"012330","삼성물산":"028260",
    "삼성SDI":"006400","LG화학":"051910","SK이노베이션":"096770",
    "카카오":"035720","네이버":"035420","두산에너빌리티":"034020",
    "HD현대일렉트릭":"267260","한화에어로스페이스":"012450",
    "HD현대중공업":"329180","삼성중공업":"010140","한화오션":"042660",
    "크래프톤":"259960","넷마블":"251270","CJ제일제당":"097950",
    "LS ELECTRIC":"010120","POSCO홀딩스":"005490",
    "KB금융":"105560","신한지주":"055550","하나금융지주":"086790",
    "우리금융지주":"316140","기업은행":"024110","BNK금융지주":"138930",
    "DGB금융지주":"139130","JB금융지주":"175330",
    "삼성생명":"032830","한화생명":"088350","삼성화재":"000810",
    "DB손해보험":"005830","현대해상":"001450","메리츠금융지주":"138040",
    "미래에셋증권":"006800","한국금융지주":"071050","NH투자증권":"005940",
    "키움증권":"039490","대신증권":"003540",
    "현대제철":"004020","동국제강":"001230","세아베스틸지주":"001430",
    "금호석유화학":"011780","한화솔루션":"009830","OCI홀딩스":"010060",
    "효성첨단소재":"298050","SKC":"011790",
    "GS건설":"006360","현대건설":"000720","대우건설":"047040",
    "HDC현대산업개발":"294870","삼성엔지니어링":"028050",
    "현대로템":"064350","두산밥캣":"241560","HD현대":"267250",
    "현대글로비스":"086280","CJ대한통운":"000120",
    "이마트":"139480","롯데쇼핑":"023530","현대백화점":"069960",
    "BGF리테일":"282330","GS리테일":"007070","CJ ENM":"035760",
    "하이브":"352820","SM엔터":"041510","YG엔터":"122870","JYP":"035900",
    "호텔신라":"008770","파라다이스":"034230","강원랜드":"035250",
    "오리온":"271560","농심":"004370","하이트진로":"000080",
    "대상":"001680",
    "알테오젠":"196170","HLB":"028300","리가켐바이오":"141080",
    "삼천당제약":"000250","클래시스":"214150","파마리서치":"214450",
    "한미약품":"128940","유한양행":"000100","종근당":"185750",
    "동국제약":"086450","보령":"003850","대웅제약":"069620",
    "일동제약":"249420","JW중외제약":"001060","셀트리온헬스케어":"091990",
    "메디톡스":"086900","휴젤":"145020","SK바이오팜":"326030",
    "녹십자":"006280",
    "삼성전기":"009150","LG이노텍":"011070","LG디스플레이":"034220",
    "SK스퀘어":"402340","카카오뱅크":"323410","카카오페이":"377300",
    "KT":"030200","SKT":"017670","LG유플러스":"032640",
    "이오테크닉스":"039030","HPSP":"403870","원익IPS":"240810",
    "파크시스템스":"140860","솔브레인":"357780","테크윙":"089030",
    "피에스케이":"031980","코스맥스":"044820",
    "한화시스템":"272210","LIG넥스원":"079550","레인보우로보틱스":"277810",
    "한국항공우주":"047810","빅텍":"065450",
    "한국전력":"015760","한국가스공사":"036460","GS":"078930",
    "S-Oil":"010950","씨에스윈드":"112610","두산퓨얼셀":"336260",
    "에코프로비엠":"247540","에코프로":"086520","펄어비스":"263750",
    "엔씨소프트":"036570","카카오게임즈":"293490",
    "SK아이이테크놀로지":"361610","인텔리안테크":"189300","에스티팜":"237690",
    "저스템":"085660","케이카":"381970",
    "락앤락":"115390","F&F":"383220","한섬":"020000","코웰패션":"033290",
    "자화전자":"033240","비에이치":"090460","이녹스첨단소재":"272290",
    "덕산네오룩스":"213420","후성":"093370","솔루스첨단소재":"336370",
    "LG전자":"066570","SK":"034730","HMM":"011200","대한항공":"003490",
    "아시아나항공":"020560","롯데케미칼":"011170",
}.items():
    _ticker_to_name[ticker] = name

# 최종 유니버스: 고유 티커 dict
UNIVERSE = {t: t for t in _ticker_to_name.keys()}


# ----------------------------------------------------------------------
#  데이터 수집
# ----------------------------------------------------------------------
def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    if not PYKRX_OK:
        return pd.DataFrame()
    try:
        df = krx.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if "date" in cl or "날짜" in cl:
                col_map[c] = "date"
            elif "open" in cl or "시가" in cl:
                col_map[c] = "open"
            elif "high" in cl or "고가" in cl:
                col_map[c] = "high"
            elif "low" in cl or "저가" in cl:
                col_map[c] = "low"
            elif "close" in cl or "종가" in cl:
                col_map[c] = "close"
            elif "volume" in cl or "거래량" in cl:
                col_map[c] = "volume"
        df = df.rename(columns=col_map)
        needed = ["date", "open", "high", "low", "close", "volume"]
        if any(c not in df.columns for c in needed):
            return pd.DataFrame()
        df = df[needed].copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["open"] > 0) & (df["close"] > 0) & (df["volume"] > 0)]
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        return pd.DataFrame()


def load_universe_data(cache_file: str) -> dict[str, pd.DataFrame]:
    cache_path = os.path.join(os.path.dirname(__file__), cache_file)
    tickers = list(UNIVERSE.keys())

    if os.path.exists(cache_path):
        print(f"[cache] {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = {}
        for t, records in raw.items():
            df = pd.DataFrame(records)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                result[t] = df
        # 캐시에 없는 종목만 추가 다운로드
        missing = [t for t in tickers if t not in result]
        if missing:
            print(f"  추가 다운로드: {len(missing)}개")
            new_cache = {}
            for i, ticker in enumerate(missing, 1):
                name = _ticker_to_name.get(ticker, ticker)
                print(f"  [{i}/{len(missing)}] {name}({ticker})", end=" ", flush=True)
                df = fetch_ohlcv(ticker, CONFIG["start_date"], CONFIG["end_date"])
                if not df.empty:
                    result[ticker] = df
                    new_cache[ticker] = df.assign(date=df["date"].astype(str)).to_dict("records")
                    print(f"OK {len(df)}")
                else:
                    print("SKIP")
                time.sleep(0.3)
            # 캐시 갱신
            raw.update(new_cache)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False)
        print(f"  -> {len(result)}개 종목 로드")
        return result

    # 최초 다운로드
    print(f"[download] {len(tickers)}개 종목...")
    result, cache_data = {}, {}
    for i, ticker in enumerate(tickers, 1):
        name = _ticker_to_name.get(ticker, ticker)
        print(f"  [{i:3d}/{len(tickers)}] {name}({ticker})", end=" ", flush=True)
        df = fetch_ohlcv(ticker, CONFIG["start_date"], CONFIG["end_date"])
        if not df.empty:
            result[ticker] = df
            cache_data[ticker] = df.assign(date=df["date"].astype(str)).to_dict("records")
            print(f"OK {len(df)}")
        else:
            print("SKIP")
        time.sleep(0.3)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False)
    print(f"\n[saved] {cache_path} ({len(result)} tickers)")
    return result


# ----------------------------------------------------------------------
#  지표
# ----------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    p = STRATEGY_B["vol_ma_period"]
    df["vol_ma"] = df["volume"].rolling(p).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"]
    df["gap_pct"] = (df["open"] / df["close"].shift(1) - 1) * 100
    df["chg_pct"] = (df["close"] / df["close"].shift(1) - 1) * 100

    lb = STRATEGY_B["lookback"]
    df[f"high_{lb}d"] = df["close"].rolling(lb).max().shift(1)

    rp = STRATEGY_B["rsi_period"]
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(rp).mean()
    loss = (-delta.clip(upper=0)).rolling(rp).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)
    return df


# ----------------------------------------------------------------------
#  신호
# ----------------------------------------------------------------------
def signal_B(row: pd.Series) -> bool:
    lb = STRATEGY_B["lookback"]
    hcol = f"high_{lb}d"
    if pd.isna(row.get(hcol)) or pd.isna(row.get("rsi")) or pd.isna(row.get("vol_ratio")):
        return False
    return (
        row["close"] > row[hcol]
        and row["vol_ratio"] >= STRATEGY_B["vol_mult"]
        and STRATEGY_B["rsi_min"] <= row["rsi"] <= STRATEGY_B["rsi_max"]
    )


# ----------------------------------------------------------------------
#  시뮬레이션
# ----------------------------------------------------------------------
@dataclass
class Position:
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    size: float
    peak_price: float = field(init=False)
    days_held: int = 0

    def __post_init__(self):
        self.peak_price = self.entry_price


@dataclass
class Trade:
    ticker: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    size: float
    reason: str

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1) * 100 - ROUND_TRIP_COST * 100

    @property
    def pnl_krw(self) -> float:
        gross = (self.exit_price - self.entry_price) * self.size
        cost = self.entry_price * self.size * ROUND_TRIP_COST
        return gross - cost


def simulate(data: dict[str, pd.DataFrame], dates: list) -> tuple[list[Trade], list[float]]:
    capital = CONFIG["initial_capital"]
    pos_krw = CONFIG["pos_krw"]
    max_pos = CONFIG["max_positions"]

    positions: list[Position] = []
    trades: list[Trade] = []
    equity_curve = [capital]

    ticker_idx = {t: {row["date"]: i for i, row in df.iterrows()} for t, df in data.items()}

    cfg = STRATEGY_B
    for date in dates:
        date_ts = pd.Timestamp(date)

        # 청산
        closed = []
        for pos in positions:
            if pos.ticker not in data:
                continue
            idx = ticker_idx[pos.ticker].get(date_ts)
            if idx is None:
                pos.days_held += 1
                continue
            row = data[pos.ticker].iloc[idx]
            pos.days_held += 1
            pos.peak_price = max(pos.peak_price, float(row["high"]))

            entry = pos.entry_price
            high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
            peak = pos.peak_price
            stop_p  = entry * (1 + cfg["stop_pct"] / 100)
            tgt_p   = entry * (1 + cfg["target_pct"] / 100)
            trail_t = entry * (1 + cfg["trail_trigger"] / 100)
            trail_p = peak * (1 - cfg["trail_pct"] / 100)

            exit_price, reason = None, None
            if low <= stop_p:
                exit_price, reason = stop_p, "stop"
            elif high >= tgt_p:
                exit_price, reason = tgt_p, "target"
            elif peak >= trail_t and low <= trail_p:
                exit_price, reason = max(trail_p, low), "trail"
            elif pos.days_held >= cfg["max_hold_days"]:
                exit_price, reason = close, "timeout"

            if exit_price:
                t = Trade(pos.ticker, pos.entry_date, date_ts,
                          entry, exit_price, pos.size, reason)
                capital += t.pnl_krw
                trades.append(t)
                closed.append(pos)

        for p in closed:
            positions.remove(p)

        # 진입
        if len(positions) < max_pos and capital >= pos_krw:
            candidates = []
            for ticker, df in data.items():
                if any(p.ticker == ticker for p in positions):
                    continue
                idx = ticker_idx[ticker].get(date_ts)
                if idx is None or idx < 1:
                    continue
                row = df.iloc[idx]
                if signal_B(row):
                    candidates.append((ticker, row, float(row.get("vol_ratio", 0))))
            candidates.sort(key=lambda x: x[2], reverse=True)
            for ticker, row, _ in candidates[:max_pos - len(positions)]:
                ep = float(row["open"]) * (1 + CONFIG["slippage"])
                if ep <= 0 or capital < pos_krw:
                    continue
                sz = pos_krw / ep
                positions.append(Position(ticker, date_ts, ep, sz))

        # 미실현 평가
        unreal = sum(
            (data[p.ticker].iloc[ticker_idx[p.ticker][date_ts]]["close"] - p.entry_price) * p.size
            for p in positions
            if ticker_idx[p.ticker].get(date_ts) is not None
        )
        equity_curve.append(capital + unreal)

    # 잔여 강제청산
    if dates:
        last = pd.Timestamp(dates[-1])
        for pos in positions:
            idx = ticker_idx[pos.ticker].get(last)
            if idx is not None:
                ep = float(data[pos.ticker].iloc[idx]["close"])
                t = Trade(pos.ticker, pos.entry_date, last,
                          pos.entry_price, ep, pos.size, "end")
                capital += t.pnl_krw
                trades.append(t)

    return trades, equity_curve


# ----------------------------------------------------------------------
#  통계
# ----------------------------------------------------------------------
def calc_stats(trades: list[Trade], equity_curve: list[float]) -> dict:
    if not trades:
        return {"error": "no trades"}

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    wr = len(wins) / len(pnls) * 100
    avg_w = np.mean(wins) if wins else 0.0
    avg_l = np.mean(losses) if losses else 0.0
    pl = abs(avg_w / avg_l) if avg_l != 0 else 0.0
    ev = wr / 100 * avg_w + (1 - wr / 100) * avg_l

    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    mdd = float(np.min((eq - peak) / peak * 100))

    init = CONFIG["initial_capital"]
    final = equity_curve[-1]
    years = (pd.Timestamp(CONFIG["end_date"]) - pd.Timestamp(CONFIG["start_date"])).days / 365.25
    ann = ((final / init) ** (1 / years) - 1) * 100

    dr = np.diff(eq) / eq[:-1]
    sharpe = float(np.mean(dr) / np.std(dr) * np.sqrt(252)) if np.std(dr) > 0 else 0.0

    reasons = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1

    # 섹터별 성과
    sector_map = {
        "005930":"반도체","000660":"반도체","373220":"배터리","207940":"바이오",
        "005380":"자동차","000270":"자동차","005490":"철강","068270":"바이오",
        "012330":"자동차","028260":"지주","006400":"배터리","051910":"화학",
        "096770":"에너지","035720":"IT","035420":"IT",
        "105560":"금융","055550":"금융","086790":"금융","316140":"금융",
        "024110":"금융","138930":"금융","139130":"금융","175330":"금융",
        "032830":"금융","088350":"금융","000810":"금융","005830":"금융",
        "001450":"금융","138040":"금융","006800":"금융","071050":"금융",
        "005940":"금융","039490":"금융","003540":"금융",
        "004020":"철강","001230":"철강","001430":"철강",
        "011780":"화학","009830":"화학","010060":"화학","298050":"화학","011790":"화학",
        "006360":"건설","000720":"건설","047040":"건설","294870":"건설","028050":"건설",
        "064350":"산업재","241560":"산업재","267250":"산업재","086280":"산업재","000120":"산업재",
        "139480":"소비재","023530":"소비재","069960":"소비재","282330":"소비재","007070":"소비재",
        "035760":"엔터","352820":"엔터","041510":"엔터","122870":"엔터","035900":"엔터",
        "008770":"여행","034230":"여행","035250":"여행",
        "271560":"식품","004370":"식품","000080":"식품","001680":"식품",
        "196170":"바이오","028300":"바이오","141080":"바이오","000250":"제약",
        "214150":"제약","214450":"제약","128940":"제약","000100":"제약",
        "185750":"제약","086450":"제약","003850":"제약","069620":"제약",
        "249420":"제약","001060":"제약","091990":"바이오","086900":"바이오",
        "145020":"제약","006280":"제약",
        "009150":"전자","011070":"전자","034220":"디스플레이",
        "402340":"IT","323410":"IT","377300":"IT","030200":"통신","017670":"통신","032640":"통신",
        "039030":"반도체","403870":"반도체","240810":"반도체","140860":"반도체",
        "357780":"반도체","089030":"반도체","031980":"반도체","044820":"화학",
        "272210":"방산","079550":"방산","277810":"로봇","047810":"방산","065450":"방산",
        "015760":"에너지","036460":"에너지","078930":"에너지","010950":"에너지",
        "112610":"신재생","336260":"신재생",
        "247540":"배터리","086520":"배터리","263750":"게임","036570":"게임","293490":"게임",
        "361610":"반도체","189300":"통신장비","237690":"제약","085660":"의료","381970":"기타",
        "066570":"전자","034730":"지주","011200":"해운","003490":"항공","020560":"항공",
        "011170":"화학","012450":"방산","010140":"조선","042660":"조선","329180":"조선",
        "034020":"산업재","267260":"전기","267250":"지주","251270":"게임","097950":"식품",
        "010120":"전기","259960":"게임",
    }

    sector_pnl: dict[str, list] = {}
    for t in trades:
        sec = sector_map.get(t.ticker, "기타")
        sector_pnl.setdefault(sec, []).append(t.pnl_pct)
    sector_stats = {
        sec: {
            "trades": len(ps),
            "wr": round(sum(1 for p in ps if p > 0) / len(ps) * 100, 1),
            "avg_pnl": round(np.mean(ps), 2),
        }
        for sec, ps in sorted(sector_pnl.items(), key=lambda x: -len(x[1]))
    }

    return {
        "universe_size":  len(data_ref),
        "trade_count":    len(trades),
        "win_rate":       round(wr, 1),
        "avg_win":        round(avg_w, 2),
        "avg_loss":       round(avg_l, 2),
        "pl_ratio":       round(pl, 2),
        "ev_per_trade":   round(ev, 3),
        "annual_return":  round(ann, 1),
        "total_return":   round((final / init - 1) * 100, 1),
        "mdd":            round(mdd, 1),
        "sharpe":         round(sharpe, 2),
        "final_capital":  round(final),
        "exit_reasons":   reasons,
        "sector_stats":   sector_stats,
    }


# 전역 참조용 (calc_stats에서 사용)
data_ref = {}


# ----------------------------------------------------------------------
#  메인
# ----------------------------------------------------------------------
def main():
    global data_ref

    print("=" * 60)
    print("  Korea Strategy B - Wide Universe Validation")
    print(f"  period : {CONFIG['start_date']} ~ {CONFIG['end_date']}")
    print(f"  capital: {CONFIG['initial_capital']:,} KRW (per pos: {CONFIG['pos_krw']:,})")
    print(f"  target universe: {len(UNIVERSE)} tickers")
    print("=" * 60)

    # 1. 데이터
    data = load_universe_data(CONFIG["cache_file"])
    data_ref = data
    if not data:
        print("[ERROR] No data.")
        return

    # 2. 지표
    print("\n[indicator] computing...")
    processed = {t: add_indicators(df) for t, df in data.items()}
    print(f"  -> {len(processed)} tickers ready")

    # 3. 거래일
    all_dates: set = set()
    for df in processed.values():
        all_dates.update(df["date"].tolist())
    s, e = pd.Timestamp(CONFIG["start_date"]), pd.Timestamp(CONFIG["end_date"])
    dates = sorted(d for d in all_dates if s <= d <= e)
    print(f"  -> {len(dates)} trading days")

    # 4. 시뮬레이션
    print("\n[simulate] Strategy B (New High Breakout)...")
    trades, equity = simulate(processed, dates)
    stats = calc_stats(trades, equity)

    if "error" in stats:
        print("  No trades generated.")
        return

    # 5. 결과 출력
    print("\n" + "=" * 60)
    print("  RESULT: Strategy B - Wide Universe")
    print("-" * 60)
    print(f"  Universe     : {stats['universe_size']}개 종목")
    print(f"  Trade count  : {stats['trade_count']}건")
    print(f"  Win rate     : {stats['win_rate']:.1f}%")
    print(f"  Avg win      : +{stats['avg_win']:.2f}%")
    print(f"  Avg loss     : {stats['avg_loss']:.2f}%")
    print(f"  P/L ratio    : {stats['pl_ratio']:.2f}x")
    print(f"  EV / trade   : {stats['ev_per_trade']:+.3f}%")
    print(f"  Annual return: {stats['annual_return']:+.1f}%")
    print(f"  Total return : {stats['total_return']:+.1f}%")
    print(f"  MDD          : {stats['mdd']:.1f}%")
    print(f"  Sharpe       : {stats['sharpe']:.2f}")
    print(f"  Final capital: {stats['final_capital']:,} KRW")

    # 청산 이유
    reasons = stats["exit_reasons"]
    total = sum(reasons.values())
    print(f"\n  Exit reasons:")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {r:<12} {c:4d}건 ({c/total*100:.0f}%)")

    # 섹터별 성과
    print(f"\n  Top sectors (by trade count):")
    sec = stats["sector_stats"]
    for sname, sv in list(sec.items())[:10]:
        print(f"    {sname:<10} T={sv['trades']:3d} WR={sv['wr']:5.1f}% avg={sv['avg_pnl']:+.2f}%")

    # 6. narrow vs wide 비교 (narrow 결과 파일 있으면)
    narrow_path = os.path.join(os.path.dirname(__file__), "korea_backtest_result.json")
    if os.path.exists(narrow_path):
        with open(narrow_path, encoding="utf-8") as f:
            narrow_results = json.load(f)
        narrow_B = next((r for r in narrow_results if "B_new_high" in r.get("strategy", "")), None)
        if narrow_B:
            print("\n" + "=" * 60)
            print("  NARROW vs WIDE comparison (Strategy B)")
            print("-" * 60)
            print(f"  {'':12} {'narrow':>10} {'wide':>10}")
            print(f"  {'universe':12} {'71개':>10} {str(stats['universe_size'])+'개':>10}")
            print(f"  {'trades':12} {narrow_B['trade_count']:>10} {stats['trade_count']:>10}")
            print(f"  {'win rate':12} {narrow_B['win_rate']:>9.1f}% {stats['win_rate']:>9.1f}%")
            print(f"  {'pl ratio':12} {narrow_B['pl_ratio']:>10.2f} {stats['pl_ratio']:>10.2f}")
            print(f"  {'annual ret':12} {narrow_B['annual_return']:>+9.1f}% {stats['annual_return']:>+9.1f}%")
            print(f"  {'MDD':12} {narrow_B['mdd']:>+9.1f}% {stats['mdd']:>+9.1f}%")
            print(f"  {'sharpe':12} {narrow_B['sharpe']:>10.2f} {stats['sharpe']:>10.2f}")

            # 판정
            diff_wr = stats["win_rate"] - narrow_B["win_rate"]
            diff_ann = stats["annual_return"] - narrow_B["annual_return"]
            print("\n  Verdict:")
            if abs(diff_wr) <= 10 and stats["sharpe"] >= 2.0:
                print("  [VALID] Wide universe confirms strategy edge.")
                print(f"  Win rate diff: {diff_wr:+.1f}% | Annual diff: {diff_ann:+.1f}%")
            else:
                print("  [WARNING] Significant performance gap - may be overfit.")
                print(f"  Win rate diff: {diff_wr:+.1f}% | Annual diff: {diff_ann:+.1f}%")

    # 7. JSON 저장
    def _safe(o):
        if isinstance(o, dict): return {k: _safe(v) for k, v in o.items()}
        if isinstance(o, list): return [_safe(v) for v in o]
        if isinstance(o, (np.bool_, bool)): return bool(o)
        if isinstance(o, np.integer): return int(o)
        if isinstance(o, np.floating): return float(o)
        return o

    out = os.path.join(os.path.dirname(__file__), "korea_wide_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(_safe(stats), f, ensure_ascii=False, indent=2)
    print(f"\n[saved] {out}")
    print("=" * 60)


if __name__ == "__main__":
    main()

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.agents.base import BaseAgent
from app.core.models import AgentResult
from app.services.korea_sentiment import get_combined_sentiment, get_theme_boost, get_stock_news_risk
from app.services.korea_supply_demand import get_institutional_tickers, get_supply_demand_score
from app.services.korea_universe import get_korea_universe
from app.services.kis_stream_cache import (
    subscribe_tickers,
    get_opening_reversal_signal,
    get_stream_status,
)
from app.services.market_gateway import get_kosdaq_snapshot, get_naver_daily_prices
from app.services.signal_engine import (
    summarize_equity_signal,
    summarize_breakout_signal,
    summarize_pullback_uptrend_signal,
)

_FETCH_WORKERS = 12
# Max candidates from dynamic universe to run breakout scoring on (keeps runtime bounded)
_UNIVERSE_SCAN_LIMIT = 80
# Top technical candidates to enrich with sentiment
_SENTIMENT_TOP_N = 15


class KoreaStockDeskAgent(BaseAgent):
    def __init__(self):
        super().__init__("korea_stock_desk_agent")

    def run(self) -> AgentResult:
        # ── Path A: Gap-up candidates (KOSDAQ movers today) ─────────────────
        # top_n_down=15: 갭다운 종목도 함께 수집 (Path E 갭 메꾸기에 사용)
        leaders = get_kosdaq_snapshot(top_n=30, top_n_down=15)

        gap_items = [
            item for item in leaders
            if 1.2 <= float(item.get("gap_pct", 0.0) or 0.0) <= 12.0
            and str(item.get("ticker", "")).strip()
        ]

        def _fetch_a(item: dict) -> tuple[dict, list[dict]]:
            ticker = str(item.get("ticker", "")).strip()
            return item, get_naver_daily_prices(ticker, count=42)

        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
            path_a_results = list(executor.map(_fetch_a, gap_items))

        enriched_candidates: list[dict] = []
        for item, candles in path_a_results:
            gap_pct = float(item.get("gap_pct", 0.0) or 0.0)
            signal = summarize_equity_signal(candles)
            signal_score = float(signal.get("score", 0.5) or 0.5)
            volume = float(item.get("volume", 0.0) or 0.0)
            rsi_value = signal.get("rsi")
            burst_change = float(signal.get("burst_change_pct", 0.0) or 0.0)
            ema_gap = float(signal.get("ema_gap_pct", 0.0) or 0.0)
            liquidity_score = min(volume, 250000) / 250000 * 0.24 if volume > 0 else 0.0
            overheat_penalty = 0.0
            if gap_pct >= 10.0:
                overheat_penalty += 0.08
            if rsi_value is not None and float(rsi_value) >= 78.0:
                overheat_penalty += 0.12
            if burst_change >= 12.0:
                overheat_penalty += 0.08
            if ema_gap >= 12.0:
                overheat_penalty += 0.06
            candidate_score = round(
                (gap_pct * 0.022)
                + liquidity_score
                + (signal_score * 0.68)
                - overheat_penalty,
                2,
            )
            # 상승 다이버전스 정보 추출 (signal에서 이미 계산됨)
            bullish_div = bool(signal.get("bullish_divergence_ok", False))
            div_strength = float(signal.get("divergence_strength", 0.0) or 0.0)
            # 다이버전스 신호 시 추가 점수 (눌림목 매집 구간)
            div_bonus = round(div_strength * 0.12, 3) if bullish_div else 0.0
            enriched_candidates.append(
                {
                    **item,
                    "signal_bias": signal.get("bias", "neutral"),
                    "signal_score": signal_score,
                    "signal_reasons": signal.get("reasons", []),
                    "rsi": rsi_value,
                    "burst_change_pct": burst_change,
                    "ema_gap_pct": ema_gap,
                    "overheat_penalty": round(overheat_penalty, 2),
                    "candidate_score": round(candidate_score + div_bonus, 2),
                    "is_breakout": False,
                    "breakout_count": 0,
                    "sentiment_score": 0.5,
                    "bullish_divergence_ok": bullish_div,
                    "divergence_strength": div_strength,
                }
            )

        # ── Path B: Dynamic universe breakout scan ───────────────────────────
        # Replaces fixed 20-stock watchlist with KOSPI+KOSDAQ top-volume universe
        already_tickers = {str(c.get("ticker", "")).strip() for c in enriched_candidates}

        universe = get_korea_universe()
        watchlist_items = [
            (item["ticker"], item["name"])
            for item in universe
            if item["ticker"] not in already_tickers
        ][:_UNIVERSE_SCAN_LIMIT]

        def _fetch_b(ticker_name: tuple[str, str]) -> tuple[str, str, list[dict]]:
            ticker, name = ticker_name
            return ticker, name, get_naver_daily_prices(ticker, count=42)

        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
            path_b_results = list(executor.map(_fetch_b, watchlist_items))

        breakout_candidates: list[dict] = []
        pullback_ma_candidates: list[dict] = []
        for ticker, name, candles in path_b_results:
            if len(candles) < 22:
                continue
            bk = summarize_breakout_signal(candles, breakout_period=20, vol_surge_mult=2.5,
                                           rsi_min=55.0, rsi_max=78.0)
            confirmed_count = int(bk.get("confirmed_count", 0) or 0)
            signal = summarize_equity_signal(candles)
            signal_score = float(signal.get("score", 0.5) or 0.5)
            last_volume = float(candles[-1].get("volume") or 0.0)
            last_close = float(candles[-1].get("close") or 0.0)
            rsi_value = bk.get("last_rsi")
            _b_burst = float(signal.get("burst_change_pct", 0.0) or 0.0)
            _b_ema_gap = float(signal.get("ema_gap_pct", 0.0) or 0.0)
            overheat_penalty = 0.0
            if rsi_value is not None and float(rsi_value) >= 78.0:
                overheat_penalty += 0.12
            if _b_burst >= 12.0:               # Path A와 동일한 급등 과열 체크
                overheat_penalty += 0.08
            if _b_ema_gap >= 12.0:             # EMA 이격 과열 체크 (Path B/F 누락 버그 수정)
                overheat_penalty += 0.06

            # ── Path B: 브레이크아웃 후보 ───────────────────────────────────
            if confirmed_count >= 2:
                breakout_score = float(bk.get("breakout_score", 0.0) or 0.0)
                candidate_score = round(
                    breakout_score * 0.65 + signal_score * 0.35 - overheat_penalty, 2
                )
                breakout_candidates.append(
                    {
                        "ticker": ticker,
                        "name": name,
                        "current_price": last_close,
                        "gap_pct": 0.0,
                        "volume": int(last_volume),
                        "signal_bias": signal.get("bias", "neutral"),
                        "signal_score": signal_score,
                        "signal_reasons": signal.get("reasons", []),
                        "rsi": rsi_value,
                        "burst_change_pct": float(signal.get("burst_change_pct", 0.0) or 0.0),
                        "ema_gap_pct": float(signal.get("ema_gap_pct", 0.0) or 0.0),
                        "overheat_penalty": round(overheat_penalty, 2),
                        "candidate_score": candidate_score,
                        "is_breakout": confirmed_count >= 3,
                        "breakout_count": confirmed_count,
                        "vol_ratio": float(bk.get("vol_ratio", 0.0) or 0.0),
                        "breakout_reasons": bk.get("reasons", []),
                        "sentiment_score": 0.5,
                    }
                )

            # ── Path F: 눌림목 매수 후보 (상승 추세 + MA 눌림) ───────────────
            if len(candles) >= 42:
                pb = summarize_pullback_uptrend_signal(candles)
                if pb["pullback_ma_detected"] and pb["pullback_ma_score"] >= 0.50:
                    pb_score = round(pb["pullback_ma_score"] * 0.70 + signal_score * 0.30, 2)
                    pullback_ma_candidates.append({
                        "ticker": ticker,
                        "name": name,
                        "current_price": last_close,
                        "gap_pct": 0.0,
                        "volume": int(last_volume),
                        "signal_bias": signal.get("bias", "neutral"),
                        "signal_score": signal_score,
                        "signal_reasons": signal.get("reasons", []),
                        "rsi": pb.get("pullback_rsi"),
                        "burst_change_pct": float(signal.get("burst_change_pct", 0.0) or 0.0),
                        "ema_gap_pct": float(signal.get("ema_gap_pct", 0.0) or 0.0),
                        "overheat_penalty": round(overheat_penalty, 2),
                        "candidate_score": pb_score,
                        "is_breakout": False,
                        "breakout_count": 0,
                        "vol_ratio": float(bk.get("vol_ratio", 0.0) or 0.0),
                        "sentiment_score": 0.5,
                        "pullback_ma": True,
                        "pullback_ma_score": pb["pullback_ma_score"],
                        "pct_from_ma20": pb["pct_from_ma20"],
                        "pct_from_ma40": pb["pct_from_ma40"],
                        "ma20": pb["ma20"],
                        "vol_declining": pb["vol_declining"],
                    })
        pullback_ma_candidates.sort(key=lambda c: c["pullback_ma_score"], reverse=True)

        # ── 수급 데이터 (기관 레이더 종목집합) 한 번만 가져오기 ─────────────
        try:
            inst_tickers = get_institutional_tickers()
        except Exception:
            inst_tickers = set()

        # 수급 점수를 Path B 브레이크아웃 후보에 반영 (기술점수 재계산)
        for bc in breakout_candidates:
            ticker = str(bc.get("ticker", "")).strip()
            sd_score = get_supply_demand_score(ticker, inst_tickers)
            bc["supply_demand_score"] = round(sd_score, 3)
            bc["inst_radar"] = ticker in inst_tickers
            # candidate_score 재계산: 수급 10% 가중치 추가
            bc["candidate_score"] = round(
                bc["candidate_score"] * 0.90 + sd_score * 0.10,
                2,
            )

        # ── Merge, take top-15 by technical score, then enrich with sentiment ─
        all_candidates = enriched_candidates + breakout_candidates
        all_candidates.sort(
            key=lambda c: (
                c.get("candidate_score", 0.0),
                c.get("breakout_count", 0),
                c.get("gap_pct", 0.0),
            ),
            reverse=True,
        )
        top15 = all_candidates[:_SENTIMENT_TOP_N]

        def _enrich_sentiment(c: dict) -> dict:
            ticker = str(c.get("ticker", "")).strip()
            if not ticker:
                return c
            try:
                sent = get_combined_sentiment(ticker)
                sentiment_score = float(sent.get("combined_score", 0.5))
                attention_boost = bool(sent.get("attention_boost", False))
                theme_boost = float(sent.get("theme_boost", 0.0))
                detected_themes = sent.get("detected_themes", {})
                # supply/demand score (이미 있으면 재사용, 없으면 inst_tickers 사용)
                sd_score = float(c.get("supply_demand_score", get_supply_demand_score(ticker, inst_tickers)))
                overheat_penalty = float(c.get("overheat_penalty", 0.0))
                bk_score = float(c.get("breakout_count", 0)) / 5.0 if c.get("is_breakout") else 0.0
                sig_score = float(c.get("signal_score", 0.5))
                # Weights: technical 45%, sentiment 20%, supply/demand 15%, theme 10%, gap/liq 10%
                if c.get("is_breakout"):
                    base = bk_score * 0.45 + sig_score * 0.20
                else:
                    gap = float(c.get("gap_pct", 0.0)) * 0.022
                    liq = min(float(c.get("volume", 0)), 250000) / 250000 * 0.10
                    base = gap + liq + sig_score * 0.33
                new_score = round(
                    base
                    + sentiment_score * 0.20
                    + sd_score * 0.15
                    + theme_boost
                    - overheat_penalty,
                    2,
                )
                c = {
                    **c,
                    "sentiment_score": round(sentiment_score, 3),
                    "sentiment_attention": attention_boost,
                    "top_discussion": sent.get("top_discussion", []),
                    "detected_themes": detected_themes,
                    "theme_boost": round(theme_boost, 3),
                    "supply_demand_score": round(sd_score, 3),
                    "inst_radar": c.get("inst_radar", ticker in inst_tickers),
                    "candidate_score": new_score,
                }
            except Exception:
                pass
            return c

        with ThreadPoolExecutor(max_workers=min(8, len(top15))) as executor:
            enriched_top = list(executor.map(_enrich_sentiment, top15))

        # Rebuild final ranked list: enriched top + remaining (already scored)
        top_tickers = {str(c.get("ticker", "")) for c in enriched_top}
        rest = [c for c in all_candidates[_SENTIMENT_TOP_N:] if str(c.get("ticker", "")) not in top_tickers]
        gap_candidates = sorted(
            enriched_top + rest,
            key=lambda c: (
                c.get("candidate_score", 0.0),
                c.get("breakout_count", 0),
                c.get("gap_pct", 0.0),
                c.get("volume", 0.0),
            ),
            reverse=True,
        )
        top_candidates = gap_candidates[:3]

        avg_gap = round(
            sum(float(c.get("gap_pct", 0) or 0.0) for c in top_candidates) / len(top_candidates), 2
        ) if top_candidates else 0.0
        avg_volume = round(
            sum(float(c.get("volume", 0) or 0.0) for c in top_candidates) / len(top_candidates)
        ) if top_candidates else 0.0
        avg_signal = round(
            sum(float(c.get("signal_score", 0.0) or 0.0) for c in top_candidates) / len(top_candidates), 2
        ) if top_candidates else 0.0
        avg_sentiment = round(
            sum(float(c.get("sentiment_score", 0.5) or 0.5) for c in top_candidates) / len(top_candidates), 3
        ) if top_candidates else 0.5

        active_gap_count = sum(1 for c in enriched_candidates if float(c.get("gap_pct", 0) or 0) >= 1.2)
        breakout_confirmed_count = sum(1 for c in breakout_candidates if int(c.get("breakout_count", 0) or 0) >= 4)
        breakout_partial_count = sum(1 for c in breakout_candidates if int(c.get("breakout_count", 0) or 0) == 3)

        # ── Path C: 아침 오픈 리버설 (틱 스트림 기반) ──────────────────────
        # 09:00~09:40 KST (00:00~00:40 UTC) 구간에만 작동
        # 매도 쏟아지는 갭다운 → 소진 → 반전 패턴 감지
        now_utc_hour = datetime.now(timezone.utc).hour
        now_utc_min  = datetime.now(timezone.utc).minute
        _in_open_window = (now_utc_hour == 0 and now_utc_min <= 40)

        reversal_candidates: list[dict] = []
        if _in_open_window:
            # 틱 구독 유지: gap_candidates 상위 10 + enriched_candidates 상위 10
            _watch = list({
                str(c.get("ticker", "")) for c in (gap_candidates[:10] + enriched_candidates[:10])
                if c.get("ticker")
            })
            if _watch:
                subscribe_tickers(_watch)

            stream_ok = get_stream_status().get("active", False)
            if stream_ok:
                for ticker in _watch:
                    sig = get_opening_reversal_signal(ticker)
                    if sig["score"] < 55 or not sig["cascade"]:
                        continue
                    # 기존 후보 중 해당 종목 찾아서 메타 병합
                    base = next(
                        (c for c in gap_candidates if str(c.get("ticker", "")) == ticker),
                        {"ticker": ticker, "name": ticker, "current_price": 0.0,
                         "gap_pct": 0.0, "volume": 0, "signal_score": 0.5,
                         "signal_bias": "neutral", "signal_reasons": [],
                         "rsi": None, "ema_gap_pct": 0.0, "burst_change_pct": 0.0,
                         "overheat_penalty": 0.0, "breakout_count": 0,
                         "is_breakout": False, "sentiment_score": 0.5},
                    )
                    rev_score = round(sig["score"] / 100.0 * 0.85 + 0.1, 3)
                    reversal_candidates.append({
                        **base,
                        "candidate_score": rev_score,
                        "open_reversal": True,
                        "reversal_score": sig["score"],
                        "reversal_cascade": sig["cascade"],
                        "reversal_exhaustion": sig["exhaustion"],
                        "reversal_reversal": sig["reversal"],
                        "price_vs_open_pct": sig["price_vs_open_pct"],
                        "sell_ratio_30": sig["sell_ratio_30"],
                        "sell_ratio_10": sig["sell_ratio_10"],
                        "tick_count": sig["tick_count"],
                    })
                reversal_candidates.sort(key=lambda c: c["reversal_score"], reverse=True)

        # 리버설 후보를 gap_candidates 앞에 삽입 (오픈 시간에만)
        if reversal_candidates:
            existing_tickers = {str(c.get("ticker", "")) for c in gap_candidates}
            for rc in reversal_candidates:
                if str(rc.get("ticker", "")) not in existing_tickers:
                    gap_candidates.insert(0, rc)
            gap_candidates = gap_candidates[:10]

        # ── Path D: 종가 추격 (Close Drive) ─────────────────────────────────
        # 14:50~15:10 KST (05:50~06:10 UTC) 구간에만 작동
        # 당일 강세 유지 + 기관 수급 확인 종목 → 오버나이트 홀딩
        now_utc = datetime.now(timezone.utc)
        _in_close_window = (
            (now_utc.hour == 5 and now_utc.minute >= 50)
            or (now_utc.hour == 6 and now_utc.minute <= 10)
        )
        close_drive_candidates: list[dict] = []
        if _in_close_window and gap_candidates:
            for c in gap_candidates:
                ticker = str(c.get("ticker", "")).strip()
                gap = float(c.get("gap_pct", 0) or 0)
                vol = float(c.get("volume", 0) or 0)
                sig = float(c.get("signal_score", 0) or 0)
                sent = float(c.get("sentiment_score", 0.5) or 0.5)
                rsi = float(c.get("rsi", 0) or 0)
                sd = float(c.get("supply_demand_score", 0.4) or 0.4)
                # 당일 강세 유지 + 거래량 + 신호 + 감성 + 기관 레이더 조건
                if (
                    gap >= 2.0
                    and vol >= 20000
                    and sig >= 0.55
                    and sent >= 0.52
                    and (rsi == 0 or rsi <= 75)
                    and sd >= 0.70   # 기관 레이더에 있어야 함
                ):
                    cd_score = round(gap * 0.025 + sig * 0.38 + sent * 0.20 + sd * 0.15, 2)
                    close_drive_candidates.append({
                        **c,
                        "close_drive": True,
                        "candidate_score": cd_score,
                        "inst_radar": ticker in inst_tickers,
                    })
                if len(close_drive_candidates) >= 3:
                    break
            close_drive_candidates.sort(key=lambda c: c["candidate_score"], reverse=True)

        # ── Path E: 갭 메꾸기 (Gap Fill) ────────────────────────────────────
        # 09:00~10:00 KST (00:00~01:00 UTC): 이유 없이 갭다운한 종목 → 갭 메꾸기 기대
        # 조건: gap_pct -3.0~-0.5% + vol>=15000 + RSI>25 + signal not bearish
        #       + 뉴스 리스크 없음 + 종목 특이 갭 (시장 전체 갭다운이 아닌 경우 우선)
        gap_fill_candidates: list[dict] = []
        if _in_open_window:
            # 시장 평균 갭 계산 (브로드 마켓 갭다운 판단)
            all_gaps = [float(item.get("gap_pct", 0.0) or 0.0) for item in leaders if item.get("ticker")]
            market_avg_gap = sum(all_gaps) / len(all_gaps) if all_gaps else 0.0

            gap_down_items = [
                item for item in leaders
                if -3.0 <= float(item.get("gap_pct", 0.0) or 0.0) <= -0.5
                and str(item.get("ticker", "")).strip()
                and float(item.get("volume", 0) or 0) >= 15000
            ]

            def _fetch_gf(item: dict) -> tuple[dict, list[dict]]:
                return item, get_naver_daily_prices(str(item.get("ticker", "")).strip(), count=42)

            if gap_down_items:
                with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
                    gf_results = list(executor.map(_fetch_gf, gap_down_items))

                for item, candles in gf_results:
                    if len(candles) < 22:
                        continue
                    ticker = str(item.get("ticker", "")).strip()
                    gap_pct = float(item.get("gap_pct", 0.0) or 0.0)
                    vol = float(item.get("volume", 0) or 0)

                    signal = summarize_equity_signal(candles)
                    signal_score = float(signal.get("score", 0.5) or 0.5)
                    signal_bias = str(signal.get("bias", "neutral") or "neutral")
                    rsi_v = signal.get("rsi")

                    if rsi_v is not None and float(rsi_v) < 25:
                        continue  # 극단 과매도 — 펀더멘털 이슈 가능
                    if signal_bias == "bearish":
                        continue  # 기술적 약세 — 갭 메꾸기 가능성 낮음

                    # 뉴스 리스크 필터: 실적/소송/조사 등 이유 있는 갭다운 제외
                    try:
                        news_risk = get_stock_news_risk(ticker)
                        if news_risk.get("has_risk"):
                            continue
                    except Exception:
                        pass

                    # MA20 위에 있는 종목만 (상승 추세 내 조정)
                    pb = summarize_pullback_uptrend_signal(candles)
                    if pb["ma20"] is not None:
                        last_close = float(candles[-1].get("close") or 0)
                        if last_close < pb["ma20"] * 0.97:
                            continue  # MA20 크게 하회 — 추세 훼손

                    # 종목 특이 갭 여부: 시장 평균 갭보다 더 많이 빠졌을 때 우선
                    stock_specific = gap_pct < market_avg_gap - 0.5
                    gf_score = round(
                        0.45
                        + (0.15 if rsi_v is not None and 30 <= float(rsi_v) <= 52 else 0.0)
                        + (0.15 if signal_score >= 0.50 else 0.0)
                        + (0.10 if vol >= 30000 else 0.05 if vol >= 15000 else 0.0)
                        + (0.10 if -1.5 <= gap_pct <= -0.5 else 0.0)
                        + (0.05 if stock_specific else 0.0),
                        2,
                    )
                    gap_fill_candidates.append({
                        **item,
                        "signal_bias": signal_bias,
                        "signal_score": signal_score,
                        "signal_reasons": signal.get("reasons", []),
                        "rsi": rsi_v,
                        "burst_change_pct": float(signal.get("burst_change_pct", 0.0) or 0.0),
                        "ema_gap_pct": float(signal.get("ema_gap_pct", 0.0) or 0.0),
                        "overheat_penalty": 0.0,
                        "candidate_score": gf_score,
                        "is_breakout": False,
                        "breakout_count": 0,
                        "sentiment_score": 0.5,
                        "gap_fill": True,
                        "gap_fill_score": gf_score,
                        "stock_specific_gap": stock_specific,
                        "market_avg_gap": round(market_avg_gap, 2),
                    })
            gap_fill_candidates.sort(key=lambda c: c["gap_fill_score"], reverse=True)

        score = 0.34
        if gap_candidates:
            score += 0.12
        if len(gap_candidates) >= 3:
            score += 0.10
        if avg_gap >= 3.0:
            score += 0.12
        elif avg_gap >= 1.8:
            score += 0.06
        if avg_volume >= 50000:
            score += 0.11
        elif avg_volume >= 12000:
            score += 0.05
        elif avg_volume < 3500 and gap_candidates:
            score -= 0.14
        if avg_signal >= 0.62:
            score += 0.12
        elif avg_signal >= 0.54:
            score += 0.06
        elif avg_signal < 0.44 and gap_candidates:
            score -= 0.08
        if breakout_confirmed_count >= 1:
            score += 0.15
        elif breakout_partial_count >= 1:
            score += 0.08
        # Sentiment boost: top candidates leaning bullish
        if avg_sentiment >= 0.60:
            score += 0.08
        elif avg_sentiment < 0.42:
            score -= 0.06
        score = min(round(score, 2), 0.95)

        inst_radar_count = sum(1 for c in gap_candidates[:5] if c.get("inst_radar", False))

        return AgentResult(
            name=self.name,
            score=max(score, 0.2),
            reason="Full universe scan + sentiment + supply/demand + theme enrichment",
            payload={
                "gap_candidates": gap_candidates[:5],
                "close_drive_candidates": close_drive_candidates[:3],
                "gap_fill_candidates": gap_fill_candidates[:3],
                "pullback_ma_candidates": pullback_ma_candidates[:3],
                "leader_count": len(leaders),
                "universe_size": len(universe),
                "active_gap_count": active_gap_count,
                "breakout_confirmed_count": breakout_confirmed_count,
                "breakout_partial_count": breakout_partial_count,
                "top_focus": gap_candidates[0]["name"] if gap_candidates else None,
                "quality_score": score,
                "avg_gap_pct_top3": avg_gap,
                "avg_volume_top3": avg_volume,
                "avg_signal_score_top3": avg_signal,
                "avg_sentiment_top3": avg_sentiment,
                "inst_radar_count": inst_radar_count,
                "in_close_window": _in_close_window,
                "in_open_window": _in_open_window,
            },
        )

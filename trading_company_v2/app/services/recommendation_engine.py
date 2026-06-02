from __future__ import annotations

from typing import Any


def _is_paused(strategy_id: str) -> bool:
    """Check if a strategy has been auto-paused by the WR monitor."""
    try:
        from app.services.strategy_monitor import is_strategy_paused
        return is_strategy_paused(strategy_id)
    except Exception:
        return False  # fail open — never block entries due to monitor errors


def build_crypto_plan(stance: str, regime: str, payload: dict[str, Any]) -> dict[str, Any]:
    """코인 데스크 추천 — Strategy D (ETH 4H 신고점 돌파) 전용.

    검증된 전략만 진입. 신호 없으면 watchlist_only.
    """
    lead_market = str(payload.get("lead_market", "KRW-BTC") or "KRW-BTC")
    desk_bias = str(payload.get("desk_bias", "balanced") or "balanced")
    reasons = [str(r) for r in (payload.get("reasons", []) or [])]

    # ── 1. 스트레스 레짐 / VIX 패닉 차단 ─────────────────────────────────
    vix_regime = str(payload.get("vix_regime", "") or "")
    us_regime  = str(payload.get("us_regime", "") or "")
    vix_val    = float(payload.get("vix", 0.0) or 0.0)
    if regime == "STRESSED" or vix_regime == "panic":
        block_reason = "Stress regime" if regime == "STRESSED" else f"VIX panic ({vix_val:.1f})"
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": f"{block_reason}. Preserve crypto capital.",
            "symbol": lead_market,
            "candidate_symbols": [],
            "notes": reasons + [f"{block_reason} blocks aggressive crypto entries."],
        }

    # VIX 공포 구간 → 사이즈 제한 플래그
    _vix_fear = vix_regime in ("fear",)
    _us_risk_off = us_regime == "risk_off"

    # ── 2. Strategy D: ETH 4H 신고점 돌파 ─────────────────────────────────
    # 백테스트 검증: Sharpe 2.33, WR 61.1%, MDD -7.08%, n=18 (2025-2026)
    # 조건: BTC EMA200 상승장 + ETH 4H close > 20봉 최고가 + vol ≥ 2.5x + RSI 50-70 + EMA20
    # STRESSED 레짐에서만 차단, TRENDING/RANGING 모두 허용
    if payload.get("eth_4h_breakout") and not _is_paused("crypto.eth_4h_breakout"):
        _vol_4h = float(payload.get("eth_4h_vol_ratio", 0.0) or 0.0)
        _rsi_4h = float(payload.get("eth_4h_rsi", 0.0) or 0.0)
        _btc_bull = bool(payload.get("btc_4h_regime_bull", False))
        _brk_lvl = float(payload.get("eth_4h_breakout_level", 0.0) or 0.0)
        return {
            "action": "probe_longs",
            "size": "1.00x",
            "focus": f"eth_4h_breakout: KRW-ETH 4H 신고점 돌파 vol={_vol_4h:.1f}x RSI={_rsi_4h:.0f}",
            "symbol": "KRW-ETH",
            "candidate_symbols": [],
            "candidate_markets": [],
            "focus_tag": "eth_4h_breakout",
            "strategy_id": "crypto.eth_4h_breakout",
            "entry_profile": "eth_4h_breakout",
            "signal_freshness": 1.0,
            "btc_corr_15m": 0.85,
            "notes": [
                f"4H close > 20봉 최고가 {_brk_lvl:,.0f}원",
                f"vol_ratio {_vol_4h:.2f}x (조건 ≥2.5x) / RSI {_rsi_4h:.1f} (조건 50-70)",
                f"BTC 4H EMA200 레짐: {'상승장 ✓' if _btc_bull else '하락장 ✗'}",
                "백테스트: Sharpe 2.33 / WR 61.1% / MDD -7.08% (n=18, 2025-2026)",
            ],
        }

    # ── 3. Strategy S15: Momentum Breakout (추세 전략, TRENDING 특화) ────────
    # 백테스트: Sharpe 11.27, WR 66.7%, P/L 2.32, MDD -5.1%, n=51 (2026-05-20)
    # 조건: 10일 신고가 + EMA50>EMA200 + close>EMA20 + vol≥1.5x
    # NOTE: 평균회귀 전략과 상호보완 — 시장 상황이 ATH/TRENDING일 때 주로 발동
    if payload.get("momentum_breakout_long") and not _is_paused("crypto.momentum_breakout"):
        _sym  = str(payload.get("momentum_breakout_symbol", "KRW-BTC") or "KRW-BTC")
        _vrat = float(payload.get("momentum_breakout_vol_ratio", 0.0) or 0.0)
        _h10  = float(payload.get("momentum_breakout_high10", 0.0) or 0.0)
        # VIX 공포 구간이면 사이즈 축소; 기본 0.80x (walk-forward MDD -48.7% 리스크 반영)
        _size = "0.40x" if _vix_fear or _us_risk_off else "0.80x"
        return {
            "action": "probe_longs",
            "size": _size,
            "focus": f"momentum_breakout: {_sym} 10일 신고가 돌파 vol={_vrat:.1f}x",
            "symbol": _sym,
            "candidate_symbols": [],
            "candidate_markets": [],
            "focus_tag": "momentum_breakout",
            "strategy_id": "crypto.momentum_breakout",
            "entry_profile": "momentum_breakout",
            "signal_freshness": 1.0,
            "btc_corr_15m": 0.80,
            "notes": [
                f"10일 신고가 돌파: {_h10:,.0f}원 상향",
                f"거래량 {_vrat:.2f}x (조건 ≥1.5x) / EMA50>EMA200 골든크로스",
                "백테스트: Sharpe 11.27 / WR 66.7% / MDD -5.1% (n=51, 2026-05-20)",
            ],
        }

    # ── 3b. Strategy S2: MONGTATA 에어본 (평균회귀) ────────────────────────
    # 백테스트 검증: Sharpe 6.66, WR 50.0%, MDD -9.7% (코인 일봉)
    # 조건: price > EMA200 + close < lower_BB + close < EMA20×0.975
    if payload.get("mongtata_long") and not _is_paused("crypto.mongtata_airborne"):
        _symbol = str(payload.get("mongtata_symbol", "KRW-BTC") or "KRW-BTC")
        _ema20 = float(payload.get("mongtata_ema20", 0.0) or 0.0)
        _lower_bb = float(payload.get("mongtata_lower_bb", 0.0) or 0.0)
        _dev_pct = float(payload.get("mongtata_deviation_pct", 0.0) or 0.0)
        return {
            "action": "probe_longs",
            "size": "0.80x",
            "focus": f"mongtata_airborne: {_symbol} EMA20 대비 {_dev_pct:.1f}% 이탈 (하단 BB 이하)",
            "symbol": _symbol,
            "candidate_symbols": [],
            "candidate_markets": [],
            "focus_tag": "mongtata_airborne",
            "strategy_id": "crypto.mongtata_airborne",
            "entry_profile": "mongtata_airborne",
            "signal_freshness": 1.0,
            "notes": [
                f"close < lower_BB={_lower_bb:,.0f}원 / EMA20={_ema20:,.0f}원",
                f"EMA20 대비 이탈 {_dev_pct:.2f}% (조건 < -2.5%)",
                "EMA200 상승장 레짐 필터 통과",
                "백테스트: Sharpe 6.66 / WR 50.0% / MDD -9.7% (코인 일봉)",
            ],
        }

    # ── 4. Strategy S13: Dual RSI 이중 확인 평균회귀 (S9보다 우선) ─────────
    # 백테스트: Sharpe 7.28, WR 51.2%, MDD -8.7% (RSI2<10 + RSI14<40)
    if (payload.get("dual_rsi_long") and payload.get("rsi2_long")
            and not _is_paused("crypto.dual_rsi")):
        _sym = str(payload.get("rsi2_symbol", "KRW-BTC") or "KRW-BTC")
        _rsi2 = float(payload.get("rsi2_value", 0.0) or 0.0)
        _rsi14 = float(payload.get("dual_rsi_rsi14", 0.0) or 0.0)
        _dev = float(payload.get("rsi2_deviation_pct", 0.0) or 0.0)
        return {
            "action": "probe_longs",
            "size": "0.80x",
            "focus": f"dual_rsi: {_sym} RSI(2)={_rsi2:.1f} RSI(14)={_rsi14:.1f} EMA20 {_dev:.1f}%",
            "symbol": _sym,
            "candidate_symbols": [],
            "candidate_markets": [],
            "focus_tag": "dual_rsi",
            "strategy_id": "crypto.dual_rsi",
            "entry_profile": "dual_rsi",
            "signal_freshness": 1.0,
            "notes": [
                f"RSI(2)={_rsi2:.1f} (조건 <10) + RSI(14)={_rsi14:.1f} (조건 <40)",
                f"EMA20 이탈 {_dev:.2f}% / EMA200 상승장 레짐 통과",
                "백테스트: Sharpe 7.28 / WR 51.2% / MDD -8.7% (2026-05-20)",
            ],
        }

    # ── 4b. Strategy S9: RSI(2) Connors 평균회귀 ─────────────────────────
    # 백테스트: Sharpe 3.06, WR 48.1%, MDD -6.2%
    if payload.get("rsi2_long") and not _is_paused("crypto.rsi2_mean_reversion"):
        _sym = str(payload.get("rsi2_symbol", "KRW-BTC") or "KRW-BTC")
        _rsi2 = float(payload.get("rsi2_value", 0.0) or 0.0)
        _dev = float(payload.get("rsi2_deviation_pct", 0.0) or 0.0)
        return {
            "action": "probe_longs",
            "size": "0.80x",
            "focus": f"rsi2_mean_reversion: {_sym} RSI(2)={_rsi2:.1f} EMA20 대비 {_dev:.1f}%",
            "symbol": _sym,
            "candidate_symbols": [],
            "candidate_markets": [],
            "focus_tag": "rsi2_mean_reversion",
            "strategy_id": "crypto.rsi2_mean_reversion",
            "entry_profile": "rsi2_mean_reversion",
            "signal_freshness": 1.0,
            "notes": [
                f"RSI(2)={_rsi2:.1f} (조건 <10) / EMA20 이탈 {_dev:.2f}%",
                "EMA200 상승장 레짐 통과",
                "백테스트: Sharpe 3.06 / WR 48.1% / MDD -6.2%",
            ],
        }

    # ── 5. Strategy S10: N-Day Consecutive Pullback ────────────────────
    # 백테스트: Sharpe 4.78, WR 55.8%, MDD -7.7%
    if payload.get("nday_long") and not _is_paused("crypto.nday_pullback"):
        _sym = str(payload.get("nday_symbol", "KRW-BTC") or "KRW-BTC")
        _consec = int(payload.get("nday_consec_down", 3) or 3)
        _dev = float(payload.get("nday_deviation_pct", 0.0) or 0.0)
        return {
            "action": "probe_longs",
            "size": "0.80x",
            "focus": f"nday_pullback: {_sym} {_consec}일 연속 하락 EMA5 이탈 {_dev:.1f}%",
            "symbol": _sym,
            "candidate_symbols": [],
            "candidate_markets": [],
            "focus_tag": "nday_pullback",
            "strategy_id": "crypto.nday_pullback",
            "entry_profile": "nday_pullback",
            "signal_freshness": 1.0,
            "notes": [
                f"{_consec}일 연속 하락 / EMA5 이탈 {_dev:.2f}%",
                "EMA200 상승장 레짐 통과",
                "백테스트: Sharpe 4.78 / WR 55.8% / MDD -7.7%",
            ],
        }

    # ── 6. Strategy S17: Bear Market Oversold Bounce (하락장 전용) ─────────
    # 백테스트: Sharpe 10.60, WR 60%, P/L 3.63, MDD -8.9%, n=15 (2022-2026)
    # 조건: RSI(2)<10 + RSI(14)<38 + close<EMA200×0.97 + close<EMA20×0.975 (2026-05-26 완화)
    # 하락장 전용 — EMA200 아래에서만 발동 (상승장 전략과 상호배타)
    # NOTE: panic 레짐은 이미 위에서 차단됨. fear 구간도 0.35x로 추가 축소.
    if payload.get("bear_oversold_long") and not _is_paused("crypto.bear_oversold_bounce"):
        _bo_sym  = str(payload.get("bear_oversold_symbol", "KRW-BTC") or "KRW-BTC")
        _bo_r2   = float(payload.get("bear_oversold_rsi2", 0.0) or 0.0)
        _bo_r14  = float(payload.get("bear_oversold_rsi14", 0.0) or 0.0)
        _bo_gap  = float(payload.get("bear_oversold_ema200_gap_pct", 0.0) or 0.0)
        # 하락장 기본 0.75x (Sh=10.60, WR=60%, MDD=-8.9% — 포트폴리오 최고 품질)
        # VIX fear/US risk_off 구간 0.50x로 축소
        _bo_size = "0.50x" if _vix_fear or _us_risk_off else "0.75x"
        return {
            "action": "probe_longs",
            "size": _bo_size,
            "focus": f"bear_oversold_bounce: {_bo_sym} RSI(2)={_bo_r2:.1f} RSI(14)={_bo_r14:.1f} EMA200 -{_bo_gap:.1f}%",
            "symbol": _bo_sym,
            "candidate_symbols": [],
            "candidate_markets": [],
            "focus_tag": "bear_oversold_bounce",
            "strategy_id": "crypto.bear_oversold_bounce",
            "entry_profile": "bear_oversold_bounce",
            "signal_freshness": 1.0,
            "btc_corr_15m": 0.75,
            "notes": [
                f"RSI(2)={_bo_r2:.1f} (조건 <5) / RSI(14)={_bo_r14:.1f} (조건 <25)",
                f"EMA200 아래 -{_bo_gap:.1f}% / EMA20×0.975 이하 — 하락장 극단 과매도",
                "백테스트: Sharpe 10.60 / WR 60% / P/L 3.63 / MDD -8.9% (n=15, 2022-2026)",
                f"사이즈: {_bo_size} (TP+4% / SL-0.8% / 최대 5일 / VIX fear=0.50x)",
            ],
        }

    # ── 7. 신호 없음 → 관망 ───────────────────────────────────────────────
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": f"No validated crypto signal. BTC bias={desk_bias}.",
        "symbol": lead_market,
        "candidate_symbols": [],
        "notes": reasons[:3] + ["D/S15/S2/S9/S10/S13/S17 not triggered."],
    }


def _entry_quality_score(candidate: dict, payload: dict) -> tuple[float, str]:
    """진입 품질 채점 (0.0~1.0).

    나쁜 진입 최소화 원칙:
      - 약한 신호에는 작게 / 너무 약하면 스킵
      - 이미 많이 오른 종목 추격 금지
      - 거래량 미수반 신호 페널티
      - 시장 환경(VIX·US overnight) 반영

    Returns: (score 0~1, reason 문자열)
    """
    score = 0.50  # 기준선

    # 1. 신호 강도 (desk agent 계산 점수)
    sig = float(candidate.get("signal_score", 0.5) or 0.5)
    cand = float(candidate.get("candidate_score", sig) or sig)
    score += (sig - 0.5) * 0.30    # ±0.15 기여
    score += (cand - 0.5) * 0.20   # ±0.10 기여

    # 2. 거래량 확인 — 볼륨 없으면 신호 신뢰도 낮음
    vr = float(candidate.get("vol_ratio", candidate.get("vol_surge_ratio", 1.0)) or 1.0)
    if vr >= 2.5:
        score += 0.12
    elif vr >= 1.5:
        score += 0.05
    elif vr < 1.2:
        score -= 0.12   # 거래량 미수반 = 페널티

    # 3. 추격 매수 방지 — 이미 많이 오른 종목
    burst = float(candidate.get("burst_change_pct", 0.0) or 0.0)
    if burst > 8.0:
        score -= 0.25   # 오늘 8%+ 상승 종목 추격 금지
    elif burst > 5.0:
        score -= 0.15
    elif burst > 3.0:
        score -= 0.08

    # 4. VIX / 글로벌 리스크
    vix_r = str(payload.get("vix_regime", "") or "")
    if vix_r == "panic":
        score -= 0.25
    elif vix_r == "fear":
        score -= 0.12

    # 5. US 전날 밤 방향 (SPY 기준)
    spy = float(payload.get("spy_chg", 0.0) or 0.0)
    if spy < -1.5:
        score -= 0.10
    elif spy > 1.0:
        score += 0.05

    # 6. 뉴스 catalyst (있으면 부스트, 악재면 패널티)
    cat = int(candidate.get("catalyst_rating", 5) or 5)
    if cat >= 8:
        score += 0.08
    elif cat <= 2:
        score -= 0.15

    score = round(max(0.0, min(1.0, score)), 3)
    reasons = []
    if vr < 1.2: reasons.append(f"weak_vol({vr:.1f}x)")
    if burst > 3.0: reasons.append(f"chase+{burst:.0f}%")
    if vix_r in ("fear","panic"): reasons.append(f"vix_{vix_r}")
    if spy < -1.5: reasons.append(f"US_dn{spy:.1f}%")
    if cat <= 2: reasons.append("bad_news")
    return score, "|".join(reasons) if reasons else "ok"


def _quality_size(base_size: str, score: float) -> str:
    """진입 품질 점수에 따라 포지션 사이즈 조절."""
    try:
        notional = float(base_size.replace("x", ""))
    except ValueError:
        return base_size
    if score >= 0.70:
        multiplier = 1.0      # 고품질: 정상 사이즈
    elif score >= 0.55:
        multiplier = 0.70     # 중품질: 70%
    else:
        multiplier = 0.45     # 저품질: 45% (진입하되 최소화)
    return f"{round(notional * multiplier, 2):.2f}x"


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """한국 주식 데스크 추천 — 품질 게이트 + 레짐 조건부 전략 (2026-06-02).

    핵심 원칙: 나쁜 진입 최소화
      - 진입 품질 점수(0~1) 계산 → 0.38 미만이면 스킵
      - 0.38~0.55: 45% 사이즈로 소량 진입
      - 0.55 이상: 정상 사이즈

    레짐별 전략:
      RANGING : S13/S9(평균회귀) + S2(볼린저반등) + S18/S19/S20/S23
      TRENDING: 위 + B(신고점) + S15(갭모멘텀) 추가 활성
    """
    # ── 최소 진입 품질 기준 ────────────────────────────────────────────────
    _MIN_QUALITY = 0.38   # 이 미만이면 스킵

    breakout_candidates = payload.get("new_high_breakout_candidates", []) or []
    breakout_confirmed_count = int(payload.get("breakout_confirmed_count", 0) or 0)
    breakout_partial_count = int(payload.get("breakout_partial_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)

    # S15 gap momentum / S18/S19 inst_foreign / S20 catalyst_gap / S22 120d breakout / S23 pre_gap
    gap_momentum_candidates = payload.get("gap_momentum_candidates", []) or []
    inst_foreign_candidates = payload.get("inst_foreign_candidates", []) or []
    catalyst_gap_candidates = payload.get("catalyst_gap_candidates", []) or []
    breakout_120d_candidates = payload.get("breakout_120d_candidates", []) or []
    pre_gap_watch_candidates = payload.get("pre_gap_watch_candidates", []) or []

    # Best candidate (confirmed ≥ 3 conditions)
    bk_leader = next(
        (c for c in breakout_candidates if int(c.get("breakout_count", 0) or 0) >= 3),
        None,
    )
    candidate_symbols = [
        str(c.get("ticker", "")).strip()
        for c in breakout_candidates
        if str(c.get("ticker", "")).strip()
    ]
    # 종목별 현재가 맵 — execution_agent에서 symbol rotation 시 올바른 가격 사용 보장
    # breakout + gap_momentum + inst_foreign + catalyst_gap 모두 포함
    _bk_candidate_prices: dict[str, float] = {
        str(c.get("ticker", "")): float(c.get("current_price", 0.0) or 0.0)
        for c in (breakout_candidates + gap_momentum_candidates + inst_foreign_candidates + catalyst_gap_candidates + breakout_120d_candidates)
        if c.get("ticker") and float(c.get("current_price", 0.0) or 0.0) > 0
    }

    # ── 1. 장외 시간 ────────────────────────────────────────────────────────
    if not (session.get("korea_opening_window") or session.get("korea_mid_session")):
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "Korea desk is outside market hours.",
            "symbol": candidate_symbols[0] if candidate_symbols else "",
            "candidate_symbols": candidate_symbols,
            "notes": ["Scan next open for new_high_breakout candidates."],
            "quality_score": quality_score,
        }

    # ── 1b. US 컨텍스트 (Korea plan 내 공통 참조) ──────────────────────────
    _k_vix_regime = str(payload.get("vix_regime", "") or "")
    _k_us_regime  = str(payload.get("us_regime", "") or "")
    _k_vix_val    = float(payload.get("vix", 0.0) or 0.0)
    _k_spy_chg    = float(payload.get("spy_chg", 0.0) or 0.0)
    _k_vix_fear   = _k_vix_regime in ("fear", "panic")
    _k_us_risk_off = _k_us_regime == "risk_off"

    # ── 1c. 글로벌 뉴스 패닉 체크 ──────────────────────────────────────────
    # 트럼프 관세·연준 서프라이즈·시장 붕괴 뉴스 감지 시 신규 진입 차단
    _news_blocked = False
    _news_block_reason = ""
    try:
        from app.services.global_news_intel import is_entry_blocked_by_news
        _news_blocked, _news_block_reason = is_entry_blocked_by_news()
    except Exception:
        pass
    if _news_blocked:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": f"뉴스 패닉 차단: {_news_block_reason}",
            "symbol": candidate_symbols[0] if candidate_symbols else "",
            "candidate_symbols": candidate_symbols,
            "notes": [_news_block_reason, "글로벌 뉴스 패닉 감지 — 신규 진입 일시 중단"],
            "quality_score": quality_score,
        }

    # ── 2. 스트레스 레짐 차단 ──────────────────────────────────────────────
    if regime == "STRESSED" or _k_vix_regime == "panic":
        _k_block = "Stress regime" if regime == "STRESSED" else f"VIX panic ({_k_vix_val:.1f})"
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": f"{_k_block}. No new Korea stock exposure.",
            "symbol": candidate_symbols[0] if candidate_symbols else "",
            "candidate_symbols": candidate_symbols,
            "notes": [f"Risk committee blocked fresh Korea entries in {_k_block} mode."],
            "quality_score": quality_score,
        }

    # ── 2b. Strategy S23: Pre-Market Macro/News Catalyst ──────────────────────
    # 트럼프/머스크 발언, US 밤사이 급등, 종목 뉴스 급증을 조합한 선행 포착
    # 갭이 발생하기 전 장 초반에 진입 — S20(post-gap)보다 1단계 앞선 예측형 전략
    # NAVER +26.7% 같은 케이스를 사전 포착하기 위한 매크로-뉴스 드리븐 전략
    if pre_gap_watch_candidates and stance != "DEFENSE" and not _is_paused("korea.pre_gap_watch"):
        _pg = pre_gap_watch_candidates[0]
        _pg_ticker = str(_pg.get("ticker", ""))
        _pg_name = str(_pg.get("name", _pg_ticker))
        _pg_price = float(_pg.get("current_price", 0.0) or 0.0)
        _pg_syms = [str(c.get("ticker", "")) for c in pre_gap_watch_candidates if c.get("ticker")]
        _pg_macro = float(_pg.get("macro_boost", 0.0) or 0.0)
        _pg_cat = int(_pg.get("catalyst_rating", 0) or 0)
        # 장전 watchlist이므로 진입 전 시가 확인 필요 → selective_probe (작은 사이즈)
        _pg_base_size = "0.35x" if stance == "OFFENSE" else "0.25x"
        _pg_size = "0.15x" if (_k_vix_fear or _k_us_risk_off) else _pg_base_size
        _pg_headlines = (list(_pg.get("headlines", []) or []))[:2]
        return {
            "action": "selective_probe",
            "size": _pg_size,
            "focus": f"pre_gap_watch: {_pg_name} 매크로부스트={_pg_macro:.2f} 뉴스={_pg_cat}점",
            "symbol": _pg_ticker,
            "reference_price": _pg_price if _pg_price > 0 else None,
            "candidate_symbols": _pg_syms[:3],
            "focus_tag": "pre_gap_watch",
            "strategy_id": "korea.pre_gap_watch",
            "entry_profile": "pre_gap_watch",
            "notes": [
                f"매크로 부스트 {_pg_macro:.2f} / 뉴스 catalyst {_pg_cat}점 / 종토방 hot={_pg.get('jongto_hot', False)}",
                f"주요 헤드라인: {' | '.join(_pg_headlines) if _pg_headlines else '뉴스 감지됨'}",
                f"총 {len(pre_gap_watch_candidates)}개 종목 사전 포착",
                "S23: 갭 전 선행 포착 — 장 초반 시가 확인 후 추가 진입 검토",
            ],
            "quality_score": quality_score,
        }

    # ── 3. Strategy S18/S19: 기관+외국인 동시 매수 (신고점/갭 동반) ─────────
    # S18: inst_foreign_breakout — 신고점 돌파 + 기관 레이더 + 외국인 순매수
    # S19: inst_foreign_gap     — 갭 모멘텀 + 기관 레이더 + 외국인 순매수
    # 스마트머니 동반 → 가장 높은 우선순위 (기술+수급 이중 확인)
    if inst_foreign_candidates and stance != "DEFENSE" and not _is_paused("korea.inst_foreign"):
        _if = inst_foreign_candidates[0]
        _if_ticker = str(_if.get("ticker", ""))
        _if_name = str(_if.get("name", _if_ticker))
        _if_tag = str(_if.get("focus_tag", "inst_foreign"))
        _if_price = float(_if.get("current_price", 0.0) or 0.0)
        _if_syms = [str(c.get("ticker", "")) for c in inst_foreign_candidates if c.get("ticker")]
        _if_base_size = "0.60x" if stance == "OFFENSE" else "0.45x"
        _if_size = "0.30x" if (_k_vix_fear or _k_us_risk_off) else _if_base_size
        return {
            "action": "probe_longs",
            "size": _if_size,
            "focus": f"inst_foreign: {_if_name} 기관+외국인 동시 매수 ({_if_tag})",
            "symbol": _if_ticker,
            "reference_price": _if_price,
            "candidate_prices": _bk_candidate_prices,
            "candidate_symbols": _if_syms[:3],
            "focus_tag": _if_tag,
            "strategy_id": "korea.inst_foreign",
            "entry_profile": "inst_foreign",
            "notes": [
                f"기관 레이더 + 외국인 순매수 동시 확인 / 총 {len(inst_foreign_candidates)}종목",
                f"vol_ratio {_if.get('vol_ratio', _if.get('vol_surge_ratio', 0)):.1f}x / rsi={_if.get('rsi', _if.get('rsi14', 'n/a'))}",
                f"gap_pct={_if.get('gap_pct', 0):.1f}% chg1d={_if.get('chg1d', 0):.1f}%",
                "S18/S19: 스마트머니(기관+외국인) 동반 진입 — 최고 우선순위",
            ],
            "quality_score": quality_score,
        }

    # ── 3b. Strategy S22: 120일 신고가 ── 비활성화 (2026-06-02 백테스트: P&L 0.93, 포워드 테스트로 전환)
    if False and breakout_120d_candidates and stance != "DEFENSE" and not _is_paused("korea.breakout_120d"):
        _b120 = breakout_120d_candidates[0]
        _b120_ticker = str(_b120.get("ticker", ""))
        _b120_name = str(_b120.get("name", _b120_ticker))
        _b120_price = float(_b120.get("current_price", 0.0) or 0.0)
        _b120_syms = [str(c.get("ticker", "")) for c in breakout_120d_candidates if c.get("ticker")]
        _b120_base_size = "0.60x" if stance == "OFFENSE" else "0.45x"
        _b120_size = "0.30x" if (_k_vix_fear or _k_us_risk_off) else _b120_base_size
        return {
            "action": "probe_longs",
            "size": _b120_size,
            "focus": f"breakout_120d: {_b120_name} 120일 신고가 돌파 {_b120.get('breakout_pct', 0):.1f}%",
            "symbol": _b120_ticker,
            "reference_price": _b120_price,
            "candidate_prices": _bk_candidate_prices,
            "candidate_symbols": _b120_syms[:3],
            "focus_tag": "breakout_120d",
            "strategy_id": "korea.breakout_120d",
            "entry_profile": "breakout_120d",
            "notes": [
                f"120일 신고가 {_b120.get('high_120d', 0):,.0f}원 → 현재 {_b120_price:,.0f}원 (+{_b120.get('breakout_pct', 0):.2f}%)",
                f"EMA20={_b120.get('ema20', 0):,.0f} / RSI={_b120.get('rsi14', 'n/a')} / vol={_b120.get('vol_ratio', 0):.1f}x",
                f"총 {len(breakout_120d_candidates)}개 종목 신호 — 포워드 테스트 중",
                "B(20일) 대비 기간 6배 → false positive 감소, 더 강한 추세 신호",
            ],
            "quality_score": quality_score,
        }

    # ── 4. Strategy B: 20일 신고점 돌파 — 레짐 조건부 활성화 ─────────────────
    # 백테스트 결론: RANGING/STRESSED에서는 모멘텀 불리 (MDD -31%)
    #               TRENDING/BULLISH에서는 모멘텀 유효 → 레짐별 분리 적용
    # live 실적: P&L 1.42 (backtest 0.72보다 양호, n=15 소표본)
    _b_regime_ok = regime not in {"RANGING", "STRESSED"}  # TRENDING/BULLISH만 허용
    if _b_regime_ok and breakout_confirmed_count >= 1 and bk_leader and stance != "DEFENSE" and not _is_paused("korea.new_high_breakout"):
        bk_ticker = str(bk_leader.get("ticker", ""))
        bk_name = str(bk_leader.get("name", bk_ticker))
        bk_score = float(bk_leader.get("candidate_score", 0.0) or 0.0)
        bk_bias = str(bk_leader.get("signal_bias", "neutral") or "neutral").lower()
        bk_signal = float(bk_leader.get("signal_score", 0.0) or 0.0)
        # 현재 bullish 확인 필수 (과거 돌파만으로는 추세 반전 가능)
        if bk_bias != "bullish" or bk_signal < 0.50:
            return {
                "action": "stand_by",
                "size": "0.00x",
                "focus": f"Breakout confirmed ({bk_name}) but signal not bullish — skip.",
                "symbol": bk_ticker,
                "candidate_symbols": candidate_symbols,
                "notes": [
                    f"bias={bk_bias} / signal={bk_signal:.2f} — requires bullish+0.50",
                    "Daily breakout without current bullish momentum is a reversal trap.",
                ],
                "quality_score": quality_score,
            }
        # 진입 품질 게이트
        _bk_quality, _bk_qreason = _entry_quality_score(bk_leader, payload)
        if _bk_quality < _MIN_QUALITY:
            return {"action": "stand_by", "size": "0.00x",
                    "focus": f"B confirmed: quality {_bk_quality:.2f} < {_MIN_QUALITY} ({_bk_qreason}) — skip",
                    "candidate_symbols": candidate_symbols, "quality_score": quality_score}
        # VIX fear/US risk-off이면 사이즈 한 단계 축소
        _bk_base_size = "0.70x" if stance == "OFFENSE" else "0.50x"
        _bk_raw_size = "0.35x" if (_k_vix_fear or _k_us_risk_off) else _bk_base_size
        _bk_size = _quality_size(_bk_raw_size, _bk_quality)
        _us_note = f"US {_k_us_regime} / VIX={_k_vix_val:.1f}({_k_vix_regime}) SPY{_k_spy_chg:+.1f}%"
        _bk_price = float(bk_leader.get("current_price", 0.0) or 0.0)
        return {
            "action": "probe_longs",
            "size": _bk_size,
            "focus": f"new_high_breakout: {bk_name} 60일 신고점 돌파 확인",
            "symbol": bk_ticker,
            "reference_price": _bk_price,  # execution_agent 폴백용 (primary 종목 전용)
            "candidate_prices": _bk_candidate_prices,  # rotation 시 종목별 가격 조회용
            "candidate_symbols": candidate_symbols[:3],
            "focus_tag": "new_high_breakout",
            "strategy_id": "korea.new_high_breakout",
            "entry_profile": "new_high_breakout",
            "notes": [
                f"confirmed {breakout_confirmed_count}종목 / score {bk_score:.2f}",
                f"vol_ratio {bk_leader.get('vol_ratio', 0):.1f}x / RSI {bk_leader.get('rsi', 'n/a')}",
                "백테스트(기본 20일): Sharpe 2.73 / WR 32.9% / PF 1.86 — S18 필터 적용시 WR 84.6%",
                _us_note,
            ] + list(bk_leader.get("breakout_reasons", []))[:2],
            "quality_score": quality_score,
        }

    # ── 4. Strategy B partial — TRENDING에서만 허용 (RANGING/STRESSED 제외)
    if _b_regime_ok and breakout_partial_count >= 1 and bk_leader and stance != "DEFENSE" and not _is_paused("korea.new_high_breakout"):
        bk_ticker = str(bk_leader.get("ticker", ""))
        bk_name = str(bk_leader.get("name", bk_ticker))
        bk_bias = str(bk_leader.get("signal_bias", "neutral") or "neutral").lower()
        bk_signal = float(bk_leader.get("signal_score", 0.0) or 0.0)
        if bk_bias != "bullish" or bk_signal < 0.52:
            return {
                "action": "stand_by",
                "size": "0.00x",
                "focus": f"Breakout partial ({bk_name}) but signal not bullish — skip.",
                "symbol": bk_ticker,
                "candidate_symbols": candidate_symbols,
                "notes": [f"bias={bk_bias} / signal={bk_signal:.2f} — requires bullish+0.52"],
                "quality_score": quality_score,
            }
        _bk_price2 = float(bk_leader.get("current_price", 0.0) or 0.0)
        return {
            "action": "selective_probe",
            "size": "0.35x",
            "focus": f"new_high_breakout partial: {bk_name} — 3/3 signals confirmed.",
            "symbol": bk_ticker,
            "reference_price": _bk_price2,  # execution_agent 폴백용 (primary 종목 전용)
            "candidate_prices": _bk_candidate_prices,  # rotation 시 종목별 가격 조회용
            "candidate_symbols": [bk_ticker],
            "focus_tag": "new_high_breakout",
            "strategy_id": "korea.new_high_breakout",
            "entry_profile": "new_high_breakout",
            "notes": list(bk_leader.get("breakout_reasons", []))[:3],
            "quality_score": quality_score,
        }

    # ── 5. Strategy S2: MONGTATA — RANGING에서 평균회귀 용도로 허용 (사이즈 0.25x 축소)
    # 백테스트 구조 문제(-5% stop)는 유지하되 사이즈 축소로 손실 제한
    # RANGING: 볼린저 하단 반등 패턴 유효, WR 84.5% 활용
    mongtata_candidates = payload.get("mongtata_airborne_candidates", []) or []
    if mongtata_candidates and stance != "DEFENSE" and not _is_paused("korea.mongtata_airborne"):
        mt_leader = mongtata_candidates[0]
        mt_ticker = str(mt_leader.get("ticker", ""))
        mt_name = str(mt_leader.get("name", mt_ticker))
        mt_dev = float(mt_leader.get("deviation_pct", 0.0) or 0.0)
        mt_ema20 = float(mt_leader.get("ema20", 0.0) or 0.0)
        mt_lower_bb = float(mt_leader.get("lower_bb", 0.0) or 0.0)
        mt_price = float(mt_leader.get("current_price", 0.0) or 0.0)
        mt_symbols = [str(c.get("ticker", "")).strip() for c in mongtata_candidates if c.get("ticker")]
        return {
            "action": "probe_longs",
            "size": "0.25x",  # stop -5% 리스크 → 사이즈 절반으로 손실 제한
            "focus": f"mongtata_airborne: {mt_name} EMA20 대비 {mt_dev:.1f}% 이탈",
            "symbol": mt_ticker,
            "reference_price": mt_price,
            "candidate_symbols": mt_symbols[:3],
            "focus_tag": "mongtata_airborne",
            "strategy_id": "korea.mongtata_airborne",
            "entry_profile": "mongtata_airborne",
            "notes": [
                f"close < lower_BB={mt_lower_bb:,.0f}원 / EMA20={mt_ema20:,.0f}원 (이탈 {mt_dev:.2f}%)",
                f"총 {len(mongtata_candidates)}개 종목 신호 발생",
                "EMA200 상승장 레짐 필터 통과",
                "백테스트: Sharpe 8.60 / WR 56.5% / MDD -5.9%",
            ],
            "quality_score": quality_score,
        }

    # ── 6. Strategy S13/S9: RSI 평균회귀 ─────────────────────────────────────
    # S13(Dual RSI, 백테스트 PASS) → S9(RSI2, WATCH) 순으로 시도
    # 평균회귀는 RSI 극단 자체가 품질 신호 → _MIN_QUALITY 기준 약간 완화 (0.32)
    _MR_MIN_QUALITY = 0.32
    rsi2_candidates = payload.get("rsi2_candidates", []) or []
    dual_rsi_candidates = [c for c in rsi2_candidates if c.get("dual_rsi")]

    for _mr_cands, _mr_size, _mr_strat, _mr_prof, _mr_note in [
        (dual_rsi_candidates, "0.50x", "korea.dual_rsi", "dual_rsi",
         "백테스트 PASS: WR 47.1% / P&L 1.58 / Sharpe 1.55 / MDD -2.1%"),
        (rsi2_candidates, "0.30x", "korea.rsi2_mean_reversion", "rsi2_mean_reversion",
         "백테스트 WATCH: OOS Sharpe 3.13 / IS P&L 1.44"),
    ]:
        if not _mr_cands:
            continue
        if _is_paused(_mr_strat) or stance == "DEFENSE":
            continue
        r = _mr_cands[0]
        r_syms = [c.get("ticker", "") for c in _mr_cands if c.get("ticker")]
        _mr_quality, _mr_qr = _entry_quality_score(r, payload)
        if _mr_quality < _MR_MIN_QUALITY:
            continue  # 이 후보 스킵, 다음 전략으로
        _final_size = _quality_size(_mr_size, _mr_quality)
        _mr_rsi2 = r.get("rsi2", 0)
        _mr_rsi14 = r.get("rsi14", 0)
        _mr_dev = r.get("deviation_pct", 0)
        return {
            "action": "probe_longs",
            "size": _final_size,
            "focus": f"{_mr_prof}: {r.get('name', r.get('ticker',''))} RSI(2)={_mr_rsi2:.1f} RSI(14)={_mr_rsi14:.1f}",
            "symbol": r.get("ticker", ""),
            "reference_price": float(r.get("current_price", 0.0) or 0.0),
            "candidate_symbols": r_syms[:3],
            "focus_tag": _mr_prof,
            "strategy_id": _mr_strat,
            "entry_profile": _mr_prof,
            "notes": [
                f"RSI(2)={_mr_rsi2:.1f} / RSI(14)={_mr_rsi14:.1f} / EMA20 이탈 {_mr_dev:.2f}%",
                f"진입품질 {_mr_quality:.2f} ({_mr_qr or 'ok'}) / 총 {len(_mr_cands)}개 신호",
                _mr_note,
            ],
            "quality_score": quality_score,
        }

    # ── 6b (placeholder — S9 already handled in loop above) ──────────────
    if False:
        r = rsi2_candidates[0] if rsi2_candidates else {}
        r_syms = []
        return {
            "action": "probe_longs",
            "size": "0.30x",
            "focus": f"rsi2_mean_reversion: {r.get('name', r.get('ticker', ''))} RSI(2)={r.get('rsi2', 0):.1f}",
            "symbol": r.get("ticker", ""),
            "reference_price": float(r.get("current_price", 0.0) or 0.0),
            "candidate_symbols": r_syms[:3],
            "focus_tag": "rsi2_mean_reversion",
            "strategy_id": "korea.rsi2_mean_reversion",
            "entry_profile": "rsi2_mean_reversion",
            "notes": [
                f"RSI(2)={r.get('rsi2', 0):.1f} / EMA20 이탈 {r.get('deviation_pct', 0):.2f}%",
                f"총 {len(rsi2_candidates)}개 신호",
                "백테스트 WATCH: OOS Sharpe 3.13 / IS P&L 1.44 (기준 1.5 근접) — 0.30x 관찰 모드",
            ],
            "quality_score": quality_score,
        }

    # ── 8. Strategy S15: Gap Momentum — TRENDING에서만, 품질 게이트
    if _b_regime_ok and gap_momentum_candidates and stance != "DEFENSE" and not _is_paused("korea.gap_momentum"):
        _gm = gap_momentum_candidates[0]
        _gm_ticker = str(_gm.get("ticker", ""))
        _gm_name = str(_gm.get("name", _gm_ticker))
        _gm_price = float(_gm.get("current_price", 0.0) or 0.0)
        _gm_syms = [str(c.get("ticker", "")) for c in gap_momentum_candidates if c.get("ticker")]
        _gm_quality, _gm_qr = _entry_quality_score(_gm, payload)
        if _gm_quality < _MIN_QUALITY:
            pass  # 품질 미달 → S20으로 넘어감
        else:
            _gm_base_size = "0.40x" if stance == "OFFENSE" else "0.30x"
            _gm_size = _quality_size("0.20x" if (_k_vix_fear or _k_us_risk_off) else _gm_base_size, _gm_quality)
            return {
            "action": "probe_longs",
            "size": _gm_size,
                "focus": f"gap_momentum: {_gm_name} 갭업+추세 지속 vol={_gm.get('vol_ratio',0):.1f}x",
                "symbol": _gm_ticker,
                "reference_price": _gm_price,
                "candidate_prices": _bk_candidate_prices,
                "candidate_symbols": _gm_syms[:3],
                "focus_tag": "gap_momentum",
                "strategy_id": "korea.gap_momentum",
                "entry_profile": "gap_momentum",
                "notes": [
                    f"gap={_gm.get('gap_pct',0):.1f}% / chg1d={_gm.get('chg1d',0):.1f}% / vol={_gm.get('vol_ratio',0):.1f}x",
                    f"품질 {_gm_quality:.2f} / RSI(14)={_gm.get('rsi14','n/a')} / str={_gm.get('close_strength',0):.2f}",
                    f"총 {len(gap_momentum_candidates)}종목",
                ],
                "quality_score": quality_score,
            }

    # ── 9. Strategy S20: Catalyst Gap (강한 갭업 촉매 모멘텀) ────────────────
    # 2026-06-01 신설 — NAVER(+26.7%), 현대차(+6.6%), 삼성전자우(+14.1%) 미포착 교훈
    # EMA 정배열 없어도 진입 — gap>=5% + vol>=2.5x + close_strength>=0.70 + EMA200 위
    # 포워드 테스트 단계: 사이즈 작게(0.25x~0.35x), 백테스트 누적 후 상향 예정
    if catalyst_gap_candidates and stance != "DEFENSE" and not _is_paused("korea.catalyst_gap"):
        _cg = catalyst_gap_candidates[0]
        _cg_ticker = str(_cg.get("ticker", ""))
        _cg_name = str(_cg.get("name", _cg_ticker))
        _cg_price = float(_cg.get("current_price", 0.0) or 0.0)
        _cg_syms = [str(c.get("ticker", "")) for c in catalyst_gap_candidates if c.get("ticker")]
        # 촉매 갭은 포워드 테스트 — 사이즈 보수적 설정
        _cg_base_size = "0.35x" if stance == "OFFENSE" else "0.25x"
        _cg_size = "0.15x" if (_k_vix_fear or _k_us_risk_off) else _cg_base_size
        return {
            "action": "probe_longs",
            "size": _cg_size,
            "focus": f"catalyst_gap: {_cg_name} 강한 갭업+촉매 gap={_cg.get('gap_pct',0):.1f}% chg={_cg.get('chg1d',0):.1f}%",
            "symbol": _cg_ticker,
            "reference_price": _cg_price,
            "candidate_prices": _bk_candidate_prices,
            "candidate_symbols": _cg_syms[:3],
            "focus_tag": "catalyst_gap",
            "strategy_id": "korea.catalyst_gap",
            "entry_profile": "catalyst_gap",
            "notes": [
                f"gap={_cg.get('gap_pct',0):.1f}% / chg1d={_cg.get('chg1d',0):.1f}% / vol={_cg.get('vol_ratio',0):.1f}x",
                f"close_strength={_cg.get('close_strength',0):.2f} / 전일RSI={_cg.get('pre_gap_rsi14','n/a')}",
                f"EMA200={_cg.get('ema200',0):,.0f} 위 거래 (EMA 정배열 불요)",
                f"총 {len(catalyst_gap_candidates)}종목 / 포워드 테스트 — 사이즈 {_cg_size}",
            ],
            "quality_score": quality_score,
        }

    # ── 10. 신호 없음 → 관망 ──────────────────────────────────────────────
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No signal. B/S15/S18/S2/S9/S10/S20 not triggered.",
        "symbol": candidate_symbols[0] if candidate_symbols else "",
        "candidate_symbols": candidate_symbols,
        "notes": [
            f"B={breakout_confirmed_count}c/{breakout_partial_count}p S15={len(gap_momentum_candidates)} S18/19={len(inst_foreign_candidates)} S20={len(catalyst_gap_candidates)} S2={len(mongtata_candidates)} S9/S13={len(rsi2_candidates)}(dual={len(dual_rsi_candidates)}) S10={len(nday_candidates)}",
            "Waiting for B/S15/S18/S19/S2/S9/S10/S13/S20 signal.",
        ],
        "quality_score": quality_score,
    }


def build_us_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_us_count = int(payload.get("active_us_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_change = float(payload.get("avg_change_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    leaders = payload.get("leaders", []) or []
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in leaders if str(item.get("ticker", "")).strip()]
    top_ticker = candidate_symbols[0] if candidate_symbols else ""
    top_signal = float(leaders[0].get("signal_score", 0.0) or 0.0) if leaders else 0.0
    top_change = float(leaders[0].get("change_pct", 0.0) or 0.0) if leaders else 0.0
    _qmeta = {"quality_score": quality_score, "avg_signal": avg_signal, "quality_threshold": 0.72}

    if not (session.get("us_premarket") or session.get("us_regular")):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "US desk is outside session hours.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Review leaders again during pre-market or regular hours."],
            **_qmeta,
        }
    if session.get("us_premarket") and not session.get("us_regular"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": f"{top_ticker or 'US leaders'} pre-market watch only.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Wait for regular session confirmation before entering US names."],
            **_qmeta,
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Stress regime. No new US equity exposure.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Risk committee blocked fresh US entries in stress mode."],
            **_qmeta,
        }
    if leaders and (top_signal < 0.56 or top_change >= 8.5):
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"US leader {top_ticker} is overheated or under-confirmed.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"top signal {top_signal:.2f} / top change {top_change:.2f}%",
                "Wait for cleaner regular-session follow-through.",
            ],
            **_qmeta,
        }
    if quality_score < 0.62 or avg_signal < 0.52 or active_us_count < 2 or avg_change < 0.20:
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "US momentum quality is still too weak.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"quality {quality_score:.2f} / active leaders {active_us_count} / avg change {avg_change:.2f}% / avg signal {avg_signal:.2f}",
                "US entries need a stronger regular-session backdrop.",
            ],
            **_qmeta,
        }
    if active_us_count >= 4 and quality_score >= 0.76 and avg_change >= 0.55 and avg_volume >= 2000000 and avg_signal >= 0.66 and stance != "DEFENSE":
        return {
            "action": "probe_longs",
            "size": "0.25x" if stance == "BALANCED" else "0.40x",
            "focus": f"US leader follow-through on {top_ticker}.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"leaders in force {active_us_count}",
                f"quality {quality_score:.2f} / avg change {avg_change:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
            **_qmeta,
        }
    if active_us_count >= 3 and quality_score >= 0.70 and avg_signal >= 0.60:
        return {
            "action": "selective_probe",
            "size": "0.15x",
            "focus": f"{top_ticker} selective probe watch.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"watchlist leaders {active_us_count}",
                f"quality {quality_score:.2f} / avg change {avg_change:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
            ],
            **_qmeta,
        }
    # 2-leader fallback — small size, tighter individual stock requirement
    if active_us_count >= 2 and quality_score >= 0.64 and avg_signal >= 0.54 and top_signal >= 0.60 and stance != "DEFENSE":
        return {
            "action": "selective_probe",
            "size": "0.10x",
            "focus": f"{top_ticker} cautious 2-leader probe.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"2-leader setup / quality {quality_score:.2f} / avg signal {avg_signal:.2f} / top signal {top_signal:.2f}",
                "Small size — only 2 confirmed leaders in session.",
            ],
            **_qmeta,
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No US leader is strong enough right now.",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"stay selective (quality {quality_score:.2f} / signal {avg_signal:.2f})"],
        **_qmeta,
    }

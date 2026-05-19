from __future__ import annotations

from typing import Any


def build_crypto_plan(stance: str, regime: str, payload: dict[str, Any]) -> dict[str, Any]:
    bias = str(payload.get("desk_bias", "balanced") or "balanced")
    signal_score = float(payload.get("signal_score", 0.5) or 0.5)
    recent_change = float(payload.get("recent_change_pct", 0.0) or 0.0)
    burst_change = float(payload.get("burst_change_pct", 0.0) or 0.0)
    ema_gap = float(payload.get("ema_gap_pct", 0.0) or 0.0)
    rsi_value = payload.get("rsi")
    reasons = [str(item) for item in (payload.get("reasons", []) or [])]
    backtest_weights = payload.get("backtest_weights", {}) or {}
    lead_market = str(payload.get("lead_market", "") or "")
    candidate_symbols = [str(item).strip() for item in (payload.get("candidate_symbols", []) or []) if str(item).strip()]
    all_candidates = [item for item in (payload.get("all_candidates", []) or []) if isinstance(item, dict)]
    def _cf(item: dict[str, Any], key: str, default: float = 0.0) -> float:
        try:
            return float(item.get(key, default) or default)
        except (TypeError, ValueError):
            return default
    def _cb(item: dict[str, Any], key: str) -> bool:
        return bool(item.get(key, False))
    lead_weight = float(backtest_weights.get(lead_market, 0.0) or 0.0)
    discovery_score = float(payload.get("discovery_score", 0.0) or 0.0)
    volume_24h_krw = float(payload.get("volume_24h_krw", 0.0) or 0.0)
    change_rate = float(payload.get("change_rate", 0.0) or 0.0)
    validated_support = lead_weight >= 0.08
    discovery_support = discovery_score >= 0.50 and volume_24h_krw >= 8_000_000_000
    liquidity_support = volume_24h_krw >= 30_000_000_000
    research_support = validated_support or discovery_support or (liquidity_support and signal_score >= 0.58)
    support_note = (
        f"research_support={research_support} / validated={validated_support} / discovery={discovery_score:.2f} "
        f"/ liquidity KRW {int(volume_24h_krw):,} / change {change_rate:.2f}%"
    )
    rsi_quality_ok = bool(payload.get("rsi_quality_ok", True))
    rsi_bearish_divergence = bool(payload.get("rsi_bearish_divergence", False))
    rsi_extreme = bool(payload.get("rsi_extreme", False))
    micro_ready = bool(payload.get("micro_ready", False))
    micro_score = float(payload.get("micro_score", 0.0) or 0.0)
    micro_vol_ratio = float(payload.get("micro_vol_ratio", 0.0) or 0.0)
    micro_move_3 = float(payload.get("micro_move_3_pct", 0.0) or 0.0)
    micro_move_10 = float(payload.get("micro_move_10_pct", 0.0) or 0.0)
    micro_vwap_gap = float(payload.get("micro_vwap_gap_pct", 0.0) or 0.0)
    micro_range_5 = float(payload.get("micro_range_5_pct", 0.0) or 0.0)
    micro_exhausted = bool(payload.get("micro_exhausted", False))
    stream_fresh = bool(payload.get("stream_fresh", False))
    stream_score = float(payload.get("stream_score", 0.0) or 0.0)
    stream_ignition = bool(payload.get("stream_ignition", False))
    stream_reversal = bool(payload.get("stream_reversal", False))
    stream_age = float(payload.get("stream_age_seconds", 999.0) or 999.0)
    stream_move_5 = float(payload.get("stream_move_5s_pct", 0.0) or 0.0)
    stream_move_15 = float(payload.get("stream_move_15s_pct", 0.0) or 0.0)
    stream_move_60 = float(payload.get("stream_move_60s_pct", 0.0) or 0.0)
    stream_ticks_15 = int(payload.get("stream_ticks_15s", 0) or 0)
    stream_buy_ratio = float(payload.get("stream_buy_ratio_15s", 0.0) or 0.0)
    orderbook_ready = bool(payload.get("orderbook_ready", False))
    orderbook_score = float(payload.get("orderbook_score", 0.0) or 0.0)
    orderbook_bid_ask = float(payload.get("orderbook_bid_ask_ratio", 0.0) or 0.0)
    breakout_count = int(payload.get("breakout_count", 0) or 0)
    breakout_score = float(payload.get("breakout_score", 0.0) or 0.0)   # 돌파 품질 수치 (0~0.95)
    vol_ratio = float(payload.get("vol_ratio", 0.0) or 0.0)
    pullback_detected = bool(payload.get("pullback_detected", False))
    pullback_score = float(payload.get("pullback_score", 0.0) or 0.0)
    spike_pct_15m = float(payload.get("spike_pct_15m", 0.0) or 0.0)
    retrace_from_high_pct = float(payload.get("retrace_from_high_pct", 0.0) or 0.0)
    vol_contracted_on_pullback = bool(payload.get("vol_contracted_on_pullback", False))
    # 에어본 / range scalp 필드
    airborne_long = bool(payload.get("airborne_long", False))
    airborne_short = bool(payload.get("airborne_short", False))   # 가격이 EMA 위로 과이격 = 숏 신호 (롱 블록)
    airborne_score = float(payload.get("airborne_score", 0.0) or 0.0)
    airborne_deviation_pct = float(payload.get("airborne_deviation_pct", 0.0) or 0.0)
    airborne_deviation_sigma = float(payload.get("airborne_deviation_sigma", 0.0) or 0.0)
    at_bb_lower = bool(payload.get("at_bb_lower", False))
    at_bb_upper = bool(payload.get("at_bb_upper", False))         # BB 상단 근처 (>80%) = 저항/과열
    bb_pct_b = float(payload.get("bb_pct_b", 0.5) or 0.5)        # 볼린저 밴드 내 위치 (0~1)
    price_above_trend_ema = bool(payload.get("price_above_trend_ema", True))  # 가격 > EMA21 (기본 상승 구조)
    rsi_reset_confirmed = bool(payload.get("rsi_reset_confirmed", False))     # RSI 눌림 후 회복 = 재점화
    range_scalp_eligible = bool(payload.get("range_scalp_eligible", False))
    rsi_mean_rev_long = bool(payload.get("rsi_mean_rev_long", False))
    # RANGING 보조 전략 신호
    bb_squeeze_bounce = bool(payload.get("bb_squeeze_bounce", False))
    vwap_deviation_long = bool(payload.get("vwap_deviation_long", False))
    vwap_deviation_pct = float(payload.get("vwap_deviation_pct", 0.0) or 0.0)
    rsi_extreme_long = bool(payload.get("rsi_extreme_long", False))
    stoch_k = float(payload.get("stoch_k", 50.0) or 50.0)
    stoch_oversold_cross = bool(payload.get("stoch_oversold_cross", False))
    macd_histogram_reversal = bool(payload.get("macd_histogram_reversal", False))
    hammer_candle = bool(payload.get("hammer_candle", False))
    doji_candle = bool(payload.get("doji_candle", False))
    volume_climax_reversal = bool(payload.get("volume_climax_reversal", False))
    volume_climax_ratio = float(payload.get("volume_climax_ratio", 0.0) or 0.0)
    support_reclaim_long = bool(payload.get("support_reclaim_long", False))
    support_level = float(payload.get("support_level", 0.0) or 0.0)
    range_breakout_long = bool(payload.get("range_breakout_long", False))
    range_high_20 = float(payload.get("range_high_20", 0.0) or 0.0)
    range_width_pct = float(payload.get("range_width_pct", 0.0) or 0.0)
    high_tight_flag_long = bool(payload.get("high_tight_flag_long", False))
    impulse_12_pct = float(payload.get("impulse_12_pct", 0.0) or 0.0)
    flag_range_pct = float(payload.get("flag_range_pct", 0.0) or 0.0)
    # Batch 2 RANGING 신호
    williams_r_oversold = bool(payload.get("williams_r_oversold", False))
    williams_r_val = float(payload.get("williams_r", -50.0) or -50.0)
    cci_oversold_bounce = bool(payload.get("cci_oversold_bounce", False))
    cci_val = float(payload.get("cci", 0.0) or 0.0)
    keltner_lower_touch = bool(payload.get("keltner_lower_touch", False))
    mfi_oversold = bool(payload.get("mfi_oversold", False))
    mfi_val = float(payload.get("mfi", 50.0) or 50.0)
    # Batch 2 TRENDING 신호
    ema_cross_long = bool(payload.get("ema_cross_long", False))
    vwap_cross_long = bool(payload.get("vwap_cross_long", False))
    # Batch 3 TRENDING 신호
    rsi_flip_long = bool(payload.get("rsi_flip_long", False))
    macd_bull_cross = bool(payload.get("macd_bull_cross", False))
    triple_candle_bull = bool(payload.get("triple_candle_bull", False))
    # Batch 4 TRENDING/DUAL 신호
    supertrend_long = bool(payload.get("supertrend_long", False))
    engulfing_bull = bool(payload.get("engulfing_bull", False))
    vol_surge_long = bool(payload.get("vol_surge_long", False))
    adx_trend_strong = bool(payload.get("adx_trend_strong", False))
    adx_val = float(payload.get("adx_val", 0.0) or 0.0)
    bb_squeeze_breakout = bool(payload.get("bb_squeeze_breakout", False))
    consecutive_higher_lows = bool(payload.get("consecutive_higher_lows", False))
    # Batch 5 신호
    pin_bar_long = bool(payload.get("pin_bar_long", False))
    morning_star = bool(payload.get("morning_star", False))
    inside_bar_breakout = bool(payload.get("inside_bar_breakout", False))
    rsi_momentum_keep = bool(payload.get("rsi_momentum_keep", False))
    oi_momentum_long = bool(payload.get("oi_momentum_long", False))
    demand_zone_bounce = bool(payload.get("demand_zone_bounce", False))
    # Batch 6 신호
    vwap_rsi_combo = bool(payload.get("vwap_rsi_combo", False))
    breakout_vol_confirm = bool(payload.get("breakout_vol_confirm", False))
    hammer_at_support = bool(payload.get("hammer_at_support", False))
    trend_reversal_early = bool(payload.get("trend_reversal_early", False))
    ema_bounce_long = bool(payload.get("ema_bounce_long", False))
    rsi_bullish_div = bool(payload.get("rsi_bullish_div", False))
    multi_ranging_combo = bool(payload.get("multi_ranging_combo", False))
    momentum_breakout_cont = bool(payload.get("momentum_breakout_cont", False))
    capital_flow_long = bool(payload.get("capital_flow_long", False))
    capital_flow_score = float(payload.get("capital_flow_score", 0.0) or 0.0)
    capital_flow_volume_ratio = float(payload.get("capital_flow_volume_ratio", 0.0) or 0.0)
    auto_trendline_breakout_long = bool(payload.get("auto_trendline_breakout_long", False))
    trendline_break_price = float(payload.get("trendline_break_price", 0.0) or 0.0)
    trendline_slope_pct = float(payload.get("trendline_slope_pct", 0.0) or 0.0)
    flow_box_breakout_long = bool(payload.get("flow_box_breakout_long", False))
    smart_money_flow_long = bool(payload.get("smart_money_flow_long", False))
    trend_follow_score = float(payload.get("trend_follow_score", 0.0) or 0.0)
    trend_alignment = str(payload.get("trend_alignment", "unknown") or "unknown")
    trend_entry_allowed = bool(payload.get("trend_entry_allowed", False))
    trend_slope_pct = float(payload.get("trend_slope_pct", 0.0) or 0.0)
    trend_extension_pct = float(payload.get("trend_extension_pct", 0.0) or 0.0)
    choch_bullish_early = bool(payload.get("choch_bullish", False))
    choch_bearish_early = bool(payload.get("choch_bearish", False))
    bos_bullish_early = bool(payload.get("bos_bullish", False))
    bos_bearish_early = bool(payload.get("bos_bearish", False))
    ema_stack_bullish = bool(payload.get("ema_stack_bullish", False))    # EMA 8>21>34 정배열
    bsl_sweep_confirmed = bool(payload.get("bsl_sweep_confirmed", False)) # 고점 유동성 스윕 후 반락 (매도신호)
    kill_zone_name = payload.get("kill_zone_name")  # "london" / "ny" / None — ignition_note 로깅용 (ICT 블록보다 먼저 필요)
    trend_ignition_score = round(
        min(
            1.0,
            min(max(signal_score, 0.0), 1.0) * 0.24
            + min(max(trend_follow_score, 0.0), 1.0) * 0.22
            + min(max(micro_score, 0.0), 1.0) * 0.20
            + min(max(orderbook_score, 0.0), 1.0) * 0.13
            + min(max(discovery_score, 0.0), 1.0) * 0.07
            + min(max(vol_ratio / 3.0, 0.0), 1.0) * 0.08
            + min(max(stream_score, 0.0), 1.0) * 0.06
            + (0.04 if ema_stack_bullish else 0.0)          # EMA 정배열 보너스
            + min(max(breakout_score, 0.0), 1.0) * 0.05    # 돌파 품질 점수
            + (0.03 if rsi_reset_confirmed else 0.0)         # RSI 눌림 후 재점화 보너스
            + (0.02 if price_above_trend_ema else -0.03)    # EMA21 위: +0.02 / 아래: -0.03
            + max(0.0, (bb_pct_b - 0.5)) * 0.04            # BB 밴드 중앙~상단 위치: 최대 +0.02 (0.5→1.0 선형)

        ),
        3,
    )
    # 김치 프리미엄 신호 — 업비트에서 국내 수요가 높은 코인 우선
    kimchi_premium_pct = float(payload.get("kimchi_premium_pct", 0.0) or 0.0)
    kimchi_score = float(payload.get("kimchi_score", 0.5) or 0.5)
    kimchi_boost = kimchi_premium_pct >= 2.0   # 2% 이상이면 국내 수요 강세
    kimchi_warn  = kimchi_premium_pct <= -2.0  # 역프리미엄이면 주의
    # BTC 급락 반등 신호
    sharp_dip_bounce = bool(payload.get("sharp_dip_bounce", False))
    dip_change_30m = float(payload.get("dip_change_30m", 0.0) or 0.0)
    dip_rsi_btc = payload.get("dip_rsi_btc")
    dip_bounce_symbol: str = str(payload.get("dip_bounce_symbol") or "") or "KRW-BTC"

    flow_support = orderbook_score >= 0.48 or orderbook_bid_ask >= 1.02 or stream_ignition
    launch_confirmed = (
        (micro_score >= 0.55 and micro_vol_ratio >= 1.1)
        or stream_score >= 0.55
        or stream_ignition
        or (breakout_count >= 2 and vol_ratio >= 1.4)
    )
    # research_support removed from ignition_ready: historical backtest weight shouldn't block fresh movers.
    # CryptoDeskAgent already integrated all signals into combined_score — trust it here.
    # 김치프리미엄 역프리미엄(-2% 이하) 시 ignition threshold 소폭 상향 (국내 수요 약함)
    _ignition_threshold = 0.56 if not kimchi_warn else 0.62
    ignition_ready = trend_ignition_score >= _ignition_threshold and flow_support and trend_entry_allowed
    _kz_label = f" [킬존:{kill_zone_name}]" if kill_zone_name else ""
    ignition_note = (
        f"trend_ignition={trend_ignition_score:.2f} / chart={trend_follow_score:.2f} {trend_alignment} "
        f"/ micro={micro_score:.2f} "
        f"/ flow={orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / stream={stream_score:.2f} "
        f"({stream_move_15:.2f}%/15s) / breakout={breakout_count}/4 score={breakout_score:.2f}"
        f" / ema_stack={ema_stack_bullish} bb_pct_b={bb_pct_b:.2f} adx={adx_val:.0f}"
        f" / kimchi={kimchi_premium_pct:+.1f}%{'↑' if kimchi_boost else '↓' if kimchi_warn else ''}"
        f"{_kz_label}"
    )
    trend_note = (
        f"chart trend gate: {trend_alignment} score={trend_follow_score:.2f} "
        f"slope={trend_slope_pct:.2f}% extension={trend_extension_pct:.2f}%"
    )
    # Pullback entry: prior spike + EMA-zone retracement + volume contraction
    # Better entry price and tighter stop than chasing raw momentum.
    # micro_score threshold lowered 0.38→0.28: pullbacks naturally have soft 1m momentum.
    pullback_entry_ok = (
        pullback_detected
        and pullback_score >= 0.55
        and trend_entry_allowed
        and trend_follow_score >= 0.52
        and signal_score >= 0.40
        and micro_score >= 0.28
        and (orderbook_score >= 0.44 or orderbook_bid_ask >= 1.0)
        and not rsi_bearish_divergence
    )
    pullback_note = (
        f"pullback score {pullback_score:.2f} / spike {spike_pct_15m:.1f}% / "
        f"retrace {retrace_from_high_pct:.1f}% / vol contracted: {vol_contracted_on_pullback}"
    )
    # Volume gate: ignition entries need real volume confirmation.
    # Pullback entries intentionally have low current volume (contracting on retracement).
    # High-quality signals (>=0.70) bypass the strict vol gate — edge from signal quality is sufficient.
    _high_quality_signal = (
        signal_score >= 0.70 and micro_score >= 0.46 and orderbook_bid_ask >= 1.0
        and price_above_trend_ema                           # 가격이 EMA21 위 = 기본 상승 구조 필수
        and (ema_stack_bullish or breakout_score >= 0.45)  # EMA 정배열 또는 돌파 품질 추가 확인
    )
    ignition_vol_ok = vol_ratio >= 1.4 or micro_vol_ratio >= 1.5 or _high_quality_signal
    late_chase_risk = (
        micro_exhausted
        or micro_move_3 >= 1.8
        or micro_move_10 >= 3.8
        or micro_vwap_gap >= 2.3
        or micro_range_5 >= 3.4
    )
    clean_momentum_window = (
        micro_score >= 0.55
        and micro_vol_ratio >= 1.1
        and -0.35 <= micro_move_3 <= 1.55
        and micro_vwap_gap <= 2.2
        and micro_range_5 <= 3.2
        and not micro_exhausted
    )
    stream_entry_ok = (
        stream_fresh
        and stream_ignition
        and stream_age <= 2.5
        and stream_move_15 >= 0.25
        and stream_move_60 >= -0.15
        and stream_ticks_15 >= 2
        and stream_buy_ratio >= 0.48
        and orderbook_bid_ask >= 0.98
        and trend_entry_allowed
        and trend_follow_score >= 0.52
        and not stream_reversal
        and not late_chase_risk
    )
    strong_late_breakout_exception = (
        signal_score >= 0.76
        and micro_ready
        and orderbook_bid_ask >= 1.15
        and micro_vwap_gap <= 2.8
        and trend_entry_allowed
        and trend_follow_score >= 0.58
    )
    short_pressure_visible = (
        stream_reversal
        or stream_move_15 <= -0.18
        or stream_move_60 <= -0.45
        or micro_move_3 <= -0.80
        or rsi_bearish_divergence
        or choch_bearish_early
        or bos_bearish_early
        or bsl_sweep_confirmed               # BSL 스윕 = 고점 유동성 소화 후 하락 가능성
        or (at_bb_upper and not ema_stack_bullish)  # BB 상단 + EMA 정배열 없으면 저항권
        or airborne_short                    # EMA 위로 과이격 = 평균회귀 하락 압력
        or stoch_k >= 78                     # Stochastic 과매수 수치 = 단기 되돌림 압력
    )
    long_flip_confirmed = (
        (
            stream_fresh
            and not stream_reversal
            and stream_score >= 0.55
            and stream_move_5 >= 0.03
            and stream_move_15 >= 0.08
            and stream_buy_ratio >= 0.52
        )
        or (
            micro_score >= 0.55
            and micro_move_3 >= 0.0
            and orderbook_bid_ask >= 1.0
        )
        or choch_bullish_early
        or bos_bullish_early
        or support_reclaim_long
        or trend_reversal_early
        or rsi_bullish_div
        or macd_histogram_reversal
    )
    falling_knife_risk = short_pressure_visible and not long_flip_confirmed
    transition_note = (
        f"short_pressure={short_pressure_visible} long_flip={long_flip_confirmed} "
        f"stream5/15/60={stream_move_5:.2f}/{stream_move_15:.2f}/{stream_move_60:.2f}% "
        f"buy={stream_buy_ratio:.0%} micro3={micro_move_3:.2f}%"
    )

    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "High-stress regime. Preserve crypto capital.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + ["Stress regime blocks aggressive crypto entries."],
        }

    # ── ETH 4H 신고점 돌파 전략 (Strategy D, 2026-05-19) ──────────────────────
    # 백테스트 검증: Sharpe 2.33, WR 61.1%, MDD -7.08%, n=18 (2025-2026)
    # 조건: BTC EMA200 상승장 + ETH 4H close > 20봉 최고가 + vol ≥ 2.5x + RSI 50-70 + EMA20
    # STRESSED 레짐에서만 차단, TRENDING/RANGING 모두 허용
    if payload.get("eth_4h_breakout"):
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

    # ── BTC 급락 반등 (Sharp Dip Bounce) ─────────────────────────────────────
    # BTC 30분 -2.5% 이상 급락 + RSI≤32 → 과매도 반등 기대 (승률 63~68%)
    # 평균회귀 전략 — 추세 무관하게 작동, DEFENSE 스탠스만 차단
    if sharp_dip_bounce and stance != "DEFENSE" and not long_flip_confirmed:
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": "dip_bounce blocked: BTC dip has not confirmed long flip yet.",
            "symbol": dip_bounce_symbol,
            "candidate_symbols": [dip_bounce_symbol, "KRW-BTC"] + candidate_symbols[:2],
            "notes": [
                f"BTC 30min change={dip_change_30m:.2f}% / RSI={dip_rsi_btc if dip_rsi_btc is not None else '?'}",
                transition_note,
                "Do not catch a falling knife: wait for buy flow or bullish structure before dip entry.",
            ],
        }
    if sharp_dip_bounce and stance != "DEFENSE":
        _dip_rsi_str = f"{dip_rsi_btc:.0f}" if dip_rsi_btc is not None else "?"
        _is_alt = dip_bounce_symbol != "KRW-BTC"
        _focus_sym = dip_bounce_symbol.replace("KRW-", "") if _is_alt else "BTC"
        return {
            "action": "probe_longs",
            "size": "0.35x",
            "focus": f"dip_bounce: BTC {dip_change_30m:.1f}% 30분 급락 → {_focus_sym} 반등 포착",
            "symbol": dip_bounce_symbol,
            "candidate_symbols": [dip_bounce_symbol, "KRW-BTC"] + candidate_symbols[:2],
            "notes": [
                f"BTC 30min change={dip_change_30m:.2f}% / RSI={_dip_rsi_str}",
                f"target +1.2% / stop -0.7% / max 20min ({_focus_sym} 선택{'=알트' if _is_alt else '=BTC'})",
                "kimchi=" + (f"{kimchi_premium_pct:+.1f}%" if kimchi_premium_pct else "n/a"),
            ],
        }

    hard_overheat = recent_change >= 12.0 or burst_change >= 10.0 or ema_gap >= 8.0 or (rsi_value is not None and float(rsi_value) >= 92.0)

    if falling_knife_risk and regime != "RANGING":
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} short pressure still active. Wait for long flip.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                transition_note,
                "Core rule: no long entry while short pressure is visible unless a long flip is confirmed.",
            ],
        }

    # ── RANGING 시장 라우팅 ─────────────────────────────────────────────────────
    # RANGING = 추세 추종 전략 완전 차단 → 평균회귀(에어본/레인지 스캘프) 전략만 허용
    # 데이터: RANGING에서 추세 추종 승률 9%, 누적 -122% → 구조적 적자
    if regime == "RANGING":
        # ── 세력 신호 우선 진입 (RSI 불리시 다이버전스) ──────────────────────────
        # 가격 신저가 + RSI 고저가 = 스마트머니 매집 패턴
        # RANGING 블록이 일찍 return해버려 라인~1896의 rsi_div_ok 경로에 도달하지 못하는
        # 구조적 버그를 수정: RANGING 진입부에서 먼저 체크
        _ranging_div_ok = (
            rsi_bullish_div
            and not rsi_bearish_divergence
            and not hard_overheat
            and not falling_knife_risk
            and stance != "DEFENSE"
            and signal_score >= 0.42
            and orderbook_bid_ask >= 1.02
            and long_flip_confirmed
        )
        if _ranging_div_ok:
            _div_size = "0.50x" if signal_score >= 0.58 else "0.40x"
            return {
                "action": "probe_longs",
                "size": _div_size,
                "focus": f"세력신호(divergence): {lead_market or 'KRW-BTC'} 가격신저 RSI고저 → 스마트머니 매집",
                "symbol": lead_market,
                "candidate_symbols": candidate_symbols,
                "strategy_id": "crypto.rsi_bullish_divergence",
                "entry_profile": "rsi_bullish_divergence",
                "notes": reasons + [
                    "세력 신호: price lower-low + RSI higher-low = 스마트머니 축적",
                    f"signal={signal_score:.2f} ob={orderbook_bid_ask:.2f}x {trend_alignment}",
                    transition_note,
                ],
            }

        ranging_blend: list[dict[str, Any]] = []
        blend_safe = (
            not rsi_bearish_divergence
            and not hard_overheat
            and not stream_reversal
            and not choch_bearish_early
            and not falling_knife_risk
            and stance != "DEFENSE"
        )
        blend_mean_rev_signals = sum([
            airborne_long and airborne_score >= 0.35,
            bb_squeeze_bounce,
            vwap_deviation_long,
            rsi_extreme_long,
            rsi_mean_rev_long or range_scalp_eligible,
            stoch_oversold_cross,
            macd_histogram_reversal,
            hammer_candle or doji_candle,
            volume_climax_reversal,
            support_reclaim_long,
            williams_r_oversold,
            cci_oversold_bounce,
            keltner_lower_touch,
            mfi_oversold,
            rsi_bullish_div,           # 세력 신호: 가격신저 RSI고저 = 스마트머니 매집
        ])
        # 강한 과매도 확인 신호: RSI extreme / Williams %R / CCI / MFI / Stoch 중 1개 이상 필수
        # sig_* 변수들은 line 800+ 에서 정의되므로 여기서는 원본 payload 추출 변수 직접 사용
        blend_hard_oversold = (
            rsi_extreme_long           # RSI ≤ 25 극단 과매도
            or williams_r_oversold     # Williams %R ≤ -80
            or cci_oversold_bounce     # CCI < -100
            or mfi_oversold            # MFI ≤ 20
            or keltner_lower_touch     # 켈트너 채널 하단 터치
            or rsi_bullish_div         # 세력 신호 단독으로도 strong signal 인정
            or stoch_k <= 20           # Stochastic K ≤ 20 극단 과매도 수치 (bool은 교차만 감지, 수치로 상태 확인)
            or rsi_reset_confirmed     # RSI 눌림 후 회복 = 반전 재점화 확인
        )
        if (
            blend_mean_rev_signals >= 2          # ↓ 4→2: 단 2개 신호로 완화 (단, 아래 hard_oversold 필수)
            and blend_hard_oversold              # 진짜 과매도 확인 필수 — soft signal 단독 차단
            and signal_score >= 0.58             # ↓ 0.60→0.58: 신호 품질 최소 기준
            and blend_safe
            and long_flip_confirmed
            and orderbook_bid_ask >= 1.05
            and micro_move_3 >= -0.20
        ):
            ranging_blend.append({
                "score": 54 + blend_mean_rev_signals * 7 + min(airborne_score * 10, 8),
                "profile": "range_scalp",
                "strategy_id": "crypto.range_scalp",
                "size": "0.55x" if blend_mean_rev_signals >= 3 else "0.45x" if blend_mean_rev_signals >= 2 else "0.35x",
                "reason": f"mean reversion blend ({blend_mean_rev_signals}/14)",
                "note": f"mean_rev={blend_mean_rev_signals}/14 dev={airborne_deviation_pct:.2f}% vwap={vwap_deviation_pct:.1f}% rsi={rsi_value} / {transition_note}",
            })
        if (
            (range_breakout_long or high_tight_flag_long)
            and blend_safe
            and long_flip_confirmed
            and orderbook_bid_ask >= 0.98
            and micro_move_3 >= -0.05
            and micro_vwap_gap <= 4.8
        ):
            profile = "range_breakout" if range_breakout_long else "high_tight_flag"
            reason = "range_breakout + high_tight_flag" if range_breakout_long and high_tight_flag_long else profile
            ranging_blend.append({
                "score": 62 + signal_score * 18 + (6 if range_breakout_long and high_tight_flag_long else 0),
                "profile": profile,
                "strategy_id": f"crypto.{profile}",
                "size": "0.50x" if range_breakout_long and high_tight_flag_long else "0.45x" if range_breakout_long else "0.38x",
                "reason": reason,
                "note": f"local_continuation breakout={range_breakout_long} high_tight={high_tight_flag_long} ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}% / {transition_note}",
            })
        if (
            False  # quarantined: crypto.ranging_momentum_leader needs redesign/backtest
            and signal_score >= 0.74
            and trend_follow_score >= 0.55
            and blend_safe
            and long_flip_confirmed
            and (
                recent_change >= 2.0
                or burst_change >= 2.5
                or change_rate >= 3.0
                or range_breakout_long
                or high_tight_flag_long
            )
            and orderbook_bid_ask >= 0.35
            and micro_move_3 >= 0.0
            and micro_vwap_gap <= 6.0
            and (rsi_value is None or float(rsi_value) <= 86.0)
        ):
            size = "0.38x" if (signal_score >= 0.80 and recent_change >= 4.0) else "0.28x"
            if orderbook_bid_ask < 0.65:
                size = "0.22x"
            ranging_blend.append({
                "score": 60 + signal_score * 20 + min(max(recent_change, burst_change, change_rate), 8),
                "profile": "ranging_momentum_leader",
                "strategy_id": "crypto.ranging_momentum_leader",
                "size": size,
                "reason": "individual momentum leader",
                "note": f"leader signal={signal_score:.2f} trend={trend_follow_score:.2f} recent={recent_change:.2f}% burst={burst_change:.2f}% change={change_rate:.2f}% ob={orderbook_bid_ask:.2f}x / {transition_note}",
            })
        # smart_money_ok: RANGING 내 자본흐름 진입 — 추세 확인 없이 발동 차단
        _smf_ranging_confirm = (
            ema_stack_bullish
            or ssl_sweep_confirmed
            or trend_follow_score >= 0.62
        )
        smart_money_ok = (
            (smart_money_flow_long or (capital_flow_long and (auto_trendline_breakout_long or flow_box_breakout_long)))
            and blend_safe
            and long_flip_confirmed
            and signal_score >= 0.62         # 0.58 → 0.62
            and capital_flow_score >= 0.60   # 0.55 → 0.60
            and orderbook_bid_ask >= 0.95    # 0.82 → 0.95
            and micro_move_3 >= -0.10
            and micro_vwap_gap <= 3.0        # 3.8 → 3.0
            and (rsi_value is None or float(rsi_value) <= 78.0)
            and _smf_ranging_confirm         # 추세 확인 필수
        )
        if smart_money_ok:
            entry_size = "0.32x" if smart_money_flow_long and signal_score >= 0.68 else "0.24x"
            ranging_blend.append({
                "score": 66 + signal_score * 16 + capital_flow_score * 12 + (5 if auto_trendline_breakout_long else 0),
                "profile": "smart_money_flow",
                "strategy_id": "crypto.smart_money_flow",
                "symbol": lead_market,
                "size": entry_size,
                "reason": "capital flow + box/trendline break",
                "note": (
                    f"smart_money flow={capital_flow_score:.2f} vol={capital_flow_volume_ratio:.2f}x "
                    f"trendline={auto_trendline_breakout_long} box={flow_box_breakout_long} "
                    f"break={trendline_break_price:.4f} slope={trendline_slope_pct:.3f}% / {transition_note}"
                ),
            })
        # ── BB 스퀴즈 브레이크아웃: RANGING → TRENDING 전환 포착 ──────────────────
        # BB 폭 20봉 최소(스퀴즈) + 상단 돌파 + 거래량 1.3× → 추세 시작 시점 진입
        # 근거: 수집은 됐으나 한 번도 사용 안 된 신호 — 레인지 탈출 가장 초기에 포착
        # 조건 설계: signal_engine이 이미 거래량/RSI/추세 구조를 검증 → 여기선 추가 확인만
        bb_sq_width_pct = float(payload.get("bb_sq_width", 0.0) or 0.0)
        if (
            bb_squeeze_breakout
            and blend_safe
            and long_flip_confirmed
            and signal_score >= 0.58          # 최소 신호 품질
            and orderbook_bid_ask >= 0.92     # 매수 우위 (스퀴즈 해소 시 약간 완화)
            and micro_move_3 >= 0.0           # 이미 반등 중 (낙하 중 진입 차단)
            and (rsi_value is None or float(rsi_value) <= 80.0)
            and not hard_overheat
        ):
            ranging_blend.append({
                "score": 78,  # 최고 우선순위: regime 전환 신호는 즉시 포착해야 함
                "profile": "bb_squeeze_breakout",
                "strategy_id": "crypto.bb_squeeze_breakout",
                "symbol": lead_market,
                "size": "0.38x" if signal_score >= 0.68 else "0.28x",
                "reason": f"BB 스퀴즈 브레이크아웃 (width={bb_sq_width_pct:.3f}%)",
                "note": (
                    f"bb_squeeze 폭={bb_sq_width_pct:.3f}% signal={signal_score:.2f} "
                    f"ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}% / {transition_note}"
                ),
            })
        # Also scan all_candidates for squeeze breakouts on non-lead coins
        for _cand in all_candidates[:20]:
            _sym = str(_cand.get("market") or "").strip()
            if not _sym or _sym == lead_market:
                continue
            if not bool(_cand.get("bb_squeeze_breakout", False)):
                continue
            _combined = _cf(_cand, "combined_score", _cf(_cand, "signal_score", 0.0))
            _ob = _cf(_cand, "orderbook_bid_ask_ratio")
            _micro3 = _cf(_cand, "micro_move_3_pct")
            _rsi_f = _cf(_cand, "rsi", 50.0)
            _stream_rev = _cb(_cand, "stream_reversal")
            _bear_div = _cb(_cand, "rsi_bearish_divergence")
            _bsq_width = _cf(_cand, "bb_sq_width")
            if (
                _combined >= 0.60
                and _ob >= 0.90
                and _micro3 >= 0.0
                and _rsi_f <= 80.0
                and not _stream_rev
                and not _bear_div
                and not bool(_cand.get("micro_exhausted", False))
                and stance != "DEFENSE"
            ):
                ranging_blend.append({
                    "score": 76 + _combined * 10,
                    "profile": "bb_squeeze_breakout",
                    "strategy_id": "crypto.bb_squeeze_breakout",
                    "symbol": _sym,
                    "size": "0.30x" if _combined >= 0.65 else "0.22x",
                    "reason": f"scanner BB 스퀴즈 브레이크아웃 ({_sym})",
                    "note": f"scanner squeeze width={_bsq_width:.3f}% combined={_combined:.2f} ob={_ob:.2f}x",
                })
        # The desk leader can be overextended while another scanned coin has the
        # cleaner "capital flow + box/trendline break" pattern. Score the full
        # scanner set so we do not miss HYPER/BIO/SPK-style second-rank movers.
        for _cand in all_candidates[:24]:
            _sym = str(_cand.get("market") or "").strip()
            if not _sym or _sym == lead_market:
                continue
            _combined = _cf(_cand, "combined_score", _cf(_cand, "signal_score", 0.0))
            _flow = _cf(_cand, "capital_flow_score")
            _flow_vol = _cf(_cand, "capital_flow_volume_ratio")
            _ob = _cf(_cand, "orderbook_bid_ask_ratio")
            _ob_score = _cf(_cand, "orderbook_score")
            _micro = _cf(_cand, "micro_score")
            _micro3 = _cf(_cand, "micro_move_3_pct")
            _vwap_gap = _cf(_cand, "micro_vwap_gap_pct")
            _rsi = _cand.get("rsi")
            _rsi_f = _cf(_cand, "rsi", 50.0)
            _recent = _cf(_cand, "recent_change_pct")
            _burst = _cf(_cand, "burst_change_pct")
            _extension = _cf(_cand, "trend_extension_pct")
            _trend = str(_cand.get("trend_alignment") or "")
            _stream_reversal = _cb(_cand, "stream_reversal")
            _bear_div = _cb(_cand, "rsi_bearish_divergence")
            _smart = _cb(_cand, "smart_money_flow_long")
            _capital = _cb(_cand, "capital_flow_long")
            _trendline = _cb(_cand, "auto_trendline_breakout_long")
            _box = _cb(_cand, "flow_box_breakout_long")
            _structural = _trendline or _box
            _candidate_long_flip = (
                (
                    _cb(_cand, "stream_fresh")
                    and not _stream_reversal
                    and _cf(_cand, "stream_score") >= 0.55
                    and _cf(_cand, "stream_move_15s_pct") >= 0.08
                    and _cf(_cand, "stream_buy_ratio_15s") >= 0.52
                )
                or (_micro >= 0.50 and _micro3 >= 0.0 and _ob >= 0.95)
                or (_capital and _structural and _flow >= 0.65 and _ob >= 0.75)
            )
            _smart_candidate_ok = (
                (_smart or (_capital and _structural))
                and _candidate_long_flip
                and _combined >= (0.54 if (_smart and _structural) else 0.58)
                and _flow >= 0.55
                and (_ob >= 0.78 or _ob_score >= 0.50)
                and _micro3 >= -0.15
                and _vwap_gap <= 3.8
                and _extension <= 4.2
                and _recent < 10.0
                and _burst < 9.0
                and _rsi_f <= 82.0
                and _trend not in {"downtrend", "late_extension"}
                and not _stream_reversal
                and not _bear_div
                and stance != "DEFENSE"
            )
            if not _smart_candidate_ok:
                continue
            _size = "0.30x" if (_smart and _combined >= 0.62) else "0.22x"
            if _rsi_f >= 76.0 or _extension >= 3.2:
                _size = "0.18x"
            ranging_blend.append({
                "score": 64 + _combined * 18 + _flow * 12 + (5 if _trendline else 0) + (4 if _box else 0),
                "profile": "smart_money_flow",
                "strategy_id": "crypto.smart_money_flow",
                "symbol": _sym,
                "size": _size,
                "reason": "scanner capital flow + box/trendline break",
                "note": (
                    f"scanner_smart_money symbol={_sym} combined={_combined:.2f} flow={_flow:.2f} "
                    f"vol={_flow_vol:.2f}x ob={_ob:.2f}x micro3={_micro3:.2f}% "
                    f"trendline={_trendline} box={_box} rsi={_rsi if _rsi is not None else '?'}"
                ),
            })
        strength_follow_ok = (
            signal_score >= 0.70
            and trend_follow_score >= 0.48
            and blend_safe
            and long_flip_confirmed
            and (
                stream_score >= 0.55
                or micro_score >= 0.55
                or orderbook_score >= 0.45
                or range_breakout_long
                or high_tight_flag_long
                or change_rate >= 3.0
            )
            and micro_move_3 >= -0.10
            and micro_vwap_gap <= 2.4
            and orderbook_bid_ask >= 0.75
            and -0.20 <= airborne_deviation_pct <= 2.2
            and (rsi_value is None or float(rsi_value) <= 82.0)
        )
        if strength_follow_ok:
            entry_size = "0.24x" if signal_score >= 0.76 and change_rate >= 3.0 else "0.18x"
            ranging_blend.append({
                "score": 58 + signal_score * 18 + min(max(change_rate, recent_change, burst_change), 6),
                "profile": "ranging_strength_follow",
                "strategy_id": "crypto.ranging_strength_follow",
                "size": entry_size,
                "reason": "individual strength follow",
                "note": (
                    f"strength_follow signal={signal_score:.2f} trend={trend_follow_score:.2f} "
                    f"change={change_rate:.2f}% dev={airborne_deviation_pct:.2f}% "
                    f"vwap={vwap_deviation_pct:.1f}% rsi={rsi_value} ob={orderbook_bid_ask:.2f}x / {transition_note}"
                ),
            })
        if ranging_blend:
            best = sorted(ranging_blend, key=lambda item: float(item["score"]), reverse=True)[0]
            best_symbol = str(best.get("symbol") or lead_market or "KRW-BTC")
            notes = [
                f"{item.get('symbol', lead_market)} {item['profile']} score={float(item['score']):.1f} size={item['size']} reason={item['reason']}"
                for item in sorted(ranging_blend, key=lambda item: float(item["score"]), reverse=True)[:4]
            ]
            return {
                "action": "probe_longs",
                "size": str(best["size"]),
                "focus": f"{best['profile']}: {best_symbol} {best['reason']} - selected by RANGING strategy blend",
                "symbol": best_symbol,
                "candidate_symbols": [best_symbol] + [s for s in candidate_symbols if s != best_symbol],
                "strategy_id": str(best["strategy_id"]),
                "entry_profile": str(best["profile"]),
                "notes": reasons + [
                    "RANGING strategy blend: mean-reversion, local-continuation, and momentum-leader candidates compete together.",
                    str(best["note"]),
                    *notes,
                ],
            }
        local_continuation_ok = (
            (range_breakout_long or high_tight_flag_long)
            and signal_score >= 0.65             # ↑ 추가: RANGING 로컬 돌파 품질 기준
            and orderbook_bid_ask >= 1.05        # ↑ 0.98→1.05: 매수 우위 필수
            and micro_move_3 >= -0.35
            and micro_vwap_gap <= 4.8
            and not rsi_bearish_divergence
            and not hard_overheat
            and not stream_reversal
            and not choch_bearish_early
            and not falling_knife_risk
            and long_flip_confirmed
            and stance != "DEFENSE"
        )
        if local_continuation_ok:
            if range_breakout_long and high_tight_flag_long:
                entry_size = "0.50x"
                primary_reason = "range_breakout + high_tight_flag"
                profile = "range_breakout"
            elif range_breakout_long:
                entry_size = "0.45x"
                primary_reason = f"range_breakout above {range_high_20:.4f}"
                profile = "range_breakout"
            else:
                entry_size = "0.38x"
                primary_reason = f"high_tight_flag impulse={impulse_12_pct:.1f}% flag={flag_range_pct:.1f}%"
                profile = "high_tight_flag"
            return {
                "action": "probe_longs",
                "size": entry_size,
                "focus": f"{profile}: {lead_market or 'KRW-BTC'} {primary_reason} - local continuation in RANGING",
                "symbol": lead_market,
                "candidate_symbols": candidate_symbols,
                "notes": reasons + [
                    f"local continuation: breakout={range_breakout_long} high_tight={high_tight_flag_long} "
                    f"range_width={range_width_pct:.1f}% high20={range_high_20:.4f}",
                    f"timing: ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}% "
                    f"vwap_gap={micro_vwap_gap:.2f}% rsi={rsi_value}",
                    "RANGING gate blocks generic trend-following; explicit local breakout/flag patterns pass.",
                ],
            }
        # ── 평균회귀 신호 조합 (멀티 컨펌 → 사이즈 확대) ──────────────────────
        # 신호 1: 에어본 (EMA 이격 과대)
        # Broad tape can be RANGING while a single coin is clearly trending.
        # Let scanner leaders participate with reduced size instead of missing the whole move.
        local_momentum_leader_ok = (
            False  # quarantined: crypto.ranging_momentum_leader needs redesign/backtest
            and
            signal_score >= 0.74
            and trend_follow_score >= 0.55
            and (
                recent_change >= 2.0
                or burst_change >= 2.5
                or change_rate >= 3.0
                or range_breakout_long
                or high_tight_flag_long
            )
            and orderbook_bid_ask >= 0.65
            and micro_move_3 >= 0.0
            and micro_vwap_gap <= 6.0
            and (rsi_value is None or float(rsi_value) <= 86.0)
            and not rsi_bearish_divergence
            and not hard_overheat
            and not stream_reversal
            and not choch_bearish_early
            and not falling_knife_risk
            and long_flip_confirmed
            and stance != "DEFENSE"
        )
        if local_momentum_leader_ok:
            entry_size = "0.38x" if (signal_score >= 0.80 and recent_change >= 4.0) else "0.28x"
            if orderbook_bid_ask < 0.65:
                entry_size = "0.22x"
            return {
                "action": "probe_longs",
                "size": entry_size,
                "focus": f"ranging_momentum_leader: {lead_market or 'KRW-BTC'} individual momentum leader in RANGING",
                "symbol": lead_market,
                "candidate_symbols": candidate_symbols,
                "strategy_id": "crypto.ranging_momentum_leader",
                "entry_profile": "ranging_momentum_leader",
                "notes": reasons + [
                    f"leader momentum: signal={signal_score:.2f} trend={trend_follow_score:.2f} "
                    f"recent={recent_change:.2f}% burst={burst_change:.2f}% change={change_rate:.2f}%",
                    f"timing: ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}% "
                    f"vwap_gap={micro_vwap_gap:.2f}% rsi={rsi_value} / {transition_note}",
                    "Broad market is RANGING, so size is reduced and trailing/stop are tighter.",
                ],
            }

        local_strength_follow_ok = (
            signal_score >= 0.78              # ↑ 0.70→0.78: RANGING 강도추종 품질 강화
            and trend_follow_score >= 0.52    # ↑ 0.48→0.52
            and orderbook_bid_ask >= 0.75
            and micro_move_3 >= -0.10
            and micro_vwap_gap <= 2.4
            and -0.20 <= airborne_deviation_pct <= 2.2
            and (rsi_value is None or float(rsi_value) <= 82.0)
            and (
                stream_score >= 0.55
                or micro_score >= 0.55
                or orderbook_score >= 0.45
                or range_breakout_long
                or high_tight_flag_long
                or change_rate >= 3.0
            )
            and not rsi_bearish_divergence
            and not hard_overheat
            and not stream_reversal
            and not choch_bearish_early
            and not falling_knife_risk
            and long_flip_confirmed
            and stance != "DEFENSE"
        )
        if local_strength_follow_ok:
            entry_size = "0.24x" if signal_score >= 0.76 and change_rate >= 3.0 else "0.18x"
            return {
                "action": "probe_longs",
                "size": entry_size,
                "focus": f"ranging_strength_follow: {lead_market or 'KRW-BTC'} individual strength in RANGING",
                "symbol": lead_market,
                "candidate_symbols": candidate_symbols,
                "strategy_id": "crypto.ranging_strength_follow",
                "entry_profile": "ranging_strength_follow",
                "notes": reasons + [
                    f"strength follow: signal={signal_score:.2f} trend={trend_follow_score:.2f} "
                    f"change={change_rate:.2f}% dev={airborne_deviation_pct:.2f}% vwap={vwap_deviation_pct:.1f}% rsi={rsi_value}",
                    f"timing: ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}% "
                    f"micro={micro_score:.2f} stream={stream_score:.2f} / {transition_note}",
                    "RANGING broad tape is not enough to block a coin that already flipped long and is showing controlled strength.",
                ],
            }

        sig_airborne    = airborne_long and airborne_score >= 0.35
        # 신호 2: BB 스퀴즈 반등 (변동성 수축 + 하단 터치)
        sig_bb_squeeze  = bb_squeeze_bounce
        # 신호 3: VWAP 이격 (기관 평균단가 복귀 기대)
        sig_vwap_dev    = vwap_deviation_long
        # 신호 4: RSI 극단 과매도 (≤22)
        sig_rsi_extreme = rsi_extreme_long
        # 신호 5: RSI 평균회귀 (≤35)
        sig_rsi_rev     = rsi_mean_rev_long or range_scalp_eligible
        # 신호 6: Stochastic 과매도 교차 (%K < 25, %K crosses above %D)
        sig_stoch_cross = stoch_oversold_cross
        # 신호 7: MACD 히스토그램 바닥 반전
        sig_macd_rev    = macd_histogram_reversal
        # 신호 8: 캔들 반전 패턴 (Hammer / Doji)
        sig_candle_rev  = hammer_candle or doji_candle
        # 신호 9: 거래량 클라이맥스 반전
        sig_vol_climax  = volume_climax_reversal
        # 신호 10: 레인지 하단 지지선 리클레임
        sig_support_reclaim = support_reclaim_long
        # 신호 11: Williams %R 과매도 교차
        sig_williams_r = williams_r_oversold
        # 신호 12: CCI 과매도 반등
        sig_cci = cci_oversold_bounce
        # 신호 13: 켈트너 채널 하단 터치
        sig_keltner = keltner_lower_touch
        # 신호 14: MFI 과매도
        sig_mfi = mfi_oversold
        # 신호 15: RSI 불리시 다이버전스 (세력 신호) — 가격신저 + RSI고저 = 스마트머니 매집
        sig_rsi_div = rsi_bullish_div

        mean_rev_count = sum([
            sig_airborne, sig_bb_squeeze, sig_vwap_dev, sig_rsi_extreme, sig_rsi_rev,
            sig_stoch_cross, sig_macd_rev, sig_candle_rev, sig_vol_climax, sig_support_reclaim,
            sig_williams_r, sig_cci, sig_keltner, sig_mfi,
            sig_rsi_div,   # 세력 신호
        ])

        # 공통 필터: 급락 중 아님 + 베어리쉬 구조 아님 + 과열 아님
        _ranging_guard = (
            orderbook_bid_ask >= 1.05
            and micro_move_3 >= -1.0
            and not rsi_bearish_divergence
            and not hard_overheat
            and not stream_reversal
            and not choch_bearish_early
            and not falling_knife_risk
            and long_flip_confirmed
            and stance != "DEFENSE"
        )
        # 최소 2개 신호 + 강한 과매도 확인 1개 + 신호 품질 + 공통 필터 충족 시 진입
        # soft signal 1개 단독 진입 차단, 진짜 과매도 구간만 허용
        _hard_oversold_flag = (
            sig_rsi_extreme or sig_williams_r or sig_cci or sig_mfi or sig_keltner
            or sig_rsi_div          # 세력 신호도 strong confirmation으로 인정
            or stoch_k <= 20        # Stochastic 극단 수치 (교차 대기 없이 상태로 판단)
            or rsi_reset_confirmed  # RSI 눌림 후 회복 확인
        )
        range_scalp_ok = (
            mean_rev_count >= 2
            and _hard_oversold_flag
            and signal_score >= 0.58
            and _ranging_guard
        )

        airborne_note = (
            f"airborne dev={airborne_deviation_pct:.2f}% sigma={airborne_deviation_sigma:.1f}x "
            f"score={airborne_score:.2f} bb_lower={at_bb_lower} bb_pct_b={bb_pct_b:.2f}"
        )
        ranging_signal_note = (
            f"ranging signals({mean_rev_count}/15): airborne={sig_airborne} bb_sq={sig_bb_squeeze} "
            f"vwap={sig_vwap_dev}({vwap_deviation_pct:.1f}%) rsi_ext={sig_rsi_extreme} rsi_rev={sig_rsi_rev} "
            f"stoch={sig_stoch_cross}(k={stoch_k:.0f}) macd_rev={sig_macd_rev} candle={sig_candle_rev} "
            f"vol_climax={sig_vol_climax}({volume_climax_ratio:.1f}x) support_reclaim={sig_support_reclaim} "
            f"wr={sig_williams_r}({williams_r_val:.0f}) cci={sig_cci}({cci_val:.0f}) "
            f"keltner={sig_keltner} mfi={sig_mfi}({mfi_val:.0f}) "
            f"세력신호(div)={sig_rsi_div} bb_pct_b={bb_pct_b:.2f} airborne_short={airborne_short} "
            f"local_breakout={range_breakout_long} high_tight={high_tight_flag_long}"
        )

        if range_scalp_ok:
            # 신호 수 + BB 밴드 위치 + 신호 강도에 따른 사이즈 결정
            # bb_pct_b < 0.25 = 하단 25% 이내 = 최상의 평균회귀 진입 구간 → 사이즈 확대
            _deep_bb_lower = bb_pct_b < 0.25
            if mean_rev_count >= 3 or (mean_rev_count >= 2 and (at_bb_lower or _deep_bb_lower or airborne_deviation_sigma >= 2.0)):
                entry_size = "0.55x"
            elif mean_rev_count >= 2 or (airborne_deviation_sigma >= 1.5 and sig_airborne) or _deep_bb_lower:
                entry_size = "0.45x"
            else:
                entry_size = "0.35x"
            # 대표 진입 이유 결정
            if sig_airborne:
                primary_reason = f"airborne_long dev={airborne_deviation_pct:.2f}%"
            elif sig_bb_squeeze:
                primary_reason = "bb_squeeze_bounce"
            elif sig_vwap_dev:
                primary_reason = f"vwap_deviation {vwap_deviation_pct:.1f}%"
            elif sig_rsi_extreme:
                primary_reason = f"rsi_extreme rsi={rsi_value}"
            elif sig_vol_climax:
                primary_reason = f"volume_climax {volume_climax_ratio:.1f}x"
            elif sig_support_reclaim:
                primary_reason = f"support_reclaim {support_level:.4f}"
            elif sig_williams_r:
                primary_reason = f"williams_r_cross wr={williams_r_val:.0f}"
            elif sig_cci:
                primary_reason = f"cci_bounce cci={cci_val:.0f}"
            elif sig_keltner:
                primary_reason = "keltner_lower_touch"
            elif sig_mfi:
                primary_reason = f"mfi_oversold mfi={mfi_val:.0f}"
            else:
                primary_reason = f"rsi_mean_rev rsi={rsi_value}"
            return {
                "action": "probe_longs",
                "size": entry_size,
                "focus": f"range_scalp: {lead_market or 'KRW-BTC'} {primary_reason} — mean reversion ({mean_rev_count} signals)",
                "symbol": lead_market,
                "candidate_symbols": candidate_symbols,
                "notes": reasons + [
                    airborne_note,
                    ranging_signal_note,
                    f"range scalp: ob={orderbook_bid_ask:.2f}x micro={micro_score:.2f} rsi={rsi_value}",
                ],
            }
        # ── RANGING 내 강한 개별 추세 코인 — 소규모 추세 진입 허용 ─────────────
        # 시장 전체는 RANGING이더라도 개별 코인이 ADX>40 + CHoCH 불리시 + 추세 정렬이면
        # selective_probe 스타일(0.18~0.22x)로 소규모 진입. 추세추종 전략의 최소 경로 확보.
        # 근거: crypto.selective_probe WR 100%(4건) — 최고 성과 전략을 RANGING에서 완전 차단하는
        #       구조적 손실을 해소. ema_gap <= 5.0으로 과이격 진입 차단.
        # ADX 조건 완화: adx_trend_strong(ADX≥22+DI+>DI-)이 False여도
        # ADX≥35 + CHoCH + 추세구조 조합이면 진입 허용 (강한 추세 중 단기 DI- 우위 허용)
        _adx_strong_re = adx_trend_strong or (
            adx_val >= 35.0
            and choch_bullish_early
            and trend_alignment in ("trend_long", "pullback_long")
        )
        _ranging_trend_adx_ok = (
            _adx_strong_re                          # ADX 강세 (직접 or ADX≥35 + CHoCH 구조)
            and choch_bullish_early                 # CHoCH 불리시 (추세 전환 확인)
            and trend_alignment in ("trend_long", "pullback_long")  # 추세 구조 (pullback도 허용)
            and combined >= 0.62                    # 신호 품질 최소 기준
            and signal_score >= 0.65                # 차트 신호 최소 기준
            and ema_gap <= 5.0                      # 과이격 차단 (5% 초과면 너무 늦음)
            and not hard_overheat                   # 과열 차단
            and long_flip_confirmed                 # 단기 방향 확인
            and blend_safe                          # 기본 안전 필터
            and orderbook_bid_ask >= 0.88           # 오더북 최소 매수 우위
            and micro_move_3 >= -0.30               # 직전 3분 급락 차단
            and not rsi_bearish_divergence          # RSI 베어리시 다이버전스 차단
        )
        if _ranging_trend_adx_ok:
            _rt_size = "0.22x" if (combined >= 0.72 and orderbook_bid_ask >= 1.05) else "0.18x"
            return {
                "action": "probe_longs",
                "size": _rt_size,
                "focus": (
                    f"RANGING내 강한 추세({lead_market}) ADX강세+CHoCH불리시 — "
                    f"소규모 추세진입 (combined={combined:.2f} ema_gap={ema_gap:.1f}%)"
                ),
                "symbol": lead_market,
                "candidate_symbols": candidate_symbols,
                "strategy_id": "crypto.selective_probe",
                "entry_profile": "selective_probe",
                "notes": reasons + [
                    f"RANGING-override: adx_strong={adx_trend_strong} choch={choch_bullish_early} "
                    f"trend={trend_alignment} combined={combined:.2f} ema_gap={ema_gap:.1f}%",
                    f"ob={orderbook_bid_ask:.2f}x micro3={micro_move_3:.2f}% flip={long_flip_confirmed}",
                    transition_note,
                ],
            }
        # RANGING이지만 신호 없음 → 대기
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": (
                f"RANGING — 추세추종 차단. 평균회귀 신호 대기 "
                f"(dev={airborne_deviation_pct:.2f}% vwap={vwap_deviation_pct:.1f}% rsi={rsi_value})"
            ),
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                airborne_note,
                ranging_signal_note,
                "진입 조건(14): 에어본/BB스퀴즈/VWAP이격/RSI극단/스토캐스틱/MACD반전/캔들패턴/거래량클라이맥스/지지선리클레임/WilliamsR/CCI/Keltner/MFI 중 1개 이상 + OB매수우위",
            ],
        }
    # ── TRENDING / 기타 시장: 기존 추세추종 로직 유지 ──────────────────────────
    soft_overheat = recent_change >= 6.0 or burst_change >= 6.5 or ema_gap >= 5.0 or (rsi_value is not None and float(rsi_value) >= 85.0)
    try:
        rsi_numeric = float(rsi_value) if rsi_value is not None else 0.0
    except (TypeError, ValueError):
        rsi_numeric = 0.0
    stream_timing_ok = (
        stream_fresh
        and stream_age <= 3.5
        and not stream_reversal
        and (
            (stream_ignition and stream_move_15 >= -0.05)
            or (stream_score >= 0.58 and stream_move_15 >= 0.05)
            or (stream_score >= 0.55 and stream_buy_ratio >= 0.52 and stream_move_15 >= -0.03)
        )
    )
    micro_timing_ok = (
        micro_score >= 0.72
        and micro_vol_ratio >= 1.15
        and micro_move_3 >= 0.05
        and micro_vwap_gap <= 1.6
        and not micro_exhausted
    )
    breakout_timing_ok = breakout_count >= 2 and vol_ratio >= 1.6 and micro_move_3 >= 0.0
    trend_pullback_timing_ok = stream_timing_ok or micro_timing_ok or breakout_timing_ok
    timing_note = (
        f"timing: stream_ok={stream_timing_ok} age={stream_age:.1f}s move15={stream_move_15:.2f}% "
        f"buy={stream_buy_ratio:.0%} / micro_ok={micro_timing_ok} m3={micro_move_3:.2f}% "
        f"mvol={micro_vol_ratio:.1f}x / breakout_ok={breakout_timing_ok}"
    )
    # Trend-pullback entry: 15m structure is only the setup. Require fresh
    # 1m/tick timing before entering so we do not buy dead pullbacks.
    trend_pullback_ok = (
        trend_alignment == "pullback_long"
        and trend_entry_allowed
        and trend_follow_score >= 0.72
        and signal_score >= 0.65
        and orderbook_bid_ask >= 1.10
        and trend_pullback_timing_ok
        and not rsi_bearish_divergence
        and not late_chase_risk
        and not hard_overheat
    )
    # Obvious trend ride: [2026-05-18 비활성화]
    # hot_path_guard의 obvious_trend_ok와 동일 근거로 비활성화.
    # 데이터: 13건 WR 0% → cycle 단계에서 setup을 arm하지 않음.
    obvious_trend_ride_ok = False  # 비활성화: obvious_trend 전략 전체 차단
    if obvious_trend_ride_ok and stance != "DEFENSE":
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} obvious 15m trend armed for live-tick continuation.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                "obvious_trend_ride: cycle arms the setup; websocket tick flow opens only on live continuation.",
                f"move={max(recent_change, burst_change, change_rate):.2f}% extension={trend_extension_pct:.2f}% rsi={rsi_value}",
                trend_note, ignition_note, timing_note,
            ],
        }
    if hard_overheat and not (signal_score >= 0.68 and micro_score >= 0.50 and flow_support):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is extremely overheated. Watch only.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"hard overheat: recent {recent_change:.2f}% / burst {burst_change:.2f}% / ema gap {ema_gap:.2f}% / rsi {rsi_value}", support_note, ignition_note],
        }
    if rsi_bearish_divergence and not ignition_ready:
        reason = "bearish RSI divergence" if rsi_bearish_divergence else "RSI extreme zone" if rsi_extreme else "RSI quality failed"
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} RSI quality filter blocked late chase.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"{reason}; wait for RSI reset before new entry.", ignition_note],
        }
    if stream_reversal and stream_fresh and stream_move_15 <= -0.45:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} stream reversal detected. No fresh long entry.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"stream reversal: 5s {stream_move_5:.2f}% / 15s {stream_move_15:.2f}% / "
                f"60s {stream_move_60:.2f}% / buy {stream_buy_ratio:.0%}",
                ignition_note,
            ],
        }
    if recent_change <= -2.8 or burst_change <= -3.2:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Crypto structure is weakening. Preserve capital.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"recent {recent_change:.2f}% / burst {burst_change:.2f}% triggered protection."],
        }
    if not trend_entry_allowed:
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} chart trend is not aligned for a long entry.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                trend_note,
                "Fast 1m/stream triggers are only allowed inside a 15m uptrend or first-pullback structure.",
                ignition_note,
            ],
        }
    if late_chase_risk and not (pullback_entry_ok or strong_late_breakout_exception):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is moving, but entry is late. Wait for first pullback/reclaim.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"late chase guard: move3 {micro_move_3:.2f}% / move10 {micro_move_10:.2f}% / "
                f"vwap gap {micro_vwap_gap:.2f}% / range5 {micro_range_5:.2f}%",
                "blocks failed-ignition style chase entries while preserving pullback/ICT entries.",
                ignition_note,
            ],
        }

    breakout_partial = bool(payload.get("breakout_partial", False))

    ict_score = float(payload.get("ict_score", 0.0) or 0.0)
    kill_zone_active = bool(payload.get("kill_zone_active", False))
    ssl_sweep_confirmed = bool(payload.get("ssl_sweep_confirmed", False))
    choch_bullish = bool(payload.get("choch_bullish", False))
    choch_bearish = bool(payload.get("choch_bearish", False))
    bos_bullish = bool(payload.get("bos_bullish", False))
    bos_bearish = bool(payload.get("bos_bearish", False))
    price_at_bull_ob = bool(payload.get("price_at_bull_ob", False))
    price_in_bull_fvg = bool(payload.get("price_in_bull_fvg", False))
    ict_bullish_count = int(payload.get("ict_bullish_count", 0) or 0)
    ict_structure = str(payload.get("ict_structure", "undecided") or "undecided")
    # kill_zone_name: line 158 부근 payload 추출 블록에서 먼저 할당됨 (ignition_note에서 사용)

    # ICT CHoCH bearish: 추세 반전 하락 — 신규 진입 차단
    if choch_bearish and signal_score < 0.58:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "ICT CHoCH bearish — trend reversing down. No new entries.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"ict_structure: {ict_structure} / signal {signal_score:.2f}"],
        }

    # ICT bearish_break 구조: 하락 추세 브레이크 — 신호 약하면 신규 진입 차단
    if ict_structure == "bearish_break" and signal_score < 0.65:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "ICT bearish structure break — market in downtrend. Waiting for reclaim.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"ict_structure: {ict_structure} / signal {signal_score:.2f} — bearish_break 구조에서 signal 0.65 미만 차단",
            ],
        }

    # ICT 컨플루언스 진입: breakout_partial 없어도 ICT 3개 이상이면 허용
    # ict_score >= 0.08: 킬존 진입 이상의 컨플루언스 (SSL스윕+킬존+FVG 중 일부 활성)
    ict_entry_ok = (
        ict_bullish_count >= 3
        or (ssl_sweep_confirmed and kill_zone_active)
        or (choch_bullish and ict_bullish_count >= 2)
        or (ict_score >= 0.10 and ict_bullish_count >= 2)  # ict_score 수치 실제 활용
    )
    # micro_entry_ok: simplified — needs 1m momentum + volume without requiring all 5 sub-flags of micro_ready
    micro_entry_ok = (
        clean_momentum_window
        and micro_score >= 0.48
        and micro_vol_ratio >= 1.1
        and micro_move_3 >= -1.5
        and orderbook_bid_ask >= 1.0
        and trend_entry_allowed
        and trend_follow_score >= 0.52
    )
    # discovery_entry_ok: removed research_support gate — backtest history shouldn't block fresh opportunities.
    # 2026-04-29: signal 0.52 → 0.56, micro 0.44 → 0.46, ob 0.98 → 1.0 after data showed
    # all selective_probe entries at 0.48~0.55 hit -0.7~-0.9% within minutes (failed_ignition).
    discovery_entry_ok = (
        signal_score >= 0.56
        and micro_score >= 0.46
        and orderbook_bid_ask >= 1.0
        and trend_entry_allowed
        and trend_follow_score >= 0.54
        and not late_chase_risk
        and not hard_overheat
    )
    # direct_entry_ok: signal_score here IS the combined_score from CryptoDeskAgent —
    # it already weights signal×0.34 + trend×0.14 + micro×0.24 + ob×0.17 + btc×0.08.
    # Do NOT re-gate on clean_momentum_window/stream/breakout — those are already baked in.
    # Just confirm: composite is high + orderbook tilted + trend direction correct.
    # ADX 수치 활용: 방향성 있는 강한 추세 = adx_val >= 20 (bool adx_trend_strong은 22 기준)
    # adx_val이 높을수록 추세 강도 → direct_entry 요건 완화 / 약할수록 유지
    _adx_trend_ok = adx_val >= 20.0 or adx_trend_strong
    direct_entry_ok = (
        signal_score >= 0.76
        and orderbook_bid_ask >= 1.08
        and trend_entry_allowed
        and trend_follow_score >= 0.58
        and launch_confirmed
        and _adx_trend_ok             # ADX 수치 기반 추세 강도 확인 추가
        and price_above_trend_ema     # EMA21 위 = 기본 상승 구조 확인
        and not rsi_bearish_divergence
        and (trend_extension_pct <= 2.8 or pullback_detected)
    )
    # combined_score_ok: lower-conviction path for moderate composite readings.
    # Fires when combined_score is meaningful but below the direct_entry_ok threshold,
    # or when orderbook is near-neutral. Smaller default size.
    combined_score_ok = (
        signal_score >= 0.82
        and trend_entry_allowed
        and trend_follow_score >= 0.62
        and orderbook_bid_ask >= 1.12
        and launch_confirmed
        and not rsi_bearish_divergence
    )
    # smart_money_flow: 2건 전패 (no_lift_exit) → 추세 강도 확인 강화
    # 자본 유입 신호만으로 부족 — EMA 정배열/SSL스윕/추세추종 중 1개 이상 필수
    _smf_trend_confirm = (
        ema_stack_bullish                # EMA 8>21>34 정배열
        or ssl_sweep_confirmed           # ICT 매도 유동성 스윕 후 상승
        or trend_follow_score >= 0.62    # 차트 추세 강도 충분
    )
    smart_money_flow_ok = (
        (smart_money_flow_long or flow_box_breakout_long or (capital_flow_long and auto_trendline_breakout_long))
        and trend_alignment not in {"downtrend", "late_extension"}
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and long_flip_confirmed
        and signal_score >= 0.62         # 0.58 → 0.62: 신호 품질 기준 상향
        and capital_flow_score >= 0.60   # 0.55 → 0.60: 자본흐름 강도 기준 상향
        and orderbook_bid_ask >= 0.95    # 0.82 → 0.95: 오더북 압력 강화
        and micro_move_3 >= -0.10
        and micro_vwap_gap <= 3.0        # 3.8 → 3.0: VWAP 근접 요구
        and (rsi_value is None or float(rsi_value) <= 78.0)  # 82 → 78: RSI 과매수 기준 강화
        and _smf_trend_confirm           # 추세 확인 필수
    )
    # Volume gate: pullback/trend paths bypass only after launch confirmation.
    if not ignition_vol_ok and not pullback_entry_ok and not ict_entry_ok and not direct_entry_ok and not stream_entry_ok and not trend_pullback_ok and not combined_score_ok and not smart_money_flow_ok and stance != "DEFENSE":
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} volume too low for entry. Wait for volume confirmation.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"volume gate: 15m vol {vol_ratio:.1f}x / 1m vol {micro_vol_ratio:.1f}x — need 1.4x/1.5x",
                ignition_note, support_note,
            ],
        }

    if smart_money_flow_ok:
        entry_size = "0.58x" if smart_money_flow_long and signal_score >= 0.68 else "0.44x"
        if soft_overheat:
            entry_size = "0.28x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"smart_money_flow: {lead_market or 'KRW-BTC'} capital flow + trendline/box break.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "strategy_id": "crypto.smart_money_flow",
            "entry_profile": "smart_money_flow",
            "notes": reasons + [
                f"capital_flow={capital_flow_score:.2f} vol={capital_flow_volume_ratio:.2f}x "
                f"trendline={auto_trendline_breakout_long} box={flow_box_breakout_long}",
                f"break_price={trendline_break_price:.4f} slope={trendline_slope_pct:.3f}% / {transition_note}",
                ignition_note,
            ],
        }

    # Pullback entry path: prior spike + controlled retracement to EMA + volume contraction
    # This is the Ross Cameron 'first red candle' / Raschke Holy Grail entry
    if pullback_entry_ok and stance != "DEFENSE" and not hard_overheat:
        entry_size = "0.65x" if validated_support else "0.50x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} pullback entry — retracement near EMA after spike.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [pullback_note, trend_note, ignition_note, support_note],
        }

    # Trend-pullback entry: 15m structure confirmed pullback_long, high signal, strong orderbook.
    # Conservative size — we're catching a dip without full momentum confirmation.
    if trend_pullback_ok and stance != "DEFENSE":
        entry_size = "0.58x" if signal_score >= 0.75 else "0.45x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} trend pullback entry — 15m structure bullish, dip buying near EMA.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                trend_note,
                f"trend_pullback: signal={signal_score:.2f} ob={orderbook_bid_ask:.2f}x trend={trend_follow_score:.2f}",
                timing_note,
                ignition_note,
            ],
        }

    # Direct entry: combined_score is the signal — enter now without extra gate chains.
    # Size scales with composite conviction.
    if direct_entry_ok and stance != "DEFENSE" and not hard_overheat:
        if signal_score >= 0.80:
            entry_size = "0.90x"
        elif signal_score >= 0.72:
            entry_size = "0.78x"
        elif signal_score >= 0.65:
            entry_size = "0.65x"
        else:
            entry_size = "0.52x"
        if soft_overheat:
            entry_size = "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} direct entry — combined {signal_score:.2f} trend {trend_alignment}.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"direct entry: combined={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment} ob={orderbook_bid_ask:.2f}x",
                f"micro={micro_score:.2f} move3={micro_move_3:.2f}% vwap_gap={micro_vwap_gap:.2f}%",
                f"stream: score={stream_score:.2f} move15={stream_move_15:.2f}% buy={stream_buy_ratio:.0%}",
                trend_note, ignition_note, support_note,
            ],
        }

    if stream_entry_ok and stance != "DEFENSE" and signal_score >= 0.58:
        return {
            "action": "selective_probe",
            "size": "0.48x",
            "focus": f"{lead_market or 'KRW-BTC'} tick ignition entry from Upbit stream.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"stream ignition: score={stream_score:.2f} age={stream_age:.2f}s "
                f"move5={stream_move_5:.2f}% move15={stream_move_15:.2f}% move60={stream_move_60:.2f}% "
                f"ticks15={stream_ticks_15} buy={stream_buy_ratio:.0%}",
                trend_note,
                ignition_note,
                support_note,
            ],
        }

    if ignition_ready and stance != "DEFENSE" and (micro_entry_ok or stream_entry_ok or breakout_count >= 2 or trend_ignition_score >= 0.60):
        entry_size = "0.88x" if trend_ignition_score >= 0.68 else "0.68x"
        if soft_overheat:
            entry_size = "0.42x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} trend ignition long. Trail instead of fixed early take-profit.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [trend_note, ignition_note, support_note, "RSI is treated as momentum context, not an automatic sell signal."],
        }

    # ── EMA Crossover Long (TRENDING) ───────────────────────────────────────
    # EMA8이 EMA21을 위로 교차 — 단기 모멘텀 전환 조기 진입
    # 과열/발산/베어리쉬 구조 차단, micro or orderbook 확인 필요
    ema_cross_entry_ok = (
        ema_cross_long
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.45
        and (micro_entry_ok or orderbook_bid_ask >= 1.05 or stream_ignition)
    )
    if ema_cross_entry_ok:
        entry_size = "0.50x" if signal_score >= 0.55 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"ema_cross_long: {lead_market or 'KRW-BTC'} EMA8/21 골든크로스 — 추세 조기 진입",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"ema_cross: score={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── VWAP Reclaim Long (TRENDING) ────────────────────────────────────────
    # 가격이 VWAP 아래에서 위로 재탈환 — 기관 평균단가 복귀 후 속도 붙는 구간
    vwap_reclaim_entry_ok = (
        vwap_cross_long
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.45
        and (micro_entry_ok or orderbook_bid_ask >= 1.05 or stream_ignition)
    )
    if vwap_reclaim_entry_ok:
        entry_size = "0.50x" if signal_score >= 0.55 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"vwap_reclaim_long: {lead_market or 'KRW-BTC'} VWAP 재탈환 — 기관 매수 재개 신호",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"vwap_cross: score={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── RSI Momentum Flip Long (TRENDING) ──────────────────────────────────────
    # RSI가 50 아래에서 위로 교차 — 모멘텀 전환 초기 포착
    rsi_flip_entry_ok = (
        rsi_flip_long
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.43
        and (micro_entry_ok or orderbook_bid_ask >= 1.04 or stream_ignition)
    )
    if rsi_flip_entry_ok:
        entry_size = "0.45x" if signal_score >= 0.55 else "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"rsi_flip_long: {lead_market or 'KRW-BTC'} RSI 50 상향돌파 — 모멘텀 전환 진입",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"rsi_flip: score={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── MACD Bullish Cross (TRENDING) ──────────────────────────────────────────
    # MACD선이 시그널선을 상향돌파 (음의 영역에서) — 중기 모멘텀 전환
    # Emma scalp combo: Keltner channel context + Supertrend flip + MACD confirmation.
    # This short-term path requires multi-indicator confluence plus live flow support.
    emma_confirm_count = sum([
        bool(keltner_lower_touch),
        bool(supertrend_long),
        bool(macd_bull_cross or macd_histogram_reversal),
    ])
    emma_scalp_ok = (
        emma_confirm_count >= 2
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.44
        and orderbook_bid_ask >= 1.04
        and (micro_entry_ok or stream_ignition or stream_score >= 0.50)
        and (trend_entry_allowed or range_scalp_eligible or supertrend_long)
    )
    if emma_scalp_ok:
        entry_size = "0.52x" if emma_confirm_count >= 3 and signal_score >= 0.56 else "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"emma_scalp: {lead_market or 'KRW-BTC'} Keltner+Supertrend+MACD scalping confluence ({emma_confirm_count}/3)",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "strategy_id": "crypto.emma_scalp",
            "entry_profile": "emma_scalp",
            "notes": reasons + [
                f"emma_combo: keltner={keltner_lower_touch} supertrend={supertrend_long} macd={macd_bull_cross or macd_histogram_reversal}",
                f"score={signal_score:.2f} micro={micro_score:.2f} stream={stream_score:.2f} ob={orderbook_bid_ask:.2f}x",
                ignition_note,
            ],
        }

    # Neo micro-compound scalp: no reliable public Moritz Neo rule set was found.
    # Implement the usable principle only: small-size, fast compounding entries
    # when stream + orderbook + chart agree.
    neo_micro_ok = (
        stream_ignition
        and stream_score >= 0.62
        and micro_score >= 0.52
        and orderbook_bid_ask >= 1.12
        and signal_score >= 0.50
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and (trend_entry_allowed or supertrend_long or range_breakout_long)
    )
    if neo_micro_ok:
        return {
            "action": "selective_probe",
            "size": "0.28x",
            "focus": f"neo_micro_scalp: {lead_market or 'KRW-BTC'} small-size stream/orderbook compounding scalp",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "strategy_id": "crypto.neo_micro_scalp",
            "entry_profile": "neo_micro_scalp",
            "notes": reasons + [
                f"neo_micro: stream={stream_score:.2f} micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x signal={signal_score:.2f}",
                "public Moritz Neo rules were not verifiable; using small-size HFT-style confluence only.",
                ignition_note,
            ],
        }

    macd_cross_entry_ok = (
        macd_bull_cross
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.43
        and (micro_entry_ok or orderbook_bid_ask >= 1.04 or stream_ignition)
    )
    if macd_cross_entry_ok:
        entry_size = "0.45x" if signal_score >= 0.55 else "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"macd_bull_cross: {lead_market or 'KRW-BTC'} MACD 골든크로스 — 중기 모멘텀 전환",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"macd_cross: score={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Triple Candle Bull (TRENDING) ──────────────────────────────────────────
    # 3연속 양봉 + 연속 고점 — 강한 매수 압력 지속 확인
    triple_bull_entry_ok = (
        triple_candle_bull
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.45
        and (micro_entry_ok or orderbook_bid_ask >= 1.05 or stream_ignition)
    )
    if triple_bull_entry_ok:
        entry_size = "0.50x" if signal_score >= 0.55 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"triple_candle_bull: {lead_market or 'KRW-BTC'} 3연속 양봉 — 매수 모멘텀 지속",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"triple_bull: score={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Pullback Continuation (TRENDING) ────────────────────────────────────────
    # Ross Cameron / Holy Grail 스타일: 급등 후 pull back → 재상승 조기 진입
    pullback_cont_ok = (
        pullback_detected
        and pullback_score >= 0.55
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.45
        and vol_contracted_on_pullback
        and (micro_entry_ok or stream_ignition)
    )
    if pullback_cont_ok:
        entry_size = "0.55x" if signal_score >= 0.60 else "0.45x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"pullback_continuation: {lead_market or 'KRW-BTC'} 급등후 조정 재진입 — Holy Grail",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"pullback: score={pullback_score:.2f} spike={spike_pct_15m:.2f}% retrace={retrace_from_high_pct:.2f}%",
                f"vol_contracted={vol_contracted_on_pullback} signal={signal_score:.2f} {trend_alignment}",
                ignition_note,
            ],
        }

    # ── CHoCH Momentum (TRENDING / STRUCTURE) ──────────────────────────────────
    # ICT Change of Character + BOS 확인 — 구조적 추세 전환 진입
    choch_momentum_ok = (
        choch_bullish
        and bos_bullish
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.47
        and (micro_entry_ok or orderbook_bid_ask >= 1.06 or stream_ignition)
    )
    if choch_momentum_ok:
        entry_size = "0.55x" if signal_score >= 0.60 else "0.45x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"choch_momentum: {lead_market or 'KRW-BTC'} CHoCH+BOS 구조적 반전 — ICT 진입",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"choch_bull: score={signal_score:.2f} ict_count={ict_bullish_count} {ict_structure}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── ICT Level Long (STRUCTURE) ──────────────────────────────────────────────
    # 불리시 OB 또는 FVG에 가격 진입 — 기관 매수 구간 진입
    ict_level_long_ok = (
        (price_at_bull_ob or price_in_bull_fvg)
        and ict_bullish_count >= 2
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.47
        and (kill_zone_active or micro_entry_ok or orderbook_bid_ask >= 1.06)
    )
    if ict_level_long_ok:
        entry_size = "0.55x" if (price_at_bull_ob and price_in_bull_fvg) else "0.45x"
        level_type = "OB+FVG" if (price_at_bull_ob and price_in_bull_fvg) else ("OB" if price_at_bull_ob else "FVG")
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"ict_level_long: {lead_market or 'KRW-BTC'} 불리시 {level_type} 진입 — 기관 매수 구간",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"ict_level: {level_type} count={ict_bullish_count} kz={kill_zone_active} {ict_structure}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Supertrend Long (TRENDING) ─────────────────────────────────────────────
    # Supertrend(10, 3.0) 불리쉬 전환 — ATR 기반 추세 필터 신호
    supertrend_entry_ok = (
        supertrend_long
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.44
        and (micro_entry_ok or orderbook_bid_ask >= 1.05 or stream_ignition)
    )
    if supertrend_entry_ok:
        entry_size = "0.50x" if signal_score >= 0.55 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"supertrend_long: {lead_market or 'KRW-BTC'} 슈퍼트렌드 불리쉬 전환 — 추세 확인 진입",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"supertrend: score={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Bullish Engulfing (DUAL) ───────────────────────────────────────────────
    # 직전봉 음봉 → 현재봉 양봉으로 완전 흡수 — 강한 매수 전환
    engulfing_entry_ok = (
        engulfing_bull
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.42
        and (
            (trend_entry_allowed and (micro_entry_ok or stream_ignition))
            or (range_scalp_eligible and orderbook_bid_ask >= 1.04)
        )
    )
    if engulfing_entry_ok:
        entry_size = "0.48x" if signal_score >= 0.55 else "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"engulfing_bull: {lead_market or 'KRW-BTC'} 불리쉬 인걸핑 — 매수 전환 캔들",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"engulfing: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Volume Surge Long (CATALYST) ──────────────────────────────────────────
    # 거래량 2.5배 급증 + 양봉 — 기관 매집 강한 매수 신호
    vol_surge_entry_ok = (
        vol_surge_long
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.42
        and (trend_entry_allowed or signal_score >= 0.60)
        and orderbook_bid_ask >= 1.03
    )
    if vol_surge_entry_ok:
        entry_size = "0.52x" if signal_score >= 0.56 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"vol_surge_long: {lead_market or 'KRW-BTC'} 거래량 급증 양봉 — 기관 매집 포착",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"vol_surge: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── ADX Trend Strong (TRENDING) ───────────────────────────────────────────
    # ADX ≥ 22 + DI+ > DI-: 방향성 있는 강한 추세 — 조기 진입
    adx_entry_ok = (
        adx_trend_strong
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.44
        and (micro_entry_ok or orderbook_bid_ask >= 1.05 or stream_ignition)
    )
    if adx_entry_ok:
        entry_size = "0.50x" if signal_score >= 0.55 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"adx_trend_strong: {lead_market or 'KRW-BTC'} ADX={adx_val:.0f} 강한 추세 방향 진입",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"adx: val={adx_val:.1f} score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── BB Squeeze Breakout (BREAKOUT) ────────────────────────────────────────
    # 볼린저 밴드 스퀴즈 해소 + 상단 돌파 — 추세 시작 포착
    bb_squeeze_break_ok = (
        bb_squeeze_breakout
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.45
        and (micro_entry_ok or orderbook_bid_ask >= 1.06 or stream_ignition)
    )
    if bb_squeeze_break_ok:
        entry_size = "0.52x" if signal_score >= 0.58 else "0.42x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"bb_squeeze_breakout: {lead_market or 'KRW-BTC'} BB 스퀴즈 상단 돌파 — 추세 시작",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"bb_squeeze: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Consecutive Higher Lows (TRENDING STRUCTURE) ──────────────────────────
    # 3연속 고점저점 구조 확인 — 상승 추세 구조 진입
    higher_lows_ok = (
        consecutive_higher_lows
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.46
        and (micro_entry_ok or stream_ignition or orderbook_bid_ask >= 1.06)
    )
    if higher_lows_ok:
        entry_size = "0.52x" if signal_score >= 0.58 else "0.42x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"consecutive_higher_lows: {lead_market or 'KRW-BTC'} 3연속 고점저점 구조 — 상승 추세 확인",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"higher_lows: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Pin Bar Long (DUAL: RANGING 반전 / TRENDING 지속) ─────────────────────
    # 아래꼬리 몸통 2배 이상, 위꼬리 작음 — 매도 거부 + 매수 개입 캔들
    pin_bar_entry_ok = (
        pin_bar_long
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.40
        and (
            (trend_entry_allowed and orderbook_bid_ask >= 1.03)
            or (range_scalp_eligible and orderbook_bid_ask >= 1.04)
        )
    )
    if pin_bar_entry_ok:
        entry_size = "0.45x" if signal_score >= 0.52 else "0.35x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"pin_bar_long: {lead_market or 'KRW-BTC'} 핀바 매수 캔들 — 매도 거부 반전",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"pin_bar: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Morning Star (RANGING 3봉 반전) ──────────────────────────────────────
    # 큰 음봉 → 도지/소형봉 → 큰 양봉: 바닥 반전 확인
    morning_star_ok = (
        morning_star
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.40
        and orderbook_bid_ask >= 1.03
        and (micro_entry_ok or stream_ignition or range_scalp_eligible)
    )
    if morning_star_ok:
        entry_size = "0.48x" if signal_score >= 0.52 else "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"morning_star: {lead_market or 'KRW-BTC'} 모닝스타 반전 — 바닥 3봉 패턴",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"morning_star: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Inside Bar Breakout (BREAKOUT) ────────────────────────────────────────
    # 직전봉이 이전봉 내부 + 현재봉 상단 돌파 — 압축 후 방향성 확인
    inside_bar_ok = (
        inside_bar_breakout
        and trend_entry_allowed
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.44
        and orderbook_bid_ask >= 1.05
        and (micro_entry_ok or stream_ignition)
    )
    if inside_bar_ok:
        entry_size = "0.50x" if signal_score >= 0.55 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"inside_bar_breakout: {lead_market or 'KRW-BTC'} 인사이드바 상단 돌파 — 압축 해소",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"inside_bar: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── RSI Momentum Keep (TRENDING 지속) ─────────────────────────────────────
    # RSI 55~72 유지 추세 중 — 추세 지속 가능 구간 진입
    rsi_keep_ok = (
        rsi_momentum_keep
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.50
        and orderbook_bid_ask >= 1.06
        and (micro_entry_ok or stream_ignition)
    )
    if rsi_keep_ok:
        entry_size = "0.52x" if signal_score >= 0.58 else "0.42x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"rsi_momentum_keep: {lead_market or 'KRW-BTC'} RSI 추세 구간 유지 — 지속 진입",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"rsi_keep: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── OI Momentum Long (거래량 지속 상승) ──────────────────────────────────
    # 3봉 연속 거래량 + 가격 증가 — 강한 매수 압력 지속
    oi_mom_ok = (
        oi_momentum_long
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.44
        and orderbook_bid_ask >= 1.04
        and trend_alignment not in {"downtrend", "late_extension"}
    )
    if oi_mom_ok:
        entry_size = "0.52x" if signal_score >= 0.56 else "0.40x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"oi_momentum_long: {lead_market or 'KRW-BTC'} 3봉 연속 거래량+가격 상승 — 매수 압력",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"oi_momentum: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── Demand Zone Bounce (수요 구간 반등) ──────────────────────────────────
    # 최근 20봉 지지 구간 하단 터치 후 반등 — 기관 수요 확인
    demand_ok = (
        demand_zone_bounce
        and stance != "DEFENSE"
        and not hard_overheat
        and not rsi_bearish_divergence
        and signal_score >= 0.40
        and orderbook_bid_ask >= 1.04
        and (micro_entry_ok or stream_ignition or range_scalp_eligible)
    )
    if demand_ok:
        entry_size = "0.48x" if signal_score >= 0.52 else "0.38x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"demand_zone_bounce: {lead_market or 'KRW-BTC'} 수요 구간 반등 — 기관 지지 확인",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"demand_zone: score={signal_score:.2f} {trend_alignment}",
                f"micro={micro_score:.2f} ob={orderbook_bid_ask:.2f}x stream={stream_score:.2f}",
                ignition_note,
            ],
        }

    # ── VWAP + RSI 복합 (RANGING 고확률) ─────────────────────────────────────
    vwap_rsi_ok = (
        vwap_rsi_combo and stance != "DEFENSE" and not hard_overheat
        and not rsi_bearish_divergence and signal_score >= 0.40
        and orderbook_bid_ask >= 1.04
        and (micro_entry_ok or stream_ignition or range_scalp_eligible)
    )
    if vwap_rsi_ok:
        return {
            "action": "probe_longs", "size": "0.45x" if signal_score >= 0.52 else "0.35x",
            "focus": f"vwap_rsi_combo: {lead_market or 'KRW-BTC'} VWAP이탈+RSI과매도 복합 신호",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"vwap_rsi: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # ── Breakout + Volume Confirm ─────────────────────────────────────────────
    bk_vol_ok = (
        breakout_vol_confirm and trend_entry_allowed and stance != "DEFENSE"
        and not hard_overheat and not rsi_bearish_divergence
        and signal_score >= 0.46 and orderbook_bid_ask >= 1.06
        and (micro_entry_ok or stream_ignition)
    )
    if bk_vol_ok:
        return {
            "action": "probe_longs", "size": "0.52x" if signal_score >= 0.56 else "0.42x",
            "focus": f"breakout_vol_confirm: {lead_market or 'KRW-BTC'} 20봉 고점 돌파+거래량 동반",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"bk_vol: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # ── Hammer at Support ─────────────────────────────────────────────────────
    hammer_sup_ok = (
        hammer_at_support and stance != "DEFENSE" and not hard_overheat
        and not rsi_bearish_divergence and signal_score >= 0.40
        and orderbook_bid_ask >= 1.04
        and (micro_entry_ok or range_scalp_eligible or stream_ignition)
    )
    if hammer_sup_ok:
        return {
            "action": "probe_longs", "size": "0.45x" if signal_score >= 0.50 else "0.36x",
            "focus": f"hammer_at_support: {lead_market or 'KRW-BTC'} 지지선 망치형 캔들 반전",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"hammer_sup: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # ── Trend Reversal Early (CHoCH 단독 조기 포착) ───────────────────────────
    trend_rev_ok = (
        trend_reversal_early and trend_entry_allowed and stance != "DEFENSE"
        and not hard_overheat and not rsi_bearish_divergence
        and signal_score >= 0.44 and orderbook_bid_ask >= 1.05
        and (micro_entry_ok or stream_ignition)
    )
    if trend_rev_ok:
        return {
            "action": "probe_longs", "size": "0.48x" if signal_score >= 0.54 else "0.38x",
            "focus": f"trend_reversal_early: {lead_market or 'KRW-BTC'} CHoCH 조기 반전 포착",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"trend_rev: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # ── EMA Bounce Long ──────────────────────────────────────────────────────
    ema_bounce_ok = (
        False  # quarantined: crypto.ema_bounce produced peak=0 loss after long-flip patch
        and ema_bounce_long and stance != "DEFENSE" and not hard_overheat
        and not rsi_bearish_divergence and signal_score >= 0.42
        and orderbook_bid_ask >= 1.04
        and (trend_entry_allowed or range_scalp_eligible)
        and (micro_entry_ok or stream_ignition)
    )
    if ema_bounce_ok:
        return {
            "action": "probe_longs", "size": "0.48x" if signal_score >= 0.53 else "0.38x",
            "focus": f"ema_bounce_long: {lead_market or 'KRW-BTC'} EMA20 반등 — 이동평균 지지 확인",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"ema_bounce: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # ── RSI Bullish Divergence ───────────────────────────────────────────────
    rsi_div_ok = (
        rsi_bullish_div and stance != "DEFENSE" and not hard_overheat
        and signal_score >= 0.40 and orderbook_bid_ask >= 1.04
        and (micro_entry_ok or range_scalp_eligible or stream_ignition)
    )
    if rsi_div_ok:
        return {
            "action": "probe_longs", "size": "0.45x" if signal_score >= 0.52 else "0.36x",
            "focus": f"rsi_bullish_div: {lead_market or 'KRW-BTC'} RSI 불리쉬 다이버전스 반전",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"rsi_div: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # ── Multi-Signal RANGING Combo ───────────────────────────────────────────
    multi_ranging_ok = (
        multi_ranging_combo and stance != "DEFENSE" and not hard_overheat
        and not rsi_bearish_divergence and signal_score >= 0.40
        and orderbook_bid_ask >= 1.05
        and (micro_entry_ok or range_scalp_eligible)
    )
    if multi_ranging_ok:
        return {
            "action": "probe_longs", "size": "0.50x" if signal_score >= 0.54 else "0.40x",
            "focus": f"multi_ranging_combo: {lead_market or 'KRW-BTC'} 멀티 RANGING 신호 3개+ 동시",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"multi_ranging: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # ── Momentum Breakout Continuation ──────────────────────────────────────
    mom_bk_cont_ok = (
        momentum_breakout_cont and trend_entry_allowed and stance != "DEFENSE"
        and not hard_overheat and not rsi_bearish_divergence
        and signal_score >= 0.46 and orderbook_bid_ask >= 1.06
        and (micro_entry_ok or stream_ignition)
    )
    if mom_bk_cont_ok:
        return {
            "action": "probe_longs", "size": "0.52x" if signal_score >= 0.56 else "0.42x",
            "focus": f"momentum_breakout_cont: {lead_market or 'KRW-BTC'} 브레이크아웃 2봉째 모멘텀 지속",
            "symbol": lead_market, "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"mom_bk_cont: score={signal_score:.2f} {trend_alignment}", ignition_note],
        }

    # Combined-score fallback: fires when ignition/direct paths didn't match but composite
    # score is still meaningful. This is the primary path for moderate-confidence setups.
    if combined_score_ok and stance != "DEFENSE" and not hard_overheat:
        if signal_score >= 0.72:
            entry_size = "0.65x"
        elif signal_score >= 0.65:
            entry_size = "0.52x"
        else:
            entry_size = "0.40x"
        if soft_overheat:
            entry_size = "0.28x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} composite signal entry — combined {signal_score:.2f}.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [
                f"combined_score_ok: score={signal_score:.2f} trend={trend_follow_score:.2f} {trend_alignment} ob={orderbook_bid_ask:.2f}x",
                f"micro={micro_score:.2f} stream={stream_score:.2f}",
                ignition_note, trend_note,
            ],
        }

    if soft_overheat and discovery_entry_ok and stance != "DEFENSE":
        return {
            "action": "selective_probe",
            "size": "0.32x",
            "focus": f"{lead_market or 'KRW-BTC'} is hot, but discovery signal allows a small test.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, support_note, f"soft overheat controlled by small size: recent {recent_change:.2f}% / burst {burst_change:.2f}% / rsi {rsi_value}"],
        }

    # 단타 스윙: 3/4 이상이면 풀사이즈 진입, 임계값 대폭 완화
    # lead_weight threshold lowered to 0.08 to accommodate 9-coin neutral-weight universe (max ~0.14)
    offense_threshold = 0.58 if regime == "RANGING" else 0.55
    if bias == "offense" and signal_score >= offense_threshold and stance != "DEFENSE" and ema_gap <= 5.0 and (breakout_partial or ict_entry_ok or micro_entry_ok or stream_entry_ok or discovery_entry_ok or direct_entry_ok):
        entry_size = "0.85x" if discovery_support and not validated_support else "1.0x"
        if stance == "OFFENSE":
            entry_size = "0.95x" if discovery_support and not validated_support else "1.15x"
        return {
            "action": "probe_longs",
            "size": entry_size,
            "focus": f"{lead_market or 'KRW-BTC'} 단타 스윙 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, f"signal {signal_score:.2f} / micro {micro_score:.2f} / stream {stream_score:.2f} ({stream_move_15:.2f}%/15s) / orderbook {orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / ema gap {ema_gap:.2f}% / vol {vol_ratio:.1f}x / 1m vol {micro_vol_ratio:.1f}x / breakout {breakout_count}/4 / ict {ict_bullish_count}/5 {ict_structure}"],
        }
    # 신호 점수만 충분하면 선택적 진입 (research_support 게이트 제거 — 새로운 모멘텀 코인도 포착)
    # 2026-04-29: threshold 0.48 → 0.54 — selective_probe entries below 0.55 dominated failed_ignition list.
    if bias == "offense" and signal_score >= max(offense_threshold - 0.04, 0.54) and stance != "DEFENSE":
        return {
            "action": "selective_probe",
            "size": "0.55x" if discovery_support and not validated_support else "0.70x",
            "focus": f"{lead_market or 'KRW-BTC'} 공격적 탐색 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, f"offense bias / signal {signal_score:.2f} / breakout {breakout_count}/4 / weight {lead_weight:.2f}", support_note],
        }

    # micro_entry_ok 단독 진입: signal_score >= 0.48 → 0.55 (failed_ignition 데이터 기반 상향)
    if micro_entry_ok and stance != "DEFENSE" and signal_score >= 0.55:
        return {
            "action": "selective_probe",
            "size": "0.45x" if discovery_support and not validated_support else "0.55x",
            "focus": f"{lead_market or 'KRW-BTC'} 1m momentum entry while swing setup is forming.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [ignition_note, f"1m micro ready / micro {micro_score:.2f} / orderbook {orderbook_score:.2f} ({orderbook_bid_ask:.2f}x) / 1m vol {micro_vol_ratio:.1f}x / move3 {micro_move_3:.2f}% / swing {signal_score:.2f}", support_note],
        }

    mild_defense = (
        regime == "RANGING"
        and stance != "DEFENSE"
        and signal_score >= 0.33
        and recent_change > -0.5
        and ema_gap > -0.35
        and research_support
    )
    if bias == "defense" and mild_defense:
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} is still defensive, but close to a pilot watch state.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"signal {signal_score:.2f} / recent {recent_change:.2f}% / ema gap {ema_gap:.2f}% / weight {lead_weight:.2f}", support_note],
        }
    if bias == "defense":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Crypto structure remains weak. No new exposure.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + ["Wait for momentum recovery before new crypto entries."],
        }

    # balanced 바이어스: research_support 게이트 제거 — 신호가 있으면 진입
    # 2026-04-29: threshold 0.48/0.52 → 0.54/0.58 (failed_ignition 패턴 차단)
    pilot_probe_threshold = 0.54 if recent_change >= -0.3 else 0.58
    if bias == "balanced" and signal_score >= pilot_probe_threshold and stance != "DEFENSE" and ema_gap <= 5.0 and recent_change > -1.5:
        return {
            "action": "probe_longs",
            "size": "0.42x" if discovery_support and not validated_support else "0.60x",
            "focus": f"{lead_market or 'KRW-BTC'} balanced 단타 진입.",
            "symbol": lead_market,
            "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"signal {signal_score:.2f} / ema gap {ema_gap:.2f}% / threshold {pilot_probe_threshold:.2f}", support_note],
        }
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": "Crypto confirmation watch.",
        "symbol": lead_market,
        "candidate_symbols": candidate_symbols,
            "notes": reasons + [f"waiting for stronger confirmation (current {signal_score:.2f}, target {pilot_probe_threshold:.2f}+)", ignition_note, support_note],
    }


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_gap_count = int(payload.get("active_gap_count", 0) or 0)
    breakout_confirmed_count = int(payload.get("breakout_confirmed_count", 0) or 0)
    breakout_partial_count = int(payload.get("breakout_partial_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_gap = float(payload.get("avg_gap_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    gap_candidates = payload.get("gap_candidates", []) or []
    close_drive_candidates = payload.get("close_drive_candidates", []) or []
    gap_fill_candidates = payload.get("gap_fill_candidates", []) or []
    pullback_ma_candidates = payload.get("pullback_ma_candidates", []) or []
    in_open_window = bool(payload.get("in_open_window", False))
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in gap_candidates if str(item.get("ticker", "")).strip()]
    top_name = str(gap_candidates[0].get("name", "No leader")) if gap_candidates else "No leader"
    top_ticker = str(gap_candidates[0].get("ticker", "")) if gap_candidates else ""
    top_signal = float(gap_candidates[0].get("signal_score", 0.0) or 0.0) if gap_candidates else 0.0
    top_gap = float(gap_candidates[0].get("gap_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_rsi = float(gap_candidates[0].get("rsi", 0.0) or 0.0) if gap_candidates else 0.0
    top_burst = float(gap_candidates[0].get("burst_change_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_penalty = float(gap_candidates[0].get("overheat_penalty", 0.0) or 0.0) if gap_candidates else 0.0
    top_candidate_score = float(gap_candidates[0].get("candidate_score", 0.0) or 0.0) if gap_candidates else 0.0
    top_signal_bias = str(gap_candidates[0].get("signal_bias", "neutral") or "neutral") if gap_candidates else "neutral"
    # Best breakout candidate among merged gap_candidates
    bk_leader = next((c for c in gap_candidates if int(c.get("breakout_count", 0) or 0) >= 3), None)
    opening_window = bool(session.get("korea_opening_window"))
    mid_session = bool(session.get("korea_mid_session"))
    in_close_window = bool(payload.get("in_close_window", False))
    _qmeta = {"quality_score": quality_score, "avg_signal": avg_signal, "quality_threshold": 0.54}

    def _stock_long_flip(candidate: dict[str, Any], min_signal: float = 0.52) -> bool:
        """Common stock entry rule: buy only after short pressure starts flipping long."""
        bias = str(candidate.get("signal_bias", "neutral") or "neutral").lower()
        signal = float(candidate.get("signal_score", 0.0) or 0.0)
        return (
            bool(candidate.get("open_reversal", False))
            or bool(candidate.get("last_candle_bullish", False))
            or bool(candidate.get("close_drive", False))
            or int(candidate.get("breakout_count", 0) or 0) >= 3
            or (bias == "bullish" and signal >= min_signal)
        )

    def _stock_falling_knife(candidate: dict[str, Any]) -> bool:
        """Reject gap-down/weak-bias entries that have not proved a long flip yet."""
        bias = str(candidate.get("signal_bias", "neutral") or "neutral").lower()
        signal = float(candidate.get("signal_score", 0.0) or 0.0)
        gap = float(candidate.get("gap_pct", 0.0) or 0.0)
        rsi_raw = candidate.get("rsi")
        try:
            rsi_float = float(rsi_raw) if rsi_raw is not None else 50.0
        except (TypeError, ValueError):
            rsi_float = 50.0
        return (
            (bias == "bearish" and not bool(candidate.get("open_reversal", False)))
            or (gap <= -3.0 and signal < 0.55 and not bool(candidate.get("open_reversal", False)))
            or (rsi_float <= 25.0 and not bool(candidate.get("open_reversal", False)))
        )

    def _stock_intraday_extended(candidate: dict[str, Any]) -> bool:
        """Reject intraday-extended entries — the move has already happened.

        패턴: 장중 4%+ 급등 후 RSI 72+ → 이미 과열. 여기서 진입하면 눌림목에 바로 스탑 맞음.
        해결: burst_change 기준으로 '이미 달린 종목' 필터 추가.
        예외: 다이버전스 신호(bullish_divergence_ok) 또는 강한 돌파 컨펌(breakout_count >= 4)은
              모멘텀이 지속될 가능성이 있으므로 통과시킴.
        """
        burst = float(candidate.get("burst_change_pct", 0.0) or 0.0)
        rsi_raw = candidate.get("rsi")
        try:
            rsi_float = float(rsi_raw) if rsi_raw is not None else 50.0
        except (TypeError, ValueError):
            rsi_float = 50.0
        # Already-run filter: strong recent move + overbought
        # Tiers: 6%+ burst is almost always exhaustion. 4-6% with RSI >= 72 is risky.
        if burst >= 6.0:
            return True
        if burst >= 4.0 and rsi_float >= 72.0:
            # Allow if: confirmed breakout strength (4+ signals) or fresh divergence
            breakout_count = int(candidate.get("breakout_count", 0) or 0)
            has_divergence = bool(candidate.get("bullish_divergence_ok", False))
            if breakout_count < 4 and not has_divergence:
                return True
        return False

    def _stock_pullback_quality(candidate: dict[str, Any]) -> float:
        """눌림목 진입 품질 보너스 (0.0 ~ 0.15).

        가장 수익성 높은 진입은 추세 속 눌림목:
          - 최근 급등 후 RSI가 55-68로 리셋됨 (과열 해소)
          - ema10 위에 가격 유지 (추세 유효)
          - 다이버전스 신호 존재 시 추가 보너스
        이를 candidate_score에 보너스로 반영.
        """
        rsi_raw = candidate.get("rsi")
        try:
            rsi_float = float(rsi_raw) if rsi_raw is not None else 50.0
        except (TypeError, ValueError):
            rsi_float = 50.0
        burst = float(candidate.get("burst_change_pct", 0.0) or 0.0)
        ema_gap = float(candidate.get("ema_gap_pct", 0.0) or 0.0)
        has_divergence = bool(candidate.get("bullish_divergence_ok", False))
        bonus = 0.0
        # RSI in reset zone (was hot, now controlled) = classic pullback entry
        if 50.0 <= rsi_float <= 65.0 and burst <= 2.5:
            bonus += 0.08
        # Tight EMA gap = price is near support, not extended
        if 0.0 <= ema_gap <= 2.0:
            bonus += 0.04
        # Bullish divergence = smart money accumulation signal (세력 신호)
        if has_divergence:
            bonus += 0.12
        return round(min(bonus, 0.15), 3)

    if not session.get("korea_open"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "Korea desk is outside market hours.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Scan the next open for rotation leaders."],
            **_qmeta,
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Stress regime. No new Korea stock exposure.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Risk committee blocked fresh Korea entries in stress mode."],
            **_qmeta,
        }
    if gap_candidates and (top_signal < 0.5 or top_gap >= 12.0 or top_rsi >= 80.0 or top_burst >= 12.0 or avg_volume < 2200):
        # Even if gap leader is overheated, a clean breakout candidate can still fire
        if bk_leader and breakout_confirmed_count >= 1 and stance != "DEFENSE":
            bk_ticker = str(bk_leader.get("ticker", ""))
            bk_name = str(bk_leader.get("name", bk_ticker))
            bk_score = float(bk_leader.get("candidate_score", 0.0) or 0.0)
            return {
                "action": "probe_longs",
                "size": "0.45x",
                "focus": f"Breakout confirmed: {bk_name} (gap leader overheated, using breakout path).",
                "symbol": bk_ticker,
                "candidate_symbols": [bk_ticker],
                "notes": [
                    f"gap leader overheated but breakout candidate {bk_name} all-4 confirmed",
                    f"breakout score {bk_score:.2f} / vol_ratio {bk_leader.get('vol_ratio', 0):.1f}x",
                ] + list(bk_leader.get("breakout_reasons", []))[:3],
                **_qmeta,
            }
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"Leader {top_name} is overheated or under-confirmed.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"top signal {top_signal:.2f} / gap {top_gap:.2f}% / rsi {top_rsi:.1f} / burst {top_burst:.2f}% / penalty {top_penalty:.2f}",
                "Wait for cleaner follow-through before touching the leader.",
            ],
            **_qmeta,
        }

    # ── Momentum breakout path (stock_backtest_v3 validated strategy) ──────
    # Fires independently of gap-up conditions; works in any session window
    # GUARD: daily candle breakout patterns are backward-looking — require bullish signal_bias
    # to confirm the stock is CURRENTLY trending up, not already reversing.
    if breakout_confirmed_count >= 1 and stance != "DEFENSE" and bk_leader:
        bk_ticker = str(bk_leader.get("ticker", ""))
        bk_name = str(bk_leader.get("name", bk_ticker))
        bk_score = float(bk_leader.get("candidate_score", 0.0) or 0.0)
        bk_bias = str(bk_leader.get("signal_bias", "neutral") or "neutral").lower()
        bk_signal = float(bk_leader.get("signal_score", 0.0) or 0.0)
        if bk_bias != "bullish" or bk_signal < 0.50:
            return {
                "action": "stand_by",
                "size": "0.00x",
                "focus": f"Breakout confirmed ({bk_name}) but signal not bullish — skip entry.",
                "symbol": bk_ticker,
                "candidate_symbols": candidate_symbols,
                "notes": [
                    f"breakout pattern valid but bias={bk_bias} / signal={bk_signal:.2f} — requires bullish+0.50",
                    "Daily breakout without current bullish momentum is a reversal trap.",
                ],
                **_qmeta,
            }
        return {
            "action": "probe_longs",
            "size": "0.70x" if stance == "OFFENSE" else "0.50x",
            "focus": f"Momentum breakout: {bk_name} — all 4 signals confirmed.",
            "symbol": bk_ticker,
            "candidate_symbols": [bk_ticker] + [
                str(c.get("ticker", "")) for c in gap_candidates
                if str(c.get("ticker", "")) != bk_ticker
            ][:2],
            "notes": [
                f"breakout confirmed {breakout_confirmed_count} stock(s) / partial {breakout_partial_count}",
                f"candidate score {bk_score:.2f} / vol_ratio {bk_leader.get('vol_ratio', 0):.1f}x",
            ] + list(bk_leader.get("breakout_reasons", []))[:3],
            **_qmeta,
        }
    if breakout_partial_count >= 1 and stance != "DEFENSE" and bk_leader:
        bk_ticker = str(bk_leader.get("ticker", ""))
        bk_name = str(bk_leader.get("name", bk_ticker))
        bk_score = float(bk_leader.get("candidate_score", 0.0) or 0.0)
        bk_bias = str(bk_leader.get("signal_bias", "neutral") or "neutral").lower()
        bk_signal = float(bk_leader.get("signal_score", 0.0) or 0.0)
        if bk_bias != "bullish" or bk_signal < 0.52:
            return {
                "action": "stand_by",
                "size": "0.00x",
                "focus": f"Breakout partial ({bk_name}) but signal not bullish — skip entry.",
                "symbol": bk_ticker,
                "candidate_symbols": candidate_symbols,
                "notes": [
                    f"3/4 breakout but bias={bk_bias} / signal={bk_signal:.2f} — requires bullish+0.52 for partial",
                    "Partial breakout without bullish confirmation is high-risk.",
                ],
                **_qmeta,
            }
        return {
            "action": "selective_probe",
            "size": "0.38x",
            "focus": f"Breakout partial ({bk_name}) — 3/4 signals confirmed.",
            "symbol": bk_ticker,
            "candidate_symbols": [bk_ticker],
            "notes": [
                f"partial breakout {breakout_partial_count} stock(s) / candidate score {bk_score:.2f}",
            ] + list(bk_leader.get("breakout_reasons", []))[:3],
            **_qmeta,
        }

    # ── Gap Fill path (09:00~10:00 KST: 갭다운 메꾸기) ───────────────────────
    # 이유 없는 갭다운 → 당일 갭 메꾸기 확률 65~70%
    # open_reversal보다 약한 신호이므로 낮은 우선순위
    if in_open_window and gap_fill_candidates and stance != "DEFENSE":
        gf = gap_fill_candidates[0]
        gf_ticker = str(gf.get("ticker", ""))
        gf_name = str(gf.get("name", gf_ticker))
        gf_gap = float(gf.get("gap_pct", 0.0) or 0.0)
        gf_score = float(gf.get("gap_fill_score", 0.0) or 0.0)
        gf_rsi = gf.get("rsi")
        if gf_score >= 0.60:
            if _stock_falling_knife(gf) or not _stock_long_flip(gf, 0.52):
                return {
                    "action": "stand_by",
                    "size": "0.00x",
                    "focus": f"gap_fill blocked: {gf_name} has not confirmed short-to-long flip.",
                    "symbol": gf_ticker,
                    "candidate_symbols": [str(c.get("ticker", "")) for c in gap_fill_candidates],
                    "notes": [
                        f"gap_fill score {gf_score:.2f} / gap {gf_gap:.1f}% / rsi {gf_rsi}",
                        f"bias={gf.get('signal_bias')} signal={float(gf.get('signal_score', 0.0) or 0.0):.2f}",
                        "Do not catch a falling knife: gap-down entries need reversal or bullish current signal.",
                    ],
                    **_qmeta,
                }
            return {
                "action": "probe_longs",
                "size": "0.45x" if stance == "OFFENSE" else "0.32x",
                "focus": f"gap_fill: {gf_name} {gf_gap:.1f}% 갭다운 — 당일 갭 메꾸기 기대",
                "symbol": gf_ticker,
                "candidate_symbols": [str(c.get("ticker", "")) for c in gap_fill_candidates],
                "notes": [
                    f"갭다운 {gf_gap:.1f}% / rsi {gf_rsi} / score {gf_score:.2f}",
                    "target +2.0% / stop -0.8% / max 1h",
                    "뉴스 없는 기술적 갭다운 → 평균회귀 기대",
                ],
                **_qmeta,
            }

    # ── Opening reversal path (KIS tick stream: cascade→exhaustion→reversal) ─
    top_reversal = gap_candidates[0].get("open_reversal", False) if gap_candidates else False
    top_reversal_score = float(gap_candidates[0].get("reversal_score", 0) or 0) if gap_candidates else 0
    if (
        top_reversal
        and top_reversal_score >= 55
        and gap_candidates
        and not _stock_falling_knife(gap_candidates[0])
        and _stock_long_flip(gap_candidates[0], 0.50)
        and stance != "DEFENSE"
    ):
        return {
            "action": "attack_opening_drive",
            "size": "0.48x",
            "focus": f"open_reversal: {top_name} — cascade exhaustion detected, buying reversal.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"reversal score {top_reversal_score:.0f}/100",
                f"cascade={gap_candidates[0].get('reversal_cascade')} exhaustion={gap_candidates[0].get('reversal_exhaustion')} reversal={gap_candidates[0].get('reversal_reversal')}",
                f"price_vs_open={gap_candidates[0].get('price_vs_open_pct', 0):.2f}% sell30={gap_candidates[0].get('sell_ratio_30', 0):.0%} sell10={gap_candidates[0].get('sell_ratio_10', 0):.0%}",
                "Tight stop -0.8% / target +3.0% — reversal trade only, exit within 2h.",
            ],
            **_qmeta,
        }

    if (
        (opening_window or in_open_window)  # in_open_window(장초반 40분)도 attack 허용
        and active_gap_count >= 2
        and quality_score >= 0.62
        and avg_gap >= 1.8
        and avg_volume >= 8000
        and avg_signal >= 0.62
        and top_candidate_score >= 0.66
        and top_signal_bias == "bullish"
        and top_signal >= 0.62
        and 0.35 <= top_burst <= 7.5
        and top_gap <= 8.0
        and gap_candidates
        and _stock_long_flip(gap_candidates[0], 0.58)
        and not _stock_falling_knife(gap_candidates[0])
        and not _stock_intraday_extended(gap_candidates[0])
        and stance != "DEFENSE"
    ):
        return {
            "action": "attack_opening_drive",
            "size": "0.65x" if stance == "BALANCED" else "0.85x",
            "focus": f"Opening drive follow-through on {top_name}.",
            "symbol": top_ticker,
            "candidate_symbols": [top_ticker],
            "notes": [
                f"active gaps {active_gap_count}",
                f"quality {quality_score:.2f} / top candidate {top_candidate_score:.2f} / top burst {top_burst:.2f}% / avg gap {avg_gap:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
                "Opening drive now requires short-to-long flip confirmation and single-leader execution.",
            ],
            **_qmeta,
        }
    if (
        active_gap_count >= 1
        and quality_score >= 0.54  # 0.50 → 0.54: 품질 기준 강화
        and avg_signal >= 0.54  # 0.48 → 0.54: 신호 강도 요구 상향
        and avg_volume >= 4000  # 3500 → 4000: 유동성 기준 강화
        and top_candidate_score >= 0.56  # 0.52 → 0.56: 후보 품질 상향
        and gap_candidates
        and _stock_long_flip(gap_candidates[0], 0.52)  # 0.50 → 0.52
        and not _stock_falling_knife(gap_candidates[0])
        and not _stock_intraday_extended(gap_candidates[0])
    ):
        pb_bonus = _stock_pullback_quality(gap_candidates[0])
        return {
            "action": "selective_probe",
            "size": "0.50x",
            "focus": f"{top_name} selective probe while confirmation improves.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "strategy_confidence": "high" if pb_bonus >= 0.10 else "normal",
            "bullish_divergence_ok": bool(gap_candidates[0].get("bullish_divergence_ok", False)),
            "notes": [
                f"watchlist candidates {active_gap_count}",
                f"quality {quality_score:.2f} / top candidate {top_candidate_score:.2f} / avg gap {avg_gap:.2f}% / avg volume {int(avg_volume):,} / avg signal {avg_signal:.2f}",
                f"pullback_bonus={pb_bonus:.2f}" if pb_bonus > 0 else "entry at current momentum",
                "Only selective exploration until follow-through proves itself.",
            ],
            **_qmeta,
        }
    # Single strong candidate — smaller size, tighter criteria
    if (
        active_gap_count >= 1
        and quality_score >= 0.54
        and avg_signal >= 0.5
        and top_candidate_score >= 0.56
        and gap_candidates
        and _stock_long_flip(gap_candidates[0], 0.52)
        and not _stock_falling_knife(gap_candidates[0])
        and not _stock_intraday_extended(gap_candidates[0])
        and not mid_session
    ):
        pb_bonus = _stock_pullback_quality(gap_candidates[0])
        return {
            "action": "selective_probe",
            "size": "0.32x",
            "focus": f"{top_name} cautious single-candidate probe (opening window).",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "strategy_confidence": "high" if pb_bonus >= 0.10 else "normal",
            "bullish_divergence_ok": bool(gap_candidates[0].get("bullish_divergence_ok", False)),
            "notes": [
                f"single candidate quality {quality_score:.2f} / signal {avg_signal:.2f} / candidate_score {top_candidate_score:.2f}",
                f"pullback_bonus={pb_bonus:.2f}" if pb_bonus > 0 else "entry at current momentum",
                "Small size — only 1 gap candidate confirmed.",
            ],
            **_qmeta,
        }
    if (
        mid_session
        and active_gap_count >= 1
        and quality_score >= 0.58
        and avg_signal >= 0.52
        and top_candidate_score >= 0.58
        and gap_candidates
        and _stock_long_flip(gap_candidates[0], 0.52)
        and not _stock_falling_knife(gap_candidates[0])
        and not _stock_intraday_extended(gap_candidates[0])
    ):
        pb_bonus = _stock_pullback_quality(gap_candidates[0])
        return {
            "action": "selective_probe",
            "size": "0.24x",
            "focus": f"{top_name} mid-session follow-through probe.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "strategy_confidence": "high" if pb_bonus >= 0.10 else "normal",
            "bullish_divergence_ok": bool(gap_candidates[0].get("bullish_divergence_ok", False)),
            "notes": [
                f"mid-session quality {quality_score:.2f} / signal {avg_signal:.2f} / candidate_score {top_candidate_score:.2f}",
                f"pullback_bonus={pb_bonus:.2f}" if pb_bonus > 0 else "entry at current momentum",
                "Small size — mid-session entry, requires high conviction.",
            ],
            **_qmeta,
        }
    # ── 종가 추격 (Close Drive, 14:50~15:10 KST) ─────────────────────────────
    if in_close_window and close_drive_candidates and stance != "DEFENSE":
        cd = close_drive_candidates[0]
        cd_ticker = str(cd.get("ticker", ""))
        cd_name = str(cd.get("name", cd_ticker))
        cd_gap = float(cd.get("gap_pct", 0) or 0)
        cd_sig = float(cd.get("signal_score", 0) or 0)
        cd_sent = float(cd.get("sentiment_score", 0.5) or 0.5)
        cd_score = float(cd.get("candidate_score", 0) or 0)
        return {
            "action": "probe_longs",
            "size": "0.45x" if stance == "OFFENSE" else "0.32x",
            "focus": f"close_drive: {cd_name} — 당일 강세 유지, 기관 수급 확인.",
            "symbol": cd_ticker,
            "candidate_symbols": [str(c.get("ticker", "")) for c in close_drive_candidates],
            "notes": [
                f"종가 추격 — gap {cd_gap:.1f}% / signal {cd_sig:.2f} / sentiment {cd_sent:.2f}",
                f"기관 레이더 종목: inst_radar={cd.get('inst_radar', False)}",
                f"candidate_score {cd_score:.2f}",
                "오버나이트 홀딩 허용 — target +3.0% / stop -1.5% / 최대 30시간",
            ],
            **_qmeta,
        }

    # ── Pullback MA path (장중 언제나: 상승 추세 내 MA20 눌림목 매수) ───────────
    if pullback_ma_candidates and stance != "DEFENSE":
        pb = pullback_ma_candidates[0]
        pb_ticker = str(pb.get("ticker", ""))
        pb_name = str(pb.get("name", pb_ticker))
        pb_score = float(pb.get("pullback_ma_score", 0.0) or 0.0)
        pb_rsi = pb.get("rsi")
        pb_pct_ma20 = float(pb.get("pct_from_ma20", 0.0) or 0.0)
        pb_ma20 = pb.get("ma20")
        pb_vol_dec = bool(pb.get("vol_declining", False))
        if pb_score >= 0.60:
            if _stock_falling_knife(pb) or not _stock_long_flip(pb, 0.52):
                return {
                    "action": "stand_by",
                    "size": "0.00x",
                    "focus": f"pullback_ma blocked: {pb_name} has not confirmed long resumption.",
                    "symbol": pb_ticker,
                    "candidate_symbols": [str(c.get("ticker", "")) for c in pullback_ma_candidates],
                    "notes": [
                        f"pullback score {pb_score:.2f} / pct_from_MA20={pb_pct_ma20:.1f}% / rsi={pb_rsi}",
                        f"bias={pb.get('signal_bias')} signal={float(pb.get('signal_score', 0.0) or 0.0):.2f}",
                        "MA pullback is only valid after bullish resumption; no falling-knife buys.",
                    ],
                    **_qmeta,
                }
            return {
                "action": "probe_longs",
                "size": "0.50x" if stance == "OFFENSE" else "0.38x",
                "focus": f"pullback_ma: {pb_name} — MA20 눌림목 매수",
                "symbol": pb_ticker,
                "candidate_symbols": [str(c.get("ticker", "")) for c in pullback_ma_candidates],
                "notes": [
                    f"MA20={pb_ma20} / pct_from_MA20={pb_pct_ma20:.1f}% / rsi={pb_rsi} / vol_declining={pb_vol_dec}",
                    f"pullback score {pb_score:.2f}",
                    "상승 추세 내 MA20 눌림목 — target +3% / stop -1.5%",
                ],
                **_qmeta,
            }

    if (
        mid_session
        and candidate_symbols
        and quality_score >= 0.80
        and avg_signal >= 0.65
        and stance != "DEFENSE"
    ):
        return {
            "action": "selective_probe",
            "size": "0.16x",
            "focus": f"mid_session_quality_probe: {top_name} strong afternoon quality, small controlled entry.",
            "symbol": top_ticker,
            "candidate_symbols": [top_ticker],
            "strategy_id": "korea.selective_probe",
            "entry_profile": "mid_session_quality_probe",
            "notes": [
                f"afternoon quality {quality_score:.2f} / avg signal {avg_signal:.2f}",
                "No opening-drive pattern, but quality is high enough for a small controlled probe.",
                "Stop/position sizing remain throttled because this is an afternoon continuation probe.",
            ],
            **_qmeta,
        }

    if mid_session:
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "No strong Korea afternoon follow-through candidate.",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["Preserve capital unless a cleaner afternoon drive appears."],
            **_qmeta,
        }
    if (
        candidate_symbols
        and quality_score >= 0.82
        and avg_signal >= 0.65
        and stance != "DEFENSE"
    ):
        probe_symbol = top_ticker or candidate_symbols[0]
        return {
            "action": "selective_probe",
            "size": "0.14x",
            "focus": f"quality_follow_probe: {probe_symbol} high-quality Korea candidate, small controlled entry.",
            "symbol": probe_symbol,
            "candidate_symbols": [probe_symbol],
            "strategy_id": "korea.selective_probe",
            "entry_profile": "quality_follow_probe",
            "notes": [
                f"quality {quality_score:.2f} / avg signal {avg_signal:.2f}",
                "General session quality fallback: not opening-drive, but score is high enough for a small test.",
                "This keeps Korea desk active without forcing full-size entries.",
            ],
            **_qmeta,
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No Korea setup is strong enough right now.",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"stay patient (quality {quality_score:.2f} / signal {avg_signal:.2f})"],
        **_qmeta,
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

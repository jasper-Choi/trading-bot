from __future__ import annotations

from typing import Any


def build_crypto_plan(stance: str, regime: str, payload: dict[str, Any]) -> dict[str, Any]:
    """코인 데스크 추천 — Strategy D (ETH 4H 신고점 돌파) 전용.

    검증된 전략만 진입. 신호 없으면 watchlist_only.
    """
    lead_market = str(payload.get("lead_market", "KRW-BTC") or "KRW-BTC")
    desk_bias = str(payload.get("desk_bias", "balanced") or "balanced")
    reasons = [str(r) for r in (payload.get("reasons", []) or [])]

    # ── 1. 스트레스 레짐 차단 ──────────────────────────────────────────────
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "High-stress regime. Preserve crypto capital.",
            "symbol": lead_market,
            "candidate_symbols": [],
            "notes": reasons + ["Stress regime blocks aggressive crypto entries."],
        }

    # ── 2. Strategy D: ETH 4H 신고점 돌파 ─────────────────────────────────
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

    # ── 3. Strategy S2: MONGTATA 에어본 (평균회귀) ────────────────────────
    # 백테스트 검증: Sharpe 6.66, WR 50.0%, MDD -9.7% (코인 일봉)
    # 조건: price > EMA200 + close < lower_BB + close < EMA20×0.975
    if payload.get("mongtata_long"):
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

    # ── 4. 신호 없음 → 관망 ───────────────────────────────────────────────
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": f"No validated crypto signal. BTC bias={desk_bias}. Watching ETH 4H / MONGTATA.",
        "symbol": lead_market,
        "candidate_symbols": [],
        "notes": reasons[:3] + ["Strategy D (ETH 4H breakout) not triggered.", "Strategy S2 (MONGTATA) not triggered."],
    }


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """한국 주식 데스크 추천 — Strategy B (60일 신고점 돌파) 전용.

    검증된 전략만 진입. 신호 없으면 stand_by.
    """
    breakout_candidates = payload.get("new_high_breakout_candidates", []) or []
    breakout_confirmed_count = int(payload.get("breakout_confirmed_count", 0) or 0)
    breakout_partial_count = int(payload.get("breakout_partial_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)

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

    # ── 2. 스트레스 레짐 차단 ──────────────────────────────────────────────
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "Stress regime. No new Korea stock exposure.",
            "symbol": candidate_symbols[0] if candidate_symbols else "",
            "candidate_symbols": candidate_symbols,
            "notes": ["Risk committee blocked fresh Korea entries in stress mode."],
            "quality_score": quality_score,
        }

    # ── 3. Strategy B: 60일 신고점 돌파 (full confirm — 3/3 조건) ───────────
    # 백테스트 검증: Sharpe 6.16, WR 84.6%, MDD -4.0%
    if breakout_confirmed_count >= 1 and bk_leader and stance != "DEFENSE":
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
        return {
            "action": "probe_longs",
            "size": "0.70x" if stance == "OFFENSE" else "0.50x",
            "focus": f"new_high_breakout: {bk_name} 60일 신고점 돌파 확인",
            "symbol": bk_ticker,
            "candidate_symbols": candidate_symbols[:3],
            "focus_tag": "new_high_breakout",
            "strategy_id": "korea.new_high_breakout",
            "entry_profile": "new_high_breakout",
            "notes": [
                f"confirmed {breakout_confirmed_count}종목 / score {bk_score:.2f}",
                f"vol_ratio {bk_leader.get('vol_ratio', 0):.1f}x / RSI {bk_leader.get('rsi', 'n/a')}",
                "백테스트: Sharpe 6.16 / WR 84.6% / MDD -4.0%",
            ] + list(bk_leader.get("breakout_reasons", []))[:2],
            "quality_score": quality_score,
        }

    # ── 4. Strategy B partial (3/3 조건 중 3개 충족) ─────────────────────
    if breakout_partial_count >= 1 and bk_leader and stance != "DEFENSE":
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
        return {
            "action": "selective_probe",
            "size": "0.35x",
            "focus": f"new_high_breakout partial: {bk_name} — 3/3 signals confirmed.",
            "symbol": bk_ticker,
            "candidate_symbols": [bk_ticker],
            "focus_tag": "new_high_breakout",
            "strategy_id": "korea.new_high_breakout",
            "entry_profile": "new_high_breakout",
            "notes": list(bk_leader.get("breakout_reasons", []))[:3],
            "quality_score": quality_score,
        }

    # ── 5. Strategy S2: MONGTATA 에어본 (평균회귀) ───────────────────────
    # 백테스트 검증: Sharpe 8.60, WR 56.5%, MDD -5.9% (주식 3년)
    mongtata_candidates = payload.get("mongtata_airborne_candidates", []) or []
    if mongtata_candidates and stance != "DEFENSE":
        mt_leader = mongtata_candidates[0]
        mt_ticker = str(mt_leader.get("ticker", ""))
        mt_name = str(mt_leader.get("name", mt_ticker))
        mt_dev = float(mt_leader.get("deviation_pct", 0.0) or 0.0)
        mt_ema20 = float(mt_leader.get("ema20", 0.0) or 0.0)
        mt_lower_bb = float(mt_leader.get("lower_bb", 0.0) or 0.0)
        mt_symbols = [str(c.get("ticker", "")).strip() for c in mongtata_candidates if c.get("ticker")]
        return {
            "action": "probe_longs",
            "size": "0.50x",
            "focus": f"mongtata_airborne: {mt_name} EMA20 대비 {mt_dev:.1f}% 이탈",
            "symbol": mt_ticker,
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

    # ── 6. 신호 없음 → 관망 ───────────────────────────────────────────────
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "No signal. Strategy B (60일 신고점) / Strategy S2 (MONGTATA) not triggered.",
        "symbol": candidate_symbols[0] if candidate_symbols else "",
        "candidate_symbols": candidate_symbols,
        "notes": [
            f"confirmed={breakout_confirmed_count} / partial={breakout_partial_count} / mongtata={len(mongtata_candidates)}",
            "Waiting for Strategy B or S2 signal.",
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

from __future__ import annotations

from typing import Any


def build_crypto_plan(stance: str, regime: str, payload: dict[str, Any]) -> dict[str, Any]:
    bias = payload.get("desk_bias", "balanced")
    signal_score = float(payload.get("signal_score", 0.5) or 0.5)
    recent_change = float(payload.get("recent_change_pct", 0.0) or 0.0)
    burst_change = float(payload.get("burst_change_pct", 0.0) or 0.0)
    ema_gap = float(payload.get("ema_gap_pct", 0.0) or 0.0)
    rsi_value = payload.get("rsi")
    reasons = payload.get("reasons", [])
    backtest_weights = payload.get("backtest_weights", {}) or {}
    lead_market = payload.get("lead_market", "")
    lead_weight = float(backtest_weights.get(lead_market, 0.0) or 0.0)
    weight_support = lead_weight >= 0.28
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "위기 해소까지 자본 보존",
            "symbol": lead_market,
            "notes": reasons + ["위기 국면으로 크립토 공격적 진입 차단"],
        }
    if recent_change >= 2.6 or burst_change >= 3.0 or ema_gap >= 2.4 or (rsi_value is not None and float(rsi_value) >= 69.0):
        return {
            "action": "watchlist_only",
            "size": "0.00x",
            "focus": f"{lead_market or 'KRW-BTC'} 과열 급등 스킵",
            "symbol": lead_market,
            "notes": reasons + [f"최근 {recent_change:.2f}% / 급등 {burst_change:.2f}% / EMA갭 {ema_gap:.2f}% / RSI {rsi_value}"],
        }
    if recent_change <= -2.2 or burst_change <= -2.8:
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "크립토 구조 급약화 — 자본 보존",
            "symbol": lead_market,
            "notes": reasons + [f"최근 {recent_change:.2f}% / 급락 {burst_change:.2f}% — 하방 압력"],
        }
    offense_threshold = 0.74 if regime == "RANGING" else 0.7
    if bias == "offense" and signal_score >= offense_threshold and stance != "DEFENSE" and ema_gap <= 2.0 and weight_support:
        return {
            "action": "probe_longs",
            "size": "0.50x" if stance == "BALANCED" else "0.65x",
            "focus": f"{lead_market or 'KRW-BTC'} 추세 지속 감시",
            "symbol": lead_market,
            "notes": reasons + [f"공격 임계값 달성 {signal_score:.2f} / EMA갭 {ema_gap:.2f}% / 백테스트 가중치 {lead_weight:.2f}"],
        }
    if bias == "offense" and signal_score >= max(offense_threshold - 0.03, 0.68) and stance != "DEFENSE" and lead_weight >= 0.18:
        return {
            "action": "selective_probe",
            "size": "0.30x",
            "focus": f"{lead_market or 'KRW-BTC'} 명확한 크립토 추종 대기",
            "symbol": lead_market,
            "notes": reasons + [f"시그널 지지적이나 백테스트 가중치 선별적 수준 {lead_weight:.2f}"],
        }
    if bias == "defense":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "구조 약화 중 신규 크립토 노출 없음",
            "symbol": lead_market,
            "notes": reasons + ["모멘텀 회복까지 대기"],
        }
    # balanced bias: 낮은 기준으로 파일럿 검증용 소량 탐색 허용
    # 손실 최소화 — 파일럿 한도(₩60,000)가 실제 주문액 상한
    if bias == "balanced" and signal_score >= 0.58 and stance != "DEFENSE" and ema_gap <= 1.5 and recent_change > -1.0:
        return {
            "action": "probe_longs",
            "size": "0.20x",
            "focus": f"{lead_market or 'KRW-BTC'} 균형 국면 파일럿 탐색",
            "symbol": lead_market,
            "notes": reasons + [f"균형 편향 파일럿: 시그널 {signal_score:.2f} / EMA갭 {ema_gap:.2f}% (₩60,000 한도 적용)"],
        }
    return {
        "action": "watchlist_only",
        "size": "0.00x",
        "focus": "선별적 크립토 감시",
        "symbol": lead_market,
        "notes": reasons + [f"더 강한 확인 신호 대기 (현재 {signal_score:.2f}, 목표 0.58+)"],
    }


def build_korea_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_gap_count = int(payload.get("active_gap_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_gap = float(payload.get("avg_gap_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    gap_candidates = payload.get("gap_candidates", [])
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in gap_candidates if str(item.get("ticker", "")).strip()]
    top_name = gap_candidates[0]["name"] if gap_candidates else "No leader"
    top_ticker = gap_candidates[0]["ticker"] if gap_candidates else ""
    top_signal = float(gap_candidates[0].get("signal_score", 0.0) or 0.0) if gap_candidates else 0.0
    top_gap = float(gap_candidates[0].get("gap_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_rsi = float(gap_candidates[0].get("rsi", 0.0) or 0.0) if gap_candidates else 0.0
    top_burst = float(gap_candidates[0].get("burst_change_pct", 0.0) or 0.0) if gap_candidates else 0.0
    top_penalty = float(gap_candidates[0].get("overheat_penalty", 0.0) or 0.0) if gap_candidates else 0.0
    top_candidate_score = float(gap_candidates[0].get("candidate_score", 0.0) or 0.0) if gap_candidates else 0.0
    top_signal_bias = str(gap_candidates[0].get("signal_bias", "neutral") or "neutral") if gap_candidates else "neutral"
    opening_window = bool(session.get("korea_opening_window"))
    mid_session = bool(session.get("korea_mid_session"))

    if not session.get("korea_open"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "국내 시장 외 시간 — 한국주식 데스크 대기",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["다음 코스닥 개장 후 리더 종목 로테이션 검토"],
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "위기 국면 — 신규 코스닥 노출 없음",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["위험위원회가 신규 한국주식 진입 차단"],
        }
    if gap_candidates and (top_signal < 0.55 or top_gap >= 24.0 or top_rsi >= 74.0 or top_burst >= 10.0 or avg_volume < 3000):
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"과열 또는 확인 미흡 리더 {top_name} 스킵",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"상위 후보 시그널 {top_signal:.2f} / 갭 {top_gap:.2f}% / RSI {top_rsi:.1f} / 급등 {top_burst:.2f}% / 페널티 {top_penalty:.2f}",
                "구조 안정까지 과도한 시가 급등 스킵",
            ],
        }
    if opening_window and active_gap_count >= 3 and quality_score >= 0.72 and avg_gap >= 3.2 and avg_volume >= 20000 and avg_signal >= 0.64 and top_candidate_score >= 0.74 and top_signal_bias != "neutral" and stance != "DEFENSE":
        return {
            "action": "attack_opening_drive",
            "size": "0.50x" if stance == "BALANCED" else "0.70x",
            "focus": f"리더 {top_name} 추적",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"유동성 지지 갭 후보 {active_gap_count}종목",
                f"품질점수 {quality_score:.2f} / 상위후보 {top_candidate_score:.2f} / 평균갭 {avg_gap:.2f}% / 평균거래량 {int(avg_volume):,} / 평균시그널 {avg_signal:.2f}",
            ],
        }
    if active_gap_count >= 2 and quality_score >= 0.58 and avg_signal >= 0.52 and avg_volume >= 8000 and top_candidate_score >= 0.62:
        return {
            "action": "selective_probe",
            "size": "0.35x",
            "focus": f"{top_name} 확인 대기" if opening_window else f"{top_name} 장중 후행 확인만",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"모니터링 가치 후보 {active_gap_count}종목",
                f"품질점수 {quality_score:.2f} / 상위후보 {top_candidate_score:.2f} / 평균갭 {avg_gap:.2f}% / 평균거래량 {int(avg_volume):,} / 평균시그널 {avg_signal:.2f}",
                "시가 윈도우 선별 탐색" if opening_window else "장중 선별 탐색만",
            ],
        }
    if mid_session:
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "추격할 만한 장후반 한국주식 돌파 없음",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["평범한 장중 추종 스킵 — 위험예산 보존"],
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "현재 시가 공략 품질 후보 없음",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"약한 갭 스킵 — 집중력 보존 (품질 {quality_score:.2f} / 시그널 {avg_signal:.2f})"],
    }


def build_us_plan(stance: str, regime: str, payload: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    active_us_count = int(payload.get("active_us_count", 0) or 0)
    quality_score = float(payload.get("quality_score", 0.0) or 0.0)
    avg_change = float(payload.get("avg_change_pct_top3", 0.0) or 0.0)
    avg_volume = float(payload.get("avg_volume_top3", 0.0) or 0.0)
    avg_signal = float(payload.get("avg_signal_score_top3", 0.0) or 0.0)
    leaders = payload.get("leaders", [])
    candidate_symbols = [str(item.get("ticker", "")).strip() for item in leaders if str(item.get("ticker", "")).strip()]
    top_ticker = candidate_symbols[0] if candidate_symbols else ""
    top_signal = float(leaders[0].get("signal_score", 0.0) or 0.0) if leaders else 0.0
    top_change = float(leaders[0].get("change_pct", 0.0) or 0.0) if leaders else 0.0

    if not (session.get("us_premarket") or session.get("us_regular")):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": "시장 윈도우 외 — 미국주식 데스크 대기",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["프리마켓 또는 정규 세션 중 미국 핵심 리더 검토"],
        }
    if session.get("us_premarket") and not session.get("us_regular"):
        return {
            "action": "pre_market_watch",
            "size": "0.00x",
            "focus": f"{top_ticker or '미국 리더'} 장전 감시만",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["미국 진입 전 정규 세션 확인 대기"],
        }
    if regime == "STRESSED":
        return {
            "action": "capital_preservation",
            "size": "0.00x",
            "focus": "위기 국면 — 신규 미국주식 노출 없음",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": ["위험위원회가 신규 미국주식 진입 차단"],
        }
    if leaders and (top_signal < 0.56 or top_change >= 8.5):
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": f"과열 또는 확인 미흡 미국 리더 {top_ticker} 스킵",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"상위 후보 시그널 {top_signal:.2f} / 일간변동 {top_change:.2f}%",
                "더 깔끔한 미국 추종 셋업 대기",
            ],
        }
    if quality_score < 0.72 or avg_signal < 0.62 or active_us_count < 3 or avg_change < 0.35:
        return {
            "action": "stand_by",
            "size": "0.00x",
            "focus": "미국주식 추종 품질 부족",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"품질 {quality_score:.2f} / 활성 리더 {active_us_count} / 평균변동 {avg_change:.2f}% / 평균시그널 {avg_signal:.2f}",
                "미국 롱 탐색 전 강한 정규 세션 폭 필요",
            ],
        }

    if active_us_count >= 4 and quality_score >= 0.76 and avg_change >= 0.55 and avg_volume >= 2000000 and avg_signal >= 0.66 and stance != "DEFENSE":
        return {
            "action": "probe_longs",
            "size": "0.25x" if stance == "BALANCED" else "0.40x",
            "focus": f"미국 리더 {top_ticker} 추종",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"미국 리더 {active_us_count}종목 강세",
                f"품질점수 {quality_score:.2f} / 평균변동 {avg_change:.2f}% / 평균거래량 {int(avg_volume):,} / 평균시그널 {avg_signal:.2f}",
            ],
        }
    if active_us_count >= 3 and quality_score >= 0.7 and avg_signal >= 0.6:
        return {
            "action": "selective_probe",
            "size": "0.15x",
            "focus": f"{top_ticker} 추종 대기",
            "symbol": top_ticker,
            "candidate_symbols": candidate_symbols,
            "notes": [
                f"모니터링 가치 미국 후보 {active_us_count}종목",
                f"품질점수 {quality_score:.2f} / 평균변동 {avg_change:.2f}% / 평균거래량 {int(avg_volume):,} / 평균시그널 {avg_signal:.2f}",
            ],
        }
    return {
        "action": "stand_by",
        "size": "0.00x",
        "focus": "현재 품질 미국 리더 셋업 없음",
        "symbol": top_ticker,
        "candidate_symbols": candidate_symbols,
        "notes": [f"약한 미국 추종 스킵 (품질 {quality_score:.2f} / 시그널 {avg_signal:.2f})"],
    }

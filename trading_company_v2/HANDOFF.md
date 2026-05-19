# Trading Company V2 Handoff

## 0. Claude - 2026-05-19 (전략 코드 전체 리셋: Strategy B + D만 유지)

### 배경
기존 코드에 미검증 전략이 대거 누적되어 진입 0건 지속.
모든 전략 코드를 리셋하고 백테스트 통과 전략(B, D)만 남김.
새 전략은 코인 + 주식 모두 백테스트 통과 후에만 코드에 추가.

### 수정 내용 (커밋 3159af1)

**crypto_desk_agent.py**: 796 → 163 lines
- 18마켓 병렬 스캐너, 복잡한 combined_score 전체 제거
- ETH 4H 신고점 돌파 체크 (_check_eth4h_breakout) + BTC 방향성만 유지

**korea_stock_desk_agent.py**: 577 → 146 lines
- Path A (gap-up), Path C (reversal), Path D (close drive), Path E (gap fill), Path F (pullback MA) 전체 제거
- Path B (60일 신고점 돌파) + 수급 점수만 유지

**recommendation_engine.py**: 2,951 → 311 lines
- build_crypto_plan: STRESSED → eth_4h_breakout → watchlist_only (3단계)
- build_korea_plan: 시간 체크 → STRESSED → new_high_breakout → stand_by (5단계)
- build_us_plan: 기존 그대로 유지

### 규칙
새 전략 추가 절차:
1. 백테스트 스크립트 작성 (Desktop/backtest/)
2. 코인 + 한국 주식 모두 테스트
3. 통과 기준: Sharpe ≥ 1.2 / WR ≥ 48% / MDD ≥ -12%
4. 통과 후 코드 추가

### 백테스트 큐 (우선순위 순)
1. Ross Cameron — First Pullback (인트라데이 첫 눌림목)
2. MONGTATA 에어본 — 평균회귀
3. Linda Raschke — Holy Grail (ADX+EMA pullback)
4. ICT — CHoCH/BOS/FVG
5. Mark Minervini — VCP (Volatility Contraction Pattern)
6. Emma — 스캘핑 (Keltner+Supertrend+MACD)
7. Moritz Neo — 마이크로 스캘핑
8. 이미지 기반 — 자동 추세선 돌파

---

## 0. Claude - 2026-05-19 (Strategy D: ETH 4H 신고점 돌파 전략 실전 적용)

### 배경
코인 전략 v1~v4 백테스트 완료 후 ETH 4H 신고점 돌파 전략 채택.
기존 crypto_desk_agent는 15m 기반 복잡 멀티전략이나 hot-path 의존으로 실질적 진입 0건.
→ 독립적인 4H 스윙 전략(Strategy D) 추가로 crypto desk 재활성화 도모.

### 백테스트 결과 (coin_strategy_v3.py / v4.py)
| 항목 | 값 |
|------|-----|
| 대상 | KRW-ETH, 2025-2026 (약 13개월) |
| 승률 | 61.1% |
| 평균 PnL | +2.72%/trade |
| Sharpe | 2.33 |
| MDD | -7.08% |
| n | 18 |
| 슬리피지 0.30% 생존 | Sharpe 1.90 |
| 2026Q2 issue | 5건 WR 20% (분배형 장세 — 레짐 필터로 제거 불가) |
| RSI_max 70 완화 | Q2 3건 필터 가능 (vol_ratio 3.38-4.37x, RSI 62-73) |

### 전략 파라미터
- 유니버스: KRW-ETH 단독
- 타임프레임: 4H (240분봉)
- 진입: close > 20봉 최고가 + vol ≥ 2.5x + RSI 50-70 + close > EMA20 + BTC EMA200 상승장
- 청산: TP +7% / SL -3% / trail(peak≥4%: giveback 3.5%, floor 1.5%) / max 20봉 (80h)

### 수정 내용 (커밋 d0ad809)

**market_gateway.py** — `get_upbit_4h_candles()` 추가
```python
def get_upbit_4h_candles(market, count=25):
    return get_upbit_minute_candles(market, unit=240, count=count)
```

**crypto_desk_agent.py** — EMA/RSI 헬퍼 + `_check_eth4h_breakout()` + payload 추가
```python
# payload에 **_check_eth4h_breakout() 병합
# 조건: BTC 4H close > EMA200 + ETH 4H 신고점 + vol≥2.5x + RSI 50-70 + EMA20
```

**recommendation_engine.py** — `eth_4h_breakout=True` 시 probe_longs 직접 반환
```python
if payload.get("eth_4h_breakout"):
    return {"action": "probe_longs", "symbol": "KRW-ETH",
            "strategy_id": "crypto.eth_4h_breakout", "size": "1.00x", ...}
# candidate_symbols=[] → hot-path 차단 우회 (4H 신호는 사이클 직접 진입 허용)
```

**state_store.py** — `_crypto_eth4h_trail_rules()` + 전용 청산 분기
```python
# _position_thresholds()
'eth_4h_breakout' in focus: target 7.0%, stop -3.0%, max 36000 cycles (80h)

# _crypto_eth4h_trail_rules()
peak >= 7%: giveback 4.0%, floor 4.0%
peak >= 4%: giveback 3.5%, floor 1.5%
peak >= 2%: giveback 2.0%, floor 0.0%

# 청산 분기: is_range_scalp 다음, 일반 추세추종 전
# 15m 노이즈 기반 조기청산(no_lift_exit, trend_exit) 비적용
```

**execution_agent.py** — `_infer_strategy_id()`에 eth_4h_breakout 패턴 등록

### 아키텍처 핵심
- candidate_symbols=[] → `candidate_meta` 비어있음 → hot-path 차단 코드(lines 1584-1633) 미적용
- signal_freshness=1.0 명시 → stale_signal_block 차단 없음
- btc_corr_15m=0.85 명시 → high_corr_cap 계산에 반영

### 배포
Oracle VM 재시작 완료 (2026-05-19 06:25 UTC), 사이클 정상 동작 확인.

---

## 0. Claude - 2026-05-19 (Strategy B: 60일 신고점 돌파 전략 실전 적용)

### 배경
기존 Korea 전략 (probe_longs gap-up 탐색)의 기대 수익률이 -0.28%/trade로 음수.
전략 방향 전체 재설계 결정 → 백테스트 3개 후보 동시 테스트 → Strategy B 채택.

### 백테스트 결과 (korea_wide_backtest.py)
| 항목 | 값 |
|------|-----|
| 대상 | 152종목 (전 섹터), 2022-2025 (3년) |
| 승률 | 84.6% |
| 연간 수익 | +89.5% |
| Sharpe | 6.16 |
| MDD | -4.0% |
| Walk-forward OOS diff | 3.5% (과적합 아님) |
| 슬리피지 0.30% 생존 | annual +80.5%, Sharpe 5.35 |

### 전략 파라미터
- 진입 조건: 60일 신고점 + 거래량 ≥ 20일 평균 2배 + RSI 55-80 → 3조건 이상 충족
- 청산: target +10%, stop -4%, trail trigger 5% (peak≥5%: 4% giveback, peak≥10%: 6% giveback)
- 최대 보유: 20 거래일 (2700 cycles)

### 수정 내용 (커밋 2d18633)

**korea_stock_desk_agent.py** — Path B 파라미터 교체
```
fetch count: 42 → 65 (60일 데이터 확보)
min candles: 22 → 62
breakout_period: 20 → 60
vol_surge_mult: 2.5 → 2.0
rsi_max: 78 → 80
confirmed_count threshold: >=2 → >=3
+ focus_tag='new_high_breakout' 추가 (state_store 라우팅용)
```

**state_store.py** — Strategy B 전용 trail + threshold
```python
# _korea_newhi_trail_rules()
peak >= 10%: giveback 6%, floor 6%
peak >= 5%:  giveback 4%, floor 2%
peak < 5%:   trail 없음 (hard stop -4%만 작동)

# _position_thresholds()
'new_high_breakout' in focus: target 10%, stop -4%, max 2700 cycles
```

**execution_agent.py** — focus_tag → position focus 전파
```python
# _apply_korea_candidate_snapshot
elif "breakout" in base_focus:
    if snapshot.get("focus_tag") == "new_high_breakout":
        focus = "new_high_breakout: {name} ({symbol}) 60-day new high breakout"
        entry_profile = "new_high_breakout"
        strategy_id = "korea.new_high_breakout"

# _infer_strategy_id: 'new_high_breakout' 패턴 추가
```

### 배포
Oracle VM 재시작 완료 (2026-05-19)

---

## 0. Claude - 2026-05-19 (Korea 피라미드 수익 하한선 잠금 재활성화)

### 배경
5/14 단 하루 -106,900원 (KIS 1천만 기준 paper):
- 141080 -4.03% x0.10 = -40,300원 (즉시 stop)
- 032500 피라미드 -3.12% x0.20 = -62,400원 (2분 만에 stop)
- 218410 피라미드 -2.23% x0.20 = -44,600원 (2분 만에 stop)
피라미드(0.20x = 기존 2배 사이즈)가 고점에 진입 후 즉시 반전 → 수익이 손실로 전환.

### 수정 내용 (커밋 bb63e47)

**수익 하한선 잠금 (profit-floor lock)**
피라미드 진입 전에 기존 포지션의 `peak_pnl_pct`를 강제 상향:
```python
_required_peak = position.pnl_pct + trail_giveback + 0.2
if position.peak_pnl_pct < _required_peak:
    position.peak_pnl_pct = _required_peak
# → protect_level >= current_pnl + 0.2% 보장
# → 피라미드 실패해도 기존 포지션은 현재 수익 이상에서 청산
```

**사이즈 축소**: 0.20x → **0.10x** (기존 포지션과 동일 사이즈)

**재활성화**: `korea.pyramid`를 `_PERMANENTLY_DISABLED`, `_RETIRED_STRATEGY_IDS`에서 제거

### 현재 손익 현황 (paper, KIS 1천만 + Upbit 200만 기준)
| 날짜 | 손익 |
|------|------|
| 5/12 | +31,724원 |
| 5/13 | +18,834원 |
| 5/14 | **-106,900원** (피라미드 문제) |
| 5/15 | -4,086원 |
| 5/18 | -27,350원 |
| 5/19 | -40,422원 |
| **누적** | **-128,200원** |

---

## 0. Claude - 2026-05-19 (stream=0 cycle-path 진입 차단)

### 배경
사이클 진입 포지션이 시작하자마자 음수(-0.55%, -0.92%, -0.23%)로 떨어지고
`peak_pnl=0.0` → `no_lift_exit` 패턴. 전부 공통점: **`stream=0.00`** (WebSocket 틱 없음).

실전 데이터 결과:
| 코인 | stream | peak_pnl | 결과 | 청산 이유 |
|------|--------|----------|------|-----------|
| KRW-ONDO | 0.00 | 0.0 | -0.92% | no_lift_exit |
| KRW-BTC | 0.00 | 0.0 | -0.23% | flat_no_lift_exit |
| KRW-AKT | 0.00 | 0.0 | -0.55% | (open) |
| KRW-ONT | 0.00 | 0.0 | 방금 오픈 | (open) |

**stream=0 사이클 진입 = 100% 손실** (과거 설계 주석 "저거래량 시간대에도 허용"이 잘못된 가정)

### 수정 — execution_agent.py (커밋 f602ae8)

`_crypto_cycle_entry_override_ok` 맨 앞에 조기 차단 추가:
```python
if meta_stream <= 0.0:
    return False, "stream=0 cycle override blocked (no tick activity, all stream=0 entries historically lose)"
```

WebSocket 틱이 없으면 실시간 모멘텀 확인 불가 → cycle 진입 비허용.
틱이 있을 때 (hot-path)만 자동 진입 또는 stream > 0 시간대에만 cycle override 허용.

### 현재 오픈 포지션 처리
KRW-AKT, KRW-ONT (stream=0로 진입된 포지션) → 기존 `no_lift_exit` 로직이 10분 후 자동 청산.

---

## 0. Claude - 2026-05-19 (execution_agent dict 속성 접근 + korea_stock_desk empty 크래시 수정)

### 배경
재배포 후 `execution_agent: error: 'dict' object has no attribute 'desk'` 발생.
`safe_run()`이 무음으로 삼켜 `orders=[]`로 복귀.
동시에 한국 장 외 시간에 `korea_stock_desk_agent: error: max_workers must be greater than 0` 크래시.

---

### 버그 4: open_positions dict 속성 접근 오류 — execution_agent.py (커밋 015393e)

**문제**: `self.open_positions: list[dict]`이지만 lines 856-875에서
`p.desk`, `p.focus`, `p.entry_profile`, `p.status` 속성 접근 사용.
→ `AttributeError: 'dict' object has no attribute 'desk'`
→ `safe_run()` 삼킴 → 모든 사이클 `orders=[]`.

**수정**: 해당 구간 전체를 `p.get("desk")`, `p.get("focus", "")` 등 dict 접근으로 변경.

---

### 버그 5: top15 빈 리스트 시 max_workers=0 — korea_stock_desk_agent.py (커밋 015393e)

**문제**: 한국 장 외 시간 / 갭 후보 없을 때 `top15=[]`
→ `ThreadPoolExecutor(max_workers=min(8, 0))` → `ValueError: max_workers must be greater than 0`.

**수정**: `max(1, min(8, len(top15)))` + 빈 리스트 가드 `if top15 else []`.

---

### 최종 상태 (배포 완료 - 2026-05-19 00:00 UTC)
- `execution_agent`: `orders=2` 정상 복구 확인 (KRW-AKT selective_probe + korea pre_market_watch)
- `korea_stock_desk_agent`: `reason: Full universe scan + sentiment...` 정상
- 업비트: `paper` 모드 확인 (`EXECUTION_MODE=paper`, `UPBIT_ALLOW_LIVE=false`)
- market_data_agent: `57 crypto leaders` 포착 (신규 상장 코인 자동 포함 활성)

### 다음 세션 확인사항
- 실전 전환 전 paper PnL 추적 (selective_probe WR 목표 ≥48%)
- Korea 장중 orders 발화 여부 (09:00 KST 이후)

---

## 0. Claude - 2026-05-18 (orders=[] 근본 원인 3개 수정)

### 배경
이전 세션에서 RANGING→TRENDING 개선(±0.08→±0.06, cycle override, pullback_long 추가)을 배포했지만
사이클 저널의 `total_orders=0`이 지속됨. 근본 원인 3개를 발굴·수정.

---

### 버그 1: UnboundLocalError — execution_agent.py (커밋 9b68039)

**문제**: `_plan_to_order` 내 `strategy_recovery_allowed` 변수가 라인 803에서 사용되지만
정의는 라인 887에서 이루어짐. `safe_run()`이 예외를 무음으로 잡아 `payload={}` 반환
→ 모든 사이클에서 `orders=[]`.

**수정**: 라인 800 직전에 `entry_profile` / `strategy_id` 조기 계산 블록 추가.
라인 887의 최종 계산은 그대로 유지 (동일 값으로 재계산, 동작 변화 없음).

---

### 버그 2: adx_trend_strong 게이트 이진 과민 — 3개 파일 (커밋 2b8dc13)

**문제**: `adx_trend_strong = (adx_val >= 22) AND (DI+ > DI-)`.  
KRW-HYPER: ADX=47.9인데 단기 pullback 중 DI->DI+ 역전으로 `adx_trend_strong=False`.
→ cycle override, hot_path_guard, recommendation_engine 모두 차단.

**수정 (`execution_agent.py`, `hot_path_guard.py`, `recommendation_engine.py`)**:
```python
_adx_ok = adx_trend_strong or (
    adx_val >= 35.0          # ADX 수치 직접 확인 (고강도 추세)
    and choch_bullish         # CHoCH 불리시 (구조 전환 확인)
    and trend_alignment in ("trend_long", "pullback_long")
)
```

---

### 버그 3: strategy_id 오인식 → candidate_rotation 영구 차단 — execution_agent.py (커밋 b509625)

**문제**: cycle override 통과 후 `_apply_crypto_candidate_meta`가 focus에
"candidate-specific multi-coin entry" 텍스트 삽입 → `_infer_strategy_id`가
`crypto.candidate_rotation` 반환 → 영구 차단 전략 → `status=idle`.

**수정**: `eligible_candidates` 비어있지 않을 때 plan에 명시적으로
`strategy_id="crypto.selective_probe"`, `entry_profile="selective_probe"` 스탬프.

---

### 최종 상태 (배포 완료)
- `total_orders`: 0 → **2** (crypto + korea 정상 생성)
- cycle override: KRW-HYPER 조건 충족 시 `ok=True` 확인 완료
- 현재 KRW-HYPER: `trend=late_extension` (과이격) → 차단 정상
- 다음 진입 조건: `trend_long/pullback_long` 복귀 + `choch_bullish` + `adx_val>=35`

### 다음 세션 확인사항
- 실제 거래 발생 여부 모니터링 (live_order_log)
- Korea 시장 개장 시 (월 09:00 KST) attack_opening_drive 발화 여부
- 누적 데이터 기반 crypto.selective_probe WR 모니터링

---

## 0. Claude - 2026-05-18 (전체 재점검: RANGING→TRENDING + cycle override + pullback_long)

### 수정 내용 (커밋 3b9f450, 1835829)

**문제**: RANGING 레짐에서 모든 전략 차단 → 거래 0건.
- `abs(trend_score - 0.5) <= 0.08` → RANGING 과다 판정
- TRENDING에서도 cycle override 차단 → hot-path 없으면 진입 불가

**1. `orchestrator.py` — RANGING 밴드 축소**
- `±0.08` → `±0.06` (→ TRENDING 전환 빈도 증가)

**2. `execution_agent.py` — `_crypto_cycle_entry_override_ok` 재작성**
- TRENDING regime 지원 추가
- ADX 강세 + CHoCH 불리시 경로: RANGING/TRENDING 공통 허용

**3. `recommendation_engine.py` + `hot_path_guard.py`**
- RANGING-override 추세 경로 추가 (ADX 강세 + CHoCH + trend_long/pullback_long)

**4. `state_store.py` — 전략 비활성화 임계값 완화**
- `catastrophic_peak0`: count>=2 → >=5
- `repeated_stop_like`: count>=3 → >=6

---

## 0. Claude - 2026-05-18 (BB 스퀴즈 브레이크아웃 전략 배선)

### 수정 내용 (커밋 bbad60b)

**문제**: `bb_squeeze_breakout` 신호가 `signal_engine`에서 계산되었으나 파이프라인에 연결되지 않아 RANGING 시장에서 진입 기회를 100% 놓침.

**1. `hot_path_guard.py` — RANGING 게이트 추가**
- RANGING 블록 마지막 `return False` 직전에 `bb_squeeze_breakout` 전용 게이트 추가
- 조건: `combined>=0.56`, `signal_freshness>=0.48`, `ob>=0.90`, `rsi<=80`, `micro3>=0.0`, `stream_score>=0.40`
- hard_overheat / rsi_bearish_divergence / micro_exhausted / stream_reversal 차단

**2. `recommendation_engine.py` — ranging_blend 추가**
- 리드 마켓 bb_squeeze_breakout: signal_score>=0.58, ob>=0.92 → size 0.28x~0.38x
- 전체 후보군 스캔(상위 20개): combined>=0.60, ob>=0.90 → size 0.22x~0.30x

**3. `execution_agent.py` — 전략 ID 인식**
- `"bb_squeeze_breakout"` / `"bb 스퀴즈"` 텍스트 → `crypto.bb_squeeze_breakout` 매핑

**배포**: Oracle VM 07:01 UTC 재기동, 첫 사이클 확인 완료 (`risk_budget=0.32` 유지)

### 다음 세션 확인사항
- bb_squeeze_breakout 진입 로그 모니터링 (RANGING 레짐 + BB 압축 발생 시)
- RANGING 전략 추가 발굴: range_scalp / pullback_gap 신호 품질 점검
- Korea micro/orderbook 파이프라인 구축 (장기)

---

## 0. Claude - 2026-05-18 (risk_budget 회복: streak decay + crypto_growth_mode + Korea 신호)

### 수정 내용 (커밋 0e4823b, e664e13)

**진단 결과**: risk_budget=0.18 (floor) — DB 확인 결과
- Open positions: 0개
- 오늘 손실 1건: 090710 -3.55% (68h 크래시 때 묶혔다 02:26 UTC 자동 청산)
- Crypto 연패: 8 (마지막 손실 May 15, 68시간 전)
- 계산: 0.28 × 0.55 = 0.154 → floor 0.18

**1. `crypto_growth_mode` 버그 수정** (`risk_committee_agent.py`)
- 이전: `active_desks == {"crypto"}` → Korea 병행 시 False → floor 0.18
- 수정: `"crypto" in active_desks` → Korea+Crypto 혼합에도 crypto 기준 적용
- 효과: floor 0.18 → 0.32, exposure_warn 0.9 → 1.65

**2. 연패 streak 시간 감쇠** (`risk_committee_agent.py`, `state_store.py`)
- `load_hours_since_last_loss()` 함수 추가
- 마지막 손실 24h+ 이전이면 24h마다 streak -1 (최대 -3 감쇠)
- 현재: streak=8, 68h 경과 → effective=6, mult 0.55→0.60
- 재활성화: 68h/24 = 2 감쇠 적용

**결과**: risk_budget 0.18 → **0.32** (78% 개선, 로그 확인 완료)

**3. Korea debate 미사용 신호 배선** (`korea_stock_desk_agent.py`)
- `inst_radar_count` → quality_score: +0.10(2개+), +0.05(1개)
- `in_open_window` → +0.06 (09:00~09:40 KST 갭 모멘텀 보너스)
- `in_close_window` → -0.05 (15:00~15:30 KST 마감 리스크)

### 다음 세션 확인사항
- 내일(May 19) 새 거래에서 crypto streak이 깨지면 risk_budget 0.48+ 기대
- Korea inst_radar 신호 실제 포착 여부 로그 확인
- RANGING 전략 부족 이슈 장기 개선 (레짐 감지 속도, 레인지 특화 전략)

---

## 0. Claude - 2026-05-18 (전략 개선: obvious_trend 비활성화 + recovery size + Korea 품질)

### 수정 내용 (커밋 634e6d8)

**1. `obvious_trend` 전략 완전 비활성화**
- 파일: `hot_path_guard.py`, `recommendation_engine.py`
- 근거: 13건 WR 0%, peak=0.000% — 임계값 강화해도 개선 없음
- `obvious_trend_ok = False`, `obvious_trend_ride_ok = False`
- 재활성화 기준: 백테스트 WR >= 55% 확인 후

**2. Recovery 전략 최소 size 0.10x 보장**
- 파일: `execution_agent.py`
- 문제: 8연패 후 risk_budget 축소 → scaled_notional_pct=0.04~0.06x → 회복 불가 루프
- 수정: `strategy_recovery_allowed` 상태에서 stop-pressure 없으면 최소 0.10x 보장

**3. Korea `selective_probe` 진입 품질 강화**
- 파일: `recommendation_engine.py`
- `avg_signal` 0.48 → 0.54, `top_candidate_score` 0.52 → 0.56, `quality_score` 0.50 → 0.54
- 근거: Korea selective_probe WR 38% → 저품질 진입 차단

**4. `attack_opening_drive` 장초반 창 확장**
- 파일: `recommendation_engine.py`
- `opening_window OR in_open_window` — 장초반 40분(KST 09:00~09:40) 적극 활용

**5. `range_impulse` combined 임계값 강화**
- 파일: `hot_path_guard.py`
- `combined >= 0.38` → `combined >= 0.45` — RANGING 약한 신호 오진입 차단

### 분석 요약 (전략 전수 감사)
- 41개 전략 중 수익: `crypto.selective_probe` 100% WR, `crypto.attack_opening_drive` 100% WR
- 손실: `obvious_trend` 0% WR (13건), RANGING 전략 전반 저조
- 핵심 문제: RANGING 시장에서 가용 전략 4개뿐 → 장기 개선 과제
- Korea combined_score 미구축 → `volume_ratio + foreign_net_buy` 활용은 장기 과제

### 다음 세션 확인사항
- obvious_trend 비활성화 후 사이클 로그에서 obvious_trend 진입 시도가 사라지는지 확인
- Korea selective_probe 품질 강화 후 WR 변화 모니터링 (현재 38%)
- RANGING 전략 장기 개선: regime 감지 속도 향상, 레인지 특화 전략 발굴

---

## 0. Claude - 2026-05-15 (3차 감사 잔여 수정: ict_structure + ssl_sweep + Korea overheat)

### 수정 내용 (3건, 커밋 eb55f9f)

**1. `ict_structure == "bearish_break"` dead variable → 실제 차단 게이트 활성화**
- 파일: `recommendation_engine.py`
- 문제: `ict_structure`가 f-string 로그에만 쓰이고 진입 차단 로직 없음
- 수정: `choch_bearish` 블록 바로 아래에 gate 추가
  - `ict_structure == "bearish_break" and signal_score < 0.65` → `capital_preservation` 반환

**2. `ssl_sweep_confirmed` hot_path_guard 완전 누락 → 추출 + 임계값 완화 적용**
- 파일: `hot_path_guard.py`
- 문제: recommendation_engine에서는 ICT entry gate로 쓰이지만 hot_path_guard에는 미추출
- 수정: 추출 추가 + `_ema_stack_relax`에 `+0.015 if ssl_sweep_confirmed` 항목 추가
  - SSL 스윕 = 스마트머니가 매도 유동성 소화 후 상승 의도 → 진입 임계값 1.5% 완화

**3. Korea Path B/F 과열 페널티 불완전 → `burst_change_pct`, `ema_gap_pct` 추가**
- 파일: `korea_stock_desk_agent.py`
- 문제: RSI >= 78 체크만 있고 급등폭/EMA 이격 기준 없음
- 수정: `burst_change_pct >= 12% → +0.08`, `ema_gap_pct >= 12% → +0.06` 페널티 추가

### 변경 파일
- `app/agents/korea_stock_desk_agent.py`
- `app/services/hot_path_guard.py`
- `app/services/recommendation_engine.py`

### 배포
- git push origin main + Oracle VM pull & restart 완료 (active × 2)

### 다음 세션 확인사항 (추가 잔여 이슈)
- Korea debate agents: `avg_sentiment_top3`, `inst_radar_count`, `in_open_window`/`in_close_window` bull/bear scoring 미사용
- `pullback_gap_pct`, `range_4_pct`: signal_engine 계산, 어디서도 미사용
- `bb_sq_width`: BB 스퀴즈 폭 수치, 계산되지만 미사용

---

## 0. Claude - 2026-05-15 (지표 감사 + 미연결 신호 11개 전체 배선)

### 배경
- 사용자: "내가 얘기한 지표들이 한두개가 아닌데 왜 제대로 적용이 안되냐"
- 전체 감사 결과: signal_engine에서 계산만 되고 결정 로직에 전혀 안 쓰이는 신호 15개, 추출만 되고 노트에만 쓰이는 수치 13개 발견
- 그 중 actionable한 11개 즉시 연결

### 버그 수정

**1. RSI 불리시 다이버전스 RANGING 경로 단절 버그**
- 문제: `rsi_bullish_div` 진입 로직이 line~1896에 있는데, RANGING 블록이 line 356에서 먼저 return → 세력 신호가 RANGING에서 영구 도달 불가
- 수정: RANGING 블록 최상단에 세력 신호 우선 체크 추가
  - `rsi_bullish_div + long_flip_confirmed + signal >= 0.42 + ob >= 1.02` → `probe_longs 0.40~0.50x`
  - `mean_rev_count` 15번째 신호로도 추가, `_hard_oversold_flag`에도 포함

**2. vol_breakout 캐시 미스 차단 버그**
- 문제: `_daily_persist()` 캐시 미스 시 0.5 반환, 임계값 0.52로 설정 → vol_breakout 항상 차단
- 수정: 3곳 모두 0.52 → 0.48 (캐시 미스 중립값 0.5가 통과되도록)

**3. RANGING probe_longs 0% 승률 구조**
- 문제: `mean_rev_count >= 1` 단일 soft signal 진입 → 8건 전패
- 수정: `mean_rev_count >= 2 + hard_oversold 1개 이상 + signal >= 0.58`
  - hard_oversold = RSI extreme / Williams%R / CCI / MFI / Keltner / rsi_bullish_div 중 1개

### 미연결 신호 11개 배선 (recommendation_engine.py + hot_path_guard.py)

| 신호 | 적용 |
|---|---|
| `ema_stack_bullish` | `trend_ignition_score` +0.04, `_high_quality_signal` 조건, hot_path 임계값 -0.02 완화 |
| `breakout_score` (수치) | `trend_ignition_score` ×0.05, `_high_quality_signal` 조건, `vol_breakout` >= 0.45 수치 기준 |
| `bsl_sweep_confirmed` | `short_pressure_visible` 추가, hot_path combined < 0.72 롱 차단 |
| `price_above_trend_ema` | `trend_ignition_score` ±조정, `_high_quality_signal` 필수, `direct_entry_ok` 필수, hot_path < 0.68 차단 |
| `rsi_reset_confirmed` | `trend_ignition_score` +0.03, hot_path 임계값 -0.01 완화 |
| `at_bb_upper` | `short_pressure_visible` 추가, hot_path combined < 0.76 차단 |
| `adx_val` (수치) | `_adx_trend_ok = adx_val >= 20` 수치 기준 → `direct_entry_ok`에 사용 |
| `ict_score` (수치) | `ict_entry_ok`에 `ict_score >= 0.10` 경로 추가 (이전: 완전 dead variable) |
| `airborne_short` | `short_pressure_visible` 추가, hot_path combined < 0.74 차단 |
| `bb_pct_b` (수치) | `trend_ignition_score` +(pct_b-0.5)×0.04, RANGING 진입 사이즈 < 0.25 시 확대 |
| `kill_zone_name` | `ignition_note` 로그에 킬존 이름 표시 |

### 변경 파일
- `app/services/recommendation_engine.py`
- `app/services/hot_path_guard.py`

### 검증
- git push origin main → Oracle VM pull & restart 완료
- 커밋: `fix: RANGING probe_longs`, `fix: vol_breakout persistence gate`, `fix: rsi_bullish_div RANGING path`, `feat: wire 11 unused indicators`

### 다음 세션 확인사항
- `vol_breakout` 실제 발화 여부 (캐시 미스 버그 수정 후 첫 TRENDING 조건 확인)
- 세력 신호(`rsi_bullish_div`) RANGING 진입 발화 여부
- `at_bb_upper` + `airborne_short` 차단 효과 — 잘못된 저항권/과열 진입 감소 여부
- Korea `attack_opening_drive` 승률 유지 확인 (86% → 변화 없어야 함)

---

## 0. Claude - 2026-05-15 (전략 앙상블 개선 + 세력 신호 추가)

### 배경
- 전체 5건 거래 모두 손실 (0% 승률): Korea 3 stop_hit, Crypto 2 momentum_collapse_exit
- 문제 진단:
  1. 연패 이중 패널티: BearCase +0.18 + RiskCommittee budget 축소 → PM이 강한 신호도 차단
  2. 순서형 판단: 전략들이 독립 평가 없이 combined score로만 필터링
  3. 한국 주식 추격 진입: burst>=4%+RSI>=72 과열 구간에서 진입 후 즉시 stop_hit

### 변경 내용
- `debate_agents.py`:
  - BearCase 연패 패널티 +0.18 → +0.06 (RiskCommittee가 이미 budget 축소)
  - PM 차단 임계치 bear>=0.78 → bear>=0.88 (훨씬 강해야 차단)
  - 강한 독립 신호(bull>=0.82, divergence_confirmed) → `strong_signal_probe` 패스
  - 연패 사이즈 컷 0.72x → 0.80x
- `recommendation_engine.py`:
  - `_stock_intraday_extended()`: burst>=6% 또는 burst>=4%+RSI>=72 → 과열 필터 (추격 차단)
  - `_stock_pullback_quality()`: 눌림목(RSI 50-65) + 다이버전스 보너스 계산
  - 모든 선별 진입에 과열 체크 추가, `strategy_confidence='high'` 플래그 전달
- `signal_engine.py`:
  - `summarize_rsi_bullish_divergence()`: 세력 신호 감지 (가격LL + RSI HL + 축적 구간)
  - `summarize_equity_signal()` bias: offense/defense → bullish/bearish (기존 버그 수정)
  - 다이버전스 확인 시 score 보너스 자동 적용
- `korea_stock_desk_agent.py`:
  - `bullish_divergence_ok`, `divergence_strength` candidate에 전달

### 검증
- 문법 OK (py_compile 통과)
- PM debate 유닛 테스트: bull=0.85 → strong_signal_probe 정상 동작 확인
- VM 배포 완료 (trading-loop, trading-dashboard 재시작)
- equity_signal bias 버그 수정으로 breakout path도 이전보다 더 잘 발화할 것

### 다음 세션 확인사항
- 오늘 장중 한국 주식: `_stock_intraday_extended()` 필터가 과열 종목 잡아내는지 로그 확인
- divergence 신호가 candidate_score에 반영되는지 스캐너 페이지에서 확인
- 추가 고려: ML 학습 데이터에 divergence 피처 추가 (다음 일요일 재학습 전)

---

## 0. Latest Codex Notes - 2026-05-15 (session 46 - controlled cycle entry override)

### Why this change was needed
- After scanner-wide selection, live plan could select candidates like `KRW-POLYX` as `crypto.smart_money_flow`.
- Execution still converted crypto cycle entries into watch-only:
  - `RANGING impulse candidates armed for tick confirmation.`
- This preserved safety, but it also caused strong structure-confirmed RANGING candidates to wait for websocket ignition only.

### Changes
- Updated `app/agents/execution_agent.py`:
  - Added `_crypto_cycle_entry_override_ok()`.
  - Allows a small cycle-level entry only for:
    - `crypto.smart_money_flow`,
    - `crypto.ranging_strength_follow`.
  - Still blocks if RSI/extension/recent move are too hot, bearish structure appears, stream reversal appears, or the symbol recently failed.
  - Smart-money override requires:
    - capital flow + box/trendline structure,
    - combined score,
    - flow score,
    - supportive orderbook,
    - positive micro timing.
  - Ranging-strength override requires:
    - high combined/signal,
    - supportive orderbook,
    - positive micro timing,
    - controlled extension/RSI.

### Verification
- `python -m compileall app` passed.

## 0. Latest Codex Notes - 2026-05-15 (session 45 - scanner-wide smart money selection)

### Why this change was needed
- Live Oracle data showed `KRW-KAVA` as the crypto desk leader with `smart_money_flow_long=True`, but it was unsafe:
  - RSI about `79`,
  - trend extension about `4.7%`,
  - orderbook bid/ask about `0.33x`.
- Another scanned coin, `KRW-AZTEC`, had the cleaner reference-pattern setup:
  - capital flow,
  - box breakout,
  - auto trendline breakout,
  - orderbook about `2.2x`.
- The previous RANGING strategy blend judged mostly the desk leader, so clean second-rank smart-money candidates could be missed.

### Changes
- Updated `app/services/recommendation_engine.py`:
  - Added scanner-wide smart-money candidate selection from `all_candidates`.
  - Allows a non-leader coin to win the RANGING blend when it has:
    - `capital_flow_long`,
    - box or auto-trendline breakout,
    - candidate-specific long flip,
    - controlled RSI/extension,
    - acceptable orderbook and micro timing.
  - Keeps overextended leaders blocked instead of chasing them.

### Verification
- `python -m compileall app` passed.
- Replayed the live-style KAVA/AZTEC case:
  - leader `KRW-KAVA` was skipped,
  - `KRW-AZTEC` selected as `crypto.smart_money_flow`,
  - action `probe_longs`, size `0.22x`.

## 0. Latest Codex Notes - 2026-05-15 (session 44 - scanner visibility for smart money flow)

### Why this change was needed
- `crypto.smart_money_flow` was added from the user's chart references, but the scanner still only showed generic columns.
- User needs to see whether "capital inflow / smart money", box breakout, and auto trendline breakout are actually present before the bot enters or waits.

### Changes
- Updated `/scanner` embedded UI in `app/main.py`:
  - Added market summary card: `세력흐름`.
  - Added filter chip: `💜 세력흐름`.
  - Added table column: `세력`, showing `capital_flow_score`, flow volume ratio, box breakout, auto trendline breakout, and `SMF` tag.
  - Added status badges for `SMF`, `세력`, `박스`, and `작도`.
  - Added discovery section: `💜 세력흐름 / 작도 돌파`.
- This is intentionally UI-only. It does not change entry/exit behavior.

### Verification
- `python -m compileall app` passed.

## 0. Latest Codex Notes - 2026-05-15 (session 43 - smart money flow from user chart references)

### Why this change was needed
- User provided trader screenshots showing:
  - MACD-like long/short entry waves,
  - "capital inflow / smart money" zones,
  - box/range breakout tracking,
  - hand-drawn descending trendline/channel breakout projections.
- The goal was to convert these visual chart-reading ideas into objective bot signals without adding another pure defensive gate.

### Changes
- Added `smart_money_flow` crypto strategy:
  - `capital_flow_long`: MACD line above signal + histogram expansion + volume ratio + price/EMA confirmation.
  - `auto_trendline_breakout_long`: automatic descending swing-high trendline projection and breakout detection.
  - `flow_box_breakout_long`: box/range high breakout with volume-backed flow confirmation.
  - `smart_money_flow_long`: capital flow plus at least one structural confirmation.
- Propagated the new fields through:
  - `app/services/signal_engine.py`
  - `app/agents/crypto_desk_agent.py`
  - `app/services/recommendation_engine.py`
  - `app/services/hot_path_guard.py`
- Added cycle-path and websocket hot-path entries for `crypto.smart_money_flow`.
- Added strategy attribution and position thresholds:
  - target `+2.4%`
  - stop `-0.80%`
  - max hold about `16 min`

### Verification
- `python -m compileall app` passed.
- Replayed a RANGING payload with capital flow + auto trendline breakout:
  - result: `probe_longs`
  - `strategy_id=crypto.smart_money_flow`
  - `entry_profile=smart_money_flow`

## 0. Latest Codex Notes - 2026-05-15 (session 42 - unblock controlled RANGING strength)

### Why this change was needed
- User reported that after the prior guardrail work, there appeared to be no new trades.
- Oracle VM check showed the service was healthy and cycling every ~45s, but crypto stayed in `RANGING`.
- The active crypto plan kept saying:
  - `RANGING — trend-following blocked. Waiting for mean-reversion signal`.
- This correctly blocked old failed RANGING trend-chase paths, but it also blocked individual coins that were already showing controlled strength, such as `KRW-HYPER score 0.75`.

### Changes
- Added a new, separate crypto strategy:
  - `crypto.ranging_strength_follow`
- This does **not** reactivate the retired `crypto.ranging_momentum_leader`.
- It allows a small RANGING entry only when:
  - combined/signal score is strong enough,
  - long flip is confirmed,
  - short/falling-knife pressure is absent,
  - RSI is not extreme,
  - price is not overextended from EMA/VWAP,
  - micro/stream/orderbook or change-rate confirms controlled strength.
- Added cycle-path support in `app/services/recommendation_engine.py`.
- Added websocket hot-path support in `app/services/hot_path_guard.py`.
- Added strategy attribution in:
  - `app/agents/execution_agent.py`
  - `app/core/state_store.py`
- Added tighter position thresholds:
  - target `+1.6%`
  - stop `-0.55%`
  - max hold about `10 min`

### Verification
- Replayed a HYPER-like RANGING payload:
  - `signal=0.75`, `change=4%`, `RSI=80`, controlled EMA/VWAP deviation, long flip confirmed.
- Result changed from `watchlist_only` to:
  - `probe_longs 0.18x`
  - `strategy_id=crypto.ranging_strength_follow`

## 0. Latest Codex Notes - 2026-05-14 (session 41 - retired strategy loss filter)

### Why this change was needed
- User asked whether the bot is now blocking entries too much.
- Oracle analysis showed the answer was partly yes:
  - New entries were allowed globally, but Korea desk entries were still being paused by `desk loss pressure`.
  - The pressure came mostly from retired `korea.pyramid` follow-on losses, not from the currently allowed opening-drive/base strategies.
- This made the system treat a removed bad strategy as if the current strategy stack was still failing.

### Changes
- `app/agents/execution_agent.py`
  - Added `korea.pyramid` to the permanently disabled strategy list.
  - Added retired-strategy filtering for:
    - desk recent trades,
    - desk loss pressure,
    - desk stop pressure,
    - desk offense score,
    - recent same-symbol cooldown,
    - repeated-loss and symbol-edge calculations.
  - Korea desk now uses eligible current-strategy history for offense scoring instead of polluted daily totals.
  - The same retired-trade filter also excludes disabled crypto strategies:
    - `crypto.candidate_rotation`
    - `crypto.ranging_momentum_leader`
    - `crypto.ema_bounce`

### Verification
- Replayed the exact problematic Korea set:
  - `032500` base win, `218410` base win, `141080` base stop,
  - plus retired `032500/218410 korea.pyramid` losses.
- After filtering:
  - `loss_pressure=False`
  - `offense=balanced`
  - `032500/218410 cooldown=False`
  - `141080 cooldown=True`
- Result: broad Korea entry freeze is lifted, while the actually failed symbol still remains protected.

## 0. Latest Codex Notes - 2026-05-14 (session 40 - KIS routing and loss spike diagnosis)

### Why this change was needed
- User saw the day PnL suddenly move beyond `-1%` during a losing streak.
- Oracle analysis showed the sudden drawdown came from `korea.pyramid` follow-on entries created before the opening-drive pyramid block:
  - `korea.pyramid`: 2 trades, 0 wins, capital PnL about `-1.07%`.
  - Opening-drive base trades were mixed; the late add-ons gave back the earlier winners.
- KIS app did not show recent orders because broker routing fell back to paper:
  - Several KIS attempts failed with `unsupported_order_shape`.
  - Root cause for high-priced stocks: KIS sizing used `LIVE_CAPITAL_KRW=2M`; `0.10x` budget was about 200k, sometimes below one share.

### Changes
- `app/core/state_store.py`
  - Retired `korea.pyramid` completely. The strategy produced 2 peak-zero stop losses and erased about `-1.07%` capital from opening-drive winners.
  - Strategy health now marks `korea.pyramid` as `disabled_candidate`.
- `app/services/broker_router.py`
  - KIS readiness now checks `KIS_CAPITAL_KRW`, not generic `LIVE_CAPITAL_KRW`.
- `app/services/kis_broker.py`
  - KIS order sizing now uses `KIS_CAPITAL_KRW`.
  - Falls back to reference price and notional from order rationale when top-level fields are missing.
  - Rounds up to one share only when the sizing budget is at least 70% of one share.
  - `unsupported_order_shape` logs now include reference price, notional, KIS capital, estimated budget, estimated quantity, and a clear message.
  - HTTP request failures now include the KIS response body excerpt for diagnosis.

### Ops note
- Set Oracle `.env` `KIS_CAPITAL_KRW=10000000` if the KIS mock account is intended to mirror the 10M paper/effective capital.

## 0. Latest Codex Notes - 2026-05-14 (session 39 - Korea opening-drive guardrail)

### Why this change was needed
- Korea market opened and the desk generated `attack_opening_drive` paper entries.
- The opening-drive plan could expand `candidate_symbols` into multiple simultaneous Korea orders.
- One opened basket member had `peak_pnl_pct=0.0` and hit a large immediate loss, which violates the user rule: do not catch falling knives; enter only after short pressure flips long.

### Changes
- `app/services/recommendation_engine.py`
  - Tightened opening-drive requirements:
    - `quality_score >= 0.62`, `avg_signal >= 0.62`, `top_candidate_score >= 0.66`, `top_signal >= 0.62`.
    - `top_burst` must be positive but not overextended (`0.35%..7.5%`).
    - `top_gap <= 8.0%`.
    - Requires `_stock_long_flip(..., 0.58)` and rejects `_stock_falling_knife()`.
  - Opening-drive now emits only the top ticker in `candidate_symbols`.
- `app/agents/execution_agent.py`
  - Korea `attack_opening_drive` is forced to a single top candidate even if a plan carries multiple symbols.
  - Korea plans whose focus is opening-drive are also forced to a single top candidate even if risk logic later downgrades action to `selective_probe`.
  - Candidate-specific focus/strategy attribution now labels opening-drive rows as `korea.attack_opening_drive`.
- `app/core/state_store.py`
  - Added explicit Korea opening-drive thresholds: target `+3.5%`, stop `-1.0%`, max hold about `2h`.
  - Strategy type derivation now recognizes `opening_drive`.
  - Disabled pyramid follow-on entries for opening-drive positions. The first leader trade may win, but late add-ons were giving back the profit.

### Current rule
- Korea opening drive is no longer a basket entry. It is a single-leader, long-flip-confirmed intraday trade.
- If the leader is already overextended or has not flipped from short pressure to long pressure, the bot must wait.
- Opening-drive profit should be protected by trailing/exit logic, not by adding another late pyramid position.

## 0. Latest Codex Notes - 2026-05-14 (session 37 - Peak-zero loss streak quarantine)

### Why this change was needed
- User reported a 5-loss streak after the no-falling-knife patch.
- Oracle VM closed-position analysis showed all 5 recent crypto losses had `peak_pnl_pct=0.0`.
- Root cause: cycle-level long-flip logic improved, but websocket hot path still allowed `crypto.ranging_momentum_leader` and `crypto.ema_bounce` entries from cached candidates.
- Live paper stats:
  - `crypto.ranging_momentum_leader`: 4 trades, 0 wins, 4 losses, 100% peak0, total raw PnL about -1.46%.
  - `crypto.ema_bounce`: 1 trade, 0 wins, 100% peak0.

### Changes
- `app/services/hot_path_guard.py`
  - Permanently quarantined `crypto.ranging_momentum_leader` and `crypto.ema_bounce` alongside `crypto.candidate_rotation`.
  - This blocks websocket/tick hot entries even if stale cached candidates still exist.
- `app/agents/execution_agent.py`
  - Same permanent quarantine applied at cycle execution level.
- `app/core/state_store.py`
  - Strategy health now disables crypto strategies much earlier:
    - `count >= 2`, `win_rate=0`, `peak0_pct >= 80`, negative raw PnL.
    - `count >= 3`, `stop_like_pct >= 80`, negative raw PnL.
  - This prevents waiting for 7-15 samples while a strategy is obviously failing.
- `app/services/recommendation_engine.py`
  - `ranging_momentum_leader` no longer participates in the RANGING strategy blend or fallback local leader path.
  - `ema_bounce` cycle path is disabled pending redesign/backtest.

### Current rule
- Any crypto entry path that produces repeated `peak=0` losses is treated as invalid direction detection, not normal drawdown.
- Do not re-enable quarantined strategies without a redesign plus replay/backtest proof that entries occur after a real long flip.

## 0. Latest Codex Notes - 2026-05-14 (session 38 - Korea win-rate monetization)

### Why this change was needed
- Recent Oracle VM sample showed Korea desk had a strong hit rate but weak capital contribution:
  - 14 recent Korea closes, win rate about 78.6%, raw PnL about +6.8%.
  - Capital-weighted PnL was only about +0.52% because many orders were scaled down to ~0.04-0.07x.
- Root cause: crypto-driven global `risk_budget` was suppressing Korea sizing even when Korea desk was in a positive/high-win state.
- Also found old Korea rows tagged as `crypto.*` strategy IDs, which muddied strategy attribution.

### Changes
- `app/agents/execution_agent.py`
  - Korea desk now uses a separate effective risk floor when desk offense is positive:
    - `press`: at least 0.65 risk multiplier.
    - `balanced`: at least 0.50 risk multiplier.
  - Korea `press` entries get a small notional floor when no stop pressure exists:
    - `attack_opening_drive`: at least 0.16x.
    - other Korea entries: at least 0.12x.
  - Candidate-specific Korea orders now force `korea.*` strategy IDs if inherited plan metadata accidentally carries a non-Korea namespace.
- `app/core/state_store.py`
  - Korea trailing tiers tightened to protect more of a good move:
    - peak >= 4% now protects at least 2.5% and gives back only 1.5%.
    - peak >= 2% now protects at least 1.2%.
- `app/services/recommendation_engine.py`
  - Korea setup sizes increased modestly across breakout, opening drive, gap fill, close drive, pullback MA, and selective probe paths.

### Ops note
- Oracle VM had `ACTIVE_DESKS` unset, so default runtime was crypto-only. Set `.env` to `ACTIVE_DESKS=crypto,korea` before restarting services when deploying this session.

## 0. Latest Codex Notes - 2026-05-13 (session 36 - No falling knife core rule)

### User-defined core rule
- Buy only when short pressure is visible but starts flipping long.
- Do not buy simply because price is low, oversold, gap-down, or near support.
- After entry, ride the long move and exit immediately on short-transition signals or TP/trailing protection.
- Applies to both crypto and stocks.

### Change
- `app/services/recommendation_engine.py`
  - Added crypto transition state:
    - `short_pressure_visible`
    - `long_flip_confirmed`
    - `falling_knife_risk`
  - Crypto `RANGING` blend now requires `long_flip_confirmed` and rejects `falling_knife_risk`.
  - `range_scalp` now needs actual turn confirmation; mean reversion alone is not enough.
  - `range_breakout`, `high_tight_flag`, and `ranging_momentum_leader` require non-negative micro direction plus long-flip confirmation.
  - `dip_bounce` is blocked until BTC dip confirms a long flip.
  - Non-RANGING crypto entries are blocked if short pressure remains active without long-flip confirmation.
  - Added common Korea stock guards:
    - `_stock_long_flip()`
    - `_stock_falling_knife()`
  - Korea `gap_fill`, `open_reversal`, `opening_drive`, `selective_probe`, `mid-session follow-through`, and `pullback_ma` now require bullish resumption / reversal confirmation rather than buying weakness alone.

## 0. Latest Codex Notes - 2026-05-13 (session 35 - RANGING momentum hot-path loss fix)

### Why this change was needed
- Immediately after enabling `ranging_momentum_leader`, two hot-path entries closed as `rapid_tick_failed_start`.
- Both trades had `peak_pnl_pct=0.0`, meaning price never moved in favor after entry.
- Diagnosis: the cycle strategy could correctly identify individual leaders, but the websocket hot path was still too willing to chase a short tick spike in a broad `RANGING` tape.

### Change
- `app/services/hot_path_guard.py`
  - Reduced `ranging_momentum_leader` hot-path size from `0.055-0.07x` to `0.025-0.04x` until live edge improves.
  - Tightened candidate eligibility:
    - combined >= `0.66`
    - signal >= `0.78`
    - trend >= `0.60`
    - orderbook bid/ask >= `0.65`
    - micro 3m move must already be non-negative
    - trend extension <= `4.0%`
    - live stream must be fresh, non-reversal, with ticks15 >= `4`, stream >= `0.70`, buy ratio >= `62%`
    - move15 must be between `0.18%` and `0.45%` to block late spike chasing.
  - Added an explicit `ranging_momentum_leader` ignition path:
    - ticks15 >= `5`, stream >= `0.74`, move5 >= `0.08%`, move15 `0.20-0.45%`, move60 >= `0.04%`, buy ratio >= `64%`.
  - Added richer focus text (`move5`, `move15`, `move60`, buy ratio, ticks15) so future losses can be diagnosed directly from the dashboard/trade log.

## 0. Latest Codex Notes - 2026-05-13 (session 34 - Strategy blend correction)

### Why this change was needed
- User correctly flagged that strategies were being evaluated sequentially, so an earlier path could block later strategies instead of complementing them.
- The first correction targets the highest-impact area: crypto `RANGING` regime.

### Change
- `app/services/recommendation_engine.py`
  - Added a `ranging_blend` candidate board at the top of the RANGING block.
  - Three families now compete together:
    - `range_scalp`: mean-reversion / Airborne / RSI / Keltner / MFI / other RANGING signals.
    - `range_breakout` / `high_tight_flag`: local continuation inside broad flat tape.
    - `ranging_momentum_leader`: individual coin momentum leader while broad market is RANGING.
  - Best candidate is selected by score and returned with notes showing competing candidates.
- This keeps defensive filters as safety rails, but stops one strategy family from suppressing another family’s edge.

## 0. Latest Codex Notes - 2026-05-13 (session 33 - Crypto opportunity + mobile stock visibility)

### Crypto opportunity recovery
- User feedback: crypto entries became too quiet after loss-control hardening.
- Added `crypto.ranging_momentum_leader` for cases where broad regime is `RANGING` but an individual coin is visibly leading.
- `app/services/recommendation_engine.py`
  - New RANGING path allows reduced-size entries when:
    - `signal_score >= 0.74`
    - `trend_follow_score >= 0.55`
    - recent/burst/change move is strong, or local breakout/high-tight flag is active
    - orderbook is not hostile (`bid/ask >= 0.35`)
    - no overheat, stream reversal, bearish RSI divergence, or bearish CHoCH.
  - Size is intentionally reduced (`0.22x` to `0.38x`) because the broad tape is still flat.
- `app/services/hot_path_guard.py`
  - Added hot-path eligibility and tick sizing for `ranging_momentum_leader`.
- `app/core/state_store.py`
  - Added thresholds: target `+2.00%`, stop `-0.70%`, max `90` cycles.
  - Added strategy attribution label.
- `app/agents/execution_agent.py`
  - Added strategy id inference for `crypto.ranging_momentum_leader`.

### Mobile stock visibility
- `app/main.py`
  - Dashboard desk cards no longer hide Korea/US when inactive; they show as `준비중` with a dashed disabled style.
  - Scanner Korea section no longer disappears when there are zero candidates.
  - Empty Korea scanner state now says candidates will appear there when a market-hours signal fires.

## 0. Latest Codex Notes - 2026-05-13 (session 32 - Strategy stats UI + Emma/Neo crypto paths)

### Operations checks
- KIS readiness:
  - KIS token and balance checks now pass after the user replaced the App Secret and account settings.
  - Recent `live_order_log` rows still show only pre-fix Korea paper fallback orders; no new post-fix `applied_mode=kis_live` order has fired yet.
  - Next validation target: wait for the next Korea signal and confirm `live_order_log.applied_mode=kis_live`, `broker_live=true`.
- ML model files on Oracle VM:
  - `models/lgbm_model.pkl`
  - `models/persistence_cnn.pt`
  - user cron includes `0 12 * * 0 /home/ubuntu/retrain_models.sh`.

### Dashboard/API fix
- `app/core/state_store.py`
  - `get_strategy_stats()` now returns both old and UI-friendly fields:
    - `n_trades` and `total_trades`
    - `avg_pnl` and `avg_pnl_pct`
    - `total_pnl` and `total_pnl_pct`
    - `losses`
- `app/main.py`
  - Added a compatible `renderStrategyStatsCompat()` override so `/api/strategy-stats` displays correctly on the dashboard.
  - Table now shows strategy, desk, trade count, win rate, average PnL, total PnL, and W/L.

### New crypto strategy paths
- `app/services/recommendation_engine.py`
  - Added `crypto.emma_scalp`.
    - Uses Keltner lower-band context, Supertrend long, and MACD bullish/histogram reversal.
    - Requires at least 2/3 confirmations plus orderbook and micro/stream support.
    - Intended as a short-term scalping confluence, not a broad swing entry.
  - Added `crypto.neo_micro_scalp`.
    - Public Moritz Neo rule details were not verifiable from reliable sources.
    - Implemented only the usable generic principle: small-size, fast compounding entries when stream + orderbook + chart agree.
- `app/core/state_store.py`
  - Added tight crypto thresholds:
    - `emma_scalp`: target `+1.40%`, stop `-0.45%`, max `60` cycles.
    - `neo_micro_scalp`: target `+0.90%`, stop `-0.35%`, max `45` cycles.
  - Added strategy attribution labels for `emma_scalp` and `neo_micro_scalp`.

## 0. Latest Codex Notes - 2026-05-13 (session 31 - KIS mock visibility/routing fix)

### Why KIS mock app did not show Korea trades
- Oracle `live_order_log` showed recent Korea orders as `requested_mode=upbit_live`, `applied_mode=paper`, `broker_live=false`.
- Recent fallback reasons included `unsupported_order_shape`, `request_exception`, and older `unsupported_desk_for_upbit`.
- Therefore the Korea rows visible on the dashboard were mostly internal paper positions, not actual KIS mock account orders.

### Fixes applied
- `app/services/broker_router.py`
  - Reworked routing to be desk-aware under `EXECUTION_MODE=upbit_live`.
  - Crypto orders route to Upbit when Upbit is ready.
  - Korea orders route to KIS when `KIS_ALLOW_LIVE=true`, KIS credentials are present, and `LIVE_CAPITAL_KRW > 0`.
  - Route details now carry per-order `applied_mode` and `broker_live` so mixed broker cycles are recorded correctly.
- `app/orchestrator.py`
  - Live duplicate guard now treats Korea as a live desk when KIS is configured even if global mode is `upbit_live`.
  - Live order refresh now dispatches by desk/applied broker, so KIS order IDs are queried through KIS, not Upbit.
  - Live position sync now uses `route_summary.live_desks` instead of assuming one global broker per cycle.
- `app/core/state_store.py`
  - `save_live_order_attempts()` records per-order `applied_mode` / `broker_live`.
  - `refresh_live_order_statuses()` passes `applied_mode` to broker-specific refresh dispatch.
- `app/agents/execution_agent.py`
  - Korea `reference_price` lookup now includes `close_drive_candidates`, `gap_fill_candidates`, and `pullback_ma_candidates`.
  - This prevents supported KIS buy actions from falling back because quantity calculation received price `0`.
- `app/services/kis_broker.py`
  - Added a shared file token cache at `app/data/kis_access_token_cache.json`.
  - KIS VTS token issuance can return `403` when loop/dashboard/diagnostics request tokens repeatedly; all processes now reuse the cached token until expiry.

### Verification
- Local compile passed for:
  - `app/services/kis_broker.py`
  - `app/services/broker_router.py`
  - `app/orchestrator.py`
  - `app/core/state_store.py`
  - `app/agents/execution_agent.py`

## 0. Latest Codex Notes - 2026-05-13 (session 30 - Korea loss-control hotfix)

### Addendum - Korea per-symbol order labels
- Fixed a dashboard/telegram clarity bug in `app/agents/execution_agent.py`.
- Korea multi-candidate plans used to expand several symbols while preserving the first candidate's `focus` text.
- Result: actual orders for symbols such as `064760` / `131290` could display the same leader name, e.g. `티에스이 selective probe...`.
- New behavior:
  - each Korea order looks up its own latest market snapshot row,
  - rewrites `focus` with the actual candidate name and ticker,
  - carries candidate-specific signal/candidate/gap/volume details into notes,
  - preserves strategy namespace such as `korea.selective_probe`, `korea.breakout`, `korea.gap_fill`, etc.

### Oracle VM deployment status
- Local commits:
  - `c8527cb fix: ignore flat Korea stop exits in pressure gate`
  - `5680fd5 fix: tighten Korea selective probe risk`
  - `702562b fix: define state store logger`
- Oracle VM applied commits:
  - `98cb579 fix: ignore flat Korea stop exits in pressure gate`
  - `f687cdb fix: tighten Korea selective probe risk`
  - `e965b53 fix: define state store logger`
- Services verified active after deployment:
  - `trading-loop`: active
  - `trading-dashboard`: active

### Why loss appeared after Korea entries
- `491000` closed at raw `-1.62%` from a Korea `selective_probe`.
- Root cause: Korea exploratory entries were falling through to the broad default threshold `target +25% / stop -1.5% / max 2700 cycles`.
- That is too wide for exploratory Korea entries. A selective probe should test the idea with limited downside, not behave like a full swing position.
- The other two entries validated that the scanner was not fully broken:
  - `064760` closed `+4.25%` at target.
  - `131290` remained open around `+1.14%` at the last check.

### Fixes applied
- `app/core/state_store.py`
  - Added Korea `selective_probe` thresholds: target `+3.0%`, stop `-0.8%`, max `360` cycles.
  - Tightened Korea non-attack fast-fail to `12` minutes / `8` cycles.
  - Added `_log = logging.getLogger(__name__)` because Korea pyramiding used `_log.info()` and caused `name '_log' is not defined`.
- `app/agents/execution_agent.py`
  - `_is_stop_like_exit()` now ignores flat/slightly positive exits (`pnl_pct >= -0.05`) so Korea desk stop-pressure is not poisoned by incorrectly tagged flat `stop_hit` rows.

### Follow-up risks
- Korea dashboard labels currently reuse the top candidate name in focus text for multiple symbols. Example: `064760` and `131290` can show `티에스이 selective probe...`.
- This is a reporting/focus-labeling bug, not necessarily an execution-symbol bug. Next cleanup should make per-symbol Korea order messages use the actual candidate name.
- Local `git push` is still blocked by HTTPS credential/token auth. Oracle VM has the patches applied, but GitHub remote may not have these local commits until credentials or connector push is fixed.

Last updated: 2026-05-13 (session 29 — Korea stop-pressure false positive fix)
Maintained for: Claude / Codex continuation

## 0. Latest Codex Notes - 2026-05-13 (session 29 — Korea stop-pressure false positive fix)

### 커밋 TBD — Oracle VM 배포 대상

**[한국장 후보는 뜨지만 거래가 없는 문제 진단/수정]**

실시간 Oracle VM 확인 결과, 한국장 정규장 중 티에스이 등 `attack_opening_drive`/`selective_probe` 후보가 계속 생성되고 있었음.
품질점수 `0.85`, PM debate `bull=0.88 / bear=0.16`으로 신호는 충분했지만 모든 주문이 `status=idle`.

원인:
- 최근 한국 paper closed rows 중 `closed_reason=stop_hit`이지만 실제 `pnl_pct=+0.01%`인 본전/소폭 플러스 청산 3건 존재.
- `ExecutionAgent._is_stop_like_exit()`가 손익을 보지 않고 `closed_reason` 이름만으로 stop-like로 분류.
- 그 결과 한국 desk가 `stop pressure high`로 오인되어 `new entries paused`가 반복됨.

수정:
- `app/agents/execution_agent.py`
- `_is_stop_like_exit()`에서 `pnl_pct >= -0.05%`인 flat/positive exits는 stop pressure에서 제외.
- 실제 음수 손절만 desk/symbol stop-pressure와 후보 패널티에 반영.

검증:
- `python -m compileall app/agents/execution_agent.py`
- Unit smoke:
  - `stop_hit +0.01%` → stop-like `False`
  - `stop_hit -0.01%` → stop-like `False`
  - `stop_hit -0.10%` → stop-like `True`

## 0-prev. Latest Claude Notes - 2026-05-12 (session 28 — 6대 전략 확장)

### 커밋 bb9856d — Oracle VM 배포 완료

**[6대 전략 전체 구현 + 간섭 방지 설계]**

1. **기관 수급 필터** (`app/services/korea_supply_demand.py` NEW)
   - Naver sise_invest 스크래핑 → 기관 레이더 종목집합 (1h TTL)
   - Path B 브레이크아웃 점수 10% + sentiment enrichment 15% 수급 가중치

2. **종가 추격 전략** (Path D, 14:50~15:10 KST = 05:50~06:10 UTC)
   - 당일 gap>=2% 유지 + 기관 레이더 + signal>=0.55 + sentiment>=0.52
   - target +3.0% / stop -1.5% / max 30h (오버나이트)
   - close_drive 슬롯 최대 1개 (중복 진입 방지)

3. **호가잔량 연동** (H0STASP0 TR, `kis_stream_cache.py`)
   - KIS 호가 TR 동시 구독 → get_orderbook_imbalance()
   - 오픈리버설 점수에 매수잔량 우세 시 +10점

4. **테마 감지** (`korea_sentiment.py`)
   - 8개 핫 테마(AI/반도체/2차전지/바이오 등) 감지
   - 종목 토론방/뉴스에서 테마 언급 시 0.0~0.15 부스트

5. **김치프리미엄** (`app/services/kimchi_premium.py` NEW)
   - Dunamu 환율 + Binance 가격 비교 (5m TTL)
   - 역프리미엄(-2%) 시 crypto ignition threshold 0.56→0.62

6. **피라미딩** (`state_store.py`)
   - Korea 포지션 peak_pnl>=3.0% + current>=2.0% → +0.20x 추가 진입
   - open_reversal / close_drive / pyramid 대상 제외
   - is_pyramided 컬럼 추가 (schema migration 자동)

**간섭 방지:**
- Korea max 슬롯 2→3 (execution_agent)
- per-strategy 슬롯: open_reversal max1, close_drive max1
- 피라미드는 별도 카운팅 (max1)
- 시간창 자연 분리: 리버설(9:00~9:40) / 브레이크아웃(장중) / 종가(14:50~15:10)

## 0-prev. Latest Claude Notes - 2026-05-12 (session 27 — KIS 틱 스트림 오픈 리버설 전략)

### 커밋 8f17a54 — Oracle VM 배포 완료

**[KIS WebSocket H0STCNT0 틱 스트림 + 오픈 리버설 전략]**

새 파일 `app/services/kis_stream_cache.py`:
- KIS WebSocket (H0STCNT0 TR) 실시간 체결 틱 수신
- 종목당 300틱 ring buffer, background daemon thread
- `get_opening_reversal_signal(ticker)`: cascade→exhaustion→reversal 3단계 감지
  - cascade: 30틱 매도비율 ≥65% AND 시가 대비 -0.8%↓
  - exhaustion: cascade AND 10틱 매도비율 <52% AND 틱볼륨 수축
  - reversal: 5틱 매수 우세 AND 저점 위
  - score 0~100 (cascade +30+최대20, exhaustion +25, reversal +25, 매도비율개선 +10)
- `subscribe_tickers()`, `get_stream_status()` 공개 API

`app/agents/korea_stock_desk_agent.py` Path C 추가:
- 09:00~09:40 KST (00:00~00:40 UTC) 구간에만 활성화
- gap_candidates[:10] + enriched_candidates[:10] 틱 구독
- score ≥ 55 AND cascade=True인 종목 → reversal_candidates (gap_candidates 앞에 삽입)

`app/services/recommendation_engine.py` open_reversal 핸들러:
- top candidate에 `open_reversal=True` 있으면 → `attack_opening_drive` 0.40x
- focus 문자열에 "open_reversal:" 포함 → _position_thresholds 트리거

`app/core/state_store.py` 전용 임계값:
- focus에 "open_reversal" 포함 시: target +3.0% / stop -0.8% / max 360 cycles (2h)

**이전 세션 주요 변경 (session 26):**
- Korea stop -2.5%→-1.5%, trail trigger +4%→+1.5%
- Korea 최대 동시 포지션 3→2
- `_infer_strategy_id` desk 파라미터 추가 (korea.*, us.*)
- crypto ignition gate 완화 (high quality signal bypass)
- 전체 유니버스 스캔 (KOSPI+KOSDAQ 상위거래량 120종목)
- Naver 종목토론방+뉴스 sentiment enrichment

## 0-prev. Latest Claude Notes - 2026-05-12 (session 26 — KIS 연동 + 스캐너 주식 추가 + 종목명 표시)

### 커밋 b09442c — Oracle VM 배포 완료

**[스캐너 한국 주식 섹션 추가]**
- `/scanner-data` API: `korea_candidates` 필드 추가 (desk_views.korea_stock_desk 기반, 최대 15개)
- 스캐너 페이지: 🇰🇷 한국 주식 스캐너 테이블 섹션 추가 (korea_candidates가 있을 때만 표시)
  - 컬럼: 순위, 종목, 현재가, Signal 게이지, RSI, Vol배율, 브레이크아웃, Bias, 점수
- `KOREA_NAMES` 매핑 딕셔너리 (20종목) + `koreaSymName()` 헬퍼 추가 (대시보드/스캐너 양쪽)
- `renderPositions()`: korea 데스크 포지션 "회사명(종목코드)" 형식으로 표시
- `renderTrades()`: korea 데스크 청산 내역 "회사명(종목코드)" 형식으로 표시

**[이번 세션에서 완료된 KIS 연동 작업]** (이전 커밋들)
- `broker_router.py`: upbit_live 모드에서 korea 데스크 → KIS 브로커로 분기
- `config.py`: `KIS_CAPITAL_KRW` 설정 추가 (KIS 전용 자본금, 기본값=LIVE_CAPITAL_KRW)
- `orchestrator.py`: 마켓 snap과 KoreaStockDesk의 gap_candidates 병합
- `execution_agent.py`: `_reference_price()` 개선 — KOSPI 감시종목도 조회 가능
- VM `.env`: `KIS_ALLOW_LIVE=true`, `KIS_MOCK=true`, `KIS_CAPITAL_KRW=5000000`
- stream 신선도 6s→30s 완화 (`crypto_desk_agent.py`)

**현황 (2026-05-12 09:56 KST)**:
- 스캐너: 코인 18개 + 주식 5개 (펩트론, 휴림로봇, 에이프릴바이오, 대주전자재료, 브이엠)
- KIS 모의투자(mock) 모드로 주식 주문 라우팅 활성화
- 모의투자 → 실전 전환: VM .env에서 `KIS_MOCK=false`로 변경 후 uvicorn 재시작

**다음 우선순위**:
1. KIS 모의투자 주문 실제 체결 확인 — cycle_journal에서 `applied_mode: kis_live` 확인
2. KIS 실전 전환 (`KIS_MOCK=false`) 결정
3. ML 모델 신호 반영 확인 (models/ 디렉토리 pkl/pt 존재 여부)
4. Binance 선물 연결 — 미래 계획

---

## 0. Latest Claude Notes - 2026-05-12 (session 25 — best3 전략 이식 + stream_score=0 버그 수정)

### 커밋 3222811, f32bf74, 50d3890 — Oracle VM 배포 완료

**[best3_strategies_for_crypto.md 전면 이식]**
- `app/services/ml_strategy.py` (신규): LightGBM 이진 분류, 10개 피처, 15분 캐시
- `app/services/narrative_momentum.py` (신규): Fear&Greed EMA 모멘텀(60%) + CoinGecko sentiment(40%), 4시간 캐시
- `app/services/persistence_cnn_model.py` (신규): PyTorch Conv1d(5→16→32) + AdaptiveAvgPool1d, WIN=20 HORIZON=3, 4시간 캐시
- `app/services/ensemble_signal.py` (신규): softmax 정규화 가중 평균 (ml=0.20, narrative=0.18, persistence=0.14), BUY≥0.58/SELL≤0.40
- `scripts/train_ml_strategy.py` (신규): Upbit 30종 일봉, val_acc=60.7%
- `scripts/train_persistence_cnn.py` (신규): Upbit 30종 일봉, accuracy=62.6%
- `requirements.txt`: numpy, scikit-learn, lightgbm, torch 추가
- hot_path_guard.py: 앙상블 신호를 5개 전략(ema_cross/vwap_reclaim/rsi_flip/macd_cross/triple_bull)에 적용
  - `and not _ensemble_sell`: SELL 신호 시 진입 차단
  - `and combined >= (X if _ensemble_buy else Y)`: BUY 신호 시 임계값 약 0.02 완화

**[Oracle VM 자동 재학습 cron 설정]**
- `/home/ubuntu/retrain_models.sh`: git pull + train_ml_strategy.py + train_persistence_cnn.py + systemctl restart
- cron: `0 12 * * 0` (일요일 UTC 12:00 = KST 21:00)

**[stream_score=0.000 버그 수정 — 거래 없던 원인]**
- 원인: CryptoDeskAgent가 lead market(BTC)만 stream 계산 → 개별 심볼 all_candidates에 stream_score=0 저장
  → `_ranging_base_ok` 조건 `stream_score >= 0.48` 항상 False → 모든 RANGING 진입 차단
- 수정: 캐시된 stream_score < 0.01이면 `summarize_stream_momentum(symbol, max_age_seconds=60.0)` 라이브 조회
- 수정된 4곳:
  1. RANGING 공통 `_ranging_base_ok` 블록
  2. obvious_trend Path B (`_ot_stream_score`)
  3. RANGING Path B `_elig` 블록 (`_elig_stream_score`)
  4. vol_breakout 블록 (`_vb_stream_score`) ← 이번 세션 완료

**다음 우선순위**:
1. 코인 진입 재개 여부 확인 — 다음 사이클 후 diag_candidate.py 또는 VM 로그 확인
2. ML 모델 신호가 앙상블에 반영되는지 확인 (models/ 디렉토리에 pkl/pt 존재해야 함)
3. Binance 선물 연결 — 미래 계획

---

## 0. Latest Claude Notes - 2026-05-11 (session 24 — 한국주식 투트랙 + 코인 급락 대응)

### 커밋 172c003, 15913a9 (hot_path_guard), 222c8ad, ada4434 (state_store) — Oracle VM 배포 완료

**[긴급수정] `_ranging_base_ok` UnboundLocalError — 모든 사이클 크래시 원인**
- 원인: 이전 세션에서 변수가 `return False` 아래 데드코드로 이동됨
- 수정: `_ranging_stream_score`, `rs_vol_ratio`, `rs_vol_24h`, `_ranging_base_ok` 정의를
  ema_bounce 블록 앞으로 이동
- 커밋 172c003

**[진입 조건 완화] RSI 임계값 조정**
- ema_bounce: RSI 40 → 50
- multi_ranging: RSI 45 → 55
- 커밋 15913a9

**[한국 주식 desk 활성화]**
- Oracle VM `.env`: `ACTIVE_DESKS=crypto,korea`
- Korea position thresholds: target=25% (사실상 미도달 — 트레일링 전담), stop=-2.5%, max_cycles=2700
- `_korea_trail_rules` 추가:
  - peak ≥ 15%: giveback 3.5%, floor 10%
  - peak ≥ 8%: giveback 3.0%, floor 5%
  - peak ≥ 4%: giveback 3.5%, floor 2%
- Korea 전용 청산 블록 (`korea_trail` 이유) state_store에 추가
- 전략 근거: stock_backtest_v3.py (20일 고점 돌파 + vol 2.5x + RSI 55-78 + EMA20)
  - 셀트리온 승률 85.7%/Sharpe 26.6, 클래시스 80%/15.3, 현대차 80%/22.2
- 커밋 222c8ad, ada4434

**[코인 WebSocket 실시간 급락 대응 — 이미 구현완료 확인]**
- `hot_guard_crypto_tick` (hot_path_guard.py line 1864) 존재
- runtime.py line 178: `register_trade_callback` 등록 완료
- 0.45초 스로틀 per symbol (20초 폴링이 아님)
- 스탑/타겟/트레일 + 조기 실패 + 반전 신호 모두 실시간 체크
- 한국 주식은 KIS REST 폴링 구조 → WebSocket 불가, 현 20초 폴링 유지

**다음 우선순위**:
1. Korea stock desk 성과 모니터링 (내일 장 마감 후 diag_vm_trades.py 실행)
2. 코인 진입 재개 여부 확인 (stream_score 구조적 저하 여부)
3. Binance 선물 연결 — 미래 계획 (숏 포지션, 서버사이드 스탑 오더)

---

## 0. Latest Claude Notes - 2026-05-11 (session 23 — part 2 — Option A+B: RANGING 전면 차단 + 백테스트 전략 이식)

### 커밋 2c66a44, afe0fc8 — Oracle VM 배포 완료

**[Option A] RANGING 전략 전면 차단**
- obvious_trend Path A/B 이외 RANGING 전략 모두 비활성 (`return False` 조기 종료)
- 차단된 전략: range_scalp, B3-7 전략 전체 (higher_lows, trend_reversal_early, inside_bar_break 등)
- 재활성화 조건: 실전 누적 데이터에서 특정 전략 승률 30%+ 확인 후 주석 해제
- 코드는 보존됨 (삭제 아님)

**[Option B-1] daily_persistence.py — PersistenceCNN 룰 기반 일봉 필터**
- 신규 모듈: `app/services/daily_persistence.py`
- Upbit 일봉 20개로 t-통계량 기반 추세 지속성 점수 계산
  - score > 0.55: 상승 추세 지속 → TRENDING long 허용
  - score < 0.50: 하락 추세 → 차단
- 4시간 캐시, 백그라운드 사전 워밍
- hot_path_guard TRENDING 전략들에 통합:
  - obvious_trend Path B: >= 0.52
  - ema_cross, vwap_reclaim, macd_cross: >= 0.50
  - rsi_flip, triple_bull: >= 0.55 (더 엄격)
  - vol_breakout: >= 0.52

**[Option B-2] coin_backtest_v5 전략 실전 이식 — vol_breakout**
- 백테스트 검증: 60분봉 거래량급등+신고점돌파+RSI(55-78)+EMA
  - 승률~48%, 손익비~2.0, Sharpe~1.2, MDD<-20%
- hot_path_guard: `vol_breakout` 신규 진입 경로 추가 (TRENDING 섹션 최우선)
  - 조건: vol_surge_long + breakout_vol_confirm + RSI(55-78) + trend_entry_allowed
          + combined>=0.58 + ob>=1.06 + stream>=0.52 + daily_persistence>=0.52
- state_store: `_position_thresholds("vol_breakout")` → target=+4.0%, stop=-2.0%, cycles=1620
  - 기존 스캘핑과 달리 추세 지속 구조에서 수시간 보유 허용

**전체 변경 요약 (session 23)**:
```
RANGING:  obvious_trend(PathA/B)만 허용 — 나머지 전부 차단
TRENDING: ema_cross, vwap_reclaim, rsi_flip, macd_cross, triple_bull (persist 필터)
          + vol_breakout (coin_backtest_v5 전략, 최우선)
일봉:     daily_persistence.py로 일봉 하락 추세 코인 진입 차단
```

**다음 우선순위**:
1. vol_breakout 실제 발화 여부 확인 (vol_surge + breakout 동시 조건이 까다로움)
2. obvious_trend Path A 발화 모니터링
3. RANGING→TRENDING 전환 시 ema_cross/vwap_reclaim 발화 확인
4. 승률 목표: 30%+ (현재 6.2%)

---

## 0. Latest Claude Notes - 2026-05-11 (session 23 — 수익률 개선: 진입 품질 대폭 강화)

### 커밋 7bb89ce — Oracle VM 배포 완료

**배경: 32건 거래 분석 → 6.2% 승률, -11.73% 누적**

| 전략 | 건수 | 승률 | 원인 |
|------|------|------|------|
| trend_reversal_early | 7 | 0% | 하락 중 CHoCH 진입(칼날 잡기) |
| higher_lows | 7 | 0% | 구조신호 발화 시 가격 여전히 하락 중 |
| range_scalp | 4 | 0% | 과매도지만 실시간 매수세 없음 |
| inside_bar_break | 3 | 0% | 거짓 돌파 |
| tick_ignition | 6 | 17% | DKA +1.5% (유일한 큰 승리) |
| ema_bounce | 3 | 33% | WLD +0.68% |

**근본 원인:** 구조적 신호(차트 패턴) ≠ 실시간 모멘텀
→ PersistenceCNN 인사이트: 가격이 이미 반전 방향으로 움직이고 있을 때만 진입

**변경사항 (hot_path_guard.py):**
1. `_ranging_base_ok`: `stream_score >= 0.48` 추가 (전체 RANGING 전략에 적용)
2. `_ranging_b_check()`: `min_stream`, `min_micro3` 파라미터 신규 추가
3. `higher_lows`(RANGING): combined 0.55→0.57, RSI 58→52, stream≥0.52, micro3≥0
4. `trend_reversal_early`(RANGING): combined 0.54→0.57, RSI 58→52, stream≥0.54, micro3≥0
5. `inside_bar_break`(RANGING): combined 0.56→0.58, RSI 55→50, stream≥0.52
6. `range_scalp_hot_ok`: micro_move_3 범위 -1.0→0.0 (하락 중 진입 차단), stream≥0.52
7. `higher_lows`(TRENDING): stream≥0.52, micro3≥0 추가
8. `_b6_check()`: min_stream=0.48 파라미터 추가 (모든 B6 전략 틱 모멘텀 최소 기준)
9. `trend_reversal_early`(_b6): combined 0.55→0.57, ob 1.05→1.06, stream≥0.52
10. `ema_bounce`(RANGING): min_stream=0.52 추가

**AI-Trader / best3_strategies.md 분석:**
- AI-Trader(HKUDS): 소셜 트레이딩 플랫폼 — 직접 적용 불가, 개념만 참조
- PersistenceCNN: stream_score + micro_move_3로 "추세 지속 확률" 근사 적용 완료
- MLStrategy: combined_score가 이미 다중 피처 앙상블 역할 → 별도 LightGBM 불필요
- NarrativeMomentum: 단기 스캘핑에 부적합 (뉴스 기반 → 장기 포지션용)

**기대 효과:**
- trend_reversal_early/higher_lows: 7건 0승 → stream+micro3 필터로 거짓 신호 대폭 차단
- range_scalp: micro_move_3 ≥ 0 요구로 하락 중 진입 차단
- 전체 RANGING: stream_score < 0.48 코인 자동 차단

**다음 우선순위:**
1. 새 필터 적용 후 승률 모니터링 (목표: 승률 30%+, peak0 < 50%)
2. obvious_trend Path B 실제 발화 확인
3. tick_ignition 추가 개선 (현재 유일하게 양수 수익 가능 전략)
4. 충분한 데이터 축적 후 HANDOFF 재업데이트

---

## 0. Latest Claude Notes - 2026-05-08 (session 22 — obvious_trend Path B 캐시 진입 버그 수정)

### 커밋 4310884 — Oracle VM 배포 완료

**[버그 수정] obvious_trend Path B가 RANGING에서 hot-path 캐시에 진입 불가 문제 해결**

**근본 원인:**
- session 19에서 hot_path_guard `obvious_trend_ok`에 **Path B** 추가 (ignition 없이 초고점수 허용)
- 그러나 `_candidate_is_hot_entry_eligible`(RANGING)는 여전히 **stream_ignition 필수** 체크
- → Path B 조건 부합 코인이 hot-path 캐시에 **절대 진입 불가** → Path B 단 한 번도 발화하지 않음
- 동일 원인으로 `execution_agent._crypto_obvious_trend_entry_ok`도 Path A만 있어
  entry_profile="obvious_trend" 미설정 → cycle plan에서도 Path B 인식 불가

**수정 내용 (hot_path_guard.py + execution_agent.py):**

1. `_candidate_is_hot_entry_eligible` RANGING 블록:
   - `entry_profile=="obvious_trend"` 브랜치: Path A + **Path B** 동시 체크
   - 추가 블록: entry_profile 미설정 코인도 Path B 기준 부합 시 캐시 진입 허용
     (execution_agent가 아닌 경로로 온 경우 대비 safety net)

2. `_crypto_obvious_trend_entry_ok` (execution_agent):
   - Path A: stream_ignition + 표준 임계값 (기존 유지)
   - **Path B (신규)**: ts>=0.91 + chart>=0.86 + combined>=0.84 + stream_score>=0.64 + NOT ignition
   - `ok = _path_a or _path_b`
   - return message에 path_label, stream_score, ignition 포함

**Path B 기준 (양쪽 동일):**
```
trend_alignment == "trend_long"
AND trend_score >= 0.91 / 0.85(eligibility)
AND combined >= 0.84 / 0.75(eligibility)
AND stream_score >= 0.64
AND NOT stream_ignition
AND ext <= 5.0
```

**현재 시장 진단 (07:43 UTC 재시작 후):**
- 18개 candidates 중 Path B 근접: BLEND(comb=0.84, ts=0.94, ss=0.64, si=F, ext=4.4%)
- SAHARA(comb=0.78, ts=0.94, ss=0.64, si=T) → Path A 기준 near-miss (combined 0.78 = 정확히 임계)
- SOL: multi_ranging_combo=True but combined=0.35 (< 0.52) → B3-7 올바르게 차단
- CFG: RSI=29.9 but downtrend → 올바르게 차단

**다음 우선순위:**
- obvious_trend Path B 실제 발화 여부 모니터링 (특히 완만한 상승장에서)
- range_scalp 새 필터(session 19) 효과 누적 확인 (현재 cnt=7, 신규 거래 필요)
- B3-7 전략 발화 대기 (극단 과매도 시장 조건 필요)

---

## 0. Latest Claude Notes - 2026-05-08 (session 21 — "unknown" 버킷 window crowding 해결)

### 커밋 1개 — Oracle VM 배포 예정

**[개선] health window에서 "unknown" strategy_id 제외 (state_store.py)**

**문제:** `_strategy_performance_stats`가 최근 80건 closed positions를 평가하는데,
과거 태깅 이전(레거시) 포지션 60건이 `strategy_id="unknown"`으로 저장돼 있어
실제 전략 포지션이 20건만 평가 가능한 상태였음.
→ 새 전략들이 15건 임계값 도달에 오래 걸리고, health 판단이 편향됨.

**수정:** 두 곳 모두 수정:
1. `load_daily_summary` 내 `strategy_performance_stats` 계산 (~line 1591):
   ```python
   # 기존: 최근 80건 모두 포함 (unknown 60건 포함)
   [r for r in positions if r.status == "closed"][:80]
   # 수정: strategy_id 명시된 것만 포함 (unknown/빈값 제외)
   [r for r in positions if r.status == "closed"
    and (r.strategy_id or "").strip() not in ("", "unknown")][:80]
   ```
2. `load_strategy_performance_stats` 진단 함수 (~line 2065):
   - DB 쿼리에서 `strategy_id NOT IN (NULL, "", "unknown")` 필터 추가

**효과:**
- 이전: 80건 슬롯 중 60건이 "unknown" → 전략 20건만 평가
- 이후: 태깅된 전략만 최대 80건 → 모든 슬롯이 실제 전략 데이터
- candidate_rotation(8건), range_scalp(7건), obvious_trend(3건) 등 현재 태깅 데이터 전체 활용
- 새 전략들이 데이터 쌓이면서 health 임계값(15건/10건/7건)에 더 빨리 도달

**주의:** "unknown" 자체는 건강도 평가에서 제외되지만 DB에는 남아있음.
`load_strategy_performance_stats(window=300)` 진단 함수도 동일하게 "unknown" 제외.

---

## 0. Latest Claude Notes - 2026-05-08 (session 20 — candidate_rotation 완전 영구 차단 + ALL-regime cycle 블록)

### 커밋 2개 (7968633) — Oracle VM 배포 완료

**[개선 1] candidate_rotation 영구 차단 (hot_path_guard.py + execution_agent.py)**
- `_PERMANENTLY_DISABLED_STRATEGIES = frozenset({"crypto.candidate_rotation"})` — hot_path_guard.py (~line 79)
- `_PERMANENTLY_DISABLED = frozenset({"crypto.candidate_rotation"})` — execution_agent.py (클래스 속성)
- health window가 aging으로 초기화되더라도 영구 차단 유지
- `_disabled_strategy_ids()` = health_disabled ∪ PERMANENTLY_DISABLED

**[개선 2] cycle-level entry ALL-regime 차단 (execution_agent.py ~line 1088)**
- 기존: RANGING regime에서만 candidate_symbols cycle-path 차단
- 수정: 모든 regime에서 candidate_symbols cycle-path 차단
- 배경: 2026-05-07 TRENDING 순간에 candidate_rotation 8건 발화
  → session-15 RANGING-only 차단의 허점
- 효과: regime=TRENDING/RANGING/BREAKOUT 모두 동일하게 watchlist_only

**[확인] Oracle VM 배포 후 상태**
- candidate_rotation: cnt=8/win=0%/peak0=100% → health=disabled_candidate ✅
- shadow_signals: 오늘 없음 ✅
- 최근 거래: 00:12 KRW-G breakout_vol_confirm (-0.63%) 1건 후 없음

---

## 0. Latest Claude Notes - 2026-05-08 (session 19 — range_scalp 품질 강화 + obvious_trend 대안 경로)

### 커밋 2개 (489adee, 86610ba) — Oracle VM 배포 완료

**[개선 1] range_scalp 품질 강화 (hot_path_guard.py)**
1. `trend_alignment != "downtrend"` 추가
   - EMA stack bearish 코인의 RSI과매도 = 낙하 중 반등이 아님
   - SC(-0.99%), STORJ(-0.80%), ANKR(-0.59%) 하락추세 진입 차단
2. `combined >= 0.48` 최소 복합점수 요구
   - ranging_signal 단독 불충분; combined로 하락추세 코인 자동 필터
3. `vol_ratio 0.8 → 1.5` 유동성 기준 강화
   - ANKR/STORJ/B3가 vol_ratio 0.8로 통과하던 구멍 차단
   - 이제: 150% 이상 활성화 OR 24h거래량 50억 KRW+ 이어야 통과

**[개선 2] obvious_trend 대안 경로 B 추가 (hot_path_guard.py)**
- 경로 A (기존): stream_ignition + trend>=0.88 + combined>=0.78
- 경로 B (신규): trend>=0.91 + chart>=0.86 + combined>=0.84 + stream_score>=0.64 + NOT ignition
- 배경: 완만한 추세 상승장에서 combined=0.75-0.97 고점수 코인 놓치는 문제 해결
- 보수적 설계: 경로 B는 ignition=False일 때만 → 과발화 방지

**[진단] range_scalp 실패 원인 규명**
- 5실패: B3(-1.15%), STORJ(-0.80%), SC(-0.99%), ANKR(-0.59%), FIL(-0.58%)
- 2성공: FIL(+0.22%), FIL(+0.03%)
- 패턴: 소형/저유동성 코인 + 하락추세 코인 → 개선 필터 적용

**[진단] candidate_rotation 발화 원인**
- 8건 ALL 2026-05-07: session-15 이후 TRENDING regime 순간에 발화
- cycle-level block은 RANGING에만 적용 → TRENDING 시 일부 통과
- 현재: health threshold (cnt>=7, peak0=100%) → disabled_candidate → 차단

## 0. Latest Claude Notes - 2026-05-08 (session 18 — auto-disable 3단계 + 돌파형 ignition 분리)

### 커밋 3개 (3dced3c, 3969af0, ab5ba7a) — Oracle VM 배포 완료

**[개선 1] auto-disable 3단계 임계값 체계 구성 (state_store.py)**

```
1. cnt>=7  AND peak0=100%  → 즉시 disabled_candidate (신규)
2. cnt>=10 AND peak0>=90%  → 조기 disabled_candidate (신규)
3. cnt>=15 AND (win<20% OR peak0>=75% OR capital<-2%) → 표준 disabled
```

- 배경: `candidate_rotation` 8건/0%win/100%peak0이 15건 임계값 미달로 계속 진입
- 효과: threshold#1 적용 → candidate_rotation 즉시 disabled_candidate
- 동작: execution_agent `_strategy_disabled` + hot_path_guard `_disabled_strategy_ids` 모두 적용

**[개선 2] 돌파형 전략 ignition 분리 (hot_path_guard.py)**

- `breakout_vol_confirm`, `range_breakout`, `support_reclaim`, `macd_hist_rev` → 전용 ignition 블록
- 기존 B3-7 반전형 ignition(move_60 >= -0.25) 에서 분리
- 새 조건: `move_60 >= 0.02` (60초 실제 상승 중), `stream_score >= 0.62`, `buy_ratio >= 0.58`
- 근거: KRW-G breakout_vol_confirm 00:12 실패 → move_60 음수 허용이 원인

**[진단] 2026-05-08 현재 상황**
- regime=RANGING, stance=BALANCED, risk_budget=0.32
- obvious_trend NOT disabled (3건/67%win/33%peak0) → 정상 작동
- obvious_trend RANGING 모드: stream_ignition=True 개별 코인 급등 시 발화
- candidate_rotation 완전 차단 (기존 cycle-path 블록 + 신규 health disable)
- 오늘(05-08) KRW-G breakout_vol_confirm 00:12 1건 실패 후 거래 없음 (시장 관망)

**[전략 건강도 현황 (80건 기준)]**
- unknown: 60건/7%win/68%peak0 → DISABLED
- candidate_rotation: 8건/0%win/100%peak0 → DISABLED (신규 threshold#1)
- range_scalp: 7건/29%win/71%peak0 → watch (개선 중)
- obvious_trend: 3건/67%win/33%peak0 → OK ✅
- range_breakout: 1건 → insufficient data
- breakout_vol_confirm: 1건 → insufficient data

## 0. Latest Claude Notes - 2026-05-08 (session 17 — Gemini 4개 개선 + ranging_b36 치명적 버그 수정)

### 커밋 cc4e076 — 4개 개선 + 1개 치명적 버그 수정

**[치명적 버그 수정] RANGING Batch 7 전략 position thresholds 오적용**
- 버그: `rsi_extreme_bounce`, `volume_climax_bounce`, `mfi_stoch_oversold`, `keltner_rsi_bounce`,
  `cci_bb_bounce`, `williams_vol_bounce`, `panic_reversal` 7개 전략이 `_open_hot_entry`에서
  "ranging_b36:" prefix 없이 focus 저장 → trend thresholds(target=10%, stop=-2%) 잘못 적용
- 수정: `_open_hot_entry` focus 분기에 Batch 7 + `liquidity_sweep` 추가
- 효과: 올바른 target=1.80%, stop=-0.40%, range_scalp trail rules 적용

**[Gemini 개선 1] Liquidity_Sweep_Reversal 전략 추가 (개미털기/스탑헌트)**
- 위치: Batch 7 ALL-regime 섹션 (momentum_high_vol 다음)
- 조건: `pin_bar_long + support_reclaim_long + RSI<=38 + combined>=0.52 + ob>=1.05`
- 원리: 세력이 스윙 저점 밑으로 밀어 스탑로스 터치 후 즉시 매집 → V자 반전
- 적용: ignition set(B3-7 공통), is_b6 rapid guard, ranging_b36 focus prefix
- strategy_id: `crypto.liquidity_sweep`

**[Gemini 개선 2] panic_reversal ask-wall thinning 강화**
- 경로 1: `_panic_base AND bid/ask>=1.12` → 낮은 combined(0.49) 허용
- 경로 2: `_panic_base` 단독 → 높은 combined(0.54) 요구
- 의미: 공황 매도세 소진(ask 측 얇아짐) 동시 확인 시 진입 완화

**[Gemini 개선 3] 3분 타임컷 (time_cut_3min)**
- 조건: `minutes_open >= 3.0 AND peak_pnl <= 0.05 AND pnl_pct <= -0.10 AND not range_scalp`
- 위치: `state_store.py rapid_guard_crypto_positions` + `hot_guard_crypto_tick`
- 배경: 74% peak=0 문제 대응 — 3분 후 모멘텀 없는 포지션 즉시 청산
- 메움: rapid_tick_failed_start(0.33min/-0.22%) ↔ rapid_no_lift(10min/-0.30%) 사이 공백

**전략 포트폴리오 현황 (총 58개)**
- liquidity_sweep 1개 추가 (57 → 58개)

### 배포 필요
- Oracle VM pull: `ssh ubuntu@134.185.118.144 "cd ~/trading-bot && git pull && sudo systemctl restart trading-loop"`

## 0. Latest Claude Notes - 2026-05-08 (session 16 — 전략 포트폴리오 확장 57개 + obvious_trend 버그 3개 수정)

### 퀀트 성과 진단 결과 (342건 기준)
- 총 342건, 승률 9.6%, 누적 손실 -129.13%, peak=0 74%
- **치명적 버그 발견**: obvious_trend pullback_long alignment = 78건 99% peak=0

### 커밋 88686e2 — 버그 수정 3개 + 신규 전략 12개

**[Bug Fix 1] obvious_trend pullback_long alignment 완전 제거**
- `hot_path_guard.py` + `execution_agent.py` 동시 수정
- `trend_alignment in {"trend_long", "pullback_long"}` → `trend_alignment == "trend_long"`
- combined 0.72→0.78, trend_score 0.85→0.88, ext 6.0→5.0
- 영향: obvious_trend 발화 빈도 감소, 승률 급상승 예상

**[Bug Fix 2] rapid_obvious_trend_fail 최소 보유시간**
- `state_store.py`: `minutes_open >= 0.25` → `minutes_open >= 1.5`
- 배경: 시장가 매수 슬리피지(~0.1%) 를 15초 만에 실패로 오판하던 문제
- 최대손실 -0.38% → -0.42% (슬리피지 흡수 여유)

**[신규 전략] RANGING Batch 7 — 복합 과매도 7개**
```
rsi_extreme_bounce   : RSI<30 + BB하단 (max_rsi=35)
volume_climax_bounce : 거래량 클라이맥스 단독 (max_rsi=45)
mfi_stoch_oversold   : MFI + Stoch 이중 (max_rsi=40)
keltner_rsi_bounce   : Keltner하단 + RSI (max_rsi=40)
cci_bb_bounce        : CCI + BB하단 (max_rsi=42)
williams_vol_bounce  : Williams%R + 클라이맥스 (max_rsi=42)
panic_reversal       : 3중 과매도 = 가장 강한 반전 신호 (max_rsi=38)
```

**[신규 전략] ALL-regime Batch 7 — 5개**
```
support_reclaim  : 지지선 재탈환 (B3-6 ignition)
macd_hist_rev    : MACD 히스토그램 반전 (B3-6 ignition)
kill_zone_ict    : ICT 킬존 + 불리시 OB (trend ignition)
adx_di_cross     : ADX>=25 + DI+>DI- (trend ignition)
momentum_high_vol: vol_surge + breakout 이중 확인 (trend ignition)
```

**전략 포트폴리오 현황 (총 57개)**
```
RANGING전용: range_scalp, range_breakout, high_tight_flag
             + B3-7 (multi_ranging, demand_zone, vwap_rsi_combo, hammer_at_support,
               rsi_bullish_div, higher_lows, trend_reversal_early, inside_bar_break,
               bb_squeeze_break, breakout_vol_confirm, ema_bounce,
               rsi_extreme_bounce, volume_climax_bounce, mfi_stoch_oversold,
               keltner_rsi_bounce, cci_bb_bounce, williams_vol_bounce, panic_reversal)
TRENDING전용: obvious_trend, trend_ignition, ema_cross, vwap_reclaim, rsi_flip,
              macd_cross, triple_candle_bull, supertrend, engulfing_bull, vol_surge,
              adx_trend, bb_sq_break, higher_lows, pin_bar, morning_star,
              inside_bar_break, rsi_keep, oi_momentum, demand_zone
ALL-regime:  pullback_continuation, pullback_long, choch_momentum, ict_level_long,
             vwap_rsi_combo(B6), breakout_vol_confirm(B6), hammer_at_support(B6),
             trend_reversal_early(B6), ema_bounce(B6), rsi_bullish_div(B6),
             multi_ranging(B6), momentum_bk_cont(B6),
             support_reclaim, macd_hist_rev, kill_zone_ict, adx_di_cross, momentum_high_vol
```

### 배포 현황 (2026-05-08 04:25 UTC)
- Oracle VM: git pull + trading-loop restart → active (PID 1894668)

### 다음 관찰 포인트
- obvious_trend: pullback_long 제거 후 승률 변화 (기존 67% → 목표 75%+)
- panic_reversal: 가장 강한 과매도 신호, 첫 발화 시 성과 확인
- volume_climax_bounce: 패닉 매도 소진 포착 여부
- kill_zone_ict: ICT 킬존(04:00-06:00 UTC, 12:00-13:30 UTC) 발화 시간대 확인

## 0. Latest Claude Notes - 2026-05-08 (session 15 — pullback_long 신규 + cycle-level 전 regime 차단)

### 커밋 46a0387 — pullback_long 전략 추가 + cycle-entry 전면 차단

**hot_path_guard.py — `_candidate_is_hot_entry_eligible`:**
- `pullback_long` 신규 전략 추가 (pullback_continuation 블록 다음에 위치)
  - `pullback_score >= 0.75` (pullback_continuation의 0.55보다 강한 조건)
  - `trend_alignment in {"pullback_long", "trend_long"}`
  - `trend_score >= 0.68`, `combined >= 0.65`, `orderbook_bid_ask >= 1.08`
  - `vol_contracted=True`, `signal_freshness >= 0.58`
  - strategy_id = `crypto.pullback_long`

**hot_path_guard.py — `_on_tick` ignition block:**
- `pullback_long` elif 추가 (range_scalp 블록 다음, RANGING Batch 3-6 블록 이전)
  ```python
  elif entry_profile == "pullback_long":
      ignition = (stream_ok and ticks_15 >= 2 and stream_score >= 0.60
                  and move_15 >= 0.15 and move_60 >= -0.20 and buy_ratio >= 0.55)
  ```

**hot_path_guard.py — `_candidate_is_hot_entry_eligible` RANGING exception (session 14 편집 포함):**
- RANGING에서 obvious_trend 강한 개별 돌파 허용 예외 추가
  - `trend_follow_score >= 0.85`, `combined_score >= 0.75`, `stream_ignition=True`, `trend_ext <= 5.0`

**execution_agent.py:**
- cycle-level multi-order 진입을 **RANGING에서 모든 regime으로** 전면 차단
  - 기존: `if self.regime == "RANGING": skip`
  - 변경: 모든 regime에서 `"cycle-entry blocked (hot-path only)"` → skipped
  - 이유: 사이클 계산→실행 지연(수초) 동안 모멘텀 소진 → tick ignition이 더 정확
  - range_impulse_armed 후보는 여전히 tracking (RANGING impulse 대기열 유지)

### 배포 현황 (2026-05-08 02:05 UTC)
- Oracle VM: git pull + trading-loop restart → active (PID 1879654)
- 서비스 정상 기동 확인

### 포지션 임계값 (pullback_long)
- `_position_thresholds`에 별도 블록 없음 → crypto 기본값 사용 (10.0%, -2.0%, 180cycles)
- trail: `_crypto_trail_rules` (trend 전략 표준)

### 다음 관찰 포인트
- pullback_long 신호 발생 여부 (candidate에 `pullback_detected=True`, `pullback_score>=0.75`, `trend_alignment="pullback_long"` 조건)
- cycle-level 차단 효과: cycle_journal에서 "cycle-entry blocked" 로그 확인
- RANGING 시장 조정 후 RANGING Batch 3-6 신호 발생 여부 (RSI 50 이하 필요)

## 0. Latest Claude Notes - 2026-05-08 (session 14 — cycle-level stream gate + range_scalp 낙도 방어)

### 현재 시장 상황 (01:46 UTC 기준)
- regime=RANGING, stance=BALANCED, RSI=72.5 → 시장 과매수
- ranging signals(0/14): 14개 RANGING 신호 모두 False → 진입 없음 (정상)
- 재시작(00:51 UTC) 이후 거래 1건뿐: breakout_vol_confirm -0.63%
- 시장이 과매도 구간으로 조정 받으면 RANGING Batch 3-6 신호 발생 예상

### 커밋 7c09360 — candidate_rotation live stream gate
**execution_agent.py:**
- `_crypto_candidate_entry_ok` 통과 후 `summarize_stream_momentum(candidate)` 실시간 재확인
- stream_fresh=True AND (reversal OR score<0.35 OR move15<-0.08%) → 진입 차단
- 사이클 계산 시점과 실제 진입 시점 사이 모멘텀 소진/반전 방지
- candidate_rotation 8건 0% 승률 개선 목적

### 커밋 6336abb — range_scalp 낙도(falling knife) 방어
**hot_path_guard.py:**
- `_open_hot_entry`에서 range_scalp 전용 stream 체크 추가
- stream_fresh=True AND (move15 < -0.12% OR reversal) → 진입 차단
- RSI<=42 과매도이더라도 15s 하락 중이면 낙도 패턴 → entry_opened=0
- rapid_range_scalp_stop avg=-0.822% 감소 목적

### 진단 결과
- obvious_trend: strategy_disabled=False (cnt=3, win=67%) → 건강
- shadow_signals 최근 30건 모두 2026-05-07 (구버전 데이터): strategy_disabled 이유
- 현재 서비스에서 shadow_signal 없음 = 신호 자체가 미발생 (시장 과매수 때문)
- "crypto desk loss pressure active, recovery mode keeps only throttled entries" → 진입 차단 아님, size throttle만

### 배포 현황 (2026-05-08 01:46 UTC)
- 779a457: RSI 필터, ema_bounce, ranging_b36 trail
- 7c09360: candidate_rotation live stream gate
- 6336abb: range_scalp falling knife 방어

### 다음 관찰 포인트
- 시장 RSI가 50 이하로 조정 시 RANGING Batch 3-6 신호 발생 여부
- range_scalp peak=0 비율: RSI<=42 + falling knife 방어 효과
- candidate_rotation: live stream gate 후 승률 개선 여부
- obvious_trend: move_60>=0.00 조건 이후 성과

## 0. Latest Claude Notes - 2026-05-08 (session 13 — 전략 RSI 필터 + ema_bounce + ranging_b36 관리)

### 커밋 779a457 — 전략 품질 개선 패키지

**hot_path_guard.py:**
- **range_scalp**: `rsi_value <= 42.0` 추가 → 진짜 과매도 구간만 진입 (71% peak=0 → 개선 기대)
- **_ranging_b_check max_rsi 파라미터화**: 전략 유형별 RSI 상한 차별화
  - 평균회귀 (multi_ranging/demand_zone/vwap_rsi): max_rsi=50
  - 망치형 (hammer_at_support): max_rsi=52
  - RSI다이버전스 (rsi_bullish_div): max_rsi=48 (다이버전스 = 더 과매도여야)
  - 구조개선 (higher_lows/trend_reversal): max_rsi=58
  - 압축돌파 (inside_bar/bb_squeeze): max_rsi=65
  - 거래량돌파 (breakout_vol_confirm): max_rsi=63
  - EMA반등 (ema_bounce): max_rsi=55
- **ema_bounce_long 전략 추가** (11번째 RANGING Batch 3-6): `ema_bounce_long` 신호 → "ema_bounce" 프로파일
- **obvious_trend ignition**: `move_60 >= -0.15` → `move_60 >= 0.00` (60s 하락 중 진입 차단)
- **ranging_b36 focus 마커**: entry_profile이 RANGING 11개 중 하나면 focus에 "ranging_b36:" 태그

**state_store.py:**
- **_position_thresholds**: ranging_b36 블록 추가 → target=1.80%, stop=-0.40%, max_cycles=90
- **is_range_scalp/is_range_scalp_rapid**: `"ranging_b36" in focus` 포함
  → RANGING Batch 3-6도 `_range_scalp_trail_rules` 적용 (공격적 trail/stop 관리)

### 설계 의도
- RANGING에서 평균회귀 전략(RSI≤50)과 구조개선/돌파 전략(RSI≤58-65) 명확 구분
- ranging_b36 포지션: range_scalp처럼 빠른 trail → 목표=1.80% (RANGING에서 현실적), 손절=-0.40%
- rapid_tick_failed_start도 ranging_b36에 적용 (range_scalp_rapid 분기)

### 배포 (2026-05-08 00:51 UTC)
- Oracle VM git pull + trading-loop.service restart 완료
- 서비스 정상 기동 확인 (PID 1870961)

## 0. Latest Claude Notes - 2026-05-08 (session 12 — RANGING 진입 파이프라인 완전 정비)

### 3차 발견: RANGING Batch 3-6 전략 ignition 조건 미설정 (커밋 c016c9c)
- `entry_profile` = "trend_reversal_early", "higher_lows" 등이 모두 `else` 브랜치
- `else` = trend_ignition: stream_score≥0.74, ticks_15≥4, move_15≥0.35 → RANGING에서 절대 불가
- AVAX(combined=0.75, trend_reversal_early=True, base_ok=True)가 진입 못 한 이유
- **수정**: 10개 RANGING 프로파일 전용 ignition 추가 (stream_score≥0.58, ticks_15≥2, move_15≥0.14)

### 4차 발견: range_scalp B3/ANKR 갭점프 차단 불충분 (커밋 2f57b88)
- B3: ob=1.085, vol_24h=168.7B → 유동성 필터 통과했으나 진입 직후 -1.15% 갭점프
- `orderbook_bid_ask >= 1.05` → `>= 1.10` 상향 → B3(ob=1.085) 차단
- ANKR도 얇은 오더북 코인 → 동일 필터로 차단

### 2차 발견: RANGING 사이클 레벨 진입 차단 (커밋 27c27aa)
- ExecutionAgent._multi_orders에 `self.regime == "RANGING"` 블록
- 재시작 이후 cycle_journal 100% watchlist_only 확인

### 5차 발견: RANGING Batch 3-6 _ranging_base_ok 유동성 필터 누락 (커밋 88c45be)
- KRW-G (vol_24h=1.6B, vol_ratio=285x 펌프) → breakout_vol_confirm 진입 → -0.630%, peak=0
- `_ranging_base_ok`에 유동성 체크 없어 소형/펌프 코인 통과
- **수정**: `(vol_24h>=20억 OR vol_ratio>=0.8) AND vol_ratio<=80` 추가

### 검증: RANGING ignition 작동 확인
- KRW-G breakout_vol_confirm 진입 (00:12 UTC May 8) → ignition 수정 작동 확인
- 다만 G는 소형 펌프 코인 → 유동성 필터로 이제 차단됨

### 전략 상태 (window=80 기준)
- `crypto.obvious_trend`: cnt=3, win=67%, avg=+0.10% → 정상 (ENABLED)
- `unknown`: cnt=60, win=7%, peak0=68% → DISABLED (구버전 cycle-level 잔재)
- obvious_trend가 어제 strategy_disabled로 shadow_signal 기록된 것은 이전 서비스 실행분

### 배포 현황 (2026-05-08 UTC)
- 27c27aa: RANGING 사이클 레벨 진입 완전 차단
- c016c9c: RANGING Batch 3-6 ignition 조건 추가 (stream_score≥0.58, ticks_15≥2, move_15≥0.14)
- 2f57b88: range_scalp ob 임계값 1.10 상향
- 88c45be: RANGING base_ok 유동성 필터 (vol_24h≥20억, vol_ratio≤80)

### 다음 관찰 포인트
- RANGING 전략 진입 시 entry_profile 분포 (higher_lows, inside_bar_break 등)
- range_scalp B3/ANKR 재진입 차단 여부 (ob 1.10 기준)
- obvious_trend: NEAR(score=0.95), TIA, JTO 등 고점수 후보 진입 시도 모니터링
- crypto.candidate_rotation: 8건 0% 수익 → TRENDING 전환 시 사이클 레벨 재개 후 개선 여부

## 0. Latest Claude Notes - 2026-05-08 (session 12 — RANGING 사이클 레벨 진입 완전 차단)

### 배경: candidate_rotation 8건 모두 peak=0.000% 손실
- ExecutionAgent 사이클 루프가 RANGING 레짐에서도 cycle-level 진입 실행
- obvious_trend는 trend_alignment 조건으로 자연 차단되나, candidate_rotation은 미차단
- 8건 모두 진입 직후 바로 rapid_tick_failed_start로 청산 (tick-level 확인 없이 진입)

### 수정 (커밋 27c27aa)

**execution_agent.py `_multi_orders()`**
- `for candidate in all_candidates` 루프 시작에 RANGING 블록 추가:
  ```python
  if self.regime == "RANGING":
      skipped_candidates.append(f"{candidate}: cycle-entry blocked in RANGING (hot-path only)")
      continue
  ```
- `elif plan.get("candidate_symbols")` 분기(candidate_meta 없는 경우)도 RANGING 차단 추가
- RANGING 시 모든 candidate → `eligible_candidates=[]` → `watchlist_only` 반환

### 검증 결과 (23:43 UTC 재시작 이후)
- cycle_journal: 재시작 직후부터 100% `watchlist_only` 출력
- 포커스: "RANGING — 추세추종 차단. 평균회귀 신호 대기"
- candidate_rotation 신규 진입 **0건** (완전 차단 확인)
- hot_path_guard의 10개 RANGING 전략이 유일한 진입 경로

### 다음 관찰 포인트
- RANGING 레짐에서 hot_path_guard의 RANGING 전략(higher_lows, inside_bar_break 등) 발화 확인
- RANGING → TRENDING 전환 시 cycle-level candidate_rotation 재개 확인
- obvious_trend 2건 수익(TOKAMAK)이 계속 유지되는지 모니터링
- range_scalp KRW-B3 -1.15%, KRW-ANKR -0.59% → 유동성 필터 추가 효과 확인

## 0. Latest Claude Notes - 2026-05-07 (session 11 — RANGING 레짐 Batch 3-6 활성화)

### 핵심 발견: Batch 2-6 전략이 72시간 단 1건도 발화 안 된 원인
```
hot_path_guard.py line 296-403:
if regime == "RANGING":
    ...  # range_scalp / range_breakout만 체크
    return False  ← 모든 Batch 3-6 전략 완전 차단!
```
- 18개 현재 candidate 중 consecutive_higher_lows=3/18, adx_trend_strong=2/18, 
  inside_bar_breakout=1/18, breakout_vol_confirm=1/18 발화 중이었으나 전부 차단
- recommendation_engine도 `watchlist_only` 반환 중 (사이클 레벨 진입 없음)
- hot_path가 유일한 진입 경로인데 RANGING 블록이 막고 있었음

### 수정 (커밋 64e2b45)
RANGING 블록 내 `_ranging_b_check()` 헬퍼 추가, 10개 RANGING 호환 전략 활성화:
1. `multi_ranging_combo` (combined≥0.52)
2. `demand_zone_bounce` (combined≥0.52)
3. `vwap_rsi_combo` (combined≥0.53)
4. `hammer_at_support` (combined≥0.52)
5. `consecutive_higher_lows` → higher_lows (combined≥0.55)
6. `inside_bar_breakout` → inside_bar_break (combined≥0.55)
7. `bb_squeeze_breakout` → bb_squeeze_break (combined≥0.56)
8. `breakout_vol_confirm` (combined≥0.56)
9. `rsi_bullish_div` (combined≥0.52)
10. `trend_reversal_early` (combined≥0.54)

### 배포
- 2026-05-07, Oracle VM pull+restart 완료 (64e2b45)

### 다음 관찰 포인트
- Batch 3-6 전략이 entry_profile에 나타나는지 확인 (목표: daily 10건 이상)
- RANGING에서 consecutive_higher_lows, inside_bar_breakout 첫 발화 확인
- multi_ranging_combo / demand_zone_bounce 수익성 확인
- 여전히 cycle-level은 watchlist_only → hot_path가 유일한 진입 수단

## 0. Latest Claude Notes - 2026-05-07 (session 10 — 급속 청산 + 블랙리스트 + range_scalp 개선)

### 배경: 72h 손실 원인 딥다이브
- `rapid_tick_failed_start` 40건 avg -0.571% → 최대 손실원
- `rapid_repeat_symbol_failure` 22건 avg -0.300% → 블랙리스트 TTL 부족
- `rapid_range_scalp_stop` 4건 avg -0.740% (ANKR/FIL/STORJ/SC, 2초 내 갭점프)
- DOGE 8건 0수익, LINK 7건 0수익 등 반복 진입 패턴

### 적용된 수정 (커밋 27a7dc8)

**hot_path_guard.py**
- `rapid_tick_failed_start` 2단계 조기화:
  - 1단계: 0.20min(12s) + pnl≤-0.25% → 즉시청산
  - 2단계: 0.33min(20s) + pnl≤-0.18% (기존 -0.22% → -0.18%)
- `_FAILURE_BLACKLIST_SECONDS`: 360s → **900s** (15분)
- `range_scalp` 유동성 필터 추가: vol_ratio≥0.8 OR vol_24h≥50억원 (소형코인 차단)

**state_store.py**
- range_scalp no_lift: 0.20min/-0.15% → **0.15min/-0.12%** (더 빠른 감지)
- range_scalp stop_pct: -0.30% → **-0.22%**

**session 9에서 적용된 수정도 별도 유지 (c57e687, b87042d)**

### 배포
- 2026-05-07, Oracle VM pull+restart 완료 (27a7dc8)

### 다음 관찰 포인트
- rapid_tick_failed_start 건수 40건→20건 이하 목표
- rapid_repeat_symbol_failure 22건→5건 이하 목표
- range_scalp: 유동성 없는 소형코인 진입 차단 확인
- strategy_performance 테이블 없음 → _strategy_is_disabled() 작동 방식 재확인 필요

## 0. Latest Claude Notes - 2026-05-07 (session 9 — 진입 게이트 대폭 강화)

### 배경: 라이브 성능 진단 결과 (72h 기준)
- 승률 9%, 총 PnL -39.25% (112 trades)
- `obvious_trend` 13건 → **전건 peak=0.000%** (RANGING 시장에서 방향 오류)
- cycle-level "unknown" 진입 105/112건 → combined 0.76-0.84에서 전부 손실
- Batch 3-6 신규 전략: 0건 (아직 미발화)

### 적용된 수정 (커밋 c57e687)

**Fix 1 — `hot_path_guard.py` obvious_trend_ok 강화**
- `trend_alignment`에서 "range" 제거 (RANGING 시장 차단)
- `stream_ignition` 필수화 (기존: 없음)
- `trend_score` 0.68 → 0.85, `chart_score` 0.76 → 0.82, `combined` 0.52 → 0.72
- `trend_extension` 8.5 → 6.0, `micro_vwap_gap` 6.5 → 4.0
- `trend_early` 불인정 (trend_entry_allowed 만 허용)

**Fix 2 — `execution_agent._crypto_obvious_trend_entry_ok()` 동일 강화**
- "range" alignment 제거, trend_early 불인정
- `trend_score` 0.68 → 0.82, `combined` 0.52 → 0.72, `stream_ignition` 필수
- `trend_extension` 8.5 → 6.0, `micro_vwap_gap` 6.5 → 4.0

**Fix 3 — `execution_agent._crypto_candidate_entry_ok()` 기본 진입 문턱 상향**
- `score` 0.76 → 0.82 (cycle-level 0.76-0.84 전건 손실)
- `trend_score` 0.58 → 0.62
- `ob (orderbook_bid_ask)` 1.08 → 1.12
- `micro_entry_ok` (micro≥0.55 AND vol≥1.1) 필수화 — 이전에는 launch_confirmed 선택지 중 하나

### 배포
- 2026-05-07, Oracle VM pull+restart 완료 (c57e687)

### 다음 관찰 포인트
- obvious_trend 발화 건수 대폭 감소 예상 (stream_ignition 필수 + combined 0.72)
- cycle-level 진입 건수 감소 → 승률 상승 기대
- Batch 3-6 전략 발화 여부 계속 모니터링 (현재 0건)
- rapid_tick_failed_start 여전히 39건 → 추가 타이트닝 검토 (-0.18%/15s)

## 0. Latest Claude Notes - 2026-05-07 (session 8 — Batch 4/5/6 전략 20개 추가, 목표 50개 달성)

### 추가된 전략 (커밋 64dec7e, cbb64be, 39b4991)

**Batch 4 — 6개 신규 (64dec7e)**
- `supertrend_long`: Supertrend(10,3) 불리쉬 전환 (ATR 기반)
- `engulfing_bull`: 불리쉬 인걸핑 캔들 (음봉→양봉 완전 흡수)
- `vol_surge_long`: 거래량 2.5배 + 양봉 (기관 매집 포착)
- `adx_trend_strong`: ADX≥22 + DI+>DI- (방향성 있는 추세)
- `bb_squeeze_breakout`: BB 스퀴즈 + 상단 돌파
- `consecutive_higher_lows`: 3연속 고점저점 구조 (HL 패턴)

**Batch 5 — 6개 신규 (cbb64be)**
- `pin_bar_long`: 아래꼬리 몸통 2배 핀바 캔들
- `morning_star`: 음봉→도지→양봉 3봉 반전 패턴
- `inside_bar_breakout`: 압축된 인사이드바 상단 돌파
- `rsi_momentum_keep`: RSI 55~72 추세 구간 유지 확인
- `oi_momentum_long`: 3봉 연속 거래량+가격 동반 상승
- `demand_zone_bounce`: 최근 20봉 지지 하단 반등

**Batch 6 — 8개 신규 (39b4991) — 목표 50개 달성**
- `vwap_rsi_combo`: VWAP 이탈 + RSI≤38 복합 신호
- `breakout_vol_confirm`: 20봉 고점 돌파 + 거래량 1.5배
- `hammer_at_support`: 지지선 망치형 캔들 반전
- `trend_reversal_early`: CHoCH 단독 조기 포착
- `ema_bounce_long`: EMA20 근처 반등 회복
- `rsi_bullish_div`: RSI 불리쉬 다이버전스
- `multi_ranging_combo`: RANGING 신호 3개+ 동시 만족
- `momentum_breakout_cont`: 브레이크아웃 2봉째 모멘텀 지속

**현재 전략 수: ~50개 distinct entry path (목표 달성 ✓)**

### 코드 구조 최종 상태
- `signal_engine.py`: 총 ~50개 신호 계산 + return dict
- `crypto_desk_agent.py`: 모든 신호 ranked_candidates/fallback/payload 3곳 전파
- `recommendation_engine.py`: ~40개 named entry 경로 + combined_score fallback
- `hot_path_guard.py`: 모든 entry_profile 진입 조건 + rapid 청산 + size 테이블
  - _b6_check 헬퍼 함수로 Batch 6 코드 간결화
  - RAPID_EXIT_REASONS: 약 35개 등록

### 배포
- 2026-05-07, Oracle VM pull+restart 완료 (39b4991)

## 0. Latest Claude Notes - 2026-05-07 (session 7 — 수익률 개선 + Batch 3 전략 6개)

### 수익률 개선 핫픽스 (커밋 55d5584 — 이전 세션)

**원인 분석**
- `obvious_trend` 전략이 `infer_strategy_id()` fallback으로 오염된 통계 때문에 permanently disabled 상태
  → `state_store.py`에서 `strategy_id = str(row.strategy_id or "unknown")` (inference 제거)
- `_ENABLE_EXPERIMENTAL_IMPULSE_ENTRIES = False` 플래그가 obvious_trend/range_impulse hot_path 완전 차단
  → `hot_path_guard.py`에서 두 플래그 게이트 제거
- `range_scalp` stop -0.50% 설정인데 실제 -0.80~-0.99% 청산: 슬리피지 salt 차이
  → stop -0.30%로 타이트하게 변경
- `rapid_tick_failed_start` 평균 -0.616%: 30s/-0.25% → 20s/-0.22%로 조기화

**수정 내용 (state_store.py)**
- strategy attribution: `infer_strategy_id()` fallback 제거
- range_scalp stop: -0.50% → -0.30%
- obvious_trend hard stop: -0.45% → -0.38%
- range_scalp no_lift: 24s → 12s (0.40min → 0.20min)
- rapid_tick_failed_start: 30s/-0.25% → 20s/-0.22%

**수정 내용 (hot_path_guard.py)**
- obvious_trend + range_impulse 게이트 플래그 제거 (이제 활성화)
- obvious_trend hard stop: -0.45% → -0.38%
- generic early exit 추가: peak≤0.05 AND 20s AND -0.22%
- range_scalp no_lift: 24s → 12s

### Batch 3 전략 6개 추가 (커밋 ca3a8fc)

**TRENDING 신호 3개 신규 (signal_engine 계산 로직 추가)**
- `rsi_flip_long`: RSI 50 하향→상향 돌파 — 모멘텀 전환 초기 포착
  - hot_path: trend_score≥0.60, combined≥0.56, ob≥1.06 / rapid: 18s/-0.28% 또는 -0.46%
- `macd_bull_cross`: MACD가 시그널선 상향 돌파 (음의 영역) — 중기 모멘텀 전환
  - hot_path: 동일 조건 / rapid: 18s/-0.28% 또는 -0.46%
- `triple_candle_bull`: 3연속 양봉 + 연속 고점 + 거래량 확인
  - hot_path: trend_score≥0.65, combined≥0.60, ob≥1.08 / rapid: 17s/-0.26% 또는 -0.44%

**구조 기반 전략 3개 신규 (기존 계산 신호 활용)**
- `pullback_continuation`: 급등 후 조정 재진입 Holy Grail (pullback_detected + vol_contracted)
  - hot_path: trend_score≥0.62, combined≥0.58, vol_contracted 필수 / rapid: 18s/-0.28% 또는 -0.46%
- `choch_momentum`: CHoCH + BOS 구조적 반전 ICT 스타일
  - hot_path: choch_bullish AND bos_bullish, trend_score≥0.62 / rapid: 18s/-0.30% 또는 -0.48%
- `ict_level_long`: 불리시 OB 또는 FVG 기관 매수 구간
  - hot_path: price_at_bull_ob OR price_in_bull_fvg AND ict_bullish_count≥2 / rapid: 18s/-0.30% 또는 -0.48%

**전파 경로**
- `signal_engine.py`: rsi_flip_long, macd_bull_cross, triple_candle_bull 계산 + return dict
- `crypto_desk_agent.py`: 3곳 (ranked_candidates, fallback, leader payload)
- `recommendation_engine.py`: 변수 추출 + 6개 신규 entry 경로 + bos_bullish/OB/FVG 추출 추가
- `hot_path_guard.py`: 6개 신규 진입 경로 + 각 entry_profile + RAPID_EXIT_REASONS + size table

**현재 전략 수**: ~30개 distinct entry path (목표 50개 대비 60%)

### 배포
- 2026-05-07 UTC, Oracle VM git pull + restart 완료 (ca3a8fc)

## 0. Latest Claude Notes - 2026-05-06 (session 6 — 전략 확장 Batch 2)

### 추가된 전략 신호 6개 (커밋 ece4116)

**RANGING 신호 4개 추가 (총 10→14개)**
- `williams_r_oversold`: Williams %R ≤ -80 → -80 이상으로 교차 (과매도 탈출)
- `cci_oversold_bounce`: CCI ≤ -100 상태에서 상승 전환 (CCI 과매도 반등)
- `keltner_lower_touch`: 가격 ≤ EMA20 - 1.5×ATR14 (켈트너 채널 하단 터치)
- `mfi_oversold`: MFI ≤ 25 (거래량 가중 RSI 과매도)

**TRENDING 신호 2개 추가 (신규 진입 경로)**
- `ema_cross_long`: EMA8이 EMA21 위로 교차 (골든크로스 변형, 조기 진입)
  - hot_path_guard: trend_score ≥ 0.65, combined ≥ 0.60, ob ≥ 1.08
  - rapid fail: min≥0.3 peak≤0.05 pnl≤-0.30 또는 pnl≤-0.50
- `vwap_cross_long`: 가격이 VWAP 아래에서 위로 복귀 (기관 평균단가 재탈환)
  - hot_path_guard: trend_score ≥ 0.62, combined ≥ 0.58, ob ≥ 1.06
  - rapid fail: min≥0.3 peak≤0.05 pnl≤-0.30 또는 pnl≤-0.50

**전파 경로**
- `signal_engine.py`: 6개 신호 계산 로직 추가
- `crypto_desk_agent.py`: ranked_candidates, fallback dict, payload 3곳 추가
- `recommendation_engine.py`: 변수 추출 + RANGING /14 + ema_cross/vwap_reclaim TRENDING 경로 추가
- `hot_path_guard.py`: RANGING gate /14 + ema_cross/vwap_reclaim 경로 + 진입 크기 + 빠른 청산 guard

**현재 전략 수**: ~23개 distinct entry path (목표 50개 대비 46%)

### 배포
- 2026-05-06 UTC, Oracle VM pull+restart 완료

## 0. Latest Claude Notes - 2026-05-06 (session 5 — 수익률 개선)

### 데이터 기반 분석 (300건 closed positions)
- 승률 9.7% (RANGING gate 이전 구 데이터 322건이 대부분)
- RANGING gate 이후: range_scalp 4건, 50% 승률
- 최대 손실 원인: rapid_tick_failed_start(95건 -58%), rapid_obvious_trend_fail(70건 -35%)
- range_scalp avg stop -0.895% (설정 -0.70% 초과), KRW-STORJ dev=+1.32%에서 잘못된 방향 진입

### 수익률 개선 수술 (커밋 d3b4b3d)

**1. range_scalp stop -0.70% → -0.50%** (`state_store.py`)
- STORJ -0.80%, SC -0.99% → 최대 -0.50%로 제한

**2. dev > +1.0% range_scalp 롱 차단** (`hot_path_guard.py`)
- `airborne_deviation_pct > 1.0` 이면 range_scalp_hot_ok=False
- EMA 위에서 평균회귀 롱 = 방향 반대 → 구조적 실패 원천 제거

**3. rapid_tick_failed_start fallback** (`state_store.py`)
- stream_reversal 없어도: peak<=0.05 AND min>=0.5 AND pnl<=-0.25% → 즉시청산
- avg -0.612% → ~-0.25% 목표 (손실 59% 절감)

**4. _crypto_trail_rules 수익 보호 강화** (`state_store.py`)
- peak >= 0.55%: giveback 0.30→0.20 (수익반납 -0.28% 사례 방지)
- peak >= 0.40%: giveback 0.30→0.20, floor 0→0.05
- peak >= 0.25%: 신규 tier (giveback 0.15, 조기 원금 보호)

**5. range_scalp no_lift 단축** (`state_store.py`)
- 3.0min → 1.5min, -0.25% → -0.22%

### 배포
- 2026-05-06 07:28 UTC, 에러 없음

## 0. Latest Codex Notes - 2026-05-06 (RANGING local breakout strategies)

### Session Goal
Continue strategy expansion while fixing the scanner-observed issue where the global regime is `RANGING` but individual coins such as HYPER/BIO/SPK can still show strong local continuation.

### Implemented
- Added 2 crypto continuation strategies:
  - `crypto.range_breakout`: local 20-candle range high reclaim with volume and no bearish RSI/ICT warning.
  - `crypto.high_tight_flag`: strong impulse followed by compact consolidation near highs.
- Propagated both signals through:
  - `signal_engine.py`
  - `crypto_desk_agent.py`
  - `recommendation_engine.py`
  - `hot_path_guard.py`
  - `state_store.py`
- RANGING routing now has two explicit paths:
  - Mean reversion path: Airborne/BB/VWAP/RSI/Stoch/MACD/candle/volume climax/support reclaim.
  - Local continuation exception: range breakout or high-tight flag only, so generic trend chasing stays blocked.
- Hot path now arms `range_breakout` and `high_tight_flag` candidates and opens only after websocket tick confirmation.
- Added strategy attribution labels and tighter thresholds:
  - `range_breakout`: target +2.20%, stop -1.00%, max ~12 minutes.
  - `high_tight_flag`: target +1.80%, stop -0.90%, max ~12 minutes.
- Added rapid guards:
  - Fail fast if no lift shortly after entry.
  - Protect profits once peak reaches ~0.35-0.45%.

### Why This Matters
This avoids the previous binary mistake of “RANGING means no trend trades at all.” The bot still blocks weak CHoCH/BOS noise in a range, but can now participate when a single coin genuinely breaks its own box and live ticks confirm continuation.

## 0. Latest Codex Notes - 2026-05-06 (strategy attribution foundation)

### Session Goal
Before adding more crypto strategies, make each entry path measurable. The bot has been losing because weak strategies and strong strategies were mixed together in aggregate PnL, so the next layer needs per-strategy attribution and future kill switches.

### Implemented
- Added `strategy_id` and `entry_profile` to `PaperOrder` and `PaperPosition` models.
- Added SQLite columns/indexes for `paper_orders` and `paper_positions` with schema migration in `state_store.py`.
- Added `infer_strategy_id()` mapping for legacy rows and future entries:
  - `crypto.range_scalp`
  - `crypto.tick_ignition`
  - `crypto.pullback_entry`
  - `crypto.trend_pullback`
  - `crypto.direct_entry`
  - `crypto.composite_entry`
  - `crypto.stream_entry`
  - `crypto.candidate_rotation`
  - `crypto.balanced_swing`
  - `crypto.offense_probe`
  - fallback labels for unknown action/focus paths
- Execution agent now writes `strategy_id` and `entry_profile` into order metadata and `PaperOrder`.
- Hot path direct DB inserts now persist strategy metadata for websocket tick entries.
- Dashboard/performance APIs now expose strategy-level stats:
  - `/performance-data` -> `strategy_stats`
  - `/performance?format=json` -> `strategy_stats`
  - `ops_summary` / `mobile_summary` include strategy performance diagnostics.

### Metrics Produced
`load_strategy_performance_stats()` groups recent closed paper positions by strategy and calculates:
- count / wins / losses / win_rate
- raw_pnl_pct and capital_pnl_pct
- avg_raw_pnl_pct and avg_capital_pnl_pct
- peak0_pct
- stop_like_pct
- avg_size
- health: `candidate`, `watch`, or `disabled_candidate`

### Why This Matters
This is the foundation for the next critical step: automatic strategy kill switches. Once enough fresh post-RANGING-gate trades accumulate, strategies with low win rate, high peak0%, or negative capital contribution can be blocked or moved to shadow mode instead of continuing to burn capital.

### Verification
- `python -m compileall app` passed locally.
- Local smoke test passed: `init_db()`, `load_strategy_performance_stats()`, and `load_company_state()` all run with the new schema.

### Suggested Next Work
1. Add shadow-mode logging for disabled strategies so they can keep being measured without real/paper capital impact.
2. Show strategy stats visibly on `/performance` and mobile summary instead of only JSON/API output.
3. Add strategy reset controls so old pre-fix data can be archived without deleting raw history.

### Follow-up Patch: Strategy Kill Switch
- Added cycle-level strategy disable gate in `ExecutionAgent`.
- Added websocket hot-path strategy disable gate in `hot_path_guard.py`.
- Hot-path blocklist is cached for 60 seconds so tick callbacks do not query SQLite on every tick.
- Disabled strategies now produce an explanatory note with win rate, capital PnL, and peak0%.
- This converts attribution from passive reporting into active capital protection.

### Follow-up Patch: Shadow Mode Logging
- Added `shadow_signals` table for blocked strategy signals.
- Added `save_shadow_signal()` with 60s dedupe to avoid websocket log spam.
- Added `load_recent_shadow_signals()` and `load_shadow_signal_stats()`.
- Cycle-level disabled entries now log a shadow signal instead of silently disappearing.
- Hot-path disabled candidates/open attempts now log shadow signals with candidate/stream payload.
- `/performance-data` and `/performance?format=json` expose:
  - `shadow_signal_stats`
  - `recent_shadow_signals`
- This allows disabled strategies to keep being measured without opening paper/live positions.

### Follow-up Patch: Performance UI + RANGING Strategy Expansion
- `/performance` now renders:
  - strategy-level performance table
  - strategy health: allowed / watch / blocked
  - shadow signal summary
  - recent shadow signal rows
- Added 2 more RANGING mean-reversion signals:
  - `volume_climax_reversal`: abnormal volume + large lower wick + close recovery
  - `support_reclaim_long`: recent range low probe + close reclaim
- RANGING mean-reversion signal count is now 10:
  1. Airborne EMA deviation
  2. BB squeeze bounce
  3. VWAP deviation
  4. RSI extreme
  5. RSI mean reversion
  6. Stochastic oversold cross
  7. MACD histogram reversal
  8. Hammer/Doji candle reversal
  9. Volume climax reversal
  10. Support reclaim
- `crypto_desk_agent`, `recommendation_engine`, and `hot_path_guard` now propagate/use the 2 new signals.

## 0. Latest Claude Notes - 2026-05-06 (session 4)

### Session Goal
Fix 8.8% win rate / -26.72% PnL (all pre-RANGING gate). Strategy count: 8 RANGING signals.

### Fix 1: Hot path entry quality tightened (`hot_path_guard.py`)
- `common_guards`: micro_move_3 ≤0.95 (was 1.20), vwap_gap ≤1.50 (was 1.80), freshness ≥0.58
- `standard_ok`: trend_score ≥0.78, combined ≥0.74, ob ≥1.10, extension ≤2.8
- `early_ok`: trend_score ≥0.72, combined ≥0.76, ob ≥1.22, extension ≤1.8
- `trend_ignition`: ticks≥4, score≥0.74, move5≥0.12, 0.35≤move15≤0.75, move60≤1.20, buy_ratio≥0.63, move5≥move15×0.20

### Fix 2: Repeat failure blacklist (`hot_path_guard.py`, `state_store.py`)
- Failure threshold: -0.30% → -0.15%
- Limit: 2 → 3 lookback; 2+ small failures → 6-min blacklist
- `_failure_blacklist: dict[str, float]`; cleared on success
- Cooldown update moved to success path only

### Fix 3: RANGING strategy expansion — first batch (session 3, commit 7eb5129)
Added 3 new signals to `summarize_crypto_signal()`:
- `bb_squeeze_bounce`: BB폭 수축 + 하단 터치 + RSI 22-52
- `vwap_deviation_long`: 20봉 VWAP 대비 -1.5% 이하 + RSI 20-50
- `rsi_extreme_long`: RSI ≤22
RANGING block: 5-signal composite (mean_rev_count/5), 0.35x/0.45x/0.55x sizing

### Fix 4: RANGING strategy expansion — second batch (this session, commit b5e1d9b)
Added 3 more signal groups → total 8 RANGING signals:

**signal_engine.py**:
- `stochastic()` helper: %K=(close-ll)/(hh-ll)×100, %D=SMA(K,3)
- `stoch_oversold_cross`: %K<25 AND K crosses above D (직전 K<D, 현재 K≥D)
- `macd_histogram_reversal`: EMA12-EMA26 히스토그램이 음수 구간에서 2봉 연속 반등
- `hammer_candle`: 양봉, 하단꼬리≥몸통2배, 상단꼬리≤몸통0.5배, RSI≤50
- `doji_candle`: 몸통≤범위10%, 범위≥0.3%, RSI≤50
- Return fields added: stoch_k, stoch_oversold_cross, macd_hist, macd_histogram_reversal, hammer_candle, doji_candle

**crypto_desk_agent.py**: 6 new fields in ranked_candidates, fallback, leader payload

**recommendation_engine.py**:
- sig_stoch_cross, sig_macd_rev, sig_candle_rev added
- mean_rev_count now /8 (was /5)
- ranging_signal_note updated

**hot_path_guard.py**: 4 new booleans in RANGING gate (stoch_oversold_cross, macd_histogram_reversal, hammer_candle, doji_candle)

### Current State
- Deployed: 2026-05-06 01:11 UTC, no errors
- Regime: RANGING (market still ranging, dev=6%)
- Bot operational, waiting for mean-reversion conditions

### Pending
- Monitor: stoch/MACD/hammer signals firing rate
- Strategy count: 8 RANGING signals + TRENDING path = ~15 total combinations → target 50
- Next expansion ideas: multi-timeframe confirmation, volume profile, Ichimoku cloud support
- Merge remaining tmp file cleanup

## 0. Latest Claude Notes - 2026-05-06 (session 3)

### Root Cause Analysis: 9% win rate, -122% PnL (322 trades)
- **All 10 recent cycles**: regime=RANGING — trend-following in RANGING = structural loss
- **74% of trades**: peak_pnl=0.00% — entered at wrong moment, price reversed immediately
- **Root cause**: bot was running trend strategies in sideways/consolidation market

### Fix F: RANGING regime gate (all 5 files)
Block trend entries completely in RANGING; add mean-reversion (Airborne) strategy instead.

**signal_engine.py**:
- `detect_airborne_signal()`: MG 에어본 지표 (price deviation from EMA20 + Bollinger Bands)
  - `deviation_pct = (price/EMA - 1) * 100`; `deviation_sigma` = normalized
  - `airborne_long` when deviation_pct <= -1.5% (price below EMA, expect bounce)
  - `bb_pct_b`, `at_bb_lower` as confirmation
- `range_scalp_eligible`: airborne_long + RSI 22-48 + flat slope + no bearish structure
- `rsi_mean_rev_long`: RSI <= 35 + no downtrend (fallback signal)

**recommendation_engine.py**:
- RANGING block before trend logic: if range_scalp_ok → `probe_longs` with focus="range_scalp:..."
- Otherwise → `watchlist_only` (trend blocked in RANGING)
- range_scalp size: 0.55x / 0.45x / 0.35x based on airborne_sigma + bb_lower

**crypto_desk_agent.py**:
- All airborne/range_scalp fields propagated: candidates, leader fallback, leader payload

**state_store.py**:
- `_position_thresholds`: "range_scalp" in focus → 1.20% target, -0.70% stop, 75 cycles
- `_range_scalp_trail_rules(peak_pnl)`: tiered trail (0.15→0.30 giveback per peak tier)
- `_range_scalp_no_lift_exit()`: exit if no lift after 4-12 min
- `manage_paper_positions`: branched range_scalp exits
- `rapid_guard_crypto_positions`: range_scalp rapid path

**hot_path_guard.py** (this session):
- `refresh_hot_entry_candidates`: inject `regime` from state into each candidate
- `_candidate_is_hot_entry_eligible`: RANGING gate at top — blocks all trend paths;
  range_scalp path: `range_scalp_eligible + airborne_long + score>=0.40 + OB>=1.05`
- `_hot_entry_size`: range_scalp sizing 0.04-0.06x
- `hot_guard_crypto_tick`: pass `focus` to `_position_thresholds`; dedicated range_scalp
  rapid guard using `_range_scalp_trail_rules` + `_range_scalp_no_lift_exit`
- `hot_process_crypto_tick`: range_scalp ignition (lighter bar: move5>=0.05, score>=0.52)
- `_open_hot_entry`: range_scalp focus tag "range_scalp: ..." for state_store routing

### Pending / Next steps
- Monitor performance: RANGING gate should push win rate from 9% toward 40%+
- 50-70 strategy combinations (long-term goal): add BB bounce, VWAP reversion, RSI
  extreme, stochastic, MACD crossover, etc. — one strategy per 3-5 regime × signal combos
- Clean up tmp files: `tmp_analyze.py`, `tmp_analyze2.py`, `tmp_vm_query*.py`,
  `tmp_state.py`, `tmp_deep_analysis.py`

## 0. Latest Claude Notes - 2026-04-30 (session 2)

### Strategic Direction (user-confirmed)
오토/퀀트 트레이딩: 추세 추종 + 틱/밀리초 즉각 대응.
- **Entry**: 상승 추세 트리거 포착 즉시 올라타기 (no EMA-stack delay)
- **Exit**: 하락 추세 트리거 포착 즉시 청산 (no hold-time buffer)
- 분봉/시간봉/일봉 = 컨텍스트만. 딜레이 도구 아님.
- Goal: 1천만원 → 1억원 (2026년 내)

### Fix A: Trail protection for +0.40-0.80% peak positions (`state_store.py`)
`_crypto_trail_rules()` previously had no tier between 0% and 0.80% — a position peaking at
+0.50% could reverse all the way to 0% with zero automatic protection (API3 trade pattern).

Added new tier:
```python
if peak_pnl >= 0.40:
    return 0.30, 0.00  # exit if falls 0.30% from peak → near breakeven
```
Now: peak=0.50% → trail fires at +0.20% instead of waiting for hard stop at -2%.

### Fix B: `trend_early_entry` flag in `signal_engine.py`
`trend_entry_allowed` requires full 3-EMA stack (EMA8 > EMA21 > EMA34 on 15m), which lags
trend start by 45-90 min. Bot was entering at the TOP of impulse moves.

New flag `trend_early_entry` fires 1-2 candles (15-30 min) BEFORE full EMA stack confirms:
```python
trend_early_entry = (
    price_above_EMA21
    and trend_slope_pct >= 0.08
    and trend_alignment not in {"downtrend", "late_extension"}
    and (choch_bullish or bos_bullish)      # structural break = real trend change
    and not choch_bearish
    and not rsi_bearish_divergence
)
```

### Fix C: `trend_early_entry` propagated through agent pipeline (`crypto_desk_agent.py`)
Added `"trend_early_entry"` field to `ranked_candidates` dict, leader fallback dict, and
leader propagation section. Now flows all the way to hot_path_guard and recommendation_engine.

### Fix D: Hot path early trend path + extension gate (`hot_path_guard.py`)
`_candidate_is_hot_entry_eligible()` refactored into two paths:

**Standard path** (EMA stack confirmed):
- Same as before + `trend_extension_pct <= 3.0` (blocks entry when price is 3%+ above EMA21)

**Early trend path** (CHoCH/BOS fires before EMA stack):
- `trend_early_entry=True` + not in downtrend/late_extension
- Stricter thresholds: `trend_score >= 0.70`, `combined >= 0.74`, `ob >= 1.20`, `extension <= 2.0`

### Fix E: Extension gate in `recommendation_engine.py`
`direct_entry_ok` now includes:
```python
and (trend_extension_pct <= 2.8 or pullback_detected)
```
Prevents the orchestrator from recommending direct entry when price is already 3%+ overextended
from EMA21 (which was the root cause of many "enter at top of impulse" trades).

### Files changed this session
- `app/core/state_store.py` — trail tier 0.40-0.80%
- `app/services/signal_engine.py` — `trend_early_entry` flag
- `app/agents/crypto_desk_agent.py` — propagate `trend_early_entry`
- `app/services/hot_path_guard.py` — early trend path + extension gate
- `app/services/recommendation_engine.py` — extension gate on `direct_entry_ok`

## 0. Latest Claude Notes - 2026-04-30

### Root Cause: Cascading Failure Cycle (now fixed)

The bot was stuck in a loop: fake losses from 8-second `trend_invalid_exit` → high stop pressure → 
throttled entries → fewer wins → more pressure → repeat. Four separate fixes were required.

### Fix 1: Hold-time ladder for trend exits (commit 6d3f497 — previous session)
`_crypto_trend_exit_reason()` now requires minimum hold time before any trend-based exit:
- choch_bearish/bos_bearish: wait 2 min
- stream_reversal/downtrend/bearish_divergence: wait 3 min
- trend_invalid_exit: wait 4 min + pnl <= -0.20%

Before this: positions were closing in 8-16 seconds with `trend_invalid_exit` (65% of all losses were
fake — coin never had time to show whether the trend was valid). Now exits are gated by actual holding time.

### Fix 2: Mid-range failed-breakout exit (commit 9eaa06b)
Two new early-exit rules prevent API3-style reversals from hitting the full -2% hard stop:

**In `rapid_guard_crypto_positions`** (runs every 2 sec):
```
If peak in [0.40, 0.80%) and minutes_open >= 1.0 and pnl <= max(-0.55, peak - 1.10):
    → failed_breakout_exit
```
Example: peak=0.59%, fires at -0.51% instead of waiting for -2.32% hard stop.

**In `manage_paper_positions`** (main cycle, runs every 8s):
```
If peak >= 0.40 and minutes_open >= 3.0 and pnl <= max(-0.50, peak - 1.20):
    → failed_followthrough
```
(Previously required peak >= 0.65 and 8 minutes — missed most reversals.)

### Fix 3: STOP_LIKE_EXIT_REASONS narrowed to true hard stops (commit 7e1f07f)
Time-gated managed exits (trend_invalid_exit, downtrend_exit, trend_reversal_exit,
bearish_divergence_exit, rapid_no_lift, no_lift_exit, reversal_loss_exit, failed_ignition,
failed_followthrough, rapid_flat_timeout, flat_no_lift_exit) were removed from the set.

**Why**: These exits require 2-18 minutes of holding before firing — they are controlled risk
management, not immediate failures. Including them caused:
- stop_pressure = "high" after any 3 managed exits → probe_longs downgraded to selective_probe
- symbol_edge_state "cold" → re-entry blocked for coins that just had managed exits
- _recent_crypto_symbol_failure flagging coins unnecessarily
- Size scaled down to 0.07x on otherwise valid setups

**Now**: Only `stop_hit`, `rapid_stop_hit`, `early_failure`, `rapid_failed_start`,
`rapid_repeat_symbol_failure` count. True stops only.

### Fix 4: Trend-pullback fast-path in `_crypto_candidate_entry_ok` (commit c82f7f5)
Added a fast-path for strong trend+orderbook setups that were blocked by the combined_score >= 0.76 gate:

```
If trend_alignment in (pullback_long, uptrend) AND trend_score >= 0.80 AND ob >= 1.60x
   AND micro >= 0.50 AND combined >= 0.65 AND no recent hard stop:
    → eligible (bypass the 0.76 combined_score requirement)
```

**Why**: Coins like SOL/XRP regularly showed trend_score=0.94, ob=2.53x, micro=0.68 but 
combined_score=0.72. These are EXCELLENT trend-following setups. The 0.76 combined_score gate 
was blocking the exact trades the strategy was designed to catch.

### Second Oracle VM data reset
Performed 2026-04-30T00:00 UTC to clear polluted stats from 45+ fake losses.
All paper_positions, closed_positions, cycle_journal were cleared.
`company_state` table NOT cleared (preserves regime, stance, strategy_book).

### Post-reset trade results (first 4 trades, as of 2026-04-30T00:20 UTC)
| Symbol   | PnL    | Peak   | Reason                | Notes                     |
|----------|--------|--------|-----------------------|---------------------------|
| CHIP     | +2.47% | +3.42% | rapid_trend_trail     | WIN — trend worked ✓      |
| ADA      | -0.27% | +0.02% | trend_invalid_exit    | small managed exit ✓      |
| API3     | -2.32% | +0.59% | rapid_stop_hit        | hard stop — failure. New failed_breakout_exit would cut at -0.51% |
| XRP      | -0.35% | 0.00%  | rapid_no_lift         | 10-min no-lift timeout ✓  |

Win rate: 1/4 = 25% (too early to evaluate). Net: -0.47%.
After failed_breakout_exit fix, API3-style loss → -0.51% (saves ~1.8% per trade).

### Current VM state (2026-04-30)
- Oracle VM: 134.185.118.144, both services active
- trading-loop service deployed commit c82f7f5
- Bot entering again: SHIB opened at 00:21:48 at 0.10x (cooldown multiplier due to low win rate)
- Size will increase naturally as win rate improves
- regime=RANGING, stance=BALANCED, capital_profile mode=neutral, allow_new_entries=True

## 0. Latest Claude Notes - 2026-04-28

Three critical bugs fixed that prevented multi-coin trading:

### Bug 1: Weight gate blocking all crypto entries
`build_crypto_plan()` in `recommendation_engine.py` had weight thresholds of 0.15-0.20 for entry.
With 9-coin neutral weights (max 0.14), NO coin ever passed. Every cycle returned `watchlist_only`
despite good signal scores (0.80+). Fixed: lowered thresholds to 0.08/0.10.

### Bug 2: ExecutionAgent generated only 1 order per desk
Added `ExecutionAgent._multi_orders()` that generates up to `max_positions` (3) concurrent orders
per desk by iterating ranked candidates. Base size is divided evenly across slots so total
notional stays constant. Falls back to single-order behavior when slots <= 1.

### Bug 3: ADA/AVAX/TRX/LINK missing from crypto price lookup
`_PINNED_CRYPTO` in `market_gateway.py` only had 5 coins (BTC/ETH/XRP/SOL/DOGE). When ADA/AVAX
etc. weren't in the top-20 Upbit volume list, `_manage_positions` couldn't find their price and
silently skipped opening the position. Fixed: pinned all 9 neutral-weight coins.

### Dynamic crypto universe - Codex 2026-04-28
The 9 fixed coins are now only a safety/price fallback, not the trading universe.
`get_krw_crypto_candidates()` scans the full Upbit KRW ticker universe each cycle and ranks coins by
live liquidity, positive momentum, and volatility. `CryptoDeskAgent` merges that live shortlist with
backtest weights, then runs expensive 15m/1m/orderbook analysis only on the top live candidates.
This keeps the universe open to all KRW coins while preventing API overload.

### Also fixed (prior session, still relevant)
- Crypto universe expanded: 2 coins (DOGE/XRP) → 9 coins with parallel evaluation
- Compounding capital: cumulative all-time P&L now tracked, displayed as 복리자본
- Position PnL display bug: renderPositions JS was using `p.unrealized_pnl_pct` (always 0), fixed to `p.pnl_pct||p.unrealized_pnl_pct`
- Mobile session auth: cookie-based so JS fetch() calls work on mobile browsers

### Commits this session
- `b8ea393` — universe expansion (9 coins), compounding capital, PnL display fix
- `da7e1e9` — multi-position execution + weight gate fix
- `98ddbf1` — pin all 9 neutral-weight coins in crypto_leaders price lookup

### Current VM state
- Oracle VM: 134.185.118.144, both services active
- `EXECUTION_MODE=upbit_live`, `UPBIT_PILOT_SINGLE_ORDER_ONLY=true` (1 live order/cycle, accumulates to 3)
- KRW-ETH currently open as paper position (live order failed, paper fallback caught it)
- Expected: 2-3 simultaneous crypto positions now functional; Korea/US open as market hours permit

## 0. Latest Codex Notes - 2026-04-27

- Project name/direction: "코인, 한국 주식 수익 극대화 프로젝트"; keep aligned with profit-maximizing, short-swing, volatility-event strategy.
- Latest pushed commit: `5509394 feat: add RSI quality overlay to crypto breakouts`.
- Deployed on Oracle VM and restarted `trading-loop` / `trading-dashboard`; both services were active after deploy.
- Added Ross Cameron / Warrior-style RSI usage as a crypto breakout quality overlay, not a standalone buy signal:
  - RSI reset/reclaim adds score for continuation after cooling.
  - Bearish RSI divergence blocks late breakout chasing.
  - RSI extreme zone blocks overheated entries.
  - Crypto overheat block was relaxed from RSI >= 74 to RSI >= 82, while divergence/extreme quality filter now handles late-chase risk.
- A `gross exposure cap breached (1.30x)` dashboard message means total open notional exposure is about 1.30x account capital and new entries are blocked by the gross exposure gate. On the Oracle check immediately after this update, current state showed `allow_new_entries=True`, `open_positions=0`, and no current gross exposure value, so the warning was not active at that moment.
- Added high-return crypto phase 1: a 1-minute micro momentum layer now runs beside the existing 15-minute swing breakout layer.
  - `get_upbit_1m_candles()` fetches 1m candles through the shared Upbit minute candle helper.
  - `summarize_crypto_micro_momentum_signal()` scores 1m high-of-window breaks, VWAP reclaim, EMA5/EMA20 stack, volume expansion, RSI momentum, and exhaustion risk.
  - `CryptoDeskAgent` blends 15m swing score, 1m micro score, BTC backdrop, and backtest weights.
  - `build_crypto_plan()` can allow a smaller `selective_probe` when 1m momentum is ready while the 15m swing setup is still forming.
- Added high-return crypto phase 2: orderbook pressure layer.
  - `get_upbit_orderbook()` fetches current Upbit depth snapshots.
  - `summarize_orderbook_pressure()` scores bid/ask depth ratio, top-5 stack, spread, and imbalance.
  - Crypto candidate ranking now blends swing, 1m momentum, orderbook pressure, BTC backdrop, and backtest weights.
  - 1m early entries now require either orderbook-ready pressure or a near-ready orderbook score, reducing false breakouts with weak depth.

## 1. Workspace

- Root repo: `C:\Users\User\Desktop\trading-bot`
- Backend app: `C:\Users\User\Desktop\trading-bot\trading_company_v2`
- React frontend: `C:\Users\User\Desktop\trading-bot\frontend`
- Default branch: `main`
- Git remote: `https://github.com/jasper-Choi/trading-bot.git`

## 2. Verified today

- Git remote is configured and points to GitHub.
- Recent commits include live broker scaffolding and dashboard redesign work.
- Local backup DB exists:
  - `C:\Users\User\Desktop\trading-bot\trading_company_v2\data\trading_company_v2.backup.db`
- Active DB exists:
  - `C:\Users\User\Desktop\trading-bot\trading_company_v2\data\trading_company_v2.db`
- Oracle SSH key folder exists:
  - `C:\Users\User\Desktop\trading-bot\trading_company_v2\오라클 SSH키`
  - key file: `ssh-key-2026-04-21.key`
- Dashboard server logs show external requests hitting `/health` and `/dashboard-data`.
- Local dashboard binds to `0.0.0.0:8080`, and duplicate starts fail because the port is already in use.
- Tailscale serve is active and currently proxies local dashboard traffic.

## 3. Current product state

### Live execution

- Execution modes are separated:
  - `paper`
  - `upbit_live`
  - `kis_live`
- Live routing exists in:
  - `app/services/broker_router.py`
- Safety gates exist:
  - `UPBIT_ALLOW_LIVE`
  - `KIS_ALLOW_LIVE`
- Upbit live scaffold exists:
  - order placement
  - balance sync
  - order status lookup
- KIS live scaffold exists:
  - token / hashkey flow
  - cash buy / sell
  - balance lookup
  - recent order status normalization

### Live ledger and safety

- `app/core/state_store.py` includes live order ledger logic.
- Partial fill states are tracked explicitly.
- Duplicate live orders are blocked when unresolved live orders already exist.
- Conservative mode lowers risk budget and blocks fresh entries when live execution state is unresolved.
- Stale live orders are surfaced separately.

### Diagnostics

Endpoints in `app/main.py`:

- `/diagnostics/live-execution-health`
- `/diagnostics/broker-live-health`
- `/diagnostics/live-readiness-checklist`
- `/diagnostics/access-map`
- existing session / live decision diagnostics

These diagnostics are also fed into dashboard data for web/mobile visibility.

### Notifications

- Telegram spam has been reduced with cooldowns and duplicate suppression.
- Passive-only realtime decision alerts are suppressed.
- Stale live execution alerts were added with low-frequency cooldown behavior.

## 4. Dashboard / UI state

### React frontend

Files:

- `frontend/src/App.jsx`
- `frontend/src/index.css`
- `frontend/src/api.js`
- `frontend/src/components/InsightPanel.jsx`

Status:

- React web/mobile UI is in major redesign mode.
- Layout now uses an app-style shell:
  - hero header
  - overview cards
  - execution + readiness signal deck
  - feature panels for positions, insight, pnl, trades, logs
- Mobile behavior has been updated so the new structure collapses into a single-column app-like layout.
- Build was passing after redesign:
  - `npm run build`

### Embedded dashboard

- `app/main.py` now includes a new embedded app-style renderer.
- It shows:
  - access cards
  - execution signal deck
  - readiness and broker health
  - positions, closures, equity, insight, journal
- Mobile readability is much better now.
- It may still need more visual polish to fully match the React UI.

## 5. Access mapping

- Confirmed Tailscale serve status:
  - `https://desktop-891gpaq.taile9aa15.ts.net` `(tailnet only)`
  - proxy target: `http://127.0.0.1:8080`
- Current detected routes from `/diagnostics/access-map`:
  - `local_url`: `http://127.0.0.1:8080`
  - `lan_url`: `http://10.10.1.65:8080`
  - `public_url`: `https://desktop-891gpaq.taile9aa15.ts.net`
- Auth is enabled.
- The current canonical external route is the Tailscale tailnet URL, not a public-open Oracle internet route.
- Support exists for:
  - `PUBLIC_BASE_URL`
  - `PUBLIC_BASE_LABEL`
- These are now set in `.env`, so the route surfaces in:
  - `/health`
  - `/diagnostics/access-map`
  - embedded dashboard access cards

## 6. Important operational notes

- The user wants autonomous execution with progress reporting, not step-by-step approval.
- The user explicitly wants web and mobile redesigned to look like a real app / website before moving into the final live-readiness stage.
- Do not promise profits or guaranteed returns.
- Prioritize safety, monitoring clarity, and execution correctness before real-money expansion.
- PowerShell output has shown mojibake on Korean text before. Prefer ASCII-safe edits in critical frontend files when possible.
- Frontend policy is now source-first:
  - commit `frontend/src/*`
  - treat `frontend/dist/*` as local build output unless a deployment path explicitly requires checked-in assets

## 7. Current known evidence from logs

- `data/dashboard_server.log` shows repeated successful hits to:
  - `/health`
  - `/dashboard-data`
- Requests include non-local source entries, which supports that external access routing is already being exercised.
- Duplicate starts fail because port `8080` is already bound, which suggests the dashboard server is already up.

## 0.9 Oracle Cloud 24/7 배포 + Upbit 실전 전환 준비 (2026-04-22)

### Oracle Cloud VM 설정
- VM: `134.185.118.144` (VM.Standard.E2.1.Micro, Ubuntu 22.04)
- SSH 키: `trading_company_v2/오라클 SSH키/ssh-key-2026-04-21.key`
- systemd 서비스 2개 등록 (자동 재시작):
  - `trading-dashboard.service` → uvicorn, port 8080
  - `trading-loop.service` → `python -m app.runtime`
- 2GB swap 설정 완료
- OCI Security List에 TCP 8080 ingress 허용

### Upbit 실전 전환 준비
- Upbit API 키 발급 및 VM `.env`에 등록 완료
- `UPBIT_ALLOW_LIVE=true`, `LIVE_CAPITAL_KRW=2000000` 설정
- 파일럿 가드레일: `UPBIT_PILOT_MAX_KRW=150000`, `UPBIT_PILOT_SINGLE_ORDER_ONLY=true`
- `/diagnostics/upbit-live-pilot` → 현재 `go_live_ready: false`
  - blockers: 0개 (API 연결, 잔고조회 모두 통과)
  - caution: daily drawdown entry gate 차단 중 (-3.03%)
- 자정 KST 자동 전환 cron 등록: `/home/ubuntu/go_live.sh` (매일 15:00 UTC)
  - entry gate 해소 확인 후 `EXECUTION_MODE=upbit_live` 변경 + 서비스 재시작

### 환경 분리
- **로컬 PC**: `EXECUTION_MODE=paper`, `UPBIT_ALLOW_LIVE=false` (개발/모니터링 전용)
- **Oracle VM**: `UPBIT_ALLOW_LIVE=true`, `EXECUTION_MODE=paper→upbit_live(자정전환예정)`

### 접근
- 대시보드: `http://134.185.118.144:8080/` (기본 인증 필요)
- Tailscale: `https://desktop-891gpaq.taile9aa15.ts.net` (PC 켜져 있을 때)

## 0.10 실전 가동 + 인프라 안정화 (2026-04-23)

### 완료
- `EXECUTION_MODE=upbit_live` 전환 완료 (VM, go_live_ready: True)
- SQLite WAL 모드 + busy_timeout 30초 설정 → dashboard/loop DB 충돌 해소
- systemd trading-loop에 `PYTHONUNBUFFERED=1` 추가 → 실시간 로그 정상 출력
- 전체 UI 한글화 (main.py 임베디드 + React 컴포넌트 5개 + recommendation_engine)
- 구 트레이딩봇 자동 실행 제거 (TradingBot.lnk 스타트업 삭제, port 8000/5173 종료)
- VM GitHub auto-pull cron 등록: `*/5 * * * * /home/ubuntu/auto_pull.sh`
  - 변경 감지 시에만 서비스 재시작, 로그: `/home/ubuntu/auto_pull.log`

### VM crontab 현재 상태
```
0 15 * * * /home/ubuntu/go_live.sh >> /home/ubuntu/go_live.log 2>&1
*/5 * * * * /home/ubuntu/auto_pull.sh
```

## 0.11 Crypto pilot signal 추적 + arming 알림 (2026-04-23)

### 완료
- crypto signal trend 저널 기록 (orchestrator: crypto_signal, crypto_trigger, crypto_action)
- Signal Trend 패널 React 대시보드에 추가 (App.jsx)
- main.py: `_crypto_live_lane_snapshot`, `_crypto_live_lane_history` 함수 추가
- `trigger_state` 계산: waiting (distance>0.08) / arming (≤0.08) / ready (≥trigger)
- **Telegram 사전 알림 추가** (a8bccd1):
  - `arming` 진입 시: "signal approaching trigger" 알림 (cooldown 2h)
  - `ready` 진입 시: "pilot READY" 알림 (cooldown 30m)
  - `notifier.send_crypto_pilot_alert()` / `orchestrator._crypto_pilot_lane()` 추가

### 현재 시그널 상태
- `crypto_signal`: 0.35 / `trigger`: 0.56 / `distance`: 0.21 / `trigger_state`: waiting
- 다음 관전 포인트: signal 0.48 도달 → arming 알림 → 0.56 → ready → tiny live order

## 0.12 모바일 UI 개선 (2026-04-23)

### 완료 (1cd34dc)
- React (index.css): btn min-height 44px 복원 (768px에서 40px로 잘못 설정됨)
- React: stat-label/priority-chip/panel-title 폰트 최솟값 11px 적용
- React: hero-title-row 560px에서 스택 (520px → 560px)
- React: 520px에서 btn min-height 44px 명시 유지
- 임베디드 대시보드: 560px btn 44px, pilot-card 패딩/폰트 조정

## 0.13 임베디드 대시보드 개편 + 시간대 수정 (2026-04-23)

### 완료
- 임베디드 대시보드(`:8080`) 전면 개편: 트레이딩 앱 스타일
  - P&L 히어로 (오늘 실현/미실현/승률/실전자본) 최상단 배치
  - 코인 파일럿 시그널 게이지 (progress bar, arming/ready 색상)
  - 한국주식·미국주식 데스크 카드에 품질 게이지 추가 (quality_score vs 진입 임계값)
  - 데스크 액션명 한국어 번역 (`watchlist_only`→관찰 대기, `pre_market_watch`→장 외 대기 등)
  - 브로커·준비도 섹션 접기 가능 (기본 숨김)
- `recommendation_engine.py`: Korea/US plan 모든 반환값에 `quality_score`, `avg_signal`, `quality_threshold` 추가
- 시간 표시 전면 KST 수정 (UTC 저장 유지, 표시만 변환)
  - embedded dashboard JS: `toKST()` 헬퍼, 업데이트 시각/진입 시각/청산 시각
  - React App.jsx: `toKST()` 헬퍼, `next_run` 시각
  - Python: `_to_kst_hhmm()`, equity curve label, crypto lane history `time` 필드
- `/diagnostics/kis-live-pilot` 엔드포인트 추가 (Upbit pilot과 동일한 구조)

### KIS 실전 전환 준비 상태
- 코드 scaffold 완성 (place_order, get_account_positions, token/hashkey)
- 진단 엔드포인트: `/diagnostics/kis-live-pilot`, `/diagnostics/broker-live-health`
- **남은 사용자 작업**: Oracle VM `.env`에 KIS 자격증명 등록 후 KIS_ALLOW_LIVE=true

## 0.14 AI 에이전트 판단 이력 대시보드 (2026-04-24)

### 완료 (2e6e58c)
- **핵심 문제 해결**: 봇이 실시간으로 판단하고 있지만 대시보드에서 전혀 보이지 않는 문제
- `main.py`: `_build_agent_log()` 함수 추가
  - `state.recent_journal` → per-cycle, per-desk 판단 이력 포맷
  - 각 사이클: 스탠스, 국면, 데스크별 action/symbol/size/status/차단 사유
  - `agent_log`를 `_build_dashboard_payload`에 추가
  - `load_closed_positions(limit=8)` → `limit=20`으로 상향
- 임베디드 대시보드 (`:8080/`):
  - "AI 에이전트 판단 이력" 섹션 추가 (데스크 카드 아래)
  - 최근 8사이클 표시, 최신 사이클 파란 테두리 강조
  - `코인`/`한국`/`미국` 태그 + 액션명 (진입 시도했으나 차단 → 노란색, 실제 진입 → 녹색)
  - 차단 사유 note 2개까지 표시
  - 청산 내역 6건 → 15건
  - `toKSTFull()` JS 헬퍼 추가
- React 프론트엔드 (`App.jsx`):
  - `agentLog = dashboard?.agent_log` 추출
  - symbol-edge-panel 아래, stat-row 위에 AI 판단 이력 패널 삽입
  - 6사이클 x (데스크별 row: 태그/액션/심볼/사이즈/note)
  - `formatKstDateTime()` 임포트 추가
- `index.css`: agent-log-panel, agent-cycle, agent-desk-row 스타일 추가

### 항목별 입력 threshold 완화 (528cd71 — 이전 세션)
- Korea: single-gap tier 추가 (gap≥1, quality≥0.65, 0.20x size)
- Korea: mid-session probe tier 추가 (gap≥1, quality≥0.70, 0.15x)
- US: stand_by 기준 완화 (quality 0.72→0.62, signal 0.62→0.52, count 3→2)
- US: 2-leader fallback tier 추가 (0.10x probe)

## 0.15 전략 전면 재설계 — 백테스트 검증 + 파라미터 이식 (2026-04-24)

### 배경
- 기존 타깃(0.65~0.9%)이 왕복 비용(0.13~0.20%) 대비 너무 작아 수익 구조 불가
- `quick_win_floor = target × 0.45` 로직이 승자를 조기 청산 → R:R 파괴
- 목적 재정의: **오토 트레이딩의 실시간 판단 및 대응을 통한 수익의 극대화**

### 백테스트 결과 (Desktop/backtest/)

| 파일 | 전략 | 주요 결과 |
|---|---|---|
| `coin_backtest_v5.py` | 60분봉 변동성 브레이크아웃 (20일 신고가 + 거래량 3x + RSI 55-78) | ✅ XRP: 52.9% 승률 / R:R 2.02 / 샤프 5.47 |
| `stock_backtest_v3.py` | 일봉 모멘텀 브레이크아웃 (20일 신고가 + 거래량 2.5x + RSI 55-78) | ✅ 20종목 포트 합산: 59.4% 승률 / 샤프 12.68 / 연 +33.61% |

### 파라미터 이식 (이번 세션 커밋)

**`app/core/state_store.py`**
- `_position_thresholds` 전면 개정:
  - crypto: +**4.0%** 타깃 / -2.0% 손절 / max 720 사이클 (24h) — 모든 action 통일
  - korea: +**4.0%** 타깃 / -2.5% 손절 / max 195 사이클 (1 KRX 세션) — attack/probe/selective 통일
  - us: +**4.0%** probe_longs / -2.0% / 200 사이클
- `quick_win_floor` 제거 (Codex가 이전 세션에 완료)
- `early_failure_pct`: `stop × 0.6` → `stop × 0.7`
- `stale_floor_pct`: `target × 0.25` → `target × 0.15`
- `fast_fail_cycle`: 기존 1~2 → crypto 30 / korea 20~30 / us 20 사이클

**`app/agents/execution_agent.py`**
- `_desk_limits`: crypto (1, 0.6x) → **(2, 1.2x)** (동시 2 포지션 허용)
- `_desk_recovery_ready`: `last_two_realized >= 0.35` → **1.5%** (4% 타깃 스케일)
- `_desk_loss_pressure`: crypto `-1.0%` → **-4.0%** (2 × -2% 손절)
- `_desk_chronic_drawdown`: crypto `-1.6%` → **-6.0%** (3 × -2% 손절)
- `_desk_performance_lock`: crypto `-1.5%` → **-6.0%**
- `_desk_stop_pressure` high: `-3.0%` → **-6.0%**, medium: `-1.5%` → **-3.0%**
- `_symbol_stop_pressure` high: `-1.8%` → **-4.0%**, medium: `-0.8%` → **-2.0%**
- `_extended_symbol_block`: `-2.0%` → **-5.0%**
- `_expected_pnl_pct`: korea 2.2~3.0% → **4.0%** 통일

**`app/services/recommendation_engine.py`** (Codex 완료)
- crypto offense_threshold: 0.74/0.70 → **0.68/0.64**
- crypto 사이즈: 0.50x → **0.65x** (BALANCED), 0.85x (OFFENSE)
- korea: quality threshold 0.72 → **0.56**, avg_volume 20000 → **8000**
- korea: attack_opening_drive 조건 active_gap_count 3 → **2**
- korea: selective_probe 조건 대폭 완화

### 다음 작업 우선순위

1. **Oracle VM pull + 서비스 재시작** — 새 파라미터가 실전에서 작동하는지 확인
2. **대시보드 모니터링** — 크립토 진입 빈도 증가 확인, Korea 데스크 활성화 확인
3. **첫 swing 거래 체결 확인** — +4% 타깃까지 보유 vs 조기 청산 여부
4. **KIS 실전 전환** — Oracle VM `.env` KIS 자격증명 등록

## 0.16 브레이크아웃 신호 엔진 + Korea 데스크 이중 경로 (2026-04-24)

### 완료

**`app/services/signal_engine.py`**
- `summarize_breakout_signal()` 신규 함수 추가:
  - 20-period 신고가 돌파 (`close > max(prior N closes)`)
  - 거래량 서지 (`current_vol / avg_vol >= 2.5x`)
  - RSI 모멘텀 구간 체크 (`rsi_min <= RSI <= rsi_max`)
  - EMA(N) 위 필터 (`close > ema20`)
  - `confirmed_count` 0-4, `breakout_score`: 4=0.90 / 3=0.70 / 2=0.45 / 1=0.20
  - 어느 타임프레임에나 작동 (15분봉 크립토 / 일봉 한국주식)
- `summarize_crypto_signal()` 브레이크아웃 오버레이 추가:
  - 기존 EMA10/30 + RSI 스코어링에 브레이크아웃 신호 가산 (+0.15 / +0.08 / +0.03)
  - 반환값에 `breakout_confirmed`, `breakout_partial`, `breakout_count`, `vol_ratio`, `breakout_score` 추가

**`app/agents/korea_stock_desk_agent.py`**
- `KOREA_BREAKOUT_WATCHLIST` (20종목) 추가: stock_backtest_v3 동일 유니버스
  - 코스닥: 에코프로비엠, 알테오젠, HLB, 리가켐바이오, 삼천당제약, 클래시스, 레인보우로보틱스, 에코프로, 셀트리온, 카카오게임즈
  - 코스피: 삼성전자, SK하이닉스, 현대차, 카카오, POSCO홀딩스, LG에너지솔루션, 삼성SDI, 크래프톤, 네이버, 두산에너빌리티
- **Path A** (기존): KOSDAQ 모버 갭업 스캔 (gap_pct 1.2-12%)
- **Path B** (신규): 워치리스트 전종목 일봉 42개 로드 → `summarize_breakout_signal()` → confirmed_count ≥ 2인 경우 후보 추가
- 두 경로 결과 병합 → `candidate_score` 기준 정렬
- payload에 `breakout_confirmed_count`, `breakout_partial_count` 추가

**`app/services/recommendation_engine.py`**
- `build_korea_plan()` 브레이크아웃 경로 추가:
  - 갭리더 과열 상태에서도 breakout_confirmed ≥1이면 `probe_longs 0.35x`
  - **브레이크아웃 전용 경로** (갭없이도 진입):
    - `breakout_confirmed_count >= 1` → `probe_longs 0.55x/0.40x`
    - `breakout_partial_count >= 1` → `selective_probe 0.30x`
  - 이 경로는 opening_window/mid_session 무관하게 트리거 (24시간 모멘텀)

### 검증
- `python -c "from app.services.signal_engine import summarize_breakout_signal"` → OK
- `python -c "from app.agents.korea_stock_desk_agent import KoreaStockDeskAgent"` → OK (20 tickers)
- 단위 테스트: breakout+vol_surge 케이스 → confirmed 3/4, score 0.70 ✓

### 다음 작업 우선순위

1. **Oracle VM auto_pull** — 5분 내 자동 반영 (수동 확인 불필요)
2. **대시보드에서 Korea 브레이크아웃 후보 확인** — 데스크 카드 클릭 → "후보 종목" 섹션에 BK 뱃지 등장 여부
3. **KIS 실전 전환** — Oracle VM `.env` KIS 자격증명 등록 후 `KIS_ALLOW_LIVE=true`

## 0.17 브레이크아웃 신호 대시보드 표시 (2026-04-24)

### 완료

**`app/main.py`**
- `_build_desk_drilldown_payload()`:
  - Korea candidate_details에 `is_breakout`, `breakout_count`, `vol_ratio`, `rsi` 추가
  - Crypto candidate_details에 `breakout_confirmed`, `breakout_count`, `vol_ratio`, `rsi` 추가 (candidate_markets 맵에서 조회)
  - 드릴다운 payload에 `breakout_confirmed_count`, `breakout_partial_count` 추가
- `_build_desk_status()`: Korea 항목에 `breakout_confirmed_count`, `breakout_partial_count` 추가
- `loadData()` JS: `window.__deskDrilldown = dash.desk_drilldown` — 기존에 빈 `{}` 고정이었음 → 수정
- `renderDeskDetail()` JS: "후보 종목" 섹션 추가 (BK 뱃지, 갭%, 거래량 배율, RSI 표시)
- `renderDesks()` JS: Korea/Crypto 카드에 BK 뱃지 표시 (confirmed=녹색, partial=노란)
- CSS: `.desk-bk-badge`, `.bk-badge`, `.bk-chip`, `.cand-row` 스타일 추가
- `kis_live_pilot()`: `korea_signal_ready` 조건에 `probe_longs` 추가 (브레이크아웃 경로)

**`frontend/src/App.jsx`**
- 후보 종목 행에 `BK N/4` 뱃지 (confirmed=녹색/partial=노란)
- `vol_ratio` (≥1.5x), `rsi` 메트릭 추가 표시

**`frontend/src/index.css`**
- `.bk-tag.full`, `.bk-tag.partial` 스타일 추가

### 다음 작업 우선순위

1. **KIS 실전 전환** — Oracle VM `.env`에 KIS 자격증명 등록:
   ```
   KIS_APP_KEY=...
   KIS_APP_SECRET=...
   KIS_ACCOUNT_NO=...  (예: 12345678-01)
   KIS_PRODUCT_CODE=01
   KIS_ALLOW_LIVE=true
   ```
   등록 후: `sudo systemctl restart trading-loop trading-dashboard`
2. **첫 브레이크아웃 신호 확인** — 한국 장 중 대시보드에서 BK 뱃지 확인
3. **자본 스케일업** — 첫 swing +4% 완료 후 `UPBIT_PILOT_MAX_KRW` 증액, `UPBIT_PILOT_SINGLE_ORDER_ONLY=false`

## 8. Suggested next work (2026-04-29 기준)

### 완료된 항목 (이번 Claude 세션)
- ✅ `backtest/walk_forward.py` — 워크포워드 백테스트 인프라 (Phase 1 완료)
- ✅ `/scanner` 페이지 + `/scanner-data` API — DartLab 스타일 18코인 스캐너 UI
- ✅ `crypto_desk_agent.py` — `all_candidates` 전체 18코인 뷰 저장
- ✅ 대시보드 상단 "📡 스캐너" 버튼 추가
- ✅ HANDOFF.md 섹션 22 추가 (fast_fail time-based + threshold 내용 포함)

### Codex 다음 작업 우선순위

**A (권장 1순위): 텔레그램 거래 일지 강화**
- 이미 텔레그램 봇 연결됨 → 추가 계정 세팅 없이 바로 가능
- 진입 시: 심볼/사이즈/가격/진입 사유(action path)/combined_score/signal_score 전송
- 청산 시: 청산가/PnL%/청산 사유/보유시간/peak_pnl 전송
- 관련 파일: `app/services/notifier.py`, `app/core/state_store.py` (`_close_position`)

**B (2순위): 성과 분석 페이지 강화**
- `/performance` 엔드포인트 이미 존재 (line ~2319 in main.py)
- 추가할 것: 시간대별 히트맵, 진입 사유별 승률, PnL 분포, 연속 손익 스트릭
- 데이터: `cycle_journal`, `closed_positions` SQLite 테이블 활용

**C (3순위): Scanner 가격 + 스파크라인**
- `/scanner` 페이지에 현재가 컬럼 추가 (upbit ticker REST or WebSocket cache)
- 15m 미니 캔들 스파크라인 (SVG inline, 최근 8봉)
- `/scanner-data` API에 `current_price`, `candles_15m_mini` 필드 추가

**D (4순위): walk-forward 실행 → 파라미터 재검토**
- `cd C:\Users\User\Desktop\backtest && python walk_forward.py` 실행 (약 1-2시간)
- `walk_forward_result.json` 확인 → 오버핏 여부 및 권장 파라미터 적용

### 나중에 할 것 (준비 필요)
- Priority 4: Slack/Notion 거래 일지 (계정 세팅 후)
- KIS 한국주식 실전 (Oracle VM `.env` KIS 자격증명 등록 후)
- Binance Futures 연동 → LONG+SHORT 양방향 (Phoenix 봇 스타일)

## 9. Useful commands

From `C:\Users\User\Desktop\trading-bot\trading_company_v2`:

- backend compile check:
  - `.\.venv\Scripts\python.exe -m compileall app`
- start services:
  - `start_trading_services.bat`
- open local dashboard:
  - `open_dashboard.bat`
- inspect access routes:
  - `/health`
  - `/diagnostics/access-map`

From `C:\Users\User\Desktop\trading-bot\frontend`:

- frontend build:
  - `npm run build`

## 10. Warning on git state

- The repo is not perfectly clean.
- There are existing modified / untracked files including `.claude` worktrees, logs, and Oracle SSH key directory.
- Do not blindly revert unrelated changes.
- Read before editing when touching files with existing local diffs.

## 11. Oracle Alignment Note

- Oracle VM `.env` has confirmed live Upbit values:
  - `UPBIT_ACCESS_KEY` set
  - `UPBIT_SECRET_KEY` set
  - `UPBIT_ALLOW_LIVE=true`
  - `LIVE_CAPITAL_KRW=2000000`
  - `EXECUTION_MODE=upbit_live`
- Local PC `.env` has been aligned to match those Upbit values for consistency checks.
- Local DB state has also been updated so `execution_mode=upbit_live`.
- Local services were restarted and now run normally again.
- Local readiness is still only `caution`, not `ready`, because:
  - Upbit balance check returns `401 Unauthorized`
  - entry gate is still blocked by defensive risk state
- Practical interpretation:
  - Oracle VM is still the canonical live host
  - local PC is config-aligned but not yet confirmed as a safe live trading host
- Frontend policy remains source-first:
  - commit `frontend/src/*`
  - do not rely on checked-in `frontend/dist/*` unless a deployment path explicitly requires it

## 12. Strategy Redesign Status (2026-04-24)

- Project name:
  - `Coin & Korea Profit Maximization Project`
- Direction remains unchanged from Claude handoff:
  - maximize profit, not minimize activity
  - define alpha first, then validate, then execute, then risk-manage
  - crypto + Korea first, with volatile short-term swing priority
- Immediate profit-limiting logic has now been corrected in code:
  - removed `quick_win_floor` early winner cut from `app/core/state_store.py`
  - expanded paper target / stop / hold windows to match swing-style trades
  - aligned execution expected PnL with backtest-scale targets:
    - crypto `probe_longs`: `4.0%`
    - korea `attack_opening_drive`: `3.0%`
- Recommendation thresholds were shifted away from over-defensive gating:
  - crypto breakout entry thresholds lowered toward validated DOGE/XRP regime
  - Korea opening-drive thresholds relaxed so the desk can actually express candidates
- Backtest environment update:
  - `pykrx` is usable on current Python 3.14 environment
  - actual blocker was console encoding, not `pkg_resources`
  - both backtest scripts now force UTF-8 stdout to avoid cp949 crashes

### Current backtest readout

- Crypto:
  - validated leaders remain `KRW-DOGE` and `KRW-XRP`
  - `app/services/backtest_advisor.py` now reads `coin_result_v5.json` first
  - live emphasis weights now resolve to:
    - `KRW-DOGE: 0.5181`
    - `KRW-XRP: 0.4819`
  - weak spot remains excessive stop-outs after breakout entry
- Korea:
  - data collection now works with real `pykrx`
  - repository-local research script added:
    - `research/korea_opening_drive_research.py`
  - widened research universe: `30` curated KRX names
  - strongest current daily-bar approximation band:
    - `gap_min_pct=1.2%`
    - `gap_max_pct=12.0%`
    - `vol_mult=1.6`
    - `drive_min_pct=1.0%`
    - `tp1=3%`
    - `tp2=5%`
    - `stop=1.5%`
  - important caution:
    - this Korea result is based on daily OHLC approximation, so absolute return metrics are optimistic
    - use it for trigger-band discovery, not for direct production expectancy
  - live Korea scanner has been widened to better match the research band:
    - `get_kosdaq_snapshot(top_n=30)`
    - live gap window now favors `1.2% ~ 12.0%`
    - Korea desk ranking/scoring thresholds eased accordingly

### Next recommended work

1. Rework Korea stock backtest universe and trigger definition until trade count is statistically usable.
2. Refine crypto breakout entry to reduce stop-hit frequency without killing DOGE/XRP expectancy.
3. After both are validated, transplant the winning rules into `recommendation_engine.py` and `execution_agent.py` more completely.

## 0. Latest Claude Notes - 2026-04-28 (2nd session)

### 전략 분석 → 5가지 개선 구현 (commit ebd61b6)

**문제 진단**: 봇이 EMA 갭이 벌어진 상태(추세 확립 후)에 진입 → 정상 되돌림 -1.2%에 손절 반복.
Ross Cameron, Raschke Holy Grail, Minervini VCP 등 세계 최고 단기 트레이더 공통 원칙:
**"스파이크 확인 → 거래량 줄며 눌림 → EMA 근처 되돌림 완료 시 진입"**

#### 변경 1: detect_pullback_entry() (signal_engine.py)
- 최근 8봉 중 1%+ 스파이크 감지 → 현재 가격이 EMA10 근처(-1~+2.5%)로 되돌림
- 눌림 구간 거래량 < 스파이크 거래량의 65% → vol_contracted_on_pullback=True
- pullback_score 0~1 반환 (0.60+ 시 진입 허용)

#### 변경 2: 거래량 게이트 (recommendation_engine.py)
- 돌파형 진입: vol_ratio < 1.4x AND micro_vol < 1.5x AND pullback/ICT 없음 → watchlist_only
- 되돌림 진입은 현재 거래량이 낮아도 허용 (스파이크 후 정상 수축)

#### 변경 3: 되돌림 진입 경로 (recommendation_engine.py)
- pullback_score ≥ 0.60 + signal ≥ 0.44 + micro ≥ 0.46 + orderbook ≥ 0.50 → probe_longs 0.65x/0.50x
- 기존 ignition_ready보다 완화된 조건이지만 더 좋은 진입 가격

#### 변경 4: 트레일링 타이트화 (state_store.py)
- peak ≥ 1.5% → 0.5% 되돌리면 청산 (신규 티어)
- peak ≥ 2.2% → 0.7% 되돌리면 청산 (기존 1.0%)
- peak ≥ 4.0% → 1.0% 되돌리면 청산 (기존 1.4%)
- fast_fail: 8사이클(16분) @-0.65% → 12사이클(24분) @-0.80%

#### 변경 5: 동시 포지션 집중화 (execution_agent.py)
- 크립토 최대 동시 포지션: 4개 → 2개 (2.4x → 1.2x 캡)
- 3~4위 신호에 자본 분산하지 말고 최우선 2개 신호에 집중

#### CryptoDeskAgent combined_score 가중치 재조정
- 15m signal: 50% → 38%
- 1m micro: 21% → 26%
- orderbook: 8% → 18% (가장 실시간 신호 → 비중 2배 이상)
- BTC direction: 15% → 12%
- backtest weight: 6% 유지

#### 기대효과 vs 현재
| 항목 | 현재 | 개선 후 |
|---|---|---|
| 진입 타이밍 | EMA 갭 벌어진 후(고가) | EMA 눌림목(저가) |
| 손절 빈도 | 노이즈 손절 多 | 의미 있는 레벨 기반 |
| peak 2.5% 포지션 청산 기준 | 2.5%-1.0%=1.5% | 2.5%-0.7%=1.8% |
| 분산 | 4코인 동시 | 2코인 집중 |

---

## 13. TradingAgents-Inspired Decision Layer (2026-04-28)

- Added a lightweight debate layer based on TauricResearch/TradingAgents concepts, without adding external LLM calls or changing the dashboard layout:
  - `BullCaseAgent`: scores each desk's upside case from momentum, volatility expansion, liquidity, orderbook/micro confirmation, and setup quality.
  - `BearCaseAgent`: scores each desk's downside case from late-chase risk, RSI/EMA overheat, weak confirmation, drawdown pressure, and gross exposure.
  - `PortfolioManagerAgent`: compares bull vs bear scores before execution, then approves, presses, throttles, cuts, or blocks planned entries.
- The layer runs after recommendation plans and compounding overlays, but before `ExecutionAgent`.
- It stores the full decision review under `strategy_book["decision_debate"]` and adds portfolio-manager notes into state notes, so Claude/Codex can audit why sizing changed.
- This is intentionally a decision-quality layer, not another hard safety gate:
  - strong clean setups can get a small size increase
  - mixed setups are throttled
  - severe bear cases are blocked or downgraded

## 14. Vibe-Investing Benchmark Strategy Update (2026-04-28)

- Borrowed the `vibe-investing` quant-research principle: keep validated edges, but do not confuse old in-sample backtest weights with live opportunity discovery.
- Crypto plan now has two support tracks:
  - `validated_support`: known backtest-backed symbols still get priority.
  - `discovery_support`: full Upbit KRW universe leaders can enter when discovery score, liquidity, micro momentum, and orderbook confirmation are strong.
- This fixes the previous bottleneck where full-universe scanning found strong coins, but `lead_weight == 0` forced most new candidates into watch-only mode.
- Recovery-mode targets were tightened to build positive samples before pressing size:
  - crypto paper threshold: `+4.5% / -2.2%`
  - Korea paper threshold: `+3.8% / -2.0%`
- Intent:
  - increase trade opportunity without blind overtrading
  - preserve our bot strengths: live scanning, debate layer, Oracle uptime, dashboard visibility
  - reduce the current 0-win sample problem by taking reachable wins first, then compounding through position sizing

## 15. Crypto-Only Trend Engine Pivot (2026-04-28)

- Project direction changed to crypto-first validation:
  - default `ACTIVE_DESKS=crypto`
  - Korea/U.S. desks stay configured for later, but are excluded from execution and hidden from the main dashboard by default
  - this prevents stock paper positions and stock desk logic from blocking crypto entries through gross exposure/risk gates
- Crypto entry logic now prioritizes trend ignition:
  - combines swing signal, 1m micro momentum, orderbook flow, full-universe discovery, and breakout volume
  - RSI is treated as momentum context instead of an automatic sell/avoid signal
  - hard overheat still blocks weak chases, but strong micro/orderbook ignition can still enter with controlled size
- Crypto exits now use trend-following protection:
  - initial stop tightened to `-1.2%`
  - target raised to `+8%`
  - failed ignitions can exit quickly after ~16 minutes
  - profitable positions track `peak_pnl_pct` and close via `breakeven_trail` / `trend_trail` when momentum gives back
- Risk/debate/execution gates now calculate loss pressure and exposure using active desks only, so disabled stock desks do not suppress crypto testing.

## 16. Phase 1 Realism Patch - Fees, Slippage, ATR Sizing (2026-04-28)

- Added paper-trading cost realism for crypto:
  - entry fill price includes adverse slippage
  - open/closed P&L includes estimated exit slippage
  - round-trip Upbit-style fee is deducted from paper P&L
  - defaults: `PAPER_FEE_BPS=5`, `PAPER_SLIPPAGE_MIN_BPS=5`, `PAPER_SLIPPAGE_MAX_BPS=15`
- Added ATR-based volatility sizing:
  - new `app/services/atr_sizing.py`
  - crypto desk calculates ATR% from the same 15m candles used for signal generation
  - execution scales crypto notional down for high/extreme ATR and slightly up for clean quiet volatility
- Intent:
  - stop paper P&L from looking better than realistic live execution
  - avoid equal sizing across low-vol and high-vol coins
  - make future strategy changes judgeable on net expectancy, not gross price movement

## 17. Phase 1 Edge Quality Patch - Freshness + BTC Correlation Cap (2026-04-28)

- Added signal freshness scoring to the crypto desk:
  - each candidate now records latest 1m candle age, freshness factor, and freshness reason
  - stale 1m data reduces combined score instead of being treated like a current signal
  - execution blocks entries when freshness collapses to stale territory
- Added 15m BTC correlation measurement:
  - each candidate now carries `btc_corr_15m`
  - execution caps high-BTC-beta crypto crowding with `CRYPTO_HIGH_CORR_THRESHOLD=0.85`
  - default max high-correlation crypto positions: `CRYPTO_HIGH_CORR_MAX_POSITIONS=2`
- Changed crypto-only drawdown behavior:
  - previous global rule blocked all new entries below `-1.5%`
  - crypto-only mode now keeps entries open until `-6.0%`, relying on throttled risk/ATR/correlation controls instead of going fully idle
  - both pre-execution orchestration and final RiskCommittee state now use the same crypto recovery floor
  - crypto desk loss pressure no longer fully pauses entries in crypto-only mode; it throttles size while continuing to gather live samples
- Intent:
  - reduce late-chase entries after the move has already aged
  - stop four alt positions from behaving like one oversized BTC-beta bet
  - preserve active full-universe scanning while forcing better diversification of live opportunities

## 18. Failed-Ignition Reduction Patch - Late Chase Guard (2026-04-28)

- Added 1m exhaustion metadata to the crypto micro signal:
  - `micro_exhausted`
  - `micro_move_10_pct`
  - `micro_range_5_pct`
  - `micro_vwap_gap_pct`
- Crypto recommendation now distinguishes:
  - clean momentum ignition: controlled 1m move, volume support, orderbook support
  - late chase: 1m/10m move already stretched, VWAP gap wide, or 5-bar range too large
- Late chase entries are blocked unless:
  - a valid pullback entry is present, or
  - a very strong live breakout exception exists (`micro_ready`, high combined score, strong orderbook)
- Intent:
  - reduce `failed_ignition` losses caused by buying the end of the first candle burst
  - preserve the high-return trend strategy by waiting for the first pullback/reclaim instead of reverting to low-risk inactivity

## 19. Fast Reaction Runtime Patch - Crypto Rapid Guard (2026-04-28)

- Added crypto-only fast runtime controls:
  - `CRYPTO_FAST_CYCLE_SECONDS=8`
  - `CRYPTO_RAPID_GUARD_SECONDS=3`
- In crypto-only mode, the full strategy loop now targets an 8-second sleep interval instead of the old 45-second watch interval.
- Added a rapid price-only guard between full strategy cycles:
  - watches only currently open crypto symbols
  - fetches lightweight Upbit ticker prices
  - updates open P&L
  - can close on target, hard stop, breakeven trail, or trend trail without waiting for the next full scan
- This is still not true HFT/arbitrage infrastructure:
  - REST ticker polling + Python + Oracle VM is not exchange-colocated execution
  - the next step for real arbitrage-like reaction speed is a persistent Upbit websocket/tick collector and event-driven execution path
- Intent:
  - keep the full-universe strategy scan open
  - make open-position risk response much faster
  - prevent profitable spikes or sudden reversals from waiting on a slow full cycle

## 20. Upbit WebSocket Ticker Cache Patch (2026-04-28)

- Added a persistent Upbit ticker stream cache:
  - new `app/services/upbit_stream_cache.py`
  - default public stream: `wss://api.upbit.com/websocket/v1`
  - subscribes to the KRW universe up to `UPBIT_WS_CODES_LIMIT=220`
  - stores latest `trade_price`, 24h KRW volume, change rate, exchange timestamp, and local receive time
- Market data now uses websocket cache first:
  - `get_upbit_ticker_prices()` reads fresh stream prices first, then REST only for missing symbols
  - `get_krw_crypto_candidates()` can rank candidates directly from the live stream cache when enough fresh rows exist
  - `get_top_krw_coins()` also uses the stream cache when populated
- Runtime starts the stream automatically in crypto-only mode when `UPBIT_WS_ENABLED=true`.
- New environment controls:
  - `UPBIT_WS_ENABLED=true`
  - `UPBIT_WS_FRESH_SECONDS=6`
  - `UPBIT_WS_CODES_LIMIT=220`
- Intent:
  - reduce REST polling latency for price updates
  - make rapid guard and candidate discovery react closer to tick speed
  - prepare the next event-driven entry/exit service without discarding the current multi-agent strategy stack

## 22. Walk-Forward Backtest Infrastructure (2026-04-29)

### 완료

**`backtest/walk_forward.py`** (신규 파일)
- Phase 1 마지막 항목: 워크포워드 백테스트 인프라 구축
- **구조**: 3개월 학습 윈도우 → 1주 OOS 테스트 → 1주 슬라이드 반복
- **그리드서치**: `vol_surge_mult`[2.0~4.0] × `breakout_period`[12~25] × `rsi_min`[48~58] × `rsi_max`[72~80] = 400 파라미터 조합
- **오버핏 감지**: train_sharpe / oos_sharpe 비율 계산 (≥2.5x = 🔴 강한 오버핏, ≥1.5x = 🟡 주의, <1.5x = 🟢 안전)
- **파라미터 안정성**: 각 윈도우별 최적 파라미터 분포 분석 → 현재 프로덕션 파라미터가 안정 범위 내인지 확인
- **프로덕션 비교**: 현재 CONFIG 파라미터(vol_surge=3.0, breakout=20, rsi=55~78)의 OOS 성과 별도 추적
- **통과 기준 (OOS)**: Sharpe ≥ 0.3 + PnL > 0 + MaxDD > -25%
- **출력**: 윈도우별 상세 결과 + 전체 요약 + 파라미터 추천 + `walk_forward_result.json`

#### 사용법
```bash
cd C:\Users\User\Desktop\backtest
python walk_forward.py
# 또는 단일 코인 테스트 (약 20-30분 소요 per coin)
```

#### 해석 가이드
- OOS 통과율 ≥ 60%: 전략 실전 투입 가능
- 오버핏 비율 ≥ 2.5x: 파라미터 범위 좁히기 (단순화)
- 권장값 ≠ 프로덕션: coin_backtest_v5.py CONFIG 업데이트 후 재검증
- 불안정 코인 (OOS < 40%): 유니버스 제외 고려

### 2026-04-29 세션 Priority 1 작업 (commit 986a4ab — 전 세션 완료)

#### fast_fail 시간 기반 전환 (state_store.py)
- 문제: Codex가 8초 빠른 사이클(CRYPTO_FAST_CYCLE_SECONDS=8) 도입 → 기존 `fast_fail_cycle=12`가
  단 96초만에 발동 (원래 의도: 24분)
- SPK 실제 사례: 29사이클 = 9.5분 → `fast_fail_cycle=12` = 겨우 4분
- **수정**: `opened_at` datetime 기반 시간 계산 → `minutes_open >= 24.0`
- **Triple gate**: minutes_open ≥ 24.0 AND pnl_pct ≤ -0.80% AND peak_pnl ≤ 0.10%

#### 약세 신호 진입 임계값 상향 (recommendation_engine.py)
- ADA/AVAX/ETH: peak_pnl=0.0%, 즉시 음수 → 실패 원인 = 낮은 임계값에서의 진입
- discovery_entry_ok: signal 0.52→0.56, micro 0.44→0.46, ob 0.98→1.0
- stream_entry_ok: signal 0.52→0.58
- offense_fallback: signal max(0.49,0.48)→max(0.54,0.54)
- micro_entry_ok solo: signal 0.48→0.55
- balanced pilot_probe: 0.48/0.52→0.54/0.58
- **기대효과**: 거래 빈도 14→8-9/24h, failed_ignition rate 50%→<25%

## 21. Sub-Minute Stream Ignition Patch (2026-04-28)

- The Upbit ticker stream now keeps a short rolling tick history per market.
- Added `summarize_stream_momentum()`:
  - calculates 5s / 15s / 60s price movement
  - tracks 15s tick activity
  - estimates short-window buy pressure from `ask_bid`
  - emits `stream_score`, `stream_ignition`, and `stream_reversal`
- Crypto desk now includes stream momentum in candidate ranking and payload metadata.
- Crypto recommendation now:
  - boosts trend ignition when the live stream confirms acceleration
  - allows controlled `tick ignition` selective probes when the 15s stream move is fresh and supported
  - blocks new long entries on fresh stream reversal
- Intent:
  - detect fast entries before a full 1m candle closes
  - keep late-chase protection intact
  - move closer to arbitrage-style reaction speed while still using strategy confirmation and risk gates

## 23. Telegram Trade Journal Patch (2026-04-29)

### Completed

- `app/notifier.py`
  - Added `send_trade_entry(position)`.
  - Added `send_trade_exit(position, exit_reason)`.
  - Entry alert includes symbol, KRW notional, entry price, entry path, Combined/Signal/Micro/OB, Bias, Pullback, Stream, and Focus.
  - Exit alert includes symbol, PnL%, estimated KRW PnL, holding minutes, exit reason, and peak PnL.
  - Trade alerts use keyed duplicate suppression so each position event sends once.
  - Existing error/risk/ops cooldown behavior is unchanged.
- `app/agents/execution_agent.py`
  - Order rationale meta now carries trade-journal scoring fields:
    - `combined_score`
    - `signal_score`
    - `micro_score`
    - `orderbook_score`
    - `orderbook_bid_ask_ratio`
    - `pullback_score`
    - `stream_score`
    - `bias`
    - `entry_path`
- `app/core/state_store.py`
  - `sync_paper_positions()` sends an entry alert after a new paper position is committed.
  - `_close_position()` sends an exit alert when a paper position closes.
  - Telegram sends run in daemon threads so Telegram HTTP latency does not hold DB write locks.

### Operating Note

- Alerts are tied to the `paper_positions` lifecycle because the dashboard and current performance accounting are paper-position centric.
- This gives one entry alert and one exit alert per bot position without double-alerting the parallel live/paper ledgers.
- If live broker fill alerts are needed later, add a separate live-fill journal off `live_order_log` to avoid duplicate Telegram messages for the same trade.

## 24. Performance Analytics Page Patch (2026-04-29)

### Completed

- Added `load_performance_analytics()` in `app/core/state_store.py`.
  - Aggregates closed paper positions into all-time summary, today summary, hourly heatmap, entry-action stats, exit-reason stats, symbol stats, PnL distribution, open positions, and recent closed trades.
  - Uses KST-aware timestamp parsing so the hourly heatmap matches the operator's Korea-time dashboard view.
  - Estimates KRW PnL from `PAPER_CAPITAL_KRW * size_x * pnl_pct`.
- Added `/performance-data`.
  - Returns both existing quick stats and the new analytics payload for future frontend/mobile reuse.
- Upgraded `/performance`.
  - Default route now renders a mobile-responsive HTML performance page.
  - `?format=json` remains available for the previous JSON-style diagnostics payload plus analytics.
- Added a dashboard topbar link to `/performance`.

### Intent

- Make it obvious which entry actions and exit reasons are actually making or losing money.
- Surface weak time windows and PnL distribution quickly so strategy tuning is driven by observed trade outcomes, not guesswork.
- Keep this focused on the current paper-position lifecycle until live fill accounting is unified.

## 25. Scanner Loading + Performance Layout Patch (2026-04-29)

### Completed

- Fixed `/scanner-data`.
  - The scanner endpoint was calling undefined `get_state()`, which returned a server error and left the scanner page stuck on loading.
  - Replaced it with `load_company_state()`.
- Fixed scanner discovery-card JavaScript quoting.
  - Replaced fragile inline quoted `highlightRow('MARKET')` HTML with `data-market` + `highlightRow(this.dataset.market)`.
  - Verified scanner and performance scripts with `node --check`.
- Improved `/performance` layout.
  - Time-of-day heatmap now spans full width and lives inside a horizontal scroll container.
  - PnL distribution is no longer placed beside the 24-hour heatmap.
  - Added `daily_performance` analytics and a daily summary table below the heatmap.

### Intent

- Make the scanner reliably render instead of silently failing after API or JavaScript errors.
- Keep the performance page readable on both desktop and mobile while preserving all 24 hourly cells.
- Add daily trade outcome visibility so strategy changes can be checked day by day.

## 26. Scanner Price + 15m Mini Chart Patch (2026-04-29)

### Completed

- Enhanced `/scanner-data`.
  - Adds live `current_price` / `trade_price` for each scanner candidate using Upbit websocket cache first, REST fallback second.
  - Adds normalized `price_change_pct` so the UI no longer has to guess whether `change_rate` is decimal or percent.
  - Adds `candles_15m`, `sparkline`, and `sparkline_change_pct` for each candidate.
  - Uses a 75-second in-process chart cache and 6-worker parallel fetch so 10-second browser refreshes do not spam candle requests.
- Enhanced `/scanner` UI.
  - Added `현재가` column.
  - Added `15m 차트` column with compact candlestick bars plus a sparkline overlay.
  - Added sorting support for current price and 15m chart change.

### Intent

- Make the scanner show not only scores but also live tradable price context.
- Let the operator visually see whether a high-scoring coin is accelerating, pulling back, or fading before clicking deeper.
- Keep the implementation lightweight until the next step introduces richer per-symbol drilldown charts.

## 27. Crypto PnL Protection Patch (2026-04-29)

### Diagnosis

- Oracle paper-position sample before this patch:
  - 14 closed positions, 2 wins / 12 losses, total PnL `-7.76%`.
  - Crypto losses were dominated by `failed_ignition` and negative `stale_exit`.
  - Two crypto winners reached meaningful peaks but gave most of it back:
    - `KRW-PRL`: peak `+1.06%` -> closed `+0.04%`.
    - `KRW-SPK`: peak `+1.48%` -> closed `+0.05%`.
- Conclusion:
  - The bot was correctly catching some fast moves, but profit protection activated too late.
  - Execution risk scoring did not treat `failed_ignition` as stop-like, so weak ignition patterns were under-penalized.

### Completed

- Added `_crypto_trail_rules()` in `app/core/state_store.py`.
  - Peak `>= +1.0%`: protect via `max(+0.35%, peak - 0.45%)`.
  - Peak `>= +1.8%`: protect via `max(+0.70%, peak - 0.65%)`.
  - Peak `>= +3.0%`: protect via `max(+1.20%, peak - 0.90%)`.
  - Peak `>= +5.0%`: protect via `max(+2.20%, peak - 1.20%)`.
- Applied the same protection rules to:
  - full-cycle `sync_paper_positions()`
  - sub-minute `rapid_guard_crypto_positions()`
- Added `profit_protect` / `rapid_profit_protect` close reasons for smaller fast winners.
- Added `failed_followthrough` close reason:
  - If crypto peak reached at least `+0.65%`, then after 8 minutes falls to `<= -0.15%`, close instead of letting it drift into a larger failed ignition.
- Updated `ExecutionAgent` stop-like classification.
  - Stop-like now includes `stop_hit`, `rapid_stop_hit`, `early_failure`, `failed_ignition`, and `failed_followthrough`.
  - Negative `stale_exit <= -0.5%` is also treated as stop-like for pressure scoring.

### Intent

- Keep high-return trend targets open, but stop giving back early +1% moves to near-breakeven.
- Penalize weak ignition patterns earlier so the bot does not repeatedly enter the same low-quality setup.
- Improve expectancy by reducing average loss and preserving partial wins without moving to futures leverage yet.

## 28. Crypto Chart Trend-Following Gate Patch (2026-04-29)

### Completed

- Added `summarize_trend_following_context()` in `app/services/signal_engine.py`.
  - Uses 15m EMA8 / EMA21 / EMA34 stack, EMA21 slope, price location, higher-high / higher-low structure, and extension risk.
  - Produces:
    - `trend_follow_score`
    - `trend_alignment` (`trend_long`, `pullback_long`, `range`, `downtrend`, `late_extension`)
    - `trend_entry_allowed`
    - `trend_slope_pct`
    - `trend_extension_pct`
    - `trend_reasons`
- `summarize_crypto_signal()` now applies the chart-trend overlay before ICT scoring.
  - Uptrend or first-pullback structure boosts score.
  - Downtrend, range, and late-extension structures reduce score.
- `CryptoDeskAgent` now ranks candidates with explicit chart-trend weight.
  - Combined score is now more trend-following aligned:
    - chart/swing signal
    - trend-follow score
    - 1m micro timing
    - orderbook flow
    - BTC backdrop
    - discovery/backtest weight
  - Candidates carry all trend fields into `/scanner-data`, dashboard payloads, and recommendation planning.
- `build_crypto_plan()` now treats chart trend as the first gate for new long entries.
  - Fast 1m/stream triggers are only allowed when 15m trend is `trend_long` or `pullback_long`.
  - Direct, stream, micro, discovery, ignition, and pullback entries all require `trend_entry_allowed`.
  - If chart trend is `range`, `downtrend`, or `late_extension`, the bot returns `watchlist_only` with an explicit chart-trend reason.

### Intent

- Make the bot a clearer chart trend-following system instead of a loose hybrid momentum scanner.
- Keep fast response speed, but only react aggressively in the direction of a confirmed 15m trend.
- Reduce failed-ignition trades caused by chasing 1m/orderbook bounces inside weak or non-trending chart structure.

## 29. Crypto Entry Gate Simplification Patch (2026-04-29)

### Diagnosis

- `signal_score` inside `build_crypto_plan()` is already the CryptoDeskAgent `combined_score`.
- That combined score already includes chart/swing signal, trend-follow score, 1m micro timing, orderbook pressure, BTC backdrop, discovery/weight, and freshness adjustment.
- The recommendation layer was re-checking many of the same sub-signals with strict `AND` gates.
  - Example failure mode: combined score can be high, but entry still becomes `watchlist_only` because one of micro/volume/stream/breakout gates is not perfect.
- Result:
  - Too few trades.
  - Late entries after waiting for every confirmation.
  - Poor bot value versus fast manual trading.

### Completed

- Simplified `direct_entry_ok`.
  - Removed duplicate requirements for `clean_momentum_window`, stream ignition, and breakout confirmation.
  - Now trusts composite score when:
    - `signal_score >= 0.63`
    - chart trend is allowed
    - trend score is at least `0.50`
    - orderbook bid/ask is not hostile (`>= 0.98`)
    - no bearish RSI divergence
- Added `combined_score_ok` fallback.
  - Allows moderate-confidence entries from `signal_score >= 0.58`.
  - Uses smaller sizing (`0.40x` to `0.65x`) so the bot becomes more active without treating every setup as a full-conviction trade.
  - Bypasses the volume gate because volume/micro are already embedded in the composite score.
- Updated direct-entry sizing.
  - `>= 0.80`: `0.90x`
  - `>= 0.72`: `0.78x`
  - `>= 0.65`: `0.65x`
  - else: `0.52x`
- Kept hard safety rails:
  - stressed regime blocks entries
  - hard overheat still blocks most chase entries
  - bearish RSI divergence blocks entries
  - chart trend must still be `trend_long` or `pullback_long`
  - execution/risk layer still enforces exposure and duplicate-position limits

### Intent

- Make the bot trade like an active trend-following trader instead of a passive checklist engine.
- Let the composite model make decisions instead of forcing it to pass every individual sub-signal again.
- Increase trade frequency while keeping the minimum structural protections that prevent random long entries in weak markets.

## 30. Crypto Growth Mode Execution Patch (2026-04-29)

### Goal

- Project target: grow the starting seed toward `100M KRW` through active crypto trend-following first.
- The bot must behave more like an active trader:
  - frequent entries when composite score is valid
  - faster re-entry after small losses
  - fewer hard blocks from duplicated risk checks
  - still no blind entries in weak chart structure

### Completed

- Relaxed crypto correlation crowding.
  - Default `CRYPTO_HIGH_CORR_MAX_POSITIONS` changed from `2` to `4`.
  - `.env.example` documents `CRYPTO_HIGH_CORR_MAX_POSITIONS=4`.
- Expanded crypto execution capacity.
  - Crypto desk limits changed from `4 positions / 2.0x` to `5 positions / 2.4x`.
  - Crypto-only gross notional cap changed:
    - high budget: `1.65x -> 2.05x`
    - medium budget: `1.15x -> 1.45x`
    - low budget: `0.75x -> 1.0x`
- Relaxed stale signal blocking.
  - Crypto stale block threshold changed from freshness `<= 0.70` to `<= 0.55`.
  - Reason: CryptoDeskAgent already freshness-adjusts combined score; execution should not double-block it.
- Changed crypto loss behavior from hard-block to smaller-probe mode.
  - Small/scratch losses no longer freeze same-symbol crypto re-entry.
  - Same-symbol crypto cooldown now requires a stop-like loss of at least `-1.0%`.
  - Repeated-loss block for crypto now requires at least `3` losses and `<= -3.0%` cumulative recent loss.
  - Desk-level high stop pressure no longer hard-blocks crypto-only growth mode; it throttles/downgrades instead.
  - Symbol high stop pressure downgrades crypto entries to `selective_probe` instead of `stand_by`.
- Relaxed crypto-only risk budget caps.
  - Balanced cap: `0.40 -> 0.48`.
  - Negative PnL cap: `0.30 -> 0.36`.
  - Losses > wins / drawdown cap: `0.20 -> 0.28`.
  - Exposure warning/block moved to `1.65x / 2.05x`.

### Intent

- Convert the system from "avoid mistakes first" to "trade valid trend edges actively, then size down when edge is weak."
- Increase turnover enough for compounding to be possible while still preserving hard protection against stressed regimes, extreme overheating, and non-trending charts.
- This does not guarantee profits or a timeline to 100M KRW; it makes the system structurally capable of higher turnover and faster compounding if the edge proves positive.

## 31. Candidate-Specific Multi-Coin Entry Patch (2026-04-29)

### Diagnosis

- The execution layer could create multiple orders from `candidate_symbols`, but it cloned the leader coin plan into secondary candidates.
- That raised two problems:
  - Good: multi-symbol entry became possible.
  - Bad: a weaker secondary coin could inherit the leader's composite score and enter without passing its own trend/orderbook checks.

### Completed

- Added candidate-specific crypto eligibility before multi-order creation.
  - Each secondary coin must pass:
    - `combined_score >= 0.58`
    - `trend_entry_allowed == true`
    - `trend_follow_score >= 0.44`
    - `orderbook_bid_ask_ratio >= 0.96`
    - no bearish RSI divergence
    - signal freshness above `0.55`
    - no hard overheat
- Added candidate-specific plan overrides.
  - Secondary entries now receive their own:
    - combined score
    - trend fields
    - micro fields
    - stream fields
    - orderbook fields
    - ATR sizing fields
    - correlation/freshness fields
    - pullback/ICT/breakout context
- Weak candidates are skipped with explicit rationale.
- Crypto multi-entry sizing now avoids over-dilution.
  - When multiple eligible coins exist, per-order base sizing divides by at most `3`, with a minimum `0.18x` before risk-budget scaling.
  - Exposure caps still prevent unlimited stacking.

### Intent

- Allow several coins to be entered during broad crypto momentum instead of forcing a single leader.
- Preserve signal quality by requiring each coin to pass its own growth-mode checks.
- Increase turnover and compounding opportunity without blindly buying every scanner candidate.

## 32. Failed Start Guard Patch (2026-04-29)

### Diagnosis

- After multi-coin growth mode opened positions, several crypto positions immediately sat at negative PnL with `peak_pnl_pct = 0.0`.
- Recent order logs also showed secondary candidates with `combined_score = 0.0`.
  - Root cause: the multi-order fallback allowed candidates missing `candidate_markets` metadata.
  - That could create idle/no-score orders and confusing focus text inherited from the lead coin.

### Completed

- Candidate-specific multi-coin entries now reject candidates with missing metadata.
  - No more fallback pass for crypto candidates without their own scanner/score payload.
- Candidate-specific entries now rewrite `focus`.
  - Focus now identifies the actual candidate symbol and its own combined score.
- Added `rapid_failed_start`.
  - If a crypto position is open at least 4 minutes, never reached `+0.05%`, and falls to `<= -0.75%`, it is closed quickly.
  - This cuts dead-on-arrival entries before they drift into larger stops.
- `rapid_failed_start` is treated as stop-like in execution scoring.

### Intent

- Stop the bot from holding positions that immediately prove the entry timing was wrong.
- Keep active multi-coin trading, but remove no-score/fallback candidates.
- Reduce immediate capital bleed while preserving fast trend-following participation.

## 33. Crypto No-Lift Exit Patch (2026-04-29)

### Diagnosis

- AVAX was not an active holding; it was already closed as `failed_ignition` with `-0.73%`.
- The active loss pattern was dead-start positions:
  - positions opened, never reached even a small positive peak, then sat around `-0.3%` to `-0.6%`
  - previous `rapid_failed_start` waited until `-0.75%`, which was too slow for active trend trading

### Completed

- Added no-lift crypto exits.
  - `rapid_no_lift`: close after 10 minutes if peak PnL stayed `<= +0.05%` and current PnL is `<= -0.30%`.
  - `rapid_reversal_loss`: close after 10 minutes if a position only reached `+0.15%` to `+0.80%` but then reverses to `<= -0.35%`.
  - `no_lift_exit`: same rule in the regular position sync path.
  - `rapid_flat_timeout` / `flat_no_lift_exit`: close after 18 minutes if peak stayed `<= +0.10%` and current PnL is not above `+0.05%`.
- Tightened failed ignition.
  - Crypto `failed_ignition` now fires at `<= -0.60%` after the fast-fail window if the position never reached `+0.10%`.
- Execution scoring treats the new no-lift exits as stop-like, so weak symbols/paths are penalized faster.
- Closed the last no-score rotation path.
  - When only one crypto slot is left, execution now still filters candidates through candidate-specific metadata.
  - If candidate metadata is missing, candidate rotation is disabled instead of rotating into a `combined_score=0.0` symbol.

### Intent

- Keep the bot active, but stop letting weak entries bleed capital.
- Free capital faster for the next momentum candidate.
- Preserve winners with trailing rules while making losers prove themselves quickly.

## 34. Paper/Shadow Position Sync Patch (2026-04-29)

### Diagnosis

- Dashboard showed `KRW-AVAX`, `KRW-XRP`, and `KRW-ADA` as still held even after their paper positions were closed.
- Root cause: the dashboard/execution state used the legacy `positions` shadow table, while paper trading truth lived in `paper_positions`.
- Paper closes did not always delete the matching shadow `positions` row, creating ghost holdings and distorted exposure/cap checks.

### Completed

- `CompanyState.open_positions` now loads from `paper_positions` in paper mode tracking.
- Dashboard closed/open performance payloads use paper closed/open helpers for the mock trading view.
- `_close_position()` now deletes the matching shadow `positions` row when a paper position closes.
- Existing ghost shadows should be pruned on deploy for symbols already closed in `paper_positions`.

### Intent

- Make the dashboard reflect actual mock trading state.
- Prevent stale ghost positions from blocking new entries or confusing exposure/PnL.

## 35. Repeat Failed Symbol Guard (2026-04-29)

### Diagnosis

- AVAX was correctly removed as a ghost position, but later re-entered as a new real paper position.
- The re-entry was allowed with `combined=0.745` even though AVAX had a recent `failed_ignition` loss.
- That made the dashboard look like AVAX "keeps coming back" and exposed the bot to repeated weak-symbol churn.

### Completed

- Crypto candidates with recent stop-like same-symbol loss now require stronger re-entry:
  - `combined_score >= 0.82`
  - `trend_follow_score >= 0.62`
  - `orderbook_bid_ask_ratio >= 1.15`
- Added `rapid_repeat_symbol_failure`.
  - If a recently failed symbol re-enters, never reaches `+0.05%`, and is below `-0.10%` after 4 minutes, it is closed quickly.

### Intent

- Keep broad-market scanning open, but stop repeatedly recycling symbols that already failed unless the new signal is clearly stronger.

## 36. Launch Confirmation Gate (2026-04-29)

### Diagnosis

- Crypto closed stats were extremely poor: 21 recent closed trades, 2 wins / 19 losses, total `-13.30%`.
- Most losing entries had a high composite score but weak real-time confirmation:
  - `micro_score` often `0.20`
  - `stream_score` often `0.0` to `0.4`
  - entries were triggered by scanner/orderbook/composite before actual price launch
- This produced repeated `rapid_failed_start`, `rapid_no_lift`, and `failed_ignition` exits.

### Completed

- Added launch confirmation before direct/composite crypto entries.
  - Requires one of:
    - `micro_score >= 0.55` and `micro_vol_ratio >= 1.1`
    - `stream_score >= 0.55`
    - `stream_ignition`
    - `breakout_count >= 2` and `vol_ratio >= 1.4`
- Tightened direct entry:
  - `combined >= 0.76`
  - `trend_follow_score >= 0.58`
  - `orderbook_bid_ask_ratio >= 1.08`
- Tightened composite fallback:
  - `combined >= 0.82`
  - `trend_follow_score >= 0.62`
  - `orderbook_bid_ask_ratio >= 1.12`
- Candidate-specific execution now rejects high composite candidates unless launch is confirmed.

### Intent

- Stop entering just because a coin looks good on a scanner.
- Enter only when the move is actually starting on 1m/tick/volume confirmation.
- Reduce losing trade count first; increase frequency later only after the win rate recovers.

## 37. Trend Trigger Exit Wiring (2026-04-29)

### Diagnosis

- The system was called trend-following, but exits were mostly stop/no-lift/time based.
- Bullish entry signals used trend context, but bearish trend triggers were not persisted in order metadata and therefore could not drive paper exits.
- This made the bot behave like "enter on uptrend candidate, exit on loss control" instead of "enter on uptrend trigger, exit on downtrend trigger."

### Completed

- Execution metadata now persists trend trigger fields:
  - `trend_alignment`, `trend_entry_allowed`, `trend_follow_score`
  - `choch_bearish`, `bos_bearish`, `stream_reversal`
  - `rsi_bearish_divergence`
- Paper position sync now closes open crypto positions on bearish trend triggers:
  - `trend_reversal_exit` for bearish CHoCH/BOS/stream reversal
  - `downtrend_exit` for explicit downtrend alignment
  - `trend_invalid_exit` when trend permission is gone and score is weak
  - `bearish_divergence_exit` when RSI divergence appears while PnL is not safely positive

### Intent

- Make the strategy match the intended model: enter on confirmed bullish trend trigger, exit when the bullish trend trigger fails or flips bearish.

## 39. Trend Exit Minimum Hold Time Fix (2026-04-30)

### Diagnosis

- Post-reset sample: 43 closed, 5 wins / 38 losses, total PnL -11.85%.
- **28 of 43 (65%) closed via `trend_invalid_exit`** with peak_pnl=0.0 and losses of -0.09 to -0.46%.
- Root cause: `_crypto_trend_exit_reason()` had no minimum hold time.
  With 8-second strategy cycles, positions entered on `trend_entry_allowed=True`
  were closed 8-16 seconds later when the EMA boundary caused `trend_entry_allowed`
  to flip False. This produced pure fee+slippage losses on every such trade.
- The trend gate is for ENTRIES, not for exits. An open position needs time to develop.

### Completed

- Added `minutes_open` parameter to `_crypto_trend_exit_reason()` in `app/core/state_store.py`.
- Updated the call site at line ~828 to pass the already-computed `minutes_open`.
- New hold-time ladder:
  - CHoCH/BOS structural reversal: 2 min minimum
  - Stream reversal: 3 min + pnl <= +0.20% (very noisy 15s metric)
  - Confirmed 15m downtrend: 3 min minimum (EMA lags at boundary)
  - RSI bearish divergence: 3 min minimum
  - Trend invalid (no permission + weak score): 4 min + pnl <= -0.20%
    (the -0.20% threshold explicitly excludes pure fee/slippage exits)
- Hard stops (-2.0%), profit trail, no-lift exits, and rapid guard are unchanged.

### Intent

- Give trades 2-4 minutes to develop before trend-based exits fire.
- Reduce the frequency of sub-15-second closures that only produce fee losses.
- Preserve meaningful exits (CHoCH, confirmed downtrend, RSI divergence) with adequate confirmation time.

---

## 38. Clean Trade Data Baseline Reset (2026-04-29)

### Completed

- Oracle VM trade/performance tables were backed up and reset so the dashboard measures the new trend-trigger strategy from a clean baseline.
- Reset timestamp:
  - UTC: `2026-04-29T07:12:48+00:00`
  - KST: `2026-04-29 16:12:48`
- Backup created before deletion:
  - `/home/ubuntu/trading-bot/trading_company_v2/data/backups/trading_company_v2_pre_reset_2026-04-29T071248+0000.db`
- Tables cleared:
  - `paper_positions`
  - `paper_orders`
  - `cycle_journal`
  - `positions`
  - `closed_positions`
  - `live_order_log`

### Verification

- Services restarted and confirmed active:
  - `trading-loop`
  - `trading-dashboard`
- After restart, the DB started fresh:
  - `paper_positions`: `0`
  - `closed_positions`: `0`
  - `live_order_log`: `0`
  - `cycle_journal`: `1` new cycle
  - `paper_orders`: `1` new post-reset order
- First post-reset order was `watchlist_only`, so no new position had opened immediately after reset.

### Intent

- Ignore pre-fix losing trades when evaluating the new strategy.
- From this point forward, track only trades generated after:
  - launch-confirmed entries
  - trend-trigger metadata persistence
  - bearish trend-trigger exits
- Use the next 20-30 post-reset trades as the first real sample for win rate, average PnL, and exit-reason analysis.

## 40. Mobile Scanner/Performance Navigation (2026-04-30)

### Diagnosis

- `/scanner` and `/performance` existed, but mobile users could miss them because the main dashboard only exposed them in the crowded top header.
- The scanner/performance pages also had no persistent mobile navigation back to the other operator pages.

### Completed

- Added fixed mobile bottom navigation to:
  - dashboard `/`
  - scanner `/scanner`
  - performance `/performance`
- Added mobile header wrapping for the dashboard so scanner/performance buttons do not get pushed off-screen.
- Added mobile scanner layout improvements:
  - bottom padding for the fixed nav
  - horizontally scrollable filter chips
  - two-column market overview cards
- Fixed `/scanner-data` source selection:
  - Scanner now reads `desk_views["crypto_desk"]["all_candidates"]`, where `CryptoDeskAgent` actually stores the full scan list.
  - Orchestrator also persists `crypto_view` into `state.market_snapshot` for future scanner consumers.

### Intent

- Make the three core operator screens reachable on mobile at all times:
  - Dashboard
  - Scanner
  - Performance
- Keep mobile monitoring usable while preserving the existing desktop layout.
- Prevent the scanner page from showing an empty/loading state when the candidate data exists in desk views.

## 41. Fresh Timing Gate for Crypto Trend Pullbacks (2026-04-30)

### Diagnosis

- The user's suspicion was correct: the bot could still enter a pullback because the 15m trend and orderbook were strong, even when the immediate 1m/tick timing was already stale or weak.
- This created the bad pattern:
  - 15m trend says bullish setup
  - entry happens after the live move has already lost lift
  - position starts negative from fee/slippage
  - exit logic then manages a weak trade instead of riding a fresh trend
- Paper repeat-failure logic also still treated managed exits like `trend_invalid_exit`, `rapid_no_lift`, and `reversal_loss_exit` as stop-like failures, which could over-penalize symbols after normal risk-management exits.

### Completed

- `app/services/recommendation_engine.py`
  - Added `trend_pullback_timing_ok`.
  - Trend-pullback entries now require at least one fresh timing confirmation:
    - fresh stream/tick support within 3.5s
    - or 1m micro support with volume and non-falling 3m move
    - or breakout + volume confirmation
  - Trend-pullback notes now include timing details so the dashboard explains why an entry was allowed.
- `app/agents/execution_agent.py`
  - Mirrored the same fresh timing gate in `_crypto_candidate_entry_ok()` so multi-coin candidate rotation cannot bypass it.
  - Fixed the trend alignment check from the invalid `"uptrend"` label to the actual `"trend_long"` label.
  - Candidate focus text now shows actual symbol, combined score, trend alignment, micro score, stream score, and orderbook ratio.
  - Missing candidate metadata now updates focus to the actual rotated symbol instead of leaving the lead-symbol text on another coin.
- `app/core/state_store.py`
  - Narrowed `_STOP_LIKE_PAPER_REASONS` to true hard stops only:
    - `stop_hit`
    - `rapid_stop_hit`
    - `early_failure`
    - `rapid_failed_start`
    - `rapid_repeat_symbol_failure`
  - Managed exits remain visible in performance stats but no longer poison repeat-symbol failure checks as if they were immediate hard stops.

### Intent

- Keep active crypto trading, but stop entering "dead pullbacks" where the high-timeframe setup is valid but the immediate launch has already faded.
- Make entries closer to the intended model: 15m trend setup + fresh 1m/tick trigger.
- Reduce false throttling after normal managed exits.

### Follow-up Tightening

- Immediate loss diagnosis after deployment still showed the losing trades were mostly no-lift entries:
  - 12 closed paper trades
  - 1 win / 11 losses
  - many losers had `peak_pnl_pct` near 0.0, meaning the bot entered before actual lift appeared.
- Tightened the micro-only timing path:
  - `micro_score` 0.60 -> 0.72
  - `micro_vol_ratio` 1.05x -> 1.15x
  - `micro_move_3_pct` must be positive (`>= 0.05%`)
  - `micro_vwap_gap_pct` max 1.8% -> 1.6%
- Tightened breakout timing:
  - `vol_ratio` 1.4x -> 1.6x
  - `micro_move_3_pct` must be non-negative.
- Tightened recommendation-level trend-pullback orderbook floor:
  - `orderbook_bid_ask_ratio` 1.02x -> 1.10x.

### Intent

- Avoid entries that look good on 15m structure but have no immediate lift.
- Keep trading active, but make "active" mean live momentum is actually present, not just a historical trend setup.

## 42. Tick-Driven Crypto Rapid Guard (2026-04-30)

### Diagnosis

- The bot already used the Upbit websocket cache, but the websocket subscription was ticker-first.
- Strategy cycles were still effectively candle-cycle driven:
  - 15m/1m selected candidates
  - stream/tick fields were used as score inputs
  - rapid guard between cycles mostly used price-only trailing/stop logic
- For the user's target style, candles should define the direction/filter, but entry/exit response must be driven by live ticks.

### Completed

- `app/services/upbit_stream_cache.py`
  - Added Upbit websocket `trade` subscription alongside `ticker`.
  - Added `_normalize_trade_message()`.
  - Added `_append_trade_tick()` with a 180-second rolling trade buffer.
  - Trade messages now update the latest price and append true trade ticks without overwriting ticker liquidity fields.
- `app/core/state_store.py`
  - `rapid_guard_crypto_positions()` now uses `summarize_stream_momentum()` for open crypto positions.
  - Added tick-driven rapid exits before hard stop:
    - `rapid_tick_failed_start` when a position never lifts and the 15s tick stream reverses.
    - `rapid_tick_reversal` when a small profitable start reverses sharply in tick flow.

### Intent

- Change the operating model from "minute-candle reaction" toward "candle-filtered, tick-driven trend following."
- Keep 15m/1m candles as market structure filters, but let live trade ticks handle fast invalidation.
- Cut dead entries earlier before they drift into larger `rapid_failed_start` or hard-stop losses.

## 43. Event-Driven Tick Guard Callback (2026-04-30)

### Direction Lock

- Strategy doctrine from the user:
  - Auto-trading + quant trading direction.
  - Trend following is the base.
  - Candles are analysis/filter data, not execution delay tools.
  - Entry/profit protection/exit must react on tick/second/millisecond-level signals as much as the current Upbit/Python stack allows.

### Completed

- `app/services/upbit_stream_cache.py`
  - Added trade callback registration.
  - Websocket trade ticks now emit callbacks after the tick is written into the in-process cache.
  - Callbacks are executed outside the stream lock so strategy code does not block cache writes.
- `app/runtime.py`
  - Registered an event-driven tick guard callback during crypto runtime startup.
  - The callback:
    - checks only symbols with open crypto positions,
    - refreshes the active symbol set at most once per second,
    - throttles per symbol to avoid DB overload,
    - calls `rapid_guard_crypto_positions({symbol: tick_price})` directly from the trade event.

### Intent

- Move from "rapid guard every few seconds" to "rapid guard on actual trade ticks."
- Keep candle cycles for broader model updates, but let live Upbit trade events drive immediate exit/protection checks.
- This is not true exchange-colocated HFT, but it is the correct next step within the current Oracle VM + Python + Upbit websocket architecture.

## 44. Hot-Path Latency Instrumentation (2026-04-30)

### Completed

- Added `app/services/hot_path_metrics.py`.
- Runtime tick guard now records:
  - websocket trade dispatch delay (`dispatch_ms`)
  - guard execution time (`guard_ms`)
  - total tick-to-decision time (`total_ms`)
  - event reason counts (`checked`, `closed`, `throttled`, `lock_busy`, `error`)
- Metrics are written to:
  - `data/hot_path_latency.json`
- Added dashboard/API diagnostic endpoint:
  - `/diagnostics/hot-path-latency`

### Intent

- Stop guessing where latency comes from.
- Separate the bottlenecks:
  - websocket dispatch delay
  - Python guard/DB decision time
  - later: order request and fill confirmation latency
- Use this data to decide when the hot execution path must move from Python into a Rust/Go sidecar.

## 45. In-Memory Hot Path Position Guard (2026-04-30)

### Diagnosis

- `/diagnostics/hot-path-latency` showed websocket dispatch was effectively instant, but guard execution took several milliseconds:
  - dispatch average: ~0.015ms
  - guard average: ~5.8ms
  - guard p95: ~12ms
- Root cause: the tick callback still called `rapid_guard_crypto_positions()`, which opened SQLite sessions, read all open positions, updated rows, committed, then refreshed all-time positions even when no close was needed.
- For tick-level execution, this is the wrong hot path. DB work belongs on close/persist events, not every tick.

### Completed

- Added `app/services/hot_path_guard.py`.
- Open crypto paper positions are now cached in memory for the tick hot path.
- The tick callback now calls `hot_guard_crypto_tick(symbol, price)`:
  - evaluates PnL, peak, trail, no-lift, tick reversal from memory
  - updates in-memory PnL/peak on non-close ticks
  - only falls back to DB-backed `rapid_guard_crypto_positions()` when a close is actually required
- Runtime now refreshes the hot position cache at startup and through the hot guard symbol path.
- Hot-path latency metrics now include the guard reason (`checked_memory`, `no_open_position`, `closed`, etc.).

### Intent

- Remove DB reads/writes from the normal per-tick path.
- Reduce guard latency and make the architecture closer to a dedicated execution engine.
- Keep DB persistence for actual state-changing events so the dashboard and paper/live tracking remain consistent.

### Follow-up

- Hot-path latency metrics now reset on runtime startup, so old pre-cache samples do not pollute the new latency window.
- Each metric event includes `recorded_at_epoch` for freshness checks.

## 46. Tick-Ignition Entry Hot Path (2026-04-30)

### Diagnosis

- The bot had tick-driven exits, but entries were still mostly cycle-driven.
- That means a 15m/1m candidate could be detected, debated, ordered, and only then opened, creating the user's concern:
  - "uptrend trigger was found, but the bot enters late"
  - "then reversal is detected late and the trade exits in loss"
- Correct doctrine:
  - candles/agents prepare the candidate and risk context
  - websocket trade ticks fire the actual entry only when live momentum ignites

### Completed

- `app/services/hot_path_guard.py`
  - Added an in-memory tick-entry candidate cache.
  - `refresh_hot_entry_candidates(state)` extracts prepared crypto candidates from the latest `crypto_desk.all_candidates`.
  - Candidates must pass structural filters:
    - `trend_entry_allowed`
    - `trend_alignment` in `trend_long` / `pullback_long`
    - `trend_follow_score >= 0.70`
    - `combined_score >= 0.64`
    - orderbook bid/ask support
    - no bearish RSI divergence / hard overheat
  - Added `hot_runtime_symbols()` so websocket callbacks monitor both open positions and prepared entry candidates.
  - Added `hot_process_crypto_tick(symbol, price)`:
    - exits are checked first through the memory guard
    - if no position is open, a prepared candidate can open only on fresh tick ignition
    - tick ignition requires fresh stream, positive 15s move, non-negative 60s context, enough ticks, and buy pressure
  - Tick entries write the DB only once, at actual open time, and open the paper position directly without calling the heavier full sync path.

- `app/runtime.py`
  - Runtime now refreshes hot entry candidates after every orchestrator cycle.
  - Websocket callbacks now call `hot_process_crypto_tick()`.
  - Hot-path metrics now mark both `opened` and `closed` events.

### Intent

- Move entry timing from "cycle says buy" to "cycle prepares, tick confirms, tick opens."
- Keep the existing agent stack as the strategic filter while making execution timing closer to a dedicated execution engine.
- This is still paper/live-stack Python, not colocated HFT, but it removes the largest architectural mismatch between trend detection and fast execution.

### Immediate Tuning After First Live Samples

- First two tick-entry samples opened correctly but closed as `rapid_tick_failed_start` with no positive peak.
- Tightened tick-entry quality gate:
  - candidate `combined_score` 0.64 -> 0.72
  - candidate `trend_follow_score` 0.70 -> 0.76
  - candidate orderbook bid/ask 1.02 -> 1.08
  - reject exhausted/late 1m candidates using `micro_move_3_pct` and `micro_vwap_gap_pct`
  - tick ignition now requires stronger stream score, 5s lift, 15s move band, and 60%+ buy pressure
- Intent: keep the new tick-speed architecture, but stop treating weak micro-bursts as valid trend ignition.

## 47. RANGING Impulse Tick-Scalp Arming (2026-04-30)

### Diagnosis

- Scanner examples such as HYPER/BIO/SPK can show a strong chart impulse while the global regime is still `RANGING`.
- The old entry path treated weak snapshot orderbook / low combined score as a full block.
- That avoids some bad chases, but it also misses fast range-break impulses where the correct action is not immediate entry, but tick-level arming.

### Completed

- `app/services/hot_path_guard.py`
  - Added `range_impulse` entry profile for scanner leaders in ranging markets.
  - A candidate can now be armed even with weak snapshot OB if:
    - chart signal is strong (`signal_score >= 0.74`)
    - recent/change-rate impulse is positive (`>= 3%`)
    - RSI is not extreme (`<= 82`)
    - signal is fresh and not bearish-divergent
  - Range impulse candidates do not open from the cycle snapshot.
  - They require stricter websocket trade confirmation:
    - 4+ ticks in 15s
    - stream score >= 0.76
    - 5s lift >= 0.12%
    - 15s lift between 0.35% and 1.15%
    - 64%+ buy pressure
  - Entry size is intentionally smaller (`0.04x` to `0.06x`) because range impulses have higher false-positive risk.

### Intent

- Do not hard-block all `RANGING` markets.
- Treat ranging leaders as scalp candidates only when live tick flow proves continuation.
- Preserve the user's objective: active trend-following behavior without blindly chasing already-extended candles.

### Dashboard/Journal Visibility

- `app/agents/execution_agent.py` now labels these cases as:
  - `RANGING impulse candidates armed for tick confirmation.`
- This prevents the dashboard from saying the candidate simply "failed" when the real state is:
  - no cycle entry,
  - candidate armed,
  - waiting for websocket trade ignition.

### First Sample Tuning

- First live range impulse sample (`KRW-HYPER`) opened correctly but failed with no positive peak.
- Added range-impulse-specific risk handling:
  - size reduced from `0.04x-0.06x` to `0.03x-0.04x`
  - no-lift fail close at `-0.25%` after 15s
  - hard fail close at `-0.40%`
  - if peak reaches `+0.28%`, protect at roughly `peak - 0.35%`
- Intent: keep scanning/arming fast movers, but make each failed range scalp cheap until the pattern proves positive expectancy.

## 48. Obvious 15m Trend Ride Override (2026-04-30)

### User Doctrine

- Candles/minute/hour/day charts are context and trigger discovery.
- Entry/exit/profit protection must happen on tick/second speed.
- If a 15m chart is already in a clear rising trigger, the bot should not keep saying "confirmation wait" just because snapshot orderbook/micro/combined score is weak.
- Once entered, the exit line must rise with profit instead of letting a paid trend fall back into loss.

### Completed

- `app/services/hot_path_guard.py`
  - Added `obvious_trend` entry profile.
  - Clear 15m trend candidates can now be prepared even when orderbook/micro snapshot gates are weak.
  - Tick entry requires only lightweight no-reversal validation:
    - fresh stream
    - at least 1 tick in the 15s window
    - 15s and 60s moves not actively dumping
    - buy ratio at least 42%
  - Size is larger than `range_impulse` but still capped (`0.07x-0.12x`) until expectancy improves.

- `app/agents/execution_agent.py`
  - Added `_crypto_obvious_trend_entry_ok()`.
  - Candidate-level execution can now approve obvious 15m trends without requiring orderbook >= 1.08x or launch-confirmed gates.
  - If the global `crypto_plan` is `watchlist_only`, execution now scans candidate metadata anyway and overrides to `probe_longs` when an obvious trend exists.
  - Focus/notes explicitly label these entries as `obvious_trend 15m trend ride`.

- `app/services/recommendation_engine.py`
  - Added `obvious_trend_ride_ok` before the defensive hard-overheat / volume gates.
  - Clear rising 15m trend now returns `watchlist_only` with focus:
    - `obvious 15m trend armed for live-tick continuation`
  - Intent: the cycle arms the setup, but websocket tick flow opens the position.

- `app/core/state_store.py`
  - Raised the crypto trailing exit line sooner:
    - peak >= `0.55%`: floor `+0.05%`
    - peak >= `0.80%`: floor `+0.18%`
    - peak >= `1.20%`: floor `+0.45%`
  - Intent: when trend pays, move the liquidation line up with the position.

- `app/agents/risk_committee_agent.py`
  - Crypto-only growth mode now uses a wider drawdown gate (`-20%`) before fully blocking entries.
  - When entries remain allowed, risk budget has a floor of `0.32`.
  - Intent: losses throttle the bot, but do not suffocate obvious trend setups down to `0.03x` just as the strategy changes.

### Intent

- HYPER/BIO/SPK-style visible 15m trend leaders should no longer die at "score not enough / confirmation wait" before execution sees them.
- This is intentionally more aggressive. The remaining guards are top-risk filters only:
  - RSI extreme,
  - bearish divergence,
  - excessive trend extension,
  - immediate live stream reversal.

### Follow-up Tuning

- First `obvious_trend` samples proved the entry path works, but BIO/DRIFT showed the next failure mode:
  - a coin can still have a large 15m/day move while the most recent 15m window has already turned down.
  - cycle snapshot orders could still open without live stream confirmation.
- Tightened the obvious-trend definition:
  - require current `recent_change_pct >= 0.00`
  - require either combined >= `0.52`, very strong chart/trend, or a high-change low-RSI exception
  - tick validation now requires stream score >= `0.55`, 15s move >= `0.18%`, and buy ratio >= `55%`
- Added recent-failure cooldown for hot entries after `rapid_tick_failed_start` / `rapid_range_impulse_fail`.
- Removed cycle-level obvious-trend opening. Obvious trend setups are now armed only; entry happens in `hot_process_crypto_tick()`.
- Added `rapid_obvious_trend_fail`:
  - if an obvious-trend entry shows no lift and reaches `-0.35%` after 15s, close
  - if it reaches `-0.70%` at any time, close
  - intent: obvious trends must prove immediate continuation; otherwise the bot exits before a full stop.

## 49. Pause Failed Experimental Impulse Entries + Weighted PnL (2026-04-30)

### Why

- After the clean tick-only reset, six new paper trades closed with negative expectancy:
  - CHIP `-1.34%`, KAT `-0.88%`, DOGE `-0.88%`, BIO `-0.04%`, ZETA `-0.44%`, HYPER `-0.84%`.
  - Most came from experimental `obvious_trend` / `range_impulse` hot entries.
- Dashboard showed roughly `-4.4%`, but those entries used small sizes (`0.03x-0.12x`).
- The old dashboard/risk summary summed raw trade returns, so a `0.04x` trade losing `-1%` displayed as `-1%` capital damage instead of `-0.04%`.

### Completed

- `app/services/hot_path_guard.py`
  - Added `_ENABLE_EXPERIMENTAL_IMPULSE_ENTRIES = False`.
  - `obvious_trend` and `range_impulse` profiles are paused after the failed first sample.
  - Standard `trend_ignition` and `early_ok` tick entries remain available.

- `app/core/state_store.py`
  - Daily realized/unrealized PnL is now capital-weighted by notional.
  - Desk stats are now capital-weighted by notional.
  - Close-reason and symbol performance `pnl_pct` are now capital-weighted.
  - Raw trade-return sums are retained as `raw_pnl_pct` for diagnostics.
  - All-time quick performance compounding now uses `realized_pnl_pct * notional_pct`.

### Intent

- Do not abandon tick trading; stop the unproven impulse profiles that were immediately bleeding.
- Evaluate fresh trades using actual capital impact, not raw trade-return summation.
- Rebuild the next sample window from stricter tick entries only.

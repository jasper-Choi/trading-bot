# Trading Company V2 Handoff

## 최신: Claude - 2026-06-11 세션5 파트3 (수익 극대화: 시장별 분리 + 레이스/매도유실 수정)

### 커밋 순서
1. `597b62f` — 반등일 조기 포착: 당일 +2% 반등 시 STRESSED→RANGING 완화
2. `c7825f4` — KOSPI/KOSDAQ 시장별 분리 운용 (사용자 승인) — STRESSED 시장 종목만 제외
3. `0801380` — 봇 매수 포지션이 kis_hold로 둔갑하는 레이스 수정 (주문 payload에 전략 프로필 동봉)
4. `4711881` — KIS 자동매도 3회 재시도 + live_order_log 감사 기록 + [kis-auto-sell] print 로깅
5. `5778763` — sync 프로필 복원 윈도우 2h→72h

### 사용자 결정 사항
- 크립토 데스크: **korea만 유지** (재활성화 안 함)
- KOSDAQ 분리 운용: 승인
- 레이스 버그 포지션 sector_wave 교정: 095610/319660/031980 모두 승인 (DB 수정 + payload 백필 완료)

### 06-11 거래 결과 (시장별 분리 배포 직후)
- sector_wave 발동: 반도체장비 7종목 +26% 파동 → laggard 3종목 매수 (095610/319660/031980, 각 0.25x)
- 031980: 트레일 청산 -0.42% (급락 슬리피지), 319660: +0.03% — **둘 다 KIS 매도 유실** (재시도 코드 이전)
- 095610: open, sector_wave 프로필로 교정 완료

### 06-11 장 마감 시점 최종 상태 (15:30 KST 직전 확인 완료)
- **KIS 계좌 잔고 0 / paper open 0건 — 완전 일치** ✅
- sector_wave 3종목 전량 익절: 095610 +0.78%, 031980 +0.96%, 319660 +0.03%
- **재시도 로직 실전 증명**: 031980 매도 1차 `unsupported_order_shape` 실패 → 2차 성공
- 319660 '유실' 추정은 오판 — 05:43 매도가 실제 체결, KIS 잔고 API 정산 지연으로 보유처럼 표시됐던 것
- stale pending 전량 정리 (1075/1078/1079 settled, 사용자 승인) → `allow_new_entries=1`, 차단 요인 0건
- 알려진 KIS VTS 특성: 잔고 API가 체결 후 수 분간 구잔고 반환 가능 / 주문 상태조회 간헐 500

### 페이스 상향 3종 (커밋 `13421d0`, 사용자 지시 — 06-11 장 마감 후 배포)
1. **hot streak 부스트**: 최근 8건+ & WR≥42% & 합산 +8%↑ 전략은 사이즈 ×1.30
   (cold streak ×0.5의 대칭 — 현재 new_high_breakout 해당, 성과 꺾이면 자동 해제)
2. **Korea 슬롯 3→4, 데스크 노셔널 1.8→2.0** (sector_wave 점유로 신호 막힘 해소)
3. **Korea/US risk_budget floor 0.18→0.22** (상한·감산 로직 불변)
→ 예상: 평균 사이즈 +20~30%, 동시 노출 +33%. 하방 가드(stop/trail/서킷브레이커/STRESSED) 전부 유지

### 내일(06-12) 관찰 포인트
1. K-regime 전환: 폭락 안정화 시 STRESSED→RANGING (chg5 회복 or 당일 +2% 반등) → 평균회귀 진입 재개
2. 트레일 청산 시 `[kis-auto-sell]` 로그로 재시도 동작 계속 확인
3. hot streak 부스트 발동 확인: 주문 노트에 "strategy hot streak: ... ×1.30" 표시

### 알려진 한계
- 트레일 슬리피지: 사이클(20-45s) 체크라 급락 시 트레일 라인보다 낮게 청산될 수 있음 (031980 +1.2% 라인 → -0.42% 체결)
- state_store의 _log.*는 핸들러 부재로 어디에도 안 남음 — 중요 이벤트는 print(flush=True) 사용

---

## 이전: Claude - 2026-06-11 세션5 파트2 (kis_hold 트레일 익절 활성화)

### 커밋: `cdbd82e`
- kis_hold 포지션도 봇이 **트레일 익절** 수행 (사용자 지시: "청산라인까지 하락하면 바로 익절")
- `_korea_trail_rules` 적용, 익절 전용 (protect 라인 항상 양수 — 손실 매도 불가)
- 손절/타임아웃/no_momentum_cut 계속 미적용, 장중(09:00~15:20 KST)만 발동
- `_close_position`: `kis_hold_trail_profit` 사유 시 KIS 매도 자동 발송 (shadow 무관)
- `sync_paper_from_kis`: 익절 후 30분 재생성 가드 (중복 매도 루프 방지)

### 실행 결과 (06-11 장중)
- 420770 기가비스: **+5.58% 익절 완료** (KIS 잔고에서 제거 확인)
- 036930 주성엔지: paper는 +12.44% 익절 처리됐으나 KIS 매도 주문 유실 (auto_pull 재시작에 스레드 소실)
  → **사용자 승인 후 01:24 UTC 재발송 — 15주 시장가 매도 체결 완료 (주문번호 0000014745)**
  → **KIS VTS 계좌 잔고 전량 비움 확인** (기가비스 +5.58%, 주성엔지 ~+12% 익절로 마감)

### ⚠️ 배포 규칙 (중요)
**VM은 `auto_pull.sh` 크론이 5분마다 git pull + 서비스 재시작** → SCP 단독 배포는 다음 pull에서 덮어써짐.
**반드시 커밋+푸시로 배포할 것.** (이번 세션에서 SCP본이 5분 만에 롤백되며 매도 주문 유실 발생)
- 크론: `*/5 * * * * auto_pull.sh`, `0 15 * * * go_live.sh`, 주간 retrain 등

---

## 이전: Claude - 2026-06-11 세션5 (한국 로컬 레짐 신설 + S27/S29 죽은코드 복구)

### 배경: 코스피 폭락 중 구조 결함 발견
- 코스피 06-02 고점 8,801 → 06-11 7,534 (-14%), 06-08 서킷브레이커 발동
- 글로벌 레짐은 crypto 신호 기반(RANGING)이라 한국 시장 폭락을 전혀 인지 못함

### 진단된 구조 결함 3가지
| # | 결함 | 영향 |
|---|---|---|
| 1 | `_determine_regime()`이 crypto 신호 유무로만 결정, `trend_structure_agent`는 0.58 고정 스텁 | KOSPI 추세/폭락과 무관하게 한국 데스크 레짐 고정 → B/S15/S23 영구 차단 or 폭락장 진입 허용 |
| 2 | S27/S29는 80de8e9 커밋 메시지에만 있고 스캐너 코드 미커밋 (소비측만 존재) | bb_squeeze/volume_surge 후보 항상 빈 리스트 = 죽은 전략 |
| 3 | EMA200 ×0.95 완화도 미반영 + S27/S29 전용 스탑 부재 | mongtata/RSI2 후보 부족, 신설 전략 리스크 불일치 |

### 수정 내역 (세션5)
1. **한국 로컬 레짐** — `korea_stock_desk_agent._compute_korea_market_regime()`:
   - KOSPI/KOSDAQ 지수 일봉 (naver fchart, symbol=KOSPI/KOSDAQ 동작 검증됨)
   - STRESSED: chg5 ≤ -3.5% (급성 폭락만, 5거래일 내 자동 해제)
   - TRENDING: 종가 > EMA20 > EMA60 & chg20 ≥ +1.5% (상승만, long-only)
   - 그 외 RANGING (만성 하락장 포함 — 평균회귀 유지, 전면 차단 금지 원칙)
   - `build_korea_plan` 최상단에서 글로벌 레짐 오버라이드 (글로벌 STRESSED는 유지, fail-open)
2. **S27/S29 스캐너 복구** — BB폭<4% 압축+상단돌파+거래량1.3x / 거래량5x+횡보<2%
3. **EMA200 ×0.95 완화 복구** (mongtata/RSI2 스캔)
4. **S27 스탑 -2.0% / S29 스탑 -2.5%** 전용 분기 (`_position_thresholds`, generic 폴백 제거)

### 배포 확인 (06-11 00:59 UTC, 장중)
```
korea_plan.action = capital_preservation ("Stress regime")  ✅ 폭락장 신규 진입 차단
글로벌 regime=RANGING (crypto 데스크 영향 없음), allow_new_entries=1
보유: 420770 기가비스 +3.81% (peak 11.78), 036930 주성엔지 +8.81% (peak 18.13) — 모두 kis_hold
06-10~11 봇 신규 진입 0건 (스캐너 무신호로 자연 보호됨)
```

### 레짐 전환 시나리오 (전략-시장 매칭)
- 폭락 진행 (현재): STRESSED → capital_preservation
- 안정화 (chg5 > -3.5% 회복): RANGING → 평균회귀(S2/S9/S13) + S18/S22/S27/S29 반등 포착
- 추세 회복 (EMA 정배열 + chg20 ≥ +1.5%): TRENDING → B/S15/S23 모멘텀 재가동

### 관찰 포인트
- 데스크 reason에 `K-regime=STRESSED/RANGING/TRENDING` 표기, stand_by 노트에 korea-local 상세
- 안정화 후 mongtata 후보 다수 발생 예상 — deviation ≥ -12% 필터(e784dcd)가 낙폭 과대 차단

---

## 이전: Claude - 2026-06-10 세션4 파트2 (전략 회고 + 4가지 추가 개선)

### 커밋 순서 (세션4 파트2)
1. `80de8e9` — S27/S29 신규전략 + sector_wave 사이즈 보정 + EMA200 완화 + RANGING pre_gap 차단
2. `e784dcd` — **전략 회고 기반 4가지 개선 (진입차단 완화 + mongtata 필터 + breakout_120d 강화 + 신호임계값)**

---

### 최종 상태 (세션4 파트2 종료)
```
allow_new_entries: True  ✅ (pending order 해소로 복구)
pending live orders: 0건 ✅
봇 오픈 포지션: 420770 기가비스 +5.34%, 036930 주성엔지 +3.11% (모두 kis_hold)
regime=RANGING, stance=BALANCED, risk_budget=0.18
내일(06-11) 오전 장 오픈 시 신규 전략 가동 예정
```

### 세션4 파트2 회고 결과 및 개선

#### 진단된 문제
| 문제 | 원인 | 영향 |
|---|---|---|
| allow_new_entries=0 | Korea floor -1.5%, 오늘 청산으로 combined_pnl << -1.5% | 종일 진입 차단 |
| mongtata 추세하락 진입 | deviation -18%, -12% 종목에도 진입 시도 | 손실 리스크 |
| breakout_120d 미발동 | 모두 0.20x selective_probe, 강돌파도 동일 처리 | 기회 미활용 |
| new_high_breakout 되돌림 손절 | signal_score 0.50이면 진입 → 약한 신호 포함 | rapid_korea_stop 남발 |

#### [A] allow_new_entries floor 완화
- `orchestrator.py` + `risk_committee_agent.py`: `-1.5%` → `-4.0%`
- 포지션 정리일의 paper PnL sum이 threshold를 건드리지 않도록

#### [B] mongtata_airborne 필터 + 사이즈
- `recommendation_engine.py`: `deviation_pct >= -12.0%` 필터
- 삼성SDI(-18%), HD현대(-12%) 같은 추세하락 차단
- 사이즈: 0.35x → 0.25x

#### [C] breakout_120d 강돌파 → probe_longs 0.30x
- `breakout_pct >= 5%`: `probe_longs 0.30x` (기존 모두 `selective_probe 0.20x`)
- 120일 신고가 5%+ 돌파 시 비중 확대

#### [D] new_high_breakout signal_score 강화
- `0.50 → 0.65` (confirmed), `0.52 → 0.65` (partial)
- 약한 신호 필터링 → 갭 되돌림 rapid_korea_stop 감소

---

## 이전: Claude - 2026-06-10 세션4 (전략 5종 개선 + 봇 재배포)

### 커밋 순서 (세션4)
1. `80de8e9` — **S27/S29 신규전략 + sector_wave 사이즈 보정 + EMA200 완화 + RANGING pre_gap 차단**

---

### 세션4에서 구현한 5가지 개선

#### [1] pre_gap_watch RANGING 진입 차단
- `recommendation_engine.py`: `regime not in {"RANGING","STRESSED"}` 가드 추가
- RANGING 장에서 갭 모멘텀 오신호 방지

#### [2] sector_wave stop_pressure 억제 해제
- `execution_agent.py`: `_is_sector_wave` → `stop_pressure_scale=1.0`
- 0.15x size floor 추가 (stop_pressure != "high" 조건)
- 기존: base 0.25x × risk_budget 0.18 × stop_pressure 0.5 × cold_streak 0.5 ≈ 0.01x → 정상화

#### [3] EMA200 필터 완화
- `korea_stock_desk_agent.py`: `closes[-1] <= ema200 * 0.95` (기존 `<= ema200`)
- RANGING 장에서 EMA200 근처 종목도 mongtata/RSI2 후보 포함

#### [4] S27: 볼린저 밴드 스퀴즈 → 상향 돌파
- `korea_stock_desk_agent.py`: `bb_width_ratio < 4%` 스퀴즈 감지 + upper_bb 돌파
- `recommendation_engine.py`: `bb_squeeze` 전략 블록 (OFFENSE: 0.35x, 그외: 0.25x)
- `state_store.py`: `candidate_keys`에 `bb_squeeze_candidates` 등록

#### [5] S29: 거래량 폭발 + 가격 횡보 (세력 집결)
- `korea_stock_desk_agent.py`: `vol_ratio ≥ 5.0x` + `price_chg < 2%` 스캐너
- `recommendation_engine.py`: `volume_surge` 전략 블록 (0.20x, STRESSED 제외)
- `state_store.py`: `candidate_keys`에 `volume_surge_candidates` 등록

---

## 이전: Claude - 2026-06-09 세션3 (성과 진단 + 3중 구조 수정)

### 커밋 순서 (세션3)
1. `9948f77` — **stop-cut→KIS sell 연동 + RANGING 진입 차단 + VTS 청산 크론**

### 커밋 순서 (세션2)
1. `906f5ff` — allow_new_entries 복원 + S26 전략 추가
2. `6cf82d3` — execution_agent 포지션 슬롯 kis_hold 제외
3. `2cdc830` — circuit_breaker daily_pnl kis_hold 제외
4. `d85b5f7` — NAVER 루프 버그 감지 (>10건 동일 종목) realized_pnl 오염 방지
5. `df3c38f` — kis_hold 포지션에 trail/stop 완전 미적용

---

### 세션3에서 해결한 문제들

#### 성과 진단 결과
- `new_high_breakout` 7일 성과: 24건 승률 41%, 합산 -17.1%, 최악 -20.23%
- **구조적 버그**: 봇이 KIS 매수는 실행하지만 paper stop-cut 시 KIS 매도 미발송 → VTS에 손실 포지션 누적
- 307950: 6월 1일 봇 매수 → -1.85%에 paper stop → KIS 안 팔림 → -28%까지 누적

#### Fix 1: pre_gap_watch RANGING/STRESSED 진입 차단
- `recommendation_engine.py` line 443
- `regime not in {"RANGING", "STRESSED"}` 조건 추가
- `new_high_breakout`은 이미 `_b_regime_ok` 가드 존재 ✅

#### Fix 2: paper stop-cut → KIS VTS 자동 매도 연동 (핵심)
- `state_store._close_position` 에 `_send_kis_sell_async()` 추가
- `shadow(PositionRecord)`가 있을 때만 발동 (KIS에 실제 포지션 있는 경우)
- kis_hold / kis_manual_sync 프로필 제외 (이미 KIS에 있던 기존 포지션)
- background thread, fire-and-forget, 장중이 아니면 KIS가 거부 → 로그만 남김

#### Fix 3: VTS 물린 포지션 자동 청산
- `sell_stuck_vts.py` 생성 (307950 + 069500 + 035420 전량 시장가)
- 크론: `5 0 10 6 *` = 2026-06-10 09:05 KST
- 성공 시 paper_position 자동 closed + 크론 삭제

---

### 현재 상태 (세션 종료 시점)

```
allow_new_entries = True  ✅
regime = RANGING
봇 PID: 3674929 (systemd trading-loop.service, 재시작됨)
```

**Open paper positions (5개, 모두 kis_hold):**
| 종목 | profile | pnl | 비고 |
|---|---|---|---|
| 035420 NAVER | kis_hold | -7.15% | 09:05 KST 자동 매도 예정 |
| 420770 (반도체장비) | kis_hold | +0.35% | 유지 |
| 036930 주성엔지니어링 | kis_hold | -0.98% | 유지 (사용자 15주 보유 확인) |
| 307950 | kis_hold | -27.99% | 09:05 KST 자동 매도 예정 |
| 069500 KODEX200 | kis_hold | -7.35% | 09:05 KST 자동 매도 예정 |

**봇 open positions: 0개** (현재 신규 진입 없음, RANGING 레짐)

---

### 세션4 현재 상태 (종료 시점)

```
봇 PID: 3900243 (재시작됨 02:31 UTC)
커밋: 80de8e9
신규 전략: S27(bb_squeeze), S29(volume_surge) 활성
regime = RANGING, stance = BALANCED, risk_budget = 0.18
```

**남은 kis_hold 포지션 (307950/069500/035420 청산 완료):**
| 종목 | profile | 비고 |
|---|---|---|
| 035420 NAVER | kis_hold | -7.15% 보유 중, 익절 대기 |
| 420770 (반도체장비) | kis_hold | +0.35% 유지 |
| 036930 주성엔지니어링 | kis_hold | 사용자 15주 실계좌 보유 |

### 잔여 이슈

1. **NAVER 익절 대기**: VTS에서 -7.15%, 수익 회복 시 수동 매도
2. **S27/S29 실전 확인**: 장중 bb_squeeze_candidates / volume_surge_candidates 신호 로그 확인
   - `journalctl -u trading-loop.service -f | grep -E "bb_squeeze|volume_surge"`
3. **Fix 2 검증**: 다음 stop-cut 발생 시 KIS 자동 매도 로그 확인
   - `journalctl -u trading-loop.service | grep "KIS auto-sell"`

---

### 아키텍처 정리 (현재)

```
봇 신규 매수
  → recommendation_engine (sector_wave / S26 / near_120d)
  → [RANGING이면 new_high_breakout / pre_gap_watch 차단됨 ✅]
  → execution_agent → place_order → KIS VTS 매수 체결
  → paper position 생성 (entry_profile=strategy 이름)

봇 stop-cut (Fix 2 이후)
  → state_store._close_position → paper position 닫힘
  → shadow(PositionRecord) 있으면 → _send_kis_sell_async() → KIS VTS 매도 주문 ✅

KIS 실계좌 보유 종목 (사용자가 직접 산 것)
  → KIS sync → positions 테이블 → kis_hold paper position 생성
  → 봇 trail/stop 미적용 (df3c38f)
  → 사용자가 KIS 앱에서 직접 매도 or sell_stuck_vts.py 스크립트
```

---

### 코드 수정 금지 사항
- "아예 막거나 제한하는 방향으로 가면 안돼" — 블랙리스트/영구 진입 제한 금지
- KIS credentials (app_key, app_secret) 출력 금지
- `.env` 파일 직접 cat 금지

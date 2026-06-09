# Trading Company V2 Handoff

## 최신: Claude - 2026-06-09 세션3 (성과 진단 + 3중 구조 수정)

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

### 잔여 이슈

1. **VTS 자동 매도 확인**: 2026-06-10 09:05 KST에 `sell_stuck_vts.py` 실행 여부 확인
   - 로그: `/home/ubuntu/trading-bot/trading_company_v2/logs/sell_stuck_vts.log`
2. **420770 probe_longs**: 모의투자 24주 보유 중, 추적 필요
3. **Fix 2 검증**: 다음 stop-cut 발생 시 KIS 자동 매도가 실제로 실행되는지 로그 확인

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

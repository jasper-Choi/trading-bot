# Trading Company V2 Handoff

## 최신: Claude - 2026-06-09 세션2 (KIS hold 루프 버그 완전 수정)

### 커밋 순서
1. `906f5ff` — allow_new_entries 복원 + S26 전략 추가
2. `6cf82d3` — execution_agent 포지션 슬롯 kis_hold 제외
3. `2cdc830` — circuit_breaker daily_pnl kis_hold 제외
4. `d85b5f7` — NAVER 루프 버그 감지 (>10건 동일 종목) realized_pnl 오염 방지
5. `df3c38f` — **kis_hold 포지션에 trail/stop 완전 미적용 (핵심 버그 수정)**

---

### 이번 세션에서 해결한 문제들

#### 1. HPSP/유진테크 미진입 근본 원인 3중 차단 해소

| 차단 원인 | 수정 내용 | 커밋 |
|---|---|---|
| KIS hold 미실현 손실 → combined_pnl < -1.5% → allow_new_entries=False | `_build_desk_stats`에서 kis_hold 제외 | 906f5ff |
| kis_hold 3개가 포지션 cap=3 점유 | `_desk_open_count` 등에서 kis_hold 제외 | 6cf82d3 |
| NAVER 버그 52건 → circuit_breaker daily_pnl=-129.6% | kis_hold + 루프버그 종목(>10건) 제외 | 2cdc830, d85b5f7 |

#### 2. NAVER 루프 버그 (핵심 수정 — df3c38f)
- **원인**: `state_store` 포지션 청산 루프에서 kis_hold 체크 없음
  - KIS sync → kis_hold paper position 생성
  - trail/stop(-5%) 발동 → rapid_korea_trail 청산
  - 다음 사이클 KIS sync → 다시 생성 → 무한 루프
- **수정**: `for position in open_positions` 루프에서 `"kis_hold" in entry_profile`이면 `continue`
- **효과**: KIS hold는 KIS에서 직접 매도해야만 닫힘 (봇 trail/stop 미적용)

#### 3. 036930(주성엔지니어링) live lock 해소
- `partial_balance_sync` lock id=1068 → `settled`, `broker_live=False`
- 15주 KIS 실계좌 보유 확인됨 (사용자 확인)

#### 4. S26 gap_near_120d + sector_wave 강화
- sector_wave: selective_probe(0.20x) → probe_longs(0.25x)
- S26: near_120d 종목이 당일 gap≥10%이면 업그레이드

---

### 현재 상태 (세션 종료 시점)

```
allow_new_entries = True  ✅
regime = RANGING
risk_budget = 0.25
봇 PID: 3645424 (systemd trading-loop.service)
최근 진입: 420770 (반도체장비 섹터, sector_wave, 모의투자)
```

**Open paper positions (5개):**
| 종목 | profile | pnl |
|---|---|---|
| 035420 NAVER | kis_hold | -7.7% (KIS 실계좌 보유 — 직접 매도 필요) |
| 420770 (반도체장비) | kis_hold | -0.2% |
| 036930 주성엔지니어링 | kis_hold | -0.5% (15주 KIS 보유) |
| 307950 | kis_hold | -28.5% |
| 069500 KODEX200 | kis_hold | -7.8% |

---

### 잔여 이슈 (다음 세션 확인 필요)

1. **NAVER KIS 실계좌**: 사용자가 KIS 앱에서 직접 매도해야 함. 매도 후 봇 positions 테이블 자동 업데이트
2. **307950 (-28.5%)**: 큰 미실현 손실 — 이 종목 확인 필요
3. **420770 probe_longs**: 모의투자 24주 매수 상태, 진행 추적 필요
4. **KIS 실거래 연결**: paper_position이 trail/stop으로 닫힐 때 KIS 실거래 매도 주문 미발송 (구조적 미구현)

---

### 아키텍처 정리

```
KIS 실계좌 보유 종목
  → ops_agent KIS sync → positions 테이블 → kis_hold paper position 생성
  → 봇 trail/stop 미적용 (df3c38f 이후)
  → 사용자가 KIS 앱에서 직접 매도 시 → 다음 sync에 positions 테이블에서 제거

봇 trading
  → recommendation_engine (sector_wave / S26 / near_120d)
  → execution_agent (paper order → KIS 모의투자 주문)
  → state_store trail/stop 관리 (kis_hold 제외)
```

---

### 코드 수정 금지 사항
- "아예 막거나 제한하는 방향으로 가면 안돼" — 블랙리스트/영구 진입 제한 금지
- KIS credentials (app_key, app_secret) 출력 금지
- `.env` 파일 직접 cat 금지

# HANDOFF — Trading Company V2

> 이 파일은 Claude / Codex 세션이 끝날 때마다 업데이트됩니다.
> 새 세션을 시작하면 이 파일을 먼저 읽어 이전 상태를 파악하세요.

---

## 마지막 업데이트

- **날짜**: 2026-04-21 (2차)
- **작업자**: Claude (Sonnet 4.6)

---

## 이번 세션에서 완료한 것

### 1. 포지션 추적 시스템 (영구 누적)
- `positions` 테이블: BUY 액션 시 진입가 기록, 중복 오픈 방지
- `closed_positions` 테이블: 청산 시 실현 P&L 영구 보존 (날짜 리셋 없음)
- 매 사이클 현재 시세로 미실현 P&L 자동 갱신 (`update_positions_unrealized`)
- 기존 `PaperPositionRecord` (세션별 추적)와 공존 — 역할이 다름

### 2. 올타임 복리 P&L 대시보드
- `load_performance_quick_stats()`: 전체 기간 복리 수익률, 승률, MDD 계산
- `/performance` API 엔드포인트 추가
- `CompanyState.performance_stats` 필드로 대시보드에 실시간 노출

### 3. 백테스트 기반 심볼 가중치
- `app/services/backtest_advisor.py` 신규 생성
- `coin_result_v4.json` 읽어 Sharpe×0.5 + 승률×0.3 + MDD×0.2 스코어 계산
- **현재 가중치**: KRW-ETH(0.43) > KRW-XRP(0.32) > KRW-BTC(0.24), SOL 제외
- `CryptoDeskAgent`가 BTC 고정 대신 ETH를 리드 마켓으로 분석
- `StrategyAllocatorAgent` payload에 `crypto_weights` 포함

### 4. main 브랜치 정리
- 다른 Codex 세션의 미커밋 작업(US 데스크, 인증, 라이브 대시보드 등) 커밋
- `claude/friendly-mestorf-e52784` 브랜치 main에 머지 완료
- `.claude/` 디렉토리 `.gitignore`에 추가

---

## 현재 상태 (main 브랜치 기준)

### 아키텍처
```
trading_company_v2/
├── app/
│   ├── agents/           # 11개 에이전트 (+ USStockDeskAgent)
│   ├── core/
│   │   ├── models.py     # Position, ClosedPosition 모델 추가됨
│   │   └── state_store.py # PaperPositionRecord + PositionRecord + ClosedPositionRecord
│   ├── services/
│   │   └── backtest_advisor.py  # NEW: 코인 백테스트 가중치
│   ├── main.py           # /performance 엔드포인트 추가, 한국어 라이브 대시보드
│   ├── orchestrator.py   # sync_paper_positions + _manage_positions 둘 다 실행
│   └── service_manager.py # 백그라운드 서비스 관리 (PID 파일)
└── launch_trading_app.vbs  # 더블클릭 → 백그라운드 실행 + 브라우저 오픈
```

### DB 테이블 (SQLite: data/trading_company_v2.db)
| 테이블 | 용도 | 리셋 여부 |
|---|---|---|
| `company_state` | 현재 stance/regime/전략북 | 없음 (upsert) |
| `paper_orders` | 페이퍼 주문 이력 | 없음 (append) |
| `cycle_journal` | 사이클별 저널 | 없음 (append) |
| `paper_positions` | 세션별 포지션 (PaperPositionRecord) | 없음 |
| `positions` | 올타임 오픈 포지션 (PositionRecord) | 없음 |
| `closed_positions` | 올타임 청산 이력 (ClosedPositionRecord) | 없음 |

### API 엔드포인트
| 경로 | 방법 | 설명 |
|---|---|---|
| `/` | GET | 한국어 라이브 대시보드 |
| `/health` | GET | 서비스 상태 |
| `/state` | GET | 전체 상태 JSON |
| `/dashboard-data` | GET | 대시보드용 JSON |
| `/cycle` | POST | 사이클 1회 수동 실행 |
| `/performance` | GET | 올타임 포지션 + P&L 통계 |
| `/telegram-test` | POST | 텔레그램 수동 테스트 |

### 실행 방법
```
launch_trading_app.vbs  ← 더블클릭으로 전체 실행
```
또는 개별 실행:
- Dashboard: `run_local.bat`
- 15분 루프: `run_company_loop.bat`

---

## 다음에 할 작업 (우선순위 순)

### 🔴 HIGH — 실전 전환 준비
1. ~~**올타임 P&L 대시보드 카드 추가**~~ ✅ 완료 (2026-04-21)
   - `metrics-strip`에 누적 복리 수익률 / 올타임 승률 / MDD 카드 3개 추가
   - commit: `b99c14a`

2. **실제 브로커 연동 준비 (Upbit)**
   - `ExecutionAgent`가 현재 paper only
   - Upbit API 키 환경변수 설정 → 실주문 라우팅 로직 추가
   - `.env.example`에 `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY` 추가

3. **KIS (한국투자증권) 연동**
   - Korea 데스크 실주문 연결
   - KIS API 토큰 관리 (만료 처리 포함)

### 🟡 MEDIUM — 정확도 향상
4. **미국 주식 백테스트 결과 생성 및 연동**
   - `USStockDeskAgent`는 구현됐으나 백테스트 가중치 없음
   - `Desktop/backtest/stock_backtest.py`에 US 종목 추가 or 별도 `us_backtest.py` 작성
   - `backtest_advisor.py`에 `get_us_weights()` 추가 → `USStockDeskAgent` 연동

5. **한국 주식 백테스트 결과 연동**
   - `Desktop/backtest/stock_result.json` 없음 → `stock_backtest.py` 실행 후 생성
   - `KoreaStockDeskAgent`에 백테스트 가중치 적용

6. **코인 백테스트 결과 갱신 자동화**
   - `backtest_advisor.py`가 자동으로 최신 버전(v1→v4 중 가장 높은 번호) 선택하도록 개선

7. **Tailscale 외부 접속 설정**
   - README에 TODO로 명시됨
   - 홈 PC에서 모바일로 대시보드 접근

### 🟢 LOW — 품질 개선
8. **포지션 추적 고도화**
   - `PositionRecord` (올타임)와 `PaperPositionRecord` (세션) 통합 검토
   - 현재 두 시스템이 병렬 운영 중 — 중복 여부 확인

---

## 주의사항 / 알려진 이슈

1. **두 가지 포지션 시스템 병렬 운영**
   - `sync_paper_positions` → `paper_positions` 테이블 (세션 단위, 더 정교함)
   - `_manage_positions` → `positions` + `closed_positions` 테이블 (올타임 복리 용도)
   - 충돌 없이 공존하지만, 나중에 통합 검토 필요

2. **백테스트 파일 경로**
   - `backtest_advisor.py`가 `~/Desktop/backtest/coin_result_v4.json` 읽음
   - 홈 PC로 이전 시 해당 경로에 파일 복사 필요

3. **US 데스크 데이터**
   - `USStockDeskAgent` 추가됐으나 무료 데이터 소스 의존
   - `get_us_data_status()` 체크로 fallback 처리됨

4. **미커밋 상태로 발견된 대규모 변경**
   - 이번 세션에서 다른 Codex 세션의 미커밋 변경(3600줄+)을 커밋함
   - 커밋: `23cf5b5 feat: live dashboard, US desk, auth, service manager, position tracking v1`

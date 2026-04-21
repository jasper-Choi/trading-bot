# HANDOFF — Trading Company V2

> 이 파일은 Claude / Codex 세션이 끝날 때마다 업데이트됩니다.
> 새 세션을 시작하면 이 파일을 먼저 읽어 이전 상태를 파악하세요.

---

## 마지막 업데이트

- **날짜**: 2026-04-21 (3차)
- **작업자**: Claude (Sonnet 4.6)

---

## 이번 세션에서 완료한 것

### 0.8 내장 대시보드 다크 네이비 리디자인 (commit: `17c3db7`)
- `app/main.py` `root()` 함수 HTML을 React 프론트엔드와 동일한 시각 언어로 전면 교체
- **CSS 디자인 토큰**: `--bg:#09111f`, `--surface:rgba(10,19,35,.84)`, `--green:#67e8a5`, `--blue:#6bc7ff` 등 React `index.css` 변수 그대로 이식
- **새 레이아웃**:
  - `.hero-shell`: 회사명 + 상태 pill + 사이클 버튼 헤더
  - `.hero-overview`: Stance / Regime / Exposure / Ops 4개 카드
  - `.dashboard` 2컬럼 그리드:
    - 좌: stat-card ×4 (실현/미실현 손익, 승률, 포트폴리오) + 데스크 현황 + 포지션 테이블 + 청산 내역
    - 우: SVG 에쿼티 커브 + 에이전트 시그널 바 + 사이클 저널
- **tone 클래스**: `.tone-ok/.warn/.risk/.danger/.muted/.blue` — 상태별 색상 자동 반영
- **반응형**: 960px → 1컬럼, 600px → 컴팩트 스택
- manifest `background_color`, `theme_color` → `#09111f` 업데이트
- 앱 아이콘 SVG → 다크 배경 + 그린/블루 강조색으로 업데이트

### 0. 올타임 자동청산/청산이력 정합성 보강
- `auto_exit_positions()`가 desk/action별 기존 포지션 임계값을 재사용하도록 정리
- `closed_positions`에 `closed_reason` 저장 추가 (`target_hit`, `stop_hit`, `time_exit`, `desk_exit`)
- 기존 SQLite DB도 `init_db()` 시 `closed_reason` 컬럼을 자동 추가하도록 보강
- `load_closed_positions()`가 대시보드/ExecutionAgent가 바로 읽을 수 있는 dict 포맷으로 정리되어, 청산 이력이 쌓여도 재진입 차단 로직이 깨지지 않음

### 0.1 실행 계층 분리 시작
- `EXECUTION_MODE` 환경변수 추가 (`paper`, `upbit_live`, `kis_live`)
- `app/services/broker_router.py` 추가: requested/applied execution mode 분리
- 현재는 live 모드 요청 시 자격증명 누락 또는 미구현 상태면 자동으로 `paper` fallback
- 전략 결정은 계속 `ExecutionAgent`가 하고, 브로커 라우팅 판단은 별도 계층에서 처리

### 0.2 Upbit live 어댑터 1차
- `app/services/upbit_broker.py` 추가
- 공식 Upbit 인증 방식에 맞춰 `HS512 JWT` + `query_hash` 기반 private API 서명 구현
- 현재 지원 범위는 `crypto` 데스크의 진입 주문만 대상
  - `LIVE_CAPITAL_KRW * order.notional_pct`를 KRW market buy 금액으로 변환
- 추가 안전장치:
  - `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY` 필요
  - `LIVE_CAPITAL_KRW > 0` 필요
  - `UPBIT_ALLOW_LIVE=true`가 아니면 키가 있어도 실제 주문 전송 없이 `paper fallback`
- `/health`에 execution 설정/자격증명 존재 여부 노출

### 0.3 Upbit 청산 경로 연결
- `ExecutionAgent`가 `reduce_risk`, `capital_preservation`이고 실제 오픈 포지션이 있으면 `status=planned`로 내리도록 수정
- `upbit_broker.py`에 `/v1/accounts` 기반 잔고 조회 추가
- `KRW-BTC` 같은 심볼에서 base asset(`BTC`) 잔고를 읽어 market sell payload 생성 가능
- 현재는 `crypto` 데스크만 Upbit live 대상이며, 한국/미국 주식 desk 주문은 계속 unsupported fallback

### 0.4 Live order ledger 추가
- `live_order_log` 테이블 추가
- 저장 항목:
  - `requested_mode`, `applied_mode`, `broker_live`
  - `request_status`, `broker_order_id(uuid)`, `broker_state`
  - `reason`, `message`, 원본 `payload`
- `save_live_order_attempts()`가 broker router 결과를 DB에 저장
- `load_recent_execution_log()`가 paper 주문 로그 + live 실행 로그를 합쳐 최근 실행 히스토리로 노출
- `CompanyState.execution_log`가 이제 merged execution log를 사용

### 0.5 Upbit 실잔고 -> positions 동기화
- `upbit_broker.py`
  - `get_account_positions()` 추가: `/v1/accounts` 응답을 `market`, `total_volume`, `avg_buy_price` 중심으로 정규화
  - `get_ticker_prices()` 추가: 보유 중인데 market snapshot에 없는 코인 가격 보강
- `state_store.py`
  - `sync_live_crypto_positions()` 추가
  - Upbit 실잔고에 없는 기존 crypto 포지션은 `closed_positions`로 이동 (`closed_reason='broker_sync_exit'`)
  - Upbit 실잔고에 있는 코인은 `positions`에 생성/업데이트 (`action='live_sync'`)
- `orchestrator.py`
  - `broker_live` + `execution_mode == 'upbit_live'`인 경우 crypto desk는 broker sync 기준으로 동작
  - 이 경우 `_manage_positions()`는 `crypto` desk를 건너뛰고, 한국/미국 desk만 기존 paper 포지션 로직 사용
- 아직 남은 일:

### 0.6 Live 주문 상태 후속 조회
- `upbit_broker.py`
  - `normalize_order_state()` 추가
  - Upbit `/v1/order` 응답을 `submitted`, `partial`, `filled`, `cancelled`로 정규화
- `state_store.py`
  - `refresh_live_order_statuses()` 추가
  - `live_order_log`에서 `submitted` / `partial` 레코드를 다시 조회해 상태 갱신
- `orchestrator.py`
  - `execution_mode == 'upbit_live'`일 때 cycle 중 최근 live 주문 상태 refresh 수행
  - note에 `checked/updated/failed` 요약 기록
- 아직 남은 일:

### 0.7 Live 주문 결과 -> 포지션 반영 상태 연결
- `live_order_log`에 아래 컬럼 추가
  - `effect_status`
  - `linked_position_symbol`
  - `linked_closed_symbol`
- `state_store.py`
  - `reconcile_live_order_effects()` 추가
  - `filled` 진입 주문:
    - 해당 심볼 오픈 포지션이 아직 없으면 `awaiting_balance_sync`
    - broker sync 이후 오픈 포지션이 있으면 `linked_open`
  - `filled` 청산 주문:
    - 해당 심볼 오픈 포지션이 남아 있으면 `closed_positions`로 이동하고 `closed_reason='broker_order_fill'`
    - 이미 없어졌으면 `already_reconciled`
  - `cancelled` 주문은 `cancelled_no_fill`
- `orchestrator.py`
  - Upbit live crypto sync 이후 `reconcile_live_order_effects()` 실행
  - note에 `checked/updated` 요약 기록
- 아직 남은 일:
  - `linked_*_symbol`까지만 저장하고, 주문 UUID와 개별 closed row id를 직접 연결하지 않음
  - 부분체결(`partial`)에서 잔량/실체결량 기준 포지션 수량, notional, 평균단가 재산정은 아직 거칠다
  - `broker_sync_exit`와 `broker_order_fill` 관계를 더 정교하게 정리할 필요 있음

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
1. ~~**내장 대시보드 리디자인**~~ ✅ 완료 (2026-04-21)
   - `app/main.py` 다크 네이비 React 시각 언어로 전면 교체
   - commit: `17c3db7`

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

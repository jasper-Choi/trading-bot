# 세션 작업 요약 — 2026-04-28

## 현재 git 상태

- 브랜치: `main`
- 최신 커밋: `a2f0a5e` (원격 동기화 완료)
- Oracle VM: 최신 코드 배포 완료, 서비스 active

---

## 이번 세션에서 한 작업

### 1. 크립토 유니버스 확장 (`b8ea393`)
- **문제**: DOGE/XRP 2종목만 거래 (backtest 파일 없을 때 neutral weight가 2개뿐)
- **수정**: `backtest_advisor.py` neutral weights → BTC/ETH/XRP/SOL/DOGE/ADA/AVAX/TRX/LINK 9종목
- **수정**: `crypto_desk_agent.py` 최대 4종 → 6종 병렬 평가

### 2. 복리자본 추적 (`b8ea393`)
- **문제**: 매일 새 데이터로 거래 시 전날 수익/손실이 반영 안 됨
- **수정**: `state_store.load_daily_summary()` → 전체 기간 누적 PnL 계산
- **수정**: `effective_capital = base * (1 + cumulative_pnl / 100)`
- **수정**: 대시보드에 `복리자본` 라벨로 표시

### 3. 모바일 인증 버그 (`d2bbc39` — 이전 세션)
- **문제**: 모바일 브라우저에서 JS fetch() 호출 시 Basic Auth 미전달 → 401 루프
- **수정**: 최초 인증 성공 시 HttpOnly 세션 쿠키 발급, 이후 fetch()는 쿠키로 인증

### 4. 포지션 PnL 표시 버그 (`b8ea393`)
- **문제**: renderPositions JS가 `p.unrealized_pnl_pct` 읽었으나 API는 `p.pnl_pct` 반환 → 항상 0%
- **수정**: `p.pnl_pct || p.unrealized_pnl_pct`로 수정

### 5. 가중치 게이트가 모든 진입 차단 (`da7e1e9`) ← 핵심 버그
- **문제**: `build_crypto_plan()`의 `lead_weight >= 0.15/0.20` 조건이 9종목 neutral weight (최대 0.14) 환경에서 항상 실패 → 신호 점수 0.82여도 `watchlist_only` 반환
- **수정**: `recommendation_engine.py` 임계값 → 0.08/0.10으로 하향

### 6. 동시 다종목 거래 (`da7e1e9`) ← 핵심 기능
- **문제**: `ExecutionAgent.run()`이 데스크당 정확히 1개 주문만 생성 (`_desk_limits`는 3 허용)
- **수정**: `ExecutionAgent._multi_orders()` 추가:
  - `slots = max_positions - desk_open_count` 만큼 후보 순환
  - 기본 size를 slots 수로 균등 분할
  - 포지션 중복 방지 및 cap 체크 유지
- **결과**: 크립토 최대 3종목 동시 보유 가능

### 7. 크립토 가격 조회 누락 (`98ddbf1`) ← 핵심 버그
- **문제**: `market_gateway._PINNED_CRYPTO`에 BTC/ETH/XRP/SOL/DOGE만 포함 → ADA/AVAX/TRX/LINK는 Upbit 거래량 상위 20위 밖이면 가격 없음 → `entry_price=0`으로 포지션 미개설
- **수정**: `_PINNED_CRYPTO` → 9종목 전체 포함

---

## 현재 Oracle VM 상태 (2026-04-28 00:12 UTC 기준)

| 포지션 | 진입가 | 현재 PnL |
|--------|--------|---------|
| KRW-ETH (크립토) | 3,433,000 | +0.09% |
| KRW-AVAX (크립토) | 13,790 | 0.0% |
| KRW-ADA (크립토) | 369 | 0.0% |

- 3종목 동시 보유 확인 ✅
- `EXECUTION_MODE=upbit_live`, `UPBIT_PILOT_SINGLE_ORDER_ONLY=true`
- 라이브 주문 실패 시 페이퍼 포지션으로 fallback 작동 중

---

## 남은 작업 (우선순위 순)

### 🔴 높음

#### 1. stale_exit 반복 — 수익 실현 불가
- **현상**: 지금까지 전체 7포지션 중 7개 모두 `stale_exit` (목표/손절 미달로 타임아웃)
- **문제**: +6%/−3% 목표/손절이 실제 변동폭 대비 너무 넓음
- **검토 필요**: `state_store.auto_exit_positions()` 의 목표/손절 임계값 재검토
  - 크립토: 현재 +6%/−3% → +3%/−1.5% 정도로 축소 검토
  - 한국주식: 현재 임계값 확인 후 검토
- **또는**: 타임아웃 사이클 수를 줄여 기회비용 감소

#### 2. 라이브 주문 400 에러 해결
- **현상**: Upbit API에 주문 시 `400 Client Error: Bad Request` 반복
- **결과**: 라이브 주문 실패 → 페이퍼 fallback으로만 포지션 생성 (실제 매매 안 됨)
- **확인 필요**: `upbit_broker.py` 주문 파라미터 검토 (price, volume, ord_type 등)
- **확인 필요**: Upbit API 계정 권한, IP whitelist 등

#### 3. Korea/US 동시 다종목 — 한국 주식
- 크립토 동시 3종목 해결됨. 한국 주식도 갭 후보 최대 3종목 동시 보유 검토
- `build_korea_plan()` 현재 단일 종목만 추천 → `candidate_symbols` 리스트 전달 확인

### 🟡 중간

#### 4. `UPBIT_PILOT_SINGLE_ORDER_ONLY` 해제 검토
- 현재 사이클당 라이브 주문 1건 제한 (안전장치)
- 동시 다종목이 안정화되면 → `false`로 변경해서 한 사이클에 2-3종목 동시 진입 가능
- 단, 라이브 주문 400 에러가 먼저 해결되어야 의미 있음

#### 5. KIS 연동 (한국 주식 실전)
- `.env`에 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO` 미등록
- 등록 후 `EXECUTION_MODE=kis_live`로 전환하면 한국 주식 실전 거래 시작 가능

#### 6. 청산 내역 탭 표시 확인
- 이전 세션에서 클로즈된 포지션이 하단 탭에 안 보인다고 보고됨
- `load_closed_positions(limit=20)`은 날짜 필터 없으나 UI에서 필드 매핑 재확인 필요
- `renderTrades` JS 함수 점검

#### 7. 미국 주식 진입 조건 지나치게 엄격
- `build_us_plan()` 조건: quality >= 0.76, avg_change >= 0.55, avg_volume >= 2,000,000 등
- 현재 NVDA만 반복 표시되고 항상 `pre_market_watch` → 조건 완화 검토

### 🟢 낮음

#### 8. backtest 파일 Oracle VM 배포
- `~/Desktop/backtest/coin_result_v5.json` 없어서 neutral weights 사용 중
- 로컬에서 backtest 실행 후 VM에 SCP 업로드하면 실제 성과 기반 가중치 사용 가능
- `coin_backtest.py` 재실행 → 결과 파일을 VM에 업로드

#### 9. 텔레그램 알림 개선
- 현재 기본 알림은 작동 중
- 포지션 진입/청산 시 개별 알림 강화 검토

#### 10. 대시보드 UI 개선
- 복리자본, 누적 PnL 등 새 필드가 대시보드에 정상 표시되는지 모바일에서 최종 확인
- 포지션 PnL 색상 표시 등 UX 마무리

---

## 파일 구조 (주요 수정 파일)

```
trading_company_v2/
├── app/
│   ├── agents/
│   │   ├── crypto_desk_agent.py        # 병렬 6종목 평가
│   │   └── execution_agent.py          # _multi_orders() 추가
│   ├── services/
│   │   ├── recommendation_engine.py    # weight 임계값 하향
│   │   ├── market_gateway.py           # _PINNED_CRYPTO 9종목
│   │   └── backtest_advisor.py         # neutral weights 9종목
│   ├── core/
│   │   └── state_store.py              # 복리자본 누적 PnL
│   └── main.py                         # 대시보드 복리자본 표시, 세션쿠키 인증
├── HANDOFF.md                          # Claude/Codex 핸드오프 문서
└── SESSION_NOTES.md                    # 이 파일
```

---

## 다음 작업자를 위한 SSH 접속

```bash
ssh -i "C:/Users/User/Desktop/trading-bot/trading_company_v2/오라클 SSH키/ssh-key-2026-04-21.key" ubuntu@134.185.118.144
```

서비스 재시작:
```bash
cd ~/trading-bot && git pull origin main && sudo systemctl restart trading-loop trading-dashboard
```

DB 포지션 확인:
```bash
sqlite3 ~/trading-bot/trading_company_v2/data/trading_company_v2.db \
  "SELECT desk, symbol, status, pnl_pct, opened_at FROM paper_positions ORDER BY opened_at DESC LIMIT 10"
```

# Trading Company V2 Handoff

Last updated: 2026-04-29
Maintained for: Claude / Codex continuation

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

# Trading Company V2 Handoff

Last updated: 2026-04-23
Maintained for: Claude / Codex continuation

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

## 8. Suggested next work

Priority order:

1. **AI 판단 이력 확인** — Oracle VM에서 `agent_log`가 실제 사이클 데이터를 보여주는지 확인 (`:8080/` 대시보드 또는 `/dashboard-data` JSON에서 `dashboard.agent_log` 배열 확인)
2. **Korea/US threshold 완화 효과 확인** — 새 single-gap 진입 tier가 실제로 작동하는지 대시보드에서 확인 (초록색/노란색 Korea/US 데스크 row 확인)
3. **tiny-size 첫 주문 체결 확인** — signal이 trigger 돌파 시 ₩150,000 한도 단일 주문 정상 체결 확인 (arming/ready Telegram 알림으로 미리 감지)
4. **자본 확대** — tiny-size 검증 완료 후 UPBIT_PILOT_MAX_KRW 상향 / SINGLE_ORDER_ONLY 해제
5. **KIS 실전 전환** — Oracle VM `.env`에 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO / KIS_PRODUCT_CODE 등록 후 KIS_ALLOW_LIVE=true → `/diagnostics/kis-live-pilot` 확인

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
  - current opening-drive rules still produce only `7` total trades over the tested universe
  - conclusion: Korea strategy is not blocked by infra anymore; it needs wider universe / parameter redesign

### Next recommended work

1. Rework Korea stock backtest universe and trigger definition until trade count is statistically usable.
2. Refine crypto breakout entry to reduce stop-hit frequency without killing DOGE/XRP expectancy.
3. After both are validated, transplant the winning rules into `recommendation_engine.py` and `execution_agent.py` more completely.

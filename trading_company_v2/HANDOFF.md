# Trading Company V2 Handoff

Last updated: 2026-04-21
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

## 7. Current known evidence from logs

- `data/dashboard_server.log` shows repeated successful hits to:
  - `/health`
  - `/dashboard-data`
- Requests include non-local source entries, which supports that external access routing is already being exercised.
- Duplicate starts fail because port `8080` is already bound, which suggests the dashboard server is already up.

## 8. Suggested next work

Priority order:

1. Tighten mobile-first presentation for both React and embedded views, especially compact cards and touch spacing.
2. Decide whether the long-term external route should stay on Tailscale or move to an Oracle-hosted public endpoint.
3. If Oracle public hosting is still intended, document:
   - reverse proxy or tunnel method
   - canonical DNS / URL
   - auth boundary
   - whether it targets embedded UI or React UI
4. After UI redesign is complete, move to the final live-readiness stage:
   - real broker check loop
   - final operational checklist
   - last safety pass before sustained live use

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

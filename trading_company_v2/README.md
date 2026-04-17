# Trading Company V2

Personal-PC-first auto trading bot stack.

## Goals

- Free to run at home with no monthly server bill
- Portable from this work PC to your personal PC
- Mobile-friendly monitoring through a local web app
- Strategy and risk handled by agents, not manual babysitting
- Built around well-known trader principles, adapted to your style

## Operating Model

- Main node: your personal PC at home
- Runtime: local Python process
- Storage: SQLite
- UI: FastAPI-served web dashboard
- Alerts/control: Telegram
- Mobile access: free tunnel/VPN later
  - Recommended: `Tailscale`
  - Alternative: `Cloudflare Tunnel`

## V2 Agent Stack

- `CIOAgent`
  - Company-level market stance
  - Converts macro + sentiment + trend into `OFFENSE / BALANCED / DEFENSE`
- `MacroSentimentAgent`
  - Tracks macro backdrop, news tone, regime stress
- `TrendStructureAgent`
  - Tracks market trend, relative strength, range expansion
- `RiskCommitteeAgent`
  - Enforces loss limits, exposure caps, block rules
- `MarketDataAgent`
  - Pulls free public crypto and KOSDAQ market snapshots
- `CryptoDeskAgent`
  - Reads BTC-led crypto structure for the global desk
- `KoreaStockDeskAgent`
  - Ranks domestic gap/liquidity leaders for the Korea desk
- `ExecutionAgent`
  - Simulated execution only in this phase
- `OpsAgent`
  - Health checks, daily summary, Telegram notifications

## Trader Principles Used

See [docs/trader_principles.md](./docs/trader_principles.md).

## Phase 1 Scope

- Local API app boots
- Agent state persists to SQLite
- One-cycle orchestration runs end-to-end
- Built-in HTML dashboard works without React/Vite
- Telegram notifier is optional
- Paper trading only

## Run On Personal PC Later

1. Copy the `trading_company_v2` folder
2. Install Python 3.11+
3. Create `.env` from `.env.example`
4. Install dependencies
5. Run `python -m app.main`

For the always-on bot loop on your home PC:

- Dashboard/API: `run_local.bat`
- 15-minute company loop: `run_company_loop.bat`

Detailed instructions are in [docs/personal_pc_setup.md](./docs/personal_pc_setup.md).

## Local Endpoints

- `GET /`
  - Mobile-friendly company dashboard
- `GET /health`
  - Service health
- `GET /state`
  - Raw current state JSON
- `GET /dashboard-data`
  - Dashboard JSON payload
- `POST /cycle`
  - Run one end-to-end decision cycle

## Telegram And Startup

- Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` for alerts
- Default behavior: notify only on stance/regime/risk changes or runtime errors
- Set `TELEGRAM_NOTIFY_EVERY_CYCLE=true` if you want every loop reported
- On Windows, `register_windows_tasks.bat` creates logon startup tasks for dashboard and loop

# Personal PC Setup

## Recommended Base

- Windows 10/11 or Linux
- Python 3.11+
- Telegram account
- Optional later: Tailscale for mobile access

## Install

1. Copy this folder to your personal PC.
2. Open terminal in `trading_company_v2`.
3. Create virtualenv:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

4. Copy `.env.example` to `.env`.
5. Fill keys only if needed.
6. Run:

```powershell
python -m app.main
```

7. Open:

```text
http://127.0.0.1:8080
```

8. First boot action:

```text
Open http://127.0.0.1:8080 and press "Run One Cycle"
```

That writes the first company state into SQLite and confirms the agent chain is working.

## Continuous Local Bot Loop

If you want the company cycle to keep running every 15 minutes on your home PC:

```powershell
python -m app.runtime
```

Or just use:

```text
run_company_loop.bat
```

You can change the interval with `CYCLE_INTERVAL_MINUTES` in `.env`.

If a batch window closes immediately, run it again after this update.
The scripts now keep the error message visible with `pause` when startup fails.

## Telegram Alerts

1. Create a Telegram bot with BotFather
2. Put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` into `.env`
3. Keep `TELEGRAM_NOTIFY_EVERY_CYCLE=false` at first

Default alert policy:

- Send when stance changes
- Send when regime changes
- Send when new entries become blocked/unblocked
- Send on runtime errors
- Include desk plan, latest paper order, and daily summary when an alert is sent

### Quick Setup Flow

1. In Telegram, open `@BotFather`
2. Run `/newbot`
3. Copy the bot token into `.env` as `TELEGRAM_BOT_TOKEN`
4. Send any message to your new bot from your Telegram account
5. In a browser, open:

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

6. Find your chat id in the JSON response and put it into `.env` as `TELEGRAM_CHAT_ID`
7. Restart `run_local.bat` and `run_company_loop.bat`

## Mobile Access Later

- Free recommended path: Tailscale
- Install Tailscale on home PC and phone
- Open the dashboard using the Tailscale IP
- Do not expose your home PC directly to the public internet in phase 1
- Current confirmed pattern on this machine:
  - `tailscale serve --bg 127.0.0.1:8080`
  - tailnet URL forwards to the local dashboard

## Public Access Mapping

If you later publish this through Oracle, a reverse proxy, or a tunnel:

- keep `APP_HOST=0.0.0.0`
- set `PUBLIC_BASE_URL` to the real external URL
- set `PUBLIC_BASE_LABEL` to something readable like `Oracle Public URL`
- then check:

```text
/health
/diagnostics/access-map
```

That makes the dashboard and diagnostics show the canonical public route together with the local and LAN routes.

If you are using `tailscale serve`, use the tailnet HTTPS URL as `PUBLIC_BASE_URL`.

## Cost Model

- Local runtime: free
- SQLite: free
- Telegram: free
- Tailscale personal use: free tier is usually enough

## Recommended Home-PC Operating Rules

- Keep this in paper trading mode until at least two weeks of clean observation
- Run it on a dedicated folder, not inside cloud-synced desktop folders
- Add Windows Task Scheduler later if you want auto-start on boot
- Keep `run_local.bat` and `run_company_loop.bat` as separate windows in phase 1

## Windows Auto-Start

After you confirm the bot runs cleanly by hand:

```text
register_windows_tasks.bat
```

That registers two Windows startup tasks:

- dashboard app
- 15-minute company loop

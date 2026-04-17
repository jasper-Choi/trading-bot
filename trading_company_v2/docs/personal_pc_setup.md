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

## Mobile Access Later

- Free recommended path: Tailscale
- Install Tailscale on home PC and phone
- Open the dashboard using the Tailscale IP
- Do not expose your home PC directly to the public internet in phase 1

## Cost Model

- Local runtime: free
- SQLite: free
- Telegram: free
- Tailscale personal use: free tier is usually enough

## Recommended Home-PC Operating Rules

- Keep this in paper trading mode until at least two weeks of clean observation
- Run it on a dedicated folder, not inside cloud-synced desktop folders
- Add Windows Task Scheduler later if you want auto-start on boot

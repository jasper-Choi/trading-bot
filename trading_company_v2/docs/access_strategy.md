# Access Strategy

Last checked: 2026-04-21

## Current live route

The dashboard is currently exposed through Tailscale Serve, not through a publicly open Oracle endpoint.

Confirmed status:

- URL: `https://desktop-891gpaq.taile9aa15.ts.net`
- Scope: `tailnet only`
- Target: `http://127.0.0.1:8080`

This means:

- the dashboard is reachable from approved devices inside the same Tailscale tailnet
- the app is not broadly exposed to the open internet
- the current external access path is safer than a direct public port open

## Current local routing

- `local_url`: `http://127.0.0.1:8080`
- `lan_url`: detected dynamically in `/diagnostics/access-map`
- `public_url`: sourced from `PUBLIC_BASE_URL` in `.env`

## Configuration now in use

Recommended `.env` fields:

```text
APP_HOST=0.0.0.0
APP_PORT=8080
PUBLIC_BASE_URL=https://desktop-891gpaq.taile9aa15.ts.net
PUBLIC_BASE_LABEL=Tailscale Tailnet URL
```

## Verification commands

```powershell
"C:\Program Files\Tailscale\tailscale.exe" serve status
"C:\Program Files\Tailscale\tailscale.exe" status
```

App-side checks:

- `GET /health`
- `GET /diagnostics/access-map`

## Recommendation

Short term:

- keep Tailscale Serve as the primary remote access route
- keep app basic auth enabled
- use the embedded dashboard or redesigned React UI behind the tailnet route

Why:

- lower exposure risk
- simpler than maintaining a public reverse proxy
- good fit for operator-only mobile access

## If Oracle public hosting is still desired later

Before switching away from Tailscale-only access, document all of the following:

- reverse proxy or tunnel method
- canonical DNS / HTTPS URL
- certificate termination point
- whether app basic auth still remains enabled
- whether the public route targets the embedded dashboard or the React build
- what rate limiting or network filtering protects the endpoint

Until those are explicit, treat Tailscale as the canonical external access path.

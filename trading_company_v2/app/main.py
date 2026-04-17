from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.config import settings
from app.core.state_store import init_db, load_company_state
from app.orchestrator import CompanyOrchestrator


app = FastAPI(title="Trading Company V2", version="0.1.0")
orchestrator = CompanyOrchestrator()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "env": settings.app_env}


@app.get("/state")
def state() -> dict:
    return load_company_state().model_dump()


@app.get("/dashboard-data")
def dashboard_data() -> dict:
    state = load_company_state()
    return {
        "company_name": settings.company_name,
        "operator_name": settings.operator_name,
        "state": state.model_dump(),
    }


@app.post("/cycle")
def cycle() -> dict:
    return orchestrator.run_cycle()


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{settings.company_name}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1ea;
      --card: #fffdf8;
      --ink: #1b1f18;
      --muted: #5b6257;
      --accent: #0f766e;
      --warn: #b45309;
      --danger: #b91c1c;
      --border: #d8d2c6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background: radial-gradient(circle at top, #ffffff 0%, var(--bg) 58%);
      color: var(--ink);
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 24px 16px 40px;
    }}
    .hero {{
      padding: 24px;
      border: 1px solid var(--border);
      background: linear-gradient(135deg, rgba(15,118,110,0.08), rgba(255,253,248,0.92));
      border-radius: 20px;
      margin-bottom: 18px;
    }}
    .grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(27,31,24,0.05);
    }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: 2rem; margin-bottom: 8px; }}
    h2 {{ font-size: 1rem; margin-bottom: 10px; }}
    .muted {{ color: var(--muted); }}
    .pill {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(15,118,110,0.1);
      color: var(--accent);
      font-size: 0.85rem;
      margin-top: 10px;
    }}
    ul {{
      padding-left: 18px;
      margin: 10px 0 0;
    }}
    li {{ margin-bottom: 6px; }}
    button {{
      margin-top: 14px;
      padding: 10px 14px;
      border-radius: 999px;
      border: none;
      background: var(--ink);
      color: white;
      cursor: pointer;
    }}
    .danger {{ color: var(--danger); }}
    .warn {{ color: var(--warn); }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>{settings.company_name}</h1>
      <p class="muted">Local-first paper trading company for {settings.operator_name}. Free to run, easy to move to your home PC, mobile-viewable later through Tailscale.</p>
      <div class="pill">Paper trading only</div>
      <button onclick="runCycle()">Run One Cycle</button>
    </section>
    <section class="grid">
      <article class="card">
        <h2>Company State</h2>
        <p id="state-line">Loading...</p>
        <p class="muted" id="updated-line"></p>
      </article>
      <article class="card">
        <h2>Signals</h2>
        <ul id="signals"></ul>
      </article>
      <article class="card">
        <h2>Trader Principles</h2>
        <ul id="principles"></ul>
      </article>
    </section>
    <section class="card" style="margin-top:14px;">
      <h2>Agent Desk</h2>
      <ul id="agents"></ul>
    </section>
  </main>
  <script>
    async function loadData() {{
      const res = await fetch('/dashboard-data', {{ cache: 'no-store' }});
      const data = await res.json();
      const state = data.state;
      document.getElementById('state-line').textContent =
        `${{state.stance}} stance / ${{state.regime}} regime / risk budget ${{state.risk_budget}} / new entries ${{state.allow_new_entries ? 'ON' : 'BLOCKED'}}`;
      document.getElementById('updated-line').textContent = `Updated: ${{state.updated_at}}`;
      document.getElementById('signals').innerHTML = state.latest_signals.map(item => `<li>${{item}}</li>`).join('') || '<li>No signals yet</li>';
      document.getElementById('principles').innerHTML = state.trader_principles.map(item => `<li>${{item}}</li>`).join('');
      document.getElementById('agents').innerHTML = state.agent_runs.map(item => `<li><strong>${{item.name}}</strong> (${{item.score}}): ${{item.reason}}</li>`).join('');
    }}
    async function runCycle() {{
      await fetch('/cycle', {{ method: 'POST' }});
      await loadData();
    }}
    loadData().catch(err => {{
      document.getElementById('state-line').textContent = `Dashboard unavailable: ${{err.message}}`;
      document.getElementById('state-line').className = 'danger';
    }});
  </script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)

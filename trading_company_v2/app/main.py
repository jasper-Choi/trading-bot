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
      --danger: #b91c1c;
      --soft: #efe7d8;
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
      max-width: 980px;
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
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(27,31,24,0.05);
    }}
    .priority {{
      border: 1px solid rgba(15,118,110,0.25);
      background: linear-gradient(180deg, rgba(15,118,110,0.08), var(--card));
    }}
    h1, h2, p {{ margin: 0; }}
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
    .metrics {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 14px;
    }}
    .metric {{
      padding: 10px 12px;
      border-radius: 14px;
      background: var(--soft);
      border: 1px solid var(--border);
    }}
    .metric strong {{
      display: block;
      font-size: 0.8rem;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .metric span {{
      font-size: 1rem;
    }}
    @media (max-width: 640px) {{
      main {{
        padding: 14px 12px 28px;
      }}
      .hero {{
        padding: 18px;
      }}
      h1 {{
        font-size: 1.5rem;
      }}
      .metrics {{
        grid-template-columns: 1fr 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>{settings.company_name}</h1>
      <p class="muted">Local-first paper trading company for {settings.operator_name}. Free to run, easy to move to your home PC, mobile-viewable later through Tailscale.</p>
      <div class="pill">Paper trading only</div>
      <button onclick="runCycle()">Run One Cycle</button>
      <div class="metrics">
        <div class="metric">
          <strong>Focus</strong>
          <span id="focus-metric">Loading...</span>
        </div>
        <div class="metric">
          <strong>Risk Budget</strong>
          <span id="risk-metric">Loading...</span>
        </div>
        <div class="metric">
          <strong>Daily Cycles</strong>
          <span id="cycles-metric">Loading...</span>
        </div>
        <div class="metric">
          <strong>Est. PnL</strong>
          <span id="pnl-metric">Loading...</span>
        </div>
      </div>
    </section>
    <section class="grid">
      <article class="card priority">
        <h2>Company State</h2>
        <p id="state-line">Loading...</p>
        <p class="muted" id="updated-line"></p>
      </article>
      <article class="card priority">
        <h2>Desk Plans</h2>
        <ul id="desk-plans"></ul>
      </article>
      <article class="card priority">
        <h2>Daily Summary</h2>
        <ul id="daily-summary"></ul>
      </article>
    </section>
    <section class="grid" style="margin-top:14px;">
      <article class="card">
        <h2>Signals</h2>
        <ul id="signals"></ul>
      </article>
      <article class="card">
        <h2>Session State</h2>
        <ul id="session-state"></ul>
      </article>
      <article class="card">
        <h2>Strategy Book</h2>
        <ul id="strategy-book"></ul>
      </article>
    </section>
    <section class="grid" style="margin-top:14px;">
      <article class="card">
        <h2>Crypto Leaders</h2>
        <ul id="crypto-leaders"></ul>
      </article>
      <article class="card">
        <h2>KOSDAQ Leaders</h2>
        <ul id="stock-leaders"></ul>
      </article>
      <article class="card">
        <h2>Desk Views</h2>
        <ul id="desks"></ul>
      </article>
    </section>
    <section class="grid" style="margin-top:14px;">
      <article class="card">
        <h2>Paper Blotter</h2>
        <ul id="paper-blotter"></ul>
      </article>
      <article class="card">
        <h2>Cycle Journal</h2>
        <ul id="cycle-journal"></ul>
      </article>
      <article class="card">
        <h2>Daily Summary</h2>
        <ul id="daily-summary"></ul>
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
      document.getElementById('focus-metric').textContent = state.strategy_book.company_focus || 'n/a';
      document.getElementById('risk-metric').textContent = state.risk_budget ?? 'n/a';
      document.getElementById('cycles-metric').textContent = state.daily_summary.cycles_run ?? 0;
      document.getElementById('pnl-metric').textContent = `${{state.daily_summary.estimated_pnl_pct ?? 0}}%`;
      document.getElementById('state-line').textContent =
        `${{state.stance}} stance / ${{state.regime}} regime / risk budget ${{state.risk_budget}} / new entries ${{state.allow_new_entries ? 'ON' : 'BLOCKED'}}`;
      document.getElementById('updated-line').textContent = `Updated: ${{state.updated_at}}`;
      document.getElementById('signals').innerHTML = (state.latest_signals || []).map(item => `<li>${{item}}</li>`).join('') || '<li>No signals yet</li>';
      document.getElementById('principles').innerHTML = (state.trader_principles || []).map(item => `<li>${{item}}</li>`).join('');
      document.getElementById('crypto-leaders').innerHTML = (state.market_snapshot.crypto_leaders || []).slice(0, 5).map(item => `<li>${{item.market}} / ${{item.change_rate}}% / KRW ${{Number(item.trade_price).toLocaleString()}}</li>`).join('') || '<li>No crypto snapshot yet</li>';
      document.getElementById('stock-leaders').innerHTML = (state.market_snapshot.gap_candidates || state.market_snapshot.stock_leaders || []).slice(0, 5).map(item => `<li>${{item.name || item.ticker}} / gap ${{item.gap_pct}}% / vol ${{Number(item.volume || 0).toLocaleString()}}</li>`).join('') || '<li>No KOSDAQ snapshot yet</li>';
      document.getElementById('desks').innerHTML = Object.entries(state.desk_views || {{}}).map(([name, payload]) => `<li><strong>${{name}}</strong>: ${{JSON.stringify(payload)}}</li>`).join('') || '<li>No desk output yet</li>';
      document.getElementById('session-state').innerHTML = Object.entries(state.session_state || {{}}).map(([name, value]) => `<li><strong>${{name}}</strong>: ${{Array.isArray(value) ? value.join(', ') : value}}</li>`).join('') || '<li>No session state yet</li>';
      document.getElementById('strategy-book').innerHTML = [
        `<li><strong>company_focus</strong>: ${{state.strategy_book.company_focus || 'n/a'}}</li>`,
        `<li><strong>desk_priorities</strong>: ${{(state.strategy_book.desk_priorities || []).join(' | ') || 'n/a'}}</li>`
      ].join('');
      document.getElementById('desk-plans').innerHTML = [
        `<li><strong>crypto</strong>: ${{state.strategy_book.crypto_plan ? state.strategy_book.crypto_plan.action + ' / ' + state.strategy_book.crypto_plan.size + ' / ' + state.strategy_book.crypto_plan.focus : 'n/a'}}</li>`,
        `<li><strong>korea</strong>: ${{state.strategy_book.korea_plan ? state.strategy_book.korea_plan.action + ' / ' + state.strategy_book.korea_plan.size + ' / ' + state.strategy_book.korea_plan.focus : 'n/a'}}</li>`
      ].join('');
      document.getElementById('paper-blotter').innerHTML = (state.execution_log || []).slice(0, 6).map(item => `<li>${{item.created_at}} / ${{item.desk}} / ${{item.action}} / ${{item.size}} / est ${{item.pnl_estimate_pct}}%</li>`).join('') || '<li>No paper orders yet</li>';
      document.getElementById('cycle-journal').innerHTML = (state.recent_journal || []).slice(0, 5).map(item => `<li>${{item.run_at}} / ${{item.stance}} / ${{item.regime}} / ${{item.company_focus}}</li>`).join('') || '<li>No journal yet</li>';
      document.getElementById('daily-summary').innerHTML = [
        `<li><strong>date</strong>: ${{state.daily_summary.date || 'n/a'}}</li>`,
        `<li><strong>cycles</strong>: ${{state.daily_summary.cycles_run || 0}}</li>`,
        `<li><strong>orders</strong>: ${{state.daily_summary.orders_logged || 0}}</li>`,
        `<li><strong>planned_orders</strong>: ${{state.daily_summary.planned_orders || 0}}</li>`,
        `<li><strong>est_pnl_pct</strong>: ${{state.daily_summary.estimated_pnl_pct || 0}}</li>`,
        `<li><strong>active_desks</strong>: ${{(state.daily_summary.active_desks || []).join(', ') || 'n/a'}}</li>`
      ].join('');
      document.getElementById('agents').innerHTML = (state.agent_runs || []).map(item => `<li><strong>${{item.name}}</strong> (${{item.score}}): ${{item.reason}}</li>`).join('');
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

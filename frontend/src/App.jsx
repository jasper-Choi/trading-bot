import { useState, useEffect, useCallback } from 'react'
import { api } from './api'
import StatCard           from './components/StatCard'
import PositionTable      from './components/PositionTable'
import PnlChart           from './components/PnlChart'
import TradeHistory       from './components/TradeHistory'
import LogViewer          from './components/LogViewer'
import MarketRegimeBanner from './components/MarketRegimeBanner'
import StockPositionTable from './components/StockPositionTable'
import InsightPanel       from './components/InsightPanel'

const T = {
  ko: {
    title:         'Trading Control Center',
    running:       'Running',
    stopped:       'Stopped',
    start:         'Start',
    stop:          'Stop',
    refresh:       'Refresh',
    lastUpdate:    'Updated',
    nextRun:       'Next run',
    totalInvested: 'Capital',
    cumPnl:        'Cum. PnL',
    winRate:       'Win Rate',
    sharpe:        'Sharpe',
    mdd:           'MDD',
    trades:        'trades',
    noData:        '--',
    openPositions: 'Open Positions',
    pnlChart:      'Cumulative PnL',
    recentTrades:  'Trade History',
    liveLog:       'Live Logs',
    coin:          'Coin',
    entryPrice:    'Entry',
    currentPrice:  'Current',
    stopLoss:      'Stop',
    unrealizedPnl: 'Unrealized PnL',
    entryDate:     'Entry Date',
    exitDate:      'Exit Date',
    exitReason:    'Reason',
    exitPrice:     'Exit',
    pnl:           'PnL',
    pnlPct:        'PnL %',
    noPosition:    'No open positions',
    noTradeData:   'No trade history',
    noLog:         'No logs yet',
    apiError:      'Cannot connect to API server',
    tabCoin:       'Coin',
    tabStock:      'Stock',
  },
  en: {
    title:         'Trading Control Center',
    running:       'Running',
    stopped:       'Stopped',
    start:         'Start',
    stop:          'Stop',
    refresh:       'Refresh',
    lastUpdate:    'Updated',
    nextRun:       'Next run',
    totalInvested: 'Capital',
    cumPnl:        'Cum. PnL',
    winRate:       'Win Rate',
    sharpe:        'Sharpe',
    mdd:           'MDD',
    trades:        'trades',
    noData:        '--',
    openPositions: 'Open Positions',
    pnlChart:      'Cumulative PnL',
    recentTrades:  'Trade History',
    liveLog:       'Live Logs',
    coin:          'Coin',
    entryPrice:    'Entry',
    currentPrice:  'Current',
    stopLoss:      'Stop',
    unrealizedPnl: 'Unrealized PnL',
    entryDate:     'Entry Date',
    exitDate:      'Exit Date',
    exitReason:    'Reason',
    exitPrice:     'Exit',
    pnl:           'PnL',
    pnlPct:        'PnL %',
    noPosition:    'No open positions',
    noTradeData:   'No trade history',
    noLog:         'No logs yet',
    apiError:      'Cannot connect to API server',
    tabCoin:       'Coin',
    tabStock:      'Stock',
  },
}

const fmtMoney = (n) =>
  n != null ? `KRW ${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : null

function isMarketOpen() {
  const now     = new Date()
  const hours   = now.getHours()
  const minutes = now.getMinutes()
  const day     = now.getDay()
  if (day === 0 || day === 6) return false
  const totalMin = hours * 60 + minutes
  return totalMin >= 9 * 60 && totalMin <= 15 * 60 + 30
}

function buildChartData(trades) {
  if (!trades.length) return []
  const sorted = [...trades].sort((a, b) => a.exit_date.localeCompare(b.exit_date))
  const grouped = {}
  sorted.forEach(({ exit_date, pnl }) => {
    grouped[exit_date] = (grouped[exit_date] ?? 0) + pnl
  })
  let cum = 0
  return Object.entries(grouped).map(([date, pnl]) => {
    cum += pnl
    return { date, cumPnl: Math.round(cum) }
  })
}

const REFRESH_SEC = 30

async function settle(promise, fallback) {
  try {
    return await promise
  } catch {
    return fallback
  }
}

export default function App() {
  const [lang,           setLang]           = useState('ko')
  const [status,         setStatus]         = useState(null)
  const [positions,      setPositions]      = useState([])
  const [trades,         setTrades]         = useState([])
  const [stats,          setStats]          = useState(null)
  const [logs,           setLogs]           = useState([])
  const [regime,         setRegime]         = useState(null)
  const [stockPositions, setStockPositions] = useState([])
  const [insights,       setInsights]       = useState(null)
  const [agentStatus,    setAgentStatus]    = useState(null)
  const [dashboardData,  setDashboardData]  = useState(null)
  const [error,          setError]          = useState(null)
  const [lastUpdate,     setLastUpdate]     = useState(null)
  const [countdown,      setCountdown]      = useState(REFRESH_SEC)
  const [activeTab,      setActiveTab]      = useState('coin')

  const t = T[lang]

  const fetchAll = useCallback(async () => {
    const [s, p, tr, st, lg, reg, sp, ins, agents, dash] = await Promise.all([
      settle(api.status(), null),
      settle(api.positions(), []),
      settle(api.trades(50), []),
      settle(api.stats(), null),
      settle(api.logs(40), { lines: [] }),
      settle(api.marketRegime(), null),
      settle(api.stockPositions(), []),
      settle(api.insights(), null),
      settle(api.agentsStatus(), null),
      settle(api.dashboardData(), null),
    ])

    if (s) setStatus(s)
    setPositions(p)
    setTrades(tr)
    if (st) setStats(st)
    setLogs(lg?.lines ?? [])
    if (reg) setRegime(reg)
    setStockPositions(sp)
    if (ins) setInsights(ins)
    if (agents) setAgentStatus(agents)
    if (dash) setDashboardData(dash)

    const hasCoreData = Boolean(s || st || ins || agents || dash)
    setError(hasCoreData ? null : 'Dashboard data temporarily unavailable')
    setLastUpdate(
      new Date().toLocaleTimeString('ko-KR', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      })
    )
    setCountdown(REFRESH_SEC)
  }, [])

  useEffect(() => {
    fetchAll()
    const iv = setInterval(fetchAll, REFRESH_SEC * 1000)
    return () => clearInterval(iv)
  }, [fetchAll])

  useEffect(() => {
    const iv = setInterval(() => setCountdown((c) => (c > 0 ? c - 1 : 0)), 1000)
    return () => clearInterval(iv)
  }, [])

  const handleStart = async () => {
    try { await api.startBot(); await fetchAll() } catch (e) { setError(e.message) }
  }
  const handleStop = async () => {
    try { await api.stopBot(); await fetchAll() } catch (e) { setError(e.message) }
  }

  const openPositions = positions.filter((p) => p.status === 'open')
  const chartData     = buildChartData(trades)
  const isRunning     = status?.running ?? false
  const totalInvested = openPositions.reduce((s, p) => s + p.capital, 0)
  const pnlVal        = stats?.total_pnl ?? null
  const pnlPositive   = pnlVal == null ? null : pnlVal >= 0
  const winRateVal    = stats ? stats.win_rate * 100 : null
  const marketOpen    = isMarketOpen()
  const dashboard     = dashboardData?.dashboard ?? null
  const executionSummary = dashboard?.execution_summary ?? {}
  const opsFlags      = dashboard?.ops_flags ?? { severity: 'stable', items: [] }
  const readiness     = dashboardData?.live_readiness_checklist ?? null
  const brokerHealth  = dashboardData?.broker_live_health ?? null
  const latestLive    = executionSummary?.latest_live ?? null
  const recentLive    = (dashboardData?.state?.execution_log ?? [])
    .filter((item) => item?.source === 'live')
    .slice(0, 3)
  const readinessToneClass =
    readiness?.overall === 'blocked' ? 'tone-risk'
      : readiness?.overall === 'caution' ? 'tone-warn'
      : readiness?.overall === 'ready' ? 'tone-ok'
      : 'tone-muted'
  const prioritySignals = [
    readiness?.overall === 'blocked'
      ? `Execution blocked: ${readiness?.block_count ?? 0} hard stop`
      : null,
    Number(executionSummary.stale_count || 0) > 0
      ? `Stale live orders: ${executionSummary.stale_count || 0}`
      : null,
    Number(executionSummary.partial_count || 0) > 0
      ? `Partial fills pending review: ${executionSummary.partial_count || 0}`
      : null,
    Number(executionSummary.pending_count || 0) > 0
      ? `Pending live orders: ${executionSummary.pending_count || 0}`
      : null,
    brokerHealth?.upbit?.configured === false && brokerHealth?.kis?.configured === false
      ? 'No live broker credentials configured'
      : null,
  ].filter(Boolean)
  const liveToneClass =
    readiness?.overall === 'blocked' ? 'tone-risk'
      : Number(executionSummary.stale_count || 0) > 0 ? 'tone-risk'
      : Number(executionSummary.partial_count || 0) > 0 ? 'tone-warn'
      : Number(executionSummary.pending_count || 0) > 0 ? 'tone-info'
      : Number(executionSummary.live_count || 0) > 0 ? 'tone-ok'
      : 'tone-muted'
  const modeLabel = readiness?.overall || opsFlags?.severity || 'stable'
  const primaryNote = prioritySignals[0] || `Runtime ${status?.next_run ? `next ${status.next_run.slice(11, 16)}` : 'cycle active'}`
  const readinessItems = (readiness?.checklist || []).slice(0, 6)
  const statusCards = [
    { label: 'Runtime', value: isRunning ? t.running : t.stopped, sub: status?.next_run ? `${t.nextRun} ${status.next_run.slice(11, 16)}` : 'Awaiting schedule' },
    { label: 'Readiness', value: String(modeLabel).toUpperCase(), sub: `${readiness?.block_count ?? 0} blocks / ${readiness?.warn_count ?? 0} warns` },
    { label: 'Exposure', value: `${openPositions.length}`, sub: `${t.openPositions} / ${executionSummary.live_count || 0} live` },
    { label: 'Ops', value: String(opsFlags?.severity || 'stable').toUpperCase(), sub: primaryNote },
  ]

  return (
    <div className="app app-shell">
      <div className="app-glow app-glow-a" />
      <div className="app-glow app-glow-b" />

      <header className="hero-shell">
        <div className="hero-copy">
          <span className="hero-kicker">Operator cockpit</span>
          <div className="hero-title-row">
            <h1 className="hero-title">{t.title}</h1>
            <span className={`hero-pill ${readinessToneClass}`}>{String(modeLabel).toUpperCase()}</span>
          </div>
          <p className="hero-summary">{primaryNote}</p>
          <div className="hero-meta">
            <span className={`status-dot ${isRunning ? 'on' : 'off'}`} />
            <span>{isRunning ? t.running : t.stopped}</span>
            <span>{t.lastUpdate}: {lastUpdate || '--:--:--'}</span>
            <span>{status?.next_run ? `${t.nextRun} ${status.next_run.slice(11, 16)}` : 'No next run yet'}</span>
          </div>
        </div>

        <div className="hero-actions">
          <div className="hero-action-group">
            <button className="btn btn-start" onClick={handleStart} disabled={isRunning}>
              {t.start}
            </button>
            <button className="btn btn-stop" onClick={handleStop} disabled={!isRunning}>
              {t.stop}
            </button>
          </div>
          <div className="hero-action-group">
            <button className="btn btn-ghost" onClick={fetchAll}>
              Refresh {countdown}s
            </button>
            <button className="btn btn-lang" onClick={() => setLang((l) => (l === 'ko' ? 'en' : 'ko'))}>
              {lang === 'ko' ? 'EN' : 'KO'}
            </button>
          </div>
        </div>
      </header>

      <section className="hero-overview">
        {statusCards.map((item) => (
          <div className="overview-card" key={item.label}>
            <span className="overview-label">{item.label}</span>
            <strong className="overview-value">{item.value}</strong>
            <span className="overview-sub">{item.sub}</span>
          </div>
        ))}
      </section>

      <MarketRegimeBanner
        regime={regime?.regime ?? 'NEUTRAL'}
        lastChanged={regime?.last_changed ?? null}
        marketOpen={marketOpen}
      />

      {error && (
        <div className="error-banner">
          {t.apiError}: {error}
        </div>
      )}

      <main className="dashboard">
        <div className="area-cards">
          <div className="stat-row">
            <StatCard
              label={t.totalInvested}
              value={totalInvested > 0 ? fmtMoney(totalInvested) : t.noData}
              sub={`${openPositions.length} ${t.openPositions}`}
            />
            <StatCard
              label={t.cumPnl}
              value={pnlVal != null ? `${pnlPositive ? '+' : '-'}${fmtMoney(pnlVal)}` : t.noData}
              sub={stats ? `${stats.total_trades} ${t.trades}` : t.noData}
              valueClass={pnlPositive == null ? 'c-text' : pnlPositive ? 'c-green' : 'c-red'}
            />
            <StatCard
              label={t.winRate}
              value={winRateVal != null ? `${winRateVal.toFixed(1)}%` : t.noData}
              sub={stats ? `${stats.winning_trades}W / ${stats.losing_trades}L` : t.noData}
              valueClass={
                winRateVal == null ? 'c-text'
                  : winRateVal >= 50 ? 'c-green'
                  : winRateVal >= 40 ? 'c-yellow'
                  : 'c-red'
              }
            />
            <StatCard
              label={t.sharpe}
              value={stats?.sharpe_ratio != null ? stats.sharpe_ratio.toFixed(2) : t.noData}
              sub={stats?.max_drawdown_pct ? `${t.mdd} ${stats.max_drawdown_pct.toFixed(1)}%` : t.noData}
              valueClass={
                stats?.sharpe_ratio == null ? 'c-text'
                  : stats.sharpe_ratio >= 1 ? 'c-green'
                  : stats.sharpe_ratio >= 0 ? 'c-yellow'
                  : 'c-red'
              }
            />
          </div>

          <div className="signal-deck">
            <div className={`execution-strip ${liveToneClass}`}>
              <div className="execution-strip-main">
                <strong>Live execution</strong>
                <span>Total {executionSummary.live_count || 0}</span>
                <span>Partial {executionSummary.partial_count || 0}</span>
                <span>Pending {executionSummary.pending_count || 0}</span>
                <span>Stale {executionSummary.stale_count || 0}</span>
                <span>Severity {opsFlags.severity || 'stable'}</span>
              </div>
              {prioritySignals.length > 0 && (
                <div className="priority-strip">
                  {prioritySignals.map((item, idx) => (
                    <span className="priority-chip" key={`${item}-${idx}`}>{item}</span>
                  ))}
                </div>
              )}
              <div className="execution-strip-sub">
                {latestLive
                  ? `Latest ${latestLive.desk || 'n/a'} / ${latestLive.action || 'n/a'} / ${latestLive.symbol || latestLive.focus || 'n/a'} / ${latestLive.status || 'n/a'} / ${latestLive.effect_status || 'n/a'}`
                  : 'No live execution yet'}
              </div>
              {recentLive.length > 0 && (
                <div className="execution-strip-list">
                  {recentLive.map((item, idx) => (
                    <div className="execution-strip-item" key={`${item.broker_order_id || item.created_at || idx}-${idx}`}>
                      <strong>{item.desk || 'n/a'} / {item.action || 'n/a'}</strong>
                      <span>{item.symbol || item.focus || 'n/a'}</span>
                      <span>{item.status || 'n/a'} / {item.effect_status || 'n/a'}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className={`readiness-strip ${readinessToneClass}`}>
              <div className="readiness-head">
                <strong>Live readiness</strong>
                <span>{readiness?.overall || 'n/a'}</span>
                <span>Blocks {readiness?.block_count ?? 0}</span>
                <span>Warns {readiness?.warn_count ?? 0}</span>
              </div>
              <div className="readiness-grid">
                {readinessItems.map((item, idx) => (
                  <div className={`readiness-item readiness-${item.status || 'warn'}`} key={`${item.label || idx}-${idx}`}>
                    <strong>{item.label || 'n/a'}</strong>
                    <span>{item.detail || 'n/a'}</span>
                  </div>
                ))}
              </div>
              <div className="broker-strip">
                <div className="broker-item">
                  <strong>Upbit</strong>
                  <span>{brokerHealth?.upbit?.configured ? 'configured' : 'missing creds'}</span>
                  <span>{brokerHealth?.upbit?.balances_ok ? `balances ${brokerHealth?.upbit?.balances_count || 0}` : 'balance check off'}</span>
                </div>
                <div className="broker-item">
                  <strong>KIS</strong>
                  <span>{brokerHealth?.kis?.configured ? 'configured' : 'missing creds'}</span>
                  <span>{brokerHealth?.kis?.balances_ok ? `balances ${brokerHealth?.kis?.balances_count || 0}` : 'balance check off'}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="area-insights feature-panel">
          <InsightPanel data={insights} agentStatus={agentStatus} />
        </div>

        <div className="area-position">
          <div className="panel dock-panel" style={{ height: '100%' }}>
            <div className="section-intro">
              <div>
                <div className="panel-title">Position Dock</div>
                <div className="panel-subcopy">Switch desks and inspect exposure like a mobile dealing app.</div>
              </div>
              <div className="tab-bar">
                <button
                  className={`btn btn-tab ${activeTab === 'coin' ? 'active' : ''}`}
                  onClick={() => setActiveTab('coin')}
                >
                  Coin
                </button>
                <button
                  className={`btn btn-tab ${activeTab === 'stock' ? 'active' : ''}`}
                  onClick={() => setActiveTab('stock')}
                >
                  Stock
                </button>
              </div>
            </div>

            {activeTab === 'coin' ? (
              <PositionTable positions={openPositions} t={t} embedded />
            ) : (
              <StockPositionTable positions={stockPositions} />
            )}
          </div>
        </div>

        <div className="area-chart feature-panel">
          <PnlChart chartData={chartData} t={t} />
        </div>

        <div className="area-trades feature-panel">
          <TradeHistory trades={trades.slice(0, 15)} t={t} />
        </div>

        <div className="area-logs feature-panel">
          <LogViewer lines={logs} t={t} />
        </div>
      </main>
    </div>
  )
}

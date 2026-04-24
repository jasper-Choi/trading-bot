import { Suspense, lazy, startTransition, useState, useEffect, useCallback } from 'react'
import { api } from './api'
import StatCard from './components/StatCard'
import PositionTable from './components/PositionTable'
import MarketRegimeBanner from './components/MarketRegimeBanner'
import StockPositionTable from './components/StockPositionTable'
import { formatKstTime, formatKstDateTime, getKstClock } from './utils/time'

const PnlChart = lazy(() => import('./components/PnlChart'))
const TradeHistory = lazy(() => import('./components/TradeHistory'))
const LogViewer = lazy(() => import('./components/LogViewer'))
const InsightPanel = lazy(() => import('./components/InsightPanel'))

const T = {
  ko: {
    title: '트레이딩 관제석',
    running: '실행 중',
    stopped: '정지',
    start: '시작',
    stop: '중지',
    refresh: '새로고침',
    lastUpdate: '마지막 업데이트',
    nextRun: '다음 실행',
    totalInvested: '투입 자본',
    cumPnl: '누적 손익',
    winRate: '승률',
    sharpe: '샤프',
    mdd: 'MDD',
    trades: '거래',
    noData: '--',
    openPositions: '보유 포지션',
    pnlChart: '손익 곡선',
    recentTrades: '최근 거래',
    liveLog: '실시간 로그',
    coin: '코인',
    entryPrice: '진입가',
    currentPrice: '현재가',
    stopLoss: '손절가',
    unrealizedPnl: '미실현 손익',
    entryDate: '진입 시각',
    exitDate: '청산 시각',
    exitReason: '사유',
    exitPrice: '청산가',
    pnl: '손익',
    pnlPct: '수익률',
    noPosition: '보유 중인 포지션이 없습니다',
    noTradeData: '거래 내역이 없습니다',
    noLog: '로그가 없습니다',
    apiError: 'API 서버에 연결할 수 없습니다',
    tabCoin: '코인',
    tabStock: '주식',
  },
  en: {
    title: 'Trading Control Center',
    running: 'Running',
    stopped: 'Stopped',
    start: 'Start',
    stop: 'Stop',
    refresh: 'Refresh',
    lastUpdate: 'Updated',
    nextRun: 'Next run',
    totalInvested: 'Capital',
    cumPnl: 'Cum. PnL',
    winRate: 'Win Rate',
    sharpe: 'Sharpe',
    mdd: 'MDD',
    trades: 'trades',
    noData: '--',
    openPositions: 'Open Positions',
    pnlChart: 'Cumulative PnL',
    recentTrades: 'Trade History',
    liveLog: 'Live Logs',
    coin: 'Coin',
    entryPrice: 'Entry',
    currentPrice: 'Current',
    stopLoss: 'Stop',
    unrealizedPnl: 'Unrealized PnL',
    entryDate: 'Entry Date',
    exitDate: 'Exit Date',
    exitReason: 'Reason',
    exitPrice: 'Exit',
    pnl: 'PnL',
    pnlPct: 'PnL %',
    noPosition: 'No open positions',
    noTradeData: 'No trade history',
    noLog: 'No logs yet',
    apiError: 'Cannot connect to API server',
    tabCoin: 'Coin',
    tabStock: 'Stock',
  },
}

const REFRESH_SEC = 30

const fmtMoney = (n) =>
  n != null ? `KRW ${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : null

function isMarketOpen() {
  const clock = getKstClock()
  const weekday = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    weekday: 'short',
  }).format(new Date())
  const hours = Number(clock.hour ?? 0)
  const minutes = Number(clock.minute ?? 0)
  const dayMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }
  const day = dayMap[weekday] ?? 0
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

function PanelFallback({ title, detail = 'Loading panel...' }) {
  return (
    <div className="panel panel-fallback">
      <div className="panel-title">{title}</div>
      <div className="panel-fallback-copy">{detail}</div>
    </div>
  )
}

async function settleAll(entries) {
  const resolved = await Promise.all(
    entries.map(async ([key, promise, fallback]) => {
      const value = await settle(promise, fallback)
      return [key, value]
    })
  )
  return Object.fromEntries(resolved)
}

async function settle(promise, fallback) {
  try {
    return await promise
  } catch {
    return fallback
  }
}

export default function App() {
  const [lang, setLang] = useState('ko')
  const [status, setStatus] = useState(null)
  const [positions, setPositions] = useState([])
  const [trades, setTrades] = useState([])
  const [stats, setStats] = useState(null)
  const [logs, setLogs] = useState([])
  const [regime, setRegime] = useState(null)
  const [stockPositions, setStockPositions] = useState([])
  const [insights, setInsights] = useState(null)
  const [agentStatus, setAgentStatus] = useState(null)
  const [dashboardData, setDashboardData] = useState(null)
  const [error, setError] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [countdown, setCountdown] = useState(REFRESH_SEC)
  const [activeTab, setActiveTab] = useState('coin')

  const t = T[lang]

  const fetchAll = useCallback(async () => {
    const core = await settleAll([
      ['status', api.status(), null],
      ['marketRegime', api.marketRegime(), null],
      ['dashboard', api.dashboardData(), null],
    ])

    startTransition(() => {
      if (core.status) setStatus(core.status)
      if (core.marketRegime) setRegime(core.marketRegime)
      if (core.dashboard) setDashboardData(core.dashboard)
      const hasCoreData = Boolean(core.status || core.marketRegime || core.dashboard)
      setError(hasCoreData ? null : 'Dashboard data temporarily unavailable')
    })

    const secondary = await settleAll([
      ['positions', api.positions(), []],
      ['trades', api.trades(50), []],
      ['stats', api.stats(), null],
      ['logs', api.logs(40), { lines: [] }],
      ['stockPositions', api.stockPositions(), []],
      ['insights', api.insights(), null],
      ['agents', api.agentsStatus(), null],
    ])

    startTransition(() => {
      setPositions(secondary.positions)
      setTrades(secondary.trades)
      if (secondary.stats) setStats(secondary.stats)
      setLogs(secondary.logs?.lines ?? [])
      setStockPositions(secondary.stockPositions)
      if (secondary.insights) setInsights(secondary.insights)
      if (secondary.agents) setAgentStatus(secondary.agents)
    })

    setLastUpdate(formatKstTime(new Date()))
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
    try {
      await api.startBot()
      await fetchAll()
    } catch (e) {
      setError(e.message)
    }
  }

  const handleStop = async () => {
    try {
      await api.stopBot()
      await fetchAll()
    } catch (e) {
      setError(e.message)
    }
  }

  const openPositions = positions.filter((p) => p.status === 'open')
  const chartData = buildChartData(trades)
  const isRunning = status?.running ?? false
  const totalInvested = openPositions.reduce((s, p) => s + p.capital, 0)
  const pnlVal = stats?.total_pnl ?? null
  const pnlPositive = pnlVal == null ? null : pnlVal >= 0
  const winRateVal = stats ? stats.win_rate * 100 : null
  const marketOpen = isMarketOpen()
  const dashboard = dashboardData?.dashboard ?? null
  const access = dashboardData?.access ?? null
  const deploymentProfile = dashboardData?.deployment_profile ?? null
  const executionSummary = dashboard?.execution_summary ?? {}
  const opsFlags = dashboard?.ops_flags ?? { severity: 'stable', items: [] }
  const readiness = dashboardData?.live_readiness_checklist ?? null
  const brokerHealth = dashboardData?.broker_live_health ?? null
  const upbitPilot = dashboardData?.upbit_live_pilot ?? null
  const cryptoLiveLane = dashboard?.crypto_live_lane ?? upbitPilot?.crypto_lane ?? null
  const cryptoLaneHistory = dashboard?.crypto_live_lane_history ?? upbitPilot?.crypto_lane_history ?? []
  const entryBlockSummary =
    dashboard?.exposure?.entry_block_summary ?? readiness?.entry_block_summary ?? null
  const deskOffense = dashboard?.desk_offense ?? []
  const deskDrilldown = dashboard?.desk_drilldown ?? {}
  const symbolEdge = dashboard?.symbol_edge ?? []
  const agentPerformance = dashboard?.agent_performance ?? []
  const capitalProfile =
    dashboard?.capital?.capital_profile ?? dashboardData?.state?.strategy_book?.capital_profile ?? {}
  const latestLive = executionSummary?.latest_live ?? null
  const recentLive = (dashboardData?.state?.execution_log ?? [])
    .filter((item) => item?.source === 'live')
    .slice(0, 3)

  const readinessToneClass =
    readiness?.overall === 'blocked'
      ? 'tone-risk'
      : readiness?.overall === 'caution'
        ? 'tone-warn'
        : readiness?.overall === 'ready'
          ? 'tone-ok'
          : 'tone-muted'

  const prioritySignals = [
    entryBlockSummary?.blocked
      ? `Entry gate: ${entryBlockSummary?.detail || 'risk gate closed'}`
      : readiness?.overall === 'blocked'
        ? `Readiness blocked: ${readiness?.block_count ?? 0} blocker(s)`
        : null,
    Number(executionSummary.stale_count || 0) > 0
      ? `Stale live orders: ${executionSummary.stale_count || 0}`
      : null,
    Number(executionSummary.partial_count || 0) > 0
      ? `Partial fills need review: ${executionSummary.partial_count || 0}`
      : null,
    Number(executionSummary.pending_count || 0) > 0
      ? `Pending live orders: ${executionSummary.pending_count || 0}`
      : null,
    brokerHealth?.upbit?.configured === false && brokerHealth?.kis?.configured === false
      ? 'Live broker credentials are not configured'
      : null,
  ].filter(Boolean)

  const liveToneClass =
    readiness?.overall === 'blocked'
      ? 'tone-risk'
      : Number(executionSummary.stale_count || 0) > 0
        ? 'tone-risk'
        : Number(executionSummary.partial_count || 0) > 0
          ? 'tone-warn'
          : Number(executionSummary.pending_count || 0) > 0
            ? 'tone-info'
            : Number(executionSummary.live_count || 0) > 0
              ? 'tone-ok'
              : 'tone-muted'

  const modeLabel = readiness?.overall || opsFlags?.severity || 'stable'
  const primaryNote =
    prioritySignals[0] ||
    `System stable${
      status?.next_run ? ` / next cycle ${formatKstTime(status.next_run)}` : ' / cycle idle'
    }`
  const readinessItems = (readiness?.checklist || []).slice(0, 6)
  const readinessNextActions = (readiness?.next_actions || []).slice(0, 3)
  const readinessHeadline = readiness?.status_headline || 'Go-live review required'
  const readinessCurrentStep = readiness?.current_step || readinessNextActions[0] || 'Review readiness checks'

  const statusCards = [
    {
      label: 'Runtime',
      value: isRunning ? t.running : t.stopped,
      sub: status?.next_run ? `${t.nextRun} ${formatKstTime(status.next_run)}` : 'cycle idle',
    },
    {
      label: 'Readiness',
      value: String(modeLabel).toUpperCase(),
      sub: entryBlockSummary?.blocked
        ? entryBlockSummary?.detail || 'risk gate closed'
        : `${readiness?.block_count ?? 0} blocked / ${readiness?.warn_count ?? 0} caution`,
    },
    {
      label: 'Positions',
      value: `${openPositions.length}`,
      sub: `${t.openPositions} / ${executionSummary.live_count || 0} live`,
    },
    {
      label: 'Ops',
      value: String(opsFlags?.severity || 'stable').toUpperCase(),
      sub: primaryNote,
    },
  ]

  const missionRailCards = [
    {
      label: 'Entry Gate',
      value: entryBlockSummary?.blocked ? 'Blocked' : 'Open',
      detail: entryBlockSummary?.detail || 'risk gate open',
      tone: entryBlockSummary?.blocked ? 'tone-risk' : 'tone-ok',
    },
    {
      label: 'Next Cycle',
      value: status?.next_run ? formatKstTime(status.next_run) : '--:--',
      detail: isRunning ? 'scheduler online' : 'scheduler offline',
      tone: isRunning ? 'tone-ok' : 'tone-warn',
    },
    {
      label: 'Latest Live',
      value: latestLive?.symbol || latestLive?.focus || 'None',
      detail: latestLive
        ? `${latestLive.status || 'n/a'} / ${latestLive.effect_status || 'n/a'}`
        : 'no live execution yet',
      tone: latestLive ? liveToneClass : 'tone-muted',
    },
    {
      label: 'Profile',
      value: deploymentProfile?.label || 'Unknown',
      detail: deploymentProfile?.summary || 'deployment profile unavailable',
      tone: deploymentProfile?.role === 'live_target' ? 'tone-info' : 'tone-muted',
    },
  ]

  const accessCards = [
    access?.public_url ? { label: access?.public_label || 'Public URL', value: access.public_url } : null,
    access?.lan_url ? { label: 'LAN URL', value: access.lan_url } : null,
    access?.local_url ? { label: 'Local URL', value: access.local_url } : null,
  ].filter(Boolean)

  const offenseLeader = deskOffense[0] ?? null
  const weakAgentCount = agentPerformance.filter((item) => item?.tone === 'weak').length
  const strongAgentCount = agentPerformance.filter((item) => item?.tone === 'strong').length
  const capitalModeLabel = capitalProfile?.mode
    ? String(capitalProfile.mode).replaceAll('_', ' ')
    : 'neutral'
  const agentLog = dashboard?.agent_log ?? []
  const candidatePanels = [
    { key: 'crypto', title: 'Crypto', detail: deskDrilldown?.crypto ?? null },
    { key: 'korea', title: 'Korea', detail: deskDrilldown?.korea ?? null },
    { key: 'us', title: 'U.S.', detail: deskDrilldown?.us ?? null },
  ]

  return (
    <div className="app app-shell">
      <div className="app-glow app-glow-a" />
      <div className="app-glow app-glow-b" />

      <header className="hero-shell">
        <div className="hero-copy">
          <span className="hero-kicker">Operator Cockpit</span>
          <div className="hero-title-row">
            <h1 className="hero-title">{t.title}</h1>
            <span className={`hero-pill ${readinessToneClass}`}>{String(modeLabel).toUpperCase()}</span>
          </div>
          <p className="hero-summary">{primaryNote}</p>
          <div className="hero-meta">
            <span className={`status-dot ${isRunning ? 'on' : 'off'}`} />
            <span>{isRunning ? t.running : t.stopped}</span>
            <span>{t.lastUpdate}: {lastUpdate || '--:--:--'}</span>
            <span>
              {status?.next_run ? `${t.nextRun} ${formatKstTime(status.next_run)}` : 'No next cycle scheduled'}
            </span>
          </div>
          {accessCards.length > 0 && (
            <div className="hero-access-grid">
              {accessCards.map((item) => (
                <div className="hero-access-card" key={item.label}>
                  <span className="hero-access-label">{item.label}</span>
                  <strong className="hero-access-value">{item.value}</strong>
                </div>
              ))}
            </div>
          )}
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
              {t.refresh} {countdown}s
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

      <section className="mission-rail">
        {missionRailCards.map((item) => (
          <div className={`mission-card ${item.tone}`} key={item.label}>
            <span className="mission-label">{item.label}</span>
            <strong className="mission-value">{item.value}</strong>
            <span className="mission-detail">{item.detail}</span>
          </div>
        ))}
      </section>

      <MarketRegimeBanner
        regime={regime?.regime ?? 'NEUTRAL'}
        lastChanged={regime?.last_changed ?? regime?.lastChanged ?? null}
        marketOpen={marketOpen}
      />

      {deploymentProfile?.role === 'local_dev' && (
        <div className="error-banner">
          Local development profile active. This screen is not a live deployment target.
        </div>
      )}

      {error && (
        <div className="error-banner">
          {t.apiError}: {error}
        </div>
      )}

      <main className="dashboard">
        <div className="area-cards">
          <div className="signal-deck">
            <div className={`execution-strip ${liveToneClass}`}>
              <div className="execution-strip-main">
                <strong>Execution Monitor</strong>
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
                <strong>Deployment Readiness</strong>
                <span>{readiness?.overall || 'n/a'}</span>
                <span>Blocked {readiness?.block_count ?? 0}</span>
                <span>Caution {readiness?.warn_count ?? 0}</span>
              </div>
              <div className="execution-strip-sub">
                {readinessHeadline}
                {readinessCurrentStep ? ` / next: ${readinessCurrentStep}` : ''}
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
                  <span>{brokerHealth?.upbit?.configured ? 'credentials configured' : 'credentials missing'}</span>
                  <span>{brokerHealth?.upbit?.balances_ok ? `balances ${brokerHealth?.upbit?.balances_count || 0}` : 'balance check pending'}</span>
                </div>
                <div className="broker-item">
                  <strong>KIS</strong>
                  <span>{brokerHealth?.kis?.configured ? 'credentials configured' : 'credentials missing'}</span>
                  <span>{brokerHealth?.kis?.balances_ok ? `balances ${brokerHealth?.kis?.balances_count || 0}` : 'balance check pending'}</span>
                </div>
              </div>
              {readinessNextActions.length > 0 && (
                <div className="pilot-grid">
                  <div className="pilot-col">
                    <strong>Go-Live Next Actions</strong>
                    {readinessNextActions.map((item, idx) => (
                      <span key={`readiness-next-${idx}`}>{item}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {upbitPilot && (
            <div className={`pilot-panel ${upbitPilot.go_live_ready ? 'tone-ok' : 'tone-warn'}`}>
              <div className="edge-head">
                <div>
                  <div className="panel-title">Upbit Live Pilot</div>
                  <div className="panel-subcopy">
                    {upbitPilot?.pilot_headline || (
                      upbitPilot.go_live_ready
                        ? `pilot ready / suggested cap KRW ${Number(upbitPilot.pilot_cap_krw || 0).toLocaleString('ko-KR')}`
                        : `pilot blocked / suggested cap KRW ${Number(upbitPilot.pilot_cap_krw || 0).toLocaleString('ko-KR')}`
                    )}
                  </div>
                  <div className="panel-subcopy">
                    {upbitPilot?.signal_headline || 'crypto signal state loading'}
                  </div>
                </div>
                <div className={`edge-pill ${upbitPilot.go_live_ready ? 'tone-ok' : 'tone-warn'}`}>
                  {upbitPilot.go_live_ready ? 'READY' : 'HOLD'}
                </div>
              </div>
              <div className="pilot-grid">
                <div className="pilot-col">
                  <strong>Pilot State</strong>
                  <span>{`infra ${upbitPilot.go_live_ready ? 'ready' : 'hold'} / signal ${upbitPilot.signal_status || 'waiting'}`}</span>
                  <span>{`mode ${upbitPilot.execution_mode || 'paper'} / cap KRW ${Number(upbitPilot.pilot_cap_krw || 0).toLocaleString('ko-KR')}`}</span>
                </div>
                <div className="pilot-col">
                  <strong>Blockers</strong>
                  {(upbitPilot.blockers || []).length > 0
                    ? upbitPilot.blockers.slice(0, 3).map((item, idx) => <span key={`blocker-${idx}`}>{item}</span>)
                    : <span>No blockers</span>}
                </div>
                <div className="pilot-col">
                  <strong>Next Steps</strong>
                  {(upbitPilot.suggested_sequence || []).slice(0, 3).map((item, idx) => (
                    <span key={`step-${idx}`}>{item}</span>
                  ))}
                </div>
                {cryptoLiveLane && (
                  <div className="pilot-col">
                    <strong>Crypto Lane</strong>
                    <span>
                      {`${cryptoLiveLane.symbol || 'KRW-BTC'} / ${cryptoLiveLane.action || 'watchlist_only'} / ${cryptoLiveLane.size || '0.00x'}`}
                    </span>
                    <span>
                      {`signal ${Number(cryptoLiveLane.signal_score || 0).toFixed(2)} / trigger ${Number(cryptoLiveLane.trigger_threshold || 0).toFixed(2)} / ${cryptoLiveLane.trigger_state || 'waiting'}`}
                    </span>
                    <span>{cryptoLiveLane.focus || 'crypto lane waiting for confirmation'}</span>
                  </div>
                )}
                {cryptoLaneHistory.length > 0 && (
                  <div className="pilot-col">
                    <strong>Signal Trend</strong>
                    {cryptoLaneHistory.slice(-4).map((item, idx) => (
                      <span key={`crypto-trend-${idx}`}>
                        {`${item.time || '--:--'} / ${Number(item.signal_score || 0).toFixed(2)} -> ${Number(item.trigger_threshold || 0).toFixed(2)} / ${item.action || 'watchlist_only'}`}
                      </span>
                    ))}
                  </div>
                )}
                {readinessNextActions.length > 0 && (
                  <div className="pilot-col">
                    <strong>Readiness Actions</strong>
                    {readinessNextActions.map((item, idx) => (
                      <span key={`pilot-readiness-${idx}`}>{item}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="edge-deck">
            <div className="edge-panel">
              <div className="edge-head">
                <div>
                  <div className="panel-title">Desk Offense Map</div>
                  <div className="panel-subcopy">
                    {offenseLeader
                      ? `leader ${offenseLeader.title} / score ${offenseLeader.score} / ${capitalModeLabel}`
                      : 'desk offense map loading'}
                  </div>
                </div>
                <div className={`edge-pill tone-${offenseLeader?.tone || 'muted'}`}>
                  {capitalModeLabel.toUpperCase()}
                </div>
              </div>
              <div className="edge-grid">
                {deskOffense.map((item) => (
                  <div className={`edge-card tone-${item.tone || 'muted'}`} key={item.desk}>
                    <div className="edge-card-top">
                      <strong>{item.title}</strong>
                      <span>{item.score}</span>
                    </div>
                    <div className="edge-card-main">{item.action || 'stand_by'} / {item.size || '0.00x'}</div>
                    <div className="edge-card-sub">{item.focus || 'no active focus'}</div>
                    <div className="edge-card-meta">
                      <span>realized {Number(item.realized_pnl_pct || 0).toFixed(2)}%</span>
                      <span>win rate {Number(item.win_rate || 0).toFixed(1)}%</span>
                      <span>multiplier {Number(item.multiplier || 1).toFixed(2)}x</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="edge-panel">
              <div className="edge-head">
                <div>
                  <div className="panel-title">Agent Effectiveness</div>
                  <div className="panel-subcopy">
                    {`${strongAgentCount} strong / ${weakAgentCount} weak in current cycle`}
                  </div>
                </div>
                <div className="edge-pill tone-info">
                  {agentPerformance.length} agents
                </div>
              </div>
              <div className="agent-board">
                {agentPerformance.slice(0, 8).map((item) => (
                  <div className={`agent-row tone-${item.tone || 'mixed'}`} key={item.name}>
                    <div className="agent-copy">
                      <strong>{item.title}</strong>
                      <span>{item.reason || 'no summary available'}</span>
                    </div>
                    <div className="agent-metrics">
                      <span>score {Number(item.score || 0).toFixed(0)}</span>
                      <span>effect {Number(item.effectiveness || 0).toFixed(0)}</span>
                      <span>{item.linked_desk ? `${item.linked_desk} desk` : 'system desk'}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="symbol-edge-panel">
            <div className="edge-head">
              <div>
                <div className="panel-title">Symbol Bias</div>
                <div className="panel-subcopy">
                  Recent symbol memory overlay with hot / neutral / cold bias
                </div>
              </div>
            </div>
            <div className="symbol-edge-grid">
              {symbolEdge.slice(0, 6).map((item) => (
                <div className={`symbol-edge-card tone-${item.tone || 'neutral'}`} key={`${item.desk}-${item.symbol}`}>
                  <strong>{item.symbol}</strong>
                  <span>{item.desk} / {item.tone}</span>
                  <span>score {Number(item.score || 0).toFixed(2)}</span>
                  <span>{item.detail || 'fresh symbol bias'}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="candidate-panel">
            <div className="edge-head">
              <div>
                <div className="panel-title">Candidate Quality</div>
                <div className="panel-subcopy">
                  Live candidate desks ranked for current profit-maximization focus
                </div>
              </div>
            </div>
            <div className="candidate-grid">
              {candidatePanels.map((panel) => {
                const detail = panel.detail || {}
                const candidates = (detail.candidate_details || []).slice(0, 4)
                const tone =
                  detail.action === 'attack_opening_drive' || detail.action === 'probe_longs'
                    ? 'tone-press'
                    : detail.action === 'selective_probe'
                      ? 'tone-balanced'
                      : 'tone-muted'
                return (
                  <div className={`candidate-card ${tone}`} key={panel.key}>
                    <div className="candidate-card-head">
                      <div>
                        <strong>{panel.title}</strong>
                        <span>{detail.focus || 'no active focus'}</span>
                      </div>
                      <span className="candidate-card-size">{detail.size || '0.00x'}</span>
                    </div>
                    <div className="candidate-meta">
                      <span>leader {detail.target_symbol || '--'}</span>
                      <span>quality {Number(detail.quality_score || 0).toFixed(2)}</span>
                      <span>signal {Number(detail.avg_signal || 0).toFixed(2)}</span>
                      <span>active {Number(detail.active_count || 0)}</span>
                    </div>
                    <div className="candidate-list">
                      {candidates.length > 0 ? (
                        candidates.map((item, idx) => (
                          <div className="candidate-row" key={`${panel.key}-${item.symbol || idx}`}>
                            <div className="candidate-copy">
                              <strong>
                                {item.label || item.symbol || '--'}
                                {item.is_primary ? ' · lead' : ''}
                              </strong>
                              <span>
                                {item.bias || 'neutral'}
                                {item.signal_score != null ? ` / signal ${Number(item.signal_score || 0).toFixed(2)}` : ''}
                                {item.weight != null ? ` / weight ${Number(item.weight || 0).toFixed(2)}` : ''}
                              </span>
                            </div>
                            <div className="candidate-metrics">
                              <span>score {Number(item.score || 0).toFixed(2)}</span>
                              {item.gap_pct != null && <span>gap {Number(item.gap_pct || 0).toFixed(1)}%</span>}
                              {item.pullback_gap_pct != null && <span>pullback {Number(item.pullback_gap_pct || 0).toFixed(2)}%</span>}
                              {item.change_pct != null && <span>change {Number(item.change_pct || 0).toFixed(2)}%</span>}
                            </div>
                          </div>
                        ))
                      ) : (
                        <div className="candidate-empty">No candidate details yet</div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {agentLog.length > 0 && (
            <div className="agent-log-panel">
              <div className="edge-head">
                <div>
                  <div className="panel-title">AI {'\uc5d0\uc774\uc804\ud2b8'} {'\ud310\ub2e8'} {'\uc774\ub825'}</div>
                  <div className="panel-subcopy">
                    {agentLog.length}{'\uc0ac\uc774\ud074'} {'\uae30\ub85d'} / {'\ucd5c\uadfc'} {formatKstDateTime(agentLog[0]?.run_at)}
                  </div>
                </div>
                <div className="edge-pill tone-info">{agentLog.length} cycles</div>
              </div>
              <div className="agent-cycles">
                {agentLog.slice(0, 6).map((cycle, idx) => (
                  <div className={`agent-cycle${idx === 0 ? ' latest' : ''}`} key={cycle.run_at || idx}>
                    <div className="agent-cycle-head">
                      <span className="agent-cycle-time">{formatKstDateTime(cycle.run_at)}</span>
                      <span className={`agent-stance-tag ${cycle.stance?.includes('OFFENSE') ? 'tone-ok' : cycle.stance?.includes('DEFENSE') ? 'tone-risk' : 'tone-muted'}`}>
                        {cycle.stance || '--'}
                      </span>
                      <span className="agent-stance-tag tone-muted">{cycle.regime || '--'}</span>
                    </div>
                    <div className="agent-desk-rows">
                      {(cycle.desks || []).map((d) => {
                        const isActionable = ['probe_longs', 'selective_probe', 'attack_opening_drive'].includes(d.action)
                        const isIdle = d.status === 'idle'
                        const cls = isActionable && !isIdle ? 'c-green' : isActionable && isIdle ? 'c-yellow' : 'c-muted'
                        const deskLabel = d.desk === 'crypto' ? '\ucf54\uc778' : d.desk === 'korea' ? '\ud55c\uad6d' : '\ubbf8\uad6d'
                        const symPart = d.symbol ? ` \u00b7 ${d.symbol.replace('KRW-', '')}` : ''
                        const szPart = isActionable && !isIdle ? ` \u00b7 ${d.size}` : ''
                        return (
                          <div className="agent-desk-row" key={d.desk}>
                            <span className="agent-desk-tag">{deskLabel}</span>
                            <div className="agent-desk-body">
                              <span className={`agent-desk-act ${cls}`}>{d.action.replace(/_/g, ' ')}{symPart}{szPart}</span>
                              {d.notes?.slice(0, 1).map((note, ni) => (
                                <span className="agent-desk-note" key={ni}>{note}</span>
                              ))}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                    {cycle.signals?.length > 0 && (
                      <div className="agent-cycle-signals">{cycle.signals.slice(0, 2).join(' \u00b7 ')}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

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
                winRateVal == null ? 'c-text' : winRateVal >= 50 ? 'c-green' : winRateVal >= 40 ? 'c-yellow' : 'c-red'
              }
            />
            <StatCard
              label={t.sharpe}
              value={stats?.sharpe_ratio != null ? stats.sharpe_ratio.toFixed(2) : t.noData}
              sub={stats?.max_drawdown_pct ? `${t.mdd} ${stats.max_drawdown_pct.toFixed(1)}%` : t.noData}
              valueClass={
                stats?.sharpe_ratio == null ? 'c-text' : stats.sharpe_ratio >= 1 ? 'c-green' : stats.sharpe_ratio >= 0 ? 'c-yellow' : 'c-red'
              }
            />
          </div>
        </div>

        <div className="area-insights feature-panel">
          <Suspense fallback={<PanelFallback title="Insights" detail="Loading operator insights..." />}>
            <InsightPanel data={insights} agentStatus={agentStatus} />
          </Suspense>
        </div>

        <div className="area-position">
          <div className="panel dock-panel" style={{ height: '100%' }}>
            <div className="section-intro">
              <div>
                <div className="panel-title">Position Dock</div>
                <div className="panel-subcopy">Review live positions, pricing, and unrealized performance in one place.</div>
              </div>
              <div className="tab-bar">
                <button
                  className={`btn btn-tab ${activeTab === 'coin' ? 'active' : ''}`}
                  onClick={() => setActiveTab('coin')}
                >
                  {t.tabCoin}
                </button>
                <button
                  className={`btn btn-tab ${activeTab === 'stock' ? 'active' : ''}`}
                  onClick={() => setActiveTab('stock')}
                >
                  {t.tabStock}
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
          <Suspense fallback={<PanelFallback title={t.pnlChart} detail="Loading cumulative curve..." />}>
            <PnlChart chartData={chartData} t={t} />
          </Suspense>
        </div>

        <div className="area-trades feature-panel">
          <Suspense fallback={<PanelFallback title={t.recentTrades} detail="Loading recent trade history..." />}>
            <TradeHistory trades={trades.slice(0, 15)} t={t} />
          </Suspense>
        </div>

        <div className="area-logs feature-panel">
          <Suspense fallback={<PanelFallback title={t.liveLog} detail="Loading live operator logs..." />}>
            <LogViewer lines={logs} t={t} />
          </Suspense>
        </div>
      </main>
    </div>
  )
}

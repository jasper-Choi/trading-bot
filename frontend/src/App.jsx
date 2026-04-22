import { Suspense, lazy, startTransition, useState, useEffect, useCallback } from 'react'
import { api } from './api'
import StatCard           from './components/StatCard'
import PositionTable      from './components/PositionTable'
import MarketRegimeBanner from './components/MarketRegimeBanner'
import StockPositionTable from './components/StockPositionTable'

const PnlChart = lazy(() => import('./components/PnlChart'))
const TradeHistory = lazy(() => import('./components/TradeHistory'))
const LogViewer = lazy(() => import('./components/LogViewer'))
const InsightPanel = lazy(() => import('./components/InsightPanel'))

const T = {
  ko: {
    title:         '트레이딩 관제센터',
    running:       '실행 중',
    stopped:       '정지',
    start:         '시작',
    stop:          '정지',
    refresh:       '새로고침',
    lastUpdate:    '마지막업데이트',
    nextRun:       '다음실행',
    totalInvested: '실전자본',
    cumPnl:        '누적손익',
    winRate:       '승률',
    sharpe:        '샤프',
    mdd:           'MDD',
    trades:        '거래',
    noData:        '--',
    openPositions: '보유포지션',
    pnlChart:      '수익곡선',
    recentTrades:  '청산내역',
    liveLog:       '실시간 로그',
    coin:          '크립토',
    entryPrice:    '진입가',
    currentPrice:  '현재가',
    stopLoss:      '손절가',
    unrealizedPnl: '미실현손익',
    entryDate:     '진입일',
    exitDate:      '청산일',
    exitReason:    '사유',
    exitPrice:     '청산가',
    pnl:           '손익',
    pnlPct:        '수익률',
    noPosition:    '보유 포지션 없음',
    noTradeData:   '거래 내역 없음',
    noLog:         '로그 없음',
    apiError:      'API 서버 연결 불가',
    tabCoin:       '크립토',
    tabStock:      '주식',
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
  const access        = dashboardData?.access ?? null
  const executionSummary = dashboard?.execution_summary ?? {}
  const opsFlags      = dashboard?.ops_flags ?? { severity: 'stable', items: [] }
  const readiness     = dashboardData?.live_readiness_checklist ?? null
  const brokerHealth  = dashboardData?.broker_live_health ?? null
  const upbitPilot = dashboardData?.upbit_live_pilot ?? null
  const entryBlockSummary = dashboard?.exposure?.entry_block_summary ?? readiness?.entry_block_summary ?? null
  const deskOffense = dashboard?.desk_offense ?? []
  const symbolEdge = dashboard?.symbol_edge ?? []
  const agentPerformance = dashboard?.agent_performance ?? []
  const capitalProfile = dashboard?.capital?.capital_profile ?? dashboardData?.state?.strategy_book?.capital_profile ?? {}
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
    entryBlockSummary?.blocked
      ? `진입게이트: ${entryBlockSummary?.detail || '위험 게이트 닫힘'}`
      : readiness?.overall === 'blocked'
      ? `실행 차단: ${readiness?.block_count ?? 0} 차단항목`
      : null,
    Number(executionSummary.stale_count || 0) > 0
      ? `미처리 실전 주문: ${executionSummary.stale_count || 0}`
      : null,
    Number(executionSummary.partial_count || 0) > 0
      ? `부분체결 검토 필요: ${executionSummary.partial_count || 0}`
      : null,
    Number(executionSummary.pending_count || 0) > 0
      ? `대기중 실전 주문: ${executionSummary.pending_count || 0}`
      : null,
    brokerHealth?.upbit?.configured === false && brokerHealth?.kis?.configured === false
      ? '실전 브로커 인증 미설정'
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
  const primaryNote = prioritySignals[0] || `런타임 ${status?.next_run ? `다음실행 ${status.next_run.slice(11, 16)}` : '사이클 활성'}`
  const readinessItems = (readiness?.checklist || []).slice(0, 6)
  const statusCards = [
    { label: '런타임', value: isRunning ? t.running : t.stopped, sub: status?.next_run ? `${t.nextRun} ${status.next_run.slice(11, 16)}` : '스케줄 대기 중' },
    { label: '준비도', value: String(modeLabel).toUpperCase(), sub: entryBlockSummary?.blocked ? (entryBlockSummary?.detail || '위험 게이트 닫힘') : `${readiness?.block_count ?? 0} 차단 / ${readiness?.warn_count ?? 0} 주의` },
    { label: '보유포지션', value: `${openPositions.length}`, sub: `${t.openPositions} / ${executionSummary.live_count || 0} 실전` },
    { label: '운영', value: String(opsFlags?.severity || 'stable').toUpperCase(), sub: primaryNote },
  ]
  const missionRail = [
    {
      label: '진입게이트',
      value: entryBlockSummary?.blocked ? '차단' : '개방',
      detail: entryBlockSummary?.detail || '위험 게이트 개방',
      tone: entryBlockSummary?.blocked ? 'tone-risk' : 'tone-ok',
    },
    {
      label: '다음사이클',
      value: status?.next_run ? status.next_run.slice(11, 16) : '--:--',
      detail: isRunning ? '런타임 온라인' : '런타임 오프라인',
      tone: isRunning ? 'tone-ok' : 'tone-warn',
    },
    {
      label: '최근실전',
      value: latestLive?.symbol || latestLive?.focus || '없음',
      detail: latestLive ? `${latestLive.status || 'n/a'} / ${latestLive.effect_status || 'n/a'}` : '아직 실전 체결 없음',
      tone: latestLive ? liveToneClass : 'tone-muted',
    },
    {
      label: '브로커',
      value: brokerHealth?.upbit?.configured || brokerHealth?.kis?.configured ? '설정됨' : '미설정',
      detail: brokerHealth?.upbit?.balances_ok || brokerHealth?.kis?.balances_ok ? '잔고 확인 통과' : '인증 또는 잔고 확인 필요',
      tone: brokerHealth?.upbit?.balances_ok || brokerHealth?.kis?.balances_ok ? 'tone-ok' : 'tone-warn',
    },
  ]
  const accessCards = [
    access?.public_url
      ? { label: access?.public_label || 'Public URL', value: access.public_url }
      : null,
    access?.lan_url
      ? { label: 'LAN URL', value: access.lan_url }
      : null,
    access?.local_url
      ? { label: 'Local URL', value: access.local_url }
      : null,
  ].filter(Boolean)
  const offenseLeader = deskOffense[0] ?? null
  const weakAgentCount = agentPerformance.filter((item) => item?.tone === 'weak').length
  const strongAgentCount = agentPerformance.filter((item) => item?.tone === 'strong').length
  const capitalModeLabel = capitalProfile?.mode ? String(capitalProfile.mode).replaceAll('_', ' ') : 'neutral'

  return (
    <div className="app app-shell">
      <div className="app-glow app-glow-a" />
      <div className="app-glow app-glow-b" />

      <header className="hero-shell">
        <div className="hero-copy">
          <span className="hero-kicker">운영 관제석</span>
          <div className="hero-title-row">
            <h1 className="hero-title">{t.title}</h1>
            <span className={`hero-pill ${readinessToneClass}`}>{String(modeLabel).toUpperCase()}</span>
          </div>
          <p className="hero-summary">{primaryNote}</p>
          <div className="hero-meta">
            <span className={`status-dot ${isRunning ? 'on' : 'off'}`} />
            <span>{isRunning ? t.running : t.stopped}</span>
            <span>{t.lastUpdate}: {lastUpdate || '--:--:--'}</span>
            <span>{status?.next_run ? `${t.nextRun} ${status.next_run.slice(11, 16)}` : '다음 실행 없음'}</span>
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
              새로고침 {countdown}s
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
        {missionRail.map((item) => (
          <div className={`mission-card ${item.tone}`} key={item.label}>
            <span className="mission-label">{item.label}</span>
            <strong className="mission-value">{item.value}</strong>
            <span className="mission-detail">{item.detail}</span>
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
          <div className="signal-deck">
            <div className={`execution-strip ${liveToneClass}`}>
              <div className="execution-strip-main">
                <strong>실행 모니터</strong>
                <span>전체 {executionSummary.live_count || 0}</span>
                <span>부분체결 {executionSummary.partial_count || 0}</span>
                <span>대기중 {executionSummary.pending_count || 0}</span>
                <span>미처리 {executionSummary.stale_count || 0}</span>
                <span>심각도 {opsFlags.severity || 'stable'}</span>
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
                  ? `최근 ${latestLive.desk || 'n/a'} / ${latestLive.action || 'n/a'} / ${latestLive.symbol || latestLive.focus || 'n/a'} / ${latestLive.status || 'n/a'} / ${latestLive.effect_status || 'n/a'}`
                  : '아직 실전 실행 없음'}
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
                <strong>실전 준비도</strong>
                <span>{readiness?.overall || 'n/a'}</span>
                <span>차단 {readiness?.block_count ?? 0}</span>
                <span>주의 {readiness?.warn_count ?? 0}</span>
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
                  <span>{brokerHealth?.upbit?.configured ? '인증 설정됨' : '인증 없음'}</span>
                  <span>{brokerHealth?.upbit?.balances_ok ? `잔고 ${brokerHealth?.upbit?.balances_count || 0}` : '잔고 점검 안 됨'}</span>
                </div>
                <div className="broker-item">
                  <strong>KIS</strong>
                  <span>{brokerHealth?.kis?.configured ? '인증 설정됨' : '인증 없음'}</span>
                  <span>{brokerHealth?.kis?.balances_ok ? `잔고 ${brokerHealth?.kis?.balances_count || 0}` : '잔고 점검 안 됨'}</span>
                </div>
              </div>
            </div>
          </div>

          {upbitPilot && (
            <div className={`pilot-panel ${upbitPilot.go_live_ready ? 'tone-ok' : 'tone-warn'}`}>
              <div className="edge-head">
                <div>
                  <div className="panel-title">Upbit Live Pilot</div>
                  <div className="panel-subcopy">
                    {upbitPilot.go_live_ready
                      ? `pilot ready / suggested cap KRW ${Number(upbitPilot.pilot_cap_krw || 0).toLocaleString('ko-KR')}`
                      : `pilot blocked / suggested cap KRW ${Number(upbitPilot.pilot_cap_krw || 0).toLocaleString('ko-KR')}`}
                  </div>
                </div>
                <div className={`edge-pill ${upbitPilot.go_live_ready ? 'tone-ok' : 'tone-warn'}`}>
                  {upbitPilot.go_live_ready ? 'READY' : 'HOLD'}
                </div>
              </div>
              <div className="pilot-grid">
                <div className="pilot-col">
                  <strong>차단항목</strong>
                  {(upbitPilot.blockers || []).length > 0
                    ? upbitPilot.blockers.slice(0, 3).map((item, idx) => <span key={`blocker-${idx}`}>{item}</span>)
                    : <span>차단항목 없음</span>}
                </div>
                <div className="pilot-col">
                  <strong>다음 단계</strong>
                  {(upbitPilot.suggested_sequence || []).slice(0, 3).map((item, idx) => <span key={`step-${idx}`}>{item}</span>)}
                </div>
              </div>
            </div>
          )}

          <div className="edge-deck">
            <div className="edge-panel">
              <div className="edge-head">
                <div>
                  <div className="panel-title">데스크 공격 현황</div>
                  <div className="panel-subcopy">
                    {offenseLeader
                      ? `상위 데스크 ${offenseLeader.title} / 점수 ${offenseLeader.score} / ${capitalModeLabel}`
                      : '데스크 압력 로딩 중'}
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
                    <div className="edge-card-sub">{item.focus || '포커스 없음'}</div>
                    <div className="edge-card-meta">
                      <span>실현손익 {Number(item.realized_pnl_pct || 0).toFixed(2)}%</span>
                      <span>승률 {Number(item.win_rate || 0).toFixed(1)}%</span>
                      <span>배수 {Number(item.multiplier || 1).toFixed(2)}x</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="edge-panel">
              <div className="edge-head">
                <div>
                  <div className="panel-title">에이전트 효율성</div>
                  <div className="panel-subcopy">
                    {`${strongAgentCount} 강함 / ${weakAgentCount} 약함 (현재 사이클)`}
                  </div>
                </div>
                <div className="edge-pill tone-info">
                  {agentPerformance.length} 에이전트
                </div>
              </div>
              <div className="agent-board">
                {agentPerformance.slice(0, 8).map((item) => (
                  <div className={`agent-row tone-${item.tone || 'mixed'}`} key={item.name}>
                    <div className="agent-copy">
                      <strong>{item.title}</strong>
                      <span>{item.reason || '요약 없음'}</span>
                    </div>
                    <div className="agent-metrics">
                      <span>시그널 {Number(item.score || 0).toFixed(0)}</span>
                      <span>엣지 {Number(item.effectiveness || 0).toFixed(0)}</span>
                      <span>{item.linked_desk ? `${item.linked_desk} 데스크` : '크로스 데스크'}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="symbol-edge-panel">
            <div className="edge-head">
              <div>
                <div className="panel-title">종목 편향</div>
                <div className="panel-subcopy">최근 종목 메모리 기반 hot / neutral / cold 재진입 편향</div>
              </div>
            </div>
            <div className="symbol-edge-grid">
              {symbolEdge.slice(0, 6).map((item) => (
                <div className={`symbol-edge-card tone-${item.tone || 'neutral'}`} key={`${item.desk}-${item.symbol}`}>
                  <strong>{item.symbol}</strong>
                  <span>{item.desk} / {item.tone}</span>
                  <span>score {Number(item.score || 0).toFixed(2)}</span>
                  <span>{item.detail || '신규 종목'}</span>
                </div>
              ))}
            </div>
          </div>

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
        </div>

        <div className="area-insights feature-panel">
          <Suspense fallback={<PanelFallback title="인사이트 패널" detail="운영 인사이트 불러오는 중..." />}>
            <InsightPanel data={insights} agentStatus={agentStatus} />
          </Suspense>
        </div>

        <div className="area-position">
          <div className="panel dock-panel" style={{ height: '100%' }}>
            <div className="section-intro">
              <div>
                <div className="panel-title">포지션 현황</div>
                <div className="panel-subcopy">실시간 보유 포지션 및 손익 현황을 확인하세요.</div>
              </div>
              <div className="tab-bar">
                <button
                  className={`btn btn-tab ${activeTab === 'coin' ? 'active' : ''}`}
                  onClick={() => setActiveTab('coin')}
                >
                  크립토
                </button>
                <button
                  className={`btn btn-tab ${activeTab === 'stock' ? 'active' : ''}`}
                  onClick={() => setActiveTab('stock')}
                >
                  주식
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
          <Suspense fallback={<PanelFallback title={t.pnlChart} detail="누적 곡선 불러오는 중..." />}>
            <PnlChart chartData={chartData} t={t} />
          </Suspense>
        </div>

        <div className="area-trades feature-panel">
          <Suspense fallback={<PanelFallback title={t.recentTrades} detail="체결 내역 불러오는 중..." />}>
            <TradeHistory trades={trades.slice(0, 15)} t={t} />
          </Suspense>
        </div>

        <div className="area-logs feature-panel">
          <Suspense fallback={<PanelFallback title={t.liveLog} detail="런타임 로그 불러오는 중..." />}>
            <LogViewer lines={logs} t={t} />
          </Suspense>
        </div>
      </main>
    </div>
  )
}

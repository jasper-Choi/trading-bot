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
    title:         '모의투자 봇 대시보드',
    running:       '실행 중',
    stopped:       '중지됨',
    start:         '봇 시작',
    stop:          '봇 중지',
    refresh:       '새로고침',
    lastUpdate:    '업데이트',
    nextRun:       '다음 실행',
    totalInvested: '투자금',
    cumPnl:        '누적손익',
    winRate:       '승률',
    sharpe:        '샤프지수',
    mdd:           'MDD',
    trades:        '거래',
    noData:        '--',
    openPositions: '보유 포지션',
    pnlChart:      '누적 손익 곡선',
    recentTrades:  '최근 거래 이력',
    liveLog:       '실시간 로그',
    coin:          '코인',
    entryPrice:    '진입가',
    currentPrice:  '현재가',
    stopLoss:      '손절가',
    unrealizedPnl: '미실현손익',
    entryDate:     '진입일',
    exitDate:      '청산일',
    exitReason:    '사유',
    exitPrice:     '청산가',
    pnl:           '손익',
    pnlPct:        '손익률',
    noPosition:    '보유 포지션이 없습니다',
    noTradeData:   '거래 이력이 없습니다',
    noLog:         '로그가 없습니다',
    apiError:      'API 서버에 연결할 수 없습니다',
    tabCoin:       '코인',
    tabStock:      '주식',
  },
  en: {
    title:         'Paper Trading Dashboard',
    running:       'Running',
    stopped:       'Stopped',
    start:         'Start Bot',
    stop:          'Stop Bot',
    refresh:       'Refresh',
    lastUpdate:    'Updated',
    nextRun:       'Next run',
    totalInvested: 'Invested',
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
  n != null ? `₩${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : null

const fmtPct = (n) =>
  n != null ? `${(n * 100).toFixed(1)}%` : null

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
  const [error,          setError]          = useState(null)
  const [lastUpdate,     setLastUpdate]     = useState(null)
  const [countdown,      setCountdown]      = useState(REFRESH_SEC)
  const [activeTab,      setActiveTab]      = useState('coin')

  const t = T[lang]

  const fetchAll = useCallback(async () => {
    try {
      const [s, p, tr, st, lg, reg, sp, ins, agents] = await Promise.all([
        api.status(),
        api.positions(),
        api.trades(50),
        api.stats(),
        api.logs(40),
        api.marketRegime().catch(() => null),
        api.stockPositions().catch(() => []),
        api.insights().catch(() => null),
        api.agentsStatus().catch(() => null),
      ])
      setStatus(s)
      setPositions(p)
      setTrades(tr)
      setStats(st)
      setLogs(lg.lines)
      if (reg) setRegime(reg)
      setStockPositions(sp)
      if (ins) setInsights(ins)
      if (agents) setAgentStatus(agents)
      setLastUpdate(
        new Date().toLocaleTimeString('ko-KR', {
          hour: '2-digit', minute: '2-digit', second: '2-digit',
        })
      )
      setError(null)
    } catch (e) {
      setError(e.message)
    }
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

  return (
    <div className="app">

      <header className="header">
        <span className="header-title">📈 {t.title}</span>

        <span className={`status-dot ${isRunning ? 'on' : 'off'}`} />
        <span className="status-text">{isRunning ? t.running : t.stopped}</span>

        {status?.next_run && (
          <span className="next-run c-muted">
            {t.nextRun}: {status.next_run.slice(11, 16)}
          </span>
        )}

        <div className="header-sep" />

        <button className="btn btn-start" onClick={handleStart} disabled={isRunning}>
          ▶ {t.start}
        </button>
        <button className="btn btn-stop" onClick={handleStop} disabled={!isRunning}>
          ■ {t.stop}
        </button>

        <div className="header-sep" />

        <button className="btn btn-ghost" onClick={fetchAll}>
          ↻ {countdown}s
        </button>

        {lastUpdate && (
          <span className="last-update c-muted">{t.lastUpdate}: {lastUpdate}</span>
        )}

        <div className="header-sep" />

        <button className="btn btn-lang" onClick={() => setLang((l) => (l === 'ko' ? 'en' : 'ko'))}>
          {lang === 'ko' ? 'EN' : 'KO'}
        </button>
      </header>

      <MarketRegimeBanner
        regime={regime?.regime ?? 'NEUTRAL'}
        lastChanged={regime?.last_changed ?? null}
        marketOpen={marketOpen}
      />

      {error && (
        <div className="error-banner">
          ⚠ {t.apiError} — {error}
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
                  : stats.sharpe_ratio >= 1   ? 'c-green'
                  : stats.sharpe_ratio >= 0   ? 'c-yellow'
                  : 'c-red'
              }
            />
          </div>
        </div>

        <div className="area-insights">
          <InsightPanel data={insights} agentStatus={agentStatus} />
        </div>

        <div className="area-position">
          <div className="panel" style={{ height: '100%' }}>
            <div className="tab-bar">
              <button
                className={`btn btn-tab ${activeTab === 'coin' ? 'active' : ''}`}
                onClick={() => setActiveTab('coin')}
              >
                🪙 {t.tabCoin}
              </button>
              <button
                className={`btn btn-tab ${activeTab === 'stock' ? 'active' : ''}`}
                onClick={() => setActiveTab('stock')}
              >
                📊 {t.tabStock}
              </button>
            </div>

            {activeTab === 'coin' ? (
              <PositionTable positions={openPositions} t={t} embedded />
            ) : (
              <StockPositionTable positions={stockPositions} />
            )}
          </div>
        </div>

        <div className="area-chart">
          <PnlChart chartData={chartData} t={t} />
        </div>

        <div className="area-trades">
          <TradeHistory trades={trades.slice(0, 15)} t={t} />
        </div>

        <div className="area-logs">
          <LogViewer lines={logs} t={t} />
        </div>

      </main>
    </div>
  )
}

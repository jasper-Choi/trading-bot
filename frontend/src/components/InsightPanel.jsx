import { formatKstDateTime } from '../utils/time'

export default function InsightPanel({ data, agentStatus }) {
  if (!data) {
    return (
      <div className="panel insight-panel">
        <div className="panel-title">Insight Score</div>
        <div className="empty">Loading operator insight data...</div>
      </div>
    )
  }

  const score = data.insight_score
  const color = score >= 0.6 ? 'c-green' : score >= 0.4 ? 'c-yellow' : 'c-red'
  const agents = data.agents || {}
  const runtimeAgents = agentStatus?.agents || {}
  const strategy = agentStatus?.strategy || {}
  const risk = agentStatus?.risk || {}
  const artifacts = agentStatus?.artifacts || {}

  return (
    <div className="panel insight-panel">
      <div className="insight-header">
        <div>
          <div className="panel-title">Insight Score</div>
          <div className="panel-subcopy">
            Model flow confidence, runtime agent health, and cache readiness.
          </div>
        </div>
        <div className={`insight-score-badge ${color}`}>
          {(score * 100).toFixed(0)}
        </div>
      </div>

      <div className="insight-agent-grid">
        {Object.entries(agents).map(([name, agent]) => {
          const s = agent.score
          const c = s >= 0.6 ? 'c-green' : s >= 0.4 ? 'c-yellow' : 'c-red'
          return (
            <div key={name} className="insight-agent-card">
              <div className="insight-agent-name">{name}</div>
              <div className={`insight-agent-score ${c}`}>{(s * 100).toFixed(0)}</div>
              <div className="insight-agent-reason">
                {agent.reason?.slice(0, 52) || 'No reasoning available'}
              </div>
            </div>
          )
        })}
      </div>

      <div className="insight-runtime">
        <div className="insight-runtime-head">
          <span>Strategy: {strategy.direction || 'NEUTRAL'}</span>
          <span>Risk: {risk.allow_new_entries === false ? 'Blocked' : 'Open'}</span>
        </div>
        <div className="insight-runtime-grid">
          {Object.entries(runtimeAgents).map(([name, agent]) => (
            <div key={name} className="insight-runtime-card">
              <div className="insight-runtime-name">{name}</div>
              <div className="insight-runtime-status">{agent.status || 'idle'}</div>
              <div className="insight-runtime-time">{formatKstDateTime(agent.last_run_at)}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="insight-artifact-grid">
        <div>코인 캐시: {artifacts.coin_cached_count ?? 0}</div>
        <div>코인 시그널: {artifacts.coin_signal_count ?? 0}</div>
        <div>주식 유니버스: {artifacts.stock_universe_count ?? 0}</div>
        <div>주식 시그널: {artifacts.stock_signal_count ?? 0}</div>
      </div>

      <div className="insight-footer">
        {formatKstDateTime(data.timestamp)} KST
      </div>
    </div>
  )
}

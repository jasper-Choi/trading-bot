export default function InsightPanel({ data, agentStatus }) {
  if (!data) return (
    <div className="panel">
      <div className="panel-title">Insight Score</div>
      <div className="c-muted" style={{padding:'1rem'}}>Loading...</div>
    </div>
  )

  const score = data.insight_score
  const color = score >= 0.6 ? 'c-green' : score >= 0.4 ? 'c-yellow' : 'c-red'
  const agents = data.agents || {}
  const runtimeAgents = agentStatus?.agents || {}
  const strategy = agentStatus?.strategy || {}
  const risk = agentStatus?.risk || {}
  const artifacts = agentStatus?.artifacts || {}

  return (
    <div className="panel">
      <div className="panel-title" style={{display:'flex',justifyContent:'space-between'}}>
        <span>Insight Score</span>
        <span className={color} style={{fontSize:'1.4rem',fontWeight:'bold'}}>
          {(score * 100).toFixed(0)}
        </span>
      </div>
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.5rem',padding:'0.75rem'}}>
        {Object.entries(agents).map(([name, agent]) => {
          const s = agent.score
          const c = s >= 0.6 ? 'c-green' : s >= 0.4 ? 'c-yellow' : 'c-red'
          return (
            <div key={name} style={{background:'var(--c-bg2)',borderRadius:'8px',padding:'0.5rem 0.75rem'}}>
              <div style={{fontSize:'0.7rem',color:'var(--c-muted)',textTransform:'uppercase'}}>{name}</div>
              <div className={c} style={{fontWeight:'bold'}}>{(s * 100).toFixed(0)}</div>
              <div style={{fontSize:'0.65rem',color:'var(--c-muted)',marginTop:'2px'}}>{agent.reason?.slice(0,40)}</div>
            </div>
          )
        })}
      </div>
      <div style={{padding:'0 0.75rem 0.75rem'}}>
        <div style={{display:'flex',justifyContent:'space-between',fontSize:'0.75rem',marginBottom:'0.5rem'}}>
          <span>Strategy: {strategy.direction || 'NEUTRAL'}</span>
          <span>Risk: {risk.allow_new_entries === false ? 'BLOCKED' : 'OPEN'}</span>
        </div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.5rem'}}>
          {Object.entries(runtimeAgents).map(([name, agent]) => (
            <div key={name} style={{background:'var(--c-bg2)',borderRadius:'8px',padding:'0.45rem 0.6rem'}}>
              <div style={{fontSize:'0.68rem',color:'var(--c-muted)',textTransform:'uppercase'}}>{name}</div>
              <div style={{fontSize:'0.72rem',fontWeight:'bold'}}>{agent.status || 'idle'}</div>
              <div style={{fontSize:'0.65rem',color:'var(--c-muted)'}}>{agent.last_run_at?.slice(11,19) || '--:--:--'}</div>
            </div>
          ))}
        </div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.5rem',marginTop:'0.75rem',fontSize:'0.7rem',color:'var(--c-muted)'}}>
          <div>Coin cache: {artifacts.coin_cached_count ?? 0}</div>
          <div>Coin signals: {artifacts.coin_signal_count ?? 0}</div>
          <div>Stock cache: {artifacts.stock_universe_count ?? 0}</div>
          <div>Stock signals: {artifacts.stock_signal_count ?? 0}</div>
        </div>
      </div>
      <div style={{fontSize:'0.65rem',color:'var(--c-muted)',textAlign:'right',paddingRight:'0.75rem',paddingBottom:'0.5rem'}}>
        {data.timestamp?.slice(11,19)} UTC
      </div>
    </div>
  )
}

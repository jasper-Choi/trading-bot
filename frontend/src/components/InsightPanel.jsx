export default function InsightPanel({ data }) {
  if (!data) return (
    <div className="panel">
      <div className="panel-title">Insight Score</div>
      <div className="c-muted" style={{padding:'1rem'}}>Loading...</div>
    </div>
  )

  const score = data.insight_score
  const color = score >= 0.6 ? 'c-green' : score >= 0.4 ? 'c-yellow' : 'c-red'
  const agents = data.agents || {}

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
      <div style={{fontSize:'0.65rem',color:'var(--c-muted)',textAlign:'right',paddingRight:'0.75rem',paddingBottom:'0.5rem'}}>
        {data.timestamp?.slice(11,19)} UTC
      </div>
    </div>
  )
}

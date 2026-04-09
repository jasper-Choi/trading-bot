/** 상단 지표 카드 1개 */
export default function StatCard({ label, value, sub, valueClass = 'c-text' }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${valueClass}`}>{value ?? '—'}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

/** 현재 오픈 포지션 테이블 */

const money = (n) =>
  n != null ? `₩${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : '—'
const pct = (n, sign = true) =>
  n != null ? `${sign && n >= 0 ? '+' : ''}${Number(n).toFixed(2)}%` : '—'

export default function PositionTable({ positions, t }) {
  const cols = {
    coin:      t.coin,
    entry:     t.entryPrice,
    current:   t.currentPrice,
    stop:      t.stopLoss,
    pnl:       t.unrealizedPnl,
    date:      t.entryDate,
  }

  return (
    <div className="panel">
      <div className="panel-title">{t.openPositions}</div>
      <div className="table-wrap">
        {positions.length === 0 ? (
          <div className="empty">{t.noPosition}</div>
        ) : (
          <table>
            <thead>
              <tr>
                {Object.values(cols).map((h) => <th key={h}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const pnlVal  = pos.unrealized_pnl ?? 0
                const isPos   = pnlVal >= 0
                const badgeCls = isPos ? 'badge-green' : 'badge-red'
                return (
                  <tr key={pos.coin}>
                    <td><strong>{pos.coin.replace('KRW-', '')}</strong></td>
                    <td>{money(pos.entry_price)}</td>
                    <td>{money(pos.current_price)}</td>
                    {/* 트레일링 스탑이 있으면 우선, 없으면 기본 손절가 */}
                    <td className="c-red">{money(pos.trailing_stop ?? pos.stop_loss)}</td>
                    <td>
                      <span className={`badge ${badgeCls}`}>
                        {isPos ? '+' : '−'}{money(pnlVal)}
                        &nbsp;({pct(pos.unrealized_pnl_pct)})
                      </span>
                    </td>
                    <td className="c-muted">{pos.entry_date}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

/** Open crypto position table. */

const money = (n) =>
  n != null ? `KRW ${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : '--'

const pct = (n) =>
  n != null ? `${n >= 0 ? '+' : ''}${Number(n).toFixed(2)}%` : '--'

/**
 * embedded=true renders only the table body so the parent panel can wrap it.
 */
export default function PositionTable({ positions, t, embedded = false }) {
  const tableBody = (
    <div className="table-wrap">
      {positions.length === 0 ? (
        <div className="empty">{t.noPosition}</div>
      ) : (
        <>
          <div className="mobile-card-list">
            {positions.map((pos) => {
              const pnlVal = Number(pos.unrealized_pnl ?? 0)
              const isPos = pnlVal >= 0
              const badgeCls = isPos ? 'badge-green' : 'badge-red'
              const stop = pos.trailing_stop ?? pos.stop_loss

              return (
                <article className="mobile-data-card" key={`card-${pos.coin}`}>
                  <div className="mobile-card-head">
                    <strong>{String(pos.coin || '--').replace('KRW-', '')}</strong>
                    <span className={`badge ${badgeCls}`}>
                      {isPos ? '+' : '-'}{money(pnlVal)}
                    </span>
                  </div>
                  <div className="mobile-card-grid">
                    <span>진입가 {money(pos.entry_price)}</span>
                    <span>현재가 {money(pos.current_price)}</span>
                    <span>손절가 {money(stop)}</span>
                    <span>수익률 {pct(pos.unrealized_pnl_pct)}</span>
                    <span>진입 {pos.entry_date?.slice(5, 16) || '--'}</span>
                    {Number(pos.pyramid_count || 0) > 0 && (
                      <span>피라미딩 x{Number(pos.pyramid_count) + 1}</span>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
          <table>
            <thead>
              <tr>
                <th>{t.coin}</th>
                <th>{t.entryPrice}</th>
                <th>{t.currentPrice}</th>
                <th>{t.stopLoss}</th>
                <th>{t.unrealizedPnl}</th>
                <th>{t.entryDate}</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const pnlVal = Number(pos.unrealized_pnl ?? 0)
                const isPos = pnlVal >= 0
                const badgeCls = isPos ? 'badge-green' : 'badge-red'
                const stop = pos.trailing_stop ?? pos.stop_loss

                return (
                  <tr key={pos.coin}>
                    <td>
                      <strong>{String(pos.coin || '--').replace('KRW-', '')}</strong>
                      {Number(pos.pyramid_count || 0) > 0 && (
                        <span className="c-yellow" style={{ fontSize: 11, marginLeft: 4 }}>
                          x{Number(pos.pyramid_count) + 1}
                        </span>
                      )}
                    </td>
                    <td>{money(pos.entry_price)}</td>
                    <td>{money(pos.current_price)}</td>
                    <td className="c-red">{money(stop)}</td>
                    <td>
                      <span className={`badge ${badgeCls}`}>
                        {isPos ? '+' : '-'}{money(pnlVal)} &nbsp;({pct(pos.unrealized_pnl_pct)})
                      </span>
                    </td>
                    <td className="c-muted">{pos.entry_date?.slice(5, 16) || '--'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  )

  if (embedded) return tableBody

  return (
    <div className="panel">
      <div className="panel-title">{t.openPositions}</div>
      {tableBody}
    </div>
  )
}

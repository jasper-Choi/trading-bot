/** 현재 오픈 포지션 테이블 */

const money = (n) =>
  n != null ? `₩${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : '—'
const pct = (n) =>
  n != null ? `${n >= 0 ? '+' : ''}${Number(n).toFixed(2)}%` : '—'

/**
 * embedded=true: 외부 .panel 없이 테이블 부분만 렌더 (탭 안에서 사용)
 */
export default function PositionTable({ positions, t, embedded = false }) {
  const tableBody = (
    <div className="table-wrap">
      {positions.length === 0 ? (
        <div className="empty">{t.noPosition}</div>
      ) : (
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
              const pnlVal   = pos.unrealized_pnl ?? 0
              const isPos    = pnlVal >= 0
              const badgeCls = isPos ? 'badge-green' : 'badge-red'
              return (
                <tr key={pos.coin}>
                  <td>
                    <strong>{pos.coin.replace('KRW-', '')}</strong>
                    {pos.pyramid_count > 0 && (
                      <span className="c-yellow" style={{ fontSize: 11, marginLeft: 4 }}>
                        ×{pos.pyramid_count + 1}
                      </span>
                    )}
                  </td>
                  <td>{money(pos.entry_price)}</td>
                  <td>{money(pos.current_price)}</td>
                  <td className="c-red">{money(pos.trailing_stop ?? pos.stop_loss)}</td>
                  <td>
                    <span className={`badge ${badgeCls}`}>
                      {isPos ? '+' : '−'}{money(pnlVal)}
                      &nbsp;({pct(pos.unrealized_pnl_pct)})
                    </span>
                  </td>
                  <td className="c-muted">{pos.entry_date?.slice(5, 16)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
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

/** 최근 거래 이력 테이블 */

const money = (n) =>
  n != null ? `₩${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : '—'

export default function TradeHistory({ trades, t }) {
  return (
    <div className="panel">
      <div className="panel-title">{t.recentTrades}</div>
      <div className="table-wrap">
        {trades.length === 0 ? (
          <div className="empty">{t.noTradeData}</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{t.coin}</th>
                <th>{t.entryDate}</th>
                <th>{t.exitDate}</th>
                <th>{t.exitReason}</th>
                <th>{t.entryPrice}</th>
                <th>{t.exitPrice}</th>
                <th>{t.pnl}</th>
                <th>{t.pnlPct}</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((tr, i) => {
                const isPos   = tr.pnl >= 0
                const badgeCls = isPos ? 'badge-green' : 'badge-red'
                const sign     = isPos ? '+' : '−'
                return (
                  <tr key={i}>
                    <td><strong>{tr.coin.replace('KRW-', '')}</strong></td>
                    <td className="c-muted">{tr.entry_date}</td>
                    <td className="c-muted">{tr.exit_date}</td>
                    <td>
                      <span className={`badge badge-blue`}>{tr.exit_reason}</span>
                    </td>
                    <td>{money(tr.entry_price)}</td>
                    <td>{money(tr.exit_price)}</td>
                    <td>
                      <span className={`badge ${badgeCls}`}>
                        {sign}{money(tr.pnl)}
                      </span>
                    </td>
                    <td className={isPos ? 'c-green' : 'c-red'}>
                      {sign}{Math.abs(tr.pnl_pct).toFixed(2)}%
                    </td>
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

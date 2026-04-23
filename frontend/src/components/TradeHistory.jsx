import { formatKstDateTime } from '../utils/time'

const money = (n) =>
  n != null ? `KRW ${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : '--'

export default function TradeHistory({ trades, t }) {
  return (
    <div className="panel">
      <div className="panel-title">{t.recentTrades}</div>
      <div className="table-wrap">
        {trades.length === 0 ? (
          <div className="empty">{t.noTradeData}</div>
        ) : (
          <>
            <div className="mobile-card-list">
              {trades.map((tr, i) => {
                const pnl = Number(tr.pnl ?? 0)
                const pnlPct = Number(tr.pnl_pct ?? 0)
                const isPos = pnl >= 0
                const badgeCls = isPos ? 'badge-green' : 'badge-red'
                const sign = isPos ? '+' : '-'
                const symbol = String(tr.coin || tr.symbol || '--').replace('KRW-', '')

                return (
                  <article className="mobile-data-card" key={`trade-card-${symbol}-${i}`}>
                    <div className="mobile-card-head">
                      <strong>{symbol}</strong>
                      <span className={`badge ${badgeCls}`}>{sign}{Math.abs(pnlPct).toFixed(2)}%</span>
                    </div>
                    <div className="mobile-card-grid">
                      <span>{`진입 ${formatKstDateTime(tr.entry_date)}`}</span>
                      <span>{`청산 ${formatKstDateTime(tr.exit_date)}`}</span>
                      <span>{`사유 ${tr.exit_reason || '--'}`}</span>
                      <span>{`손익 ${sign}${money(pnl)}`}</span>
                      <span>{`진입가 ${money(tr.entry_price)}`}</span>
                      <span>{`청산가 ${money(tr.exit_price)}`}</span>
                    </div>
                  </article>
                )
              })}
            </div>
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
                  const pnl = Number(tr.pnl ?? 0)
                  const pnlPct = Number(tr.pnl_pct ?? 0)
                  const isPos = pnl >= 0
                  const badgeCls = isPos ? 'badge-green' : 'badge-red'
                  const sign = isPos ? '+' : '-'
                  const symbol = String(tr.coin || tr.symbol || '--').replace('KRW-', '')

                  return (
                    <tr key={`${symbol}-${tr.exit_date || i}-${i}`}>
                      <td><strong>{symbol}</strong></td>
                      <td className="c-muted">{formatKstDateTime(tr.entry_date)}</td>
                      <td className="c-muted">{formatKstDateTime(tr.exit_date)}</td>
                      <td>
                        <span className="badge badge-blue">{tr.exit_reason || '--'}</span>
                      </td>
                      <td>{money(tr.entry_price)}</td>
                      <td>{money(tr.exit_price)}</td>
                      <td>
                        <span className={`badge ${badgeCls}`}>
                          {sign}{money(pnl)}
                        </span>
                      </td>
                      <td className={isPos ? 'c-green' : 'c-red'}>
                        {sign}{Math.abs(pnlPct).toFixed(2)}%
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}

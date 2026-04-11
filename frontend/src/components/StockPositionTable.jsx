/**
 * 주식 오픈 포지션 테이블
 */

const money = (n) =>
  n != null ? `₩${Math.round(Math.abs(n)).toLocaleString('ko-KR')}` : '—'
const pct   = (n) =>
  n != null ? `${n >= 0 ? '+' : ''}${Number(n).toFixed(2)}%` : '—'

export default function StockPositionTable({ positions }) {
  return (
    <div className="panel">
      <div className="panel-title">주식 보유 포지션</div>
      <div className="table-wrap">
        {positions.length === 0 ? (
          <div className="empty">보유 주식 포지션이 없습니다</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>종목</th>
                <th>진입가</th>
                <th>손절가</th>
                <th>자본</th>
                <th>사유</th>
                <th>진입일</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => (
                <tr key={pos.ticker}>
                  <td>
                    <strong>{pos.name}</strong>
                    <span className="c-muted" style={{ fontSize: 11, marginLeft: 4 }}>
                      {pos.ticker}
                    </span>
                  </td>
                  <td>{money(pos.entry_price)}</td>
                  <td className="c-red">{money(pos.stop_loss)}</td>
                  <td>{money(pos.capital)}</td>
                  <td>
                    <span className="badge badge-blue">{pos.reason}</span>
                  </td>
                  <td className="c-muted">{pos.entry_date?.slice(5, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

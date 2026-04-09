/** 누적 손익 곡선 (Recharts AreaChart) */
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts'

const fmtY  = (v) => `₩${(v / 1000).toFixed(0)}K`
const fmtTip = (v) => [`₩${Math.round(v).toLocaleString('ko-KR')}`, '누적손익']

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const v = payload[0].value
  const isPos = v >= 0
  return (
    <div style={{
      background: '#161b22', border: '1px solid #30363d',
      borderRadius: 8, padding: '8px 12px', fontSize: 13,
    }}>
      <div style={{ color: '#7d8590', marginBottom: 4 }}>{label}</div>
      <div style={{ color: isPos ? '#3fb950' : '#f85149', fontWeight: 700 }}>
        {isPos ? '+' : ''}₩{Math.round(v).toLocaleString('ko-KR')}
      </div>
    </div>
  )
}

export default function PnlChart({ chartData, t }) {
  const lastVal   = chartData.at(-1)?.cumPnl ?? 0
  const lineColor = lastVal >= 0 ? '#3fb950' : '#f85149'
  const gradId    = 'pnlGrad'

  return (
    <div className="panel">
      <div className="panel-title">{t.pnlChart}</div>

      {chartData.length === 0 ? (
        <div className="empty">{t.noTradeData}</div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={lineColor} stopOpacity={0.28} />
                <stop offset="95%" stopColor={lineColor} stopOpacity={0} />
              </linearGradient>
            </defs>

            <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />

            <XAxis
              dataKey="date"
              stroke="#7d8590"
              tick={{ fontSize: 11, fill: '#7d8590' }}
              tickFormatter={(d) => d.slice(5)}   /* MM-DD 만 표시 */
            />
            <YAxis
              stroke="#7d8590"
              tick={{ fontSize: 11, fill: '#7d8590' }}
              tickFormatter={fmtY}
              width={62}
            />

            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={0} stroke="#30363d" strokeDasharray="4 4" />

            <Area
              type="monotone"
              dataKey="cumPnl"
              stroke={lineColor}
              strokeWidth={2}
              fill={`url(#${gradId})`}
              dot={false}
              activeDot={{ r: 4, fill: lineColor }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

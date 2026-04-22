/**
 * Market regime banner shown near the top of the dashboard.
 * regime: "BULL" | "NEUTRAL" | "BEAR" | "VOLATILE"
 */
export default function MarketRegimeBanner({ regime, lastChanged, marketOpen }) {
  const map = {
    BULL:     { icon: 'UP', label: '상승장', cls: 'regime-bull' },
    NEUTRAL:  { icon: 'EQ', label: '중립장', cls: 'regime-neutral' },
    BEAR:     { icon: 'DN', label: '하락장', cls: 'regime-bear' },
    VOLATILE: { icon: 'VX', label: '변동장', cls: 'regime-volatile' },
  }
  const info = map[regime] ?? map.NEUTRAL

  return (
    <div className={`regime-banner ${info.cls}`}>
      <span className="regime-icon">{info.icon}</span>
      <span className="regime-label">{info.label}</span>
      {lastChanged && (
        <span className="regime-changed">변경: {lastChanged.slice(11, 16)}</span>
      )}
      <div className="regime-sep" />
      <span className={`market-hours ${marketOpen ? 'market-open' : 'market-closed'}`}>
        {marketOpen ? '시장 열림' : '시장 닫힘'}
      </span>
    </div>
  )
}

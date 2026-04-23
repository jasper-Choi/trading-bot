/**
 * Market regime banner shown near the top of the dashboard.
 * regime: "BULL" | "NEUTRAL" | "BEAR" | "VOLATILE"
 */
export default function MarketRegimeBanner({ regime, lastChanged, marketOpen }) {
  const map = {
    BULL: { icon: 'UP', label: 'Bullish', cls: 'regime-bull' },
    NEUTRAL: { icon: 'EQ', label: 'Neutral', cls: 'regime-neutral' },
    BEAR: { icon: 'DN', label: 'Bearish', cls: 'regime-bear' },
    VOLATILE: { icon: 'VX', label: 'Volatile', cls: 'regime-volatile' },
  }
  const info = map[regime] ?? map.NEUTRAL

  return (
    <div className={`regime-banner ${info.cls}`}>
      <span className="regime-icon">{info.icon}</span>
      <span className="regime-label">{info.label}</span>
      {lastChanged && (
        <span className="regime-changed">Changed {lastChanged.slice(11, 16)}</span>
      )}
      <div className="regime-sep" />
      <span className={`market-hours ${marketOpen ? 'market-open' : 'market-closed'}`}>
        {marketOpen ? 'Market Open' : 'Market Closed'}
      </span>
    </div>
  )
}

/**
 * 시장 국면 배너 — 상단에 항상 표시
 * regime: "BULL" | "NEUTRAL" | "BEAR" | "VOLATILE"
 */
export default function MarketRegimeBanner({ regime, lastChanged, marketOpen }) {
  const map = {
    BULL:     { icon: '🟢', label: '공격 모드',   cls: 'regime-bull' },
    NEUTRAL:  { icon: '🟡', label: '중립 모드',   cls: 'regime-neutral' },
    BEAR:     { icon: '🔴', label: '방어 모드',   cls: 'regime-bear' },
    VOLATILE: { icon: '⛔', label: '완전 방어',   cls: 'regime-volatile' },
  }
  const info = map[regime] ?? map['NEUTRAL']

  return (
    <div className={`regime-banner ${info.cls}`}>
      <span className="regime-icon">{info.icon}</span>
      <span className="regime-label">{info.label}</span>
      {lastChanged && (
        <span className="regime-changed">전환: {lastChanged.slice(11, 16)}</span>
      )}
      <div className="regime-sep" />
      <span className={`market-hours ${marketOpen ? 'market-open' : 'market-closed'}`}>
        {marketOpen ? '🏦 장 중' : '🔒 장 마감'}
      </span>
    </div>
  )
}

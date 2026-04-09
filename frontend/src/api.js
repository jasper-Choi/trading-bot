// Vite 프록시가 /api/* → http://localhost:8000 으로 전달

async function request(path, options = {}) {
  const res = await fetch(path, options)
  if (!res.ok) throw new Error(`HTTP ${res.status} — ${path}`)
  return res.json()
}

export const api = {
  /** 봇 실행 상태 */
  status:   () => request('/api/status'),
  /** 전체 포지션 (open + 오늘 closed) */
  positions: () => request('/api/positions'),
  /** 거래 이력 */
  trades:   (limit = 50) => request(`/api/trades?limit=${limit}`),
  /** 전략 통계 */
  stats:    () => request('/api/stats'),
  /** 최근 로그 */
  logs:     (lines = 40) => request(`/api/logs?lines=${lines}`),
  /** 봇 시작 */
  startBot: () => request('/api/bot/start', { method: 'POST' }),
  /** 봇 중지 */
  stopBot:  () => request('/api/bot/stop',  { method: 'POST' }),
}

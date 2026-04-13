// 로컬 개발: VITE_API_BASE_URL=http://localhost:8000 (.env.development)
// Railway 배포: VITE_API_BASE_URL 미설정 → 상대경로 /api/... (같은 도메인)
const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  if (!res.ok) throw new Error(`HTTP ${res.status} — ${path}`)
  return res.json()
}

export const api = {
  /** 봇 실행 상태 */
  status:         () => request('/api/status'),
  /** 전체 포지션 (open + 오늘 closed) */
  positions:      () => request('/api/positions'),
  /** 거래 이력 */
  trades:         (limit = 50) => request(`/api/trades?limit=${limit}`),
  /** 전략 통계 */
  stats:          () => request('/api/stats'),
  /** 최근 로그 */
  logs:           (lines = 40) => request(`/api/logs?lines=${lines}`),
  /** 봇 시작 */
  startBot:       () => request('/api/bot/start', { method: 'POST' }),
  /** 봇 중지 */
  stopBot:        () => request('/api/bot/stop',  { method: 'POST' }),
  /** 시장 국면 */
  marketRegime:   () => request('/api/bot/market-regime'),
  /** 주식 오픈 포지션 */
  stockPositions: () => request('/api/stock/positions'),
  /** 주식 거래 이력 */
  stockHistory:   (limit = 30) => request(`/api/stock/history?limit=${limit}`),
}

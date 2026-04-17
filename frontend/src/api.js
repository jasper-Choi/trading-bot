// 濡쒖뺄 媛쒕컻: VITE_API_BASE_URL=http://localhost:8000 (.env.development)
// Railway 諛고룷: VITE_API_BASE_URL 誘몄꽕?????곷?寃쎈줈 /api/... (媛숈? ?꾨찓??
const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  if (!res.ok) throw new Error(`HTTP ${res.status} ??${path}`)
  return res.json()
}

export const api = {
  /** 遊??ㅽ뻾 ?곹깭 */
  status:         () => request('/api/status'),
  /** ?꾩껜 ?ъ???(open + ?ㅻ뒛 closed) */
  positions:      () => request('/api/positions'),
  /** 嫄곕옒 ?대젰 */
  trades:         (limit = 50) => request(`/api/trades?limit=${limit}`),
  /** ?꾨왂 ?듦퀎 */
  stats:          () => request('/api/stats'),
  /** 理쒓렐 濡쒓렇 */
  logs:           (lines = 40) => request(`/api/logs?lines=${lines}`),
  /** 遊??쒖옉 */
  startBot:       () => request('/api/bot/start', { method: 'POST' }),
  /** 遊?以묒? */
  stopBot:        () => request('/api/bot/stop',  { method: 'POST' }),
  /** ?쒖옣 援?㈃ */
  marketRegime:   () => request('/api/bot/market-regime'),
  /** 二쇱떇 ?ㅽ뵂 ?ъ???*/
  stockPositions: () => request('/api/stock/positions'),
  /** 二쇱떇 嫄곕옒 ?대젰 */
  stockHistory:   (limit = 30) => request(`/api/stock/history?limit=${limit}`),
  insights:       () => request('/api/insights/'),
}



// Local development example:
// VITE_API_BASE_URL=http://localhost:8000
// If the variable is not set, requests use relative paths against the current host.
const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

const RETRY_DELAYS_MS = [0, 400, 1200]

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function request(path, options = {}) {
  let lastError

  for (const delay of RETRY_DELAYS_MS) {
    if (delay > 0) await sleep(delay)
    try {
      const res = await fetch(`${BASE}${path}`, {
        cache: 'no-store',
        ...options,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status} @ ${path}`)
      return await res.json()
    } catch (error) {
      lastError = error
    }
  }

  throw lastError
}

export const api = {
  // Runtime status
  status: () => request('/api/status'),
  // Open positions plus recently closed items
  positions: () => request('/api/positions'),
  // Recent trade history
  trades: (limit = 50) => request(`/api/trades?limit=${limit}`),
  // Summary statistics
  stats: () => request('/api/stats'),
  // Recent log lines
  logs: (lines = 40) => request(`/api/logs?lines=${lines}`),
  // Bot control
  startBot: () => request('/api/bot/start', { method: 'POST' }),
  stopBot: () => request('/api/bot/stop', { method: 'POST' }),
  // Market regime and stock views
  marketRegime: () => request('/api/bot/market-regime'),
  stockPositions: () => request('/api/stock/positions'),
  stockHistory: (limit = 30) => request(`/api/stock/history?limit=${limit}`),
  // Insight and diagnostics
  insights: () => request('/api/insights/'),
  agentsStatus: () => request('/api/insights/agents/status'),
  dashboardData: () => request('/dashboard-data'),
  mobileSummary: () => request('/mobile-summary'),
  readiness: () => request('/diagnostics/live-readiness-checklist'),
  brokerHealth: () => request('/diagnostics/broker-live-health'),
}

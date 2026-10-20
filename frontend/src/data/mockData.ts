// All sample data lives here. Phase 6 of the playbook replaces these
// exports with real API calls (React Query hooks in src/api.ts).
import type { ChartRow, NewsItem, SearchAsset, TickerItem, Timeframe, WatchAsset } from '@/types'

export const watchlist: WatchAsset[] = [
  { symbol: 'NVDA', name: 'NVIDIA', price: '$142.62', change: 2.84, points: '2,22 13,19 24,17 35,7 46,11 57,3 68,5' },
  { symbol: 'BTC', name: 'Bitcoin', price: '$96,841', change: 1.36, points: '2,19 13,14 24,18 35,11 46,13 57,5 68,8' },
  { symbol: 'GLD', name: 'Gold ETF', price: '$241.36', change: 0.62, points: '2,17 13,18 24,13 35,14 46,8 57,10 68,4' },
  { symbol: 'TSLA', name: 'Tesla', price: '$355.84', change: -1.47, points: '2,4 13,8 24,6 35,14 46,11 57,20 68,18' },
  { symbol: 'ETH', name: 'Ethereum', price: '$3,428', change: 2.12, points: '2,20 13,16 24,18 35,10 46,14 57,7 68,5' },
  { symbol: 'AAPL', name: 'Apple', price: '$238.26', change: -0.28, points: '2,7 13,5 24,10 35,8 46,15 57,13 68,16' },
]

export const ticker: TickerItem[] = [
  ['S&P 500', '6,051.25', '+0.82%'],
  ['NASDAQ', '19,859.77', '+1.12%'],
  ['BTC / USD', '96,841', '+1.36%'],
  ['GOLD', '2,689.40', '+0.62%'],
  ['10Y YIELD', '4.217', '−0.03%'],
  ['EUR / USD', '1.0512', '+0.18%'],
]

export const searchCatalog: SearchAsset[] = [
  { group: 'Stocks', name: 'NVIDIA Corporation', symbol: 'NVDA', type: 'Equity' },
  { group: 'Stocks', name: 'Apple Inc.', symbol: 'AAPL', type: 'Equity' },
  { group: 'Stocks', name: 'Tesla, Inc.', symbol: 'TSLA', type: 'Equity' },
  { group: 'Crypto', name: 'Bitcoin', symbol: 'BTC', type: 'Crypto' },
  { group: 'Crypto', name: 'Ethereum', symbol: 'ETH', type: 'Crypto' },
  { group: 'Funds', name: 'SPDR S&P 500 ETF', symbol: 'SPY', type: 'ETF' },
  { group: 'Funds', name: 'Invesco QQQ Trust', symbol: 'QQQ', type: 'ETF' },
  { group: 'Commodities', name: 'Gold', symbol: 'XAU', type: 'Metal' },
  { group: 'Commodities', name: 'Brent Crude Oil', symbol: 'BRENT', type: 'Energy' },
]

export const searchGroups = ['Stocks', 'Crypto', 'Funds', 'Commodities'] as const

export const news: NewsItem[] = [
  { source: 'Reuters', headline: 'Nvidia expands AI chip availability through new regional partnerships', time: '18m', tone: 'Positive' },
  { source: 'Bloomberg', headline: 'Chipmakers gain as infrastructure spending outlook improves', time: '42m', tone: 'Positive' },
  { source: 'Financial Times', headline: 'Investors weigh premium valuations across the semiconductor sector', time: '1h', tone: 'Neutral' },
  { source: 'CNBC', headline: 'Data-center demand remains resilient into the final quarter', time: '2h', tone: 'Positive' },
  { source: 'The Verge', headline: 'Export rules may narrow access to advanced accelerator markets', time: '3h', tone: 'Negative' },
]

export const assetProfile = {
  monogram: 'N',
  name: 'NVIDIA Corporation',
  badge: 'EQUITY · NASDAQ',
  symbol: 'NVDA',
  sector: 'Technology',
  country: 'United States',
  price: 142.62,
  priceLabel: '$142.62',
  changeLabel: '+$3.94\u00a0\u00a0 +2.84%',
}

export const forecast = {
  horizonLabel: '30-DAY FORECAST',
  expectedChange: 8.54, // %
  confidence: 78, // %
  target: 154.8,
}

export const summaryText =
  'Momentum remains constructive as data-center demand and improving margins support the current trend. Valuation is elevated, but price action and news flow point to measured upside over the next 30 days.'

export const sentiment = { score: 72, label: 'Optimistic', updated: 'UPDATED 6M AGO' }

// Fusion-layer weights (sum to 100). In production: w_price, w_news, w_graph.
export const signals = [
  { key: 'one', label: 'Price pattern', value: 46 },
  { key: 'two', label: 'News sentiment', value: 32 },
  { key: 'three', label: 'Cross-asset', value: 22 },
] as const

export const Y_MIN = 120
export const Y_MAX = 160
export const Y_TICKS = [160, 150, 140, 130, 120]
export const TODAY_X = 75

export const xLabels = ['OCT 21', 'NOV 04', 'NOV 18', 'DEC 02', 'DEC 16', 'DEC 30']

const startPrice: Record<Timeframe, number> = { '1W': 137.2, '1M': 131.5, '3M': 126.8, '1Y': 123.4 }
const wiggle: Record<Timeframe, number> = { '1W': 1.1, '1M': 2.0, '3M': 2.8, '1Y': 3.4 }
const pointCount = 24

/** Deterministic mock series: history ends exactly at today's price, then a 30-day forecast with a widening band. */
export function buildChartRows(timeframe: Timeframe): ChartRow[] {
  const end = assetProfile.price
  const start = startPrice[timeframe]
  const amp = wiggle[timeframe]
  const rows: ChartRow[] = []

  for (let i = 0; i < pointCount; i++) {
    const t = i / (pointCount - 1)
    const trend = start + (end - start) * Math.pow(t, 1.15)
    const noise = Math.sin(i * 1.9) * amp * (1 - t) + Math.cos(i * 0.8) * amp * 0.5 * (1 - t)
    const price = i === pointCount - 1 ? end : trend + noise
    rows.push({ x: +(t * TODAY_X).toFixed(2), price: +price.toFixed(2) })
  }

  const steps = 6
  const last = rows[rows.length - 1]
  last.forecast = end
  last.band = [end, end]
  for (let j = 1; j <= steps; j++) {
    const t = j / steps
    const f = end + (forecast.target - end) * (1 - Math.pow(1 - t, 1.6))
    const spread = 5.2 * t
    rows.push({
      x: +(TODAY_X + t * (100 - TODAY_X)).toFixed(2),
      forecast: +f.toFixed(2),
      band: [+(f - spread).toFixed(2), +(f + spread).toFixed(2)],
    })
  }
  return rows
}

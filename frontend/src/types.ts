export type WatchAsset = {
  symbol: string
  name: string
  price: string
  change: number
  points: string
}

export type TickerItem = [name: string, price: string, change: string]

export type SearchAsset = {
  group: 'Stocks' | 'Crypto' | 'Funds' | 'Commodities'
  name: string
  symbol: string
  type: string
}

export type Tone = 'Positive' | 'Neutral' | 'Negative'

export type NewsItem = {
  source: string
  headline: string
  time: string
  tone: Tone
}

export type Timeframe = '1W' | '1M' | '3M' | '1Y'

export type ChartRow = {
  x: number
  price?: number
  forecast?: number
  band?: [number, number]
}

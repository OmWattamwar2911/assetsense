import { useRef, useState } from 'react'
import { AssetHeader } from './components/AssetHeader'
import { ForecastCard } from './components/ForecastCard'
import { NewsSources } from './components/NewsSources'
import { PriceChart } from './components/PriceChart'
import { SentimentGauge } from './components/SentimentGauge'
import { SignalBreakdown } from './components/SignalBreakdown'
import { CardsSkeleton } from './components/skeletons'
import { SummaryCard } from './components/SummaryCard'
import { TopBar } from './components/TopBar'
import { Watchlist } from './components/Watchlist'
import { watchlist } from './data/mockData'
import type { Timeframe } from './types'

export default function App() {
  const [timeframe, setTimeframe] = useState<Timeframe>('3M')
  const [selected, setSelected] = useState('NVDA')
  const [contentLoading, setContentLoading] = useState(false)
  const timer = useRef<number>(undefined)

  // Simulated fetch delay. Replaced by React Query loading flags in Phase 6.
  const loadContent = (action: () => void) => {
    action()
    setContentLoading(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setContentLoading(false), 620)
  }

  return (
    <div className="app-shell">
      <TopBar onSelectAsset={(asset) => loadContent(() => setSelected(asset.symbol))} />
      <Watchlist assets={watchlist} selected={selected} onSelect={(s) => loadContent(() => setSelected(s))} />

      <main className="main-panel">
        <AssetHeader />
        <PriceChart timeframe={timeframe} loading={contentLoading} onTimeframeChange={(t) => loadContent(() => setTimeframe(t))} />

        {contentLoading ? (
          <CardsSkeleton />
        ) : (
          <div className="insight-grid">
            <ForecastCard />
            <SummaryCard />
            <SentimentGauge />
            <SignalBreakdown />
            <NewsSources />
          </div>
        )}

        <footer>
          <span>© 2026 ASSETSENSE</span>
          <p>Educational use only, not financial advice.</p>
          <span>DATA DELAYED UP TO 15 MIN</span>
        </footer>
      </main>
    </div>
  )
}

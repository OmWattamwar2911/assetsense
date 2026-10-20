import { useEffect, useMemo, useState } from 'react'
import { searchCatalog, searchGroups } from '@/data/mockData'
import type { SearchAsset } from '@/types'
import { SearchSkeleton } from './skeletons'
import { SearchIcon } from './ui'

type Props = {
  query: string
  onSelect: (asset: SearchAsset) => void
  onRetry: () => void
}

export function SearchDropdown({ query, onSelect, onRetry }: Props) {
  const [loading, setLoading] = useState(false)

  const groupedResults = useMemo(() => {
    const q = query.toLowerCase()
    return searchGroups
      .map((group) => ({
        group,
        items: searchCatalog.filter(
          (asset) => asset.group === group && `${asset.name} ${asset.symbol} ${asset.type}`.toLowerCase().includes(q),
        ),
      }))
      .filter((section) => section.items.length)
  }, [query])

  // Simulated latency. Replaced by useSearch() (debounced API call) in Phase 6.
  useEffect(() => {
    setLoading(true)
    const timer = window.setTimeout(() => setLoading(false), 420)
    return () => window.clearTimeout(timer)
  }, [query])

  // Type the word "error" in the search bar to preview the error state.
  const hasError = query.trim().toLowerCase() === 'error'

  return (
    <div className="search-dropdown">
      {loading ? (
        <SearchSkeleton />
      ) : hasError ? (
        <div className="search-state">
          <div className="state-icon error-icon">!</div>
          <strong>We couldn’t load results</strong>
          <p>There was a problem reaching market data. Please try again.</p>
          <button onClick={onRetry}>Retry search</button>
        </div>
      ) : groupedResults.length === 0 ? (
        <div className="search-state">
          <div className="state-icon empty-icon"><SearchIcon /></div>
          <strong>No assets found</strong>
          <p>Try a company name, ticker symbol, fund, or commodity.</p>
        </div>
      ) : (
        <div className="search-results">
          {groupedResults.map((section) => (
            <div className="result-group" key={section.group}>
              <div className="result-group-title">
                {section.group}
                <span className="num">{section.items.length}</span>
              </div>
              {section.items.map((asset) => (
                <button className="result-row" key={asset.symbol} onClick={() => onSelect(asset)}>
                  <span className="result-symbol num">{asset.symbol.slice(0, 1)}</span>
                  <span className="result-name">
                    <b>{asset.name}</b>
                    <small className="num">{asset.symbol}</small>
                  </span>
                  <span className="result-type">{asset.type}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
      <div className="search-hint">
        <span><kbd>↑</kbd><kbd>↓</kbd> to navigate</span>
        <span><kbd>↵</kbd> to select</span>
      </div>
    </div>
  )
}
